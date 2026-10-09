//+------------------------------------------------------------------+
//| Prints the symbol's quote and trade sessions for every weekday.  |
//| StrategyLab runs it once per symbol in the Strategy Tester,      |
//| because the MetaTrader5 Python package cannot read sessions.     |
//+------------------------------------------------------------------+
#property description "Prints a symbol's quote and trade sessions."

void Report(const string kind, const ENUM_DAY_OF_WEEK day)
  {
   datetime from, to;
   for(uint index = 0; index < 16; index++)
     {
      bool found = (kind == "TRADE")
                   ? SymbolInfoSessionTrade(_Symbol, day, index, from, to)
                   : SymbolInfoSessionQuote(_Symbol, day, index, from, to);
      if(!found)
         break;
      PrintFormat("SESSION %s %d %d %d", kind, (int)day, (int)from, (int)to);
     }
  }

int OnInit()
  {
   for(int day = SUNDAY; day <= SATURDAY; day++)
     {
      Report("QUOTE", (ENUM_DAY_OF_WEEK)day);
      Report("TRADE", (ENUM_DAY_OF_WEEK)day);
     }
   Print("SESSION END");
   return(INIT_SUCCEEDED);
  }

void OnTick()
  {
  }
