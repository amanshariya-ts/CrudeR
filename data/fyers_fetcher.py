# data/fyers_fetcher.py
import json
import os
import time
import numpy as np
import pandas as pd
from fyers_apiv3 import fyersModel


class FyersFetcher:
    RESOLUTION_MAP = {
        "1m": "1", "2m": "2", "3m": "3", "5m": "5", "10m": "10",
        "15m": "15", "20m": "20", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240",
        "1d": "D",
    }

    def __init__(self, app_id: str, config_path: str, token: str | None = None):
        self.app_id = app_id
        self.config_path = config_path
        self._injected_token = token   # tests only
        self._client = None
        self._token_used: str | None = None

    # ------------------------------------------------------------------ #
    def _get_client(self):
        """Return a Fyers client bound to the CURRENT token in config.json.
        Rebuilds the client only when the token actually changed."""
        if self._injected_token is not None:
            token = self._injected_token
        else:
            with open(self.config_path) as f:
                token = json.load(f)["access_token"].strip()
        if not token:
            raise RuntimeError("Fyers access token is empty — run refresh_token.py")

        if self._client is None or token != self._token_used:
            self._client = fyersModel.FyersModel(
                client_id=self.app_id, is_async=False, token=token, log_path=""
            )
            self._token_used = token
        return self._client

    # ------------------------------------------------------------------ #
    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        resolution = self.RESOLUTION_MAP.get(timeframe)
        if resolution is None:
            raise ValueError(f"Unsupported fyers timeframe: {timeframe}")

        tf_sec = self.timeframe_to_seconds(timeframe)
        end = int(time.time())
        start = end - max(limit * tf_sec * 3, 5 * 86400)
        resp = self._request_history(symbol, resolution, start, end)

        if resp.get("s") != "ok":
            raise RuntimeError(f"Fyers history error for {symbol}: {resp}")

        candles = resp.get("candles", [])
        if not candles:
            raise RuntimeError(f"No candles returned for {symbol} {timeframe}")

        arr = np.asarray(candles, dtype=float)
        if arr.ndim != 2 or arr.shape[1] < 6:
            raise RuntimeError(f"Unexpected candle shape: {arr.shape}")

        out = pd.DataFrame({
            "timestamp": pd.to_datetime(arr[:, 0], unit="s", utc=True),
            "open":   arr[:, 1].astype(float),
            "high":   arr[:, 2].astype(float),
            "low":    arr[:, 3].astype(float),
            "close":  arr[:, 4].astype(float),
            "volume": arr[:, 5].astype(float),
        })
        return (out.drop_duplicates(subset="timestamp", keep="last")
                   .sort_values("timestamp")
                   .reset_index(drop=True)).tail(limit).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def _request_history(self, symbol, resolution, start, end, retries=3):
        payload = {
            "symbol": symbol, "resolution": resolution,
            "date_format": "0", "range_from": str(start),
            "range_to": str(end), "cont_flag": "1",
        }
        last = None
        for attempt in range(retries):
            try:
                resp = self._get_client().history(payload)   # fresh token check
                msg = str(resp.get("message", "")).lower()
                if resp.get("s") == "error" and (
                    "token" in msg or resp.get("code") in (-15, -16)
                ):
                    raise TokenExpiredError(
                        "Fyers access token expired/invalid — run refresh_token.py"
                    )
                return resp
            except TokenExpiredError:
                raise
            except Exception as e:
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Fyers history failed after {retries} tries: {last}")

    # ------------------------------------------------------------------ #
    @staticmethod
    def timeframe_to_seconds(tf: str) -> int:
        multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        return int(tf[:-1]) * multipliers[tf[-1]]


class TokenExpiredError(RuntimeError):
    """Raised when Fyers rejects the token — caller should halt and refresh."""
