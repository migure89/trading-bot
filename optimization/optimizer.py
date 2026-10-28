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
import logging
import sys

import optuna
import pandas as pd

from backtesting.engine import run_backtest
from config.settings import SettingsStore
from monitoring.logger import get_logger, install_excepthook, setup_logging

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
                      apply: bool = True) -> dict:
    """Reoptimiza cada símbolo configurado y aplica el mejor resultado global.

    Un fallo en un símbolo (datos, proveedor caído…) se registra y no detiene el resto.
    Devuelve ``{"results": [...], "errors": [{"symbol", "error"}], "skipped": [...], "applied": ...}``.
    """
    cfg = store.load()
    results, errors, skipped = [], [], []
    for symbol in cfg["symbols"]:
        try:
            df = provider.get_bars(symbol, cfg["timeframe"], days=max(cfg["lookback_days"] * 3, 60))
            if len(df) < 200:
                log.warning("datos insuficientes", extra={"symbol": symbol, "bars": len(df)})
                skipped.append({"symbol": symbol, "bars": len(df)})
                continue
            res = optimize(df, cfg["strategy_params"], n_trials, cfg["timeframe"])
        except Exception as exc:  # noqa: BLE001 - se informa y se sigue con el siguiente símbolo
            error = str(exc) or type(exc).__name__
            log.error("optimización fallida", extra={"symbol": symbol, "error": error},
                      exc_info=log.isEnabledFor(logging.DEBUG))
            errors.append({"symbol": symbol, "error": error})
            continue
        res["symbol"] = symbol
        store.save_optimization(symbol, res["best_params"], res["best_oos"], False)
        log.info("optimización completada", extra={"symbol": symbol, "improved": res["improved"],
                                                   "oos": res["best_oos"]})
        results.append(res)

    applied = None
    winners = [r for r in results if r["improved"]]
    if apply and winners:
        best = max(winners, key=lambda r: objective_score(r["best_oos"]))
        applied = apply_params(store, best["best_params"], best["symbol"], best["best_oos"])
    return {"results": results, "errors": errors, "skipped": skipped, "applied": applied}


def apply_params(store: SettingsStore, params: dict, symbol: str, metrics: dict) -> dict:
    """Guarda los parámetros ganadores (validados por el store) y los marca como aplicados."""
    store.update({"strategy_params": params})
    store.save_optimization(symbol, params, metrics, True)
    log.info("nuevos parámetros aplicados", extra={"symbol": symbol, "params": params})
    return {"symbol": symbol, "params": params}


def apply_report(store: SettingsStore, path: str) -> dict | None:
    """Aplica el mejor resultado *mejorado* de un JSON generado con ``--output`` (p.ej. el del CI)."""
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    winners = [r for r in report.get("results", []) if r.get("improved")]
    if not winners:
        log.info("el informe no tiene parámetros que mejoren a los actuales", extra={"file": path})
        return None
    best = max(winners, key=lambda r: objective_score(r["best_oos"]))
    return apply_params(store, best["best_params"], best["symbol"], best["best_oos"])


def main() -> int:
    from config.settings import resolve_data_provider
    from data.factory import DataProviderRegistry

    # Logs JSON por stderr; stdout queda solo para el resultado (JSON) de la optimización.
    setup_logging(stream=sys.stderr, force=True)
    install_excepthook("optimizer")
    ap = argparse.ArgumentParser(description="Reoptimiza la estrategia con Optuna")
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--no-apply", action="store_true", help="solo informa, no guarda en la BD")
    ap.add_argument("--provider", choices=["alpaca", "ibkr"], default=None,
                    help="proveedor de datos (por defecto el de la configuración)")
    ap.add_argument("--output", "-o", default=None,
                    help="fichero donde escribir el resultado JSON (por defecto stdout)")
    ap.add_argument("--apply-report", metavar="FICHERO", default=None,
                    help="no optimiza: aplica el mejor resultado mejorado de un JSON de --output")
    args = ap.parse_args()
    store = SettingsStore()

    if args.apply_report:
        applied = apply_report(store, args.apply_report)
        write_output({"applied": applied}, args.output)
        return 0

    name = args.provider or resolve_data_provider(store.load())
    log.info("proveedor de datos", extra={"provider": name})
    try:
        provider = DataProviderRegistry().get(name)
    except Exception as exc:  # noqa: BLE001
        log.error("proveedor de datos no disponible", extra={"provider": name, "error": str(exc)})
        return 2
    report = run_feedback_loop(store, provider, args.trials, not args.no_apply)
    write_output(report, args.output)
    if report["errors"] and not report["results"]:
        log.error("ningún símbolo se pudo optimizar", extra={"errors": report["errors"]})
        return 2
    return 0


def write_output(data: dict, path: str | None) -> None:
    text = json.dumps(data, indent=2, default=str)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
