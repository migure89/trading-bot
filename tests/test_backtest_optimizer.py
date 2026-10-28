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


GOOD_OOS = {"total_return": 0.02, "sharpe": 1.2, "max_drawdown": -0.03, "win_rate": 0.6,
            "profit_factor": 1.5, "num_trades": 10}


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
    report["results"][0].update(improved=True, best_params=winner, train_score=3.0, best_oos=GOOD_OOS)
    out_file.write_text(json.dumps(report))
    assert _main(monkeypatch, tmp_path, ["--apply-report", str(out_file)], None) == 0
    params = SettingsStore(tmp_path / "settings.db").load()["strategy_params"]
    assert params["ema_fast"] == 7 and params["ema_slow"] == 31
    assert json.loads(capsys.readouterr().out)["applied"]["symbol"] == "BTC/USD"


# --- Issue #18: solo se aplican resultados con OOS positivo, suficientes operaciones y sin sobreajuste
from optimization.optimizer import AcceptanceCriteria, apply_report, evaluate  # noqa: E402

# Datos reales observados (Alpaca paper, 28-10-2026): mejores que los actuales pero con OOS negativo.
AAPL_REAL = {"improved": True, "train_score": 3.517, "best_oos": {
    "total_return": -0.00496, "sharpe": -0.243, "max_drawdown": -0.0297, "win_rate": 0.2,
    "profit_factor": 0.84, "num_trades": 5}}
MSFT_REAL = {"improved": True, "train_score": 3.442, "best_oos": {
    "total_return": -0.00982, "sharpe": -0.451, "max_drawdown": -0.0596, "win_rate": 0.667,
    "profit_factor": 0.921, "num_trades": 9}}


def test_negative_oos_is_not_applicable():
    ok, reasons = evaluate(MSFT_REAL)
    assert not ok and any("Sharpe OOS" in r for r in reasons) and any("retorno OOS" in r for r in reasons)
    ok, reasons = evaluate(AAPL_REAL)
    assert not ok and any("operaciones OOS 5 < 8" == r for r in reasons)


def test_few_trades_not_applicable():
    ok, reasons = evaluate({"improved": True, "train_score": 2.0, "best_oos": {**GOOD_OOS, "num_trades": 4}})
    assert not ok and reasons == ["operaciones OOS 4 < 8"]
    assert evaluate({"improved": True, "train_score": 2.0, "best_oos": {**GOOD_OOS, "num_trades": 4}},
                    AcceptanceCriteria(min_oos_trades=3))[0]


def test_train_oos_gap_limits_overfitting():
    res = {"improved": True, "train_score": 9.0, "best_oos": GOOD_OOS}
    ok, reasons = evaluate(res)
    assert not ok and "sobreajuste" in reasons[0]
    assert evaluate(res, AcceptanceCriteria(max_train_oos_gap=10))[0]


def test_positive_oos_with_improvement_is_applicable():
    assert evaluate({"improved": True, "train_score": 3.0, "best_oos": GOOD_OOS}) == (True, [])
    ok, reasons = evaluate({"improved": False, "train_score": 3.0, "best_oos": GOOD_OOS})
    assert not ok and reasons == ["no mejora a los parámetros actuales fuera de muestra"]


def test_criteria_from_env_and_overrides(monkeypatch):
    monkeypatch.setenv("OPT_MIN_OOS_TRADES", "12")
    monkeypatch.setenv("OPT_MIN_OOS_SHARPE", "0.5")
    c = AcceptanceCriteria.from_env(max_train_oos_gap=1.5)
    assert (c.min_oos_trades, c.min_oos_sharpe, c.min_oos_return, c.max_train_oos_gap) == (12, 0.5, 0.0, 1.5)
    assert AcceptanceCriteria.from_env(min_oos_trades=3).min_oos_trades == 3


def test_feedback_loop_does_not_apply_negative_oos(store, provider, monkeypatch):
    import optimization.optimizer as opt
    store.update({"symbols": ["AAPL", "MSFT"]})
    before = store.load()["strategy_params"]
    fake = iter([dict(AAPL_REAL, best_params={**before, "ema_fast": 7}, current_oos=AAPL_REAL["best_oos"]),
                 dict(MSFT_REAL, best_params={**before, "ema_fast": 8}, current_oos=MSFT_REAL["best_oos"])])
    monkeypatch.setattr(opt, "optimize", lambda *a, **k: next(fake))
    report = opt.run_feedback_loop(store, provider, n_trials=1, apply=True)
    assert report["applied"] is None and store.load()["strategy_params"] == before
    assert [r["applicable"] for r in report["results"]] == [False, False]
    assert all(r["rejected_reason"] for r in report["results"])
    assert report["criteria"]["min_oos_trades"] == 8


def test_apply_report_reevaluates_criteria(store, tmp_path):
    before = store.load()["strategy_params"]
    path = tmp_path / "r.json"
    bad = dict(MSFT_REAL, symbol="MSFT", applicable=True, best_params={**before, "ema_fast": 9})
    path.write_text(json.dumps({"results": [bad]}))
    assert apply_report(store, str(path)) is None and store.load()["strategy_params"] == before
