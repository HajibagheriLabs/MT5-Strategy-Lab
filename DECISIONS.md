# Decisions

What StrategyLab relies on about MetaTrader 5, how each point was checked, and the choices that
follow from it. "Verified" means checked against the official help or against files and
behaviour of a real terminal. Unless stated otherwise the terminal was MetaTrader 5 build 6249
(x64) on Windows 10, checked on 2026-10-09.

## Terminal installs and data folders

Sources: [Platform Start](https://www.metatrader5.com/en/terminal/help/start_advanced/start)
and the files of two real installs (one regular install under Program Files, one dedicated copy
started with `/portable`).

- **Data folder location (documented).** Without `/portable`, an install under Program Files
  keeps its data in `%APPDATA%\MetaQuotes\Terminal\<instance id>\`, where the id is derived
  from the install path. With `/portable`, the data folder is the install folder itself.
- **`origin.txt` (documented, encoding verified).** Every data folder under `%APPDATA%` has an
  `origin.txt` holding the install path. The file is UTF-16LE with a BOM. This is how an install
  is mapped to its data folder; the instance id is never recomputed.
- **Portable detection (verified).** The first start with `/portable` creates `MQL5\`,
  `config\`, `logs\`, `bases\` and `Tester\` inside the install folder and unpacks the example
  programs there. A regular install folder has none of these (its `Config\` holds only
  `servers.dat` and the licence). Discovery therefore treats "has an `MQL5` folder" as
  "has been run portable".
- **One terminal per data folder (documented).** "Two copies of the platform cannot run from the
  same directory." StrategyLab checks running processes before using a data folder.
- **A dedicated copy works by copying the install folder (verified).** Copying an installed
  terminal's folder elsewhere and starting `terminal64.exe /portable` from the copy gives a
  working, independent terminal.

### Discovery order

1. `local.toml` (path in `STRATEGYLAB_CONFIG`, else the repository root). When it names a
   terminal, that terminal is used or a specific error is raised; nothing is guessed.
2. Otherwise: every data folder's `origin.txt`, plus one level below Program Files,
   Program Files (x86), `%LOCALAPPDATA%\Programs` and the system drive root. If exactly one
   portable copy has been started it is chosen; otherwise, if exactly one install is usable, it
   is chosen with a note that it is not a dedicated copy; otherwise discovery stops and lists
   the candidates. Installs that were never started are reported as such.

### Detecting a running terminal

Each running `terminal64.exe` is mapped to a data folder: its own folder if its command line
contains `/portable`, otherwise the folder whose `origin.txt` names its install folder. If a
process's data folder cannot be worked out but its executable is the configured terminal, it
counts as using that data folder: refusing to start is cheap, and two terminals sharing a data
folder is not. Process paths and command lines come from `psutil`.

## MetaEditor command line

Source: [Compiling from the command line](https://www.metatrader5.com/en/metaeditor/help/beginning/integration_ide),
which documents `/compile`, `/include`, `/log` and `/s`. Everything else below was verified by
running `MetaEditor64.exe` directly.

| Behaviour | How it was checked |
|---|---|
| Values must be quoted after the colon: `/compile:"C:\a b\x.mq5"`. Quoting the whole switch (`"/compile:C:\a b\x.mq5"`, which is what an argument list produces) makes MetaEditor exit at once with code 0, no log and no `.ex5`. | Ran both forms on the same file |
| `/portable` is accepted by MetaEditor too, and makes it use the install folder as its data folder, so `<x.mqh>` includes resolve under the portable `MQL5\Include`. The documentation page does not list it. | Compile log shows includes read from `C:\MT5-Lab\MQL5\Include` |
| `/log:"path"` writes the log to that path. A bare `/log` writes `<source name>.log` beside the source. | Both forms |
| The log is UTF-16LE with a BOM and CRLF line endings. | Raw bytes of the log |
| Exit code is 1 after a successful compile and 0 after a failed one. It is also 0 when the command line was not understood, so it is recorded but never used to decide success. | Clean, failing and misquoted compiles |
| A failed compile deletes the existing `.ex5`. StrategyLab deletes it itself before compiling anyway, so a stale binary can never be mistaken for fresh output. | Compiled a file cleanly, then a failing variant into the same folder |
| `/include:"<folder>"` **replaces** the include root rather than adding to it: `<Trade\Trade.mqh>` is then looked for under `<folder>\Include\` and fails. StrategyLab always passes the data folder's own `MQL5`, which keeps the standard library and pins the root to the folder it resolved. | Compiled with `/include` pointing at the strategy folder, then at `MQL5` |
| `#include <x>` in the main file also finds `<main file folder>\Include\x`, but a header that includes another header the same way does not get that fallback. Quoted includes resolve relative to the including file and accept both `\` and `/`, including `..\`. | Five small bundles with different layouts |
| A single-file compile takes 0.4 to 0.8 s for the Moving Average example. | Timings in the log's result line |

### Log format

```
<path> : information: compiling <path>
<path> : information: including <path>
<path>(<line>,<column>) : error <code>: <message>
<path>(<line>,<column>) : warning <code>: <message>
(1,1) : error 161: unexpected end of program
 : information: generating code 50%
Result: 10 errors, 1 warnings
Result: 0 errors, 0 warnings, 724 ms elapsed, cpu='X64 Regular'
```

Diagnostics can lack a file (the `(1,1)` line above). Paths can contain parentheses, so the
position is taken from the last `(<line>,<column>)` before ` : error|warning`. Lines and columns
are reported exactly as MetaEditor gives them; for a missing semicolon that is the position of
the next token, which may be a later line, just as in MetaEditor's own Errors tab. A compile
counts as successful only when the result line reports zero errors and the `.ex5` exists. If the
number of parsed diagnostics differs from the result line, the result carries a note saying so.

Messages are parsed in English. Whether MetaEditor translates `error`, `warning` and `Result` when
the terminal runs in another language has not been checked; if it does, compiles will be
reported as "no result line" rather than misread.

## Strategy folders

- Every upload is staged into `MQL5\Experts\StrategyLab\<hash>\`, so the Strategy Tester can
  find it as `StrategyLab\<hash>\<name>.ex5` and uploads never mix.
- `<hash>` is the first 16 hex digits of a SHA-256 over every staged file's relative path and
  bytes. File names are part of it because the tester identifies an expert by its path; a
  renamed upload is a different strategy. 16 digits keep paths well under Windows' 260
  character limit.
- Folders are built under a temporary name and renamed into place, so a half-written folder is
  never taken for a staged strategy. A `strategy.json` manifest in each folder records the
  upload and the last compile result. A result is reused while the MetaEditor executable is
  unchanged (size and modification time); a terminal update recompiles on next use.
- Failed compiles are cached as well: the same source fails the same way until MetaEditor
  changes. `--force` in `scripts/doctor.py` (and `force=True` in code) recompiles.

### Uploads

- **`.mq5`**: staged under its own name, with characters outside letters, digits and
  ` .()[]+-` replaced by `_`.
- **`.ex5`**: staged and used as is; there is no source to compile or check.
- **`.zip`**: unpacked with its layout kept. Folders that wrap everything (`MyEA/`, `MQL5/`) are
  dropped. Archive clutter (`__MACOSX`, `.DS_Store`, `Thumbs.db`, ...) is ignored. Absolute
  paths, `..`, names Windows cannot store, and two names that differ only by case are rejected.
  DLLs and executables are rejected: native code would run inside the terminal with the user's
  rights. Limits: 2000 files, 64 MB unpacked, enforced while reading rather than trusting the
  archive's own size fields.
- **Choosing the EA in a zip**: the only `.mq5` outside `Include/`; if there are several, the
  only one defining `OnTick`; otherwise the upload is rejected with the list. A zip with no
  source but exactly one `.ex5` is a prebuilt upload.
- **Bundled headers**: because of the include behaviour above, a bundle that ships its own
  headers under `Include/` and refers to them as `<Lib\x.mqh>` would not compile in isolation.
  In the staged copy, each such directive is rewritten to a quoted path relative to the
  including file (`"..\Include\Lib\x.mqh"`), keeping the file's encoding and BOM. Directives
  for headers the bundle does not ship (the standard library) are left alone. The hash is of
  the upload as received, and every rewrite is listed in the compile result. Copying bundle
  headers into the shared `MQL5\Include` was rejected: uploads would overwrite each other.

## Local configuration

`local.toml` holds the terminal folder, the portable flag, an optional data folder override,
the trade server name and the workspace folder. It never holds account credentials: the
terminal is logged in by hand once and remembers the account. Unknown sections and keys are
errors, so a typo cannot silently fall back to defaults.

## Strategy Tester runs

Source: the [Platform Start](https://www.metatrader5.com/en/terminal/help/start_advanced/start)
page ("Running with a Custom Configuration File", `[Tester]` and `[Experts]` sections) and test
runs of the shipped Moving Average EA on EURUSD H1 with a demo account.

### Configuration keys

Every key StrategyLab writes, with the value used. All are documented on that page; the
"Checked" column says what was confirmed by running it.

| Section | Key | Value | Checked |
|---|---|---|---|
| Experts | `AllowLiveTrading` | `0` | The test still runs. |
| Experts | `AllowDllImport` | `0` | The test still runs. |
| Experts | `Enabled` | `0`, so no chart EA can run while StrategyLab drives the terminal | The tester still runs the expert under test. |
| Tester | `Expert` | path relative to `MQL5\Experts`, no extension | `StrategyLab\<hash>\Moving Average` loads `...\Moving Average.ex5`; a missing file ends the run with "EX5 not found". |
| Tester | `ExpertParameters` | `strategylab-<run id>.set` in `MQL5\Profiles\Tester` | Always written, even when empty: without it the tester reuses `<EA name>.set`, the inputs last used for any EA of that name. An empty file gives the EA's defaults; `Name=value` lines in UTF-16LE override single inputs. |
| Tester | `Symbol` | as named on the server, suffix included (`EURUSD@`) | An unknown symbol ends the run with "symbol ... not exist". |
| Tester | `Period` | one of the 21 timeframes, `M1` to `MN1` | |
| Tester | `Model` | `0` every tick, `1` 1 minute OHLC, `2` open prices, `4` real ticks (`3`, math calculations, is not a trading backtest) | The tester log names the mode, e.g. "1 minutes OHLC ticks generating". |
| Tester | `ExecutionMode` | `0`, no artificial delay | |
| Tester | `Optimization` | `0` | |
| Tester | `ForwardMode` | `0` | |
| Tester | `FromDate`, `ToDate` | `YYYY.MM.DD` | **`ToDate` is exclusive**: the tester logs "from 2025.06.02 00:00 to 2025.06.04 00:00", and the last deal of a one-year run is "end of test" at 2025.12.31 22:59:59. StrategyLab uses the same convention everywhere so a run can be repeated by hand with identical dates. |
| Tester | `Deposit`, `Currency`, `Leverage` | e.g. `10000`, `USD`, `1:100` | Echoed in the report's settings. |
| Tester | `Report` | `StrategyLab\reports\<run id>\report`, relative | **An absolute path is silently ignored**: no report and no journal line. A path relative to the terminal folder works when the folder exists, and `.htm` is added. Four PNG charts are written beside it. Verified for a portable install only; for a regular install the help says "the trading platform directory", which StrategyLab takes to be the data folder. |
| Tester | `ReplaceReport` | `1` | |
| Tester | `ShutdownTerminal` | `1` | The terminal exits after the test. |
| Tester | `Visual` | `0` | |
| Tester | `UseLocal`, `UseRemote`, `UseCloud` | `1`, `0`, `0` | Strategies and history never leave the machine. |

`Login` and `Port` are not written: the terminal uses the account it is logged in to, and one
local agent is enough for one test at a time. The configuration file is UTF-16LE with a BOM,
like the terminal's own ini files, and the terminal is started as
`terminal64.exe /portable /config:"<file>"` (only portable installs get `/portable`).

### Outcomes

| Outcome | How it is recognised |
|---|---|
| success | A report with at least one closing deal. |
| zero_trades | A report whose Deals table holds only the initial balance deal. |
| no_report | No report file. The terminal journal says why: warning lines from the tester and a final `shutdown with <code> (<reason>)`, e.g. `-1000012358 (tester symbol does not exist)` or `-1000012355 (tester EX5 not found)`. The same code is the process exit code, read unsigned. A successful run exits with 0. |
| timeout | The process outlived the time limit. It is asked to close through its window (WM_CLOSE), which the terminal honours with "shutdown with 0", and is killed with its child processes only if it is still running 30 s later. |
| terminal_running | Detected before launch from the process list (see above). Verified separately: starting a second copy with `/config` while the terminal runs hands over to the running copy and exits within a second with code 0, with no test, no report and no journal line. Because that looks like success from outside, a launch that returns within five seconds without the journal line `launched with <config>` is also treated as this outcome. |

### Journals collected per run

The terminal journal (`logs\YYYYMMDD.log`), the tester journal (`Tester\logs\`) and the agent
journal (`Tester\Agent-127.0.0.1-3000\logs\`, which holds the EA's own `Print` output and every
trade) are all UTF-16LE. Their sizes are noted before launch and only the bytes appended during
the run are kept, converted to UTF-8, in `workspace\runs\<run id>\logs\`.

## Strategy Tester report

The report checked is the one build 6249 writes for a single test: HTML 4, UTF-16LE with a BOM,
two tables.

- **First table**: a title row, a `<server> (Build <n>)` row, then the Settings and Results
  sections, each under a single 13-column heading cell. Settings rows come in a fixed order:
  Expert, Symbol, Period (`H1 (2025.01.01 - 2026.01.01)`), Inputs (one `Name=value` per row,
  only the first labelled), Company, Currency, Initial Deposit, Leverage (`1:100`). Results are
  label/value cell pairs, up to three per row.
- **Second table**: an `Orders` heading, a column-title row and 11-column rows; then a `Deals`
  heading, a column-title row, 13-column rows and a totals row. The Orders volume column reads
  `initial / filled`: a cancelled buy limit shows `0.1 / 0`.
- **Numbers** group thousands with a space (`10 000.00`) and use `.` for decimals. Times are
  server time, `YYYY.MM.DD HH:MM:SS`.

### Parsing choices

- The file's encoding is detected from its BOM, never assumed.
- Settings, Orders and Deals are read **by position**, so a report written by a terminal in
  another language parses the same way. A test swaps the English labels for German ones to keep
  this true.
- Results figures are kept exactly as printed, keyed by label, in `reported`. The keys are the
  English labels when the terminal runs in English and that language's labels otherwise.
  StrategyLab's own figures are computed from the deals, never taken from `reported`.
- Number parsing accepts spaces, no-break spaces, apostrophes, commas or dots as grouping, and
  `.` or `,` as the decimal separator (the later of the two when both appear; a lone `,` is
  decimal unless exactly three digits follow it).
- The balance series is rebuilt by summing each deal's profit, commission and swap. The report's
  own balance column is used only as a check, and any disagreement is recorded in the result's
  notes. The PNG charts are never read.
- The report does not say which tick model was used, so that comes from the run's settings.
- Not supported: optimisation reports (XML), forward-test reports, and multi-symbol tests (they
  parse, but the run metadata describes the main symbol only).

## Server time and UTC

Everything MetaTrader produces is stamped in the trade server's clock. The demo server here was
measured to move between UTC+2 and UTC+3, but **not by one rule**: up to spring 2024 it changed
on European dates (end of March, end of October), and from autumn 2024 on US dates (2024-11-04,
2025-03-10, 2025-11-03, 2026-03-09). Any fixed rule would have been an hour wrong for weeks.

So the offset is measured per trading week from the server's own H1 history of an FX major. The
FX week opens at 17:00 New York time on Sunday, so the first bar after each weekend says how far
the server clock was from UTC that week. Only gaps of 36 hours or more that end on a Sunday or
Monday count, and measurements more than an hour from the median are dropped (a late open after
a holiday, such as Monday 06:00 after Christmas). Clock changes happen while FX is closed, so one
offset per week is exact for FX deals; an instrument that trades at weekends could be an hour out
between a change and the next weekly open.

The measured weeks are cached per server in `workspace\cache\server_clock\` and measured again
when a run's dates are not covered. Results carry both `server_time` (as MetaTrader shows it) and
`time` (UTC). `time` is left empty, with a note, for any moment the clock does not cover.

Measuring needs the terminal through the `MetaTrader5` package. `mt5.initialize()` starts the
terminal when it is not running, but `mt5.shutdown()` only disconnects, leaving the terminal
running (verified). StrategyLab closes a terminal it had to start, through its window, before
launching the tester.

## History available through the MetaTrader5 package

`copy_rates_range` only returns bars within the terminal's "Max bars in chart" limit, counted
back from now (`[Charts] MaxBars=100000` in `config\common.ini` by default). That covers about
16 years of H1 but only about 70 trading days of M1: a request for 2025 M1 bars returned one bar
per month. The Strategy Tester is not affected; it downloads its own history.
