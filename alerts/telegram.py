# alerts/telegram.py
import os
import requests
import logging

log = logging.getLogger(__name__)

class TelegramAlert:
    def __init__(self, bot_token: str, chat_id: str):
        # Values are injected from environment variables, which GitHub Actions
        # populates from repository Secrets. Never hardcode them here.
        self.bot_token = bot_token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
        self.url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send(self, signal) -> None:
        ts = signal.timestamp.strftime("%Y-%m-%d %H:%M IST")
        header = "🟢 — Bullish CrudeR" if signal.side == "BUY" \
                 else "🔴 — Bearish CrudeR"

        msg = (
            f"{header}\n"
            f"Symbol: {signal.symbol}\n"
            f"Timeframe: {signal.timeframe}\n"
            f"Price: {signal.price}\n"
            f"Time: {ts}"
        )
        try:
            r = requests.post(self.url, json={
                "chat_id": self.chat_id,
                "text": msg,
            }, timeout=10)
            r.raise_for_status()
        except Exception as e:
            # Never log the URL/response body here — it can contain the token.
            log.error("Telegram send failed: %s", e)
