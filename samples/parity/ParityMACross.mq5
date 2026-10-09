//+------------------------------------------------------------------+
//| Moving-average cross on closed bars, a fixed lot, a fixed stop   |
//| loss and take profit in points, one position at a time.          |
//| Its twin, ma_cross_parity.py, follows exactly the same logic.    |
//+------------------------------------------------------------------+
#property description "Parity strategy: moving-average cross with fixed stop loss and take profit."
#property version   "1.00"

#include <Trade\Trade.mqh>

input int    FastPeriod       = 10;     // Fast average, bars
input int    SlowPeriod       = 30;     // Slow average, bars
input double Lots             = 0.10;   // Lot size
input int    StopLossPoints   = 200;    // Stop loss, points
input int    TakeProfitPoints = 400;    // Take profit, points
input ulong  Magic            = 20251;  // Magic number

CTrade   trade;
datetime last_bar = 0;

//--- the mean of closes[last - period + 1 .. last], summed oldest first like the Python twin
double Average(const double &closes[], const int last, const int period)
  {
   double sum = 0.0;
   for(int i = last - period + 1; i <= last; i++)
      sum += closes[i];
   return(sum / period);
  }

bool HasPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetTicket(i) > 0
         && PositionGetString(POSITION_SYMBOL) == _Symbol
         && PositionGetInteger(POSITION_MAGIC) == (long)Magic)
         return(true);
     }
   return(false);
  }

int OnInit()
  {
   trade.SetExpertMagicNumber(Magic);
   trade.SetTypeFillingBySymbol(_Symbol);
   return(INIT_SUCCEEDED);
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == last_bar)
      return;
   last_bar = bar;

   double closes[];
   //--- closes[0] is the oldest; closes[SlowPeriod] is the bar that has just closed
   if(CopyClose(_Symbol, _Period, 1, SlowPeriod + 1, closes) != SlowPeriod + 1)
      return;
   double fast_now    = Average(closes, SlowPeriod, FastPeriod);
   double slow_now    = Average(closes, SlowPeriod, SlowPeriod);
   double fast_before = Average(closes, SlowPeriod - 1, FastPeriod);
   double slow_before = Average(closes, SlowPeriod - 1, SlowPeriod);
   bool crossed_up    = fast_before <= slow_before && fast_now > slow_now;
   bool crossed_down  = fast_before >= slow_before && fast_now < slow_now;
   if(!(crossed_up || crossed_down) || HasPosition())
      return;

   if(crossed_up)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      trade.Buy(Lots, _Symbol, ask,
                NormalizeDouble(ask - StopLossPoints * _Point, _Digits),
                NormalizeDouble(ask + TakeProfitPoints * _Point, _Digits), "parity");
     }
   else
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      trade.Sell(Lots, _Symbol, bid,
                 NormalizeDouble(bid + StopLossPoints * _Point, _Digits),
                 NormalizeDouble(bid - TakeProfitPoints * _Point, _Digits), "parity");
     }
  }
