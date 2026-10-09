"""HTTP API for the local frontend.

The server binds to 127.0.0.1 only: uploaded strategies are arbitrary code, and nothing here
should be reachable from another machine. Requests must also name this machine in their Host
header, so a web page cannot reach the API by pointing a domain of its own at 127.0.0.1 (DNS
rebinding). Strategy code never runs in this process: MQL5 runs in the terminal and Python in a
subprocess, both through the job queue.

    python -m strategylab.api        serve on http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import math
import tempfile
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from strategylab import __version__
from strategylab.catalog import CatalogView, HistoryCatalog, Measure
from strategylab.charts import BarStore, BarsUnavailableError, Exporter, as_points, window
from strategylab.compiler import CompileError, CompileResult, compile_upload
from strategylab.config import (
    REPO_ROOT,
    ConfigError,
    Settings,
    TerminalInstall,
    load_settings,
    terminals_using,
)
from strategylab.jobs import BacktestExecutor, EventLog, Executor, JobQueue, RunStateError
from strategylab.params import InputParam, ParamsError, discover
from strategylab.pystrategy import ScriptError, ScriptInput, load_script, run_inputs, stage_script
from strategylab.result import BacktestResult, Engine, TickModel
from strategylab.store import (
    Diagnostic,
    NotFoundError,
    Parameter,
    ParameterOption,
    RunRecord,
    RunSettings,
    RunStatus,
    Store,
    StrategyRecord,
    now,
)

HOST = "127.0.0.1"
PORT = 8000
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
EVENT_POLL_S = 0.25
KEEP_ALIVE_S = 15.0
FINAL_EVENT_GRACE_S = 2.0
FINISHED_STATES = {"done", "failed", "cancelled", "deleted"}
PYTHON_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
PYTHON_MODELS = (TickModel.OHLC_M1, TickModel.REAL_TICKS)

Compiler = Callable[[Path, TerminalInstall, str], CompileResult]


def _compile(upload: Path, install: TerminalInstall, upload_name: str) -> CompileResult:
    return compile_upload(upload, install, upload_name=upload_name)


def _default_measure(
    install: TerminalInstall, first_years: dict[str, int]
) -> tuple[str, list[dict[str, Any]]]:
    from strategylab.mt5_data import measure_history

    return measure_history(install, first_years)


@dataclass
class Services:
    """The parts of the API that touch MetaTrader, replaceable in tests."""

    settings_loader: Callable[[], Settings] = load_settings
    compiler: Compiler = _compile
    executor: Executor | None = None
    measure: Measure = _default_measure
    bar_exporter: Exporter | None = None
    workspace_dir: Path | None = None
    """Where to keep runs when the settings cannot be loaded."""
    start_worker: bool = True


class Lab:
    """Settings, storage, queue and symbol catalog shared by every request."""

    def __init__(self, services: Services) -> None:
        self.services = services
        self._settings: Settings | None = None
        self.settings_error: str | None = None
        self.settings()
        # The engines write under the settings' workspace, so the store and queue must too; the
        # fallback only serves a server started before the terminal could be found.
        workspace = (
            self._settings.workspace_dir
            if self._settings is not None
            else services.workspace_dir or REPO_ROOT / "workspace"
        )
        self.workspace = workspace
        self.store = Store(workspace / "strategylab.sqlite")
        self.events = EventLog()
        executor = services.executor or BacktestExecutor(self.require_settings)
        self.queue = JobQueue(self.store, executor, workspace / "runs", self.events)
        self._catalog: HistoryCatalog | None = None
        exporter = {"exporter": services.bar_exporter} if services.bar_exporter else {}
        self.bars = BarStore(self.require_settings, self.queue.terminal_lock, **exporter)

    def settings(self) -> Settings | None:
        """The current settings, loading them again if they failed before (the user may have
        fixed local.toml or installed the terminal since)."""
        if self._settings is None:
            try:
                self._settings = self.services.settings_loader()
                self.settings_error = None
            except ConfigError as exc:
                self.settings_error = str(exc)
        return self._settings

    def require_settings(self) -> Settings:
        settings = self.settings()
        if settings is None:
            raise HTTPException(503, self.settings_error or "MetaTrader 5 was not found.")
        return settings

    def catalog(self) -> HistoryCatalog:
        settings = self.require_settings()
        if self._catalog is None:
            self._catalog = HistoryCatalog(
                settings.terminal,
                settings.account_server,
                self.workspace / "cache" / "catalog",
                self.queue.terminal_lock,
                self.services.measure,
            )
        return self._catalog


# --- response and request models ---------------------------------------------------------------


class TerminalStatus(BaseModel):
    found: bool
    error: str | None = None
    install_dir: str | None = None
    data_dir: str | None = None
    portable: bool | None = None
    server: str | None = None
    source: str | None = None
    notes: list[str] = Field(default_factory=list)
    running_pids: list[int] = Field(default_factory=list)
    busy_with_run: str | None = None
    """The run StrategyLab is driving the terminal for, if any."""


class QueueStatus(BaseModel):
    current: str | None
    queued: int


class Health(BaseModel):
    status: Literal["ok", "attention", "unavailable"]
    message: str | None
    terminal: TerminalStatus
    queue: QueueStatus
    version: str


class SymbolOut(BaseModel):
    name: str
    description: str | None
    digits: int | None
    path: str | None
    bars_from: date | None
    bars_to: date | None
    """First and last day with M1 bars in the terminal (server dates)."""
    history_years: list[int]
    ticks_from: date | None
    """First month with recorded ticks on this machine."""
    tick_months: int
    measured: bool


class SymbolsOut(BaseModel):
    server: str | None
    symbols: list[SymbolOut]
    pending: list[str]
    message: str | None


class StrategyDetail(BaseModel):
    strategy: StrategyRecord
    runs: list[RunRecord]


class RunDetail(BaseModel):
    run: RunRecord
    strategy: StrategyRecord | None
    queue_position: int | None


Value = str | int | float | bool


class RunCreate(BaseModel):
    strategy_hash: str
    symbol: str
    timeframe: str
    date_from: date
    date_to: date
    """Exclusive, as in the Strategy Tester."""
    model: TickModel = TickModel.OHLC_M1
    deposit: float = Field(10_000.0, gt=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    leverage: int = Field(100, ge=1, le=10_000)
    parameters: dict[str, Value] = Field(default_factory=dict)
    commission_per_lot: float = Field(0.0, ge=0)
    timeout_s: float | None = Field(None, gt=0, le=24 * 3600)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("a three-letter currency code")
        return value.upper()


class RerunRequest(BaseModel):
    """Changes to a past run's settings; parameters are merged over the old ones."""

    symbol: str | None = None
    timeframe: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    model: TickModel | None = None
    deposit: float | None = Field(None, gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    leverage: int | None = Field(None, ge=1, le=10_000)
    parameters: dict[str, Value] = Field(default_factory=dict)
    commission_per_lot: float | None = Field(None, ge=0)
    timeout_s: float | None = Field(None, gt=0, le=24 * 3600)


class SeriesPoint(BaseModel):
    time: datetime | None
    server_time: datetime
    balance: float
    equity: float | None
    drawdown: float
    drawdown_pct: float


class SeriesOut(BaseModel):
    points: list[SeriesPoint]
    total: int
    downsampled: bool


class DealsOut(BaseModel):
    deals: list[dict[str, Any]]
    total: int


class LogSource(BaseModel):
    source: str
    size: int


class Bar(BaseModel):
    time: int
    """Server time, seconds since 1970, as MetaTrader stamps bars."""
    open: float
    high: float
    low: float
    close: float


class BarsOut(BaseModel):
    timeframe: str
    bars: list[Bar]
    first_index: int
    total: int


class ResultSummary(BaseModel):
    meta: dict[str, Any]
    metrics: dict[str, float | int | None]
    reported: dict[str, str]
    orders: int
    deals: int


# --- helpers -----------------------------------------------------------------------------------


def _text(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _mql5_parameter(item: InputParam, source: str) -> Parameter:
    options = [
        ParameterOption(value=str(m.value), label=m.label or m.name)
        for m in item.members
        if m.value is not None
    ]
    return Parameter(
        name=item.name,
        kind=item.kind,
        default=item.value if item.value is not None else item.default,
        label=item.label,
        group=item.group,
        options=options,
        source="set_file" if source == "set_file" else ("sinput" if item.static else "input"),
        type_name=item.mql_type or None,
    )


def _script_parameter(item: ScriptInput) -> Parameter:
    return Parameter(
        name=item.name,
        kind=item.kind,
        default=item.default,
        label=item.label,
        group="Constants" if item.source == "constant" else "Command-line options",
        options=[ParameterOption(value=c, label=c) for c in item.choices],
        source=item.source,
    )


def _diagnostics(items: Any) -> list[Diagnostic]:
    return [
        Diagnostic(
            severity=d.severity,
            message=d.message,
            code=d.code,
            file=d.file,
            line=d.line,
            column=d.column,
        )
        for d in items
    ]


def _safe_name(name: str | None) -> str:
    base = PurePosixPath((name or "").replace("\\", "/")).name
    if not base:
        raise HTTPException(400, "The upload has no file name.")
    return base


async def _read_upload(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"{upload.filename} is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        )
    if not data:
        raise HTTPException(400, f"{upload.filename} is empty.")
    return data


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for row in frame.to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if isinstance(value, pd.Timestamp):
                clean[key] = None if pd.isna(value) else value.isoformat()
            elif value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
                clean[key] = None
            else:
                clean[key] = value
        out.append(clean)
    return out


def drawdown_points(frame: pd.DataFrame) -> pd.DataFrame:
    """Balance with its drawdown from the running peak, in money and percent of the peak."""
    out = frame.copy()
    peak = out["balance"].cummax()
    out["drawdown"] = (peak - out["balance"]).round(2)
    out["drawdown_pct"] = ((peak - out["balance"]) / peak.where(peak > 0) * 100).fillna(0).round(4)
    return out


def downsample(frame: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """At most about `max_points` rows, keeping each bucket's lowest and highest balance and its
    deepest drawdown, so peaks and the worst drawdown survive."""
    if len(frame) <= max_points:
        return frame
    buckets = max(1, max_points // 3)
    size = math.ceil(len(frame) / buckets)
    keep: set[int] = {0, len(frame) - 1}
    balance = frame["balance"].to_numpy()
    drawdown = frame["drawdown"].to_numpy()
    for start in range(0, len(frame), size):
        stop = min(start + size, len(frame))
        keep.add(start + int(balance[start:stop].argmin()))
        keep.add(start + int(balance[start:stop].argmax()))
        keep.add(start + int(drawdown[start:stop].argmax()))
    return frame.iloc[sorted(keep)]


def _sse(event: str, data: dict[str, Any], seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# --- the application ---------------------------------------------------------------------------


def create_app(services: Services | None = None) -> FastAPI:
    lab = Lab(services or Services())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if lab.services.start_worker:
            lab.queue.start()
        else:
            lab.queue.recover()
        yield
        await run_in_threadpool(lab.queue.stop)

    app = FastAPI(title="StrategyLab", version=__version__, lifespan=lifespan)
    app.state.lab = lab

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        host = (request.headers.get("host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name not in LOOPBACK_NAMES:
            return JSONResponse(
                {"detail": f"StrategyLab only answers requests addressed to {HOST}."}, 421
            )
        client = request.client.host if request.client else None
        try:
            if client is not None and not ipaddress.ip_address(client).is_loopback:
                return JSONResponse({"detail": "StrategyLab only answers this machine."}, 403)
        except ValueError:
            pass
        return await call_next(request)

    router = APIRouter(prefix="/api")

    # --- health --------------------------------------------------------------------------------

    @router.get("/health")
    def health() -> Health:
        settings = lab.settings()
        queue = QueueStatus(
            current=lab.queue.current,
            queued=len(lab.store.runs(status=RunStatus.QUEUED, limit=10_000)),
        )
        if settings is None:
            return Health(
                status="unavailable",
                message=lab.settings_error,
                terminal=TerminalStatus(found=False, error=lab.settings_error),
                queue=queue,
                version=__version__,
            )
        install = settings.terminal
        pids = [r.pid for r in terminals_using(install)]
        busy = lab.queue.current
        terminal = TerminalStatus(
            found=True,
            install_dir=str(install.install_dir),
            data_dir=str(install.data_dir),
            portable=install.portable,
            server=settings.account_server,
            source=settings.source,
            notes=list(settings.notes),
            running_pids=pids,
            busy_with_run=busy,
        )
        if pids and busy is None:
            return Health(
                status="attention",
                message=(
                    "The terminal is already running from its data folder, so runs cannot start. "
                    "Close it; StrategyLab starts and stops it itself."
                ),
                terminal=terminal,
                queue=queue,
                version=__version__,
            )
        return Health(
            status="ok", message=None, terminal=terminal, queue=queue, version=__version__
        )

    # --- strategies ----------------------------------------------------------------------------

    def _add_python(name: str, data: bytes) -> StrategyRecord:
        try:
            staged = stage_script(data, name, lab.workspace / "strategies" / "python")
        except ScriptError as exc:
            raise HTTPException(400, str(exc)) from exc
        record = StrategyRecord(
            hash=staged.hash,
            name=name,
            kind="py",
            engine=Engine.PYTHON_SIM,
            folder=str(staged.folder),
            entry=staged.entry,
            parameters=[_script_parameter(i) for i in staged.inputs],
            parameter_source="script",
            ok=staged.ok,
            diagnostics=_diagnostics(staged.diagnostics),
            uploaded_at=now(),
        )
        return lab.store.save_strategy(record)

    def _add_mql5(name: str, data: bytes, set_name: str | None, set_data: bytes | None) -> Any:
        settings = lab.require_settings()
        suffix = PurePosixPath(name).suffix.lower()
        if set_data is not None and suffix != ".ex5":
            raise HTTPException(
                400,
                "A .set file is only needed with a compiled .ex5; for source the inputs are "
                "read from the code.",
            )
        with tempfile.TemporaryDirectory(dir=lab.workspace) as temp:
            upload = Path(temp) / name
            upload_name = name
            if set_data is not None and set_name is not None:
                # Shipped together, the tester's parameters file stays beside the .ex5.
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as bundle:
                    bundle.writestr(name, data)
                    bundle.writestr(set_name, set_data)
                upload = Path(temp) / f"{PurePosixPath(name).stem}.zip"
                upload_name = upload.name
                data = buffer.getvalue()
            upload.write_bytes(data)
            try:
                compiled = lab.services.compiler(upload, settings.terminal, upload_name)
            except CompileError as exc:
                raise HTTPException(400, str(exc)) from exc
        staged = compiled.strategy
        set_files = sorted(f for f in staged.files if f.lower().endswith(".set"))
        shipped = staged.folder / set_files[0] if staged.prebuilt and set_files else None
        try:
            discovery = discover(staged.entry_path, staged.folder, shipped)
            parameters = [_mql5_parameter(p, discovery.source) for p in discovery.params]
            parameter_source = discovery.source
            notes = [discovery.note] if discovery.note else []
        except (OSError, ParamsError) as exc:
            parameters, parameter_source = [], "none"
            notes = [f"The inputs could not be read: {exc}"]
        record = StrategyRecord(
            hash=staged.hash,
            name=name,
            kind=staged.kind,
            engine=Engine.MT5_TESTER,
            folder=str(staged.folder),
            entry=staged.entry,
            parameters=parameters,
            parameter_source=parameter_source,
            ok=compiled.ok,
            diagnostics=_diagnostics(compiled.diagnostics),
            notes=[*compiled.notes, *notes],
            uploaded_at=now(),
        )
        return lab.store.save_strategy(record)

    @router.post("/strategies", status_code=201)
    async def upload_strategy(
        file: Annotated[UploadFile, File()],
        set_file: Annotated[UploadFile | None, File()] = None,
    ) -> StrategyRecord:
        name = _safe_name(file.filename)
        data = await _read_upload(file)
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == ".py":
            if set_file is not None:
                raise HTTPException(400, "A .set file belongs with a compiled .ex5, not a script.")
            return await run_in_threadpool(_add_python, name, data)
        if suffix in (".mq5", ".ex5", ".zip"):
            set_name = _safe_name(set_file.filename) if set_file is not None else None
            if set_name is not None and not set_name.lower().endswith(".set"):
                raise HTTPException(400, f"'{set_name}' is not a .set file.")
            set_data = await _read_upload(set_file) if set_file is not None else None
            return await run_in_threadpool(_add_mql5, name, data, set_name, set_data)
        raise HTTPException(
            415,
            f"'{name}' is not a strategy StrategyLab can run. Upload an MQL5 source file (.mq5), "
            "a compiled expert (.ex5), a .zip with an expert and its include files, or a Python "
            "script (.py).",
        )

    @router.get("/strategies")
    def list_strategies() -> list[StrategyRecord]:
        return lab.store.strategies()

    @router.get("/strategies/{strategy_hash}")
    def get_strategy(strategy_hash: str) -> StrategyDetail:
        strategy = _strategy(strategy_hash)
        return StrategyDetail(strategy=strategy, runs=lab.store.runs(strategy_hash=strategy_hash))

    def _strategy(strategy_hash: str) -> StrategyRecord:
        try:
            return lab.store.strategy(strategy_hash)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    # --- symbols -------------------------------------------------------------------------------

    def _symbols_out(view: CatalogView) -> SymbolsOut:
        return SymbolsOut(
            server=view.server,
            symbols=[
                SymbolOut(
                    name=s.name,
                    description=s.description,
                    digits=s.digits,
                    path=s.path,
                    bars_from=s.bars_from,
                    bars_to=s.bars_to,
                    history_years=list(s.years),
                    ticks_from=s.ticks_from,
                    tick_months=len(s.tick_months),
                    measured=s.measured,
                )
                for s in view.symbols
            ],
            pending=view.pending,
            message=view.message,
        )

    @router.get("/symbols")
    def symbols(refresh: bool = False) -> SymbolsOut:
        return _symbols_out(lab.catalog().view(refresh=refresh))

    # --- runs ----------------------------------------------------------------------------------

    def _check_history(body: RunCreate | RunSettings, engine: Engine) -> None:
        view = lab.catalog().view(only=[body.symbol])
        found = view.get(body.symbol)
        if found is None:
            names = ", ".join(s.name for s in view.symbols) or "none yet"
            raise HTTPException(
                422,
                f"The terminal has no history for {body.symbol}. Symbols with history: {names}. "
                "To add one, open a chart of it in the dedicated terminal once.",
            )
        if found.measured and found.bars_from and found.bars_to:
            last = max(found.bars_to, datetime.now(UTC).date())
            if body.date_from < found.bars_from or body.date_to > last + timedelta(days=1):
                raise HTTPException(
                    422,
                    f"History for {body.symbol} covers {found.bars_from} to {found.bars_to}; "
                    f"choose dates within it (the end date is exclusive, so at most "
                    f"{last + timedelta(days=1)}).",
                )
            if body.date_from > found.bars_to:
                raise HTTPException(
                    422, f"History for {body.symbol} ends on {found.bars_to}; start before then."
                )
        elif body.date_from.year < found.years[0]:
            raise HTTPException(
                422,
                f"History for {body.symbol} starts in {found.years[0]}; choose a later start.",
            )
        if engine is Engine.PYTHON_SIM and body.model is TickModel.REAL_TICKS:
            if found.ticks_from is None:
                raise HTTPException(
                    422,
                    f"There are no recorded ticks for {body.symbol} on this machine. Use "
                    "1-minute bars, or run a real-ticks test in the tester first to download them.",
                )
            if body.date_from < found.ticks_from:
                raise HTTPException(
                    422,
                    f"Recorded ticks for {body.symbol} start in {found.ticks_from:%B %Y}; choose "
                    "a later start or use 1-minute bars.",
                )

    def _validated(strategy: StrategyRecord, body: RunCreate) -> RunSettings:
        from strategylab.backtest import prepare_parameters
        from strategylab.compiler import load_staged
        from strategylab.tester import TIMEFRAMES

        if not strategy.ok:
            raise HTTPException(
                422, f"{strategy.name} has errors; fix them and upload it again before running."
            )
        if body.date_to <= body.date_from:
            raise HTTPException(422, "The end date must be after the start date (it is exclusive).")
        parameters = {name: _text(value) for name, value in body.parameters.items()}
        if strategy.engine is Engine.MT5_TESTER:
            if body.timeframe not in TIMEFRAMES:
                raise HTTPException(422, f"'{body.timeframe}' is not a tester timeframe.")
            try:
                prepare_parameters(load_staged(Path(strategy.folder)), parameters)
            except (ParamsError, CompileError) as exc:
                raise HTTPException(422, str(exc)) from exc
        else:
            if body.timeframe not in PYTHON_TIMEFRAMES:
                raise HTTPException(
                    422,
                    f"Python strategies run on {', '.join(PYTHON_TIMEFRAMES)}, not "
                    f"'{body.timeframe}'.",
                )
            if body.model not in PYTHON_MODELS:
                raise HTTPException(
                    422,
                    "Python strategies run on 1-minute bars (ohlc_m1) or recorded ticks "
                    "(real_ticks).",
                )
            try:
                run_inputs(load_script(Path(strategy.folder)).inputs, parameters)
            except (ScriptError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc
        _check_history(body, strategy.engine)
        return RunSettings(
            symbol=body.symbol,
            timeframe=body.timeframe,
            date_from=body.date_from,
            date_to=body.date_to,
            model=body.model,
            deposit=body.deposit,
            currency=body.currency,
            leverage=body.leverage,
            parameters=parameters,
            commission_per_lot=body.commission_per_lot,
            timeout_s=body.timeout_s,
        )

    def _submit(strategy: StrategyRecord, settings: RunSettings, rerun_of: str | None) -> Any:
        from strategylab.tester import new_run_id

        record = RunRecord(
            id=new_run_id(),
            strategy_hash=strategy.hash,
            strategy_name=PurePosixPath(strategy.name).stem,
            engine=strategy.engine,
            status=RunStatus.QUEUED,
            settings=settings,
            created_at=now(),
            rerun_of=rerun_of,
        )
        return lab.queue.submit(record)

    @router.post("/runs", status_code=201)
    def create_run(body: RunCreate) -> RunRecord:
        strategy = _strategy(body.strategy_hash)
        return _submit(strategy, _validated(strategy, body), None)

    @router.get("/runs")
    def list_runs(
        status: RunStatus | None = None,
        engine: Engine | None = None,
        strategy: str | None = None,
        symbol: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> list[RunRecord]:
        return lab.store.runs(
            status=status,
            engine=engine,
            strategy_hash=strategy,
            symbol=symbol,
            limit=limit,
            offset=offset,
        )

    def _run(run_id: str) -> RunRecord:
        try:
            return lab.store.run(run_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> RunDetail:
        run = _run(run_id)
        try:
            strategy: StrategyRecord | None = lab.store.strategy(run.strategy_hash)
        except NotFoundError:
            strategy = None
        return RunDetail(run=run, strategy=strategy, queue_position=lab.queue.position(run_id))

    @router.post("/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> RunRecord:
        _run(run_id)
        try:
            return lab.queue.cancel(run_id)
        except RunStateError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/runs/{run_id}", status_code=204)
    def delete_run(run_id: str) -> None:
        _run(run_id)
        try:
            lab.queue.delete(run_id)
        except RunStateError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/runs/{run_id}/rerun", status_code=201)
    def rerun(run_id: str, body: RerunRequest) -> RunRecord:
        past = _run(run_id)
        strategy = _strategy(past.strategy_hash)
        old = past.settings
        changes = body.model_dump(exclude_unset=True, exclude={"parameters"})
        merged = RunCreate(
            strategy_hash=strategy.hash,
            **{
                **old.model_dump(exclude={"parameters"}),
                **{k: v for k, v in changes.items() if v is not None},
            },
            parameters={**old.parameters, **body.parameters},
        )
        return _submit(strategy, _validated(strategy, merged), past.id)

    # --- results -------------------------------------------------------------------------------

    def _artefact(run: RunRecord, kind: str, what: str) -> Path:
        path = run.artefacts.get(kind)
        if path is None or not Path(path).is_file():
            state = f"is {run.status}" if not run.status.finished else f"ended {run.status}"
            raise HTTPException(404, f"Run {run.id} has no {what} (it {state}).")
        return Path(path)

    @router.get("/runs/{run_id}/result")
    def result_summary(run_id: str) -> ResultSummary:
        run = _run(run_id)
        path = _artefact(run, "result", "result")
        result = BacktestResult.model_validate_json(path.read_text(encoding="utf-8"))
        return ResultSummary(
            meta=result.meta.model_dump(mode="json"),
            metrics=result.metrics,
            reported=result.reported,
            orders=len(result.orders),
            deals=len(result.deals),
        )

    @router.get("/runs/{run_id}/deals")
    def deals(run_id: str) -> DealsOut:
        run = _run(run_id)
        frame = pd.read_parquet(_artefact(run, "deals", "deals table"))
        return DealsOut(deals=_records(frame), total=len(frame))

    @router.get("/runs/{run_id}/equity")
    def equity(run_id: str, max_points: int = Query(1500, ge=10, le=100_000)) -> SeriesOut:
        run = _run(run_id)
        frame = pd.read_parquet(_artefact(run, "equity", "balance series"))
        if frame.empty:
            return SeriesOut(points=[], total=0, downsampled=False)
        full = drawdown_points(frame)
        sampled = downsample(full, max_points)
        points = [
            SeriesPoint(
                time=None if pd.isna(row.time) else row.time,
                server_time=row.server_time,
                balance=row.balance,
                equity=None if row.equity is None or pd.isna(row.equity) else row.equity,
                drawdown=row.drawdown,
                drawdown_pct=row.drawdown_pct,
            )
            for row in sampled.itertuples()
        ]
        return SeriesOut(points=points, total=len(full), downsampled=len(sampled) < len(full))

    @router.get("/runs/{run_id}/bars")
    def bars(
        run_id: str,
        around: int | None = None,
        before: int | None = None,
        after: int | None = None,
        count: int = Query(500, ge=1, le=5000),
    ) -> BarsOut:
        run = _run(run_id)
        if not run.status.finished or run.status is not RunStatus.DONE:
            raise HTTPException(409, f"Run {run_id} has no chart until it is done.")
        try:
            frame = lab.bars.bars(run, lab.queue.runs_dir / run.id)
        except BarsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc
        part, first = window(frame, around=around, before=before, after=after, count=count)
        return BarsOut(
            timeframe=run.settings.timeframe,
            bars=[Bar(**point) for point in as_points(part)],
            first_index=first,
            total=len(frame),
        )

    @router.get("/metrics")
    def metric_definitions() -> dict[str, str]:
        from strategylab.metrics import DEFINITIONS

        return dict(DEFINITIONS)

    @router.get("/runs/{run_id}/logs")
    def logs(run_id: str) -> list[LogSource]:
        run = _run(run_id)
        return [
            LogSource(source=kind.removeprefix("log_"), size=Path(path).stat().st_size)
            for kind, path in sorted(run.artefacts.items())
            if kind.startswith("log_") and Path(path).is_file()
        ]

    @router.get("/runs/{run_id}/logs/{source}", response_class=PlainTextResponse)
    def log_text(
        run_id: str, source: str, tail_bytes: int = Query(4_000_000, ge=1)
    ) -> PlainTextResponse:
        run = _run(run_id)
        path = _artefact(run, f"log_{source}", f"{source} log")
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - tail_bytes))
            text = handle.read().decode("utf-8", "replace")
        return PlainTextResponse(text, headers={"X-Log-Size": str(size)})

    report_policy = (
        "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "frame-ancestors 'self'"
    )

    @router.get("/runs/{run_id}/report/")
    def report(run_id: str) -> FileResponse:
        run = _run(run_id)
        path = _artefact(run, "report", "Strategy Tester report")
        # The report repeats text the strategy chose (comments, names), so it is served as an
        # isolated document that can run nothing.
        return FileResponse(
            path, media_type="text/html", headers={"Content-Security-Policy": report_policy}
        )

    @router.get("/runs/{run_id}/report/{name}")
    def report_file(run_id: str, name: str) -> FileResponse:
        run = _run(run_id)
        folder = _artefact(run, "report", "Strategy Tester report").parent
        candidate = folder / name
        if PurePosixPath(name).name != name or not candidate.is_file():
            raise HTTPException(404, f"The report has no file '{name}'.")
        return FileResponse(candidate, headers={"Content-Security-Policy": report_policy})

    # --- events --------------------------------------------------------------------------------

    async def _stream(request: Request, run_id: str | None) -> AsyncIterator[str]:
        header = request.headers.get("last-event-id")
        seq = int(header) if header and header.isdigit() else 0
        if run_id is not None:
            run = _run(run_id)
            yield _sse("state", {"id": run.id, "status": str(run.status), "snapshot": True})
        quiet = 0.0
        final_sent = False
        waited_for_final = 0.0
        while True:
            if await request.is_disconnected():
                return
            latest = lab.events.last_seq
            for event in lab.events.since(seq, run_id):
                seq = event.seq
                yield _sse(event.kind, {"run_id": event.run_id, **event.data}, event.seq)
                quiet = 0.0
                if event.kind == "state" and event.data.get("status") in FINISHED_STATES:
                    final_sent = True
            seq = max(seq, latest)
            if run_id is not None:
                try:
                    finished = lab.store.run(run_id).status.finished
                except NotFoundError:
                    finished = True
                # The store says finished a moment before the final event is published; wait
                # for it, unless the run ended before this server held its events.
                if finished and not lab.events.since(seq, run_id):
                    if final_sent or waited_for_final >= FINAL_EVENT_GRACE_S:
                        return
                    waited_for_final += EVENT_POLL_S
            await asyncio.sleep(EVENT_POLL_S)
            quiet += EVENT_POLL_S
            if quiet >= KEEP_ALIVE_S:
                quiet = 0.0
                yield ": still here\n\n"

    @router.get("/events")
    async def all_events(request: Request) -> StreamingResponse:
        return StreamingResponse(_stream(request, None), media_type="text/event-stream")

    @router.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str) -> StreamingResponse:
        _run(run_id)
        return StreamingResponse(
            _stream(request, run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    app.include_router(router)
    return app


def main() -> None:
    import uvicorn

    # Fixed, not configurable: see the module docstring.
    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
