# data/fetcher.py
"""
Fetcher factory — Fyers MCX crude oil (primary/only data source).
Fyers candles: [epoch_seconds, o, h, l, c, v] — normalized to the shared
DataFrame schema: timestamp (UTC datetime64), open, high, low, close, volume.
"""
import pandas as pd

from data.fyers_fetcher import FyersFetcher


class OHLCVFetcher:
    """Generic ccxt fetcher — kept for optional crypto markets."""

    def __init__(self, exchange_name: str = "binanceusdm"):
        import ccxt
        exchange_class = getattr(ccxt, exchange_name)
        self.exchange = exchange_class({"enableRateLimit": True})

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    @staticmethod
    def timeframe_to_seconds(tf: str) -> int:
        multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        return int(tf[:-1]) * multipliers[tf[-1]]


def create_fetcher(name: str, config: dict | None = None):
    """Factory: per-market fetcher selection."""
    if name == "fyers":
        cfg = (config or {}).get("fyers", {})
        return FyersFetcher(
            app_id=cfg["app_id"],
            config_path="config.json",
        )


    if name in ("binance", "binanceusdm"):
        return OHLCVFetcher(name)
    raise ValueError(f"Unknown exchange: {name}")
