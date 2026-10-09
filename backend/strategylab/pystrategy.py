"""Python strategy uploads: staging, a syntax check, and the inputs a script declares.

A script is never imported or run here; everything is read from its syntax tree. Two kinds of
input are recognised, the two ways scripts are usually made configurable:

- module-level constants in capitals assigned a literal (`FAST_PERIOD = 12`, `SYMBOL =
  "EURUSD"`), with a trailing comment as the label, much like an MQL5 `input`. A run can give
  them other values: the literal is replaced when the script is loaded, and the file on disk is
  never changed.
- command-line options declared with argparse (`parser.add_argument("--fast", type=int,
  default=12)`). A run passes them on the command line.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from strategylab.compiler import Diagnostic, content_hash, safe_file_name

MANIFEST_NAME = "strategy.json"
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

InputKind = Literal["bool", "integer", "real", "string", "enum"]


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class ScriptInput:
    name: str
    """The constant's name, or the option's flag (`--fast`)."""
    source: Literal["constant", "option"]
    kind: InputKind
    default: str
    """The default as text: numbers as written, booleans as true/false."""
    label: str | None = None
    choices: tuple[str, ...] = ()
    """For an option declared with choices=: the allowed values as text."""
    value_kind: InputKind | None = None
    """For a choice option, the kind of the values themselves."""
    line: int = 0


@dataclass(frozen=True)
class StagedScript:
    hash: str
    folder: Path
    entry: str
    upload_name: str
    diagnostics: tuple[Diagnostic, ...] = ()
    inputs: tuple[ScriptInput, ...] = ()

    @property
    def entry_path(self) -> Path:
        return self.folder / self.entry

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


# --- reading the script ------------------------------------------------------------------------


def _literal(node: ast.expr) -> bool | int | float | str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool | int | float | str):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub | ast.UAdd)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int | float)
        and not isinstance(node.operand.value, bool)
    ):
        value = node.operand.value
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _kind_of(value: bool | int | float | str) -> InputKind:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    return "string"


def _text(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _trailing_comment(lines: list[str], node: ast.AST) -> str | None:
    end_line = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if end_line is None or end_col is None or end_line > len(lines):
        return None
    rest = lines[end_line - 1][end_col:].strip()
    if rest.startswith("#"):
        return rest.lstrip("#").strip() or None
    return None


def _constants(tree: ast.Module, lines: list[str]) -> list[ScriptInput]:
    assigned: dict[str, int] = {}
    found: dict[str, ScriptInput] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            assigned[target.id] = assigned.get(target.id, 0) + 1
            literal = _literal(value) if len(targets) == 1 else None
            if literal is None or not CONSTANT_NAME.match(target.id):
                continue
            found[target.id] = ScriptInput(
                name=target.id,
                source="constant",
                kind=_kind_of(literal),
                default=_text(literal),
                label=_trailing_comment(lines, node),
                line=node.lineno,
            )
    # A name set twice at module level is not a simple setting: replacing one of its values
    # would not decide what the script uses.
    return [item for name, item in found.items() if assigned[name] == 1]


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _option(call: ast.Call) -> ScriptInput | None:
    flags = [_literal(a) for a in call.args]
    names = [f for f in flags if isinstance(f, str) and f.startswith("-")]
    if not names or len(names) != len(flags):
        return None
    name = next((f for f in names if f.startswith("--")), names[0])
    action = _keyword(call, "action")
    action_name = _literal(action) if action is not None else None
    help_node = _keyword(call, "help")
    label = _literal(help_node) if help_node is not None else None
    label = label if isinstance(label, str) else None
    if action_name in ("store_true", "store_false"):
        return ScriptInput(
            name=name,
            source="option",
            kind="bool",
            default="false" if action_name == "store_true" else "true",
            label=label,
            line=call.lineno,
        )
    if action_name is not None and action_name != "store":
        return None
    default_node = _keyword(call, "default")
    default = _literal(default_node) if default_node is not None else None
    type_node = _keyword(call, "type")
    declared = type_node.id if isinstance(type_node, ast.Name) else None
    if declared is not None and declared not in ("int", "float", "str"):
        return None
    kind: InputKind = {"int": "integer", "float": "real", "str": "string"}.get(
        declared or "", _kind_of(default) if default is not None else "string"
    )  # type: ignore[assignment]
    choices: tuple[str, ...] = ()
    choices_node = _keyword(call, "choices")
    if isinstance(choices_node, ast.List | ast.Tuple):
        values = [_literal(e) for e in choices_node.elts]
        if all(v is not None for v in values):
            choices = tuple(_text(v) for v in values if v is not None)
    return ScriptInput(
        name=name,
        source="option",
        kind="enum" if choices else kind,
        default=_text(default) if default is not None else "",
        label=label,
        choices=choices,
        value_kind=kind if choices else None,
        line=call.lineno,
    )


def _options(tree: ast.Module) -> list[ScriptInput]:
    found: dict[str, ScriptInput] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            option = _option(node)
            if option is not None and option.name not in ("-h", "--help"):
                found.setdefault(option.name, option)
    return sorted(found.values(), key=lambda item: item.line)


def _imports_metatrader(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "MetaTrader5" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "MetaTrader5":
            return True
    return False


def read_script(source: bytes, file_name: str) -> tuple[list[Diagnostic], list[ScriptInput]]:
    """Syntax errors and warnings for a script, and the inputs it declares."""
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        message = f"The script is not UTF-8 text (byte {exc.start} cannot be read)."
        return [Diagnostic("error", message, file=file_name)], []
    try:
        tree = ast.parse(text, filename=file_name)
    except SyntaxError as exc:
        return [
            Diagnostic(
                "error",
                exc.msg,
                file=file_name,
                line=exc.lineno,
                column=exc.offset,
            )
        ], []
    diagnostics = []
    if not _imports_metatrader(tree):
        diagnostics.append(
            Diagnostic(
                "warning",
                "The script never imports MetaTrader5, so it cannot see prices or trade.",
                file=file_name,
            )
        )
    lines = text.splitlines()
    return diagnostics, [*_constants(tree, lines), *_options(tree)]


# --- staging -----------------------------------------------------------------------------------


def stage_script(data: bytes, upload_name: str, scripts_dir: Path) -> StagedScript:
    """Store an uploaded script in its own folder, named by a hash of its name and bytes."""
    if not upload_name.lower().endswith(".py"):
        raise ScriptError(f"'{upload_name}' is not a Python script.")
    if len(data) > MAX_SCRIPT_BYTES:
        raise ScriptError(
            f"'{upload_name}' is {len(data) // 1024} KB; a strategy script can be at most "
            f"{MAX_SCRIPT_BYTES // 1024 // 1024} MB."
        )
    entry = safe_file_name(Path(upload_name.replace("\\", "/")).name)
    digest = content_hash({entry: data})
    folder = scripts_dir / digest
    diagnostics, inputs = read_script(data, entry)
    staged = StagedScript(
        hash=digest,
        folder=folder,
        entry=entry,
        upload_name=upload_name,
        diagnostics=tuple(diagnostics),
        inputs=tuple(inputs),
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / entry).write_bytes(data)
    manifest = {
        "hash": digest,
        "entry": entry,
        "upload_name": upload_name,
        "staged_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (folder / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return staged


def load_script(folder: Path) -> StagedScript:
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    entry = manifest["entry"]
    diagnostics, inputs = read_script((folder / entry).read_bytes(), entry)
    return StagedScript(
        hash=manifest["hash"],
        folder=folder,
        entry=entry,
        upload_name=manifest["upload_name"],
        diagnostics=tuple(diagnostics),
        inputs=tuple(inputs),
    )


# --- applying a run's values -------------------------------------------------------------------


def parse_value(item: ScriptInput, text: str) -> bool | int | float | str:
    """A value given for an input, checked against its kind."""
    kind = item.value_kind or item.kind
    raw = text.strip()
    if item.choices and raw not in item.choices:
        raise ScriptError(f"{item.name} must be one of {', '.join(item.choices)}, got '{text}'.")
    if kind == "bool":
        if raw.lower() not in ("true", "false"):
            raise ScriptError(f"{item.name} needs true or false, got '{text}'.")
        return raw.lower() == "true"
    if kind == "integer":
        try:
            return int(raw)
        except ValueError:
            raise ScriptError(f"{item.name} needs a whole number, got '{text}'.") from None
    if kind == "real":
        try:
            return float(raw)
        except ValueError:
            raise ScriptError(f"{item.name} needs a number, got '{text}'.") from None
    if any(ch in text for ch in "\r\n\x00"):
        raise ScriptError(f"{item.name} cannot contain a line break.")
    return text


def run_inputs(
    inputs: tuple[ScriptInput, ...] | list[ScriptInput], values: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """The constants to replace and the command-line arguments for a run's values.

    Only values that differ from the declared default are applied, so a script runs exactly as
    written unless something was changed.
    """
    by_name = {item.name: item for item in inputs}
    unknown = sorted(set(values) - set(by_name))
    if unknown:
        raise ScriptError(
            f"The script has no inputs named {', '.join(unknown)}. Its inputs are: "
            f"{', '.join(by_name) or 'none'}."
        )
    constants: dict[str, Any] = {}
    args: list[str] = []
    for name, text in values.items():
        item = by_name[name]
        value = parse_value(item, text)
        if item.source == "constant":
            if _text(value) != item.default:
                constants[name] = value
        elif item.kind == "bool":
            if _text(value) != item.default:
                args.append(name)
        elif _text(value) != item.default:
            args += [name, _text(value)]
    return constants, args


def apply_constants(tree: ast.Module, constants: dict[str, Any]) -> None:
    """Replace the literal assigned to each named module-level constant."""
    for node in tree.body:
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id in constants:
            replacement = ast.Constant(constants[targets[0].id])
            ast.copy_location(replacement, node.value)  # type: ignore[arg-type]
            node.value = replacement
