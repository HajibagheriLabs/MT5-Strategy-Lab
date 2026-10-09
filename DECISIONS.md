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

## Inputs and .set files

### How the tester writes a .set file

The reference files are ones the Strategy Tester wrote itself. It saves the inputs last used for
an EA as `MQL5\Profiles\Tester\<EA name>.set` when the terminal closes (not after a `/config`
run). `tests/fixtures/mql5/AllInputs.set` was saved that way for an EA declaring every input
type, and `Moving Average.set` for the shipped example after a run with two inputs overridden.

- UTF-16LE with a BOM, CRLF line ends, three header lines (`; saved automatically on ...`).
- `input group "Name"` becomes a `; Name` line before the group's inputs.
- Numbers, booleans, enums and datetimes: `name=value||start||step||stop||N`, where the last
  field says whether the input is optimised (`Y`/`N`). The default range is taken from the
  **compiled default**, not the current value (`MovingPeriod=24||12||1||120||N` for a default of
  12): start is the default, step 1 (a tenth of the default for reals, printed with six decimals)
  and stop ten times the default. Booleans are `value||false||0||true||N`; enums
  `value||<lowest member>||0||<highest member>||N`.
- Strings, colours and `sinput` inputs are a plain `name=value`.
- Enums and colours are written as integers, colours laid out `0x00BBGGRR` (`clrDodgerBlue` is
  16748574, `C'255,128,0'` is 33023, `clrNONE` is 4294967295); datetimes as seconds since 1970
  counted in the clock the text was written in (`D'2025.01.02 03:04:05'` is 1735787045); reals
  as their shortest form (`0.10` is `0.1`, `3` is `3.0`); booleans as `true`/`false`.

A .set built by StrategyLab from the parsed source of `AllInputs.mq5` is identical, line for
line, to the one the tester wrote (header aside), and reading then writing either tester file
reproduces it byte for byte; both are tests. For a run, only the `name=value` part matters: a
file of plain `name=value` lines is accepted and inputs it leaves out keep their compiled
defaults (verified in the tester log).

### Reading inputs from source

Inputs are `input`/`sinput` declarations up to their semicolon, which may span lines and may
contain semicolons inside literals; the comment after the semicolon is the label the tester
shows. Several inputs can share one declaration. Group headings take no semicolon. Enums come
from the strategy's own files (members numbered from 0 or from an explicit value, each labelled
by the comment on its line) or from a table of standard MQL5 enums. Includes are followed into
headers that were uploaded with the strategy, at the point of inclusion, so inputs keep their
source order.

Named constants used as defaults come from `mql5_constants.py`: the standard enums
(`ENUM_TIMEFRAMES`, `ENUM_MA_METHOD`, `ENUM_APPLIED_PRICE`, ...) and the 140 web colours. All
193 values were checked by an expert that printed each one from inside the tester; none
differed. A default that is an expression (`FastPeriod * 2`) or an unknown constant is left
unresolved: it is shown as written and left out of the .set file, so the EA uses its compiled
default rather than a guess.

A compiled `.ex5` carries no readable input declarations. Its inputs can come only from a .set
file uploaded with it, and the user is told so when there is none.

## Metrics

Definitions are in `metrics.py` and are compared with MetaTrader's own figures in
`reports/metric_crosscheck.md`, regenerated by `scripts/metric_crosscheck.py` from four real
reports and checked by a test. Findings that shaped the definitions:

- A trade is a deal that closes a position, and its result is that deal's profit + swap +
  commission. This reproduces MetaTrader's gross profit, gross loss, counts and averages to the
  cent; profit alone does not.
- Among equally long winning (losing) runs MetaTrader reports the one with the largest total
  profit (loss). Established on nine reports, including one where that run is neither the first
  nor the last of the tied ones.
- Balance drawdowns use a running high that starts at the deposit, which matches exactly.
- Recovery factor and Sharpe ratio are defined differently on purpose: MetaTrader uses the equity
  curve for both (its Sharpe ratio "includes equity movements", per the build 3210 release
  notes), and a deals table has no equity. StrategyLab's versions use the balance, and the
  cross-check shows MetaTrader's recovery factor is reproduced exactly when its own equity
  drawdown is used.
- Where there are no trades MetaTrader prints 0 for ratios and averages; StrategyLab leaves them
  empty.
- Not verified: how MetaTrader attributes commission charged on entry deals to trades, because
  the demo account charges no commission.

## History export

`mt5_data.py` is the only module that imports the `MetaTrader5` package; a test scans the source
tree to keep it so, and another scans `mt5_data.py` for any trading call. It reads through a
data session:

- If the terminal is not running, StrategyLab starts it itself with a configuration file that
  raises `[Charts] MaxBars` to 10 000 000 for that session and switches off chart EAs and live
  trading (`[Experts] Enabled=0`, `AllowLiveTrading=0`). Verified: the session reports
  `maxbars=10000000`, a full month of 2025 M1 bars comes back (31 418 bars for January), and the
  terminal's own `config\common.ini` still says 100 000 afterwards.
- `mt5.initialize()` then attaches to that terminal. If StrategyLab's copy is not up in time the
  package starts its own copy without those settings (seen once, when the previous terminal
  was still closing), so a session that finds the default bar limit is refused rather than
  allowed to return two months of M1 as if it were a year. Launches that exit at once are
  retried.
- When the session ends, every terminal on that data folder that was not running before is
  closed through its window.
- Bars are fetched a month at a time and ticks a day at a time, each request repeated until the
  count stops changing, because history arrives in the background.
- Exports are cached as parquet under `workspace\cache\history\<server>\<symbol>\`, keyed by
  timeframe and date range. A range that reaches today is never cached. The symbol's full
  `symbol_info()` is saved alongside with the account currency and margin mode.

A simulated run uses M1 bars from 30 days before its start (the warm-up), the run's own
timeframe for 1000 bars before that, and optionally the recorded ticks of the run itself. The
array layouts returned by the package (`time` int64, `tick_volume` uint64, `spread` int32, ...)
were checked against a real export and are reproduced exactly.

## The Python simulator

### What it reproduces, and from where

| Behaviour | Source |
|---|---|
| Prices inside a minute: open, low, high, close on a rising bar and open, high, low, close on a falling one; a doji moves against the previous bar; fewer prices for bars with 1, 2 or 3 ticks | The "Real and Generated Ticks" help page, "1 Minute OHLC" |
| Ask = bid + the minute's recorded spread, which is the narrowest spread of that minute (the smallest among its recorded ticks in 99.5% of 28 398 EURUSD minutes) | Same page; the spread checked against a tick export |
| Only FOK fills are accepted on a market-execution symbol whose filling mode is FOK; IOC and Return get 10030 "Unsupported filling mode" | A tester run sending each fill type |
| A stop loss closer than the stops level gets 10016 "Invalid stops" (5 points rejected, 50 accepted with a 10-point level) | Same run |
| Swap in points, charged at each midnight after a weekday, triple on the symbol's three-day day (Wednesday here) | `symbol_info()` of the server and the MQL5 documentation |
| Trading sessions: requests outside them get 10018 "Market closed"; stops and pending orders wait for the session to open | Found by the parity study: the tester rejected entries at 00:00:00 and 00:00:30 with "Market closed", and of 16 038 tester deals none fell outside the trade sessions except the forced "end of test" close |
| The run starts with a balance deal at 00:00 of the first day, closes open positions at the last price before the end with the comment "end of test", and stops at 00:00 of the end date | Strategy Tester reports |
| Function names, record fields, array layouts and the 223 trading constants | Copied from the installed package; a test compares them |

### Fill model

Stated in `sim/broker.py` and repeated here. Market orders fill at once, buys at the ask and
sells at the bid; requested price and deviation are ignored, as under market execution, with
no slippage. Stop loss and take profit are checked at every price: a long position against the
bid, a short against the ask. When stop loss and take profit both fall inside a minute, the
order of the minute's prices decides.

Where a reached level fills was settled by the parity study, not assumed. The first version
filled at the opening price when a minute opened beyond the level (a gap); the tester does not.
In "1 minute OHLC" mode every one of its stop and target exits was exactly at the level, gaps
included (a short's take profit crossed by a weekend gap was booked at the level, 40.16, where
the gap price would have given 130.46). In real-tick mode stops filled at the level or worse
and take profits at the level or better: the price of the tick that crossed the level. So on
1-minute bars a reached level fills at the level, and on recorded ticks at the crossing tick's
price. Replaying that rule over the recorded ticks reproduces all 14 of the tester's real-tick
exits in the study's tick window to the second and the point.

A level counts as reached when the price is within half a point of it, which compares both at
the symbol's digits. Exact comparison is wrong here: the terminal hands out some prices a hair
off their decimal value (an ask of 1.0866500000000001 for 1.08665), and the parity study
caught a take profit at 1.08665 that the tester filled on that tick and the simulator, comparing
exactly, filled a tick later. Limit orders fill at their price; stop orders follow the same
rule as stop losses. Stop-limit orders and close-by are not simulated and raise an error
naming them. Commission is a fixed amount per lot per deal, zero unless set; margin is checked
when opening, and stop-out is not simulated. Hedging and netting accounts are both supported.
Profit in a currency other than the deposit's is converted at the closing price when the
deposit currency is the symbol's base currency (USDJPY in a USD account), otherwise with the
tick value exported with the symbol.

### Time

The simulated clock is server time. Time moves only when the strategy sleeps; a sleep that
would end before the next price ends at that price instead, since nothing observable changes in
between. That keeps a script polling every second practical: the sample strategy's year on
EURUSD H1 is about 1.45 million sleeps and takes about 100 s. The end of the history raises
`SimulationFinished`, a `BaseException`, so a strategy's `except Exception` does not swallow it.
A strategy that makes 200 000 calls without sleeping is stopped with an explanation, since its
clock can never move.

In the strategy's process, `time.sleep`, `time.time`, `time.time_ns`, `time.monotonic`,
`time.perf_counter`, `time.gmtime`, `time.localtime`, `time.strftime`, `time.ctime`,
`datetime.datetime.now/utcnow/today/fromtimestamp` and `datetime.date.today` are replaced.
Server time is presented as UTC, which is how the `MetaTrader5` package presents bar and tick
times, so `time.time()` and a bar's `time` field can be compared directly. The patched datetime
classes still pass `isinstance` checks for ordinary datetimes. Not patched:
`pandas.Timestamp.now()`, `threading` and `asyncio` timers.

### How close it is

`reports/parity.md` measures it: one strategy, written in MQL5 and in Python, run through both
engines. The fidelity note attached to every Python run is that study's conclusion, kept in
`sim/runner.py`.

### Isolation and lookahead

A strategy runs in its own process (`python -I -m strategylab.sim.runner`), in its run folder,
with a wall-clock timeout; its output goes to `strategy.log`. `MetaTrader5` in that process is the
stand-in. Every function that returns market data cuts the request at the simulated present, and
the bar still forming is built only from the prices seen so far. A test checks this at 150
random moments across seven timeframes, comparing the forming bar with one rebuilt from scratch.
`symbol_info()` replaces the live and session fields of the exported spec (which describe the
moment of export, in the simulation's future) with simulated values or zero.

The run's symbol also answers to its name without the broker's suffix (`EURUSD` for `EURUSD@`),
since names differ between brokers. Any other symbol raises an error: Python strategies are
simulated on one symbol.

### Trading sessions

Brokers quote longer than they trade: on the demo server, EURUSD and USDJPY are quoted from
00:00 to 24:00 but traded only from 00:05 (Monday) or 00:03 (other weekdays) to 23:59. The
`MetaTrader5` Python package has no call for sessions, so `sessions.py` runs
`mql5/SessionExport.mq5` for one day in the tester, reads the `SymbolInfoSessionTrade` and
`SymbolInfoSessionQuote` values it prints, and caches them beside the symbol's history. Without
them the simulator accepts orders whenever there is a price and says so in the run's notes.

The strategy's output is set to UTF-8 and line-buffered inside the subprocess. Isolated mode
(`-I`) ignores `PYTHONIOENCODING`, and with the console code page a strategy that printed "€"
stopped with an encoding error; line buffering is what lets the log be followed while it runs.

### Inputs of a Python strategy

A script has no `input` declarations, so `pystrategy.py` reads two common forms from its syntax
tree, without importing it: module-level constants in capitals assigned a literal
(`FAST_PERIOD = 12  # fast period`, the comment becoming the label), and argparse options
(`add_argument("--fast", type=int, default=12, help=...)`). A run's value for an option is
passed on the command line. A constant is changed by replacing its literal in the syntax tree
before the script is compiled, so the file on disk is never touched and tracebacks keep their
line numbers. Only values that differ from the declared default are applied.

## Run history, the job queue and the API

### One worker, one terminal

`jobs.py` has a single worker thread. It holds the terminal lock for the whole of a run, and
anything else that drives the terminal (measuring history for the symbol list) tries the same
lock without waiting, answering from its last measurement when a run holds it. So a tester run,
a history export and a data connection never overlap: the terminal's data folder can only be
used by one copy at a time, and a second launch hands over to the first and exits.

A run moves through `queued`, `compiling`, `running`, `parsing` and ends `done`, `failed` or
`cancelled`. A run that produced a result but zero trades is `done`, with the outcome
`zero_trades`; a timeout, a terminal that is already running, a report that never appeared and a
compile error are `failed`, each with the engine's message. Cancelling a queued run just marks
it; cancelling a running one closes the terminal through its window (killing it after 30 s if
it does not close) or kills the strategy's process tree. On start, runs left unfinished by a
previous server are marked failed with the stage they were in: the state of a terminal that was
driven by a process that died is unknown, so they are never resumed.

Every change of state and every log line (compile messages, the terminal's journals as they
grow, the strategy's output) is a numbered event, the last 50 000 kept in memory, streamed to
the browser as server-sent events. A browser that reconnects sends `Last-Event-ID` and gets what
it missed. Results are stored as `result.json` and, for tables and charts, the deals and the
balance series as parquet; the balance series is downsampled on the server, keeping each
bucket's lowest and highest balance and deepest drawdown.

### Available history

What "available" means was measured, not assumed:

- The server serves daily bars back to 2000 while the tester's journal said EURUSD M1 history
  began on 2021-10-14. Asking for the whole daily series made the terminal download every M1 bar
  back to 2000 (one file of about 22 MB per symbol and year, several minutes for a symbol), and
  afterwards the tester reported history from 2000. The daily series is therefore never asked
  for: the symbol list reads the yearly M1 files on disk
  (`bases\<server>\history\<symbol>\<year>.hcc`) and measures only the first M1 bar of the oldest
  of them and the latest bar.
- Through the package the first bar is later than the tester's: a data connection returns at most
  its 10 000 000 bars per chart, which on EURUSD reach back to 2000-07-05, while the tester
  reports history from 2000-01-01. The measured first bar is the one both engines can use, so the
  symbol list gives that.
- Asking for the first tick of a month (`copy_ticks_from(start, 1)`) did not return within eight
  minutes: the terminal synchronises every tick from that date on. Recorded ticks are listed by
  the month files on disk (`bases\<server>\ticks\<symbol>\YYYYMM.tkc`) instead.
- A run outside the measured range is refused with the range in the message. The end may reach
  today even when the last bar on disk is older, since both engines bring history up to date
  when they run.
- A data connection refuses a terminal the user opened by hand with the default 100 000 bars per
  chart, which would silently cut a year of M1 to about ten weeks.

### Security posture for uploaded code

- The API binds to 127.0.0.1, fixed in code and in `tasks.py`, with a test for each. Requests
  whose Host header names anything but this machine are refused (421), which stops a web page
  from reaching the API through DNS rebinding, and a request from a non-loopback address is
  refused (403). There is no CORS: in daily use the API serves the built frontend from the same
  address, and in development the frontend reaches the API through the dev server's proxy.
- Uploads are limited to 64 MB. A Python script is parsed in the server, never imported or run
  there; it runs in a separate isolated interpreter with a timeout. MQL5 is compiled by MetaEditor
  and runs in the tester with DLL imports and live trading switched off; archives may not carry
  executables or DLLs.
- The Strategy Tester report repeats text the strategy chose (names, comments), so it is served
  with `Content-Security-Policy: sandbox`, as a document that can run nothing.

### Serving the app

`python tasks.py start` builds the frontend when any of its sources is newer than the last build,
then runs the API, which serves `frontend/dist` at every path outside `/api`: a file when one
exists, otherwise `index.html`, whose router shows the page. One process and one address, so
the frontend needs no configuration and the API no CORS. Files are served with explicit media
types, because Python on Windows reads them from the registry, where `.js` can be `text/plain`,
and a browser will not run a module script served as that. Hashed assets are cached for good;
`index.html` is revalidated, so a rebuild shows on the next load.
