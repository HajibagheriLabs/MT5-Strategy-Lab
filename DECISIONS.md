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
