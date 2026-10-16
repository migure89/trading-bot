"""Autoaprendizaje: reoptimización de hiperparámetros con Optuna.

Flujo (feedback loop):
1. Evalúa los parámetros actuales sobre datos recientes (métricas en vivo/backtest).
2. Busca nuevos parámetros con Optuna sobre un tramo de entrenamiento.
3. Valida en un tramo fuera de muestra y sólo aplica si mejora al actual.
4. Guarda los ganadores en la BD; el bot los lee en caliente.
"""
from __future__ import annotations

import argparse
import json

import optuna
import pandas as pd

from backtesting.engine import run_backtest
from config.settings import SettingsStore
from monitoring.logger import get_logger

log = get_logger("optimizer")
optuna.logging.set_verbosity(optuna.logging.WARNING)


def objective_score(metrics: dict, max_dd_limit: float = -0.25) -> float:
    """Puntuación compuesta: Sharpe penalizado por drawdown, PF y win rate."""
    if metrics["num_trades"] < 3:
        return -10.0
    score = metrics["sharpe"]
    score += 0.5 * min(metrics["profit_factor"], 5.0)
    score += 1.0 * metrics["win_rate"]
    if metrics["max_drawdown"] < max_dd_limit:
        score -= 5.0 * (max_dd_limit - metrics["max_drawdown"]) * 10
    return float(score)


def suggest_params(trial: optuna.Trial, base: dict) -> dict:
    fast = trial.suggest_int("ema_fast", 5, 30)
    slow = trial.suggest_int("ema_slow", fast + 5, 100)
    return {
        **base,
        "ema_fast": fast,
        "ema_slow": slow,
        "rsi_period": trial.suggest_int("rsi_period", 7, 28),
        "rsi_overbought": trial.suggest_int("rsi_overbought", 60, 85),
        "rsi_oversold": trial.suggest_int("rsi_oversold", 15, 40),
        "atr_period": trial.suggest_int("atr_period", 7, 28),
        "atr_stop_mult": trial.suggest_float("atr_stop_mult", 1.0, 4.0, step=0.25),
        "bb_period": trial.suggest_int("bb_period", 10, 40),
        "bb_std": trial.suggest_float("bb_std", 1.5, 3.0, step=0.25),
    }


def optimize(df: pd.DataFrame, base_params: dict, n_trials: int = 50,
             timeframe: str = "1Hour", train_frac: float = 0.7, seed: int = 42) -> dict:
    split = int(len(df) * train_frac)
    train, test = df.iloc[:split], df.iloc[split:]

    def objective(trial):
        params = suggest_params(trial, base_params)
        return objective_score(run_backtest(train, params, timeframe=timeframe).metrics)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best = {**base_params, **study.best_params}

    current_oos = run_backtest(test, base_params, timeframe=timeframe).metrics
    best_oos = run_backtest(test, best, timeframe=timeframe).metrics
    improved = objective_score(best_oos) > objective_score(current_oos)
    return {"best_params": best, "best_oos": best_oos, "current_oos": current_oos,
            "improved": improved, "train_score": study.best_value}


def run_feedback_loop(store: SettingsStore, provider, n_trials: int = 50,
                      apply: bool = True) -> list[dict]:
    """Reoptimiza cada símbolo configurado y aplica el mejor resultado global."""
    cfg = store.load()
    results = []
    for symbol in cfg["symbols"]:
        df = provider.get_bars(symbol, cfg["timeframe"], days=max(cfg["lookback_days"] * 3, 60))
        if len(df) < 200:
            log.warning("datos insuficientes", extra={"symbol": symbol, "bars": len(df)})
            continue
        res = optimize(df, cfg["strategy_params"], n_trials, cfg["timeframe"])
        res["symbol"] = symbol
        store.save_optimization(symbol, res["best_params"], res["best_oos"], False)
        log.info("optimización completada", extra={"symbol": symbol, "improved": res["improved"],
                                                   "oos": res["best_oos"]})
        results.append(res)

    winners = [r for r in results if r["improved"]]
    if apply and winners:
        best = max(winners, key=lambda r: objective_score(r["best_oos"]))
        store.update({"strategy_params": best["best_params"]})
        store.save_optimization(best["symbol"], best["best_params"], best["best_oos"], True)
        log.info("nuevos parámetros aplicados", extra={"params": best["best_params"]})
    return results


def main() -> None:
    from dotenv import load_dotenv

    from data.alpaca_data import AlpacaDataProvider

    load_dotenv()
    ap = argparse.ArgumentParser(description="Reoptimiza la estrategia con Optuna")
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--no-apply", action="store_true", help="solo informa, no guarda en la BD")
    args = ap.parse_args()
    res = run_feedback_loop(SettingsStore(), AlpacaDataProvider(), args.trials, not args.no_apply)
    print(json.dumps([{k: v for k, v in r.items()} for r in res], indent=2, default=str))


if __name__ == "__main__":
    main()
