# data/fyers_fetcher.py
import json
import logging
import os
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

    # Token resolution order (first non-empty wins):
    #   1. explicit `token` arg        — tests
    #   2. FYERS_ACCESS_TOKEN env var  — GitHub Actions (never touches disk)
    #   3. config.json fallback        — local dev only (file is gitignored)
    TOKEN_ENV_VAR = "FYERS_ACCESS_TOKEN"

    def __init__(
        self,
        app_id: str | None = None,
        config_path: str = "config.json",
        token: str | None = None,
    ):
        # app_id: env-first too, so main.py can simply do FyersFetcher()
        self.app_id = app_id or os.environ.get("FYERS_APP_ID", "").strip()
        if not self.app_id:
            raise RuntimeError(
                "Fyers app_id missing — set FYERS_APP_ID env var "
                "(or pass app_id= explicitly)"
            )

        self.config_path = config_path
        self._injected_token = token   # tests only
        self._client = None
        self._token_used: str | None = None

    # ------------------------------------------------------------------ #
    def _resolve_token(self) -> str:
        """Resolve the access token without depending on config.json."""
        # 1. Explicit injection (tests)
        if self._injected_token is not None:
            return self._injected_token.strip()

        # 2. Environment (GitHub Actions / local export)
        env_token = os.environ.get(self.TOKEN_ENV_VAR, "").strip()
        if env_token:
            return env_token

        # 3. Local file fallback (gitignored, local dev only)
        if os.path.exists(self.config_path):
            with open(self.config_path) as f:
                token = str(json.load(f).get("access_token", "")).strip()
            if token:
                log.debug("Token loaded from %s (local fallback)", self.config_path)
                return token

        raise RuntimeError(
            f"Fyers access token not found — set {self.TOKEN_ENV_VAR} env var, "
            f"pass token= explicitly, or provide {self.config_path} (local only)"
        )

    def _get_client(self):
        """Return a Fyers client bound to the CURRENT token.
        Rebuilds the client only when the token actually changed — so a
        hot-reloaded token (refresh_token.py rewriting config.json/env)
        is picked up mid-session without restart."""
        token = self._resolve_token()
        if not token:
            raise RuntimeError("Fyers access token is empty — run refresh_token.py")

        if self._client is None or token != self._token_used:
            self._client = fyersModel.FyersModel(
                client_id=self.app_id, is_async=False, token=token, log_path=""
            )
            self._token_used = token
            log.info("Fyers client ready (token ...%s)", token[-6:])
        return self._client

    # ------------------------------------------------------------------ #
    def ping(self) -> bool:
        """Lightweight authenticated call for startup smoke tests.
        Raises on auth failure so the workflow fails fast (30s) instead of
        burning a 5.5h run with dead credentials."""
        resp = self._history_with_timeout({
            "symbol": "MCX:CRUDEOIL25AUG",
            "resolution": "1",
            "date_format": "0",
            "range_from": str(int(time.time()) - 3600),
            "range_to": str(int(time.time())),
            "cont_flag": "1",
        })
        msg = str(resp.get("message", "")).lower()
        if resp.get("s") == "error" and (
            "token" in msg or resp.get("code") in (-15, -16)
        ):
            raise TokenExpiredError(
                "Auth smoke test failed — token expired/invalid"
            )
        if resp.get("s") != "ok":
            raise RuntimeError(f"Auth smoke test failed: {resp}")
        log.info("Auth smoke test passed")
        return True

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
