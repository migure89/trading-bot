"""dyn_momentum: fuera ETFs de liquidez / letras / bonos cortos y volatilidad mínima."""
import json

import numpy as np
import pandas as pd
import pytest

from core import universe as uni
from shadow.runner import ShadowRunner
from strategies import dyn_momentum
from tests.test_daily_shadow import THU_10ET
from tests.test_shadow_agresiva import FakeData, _asset, _scanner

CASH_NAMES = {
    "SGOV": "iShares 0-3 Month Treasury Bond ETF",
    "BIL": "State Street SPDR Bloomberg 1-3 Month T-Bill ETF",
    "VBIL": "Vanguard 0-3 Month Treasury Bill ETF",            # solo por heurística
    "CGOV": "Corgi 0-3 Month T-Bill ETF",                       # solo por heurística
    "PMMF": "iShares Prime Money Market ETF",                    # solo por heurística
    "VCSH": "Vanguard Short-Term Corporate Bond ETF",            # solo por heurística
    "TRSY": "Xtrackers US 0-1 Year Treasury ETF",                # solo por heurística
}
NOT_CASH = {
    "AAPL": "Apple Inc. Common Stock",
    "NURE": "Nuveen Short-Term REIT ETF",                        # renta variable
    "USL": "United States 12 Month Oil Fund, LP",
    "SIXD": "AllianzIM U.S. Equity 6 Month Buffer10 Jun/Dec ETF",
    "GOVI": "Invesco Equal Weight 0-30 Year Treasury ETF",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "COWZ": "Pacer US Cash Cows 100 ETF",
    "SPLV": "Invesco S&P 500 Low Volatility ETF",
}


@pytest.mark.parametrize("symbol", ["SGOV", "BIL", "SHV", "SHY", "TFLO", "USFR", "BILS", "ICSH", "JPST",
                                    "MINT", "VGSH", "SCHO", "GBIL", "CLIP", "XHLF", "TBIL"])
def test_cash_like_list(symbol):
    assert uni.cash_like(symbol, "Acme Corp") and uni.excluded_asset(symbol, "Acme Corp", frozenset())


def test_cash_like_heuristic_and_negatives():
    for sym, name in CASH_NAMES.items():
        assert uni.cash_like(sym, name), sym
    for sym, name in NOT_CASH.items():
        assert not uni.cash_like(sym, name), sym


def test_extra_excluded_tickers_from_env(monkeypatch):
    monkeypatch.setenv("SHADOW_EXCLUDE_TICKERS", " brk.b, XYZ ,,")
    assert uni.extra_excluded_from_env() == {"BRK.B", "XYZ"}
    raw = [_asset("AAPL", "Apple Inc."), _asset("XYZ", "Block Inc."), _asset("SGOV", CASH_NAMES["SGOV"]),
           _asset("PMMF", CASH_NAMES["PMMF"]), _asset("COWZ", NOT_CASH["COWZ"])]
    assert uni.filter_assets(raw) == ["AAPL", "COWZ"]
    before = uni.filters_signature()
    monkeypatch.setenv("SHADOW_EXCLUDE_TICKERS", "XYZ")
    assert uni.filters_signature() != before


def test_filter_change_invalidates_asset_and_universe_cache(tmp_path, monkeypatch):
    data = FakeData()
    sc = _scanner(tmp_path, data, n_assets=30, top=10)
    sc.trading_client.get.return_value.append(_asset("SGOV", CASH_NAMES["SGOV"]))
    sc.closes(THU_10ET)
    cache = tmp_path / "cache"
    assets = json.loads((cache / "assets.json").read_text())
    assert "SGOV" not in assets["symbols"] and assets["filters"] == uni.filters_signature()
    # Caché antigua (sin huella de filtros, con SGOV): se vuelve a pedir la lista y a escanear.
    assets.pop("filters")
    assets["symbols"].append("SGOV")
    (cache / "assets.json").write_text(json.dumps(assets))
    universe = json.loads((cache / "universe.json").read_text())
    universe.pop("filters")
    universe["symbols"][0] = "SGOV"
    (cache / "universe.json").write_text(json.dumps(universe))
    data.calls.clear()
    panel, _ = sc.closes(THU_10ET)
    assert sc.trading_client.get.call_count == 2 and "SGOV" not in panel.columns
    assert not any("SGOV" in c for c in data.calls)
    # Cambiar la lista por entorno también invalida.
    monkeypatch.setenv("SHADOW_EXCLUDE_TICKERS", panel.columns[0])
    panel2, _ = sc.closes(THU_10ET)
    assert sc.trading_client.get.call_count == 3 and panel.columns[0] not in panel2.columns


def _panel_with_cash(n=15, days=300, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-28", periods=days, tz="UTC")
    rets = rng.normal(0.0004, 0.018, (days, n))                         # ~29 % anualizada
    panel = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx,
                         columns=[f"S{chr(65 + i)}" for i in range(n)])
    panel["CASHX"] = 100 * np.exp(np.cumsum(np.full(days, 0.00018) + rng.normal(0, 0.0003, days)))
    return panel


def test_min_vol_filter_removes_near_cash():
    panel = _panel_with_cash()
    no_filter = dyn_momentum.score(panel, min_vol=0.0).iloc[-1]
    assert no_filter.idxmax() == "CASHX"                                 # el problema de SGOV
    w = dyn_momentum.target_weights(panel)                               # 15 % por defecto
    assert w.iloc[-1]["CASHX"] == 0 and dyn_momentum.score(panel).iloc[-1].isna()["CASHX"]
    assert (w.iloc[-1] > 0).sum() == 10


@pytest.mark.parametrize("raw,expected", [("", 0.15), ("0.25", 0.25), ("0", 0.0), ("abc", 0.15),
                                          ("-1", 0.15)])
def test_min_vol_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("SHADOW_MIN_VOL", raw)
    assert dyn_momentum.min_vol_from_env() == expected


def test_min_vol_env_reaches_strategy_and_snapshot(monkeypatch, daily_store, fake_scanner):
    panel = _panel_with_cash()
    monkeypatch.setenv("SHADOW_MIN_VOL", "0")
    assert dyn_momentum.target_weights(panel).iloc[-1]["CASHX"] == pytest.approx(0.1)
    monkeypatch.setenv("SHADOW_MIN_VOL", "0.2")
    result = ShadowRunner(daily_store, None).run(daily_store.load(), now=THU_10ET, only=["dyn_momentum"])
    assert result["strategies"]["dyn_momentum"]["params"]["min_vol"] == 0.2
