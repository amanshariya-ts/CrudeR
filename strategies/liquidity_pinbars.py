# strategies/liquidity_pinbars.py
"""
Exact Python replication of the TradingView Pine Script v6 indicator:
"Liquidity Pin Bars (Bullish & Bearish)"

Pine logic (line by line):

  isRed        = close < open
  isGreen      = close > open
  bodySize     = math.abs(close - open)
  candleRange  = high - low
  upperWick    = high - math.max(open, close)
  lowerWick    = math.min(open, close) - low
  ema          = ta.ema(close, emaLength)

  smallBody    = candleRange > 0 and (bodySize / candleRange < bodySizeRatio)

  // BULLISH: RED candle, long LOWER wick, close below EMA
  bullishPinBar = isRed and smallBody and lowerWick > wickRatio * bodySize and close < ema

  // BEARISH: GREEN candle, long UPPER wick, close above EMA
  bearishPinBar = isGreen and smallBody and upperWick > wickRatio * bodySize and close > ema

Note: the isRed/isGreen requirement means bodySize > 0 automatically,
so division by zero for wick/body can never happen (matches Pine,
where such candles simply fail the color condition).
"""
import pandas as pd
from strategies.base import StrategyBase, Signal
from indicators.ema import ema


class LiquidityPinBars(StrategyBase):
    name = "liquidity_pinbars"

    def evaluate(self, df: pd.DataFrame) -> Signal | None:
        p = self.params
        ema_length     = p.get("ema_length", 9)
        body_size_ratio = p.get("body_size_ratio", 0.3)
        wick_ratio     = p.get("wick_ratio", 2.0)

        # --- EMA (ta.ema equivalent) ---
        ema_series = ema(df["close"], ema_length)

        c = df.iloc[-1]
        e = ema_series.iloc[-1]

        # --- Candle calculations ---
        is_red   = c.close < c.open
        is_green = c.close > c.open

        body_size    = abs(c.close - c.open)
        candle_range = c.high - c.low
        upper_wick   = c.high - max(c.open, c.close)
        lower_wick   = min(c.open, c.close) - c.low

        # --- Common condition ---
        small_body = candle_range > 0 and (body_size / candle_range) < body_size_ratio

        # --- Bullish Liquidity Pin Bar (red candle, long LOWER wick) ---
        long_lower_wick = lower_wick > (wick_ratio * body_size)
        close_below_ema = c.close < e
        bullish = is_red and small_body and long_lower_wick and close_below_ema

        # --- Bearish Liquidity Pin Bar (green candle, long UPPER wick) ---
        long_upper_wick = upper_wick > (wick_ratio * body_size)
        close_above_ema = c.close > e
        bearish = is_green and small_body and long_upper_wick and close_above_ema

        if bullish:
            return Signal(self.symbol, self.timeframe, "BUY", float(c.close), c.timestamp)
        if bearish:
            return Signal(self.symbol, self.timeframe, "SELL", float(c.close), c.timestamp)
        return None
