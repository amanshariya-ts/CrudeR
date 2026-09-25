# state/state_store.py
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class StateStore:
    """Persists last-alerted candle per symbol+timeframe+strategy,
    so restarts don't re-alert on the same candle.

    Thread-safe: multiple market threads share one instance.
    Crash-safe: atomic write (temp file + rename), corrupt file recovered."""

    def __init__(self, path: str = "state/state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.data = self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # Half-written state (e.g. os._exit mid-write, power cut).
            # Back it up for forensics, start clean. Worst case: one
            # duplicate alert on the candle that was being written.
            backup = self.path.with_suffix(".corrupt.bak")
            try:
                self.path.replace(backup)
                log.warning(f"Corrupt state file — backed up to {backup}, "
                            f"starting fresh (possible duplicate alert)")
            except OSError:
                log.warning(f"Corrupt state file at {self.path} "
                            f"(could not back up) — starting fresh")
            return {}

    # ------------------------------------------------------------------ #
    def already_alerted(self, key: str, candle_ts) -> bool:
        with self._lock:
            return str(self.data.get(key)) == str(candle_ts)

    # ------------------------------------------------------------------ #
    def mark_alerted(self, key: str, candle_ts):
        with self._lock:
            self.data[key] = str(candle_ts)
            self._atomic_write()

    # ------------------------------------------------------------------ #
    def _atomic_write(self):
        """Write to temp file, then rename over the target.
        Rename is atomic on Windows & POSIX — readers never see a
        half-written file even if we crash between the two steps."""
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        except OSError as e:
            # In-memory state is still updated (dedup holds for this
            # session); only the restart-persistence is degraded.
            log.error(f"State persist failed: {e}")
