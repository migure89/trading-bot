# 📈 Trading Bot — Plataforma de Trading Cuantitativo con Alpaca

Plataforma modular en Python para trading automatizado sobre **Alpaca Markets** (`alpaca-py`):
bot con configuración en caliente, backtesting vectorizado, autoaprendizaje con **Optuna**,
API **FastAPI**, dashboard **Streamlit** y monitorización con logs JSON + **Prometheus**.

> ⚠️ Software educativo. Operar en mercados conlleva riesgo de pérdida. Empieza siempre en **paper**.

## Arquitectura

```
          ┌───────────── config/settings.db (SQLite, hot-reload) ─────────────┐
          │                      ▲                 ▲                           │
   main.py (bot) ──── lee cada iteración     api/ (FastAPI) ◄──── dashboard/ (Streamlit)
     │   │                                         ▲
     │   └─ execution/ (TradingClient paper/live)  │
     └───── data/ (Stock/Crypto HistoricalDataClient)
   optimization/ (Optuna) ── backtesting/ ── strategies/ ──► guarda parámetros ganadores en la BD
   monitoring/ ── logs JSON + exporter Prometheus (:9100)
```

| Carpeta | Contenido |
|---|---|
| `config/` | `schema.sql` y `settings.py` (store SQLite; `settings.db` se crea al arrancar) |
| `data/` | Descarga de barras OHLCV de acciones y cripto |
| `strategies/` | Indicadores (EMA, SMA, RSI, MACD, Bollinger, ATR) y estrategia EMA+RSI |
| `execution/` | `AlpacaBroker`: TradingClient dinámico, sizing por ATR, kill switch |
| `backtesting/` | Motor vectorizado y métricas (Sharpe, Profit Factor, Max DD, Win Rate) |
| `optimization/` | Optuna + feedback loop con validación fuera de muestra |
| `api/` | FastAPI: `/status`, `/config`, `/toggle-bot`, `/metrics`, `/close-position` |
| `dashboard/` | Streamlit con control Paper/Live, métricas, gráficas Plotly y parámetros IA |
| `monitoring/` | Logger JSON y exporter Prometheus |
| `tests/` | pytest con mocks de Alpaca (no necesitan credenciales) |

## 1. Credenciales

1. Crea una cuenta en [Alpaca](https://alpaca.markets) y genera claves de **Paper Trading**.
2. `cp .env.example .env` y rellena:
   ```env
   APCA_API_KEY_ID=...
   APCA_API_SECRET_KEY=...
   CONFIRM_LIVE_TRADING=false
   ```
3. Las claves **nunca** se escriben en código. En GitHub, añádelas como *Secrets*
   (`Settings → Secrets and variables → Actions`) con los mismos nombres.

> Las claves de paper y live son distintas en Alpaca: para pasar a live cambia las claves en `.env`
> **y** pon `CONFIRM_LIVE_TRADING=true`. Sin esa variable, la API devuelve 403 y el bot no opera en real.

## 2. Despliegue con Docker (recomendado)

```bash
cp .env.example .env      # edita tus claves
docker compose up -d --build
```

| Servicio | URL |
|---|---|
| Dashboard | http://localhost:8501 |
| API (docs Swagger) | http://localhost:8000/docs |
| Métricas Prometheus | http://localhost:9100/metrics |

Los tres servicios comparten la BD en el volumen `botdata`. El bot arranca **pausado**:
actívalo desde el dashboard o con `curl -X POST localhost:8000/toggle-bot -H 'Content-Type: application/json' -d '{"enabled": true}'`.

## 3. Ejecución local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --once                       # una iteración de prueba
python main.py                              # bucle continuo
uvicorn api.main:app --reload               # API
API_URL=http://localhost:8000 streamlit run dashboard/app.py
python -m optimization.optimizer --trials 100   # reoptimización manual
pytest -q && ruff check .
```

## 4. Configuración dinámica (hot-reload)

Claves en la tabla `settings`: `mode`, `bot_enabled`, `symbols` (p.ej. `["AAPL","BTC/USD"]`),
`risk_pct`, `timeframe`, `loop_interval_sec`, `lookback_days`, `strategy_params`.
El bot las relee en **cada iteración**; si cambia `mode`, se recrea el `TradingClient` con `paper=True/False`.

```bash
curl -X POST localhost:8000/config -H 'Content-Type: application/json' \
     -d '{"risk_pct": 0.5, "symbols": ["AAPL", "BTC/USD"]}'
```

## 5. Estrategia y riesgo

- **Entrada:** EMA rápida > EMA lenta, RSI < sobrecompra y cierre < Bollinger superior.
- **Salida:** cruce bajista de EMAs o RSI > sobrecompra.
- **Tamaño:** `(equity × risk_pct%) / (ATR × atr_stop_mult)`, limitado al equity.

## 6. Autoaprendizaje

`optimization/optimizer.py` divide el histórico reciente en entrenamiento (70 %) y validación (30 %),
busca parámetros con Optuna (TPE) maximizando una puntuación compuesta (Sharpe + Profit Factor +
Win Rate, penalizando drawdowns > 25 %) y **solo aplica** los nuevos si superan a los actuales fuera de
muestra. Todo queda en `optimization_runs`. El workflow `backtest_scheduled.yml` lo ejecuta en días
laborables y publica los resultados como artefacto.

## 7. API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/status` | Balance, PnL diario y flotante, posiciones, modo y estado |
| GET/POST | `/config` | Lee / actualiza configuración (live exige confirmación) |
| POST | `/toggle-bot` | `{"enabled": bool?, "close_positions": bool}` — kill switch |
| POST | `/close-position` | Cierra una posición concreta |
| GET | `/metrics` | Métricas, operaciones e historial de optimización |

## 8. Gestión del proyecto

Las issues y épicas están en [`GITHUB_PROJECT_SETUP.md`](GITHUB_PROJECT_SETUP.md), con un script
`gh` para crearlas de golpe.
