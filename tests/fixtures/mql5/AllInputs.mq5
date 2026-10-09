//+------------------------------------------------------------------+
//| Declares one input of every kind StrategyLab reads. It trades     |
//| nothing; it only exists to be parsed and to be run in the tester. |
//+------------------------------------------------------------------+
#property copyright "StrategyLab test fixture"
#property version   "1.00"

enum ENTRY_MODE
  {
   MODE_CROSS = 0,   // Crossing
   MODE_TOUCH,       // Touching
   MODE_BREAK = 5,   // Breakout
   MODE_LAST         // After breakout
  };

enum SIZING { SIZE_FIXED, SIZE_RISK = 10 };

input group "Signal"
input int             FastPeriod   = 12;                // Fast MA period
input int             SlowPeriod=26;                    // Slow MA period
input ENUM_TIMEFRAMES SignalFrame  = PERIOD_H4;         // Signal timeframe
input ENTRY_MODE      EntryMode    = MODE_TOUCH;        // Entry mode
input ENUM_MA_METHOD  MaMethod     = MODE_EMA;
input double          Threshold    = -0.5;              // Threshold, points

input group "Money"
input double          Lots         = 0.10;              // Lot size
input SIZING          Sizing       = SIZE_RISK;
input bool            UseTrailing  = true;              // Trail the stop
input long            MaxVolume    = 5000000000;
input uint            Retries      = 3;
input ulong           MagicNumber  = 18446744073709551615;
input char            Offset       = -7;
input uchar           Steps        = 200;
input short           Shift        = -300;
input ushort          Window       = 60000;
input float           Ratio        = 1.5;

input group "Other"
input string          TradeComment = "StrategyLab; \"quoted\"";  // Order comment
input datetime        StartTime    = D'2025.01.02 03:04:05';     // Start trading at
input color           LineColor    = clrDodgerBlue;
input color           FillColor    = C'255,128,0';
sinput int            ReportEvery  = 100;               // Log every N ticks
input int             First = 1, Second = 2;            // Two at once

int OnInit()
  {
   PrintFormat("inputs: %d %d %s %d %s %.2f", FastPeriod, SlowPeriod,
               EnumToString(SignalFrame), EntryMode, TradeComment, Lots);
   return(INIT_SUCCEEDED);
  }

void OnTick()
  {
  }
