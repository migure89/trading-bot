"""Autoaprendizaje: reoptimización de hiperparámetros con Optuna.

Flujo (feedback loop):
1. Evalúa los parámetros actuales sobre datos recientes (métricas en vivo/backtest).
2. Busca nuevos parámetros con Optuna sobre un tramo de entrenamiento.
3. Valida en un tramo fuera de muestra y sólo aplica si mejora al actual **y** supera los criterios
   de aceptación (OOS con Sharpe y retorno mínimos, mínimo de operaciones y brecha train/OOS acotada).
4. Guarda los ganadores en la BD; el bot los lee en caliente.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass

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


@dataclass(frozen=True)
class AcceptanceCriteria:
    """Umbrales para que un resultado sea *aplicable* (además de mejorar a los parámetros actuales).

    Configurables por CLI (``--min-oos-trades``…) o entorno (``OPT_MIN_OOS_TRADES``…).
    """
    min_oos_trades: int = 8           # operaciones mínimas en el tramo fuera de muestra
    min_oos_sharpe: float = 0.0       # Sharpe OOS mínimo
    min_oos_return: float = 0.0       # retorno total OOS mínimo (0.01 = 1 %)
    max_train_oos_gap: float = 2.0    # máxima diferencia train_score - score OOS (sobreajuste)

    ENV = {"min_oos_trades": "OPT_MIN_OOS_TRADES", "min_oos_sharpe": "OPT_MIN_OOS_SHARPE",
           "min_oos_return": "OPT_MIN_OOS_RETURN", "max_train_oos_gap": "OPT_MAX_TRAIN_OOS_GAP"}

    @classmethod
    def from_env(cls, **overrides) -> AcceptanceCriteria:
        values = {}
        for field, var in cls.ENV.items():
            cast = int if field == "min_oos_trades" else float
            if overrides.get(field) is not None:
                values[field] = cast(overrides[field])
            elif os.getenv(var, "").strip():
                values[field] = cast(os.environ[var])
        return cls(**values)


def evaluate(result: dict, criteria: AcceptanceCriteria | None = None) -> tuple[bool, list[str]]:
    """``(aplicable, motivos_de_rechazo)`` de un resultado de :func:`optimize`."""
    c = criteria or AcceptanceCriteria()
    oos = result["best_oos"]
    reasons = []
    if not result.get("improved"):
        reasons.append("no mejora a los parámetros actuales fuera de muestra")
    if oos["num_trades"] < c.min_oos_trades:
        reasons.append(f"operaciones OOS {oos['num_trades']} < {c.min_oos_trades}")
    if oos["sharpe"] < c.min_oos_sharpe:
        reasons.append(f"Sharpe OOS {oos['sharpe']:.2f} < {c.min_oos_sharpe:g}")
    if oos["total_return"] < c.min_oos_return:
        reasons.append(f"retorno OOS {oos['total_return']:.2%} < {c.min_oos_return:.2%}")
    train = result.get("train_score")
    if train is not None:
        gap = float(train) - objective_score(oos)
        if gap > c.max_train_oos_gap:
            reasons.append(f"brecha train/OOS {gap:.2f} > {c.max_train_oos_gap:g} (sobreajuste)")
    return not reasons, reasons


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
                      apply: bool = True, criteria: AcceptanceCriteria | None = None) -> dict:
    """Reoptimiza cada símbolo configurado y aplica el mejor resultado *aplicable*.

    Cada resultado lleva ``applicable`` y ``rejected_reason`` (lista vacía si es aplicable).
    Un fallo en un símbolo (datos, proveedor caído…) se registra y no detiene el resto.
    Devuelve ``{"results": [...], "errors": [{"symbol", "error"}], "skipped": [...], "applied": ...,
    "criteria": {...}}``.
    """
    criteria = criteria or AcceptanceCriteria.from_env()
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
        res["applicable"], res["rejected_reason"] = evaluate(res, criteria)
        store.save_optimization(symbol, res["best_params"], res["best_oos"], False)
        log.info("optimización completada", extra={"symbol": symbol, "improved": res["improved"],
                                                   "applicable": res["applicable"],
                                                   "rejected_reason": res["rejected_reason"],
                                                   "oos": res["best_oos"]})
        results.append(res)

    applied = None
    winners = [r for r in results if r["applicable"]]
    if apply and winners:
        best = max(winners, key=lambda r: objective_score(r["best_oos"]))
        applied = apply_params(store, best["best_params"], best["symbol"], best["best_oos"])
    elif apply and results:
        log.info("ningún resultado cumple los criterios: no se aplican parámetros",
                 extra={"criteria": asdict(criteria)})
    return {"results": results, "errors": errors, "skipped": skipped, "applied": applied,
            "criteria": asdict(criteria)}


def apply_params(store: SettingsStore, params: dict, symbol: str, metrics: dict) -> dict:
    """Guarda los parámetros ganadores (validados por el store) y los marca como aplicados."""
    store.update({"strategy_params": params})
    store.save_optimization(symbol, params, metrics, True)
    log.info("nuevos parámetros aplicados", extra={"symbol": symbol, "params": params})
    return {"symbol": symbol, "params": params}


def apply_report(store: SettingsStore, path: str,
                 criteria: AcceptanceCriteria | None = None) -> dict | None:
    """Aplica el mejor resultado *aplicable* de un JSON generado con ``--output`` (p.ej. el del CI).

    Los criterios se vuelven a evaluar al aplicar (no se confía en el ``applicable`` del fichero).
    """
    criteria = criteria or AcceptanceCriteria.from_env()
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    winners = []
    for r in report.get("results", []):
        ok, reasons = evaluate(r, criteria)
        if ok:
            winners.append(r)
        else:
            log.info("resultado descartado", extra={"symbol": r.get("symbol"), "rejected_reason": reasons})
    if not winners:
        log.info("el informe no tiene parámetros aplicables", extra={"file": path})
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
                    help="no optimiza: aplica el mejor resultado aplicable de un JSON de --output")
    d = AcceptanceCriteria()
    ap.add_argument("--min-oos-trades", type=int, default=None,
                    help=f"operaciones OOS mínimas (OPT_MIN_OOS_TRADES, por defecto {d.min_oos_trades})")
    ap.add_argument("--min-oos-sharpe", type=float, default=None,
                    help=f"Sharpe OOS mínimo (OPT_MIN_OOS_SHARPE, por defecto {d.min_oos_sharpe:g})")
    ap.add_argument("--min-oos-return", type=float, default=None,
                    help=f"retorno OOS mínimo, 0.01 = 1 %% (OPT_MIN_OOS_RETURN, def. {d.min_oos_return:g})")
    ap.add_argument("--max-train-oos-gap", type=float, default=None,
                    help=f"brecha máx. train/OOS (OPT_MAX_TRAIN_OOS_GAP, def. {d.max_train_oos_gap:g})")
    args = ap.parse_args()
    store = SettingsStore()
    criteria = AcceptanceCriteria.from_env(
        min_oos_trades=args.min_oos_trades, min_oos_sharpe=args.min_oos_sharpe,
        min_oos_return=args.min_oos_return, max_train_oos_gap=args.max_train_oos_gap)

    if args.apply_report:
        applied = apply_report(store, args.apply_report, criteria)
        write_output({"applied": applied}, args.output)
        return 0

    name = args.provider or resolve_data_provider(store.load())
    log.info("proveedor de datos", extra={"provider": name})
    try:
        provider = DataProviderRegistry().get(name)
    except Exception as exc:  # noqa: BLE001
        log.error("proveedor de datos no disponible", extra={"provider": name, "error": str(exc)})
        return 2
    report = run_feedback_loop(store, provider, args.trials, not args.no_apply, criteria)
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
