"""Regression tests for main-loop stall issues after Alpaca read timeouts.

Two separate bugs are covered:

1. **``waiting_daily_run`` backoff and timer reset** (reproducible, verified by
   these tests): when ``bot_enabled=True`` and the daily run is not due, the
   old code set ``_paused=False`` and ``_last_healthcheck=None`` too early,
   causing unnecessary backoff and a healthcheck on every iteration.

2. **Defensive hardening** of ``next_delay`` (exponent cap, no overflow) and
   ``run()`` (fallback on delay-calculation errors, iteration-duration gauge
   and slow-iteration warning) to make any future stall identifiable.
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


# ---- waiting_daily_run: backoff and timer reset bugs -------------------------

def test_waiting_daily_run_healthcheck_failure_no_backoff(daily_store, provider):
    """Healthcheck failures in ``waiting_daily_run`` must not grow ``_failures``
    or trigger exponential backoff.  Before the fix, ``_paused=False`` in this
    state caused ``next_delay`` to apply up to 300 s of backoff.
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
        clock.t += 300

    assert all(d == 60 for d in delays), f"expected no backoff, got {delays}"
    assert _metric("bot_broker_connected", broker="alpaca") == 0


def test_waiting_daily_run_healthcheck_interval_wall_time(daily_store, provider):
    """In ``waiting_daily_run``, healthchecks must respect the configured
    interval across realistic wall-time steps.  Before the fix,
    ``_last_healthcheck = None`` was reset every iteration, running a
    healthcheck on every single call regardless of elapsed time.

    Scenario: healthcheck_interval=300 s, iterations at t=0, 60, 120, 180, 240,
    300, 360.  Only t=0 and t=300 should trigger a healthcheck (2 of 7).
    """
    store = _daily_store_outside_trading(daily_store)
    broker = TimingOutBroker()
    clock = FakeClock(start=0.0)
    now_et_before_run = datetime(2026, 11, 5, 13, 25, tzinfo=UTC)

    bot = TradingBot(store, provider, broker, healthcheck_interval=300, clock=clock,
                     now=lambda: now_et_before_run)

    hc_times = []
    for minute in range(7):
        clock.t = minute * 60.0
        result = bot.iteration()
        if "healthcheck" in result:
            hc_times.append(minute * 60)

    assert hc_times == [0, 300], (
        f"expected healthchecks at t=0 and t=300 only, got t={hc_times}")
    assert broker.account_calls == 2


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
    bot._failures = 2_000_000

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
        "should log the delay-calc error"


# ---- Iteration duration diagnostics -----------------------------------------

def test_iteration_duration_via_run(daily_store, provider):
    """``bot_iteration_duration_seconds`` must reflect the actual wall-clock
    duration measured by ``iteration()`` itself.  Verified through ``run()``
    with a fake clock that advances 7 s **inside** the iteration's work phase
    (``_iteration``), so the gauge reads a known value.
    """
    from monitoring import exporter

    clock = FakeClock(start=0.0)
    store = daily_store
    broker = MagicMock()
    broker.name = "alpaca"
    bot = TradingBot(store, provider, broker, healthcheck_interval=0, clock=clock)
    exporter.ITERATION_DURATION.set(-1)

    original = bot._iteration
    calls = [0]

    def slow_iteration(cfg):
        clock.t += 7.0
        result = original(cfg)
        calls[0] += 1
        if calls[0] >= 1:
            bot.stop()
        return result

    bot._iteration = slow_iteration

    bot.run()

    val = _metric("bot_iteration_duration_seconds")
    assert val is not None and val >= 7.0, (
        f"expected duration >= 7.0 s from fake clock, got {val}")
    assert bot._last_iteration_sec >= 7.0


def test_slow_iteration_is_logged(daily_store, provider, caplog):
    """Iterations exceeding the threshold are logged as warnings by ``run()``,
    reading the duration measured inside ``iteration()`` via fake clock.
    The clock advances during ``_iteration`` so it falls within the timing
    window of ``iteration()``.
    """
    clock = FakeClock(start=0.0)
    store = daily_store
    broker = MagicMock()
    broker.name = "alpaca"
    bot = TradingBot(store, provider, broker, healthcheck_interval=0, clock=clock)

    original = bot._iteration

    def slow_then_stop(cfg):
        clock.t += 150.0
        result = original(cfg)
        bot.stop()
        return result

    bot._iteration = slow_then_stop

    with caplog.at_level("WARNING", logger="bot"):
        bot.run()

    slow = [r for r in caplog.records if "iteración lenta" in r.msg]
    assert slow, f"expected slow-iteration warning, got {[r.msg for r in caplog.records]}"
    assert slow[0].elapsed_sec >= 150.0
