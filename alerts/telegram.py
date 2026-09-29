# alerts/telegram.py
import logging

import requests

log = logging.getLogger(__name__)


class TelegramAlert:
    """Sends alerts to Telegram via Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token or ""
        self.chat_id = chat_id or ""

    def send_text(self, text: str) -> bool:
        """Send a plain text message. Returns True on success."""
        if not self.bot_token or not self.chat_id:
            log.warning("Telegram not configured — skipping send")
            return False
        try:
            resp = requests.post(
                "https://api.telegram.org/bot" + self.bot_token + "/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            log.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
            return False

    def send(self, signal) -> bool:
        """Send a formatted message for a signal object."""
        arrow = "🟢 BUY CrudeR" if signal.side.upper() == "BUY" else "🔴 SELL CrudeR"
        text = arrow + " pinbar — " + signal.symbol + " [" + signal.timeframe + "]"
        if getattr(signal, "exchange", ""):
            text += " @ " + signal.exchange
        text += "\nPrice: " + f"{signal.price:.2f}"
        if getattr(signal, "strategy", ""):
            text += "\nStrategy: " + signal.strategy
        return self.send_text(text)
