"""Moving-average cross on closed bars, a fixed lot, a fixed stop loss and take profit in points,
one position at a time. The twin of ParityMACross.mq5: the same logic, step for step.

    python ma_cross_parity.py --symbol EURUSD --timeframe H1

Like ma_cross.py it is an ordinary MetaTrader5 script that polls forever. Run directly it trades
on the connected account, so it refuses anything but a demo.
"""

import argparse
import time

import MetaTrader5 as mt5

FAST_PERIOD = 10
SLOW_PERIOD = 30
LOTS = 0.10
STOP_LOSS_POINTS = 200
TAKE_PROFIT_POINTS = 400
MAGIC = 20251


def average(closes, last, period):
    # Summed oldest first, like the MQL5 twin, so both see the same floating-point result.
    total = 0.0
    for i in range(last - period + 1, last + 1):
        total += closes[i]
    return total / period


def has_position(symbol):
    return any(p.magic == MAGIC for p in mt5.positions_get(symbol=symbol) or ())


def filling_mode(info):
    if info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    if info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def open_position(symbol, buy):
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if buy:
        price, order_type = tick.ask, mt5.ORDER_TYPE_BUY
        sl = price - STOP_LOSS_POINTS * info.point
        tp = price + TAKE_PROFIT_POINTS * info.point
    else:
        price, order_type = tick.bid, mt5.ORDER_TYPE_SELL
        sl = price + STOP_LOSS_POINTS * info.point
        tp = price - TAKE_PROFIT_POINTS * info.point
    result = mt5.order_send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": LOTS,
            "type": order_type,
            "price": price,
            "sl": round(sl, info.digits),
            "tp": round(tp, info.digits),
            "magic": MAGIC,
            "comment": "parity",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode(info),
        }
    )
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"order_send failed: {result}")


def on_new_bar(symbol, timeframe):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, SLOW_PERIOD + 1)
    if rates is None or len(rates) != SLOW_PERIOD + 1:
        return
    closes = [float(c) for c in rates["close"]]
    fast_now = average(closes, SLOW_PERIOD, FAST_PERIOD)
    slow_now = average(closes, SLOW_PERIOD, SLOW_PERIOD)
    fast_before = average(closes, SLOW_PERIOD - 1, FAST_PERIOD)
    slow_before = average(closes, SLOW_PERIOD - 1, SLOW_PERIOD)
    crossed_up = fast_before <= slow_before and fast_now > slow_now
    crossed_down = fast_before >= slow_before and fast_now < slow_now
    if not (crossed_up or crossed_down) or has_position(symbol):
        return
    open_position(symbol, crossed_up)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    args = parser.parse_args()
    timeframe = getattr(mt5, f"TIMEFRAME_{args.timeframe}")

    if not mt5.initialize():
        raise SystemExit(f"initialize() failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise SystemExit("Refusing to run: the connected account is not a demo account.")
        mt5.symbol_select(args.symbol, True)
        last_bar = None
        while True:
            current = mt5.copy_rates_from_pos(args.symbol, timeframe, 0, 1)
            if current is not None and len(current) and current["time"][0] != last_bar:
                last_bar = current["time"][0]
                on_new_bar(args.symbol, timeframe)
            time.sleep(1)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
