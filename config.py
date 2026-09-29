# config.py
import os
import json
import logging

log = logging.getLogger(__name__)

# Every secret your app needs, with its config.json key name mapped to env var.
_ENV_MAP = {
    "app_id":       "FYERS_APP_ID",
    "secret_key":   "FYERS_SECRET_KEY",
    "client_id":    "FYERS_CLIENT_ID",
    "pin":          "FYERS_PIN",
    "bot_token":    "TELEGRAM_BOT_TOKEN",
    "chat_id":      "TELEGRAM_CHAT_ID",
}

# Non-secret settings can stay in a (committed) config.json
_FILE_SETTINGS = {
    "symbols":      ["MCX:CRUDEOIL25SEPFUT"],
    "timeframes":   ["5m", "30m", "1h"],
    "lookback":     200,
}


def load_config() -> dict:
    cfg = dict(_FILE_SETTINGS)

    # Optional local file for non-secret settings (never secrets!)
    if os.path.exists("config.json"):
        with open("config.json") as f:
            cfg.update(json.load(f))
        log.info("Loaded settings from config.json")

    # Secrets: env first (CI), fall back to file ONLY if it predates the leak
    missing = []
    for key, env_name in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if val:
            cfg[key] = val
        elif key not in cfg:
            missing.append(env_name)

    if missing:
        raise RuntimeError(
            f"Missing secrets (set as env vars): {missing}. "
            f"In GitHub Actions, map them under job.env; "
            f"locally, export them or use a .env file."
        )

    log.info("Config loaded: secrets from environment, %d settings", len(cfg))
    return cfg
