"""Moving-average cross on EURUSD H1: one position at a time, fixed lot, fixed SL and TP.

Written against the MetaTrader5 package the ordinary way, as a polling loop that never returns.
StrategyLab runs it unmodified against its simulator. Run directly with Python, it trades on
whatever account the terminal is logged into, which is why it refuses anything but a demo.
"""

import time

import MetaTrader5 as mt5

SYMBOL = "EURUSD"
TIMEFRAME = mt5.TIMEFRAME_H1
FAST_PERIOD = 12
SLOW_PERIOD = 26
LOTS = 0.10
STOP_LOSS_POINTS = 300
TAKE_PROFIT_POINTS = 600
DEVIATION_POINTS = 20
MAGIC = 120626
POLL_SECONDS = 1


def sma(values, period):
    return sum(values[-period:]) / period


def closed_closes(count):
    # Start at position 1: position 0 is the bar still forming, and its close is not final yet.
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 1, count)
    if rates is None or len(rates) < count:
        return None, None
    return [float(r["close"]) for r in rates], int(rates[-1]["time"])


def my_position():
    positions = mt5.positions_get(symbol=SYMBOL) or ()
    for position in positions:
        if position.magic == MAGIC:
            return position
    return None


def send(request):
    result = mt5.order_send(request)
    if result is None:
        print(f"order_send failed: {mt5.last_error()}")
    elif result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"order_send rejected: retcode={result.retcode} comment={result.comment}")
    return result


def open_position(direction):
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    if direction == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl = price - STOP_LOSS_POINTS * info.point
        tp = price + TAKE_PROFIT_POINTS * info.point
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl = price + STOP_LOSS_POINTS * info.point
        tp = price - TAKE_PROFIT_POINTS * info.point
    send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": LOTS,
            "type": order_type,
            "price": price,
            "sl": round(sl, info.digits),
            "tp": round(tp, info.digits),
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC,
            "comment": "ma_cross",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
    )


def close_position(position):
    tick = mt5.symbol_info_tick(SYMBOL)
    if position.type == mt5.POSITION_TYPE_BUY:
        order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
    else:
        order_type, price = mt5.ORDER_TYPE_BUY, tick.ask
    send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": position.volume,
            "type": order_type,
            "position": position.ticket,
            "price": price,
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC,
            "comment": "ma_cross exit",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
    )


def on_new_bar(closes):
    fast_now, slow_now = sma(closes, FAST_PERIOD), sma(closes, SLOW_PERIOD)
    fast_before, slow_before = sma(closes[:-1], FAST_PERIOD), sma(closes[:-1], SLOW_PERIOD)
    crossed_up = fast_before <= slow_before and fast_now > slow_now
    crossed_down = fast_before >= slow_before and fast_now < slow_now
    if not (crossed_up or crossed_down):
        return

    wanted = "buy" if crossed_up else "sell"
    position = my_position()
    if position is not None:
        holding = "buy" if position.type == mt5.POSITION_TYPE_BUY else "sell"
        if holding == wanted:
            return
        close_position(position)
    open_position(wanted)


def main():
    if not mt5.initialize():
        raise SystemExit(f"initialize() failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise SystemExit("Refusing to run: the connected account is not a demo account.")
        if not mt5.symbol_select(SYMBOL, True):
            raise SystemExit(f"Cannot select {SYMBOL}: {mt5.last_error()}")

        # One extra bar so the previous bar's averages are available to detect the cross.
        needed = SLOW_PERIOD + 1
        last_bar_time = None
        while True:
            closes, bar_time = closed_closes(needed)
            if closes is not None and bar_time != last_bar_time:
                last_bar_time = bar_time
                on_new_bar(closes)
            time.sleep(POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
