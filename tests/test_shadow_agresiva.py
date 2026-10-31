"""Sombra agresiva: lev_trend, lev_rotation, dyn_momentum y el escáner de universo (sin red)."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main
from config.settings import SettingsStore, shadow_strategy_list
from core import universe as uni
from main import TradingBot
from monitoring import exporter
from shadow.runner import ShadowRunner
from strategies import dyn_momentum, lev_rotation, lev_trend
from strategies.portfolio import hold_between_rebalances, weekly_rebalance_days
from tests.conftest import FakeScanner
from tests.test_daily_shadow import THU_10ET, _old_db, daily_bars

AGGR = ["lev_trend", "lev_rotation", "dyn_momentum"]


def _index(n, end="2026-10-28"):
    return pd.bdate_range(end=end, periods=n, tz="America/New_York").tz_convert("UTC")


@pytest.fixture
def daily_provider():
    p = MagicMock(spec=["get_bars"])
    p.get_bars.side_effect = lambda sym, tf, days, **kw: daily_bars(sum(map(ord, sym)))
    return p


# ---- utilidades de cartera --------------------------------------------------------------
def test_weekly_rebalance_days_last_session_of_week():
    idx = _index(10, end="2026-10-28")          # termina un miércoles
    reb = weekly_rebalance_days(idx)
    local = idx.tz_convert("America/New_York")
    assert [d.day for d in local[reb.to_numpy()]] == [16, 23]    # viernes 16/10 y 23/10
    assert not reb.iloc[-1]                                     # semana en curso
    fri = _index(5, end="2026-10-30")
    assert weekly_rebalance_days(fri).iloc[-1]


def test_hold_between_rebalances():
    idx = _index(4)
    dec = pd.DataFrame({"A": [1.0, 0.0, 0.0, 1.0], "B": [0.0, 1.0, 1.0, 0.0]}, index=idx)
    held = hold_between_rebalances(dec, pd.Series([False, True, False, False], index=idx))
    assert held["A"].tolist() == [0.0, 0.0, 0.0, 0.0] and held["B"].tolist() == [0.0, 1.0, 1.0, 1.0]


# ---- estrategias ------------------------------------------------------------------------
def test_lev_trend_follows_qqq_sma200():
    n = 260
    idx = _index(n)
    qqq = pd.Series(np.r_[np.linspace(100, 200, 230), np.linspace(200, 100, 30)], index=idx)
    w = lev_trend.target_weights(pd.DataFrame({"QQQ": qqq, "TQQQ": qqq * 0.5}))
    assert list(w.columns) == ["TQQQ"]
    assert w["TQQQ"].iloc[:199].eq(0).all()           # sin SMA200 todavía
    assert w["TQQQ"].iloc[220] == 1.0 and w["TQQQ"].iloc[-1] == 0.0
    assert w.max().max() <= 1.0


def test_lev_rotation_picks_best_momentum_weekly_and_respects_regime():
    n = 140
    idx = _index(n)
    closes = pd.DataFrame({s: 100 * np.exp(np.linspace(0, g, n)) for s, g in
                           zip(lev_rotation.TRADABLE, [0.1, 0.9, 0.2, 0.3, -0.2], strict=True)}, index=idx)
    w = lev_rotation.target_weights(closes, pd.Series(True, index=idx))
    assert (w.sum(axis=1) <= 1 + 1e-9).all()
    assert w.iloc[-1].idxmax() == "SOXL" and w.iloc[-1].max() == 1.0
    reb = weekly_rebalance_days(idx).to_numpy()
    changes = w.diff().abs().sum(axis=1).to_numpy()[1:] > 0
    assert not (changes & ~reb[1:]).any()              # solo cambia en días de rebalanceo
    off = lev_rotation.target_weights(closes, pd.Series(False, index=idx))
    assert off.to_numpy().sum() == 0


def test_dyn_momentum_top10_equal_weight():
    panel, _ = FakeScanner(n_symbols=25).closes(THU_10ET)
    w = dyn_momentum.target_weights(panel)
    last = w.iloc[-1]
    assert (last > 0).sum() == 10 and last.sum() == pytest.approx(1.0)
    assert set(last[last > 0].round(6)) == {0.1}
    sc = dyn_momentum.score(panel)
    assert sc.iloc[:126].isna().all().all()


# ---- configuración --------------------------------------------------------------------
def test_shadow_strategies_settings_compat(daily_store):
    cfg = daily_store.load()
    assert cfg["shadow_strategies"] == ["blend", *AGGR] and cfg["shadow_universe"] == "dynamic"
    daily_store.update({"shadow_strategy": "rsi2"})
    assert daily_store.load()["shadow_strategies"] == ["rsi2"]
    daily_store.update({"shadow_strategies": ["lev_trend", "blend", "lev_trend"]})
    cfg = daily_store.load()
    assert cfg["shadow_strategies"] == ["lev_trend", "blend"] and cfg["shadow_strategy"] == "lev_trend"
    daily_store.update({"shadow_strategy": "off"})
    assert daily_store.load()["shadow_strategies"] == [] and shadow_strategy_list(daily_store.load()) == []
    for bad in ({"shadow_strategies": ["ema_rsi"]}, {"shadow_strategies": "blend"},
                {"shadow_universe": "todo"}):
        with pytest.raises(ValueError):
            daily_store.update(bad)
    assert shadow_strategy_list({"shadow_strategy": "rsi2"}) == ["rsi2"]       # config antigua


@pytest.mark.parametrize("legacy,expected", [("blend", ["blend", *AGGR]), ("rsi2", ["rsi2", *AGGR]),
                                             ("off", [])])
def test_migration_enables_aggressive_shadow(tmp_path, legacy, expected):
    path = tmp_path / "old.db"
    _old_db(path, shadow_strategy=legacy, bot_enabled=False, mode="paper", strategy="ema_rsi",
            timeframe="1Day", lookback_days=420)
    cfg = SettingsStore(path).load()
    assert cfg["shadow_strategies"] == expected
    assert (cfg["bot_enabled"], cfg["mode"], cfg["strategy"], cfg["shadow_strategy"]) == (
        False, "paper", "ema_rsi", legacy)
    store = SettingsStore(path)                       # idempotente: no se vuelve a aplicar
    store.update({"shadow_strategies": ["lev_trend"]})
    assert SettingsStore(path).load()["shadow_strategies"] == ["lev_trend"]


def test_migration_keeps_existing_list(tmp_path):
    path = tmp_path / "old.db"
    _old_db(path, shadow_strategy="blend", shadow_strategies=["blend"])
    assert SettingsStore(path).load()["shadow_strategies"] == ["blend"]


# ---- runner ------------------------------------------------------------------------
def test_runner_all_strategies_without_orders(daily_store, daily_provider, fake_scanner):
    daily_store.update({"shadow_symbols": ["AAPL", "MSFT"]})        # sin SPY/QQQ ni ETFs apalancados
    result = ShadowRunner(daily_store, daily_provider).run(daily_store.load(), THU_10ET)
    assert result["errors"] == {} and result["orders_sent"] == 0
    snaps = result["strategies"]
    assert list(snaps) == ["blend", *AGGR]
    for snap in snaps.values():
        assert snap["trades"] is False and snap["orders_sent"] == 0 and snap["as_of"] == "2026-10-28"
    assert set(snaps["lev_trend"]["weights"]) == {"TQQQ"}
    assert set(snaps["lev_rotation"]["weights"]) == set(lev_rotation.TRADABLE)
    assert snaps["dyn_momentum"]["universe"]["source"] == "dynamic"
    assert len(snaps["dyn_momentum"]["weights"]) == 10
    fetched = [c.args[0] for c in daily_provider.get_bars.call_args_list]
    assert {"QQQ", "TQQQ", "SOXL", "UPRO", "TECL", "TNA", "SPY"} <= set(fetched)
    assert len(fetched) == len(set(fetched))                        # caché compartida por ejecución
    assert fake_scanner.calls == 1
    assert REGISTRY.get_sample_value("bot_shadow_universe_size", {"strategy": "dyn_momentum"}) == 15
    w = REGISTRY.get_sample_value("bot_shadow_target_weight", {"strategy": "lev_trend", "symbol": "TQQQ"})
    assert w == snaps["lev_trend"]["weights"]["TQQQ"]
    assert REGISTRY.get_sample_value("bot_shadow_last_run_timestamp_seconds", {"strategy": "blend"}) > 0


def test_runner_scanner_failure_is_isolated(daily_store, daily_provider):
    before = REGISTRY.get_sample_value("bot_shadow_errors_total", {"strategy": "dyn_momentum"}) or 0
    scanner = FakeScanner(fail=uni.ScanBudgetExceeded("90s"))
    runner = ShadowRunner(daily_store, daily_provider, scanner=scanner)
    result = runner.run(daily_store.load(), THU_10ET)
    assert list(result["strategies"]) == ["blend", "lev_trend", "lev_rotation"]
    assert "90s" in result["errors"]["dyn_momentum"]
    assert REGISTRY.get_sample_value("bot_shadow_errors_total", {"strategy": "dyn_momentum"}) == before + 1


def test_runner_fixed_universe_uses_shadow_symbols(daily_store, daily_provider, fake_scanner):
    daily_store.update({"shadow_universe": "fixed", "shadow_symbols": ["AAPL", "MSFT", "KO"]})
    runner = ShadowRunner(daily_store, daily_provider)
    result = runner.run(daily_store.load(), THU_10ET, only=["dyn_momentum"])
    snap = result["strategies"]["dyn_momentum"]
    assert snap["universe"] == {"source": "fixed", "size": 3} and fake_scanner.calls == 0
    assert set(snap["weights"]) <= {"AAPL", "MSFT", "KO"}


def test_exporter_keeps_strategies_apart():
    exporter.update_shadow({"strategy": "t_a", "weights": {"X": 0.5, "Y": 0.5}, "gross_exposure": 1})
    exporter.update_shadow({"strategy": "t_b", "weights": {"X": 1.0}, "gross_exposure": 1})
    exporter.update_shadow({"strategy": "t_a", "weights": {"Y": 1.0}, "gross_exposure": 1})
    get = REGISTRY.get_sample_value
    assert get("bot_shadow_target_weight", {"strategy": "t_a", "symbol": "X"}) is None
    assert get("bot_shadow_target_weight", {"strategy": "t_a", "symbol": "Y"}) == 1.0
    assert get("bot_shadow_target_weight", {"strategy": "t_b", "symbol": "X"}) == 1.0


def test_bot_restores_shadow_metrics_on_start(daily_store, daily_provider, fake_scanner):
    ShadowRunner(daily_store, daily_provider).run(daily_store.load(), now=THU_10ET)
    exporter.SHADOW_LAST_RUN.labels("lev_trend").set(0)
    exporter.SHADOW_UNIVERSE.labels("dyn_momentum").set(0)
    bot = TradingBot(daily_store, daily_provider, MagicMock(), healthcheck_interval=0)
    bot.running = False                                    # solo el arranque, sin iteraciones
    bot.run()
    get = REGISTRY.get_sample_value
    assert get("bot_shadow_last_run_timestamp_seconds", {"strategy": "lev_trend"}) == THU_10ET.timestamp()
    assert get("bot_shadow_universe_size", {"strategy": "dyn_momentum"}) == fake_scanner.n_symbols
    assert get("bot_shadow_gross_exposure", {"strategy": "lev_rotation"}) is not None
    assert get("bot_shadow_universe_size", {"strategy": "blend"}) == len(daily_store.load()["shadow_symbols"])


# ---- bot: reintento solo de las que fallan ----------------------------------------------
def test_bot_retries_only_failed_strategies(daily_store, daily_provider):
    scanner = FakeScanner(fail=TimeoutError("lento"))
    clock = {"now": THU_10ET}
    bot = TradingBot(daily_store, daily_provider, MagicMock(), healthcheck_interval=0,
                     now=lambda: clock["now"], shadow=ShadowRunner(daily_store, daily_provider, scanner))
    assert bot.iteration()["shadow"] == "partial"
    assert bot._shadow_pending == ["dyn_momentum"]
    for minutes, expected in ((16, "partial"), (32, "partial")):
        clock["now"] = THU_10ET + pd.Timedelta(minutes=minutes)
        daily_provider.get_bars.reset_mock()
        assert bot.iteration()["shadow"] == expected
        daily_provider.get_bars.assert_not_called()           # no se recalculan las que ya salieron
    assert scanner.calls == 3 and bot._shadow_pending is None   # 3 intentos y se cierra el día
    clock["now"] = THU_10ET + pd.Timedelta(minutes=50)
    assert "shadow" not in bot.iteration()
    scanner.fail = None
    bot2 = TradingBot(daily_store, daily_provider, MagicMock(), healthcheck_interval=0,
                      now=lambda: THU_10ET, shadow=ShadowRunner(daily_store, daily_provider, scanner))
    assert bot2.iteration()["shadow"] == "ok"


def test_bot_trades_before_shadow(daily_store, daily_provider, monkeypatch):
    order = []
    monkeypatch.setattr(TradingBot, "_iteration", lambda self, cfg: order.append("trade") or {"status": "x"})
    monkeypatch.setattr(TradingBot, "maybe_run_shadow", lambda self, cfg: order.append("shadow") or "ok")
    TradingBot(daily_store, daily_provider, MagicMock(), healthcheck_interval=0).iteration()
    assert order == ["trade", "shadow"]


def test_shadow_once_prints_all(daily_store, daily_provider, monkeypatch, capsys):
    monkeypatch.setattr(main, "SettingsStore", lambda: daily_store)
    monkeypatch.setattr(main, "DataProviderRegistry", lambda: daily_provider)
    monkeypatch.setattr("sys.argv", ["main.py", "--shadow-once"])
    monkeypatch.setattr("shadow.runner.datetime", SimpleNamespace(now=lambda tz=None: THU_10ET))
    main.main()
    out = json.loads(capsys.readouterr().out)
    assert list(out["strategies"]) == ["blend", *AGGR] and out["orders_sent"] == 0


# ---- API y dashboard ------------------------------------------------------------------
def test_api_shadow_by_strategy_and_config(daily_store, daily_provider):
    from api import main as api_main

    api_main.app.dependency_overrides[api_main.get_store] = lambda: daily_store
    try:
        client = TestClient(api_main.app)
        ShadowRunner(daily_store, daily_provider).run(daily_store.load(), THU_10ET)
        r = client.get("/shadow").json()
        assert r["strategy"] == "blend" and r["strategies"] == ["blend", *AGGR] and r["universe"] == "dynamic"
        assert r["latest"]["strategy"] == "blend" and len(r["history"]) == 1
        latest = {k: v["latest"]["strategy"] for k, v in r["by_strategy"].items()}
        assert latest == {n: n for n in r["strategies"]}
        cfg = client.post("/config", json={"shadow_strategies": ["dyn_momentum", "blend"]}).json()
        assert cfg["shadow_strategies"] == ["dyn_momentum", "blend"]
        assert cfg["shadow_strategy"] == "dyn_momentum"
        assert client.post("/config", json={"shadow_strategies": ["magia"]}).status_code in (400, 422)
        assert client.post("/config", json={"shadow_universe": "fixed"}).json()["shadow_universe"] == "fixed"
        assert client.post("/config", json={"shadow_universe": "x"}).status_code == 422
        assert client.post("/config", json={"shadow_strategy": "lev_trend"}).json()["shadow_strategies"] == [
            "lev_trend"]
    finally:
        api_main.app.dependency_overrides.clear()


def test_dashboard_shadow_tabs_per_strategy(monkeypatch):
    import requests
    from streamlit.testing.v1 import AppTest

    from tests.test_dashboard import APP, FakeAPI, FakeResponse

    def snap(name, as_of, weights, **extra):
        return {"strategy": name, "as_of": as_of, "regime_bull": True, "gross_exposure": 1.0,
                "weights": weights, "orders_sent": 0, "errors": {}, "changes": [], **extra}

    by = {"blend": {"latest": snap("blend", "2026-10-28", {"SPY": 1.0}), "history": []},
          "lev_trend": {"latest": snap("lev_trend", "2026-10-27", {"TQQQ": 1.0}), "history": []},
          "dyn_momentum": {"latest": snap("dyn_momentum", "2026-10-26", {"NVDA": 0.1},
                                          universe={"source": "dynamic", "size": 100}), "history": []},
          "lev_rotation": {"latest": None, "history": []}}

    class ShadowAPI(FakeAPI):
        def __call__(self, method, url, timeout=None, **kw):
            if url.endswith("/shadow"):
                return FakeResponse(200, {"strategy": "blend", "strategies": list(by),
                                          "daily_run_time": "09:35",
                                          "orders": False, "latest": by["blend"]["latest"], "history": [],
                                          "by_strategy": by})
            return super().__call__(method, url, timeout, **kw)

    monkeypatch.setenv("API_URL", "http://api:8000")
    monkeypatch.setattr(requests, "request", ShadowAPI())
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    values = {str(m.value) for m in at.metric}
    assert {"2026-10-28", "2026-10-27", "2026-10-26"} <= values
    assert any("Universo dinámico" in str(m.value) and "100" in str(m.value) for m in at.markdown)
    labels = [t.label for t in at.tabs]
    assert {"TQQQ con tendencia", "Rotación apalancada", "Momentum dinámico"} <= set(labels)


# ---- escáner de universo ----------------------------------------------------------------
def _asset(sym, name="Acme Corp", exchange="NASDAQ", **kw):
    return {"symbol": sym, "name": name, "exchange": exchange, "status": "active", "tradable": True,
            "class": "us_equity", **kw}


def test_filter_assets_excludes_leveraged_volatility_and_otc():
    raw = [_asset("AAPL", "Apple Inc."), _asset("USMV", "iShares MSCI USA Min Vol Factor ETF"),
           _asset("SPLV", "Invesco S&P 500 Low Volatility ETF"),
           _asset("TQQQ", "ProShares UltraPro QQQ"), _asset("NVDL", "GraniteShares 2x Long NVDA Daily ETF"),
           _asset("SQQQ", "ProShares UltraPro Short QQQ"), _asset("UVXY", "ProShares Ultra VIX Short-Term"),
           _asset("SVIX", "-1x Short VIX Futures ETF"), _asset("SH", "ProShares Short S&P500"),
           _asset("XYZ", exchange="OTC"), _asset("BRK.B", "Berkshire"), _asset("ABCDEF"),
           _asset("ZZZ", tradable=False), _asset("WARR+", "Warrants")]
    assert uni.filter_assets(raw) == ["AAPL", "BRK.B", "SPLV", "USMV"]


def _long_bars(closes: pd.DataFrame, volume=1e6) -> pd.DataFrame:
    df = closes.stack().rename("close").reset_index()
    df.columns = ["timestamp", "symbol", "close"]
    return df.assign(volume=volume)


def test_liquidity_rank_and_jump_filter():
    idx = _index(25)
    closes = pd.DataFrame({"BIG": 100.0, "MID": 50.0, "CHEAP": 2.0, "THIN": 100.0, "JUMP": 10.0}, index=idx)
    closes.iloc[-3:, closes.columns.get_loc("JUMP")] = 80.0              # salto de 8x
    bars = _long_bars(closes)
    bars.loc[bars.symbol == "BIG", "volume"] = 5e6
    bars.loc[bars.symbol == "THIN", "volume"] = 1e3
    bars.loc[bars.symbol == "CHEAP", "volume"] = 1e8
    bars.loc[bars.symbol == "JUMP", "volume"] = 1e7
    table = uni.liquidity_table(bars)
    assert bool(table.at["JUMP", "jump"]) and not table.at["BIG", "jump"]
    assert uni.rank_universe(table, top=10) == ["BIG", "MID"]
    assert uni.rank_universe(table, top=1) == ["BIG"]


class FakeData:
    """Cliente de datos falso: barras sintéticas por símbolo; ``invalid`` provoca el 400 de Alpaca."""

    def __init__(self, invalid=(), n=25):
        self.invalid, self.n, self.calls = set(invalid), n, []

    def get_stock_bars(self, req):
        syms = list(req.symbol_or_symbols)
        self.calls.append(syms)
        bad = [s for s in syms if s in self.invalid]
        if bad:
            raise Exception(f'{{"message":"invalid symbol: {bad[0]}"}}')
        idx = pd.bdate_range(end=req.end.date(), periods=self.n, tz="America/New_York").tz_convert("UTC")
        rows = [(s, t, 10.0 + i, 10.0 + i, 10.0 + i, 10.0 + i, 1e6 * (i + 1))
                for i, s in enumerate(syms) for t in idx]
        df = pd.DataFrame(rows, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        return SimpleNamespace(df=df.set_index(["symbol", "timestamp"]))


def _sym(i: int) -> str:
    return "A" + "".join(chr(65 + (i // 26 ** k) % 26) for k in (2, 1, 0))


def _scanner(tmp_path, data, n_assets=450, budget=60.0, clock=None, top=100):
    trading = MagicMock()
    trading.get.return_value = [_asset(_sym(i)) for i in range(n_assets)]
    return uni.UniverseScanner("k", "s", cache_dir=tmp_path / "cache", budget_sec=budget,
                               trading_client=trading,
                               data_client=data, top=top, clock=clock or (lambda: 0.0), sleep=lambda s: None)


def test_scanner_batches_cache_and_top(tmp_path):
    data = FakeData()
    sc = _scanner(tmp_path, data)
    panel, stats = sc.closes(THU_10ET)
    n_assets = 450
    assert [len(c) for c in data.calls[:-1]] == [200, 200, n_assets - 400]   # lotes de 200
    assert panel.shape[1] == 100 and stats["size"] == 100 and stats["candidates"] == n_assets
    first = panel.columns.tolist()
    assert (tmp_path / "cache" / "universe.json").exists()
    assert not (tmp_path / "cache" / "scan_progress.json").exists()
    sc.trading_client.get.assert_called_once_with("/assets", {"asset_class": "us_equity", "status": "active"})
    calls = len(data.calls)
    panel2, _ = sc.closes(THU_10ET)                     # mismo día: todo desde la caché
    assert len(data.calls) == calls and panel2.columns.tolist() == first
    sc.closes(datetime(2026, 10, 30, 14, 0, tzinfo=UTC))   # otro día, misma semana: solo historial
    assert len(data.calls) == calls + 1


def test_scanner_drops_invalid_symbol_and_retries(tmp_path):
    data = FakeData()
    sc = _scanner(tmp_path, data, n_assets=50, top=10)
    sc.trading_client.get.return_value.append(_asset("BADX"))
    data.invalid = {"BADX"}
    panel, _ = sc.closes(THU_10ET)
    assert "BADX" in data.calls[0] and "BADX" not in data.calls[1]
    assert panel.shape[1] == 10


def test_scanner_budget_resumes_from_progress(tmp_path):
    data = FakeData()
    t = {"now": 0.0}

    def clock():
        return t["now"]

    sc = _scanner(tmp_path, data, budget=10.0, clock=clock)
    orig = data.get_stock_bars

    def slow(req):
        t["now"] += 6.0
        return orig(req)

    data.get_stock_bars = slow
    with pytest.raises(uni.ScanBudgetExceeded):
        sc.closes(THU_10ET)
    progress = json.loads((tmp_path / "cache" / "scan_progress.json").read_text())
    assert progress["done"] == [0, 1]
    t["now"] = 100.0
    data.calls.clear()
    panel, _ = sc.closes(THU_10ET)                       # reanuda: solo el tercer lote + historial
    assert len(data.calls) == 2 and panel.shape[1] == 100


def test_scanner_history_drops_jumps(tmp_path):
    data = FakeData()
    sc = _scanner(tmp_path, data, n_assets=30, top=5)
    orig = data.get_stock_bars

    def with_jump(req):
        res = orig(req)
        df = res.df.reset_index()
        if len(df.timestamp.unique()) > 25 or len(req.symbol_or_symbols) == 5:
            first = req.symbol_or_symbols[0]
            mask = (df.symbol == first) & (df.timestamp == df.timestamp.max())
            df.loc[mask, "close"] *= 10
        return SimpleNamespace(df=df.set_index(["symbol", "timestamp"]))

    data.get_stock_bars = with_jump
    panel, stats = sc.closes(THU_10ET)
    assert len(stats["dropped_jumps"]) == 1 and panel.shape[1] == 4


def test_scanner_needs_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    sc = uni.UniverseScanner(cache_dir=tmp_path, budget_sec=5)
    with pytest.raises(RuntimeError, match="APCA_API_KEY_ID"):
        sc.closes(THU_10ET)


def test_budget_and_cache_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SHADOW_SCAN_BUDGET_SEC", "600")
    assert uni.budget_from_env() == 600
    monkeypatch.setenv("SHADOW_SCAN_BUDGET_SEC", "abc")
    assert uni.budget_from_env() == uni.DEFAULT_BUDGET_SEC
    monkeypatch.setenv("SHADOW_CACHE_DIR", str(tmp_path / "c"))
    assert uni.default_cache_dir() == tmp_path / "c"
    monkeypatch.delenv("SHADOW_CACHE_DIR")
    monkeypatch.setenv("SETTINGS_DB_PATH", str(tmp_path / "run" / "settings.db"))
    assert uni.default_cache_dir().parts[-2:] == ("cache", "universe")
