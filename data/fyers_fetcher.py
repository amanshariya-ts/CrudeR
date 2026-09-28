# data/fyers_fetcher.py
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import numpy as np
import pandas as pd
from fyers_apiv3 import fyersModel

log = logging.getLogger(__name__)


class FyersFetcher:
    RESOLUTION_MAP = {
        "1m": "1", "2m": "2", "3m": "3", "5m": "5", "10m": "10",
        "15m": "15", "20m": "20", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240",
        "1d": "D",
    }

    FETCH_TIMEOUT = 20   # hard cap per history() call — a hung call must never eat a candle cycle
    MAX_ATTEMPTS = 3
    RETRY_BASE_DELAY = 2  # exponential: 2s, 4s, 8s

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
    def _history_with_timeout(self, payload: dict) -> dict:
        """Run the SDK's blocking history() under a hard timeout.

        The fyers_apiv3 SDK does its own HTTP internally with no timeout we
        can set — a stalled connection used to block the poll thread for an
        entire candle cycle. This abandons such calls after FETCH_TIMEOUT.
        shutdown(wait=False) is critical: a plain `with` block would secretly
        block on exit waiting for the hung thread, re-creating the bug.
        """
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fyers-fetch")
        try:
            fut = ex.submit(self._get_client().history, payload)
            return fut.result(timeout=self.FETCH_TIMEOUT)
        except FutureTimeout:
            raise TimeoutError(
                f"fyers history() hung >{self.FETCH_TIMEOUT}s — abandoned"
            )
        finally:
            ex.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        resolution = self.RESOLUTION_MAP.get(timeframe)
        if resolution is None:
            raise ValueError(f"Unsupported fyers timeframe: {timeframe}")

        tf_sec = self.timeframe_to_seconds(timeframe)
        end = int(time.time())
        start = end - max(limit * tf_sec * 3, 5 * 86400)

        t0 = time.time()
        resp = self._request_history(symbol, resolution, start, end)
        log.debug(f"history {symbol} {timeframe} took {time.time()-t0:.1f}s")

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
    def _request_history(self, symbol, resolution, start, end):
        """history() with hard timeout + exponential-backoff retry.
        Token errors raise immediately — retrying an expired token is pointless."""
        payload = {
            "symbol": symbol, "resolution": resolution,
            "date_format": "0", "range_from": str(start),
            "range_to": str(end), "cont_flag": "1",
        }
        last = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = self._history_with_timeout(payload)
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
                delay = self.RETRY_BASE_DELAY ** attempt
                log.warning(
                    f"history attempt {attempt}/{self.MAX_ATTEMPTS} failed "
                    f"for {symbol} {resolution}: {e} — retrying in {delay}s"
                )
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(delay)
        raise RuntimeError(
            f"Fyers history failed after {self.MAX_ATTEMPTS} tries: {last}"
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def timeframe_to_seconds(tf: str) -> int:
        multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        return int(tf[:-1]) * multipliers[tf[-1]]


class TokenExpiredError(RuntimeError):
    """Raised when Fyers rejects the token — caller should halt and refresh."""
