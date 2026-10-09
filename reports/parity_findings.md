## Findings

### In short

- **Against the tester's own "1 minute OHLC" mode the simulator reproduces every trade.** All 394
  trades on two symbols and two timeframes are identical: same entry and exit second, same
  prices to the point, same result to the cent, swap included. On 1-minute bars a Python result
  is as good as an MQL5 result in that mode, for a strategy like this one.
- **Against real ticks, 1-minute bars are optimistic,** for the simulator and for the tester's
  own 1-minute mode alike, because both use the same bars. Over three months the simulator
  came out ahead of the tester's real-tick run in all four cases, by 6.20, 113.76, 43.04 and
  24.85 on a 10 000 deposit, and its maximum drawdown was lower in all four (by 6 to 43).
  Most trades still pair up (95.6% to 100%), but almost none are identical to the point.
- **On recorded ticks the simulator fills as the tester does;** what remains is the Python
  script's own polling, which the simulator reproduces rather than hides (see below).

### Sources of divergence

**Spread modelling.** This is the largest source. A 1-minute bar stores one spread, and it is
the narrowest spread of the minute: of 28 398 EURUSD minutes in March 2025 the bar's spread
equals the smallest spread among that minute's recorded ticks in 99.5%, while the minute's
first tick is typically 3 points wider. On bars, the ask is bid plus that narrowest spread, so
everything done at the ask looks cheaper than it was:

- Buys enter cheaper: median 0 to 4 points across the four runs, up to 23. Sells enter at the
  bid, which bars and ticks agree on: every sell entry is equal to the point.
- Shorts close at the ask. On real ticks the spread widens, and the ask reaches a short's stop
  loss that the bars never reach, or misses a take profit the bars do reach. On 6 March a
  short's stop at 1.08230 was reached on ticks at 09:47:10, when the spread was 16 points; the
  bar for that minute carries a spread of 11 and its highest ask is 1.08227, so on bars the
  stop held until 15:16. Both trades that closed the other way in the four runs (a stop loss
  on ticks, a take profit on bars) were shorts, and so were the three EURUSD M15 shorts whose
  stop came between half an hour and a weekend later on bars.

**Exits beyond the level.** On 1-minute bars a reached stop loss or take profit fills exactly at
the level, even when the minute jumped past it; the tester does the same in its 1-minute mode
(verified, see the fill model in DECISIONS.md). On real ticks the tester fills at the price of
the tick that crossed the level, so stops fill worse and targets better. Mostly this costs a
point or two, but news and weekend gaps are larger: on EURUSD M15 a take profit filled 896
points beyond its level at the Monday open of 3 February 2025 (89.60 more for the tester), and
stops filled up to 308 points beyond their level at 15:30 server time, when US data is
released. This is the *exit prices* column of the table above.

**Intrabar path.** On bars, the order of prices inside a minute is generated (open, low, high,
close on a rising bar; open, high, low, close on a falling one) and the time of each is fixed
at measured offsets. So an exit lands in the right minute (88% to 98% of paired trades) but
rarely the right second, and when stop loss and take profit are both near, the generated path
can pick the wrong one.

**Knock-on trades.** The strategy holds one position at a time, so an exit that comes earlier
or later on one side lets it take a signal the other side skips. On EURUSD M15 this left 5
trades only the tester took and 3 only the simulator took; they are the *unpaired* column.

**Bar timing.** On real ticks a bar starts at its first tick, which can be seconds into the
period (an M15 entry at 04:15:12 in the tester against 04:15:00 on bars). On bars the first
price of a minute is at :00, or at :30 for a minute with one tick, as the tester places them.
The effect on prices is small and is part of the *entry prices* column.

**Swap.** Equal to the cent for every trade held over the same midnights on both sides. Swap
is charged in points at each midnight, triple on Wednesday, and converted to the deposit
currency at the last price before midnight. Totals differ only where the trades do.

**Commission.** The demo account charges none in the tester, so commission was zero on both
sides and the study says nothing about it. The simulator charges a fixed amount per lot per
deal when one is set.

**Polling against OnTick.** In the tick window, the simulator ran on the same 2 429 776 recorded
ticks as the tester. All 14 tester entries are at the first tick of the new bar, because an
EA's OnTick runs on that tick. The Python script polls once a second, so it acts on the price
up to a second later, as it would live: 6 of its 14 entries are at the same price and the
rest 1 to 27 points away. From there both sides exit by the same rule: replaying "fill at the
first tick that crosses the level" over the recorded ticks reproduces all 14 exits of each, to
the second and the point. One short entered 7 points lower, so its stop loss was 7 points lower
too. A spike reached an ask of 1.09333, 2 points above the simulator's stop (1.09331) and 5
points short of the tester's (1.09338): the simulator's trade was stopped out for −20.20 where
the tester's went on to its take profit, +40.58. That one trade is −60.78 of the window's
−60.68 difference.

### Bugs the study found in the simulator, all fixed

Each was found by a trade that differed from the tester's (the first five in its 1-minute OHLC
mode, the sixth with real ticks), traced to its cause, fixed as a general rule, and covered by a
test. None was tuned to this strategy.

1. **Trading sessions.** The tester rejects orders outside a symbol's trade sessions ("Market
   closed"); quotes run from 00:00 but trading starts at 00:03, or 00:05 on Mondays, on this
   server. The simulator accepted entries at midnight. Sessions are now exported from the
   terminal by a small MQL5 script run in the tester, and enforced; stops wait for the session.
2. **Gap fills.** The simulator first filled a stop loss at the opening price of a minute that
   gapped past it. The tester's 1-minute mode fills at the level; the simulator now does too on
   bars, and at the crossing tick on recorded ticks, which is what the tester does on real ticks.
3. **Tick timing inside a minute.** Minutes with one, two or three ticks had their prices at the
   wrong seconds, so some exits came at a different second than the tester's. The offsets were
   measured from 28 199 prices the tester generated and are now used as measured.
4. **Stale prices.** At the start of a run and on a holiday the simulator let a strategy trade at
   a price from before the session or the run. An order now needs a price from the current
   session and the run.
5. **Swap conversion.** USDJPY swaps were a cent off because they were converted at the first
   price after midnight; the tester uses the last one before it. With fixes 4 and 5, USDJPY H1
   went from 96.5% and USDJPY M15 from 98.9% identical to 100%.
6. **Exact float comparison.** A take profit at 1.08665 was filled one tick late on recorded
   ticks: the terminal delivered that ask as 1.0866500000000001, so `ask <= 1.08665` was false.
   Levels are now compared at the symbol's digits (within half a point).

### What this means for a Python result

- On 1-minute bars, read a Python result the way you would read the tester's "1 minute OHLC"
  mode: it is that mode, reproduced. Like that mode, it flatters strategies that buy often,
  hold shorts, trade around news, or hold over weekends, because spreads are at their narrowest
  and gaps fill at the level. In this study the overstatement was 0.13 to 1.02 in the deposit
  currency per trade at 0.10 lots, roughly 1 to 12 points per trade.
- When spread or gaps matter, run the Python strategy on recorded ticks, or compare with the MQL5
  version in the tester with real ticks.
- What the study does not cover: a single strategy shape (market entries with fixed stops,
  one position), no pending orders, no commission, two FX symbols on one demo server, and three
  months of 2025.
