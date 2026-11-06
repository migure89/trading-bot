"""Regression tests for the main-loop hang after Alpaca read timeouts.

Root cause: when ``bot_enabled=True`` and the daily run is not due
(``waiting_daily_run``), the iteration sets ``_paused=False`` and resets
``_last_healthcheck=None`` unconditionally. This causes:

* ``_failures`` to grow from every healthcheck failure (broker timeout),
* ``next_delay`` to apply exponential backoff (capped at 300 s) because
  ``_paused is False``,
* a healthcheck to run on **every** iteration (the interval is bypassed), and
* combined with the shadow scan, total iteration cycles of 34-70+ minutes.

These tests pin the exact buggy behavior before the fix so the fix can be
verified deterministically and future regressions are caught.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from main import BACKOFF_MAX_SEC, TradingBot


def _metric(name, **labels):
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value(name, labels)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self):
        return self.t


class TimingOutBroker:
    """Broker whose reads always fail (simulates Alpaca read timeout)."""

    name = "alpaca"

    def __init__(self):
        self.ensure_calls = 0
        self.account_calls = 0

    def ensure_mode(self, mode):
        self.ensure_calls += 1

    def account(self):
        self.account_calls += 1
        raise TimeoutError("Read timed out (read timeout=20)")

    def positions(self):
        return []

    def connection_status(self):
        return {"broker": self.name, "connected": False}


def _daily_store_outside_trading(daily_store):
    """Configure for daily mode, bot enabled, outside NY trading hours."""
    daily_store.update({
        "bot_enabled": True,
        "timeframe": "1Day",
        "shadow_strategy": "off",
        "shadow_strategies": [],
    })
    return daily_store


# ---- Core regression: healthcheck failures must not accumulate backoff ------

def test_waiting_daily_run_healthcheck_failure_no_backoff(daily_store, provider):
    """Healthcheck failures in ``waiting_daily_run`` must not grow ``_failures``
    or trigger exponential backoff.  Before the fix, ``_paused=False`` in this
    state caused ``next_delay`` to apply up to 300 s of backoff after a single
    healthcheck failure.
    """
    store = _daily_store_outside_trading(daily_store)
    broker = TimingOutBroker()
    clock = FakeClock()
    now_et_before_run = datetime(2026, 11, 5, 13, 25, tzinfo=UTC)  # 08:25 ET < 09:35

    bot = TradingBot(store, provider, broker, healthcheck_interval=300, clock=clock,
                     now=lambda: now_et_before_run)

    delays = []
    for _ in range(6):
        result = bot.iteration()
        assert result["status"] == "waiting_daily_run"
        delays.append(bot.next_delay(60))
        clock.t += 300  # advance clock so healthcheck becomes due again

    # After fix: no backoff in waiting_daily_run — all delays should be the
    # base interval, not [60, 120, 240, 300, 300, 300].
    assert all(d == 60 for d in delays), f"expected no backoff, got {delays}"
    assert _metric("bot_broker_connected", broker="alpaca") == 0


def test_waiting_daily_run_does_not_reset_healthcheck_timer(daily_store, provider):
    """In ``waiting_daily_run``, the healthcheck timer must NOT be reset every
    iteration.  Before the fix, ``_last_healthcheck = None`` at the top of the
    ``bot_enabled=True`` path forced a healthcheck on every single iteration,
    amplifying the timeout overhead.
    """
    store = _daily_store_outside_trading(daily_store)
    broker = TimingOutBroker()
    clock = FakeClock()
    now_et_before_run = datetime(2026, 11, 5, 13, 25, tzinfo=UTC)

    bot = TradingBot(store, provider, broker, healthcheck_interval=300, clock=clock,
                     now=lambda: now_et_before_run)

    results = []
    for _ in range(5):
        results.append(bot.iteration())
        clock.t += 60  # advance 60 s (< healthcheck_interval=300)

    hc_count = sum(1 for r in results if "healthcheck" in r)
    # After fix: only 1 healthcheck (the first one); before fix all 5 would
    # trigger because _last_healthcheck was reset to None each iteration.
    assert hc_count == 1, f"expected 1 healthcheck, got {hc_count} in {results}"
    assert broker.account_calls == 1


def test_paused_healthcheck_failures_dont_grow_failures(daily_store, provider):
    """``_broker_failed(paused=True)`` must not increment ``_failures``.
    Only failures from active trading (``paused=False``) should drive backoff.
    """
    store = _daily_store_outside_trading(daily_store)
    broker = TimingOutBroker()
    clock = FakeClock()
    now_et_before_run = datetime(2026, 11, 5, 13, 25, tzinfo=UTC)

    bot = TradingBot(store, provider, broker, healthcheck_interval=60, clock=clock,
                     now=lambda: now_et_before_run)

    for _ in range(10):
        bot.iteration()
        clock.t += 60

    # After fix: _failures should not have grown from healthcheck-only failures
    assert bot._failures == 0, f"expected 0 _failures, got {bot._failures}"


# ---- next_delay hardening ---------------------------------------------------

def test_next_delay_caps_large_failure_count(daily_store, provider):
    """``next_delay`` must return a bounded value even with absurdly high
    ``_failures`` (e.g. after days without broker recovery).
    """
    bot = TradingBot(daily_store, provider, MagicMock(), healthcheck_interval=0)
    bot._paused = False

    for failures in (100, 1_000, 10_000, 1_000_000):
        bot._failures = failures
        delay = bot.next_delay(60)
        assert delay <= BACKOFF_MAX_SEC, (
            f"_failures={failures}: delay={delay} exceeds cap {BACKOFF_MAX_SEC}")
        assert isinstance(delay, int)


def test_next_delay_never_overflows(daily_store, provider):
    """``next_delay`` must never raise OverflowError, regardless of ``_failures``."""
    bot = TradingBot(daily_store, provider, MagicMock(), healthcheck_interval=0)
    bot._paused = False
    bot._failures = 2_000_000  # extreme: months of continuous failure

    delay = bot.next_delay(60)
    assert isinstance(delay, int)
    assert delay <= BACKOFF_MAX_SEC


# ---- run() loop robustness --------------------------------------------------

def test_run_loop_recovers_from_delay_error(daily_store, provider, caplog):
    """If ``next_delay`` or ``store.get`` raises, the run loop must not crash.
    It should fall back to a safe delay and continue iterating.
    """
    store = daily_store
    broker = MagicMock()
    broker.name = "alpaca"
    bot = TradingBot(store, provider, broker, healthcheck_interval=0)

    iteration_count = [0]
    original_iteration = bot.iteration

    def counting_iteration():
        iteration_count[0] += 1
        if iteration_count[0] >= 3:
            bot.stop()
        return original_iteration()

    bot.iteration = counting_iteration

    call_count = [0]

    def exploding_then_ok(interval):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OverflowError("cannot convert float infinity to integer")
        return 0

    bot.next_delay = exploding_then_ok

    original_wait = bot.shutdown.wait
    bot.shutdown.wait = lambda s: original_wait(min(s, 0.01))

    with caplog.at_level("ERROR", logger="bot"):
        bot.run()

    assert iteration_count[0] >= 3, "loop must survive OverflowError in next_delay"
    assert any("intervalo" in r.msg for r in caplog.records if r.levelname == "ERROR"), \
        "should log the OverflowError"


# ---- Iteration duration diagnostics -----------------------------------------

def test_iteration_duration_metric(daily_store, provider):
    """After each iteration, ``bot_iteration_duration_seconds`` must be set."""
    from monitoring import exporter

    store = daily_store
    bot = TradingBot(store, provider, MagicMock(), healthcheck_interval=0)
    exporter.ITERATION_DURATION.set(0)

    bot.iteration()
    val = _metric("bot_iteration_duration_seconds")
    assert val is not None and val >= 0


def test_slow_iteration_is_logged(daily_store, provider, caplog):
    """Iterations exceeding the threshold are logged as warnings by ``run()``."""
    clock = FakeClock()
    store = daily_store
    broker = MagicMock()
    broker.name = "alpaca"
    bot = TradingBot(store, provider, broker, healthcheck_interval=0, clock=clock)

    original_iteration = bot.iteration

    def slow_then_stop():
        clock.t += 150
        result = original_iteration()
        bot.stop()
        return result

    bot.iteration = slow_then_stop

    with caplog.at_level("WARNING", logger="bot"):
        bot.run()

    assert any("iteración lenta" in r.msg for r in caplog.records), \
        f"expected slow-iteration warning, got {[r.msg for r in caplog.records]}"
