# StrategyLab

StrategyLab is a local workbench for backtesting MetaTrader 5 strategies. You give it an MQL5
Expert Advisor or a Python trading script, pick a symbol, timeframe and date range, and get back
an equity curve, a trade list, metrics, and side-by-side comparison of runs, all computed from
MetaTrader's own historical data. It has two engines with different fidelity, and every result
says which one produced it. MQL5 strategies run in MetaTrader's real Strategy Tester, driven
headlessly, so their results are as good as MetaTrader's own. Python strategies written against
the `MetaTrader5` package cannot run inside the Strategy Tester, so they run in StrategyLab's own
simulator on history exported from the same terminal; those results are an approximation and are
labelled as one.

## Setup

To be written.

## Usage

To be written.

## Limitations

- **Python results are simulated, and approximate.** The parity study
  ([reports/parity.md](reports/parity.md)) ran one strategy, written once in MQL5 and once in
  Python, through both engines on EURUSD and USDJPY, H1 and M15, over three months. On
  1-minute bars the simulator reproduced the tester's "1 minute OHLC" mode exactly: 394 of 394
  trades identical to the cent. Both, however, were optimistic against the tester's real-tick
  mode, by 1 to 12 points per trade. 1-minute bars carry only the narrowest spread of each
  minute, and they fill stops and targets at their level even across a gap. On recorded ticks
  the simulator fills as the tester does. The study covers one kind of strategy (market
  entries with fixed stops, one position at a time), with no pending orders and no commission.
- Windows only, since MetaTrader 5 is.
- A backtest is not a prediction of live results.
