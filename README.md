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

To be written.
