# data/candle_poller.py
import time
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd

from data.fyers_fetcher import TokenExpiredError

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MCX_OPEN_HOUR = 9    # 09:00 IST
MCX_CLOSE_HOUR = 23  # 23:30 IST

# After a candle closes, the bar may take a moment to appear on Fyers.
CLOSE_LAG_SECONDS = 10        # first attempt after close
CLOSE_RETRY_SECONDS = 5       # retry cadence if bar not published yet
CLOSE_RETRY_MAX = 12          # max ~1 minute of retries, then give up till next close


class CandlePoller:
    """Emits each confirmed (closed) candle exactly once.
    Sleeps until candle boundaries — no fixed-interval API burning."""

    def __init__(self, fetcher, symbol: str, timeframe: str,
                 lookback: int = 200):
        self.fetcher = fetcher
        self.symbol = symbol
        self.timeframe = timeframe
        self.lookback = lookback
        self.tf_secs = self.fetcher.timeframe_to_seconds(timeframe)
        self._last_seen_close_ts: pd.Timestamp | None = None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _in_mcx_session(now_utc: datetime) -> bool:
        ist_now = now_utc.astimezone(IST)
        if ist_now.weekday() >= 5:
            return False
        return MCX_OPEN_HOUR <= ist_now.hour <= MCX_CLOSE_HOUR

    # ------------------------------------------------------------------ #
    def _seconds_to_next_close(self, now_utc: datetime) -> float:
        """Seconds until the next candle boundary (tf-aligned), from now."""
        now_epoch = now_utc.timestamp()
        next_boundary = (int(now_epoch) // self.tf_secs + 1) * self.tf_secs
        return next_boundary - now_epoch

    # ------------------------------------------------------------------ #
    def poll_forever(self):
        log.info(f"Poller started: {self.symbol} {self.timeframe} "
                 f"(wake-at-close mode, tf={self.tf_secs}s)")

        while True:
            now_utc = datetime.now(timezone.utc)

            # Outside MCX session: idle cheaply, re-check every minute.
            if not self._in_mcx_session(now_utc):
                log.info(f"Outside MCX session (IST {now_utc.astimezone(IST):%H:%M}) — idling 60s")
                time.sleep(60)
                continue

            # --- Phase 1: sleep until just after the next candle close ----
            wait = self._seconds_to_next_close(now_utc) + CLOSE_LAG_SECONDS
            log.info(f"Sleeping {wait:.0f}s until next candle close")
            time.sleep(wait)

            # --- Phase 2: fetch with short retries until bar published ----
            for attempt in range(CLOSE_RETRY_MAX):
                try:
                    df = self.fetcher.fetch_ohlcv(self.symbol, self.timeframe,
                                                  self.lookback)
                except TokenExpiredError as e:
                    log.critical(f"Fyers token expired — poller halted. {e}")
                    raise
                except Exception as e:
                    log.error(f"Poll error (attempt {attempt + 1}): {e}")
                    time.sleep(CLOSE_RETRY_SECONDS)
                    continue

                closed = df.iloc[:-1]  # drop still-open candle
                if len(closed):
                    latest = closed.iloc[-1]
                    if (self._last_seen_close_ts is None
                            or latest["timestamp"] > self._last_seen_close_ts):
                        if self._last_seen_close_ts is not None:  # skip replay
                            self._log_candle(latest)          # <-- NEW
                            yield latest, df                  # <-- CHANGED: emit pair
                        self._last_seen_close_ts = latest["timestamp"]
                        break
                time.sleep(CLOSE_RETRY_SECONDS)

            else:
                # All retries exhausted — skip this boundary, aim for next.
                log.warning(f"No new bar after {CLOSE_RETRY_MAX} retries "
                            f"— waiting for next close")

            # Small settle time so boundary math doesn't double-fire
            time.sleep(1)

    # ------------------------------------------------------------------ #
    def _log_candle(self, bar: pd.Series) -> None:                # <-- NEW
        """Log emitted candle OHLC + IST timestamp for TradingView cross-check."""
        ts = bar["timestamp"]
        # Normalize to IST whether ts is tz-aware UTC or tz-naive epoch-based
        if ts.tzinfo is None:
            ts_ist = ts.tz_localize("UTC").astimezone(IST)
        else:
            ts_ist = ts.astimezone(IST)
        log.info(
            f"YIELD {self.timeframe} "
            f"ts={ts_ist:%Y-%m-%d %H:%M} "
            f"O={bar['open']} H={bar['high']} "
            f"L={bar['low']} C={bar['close']} "
            f"V={bar.get('volume', '?')}"
        )
