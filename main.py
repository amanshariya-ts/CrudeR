# main.py
import os
import time
import logging
import threading

from config.config_loader import load_config
from data.fetcher import create_fetcher
from data.candle_poller import CandlePoller
from data.fyers_fetcher import TokenExpiredError   # <-- FIX IF NEEDED: match your fetcher module
from strategies.liquidity_pinbars import LiquidityPinBars
from alerts.telegram import TelegramAlert
from state.state_store import StateStore

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("framework")

STRATEGY_REGISTRY = {
    LiquidityPinBars.name: LiquidityPinBars,
}


def build_strategies(symbol, timeframe, strategy_cfgs):
    strategies = []
    for s in strategy_cfgs:
        if s.get("enabled", True) and s["name"] in STRATEGY_REGISTRY:
            strategies.append(STRATEGY_REGISTRY[s["name"]](
                symbol, timeframe, s.get("params", {})))
    return strategies


def run_market(cfg, tg, state, symbol, timeframe, exchange_name):
    """One watcher per symbol+timeframe. Own thread, own fetcher."""
    try:
        fetcher = create_fetcher(exchange_name, cfg)
    except KeyError as e:
        log.critical(f"Config missing key for fetcher '{exchange_name}': {e} — halting")
        os._exit(1)
    except ValueError as e:
        log.critical(f"Unknown exchange '{exchange_name}': {e} — halting")
        os._exit(1)

    strategies = build_strategies(symbol, timeframe, cfg["strategies"])
    poller = CandlePoller(fetcher, symbol, timeframe,
                          lookback=cfg["polling"]["lookback_bars"])
    log.info(f"[{symbol} {timeframe} @ {exchange_name}] "
             f"watching with {len(strategies)} strategy/ies")

    try:
        # Poller yields (closed_candle, full_df) — one API call per candle close.
        for candle, df in poller.poll_forever():
            try:
                closed_df = df.iloc[:-1]  # drop still-open candle — free, local
                for strat in strategies:
                    signal = strat.evaluate(closed_df)
                    if signal is None:
                        continue
                    key = f"{signal.symbol}|{signal.timeframe}|{strat.name}"
                    if state.already_alerted(key, signal.timestamp):
                        continue
                    signal.strategy = strat.name
                    log.info(f"SIGNAL: {signal.side} {signal.symbol} "
                             f"{signal.timeframe} @ {signal.price}")
                    tg.send(signal)
                    state.mark_alerted(key, signal.timestamp)
            except Exception as e:
                log.error(f"[{symbol} {timeframe}] eval error: {e}")

    except TokenExpiredError:
        # Shared Fyers token — when it dies, every market thread is doomed.
        # Hard-exit so the operator sees the process is down, not a silent zombie.
        log.critical(f"[{symbol}] Fyers token expired — shutting down. "
                     f"Run refresh_token.py, then restart the bot.")
        os._exit(1)


def main():
    cfg = load_config()
    tg = TelegramAlert(cfg["alerts"]["telegram"]["bot_token"],
                       cfg["alerts"]["telegram"]["chat_id"])
    state = StateStore()

    threads = []
    for market in cfg["markets"]:
        default_exchange = cfg.get("exchange", {}).get("name", "fyers")
        exchange_name = market.get("exchange", default_exchange)

        t = threading.Thread(
            target=run_market,
            args=(cfg, tg, state,
                  market["symbol"], market["timeframe"], exchange_name),
            daemon=True,
        )
        t.start()
        threads.append(t)

    log.info(f"Watching {len(cfg['markets'])} market(s) in parallel")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C)")


if __name__ == "__main__":
    main()
