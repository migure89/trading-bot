import json
import sys

import pandas as pd
import pytest

from backtesting.engine import run_backtest
from backtesting.metrics import max_drawdown, profit_factor, sharpe_ratio, win_rate
from config.settings import DEFAULT_SETTINGS, SettingsStore
from optimization.optimizer import optimize, run_feedback_loop

P = DEFAULT_SETTINGS["strategy_params"]


def test_metrics_basic():
    assert max_drawdown(pd.Series([100, 120, 90, 130])) == -0.25
    pnl = pd.Series([1.0, -0.5, 2.0])
    assert profit_factor(pnl) == 6.0
    assert abs(win_rate(pnl) - 2 / 3) < 1e-9
    assert sharpe_ratio(pd.Series([0.0, 0.0])) == 0.0


def test_backtest_runs(ohlcv):
    res = run_backtest(ohlcv, P)
    assert len(res.equity) == len(ohlcv)
    for k in ("sharpe", "profit_factor", "max_drawdown", "win_rate", "num_trades"):
        assert k in res.metrics
    assert res.metrics["num_trades"] == len(res.trades)


def test_optimize_small(ohlcv):
    out = optimize(ohlcv, P, n_trials=5)
    assert "ema_fast" in out["best_params"]
    assert out["best_params"]["ema_slow"] > out["best_params"]["ema_fast"]


def test_feedback_loop_saves(store, provider):
    store.update({"symbols": ["AAPL"]})
    report = run_feedback_loop(store, provider, n_trials=5)
    assert len(report["results"]) == 1 and report["errors"] == []
    assert store.optimization_history()


# --- Issue #10: un símbolo que falla no detiene la optimización -----------------------------------
class FlakyProvider:
    """Proveedor fake: AAPL falla (sin claves), el resto devuelve datos."""

    def __init__(self, ohlcv, failing=("AAPL",)):
        self.ohlcv, self.failing = ohlcv, set(failing)

    def get_bars(self, symbol, timeframe="1Hour", days=30, end=None):
        if symbol in self.failing:
            from data.base import DataProviderError

            raise DataProviderError("Los datos de acciones de Alpaca requieren APCA_API_KEY_ID")
        return self.ohlcv


def test_feedback_loop_continues_after_symbol_error(store, ohlcv, caplog):
    store.update({"symbols": ["BTC/USD", "AAPL", "ETH/USD"]})
    report = run_feedback_loop(store, FlakyProvider(ohlcv), n_trials=3, apply=False)
    assert [r["symbol"] for r in report["results"]] == ["BTC/USD", "ETH/USD"]
    assert report["errors"] == [{"symbol": "AAPL",
                                 "error": "Los datos de acciones de Alpaca requieren APCA_API_KEY_ID"}]
    failed = [r for r in caplog.records if r.getMessage() == "optimización fallida"]
    assert len(failed) == 1 and failed[0].symbol == "AAPL" and not failed[0].exc_info


def test_alpaca_stock_data_without_keys_is_clear(monkeypatch):
    from data.alpaca_data import AlpacaDataProvider
    from data.base import DataProviderError

    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(DataProviderError, match="APCA_API_KEY_ID"):
        AlpacaDataProvider().get_bars("AAPL")


def _main(monkeypatch, tmp_path, argv, provider):
    import optimization.optimizer as opt

    monkeypatch.setenv("SETTINGS_DB_PATH", str(tmp_path / "settings.db"))
    monkeypatch.setattr(sys, "argv", ["optimizer", *argv])
    monkeypatch.setattr(opt, "setup_logging", lambda **kw: None)
    monkeypatch.setattr(opt, "install_excepthook", lambda *a: None)
    monkeypatch.setattr("data.factory.DataProviderRegistry.get", lambda self, name: provider)
    return opt.main()


def test_main_exit_code_when_everything_fails(monkeypatch, tmp_path, ohlcv, capsys):
    SettingsStore(tmp_path / "settings.db").update({"symbols": ["AAPL"]})
    code = _main(monkeypatch, tmp_path, ["--trials", "2", "--no-apply"], FlakyProvider(ohlcv))
    assert code == 2
    out = json.loads(capsys.readouterr().out)          # stdout es un único JSON válido
    assert out["results"] == [] and out["errors"][0]["symbol"] == "AAPL"


# --- Issue #11: --output y aplicar un informe ------------------------------------------------------
def test_main_output_file_and_apply_report(monkeypatch, tmp_path, ohlcv, capsys):
    store = SettingsStore(tmp_path / "settings.db")
    store.update({"symbols": ["BTC/USD"]})
    out_file = tmp_path / "optimization.json"
    code = _main(monkeypatch, tmp_path, ["--trials", "3", "--no-apply", "-o", str(out_file)],
                 FlakyProvider(ohlcv))
    assert code == 0 and capsys.readouterr().out == ""
    report = json.loads(out_file.read_text())
    assert report["results"][0]["symbol"] == "BTC/USD" and report["applied"] is None

    # Fuerza un ganador y lo aplica sobre la BD con --apply-report.
    winner = {**report["results"][0]["best_params"], "ema_fast": 7, "ema_slow": 31}
    report["results"][0].update(improved=True, best_params=winner)
    out_file.write_text(json.dumps(report))
    assert _main(monkeypatch, tmp_path, ["--apply-report", str(out_file)], None) == 0
    params = SettingsStore(tmp_path / "settings.db").load()["strategy_params"]
    assert params["ema_fast"] == 7 and params["ema_slow"] == 31
    assert json.loads(capsys.readouterr().out)["applied"]["symbol"] == "BTC/USD"
