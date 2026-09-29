# main.py
import os
import sys
import time
import logging
import threading
from datetime import datetime, timezone, timedelta

from config.config_loader import load_config
from data.fetcher import create_fetcher
from data.candle_poller import CandlePoller
from data.fyers_fetcher import FyersFetcher, TokenExpiredError
from strategies.liquidity_pinbars import LiquidityPinBars
from alerts.telegram import TelegramAlert
from state.state_store import StateStore

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("framework")

# ---------------------------------------------------------------------------
# Runtime controls
# ---------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))

# Max hours this process may run before self-exiting.
# GitHub Actions kills jobs at 6h -> we exit at 5.5h for clean teardown.
# Set MAX_RUNTIME_HOURS=0 to disable (e.g., running locally all day).
MAX_RUNTIME_HOURS = float(os.environ.get("MAX_RUNTIME_HOURS", "5.5"))

# MCX trading window (IST): 09:00 - 23:30
MCX_START_HOUR = 9
MCX_END_HOUR = 23
MCX_END_MINUTE = 30


def in_mcx_hours(now=None):
    """True if we're inside the MCX trading session (IST)."""
    now = now or datetime.now(IST)
    if MCX_START_HOUR <= now.hour < MCX_END_HOUR:
        return True
    return now.hour == MCX_END_HOUR and now.minute < MCX_END_MINUTE


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


# ---------------------------------------------------------------------------
# Shared signal evaluation — used by BOTH the loop path and --once path,
# so dedupe/alert logic can never drift between the two.
# ---------------------------------------------------------------------------
def process_candles(strategies, closed_df, state, tg, symbol, timeframe):
    """Evaluate all strategies against closed candles; alert on new signals."""
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
            # Stop wasting API calls once the session is over; the supervisor
            # exits the process, but this keeps threads quiet at the boundary.
            if not in_mcx_hours():
                log.info(f"[{symbol}] session over — thread idling down")
                return

            try:
                closed_df = df.iloc[:-1]  # drop still-open candle — free, local
                process_candles(strategies, closed_df, state, tg, symbol, timeframe)
            except Exception as e:
                log.error(f"[{symbol} {timeframe}] eval error: {e}")

    except TokenExpiredError:
        # Shared Fyers token — when it dies, every market thread is doomed.
        # Hard-exit so the operator (or Actions log) sees the process is down.
        log.critical(f"[{symbol}] Fyers token expired — shutting down. "
                     f"Refresh the token, then restart the bot.")
        os._exit(1)


def evaluate_once(cfg, tg, state):
    """Single evaluation pass over all markets — used by --once mode."""
    for market in cfg["markets"]:
        symbol = market["symbol"]
        timeframe = market["timeframe"]
        default_exchange = cfg.get("exchange", {}).get("name", "fyers")
        exchange_name = market.get("exchange", default_exchange)

        try:
            fetcher = create_fetcher(exchange_name, cfg)
            df = fetcher.fetch_ohlcv(symbol, timeframe,
                                     cfg["polling"]["lookback_bars"])
            closed_df = df.iloc[:-1]
            strategies = build_strategies(symbol, timeframe, cfg["strategies"])
            process_candles(strategies, closed_df, state, tg, symbol, timeframe)
        except Exception as e:
            log.error(f"[{symbol} {timeframe}] once-mode error: {e}")


# ---------------------------------------------------------------------------
# Startup checks — fail fast (seconds, not hours)
# ---------------------------------------------------------------------------
def auth_smoke_test(exchange_name):
    """Verify credentials are alive BEFORE burning the run.
    A dead token fails here in ~30s; without this check it would surface
    in each thread on the first candle close, after an unknown delay."""
    if exchange_name != "fyers":
        return
    try:
        FyersFetcher().ping()
    except TokenExpiredError:
        log.critical("Auth smoke test failed: token expired/invalid — "
                     "run refresh_token.py, then restart.")
        os._exit(1)
    except Exception as e:
        # Network hiccup at startup shouldn't kill the run — the per-call
        # retry logic in the fetcher will handle transient issues later.
        log.warning(f"Auth smoke test could not complete: {e} — continuing")


def get_telegram(cfg):
    """Single validation point for alert config — clear error, not a KeyError
    from deep inside a thread's first signal."""
    try:
        tcfg = cfg["alerts"]["telegram"]
        return TelegramAlert(tcfg["bot_token"], tcfg["chat_id"])
    except (KeyError, TypeError):
        log.critical("alerts.telegram.bot_token / chat_id missing from config — halting")
        os._exit(1)


def main():
    cfg = load_config()
    tg = get_telegram(cfg)
    state = StateStore()

    default_exchange = cfg.get("exchange", {}).get("name", "fyers")

    # --once mode: one evaluation, then exit (Actions testing / manual runs)
    if "--once" in sys.argv:
        evaluate_once(cfg, tg, state)
        log.info("--once complete — exiting")
        sys.exit(0)

    # Fail fast on dead credentials before spawning anything.
    auth_smoke_test(default_exchange)

    threads = []
    for market in cfg["markets"]:
        exchange_name = market.get("exchange", default_exchange)

        t = threading.Thread(
            target=run_market,
            args=(cfg, tg, state,
                  market["symbol"], market["timeframe"], exchange_name),
            daemon=True,
            name=f"watch-{market['symbol']}-{market['timeframe']}",
        )
        t.start()
        threads.append(t)

    if MAX_RUNTIME_HOURS > 0:
        log.info(f"Watching {len(cfg['markets'])} market(s) in parallel "
                 f"(max runtime {MAX_RUNTIME_HOURS}h, MCX session 09:00-23:30 IST)")
        deadline = time.time() + MAX_RUNTIME_HOURS * 3600
    else:
        log.info(f"Watching {len(cfg['markets'])} market(s) in parallel (no runtime limit)")
        deadline = None

    try:
        while any(t.is_alive() for t in threads):
            # Session check first: outside MCX hours there is nothing to watch.
            if not in_mcx_hours():
                log.info("Outside MCX session (09:00-23:30 IST) — exiting. "
                         "Next scheduled run will pick up.")
                break
            # Runtime ceiling: exit before GitHub's 6h job kill.
            if deadline is not None and time.time() >= deadline:
                log.info("Runtime limit reached — shutting down cleanly. "
                         "Cron will restart us.")
                break
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C)")


if __name__ == "__main__":
    main()
