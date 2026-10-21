# 📈 Trading Bot — Plataforma de Trading Cuantitativo (Alpaca + Interactive Brokers)

Plataforma modular en Python para trading automatizado sobre **Alpaca Markets** (`alpaca-py`) e
**Interactive Brokers** (`ib_async` contra TWS / IB Gateway), con el broker y el proveedor de datos
**seleccionables en caliente** desde la configuración: bot con configuración en caliente, backtesting vectorizado, autoaprendizaje con **Optuna**,
API **FastAPI**, dashboard **Streamlit** y monitorización con logs JSON + **Prometheus**.

> ⚠️ Software educativo. Operar en mercados conlleva riesgo de pérdida. Empieza siempre en **paper**.

## Arquitectura

```
          ┌───────────── config/settings.db (SQLite, hot-reload) ─────────────┐
          │                      ▲                 ▲                           │
   main.py (bot) ──── lee cada iteración     api/ (FastAPI) ◄──── dashboard/ (Streamlit)
     │   │                                         ▲
     │   └─ execution/ BrokerRegistry ─┬─ AlpacaBroker (TradingClient paper/live)
     │                                 └─ IBKRBroker ──┐
     └───── data/ DataProviderRegistry ┬─ Alpaca      │  connectors/ibkr.py (ib_async)
                                       └─ IBKR ───────┴─► TWS / IB Gateway (7497/7496 · 4002/4001)
   optimization/ (Optuna) ── backtesting/ ── strategies/ ──► guarda parámetros ganadores en la BD
   monitoring/ ── logs JSON + exporter Prometheus (:9100)
```

| Carpeta | Contenido |
|---|---|
| `config/` | `schema.sql` y `settings.py` (store SQLite; `settings.db` se crea al arrancar) |
| `core/` | Registro genérico para elegir implementaciones en caliente |
| `connectors/` | `ibkr.py`: conexión `ib_async` (hilo propio, paper/live por puerto, validación de cuenta) |
| `data/` | Interfaz `DataProvider` y barras OHLCV de Alpaca (`alpaca_data.py`) e IBKR (`ibkr_data.py`) |
| `strategies/` | Indicadores (EMA, SMA, RSI, MACD, Bollinger, ATR) y estrategia EMA+RSI |
| `execution/` | Interfaz `BaseBroker` (sizing por ATR común), `AlpacaBroker`, `IBKRBroker` y `BrokerRegistry` |
| `backtesting/` | Motor vectorizado y métricas (Sharpe, Profit Factor, Max DD, Win Rate) |
| `optimization/` | Optuna + feedback loop con validación fuera de muestra |
| `api/` | FastAPI: `/status`, `/brokers`, `/config`, `/toggle-bot`, `/cancel-orders`, `/close-position`, `/metrics` |
| `dashboard/` | Streamlit con selector de broker, estado de conexión, control Paper/Live, métricas y parámetros IA |
| `monitoring/` | Logger JSON y exporter Prometheus |
| `tests/` | pytest con mocks de Alpaca e IBKR (no necesitan credenciales, TWS ni Gateway) |

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
> La misma regla se aplica a Interactive Brokers (ver sección 9).

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

Los tres servicios comparten la BD en el volumen `botdata`. Para Interactive Brokers con el
contenedor de IB Gateway incluido: `docker compose --profile ibkr up -d --build` (ver sección 9). El bot arranca **pausado**:
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

Claves en la tabla `settings`: `mode`, `broker` (`alpaca` | `ibkr`), `data_provider`
(`auto` = el del broker, `alpaca` o `ibkr`), `bot_enabled`, `symbols` (p.ej. `["AAPL","BTC/USD"]`),
`risk_pct`, `timeframe`, `loop_interval_sec`, `lookback_days`, `strategy_params`.
El bot las relee en **cada iteración**; si cambia `mode`, se recrea el `TradingClient` de Alpaca con
`paper=True/False` o se reconecta a IBKR en el puerto paper/live; si cambia `broker`, la siguiente
iteración ya opera con el nuevo broker. Las BDs antiguas se migran solas al arrancar.

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
muestra. Todo queda en `optimization_runs`. Usa el proveedor de datos de la configuración o el que
indiques: `python -m optimization.optimizer --provider ibkr` (con un `IBKR_CLIENT_ID` libre, p.ej. 3). El workflow `backtest_scheduled.yml` lo ejecuta en días
laborables y publica los resultados como artefacto.

## 7. API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/status` | Broker activo y estado de conexión, balance, PnL diario y flotante, posiciones, modo |
| GET | `/brokers` | Brokers disponibles, activo, proveedor de datos y conexión de cada uno |
| GET/POST | `/config` | Lee / actualiza configuración (live exige confirmación; cambiar de broker en live exige `confirm_live: true`, si no 409) |
| POST | `/toggle-bot` | `{"enabled": bool?, "close_positions": bool}` — kill switch del broker activo |
| POST | `/cancel-orders` | Cancela todas las órdenes abiertas del broker activo |
| POST | `/close-position` | Cierra una posición concreta |
| GET | `/metrics` | Métricas, operaciones e historial de optimización |

## 8. Gestión del proyecto

Las issues y épicas están en [`GITHUB_PROJECT_SETUP.md`](GITHUB_PROJECT_SETUP.md), con un script
`gh` para crearlas de golpe.

## 9. Interactive Brokers (IBKR)

La integración usa [`ib_async`](https://github.com/ib-api-reloaded/ib_async), el sucesor mantenido de
`ib_insync`, contra **TWS** o **IB Gateway** (la API de IBKR siempre pasa por una de las dos aplicaciones).

### 9.1 Requisitos

1. Cuenta IBKR con **Paper Trading** activado (Client Portal → *Configuración → Cuenta de paper trading*).
   La cuenta paper tiene usuario y contraseña propios y su número empieza por `DU`.
2. Datos de mercado: la cuenta paper hereda las suscripciones de la cuenta real (*Configuración →
   Market Data Subscriptions*, compartir con paper). Sin ellas, los históricos de acciones fallan o llegan diferidos.
3. TWS o IB Gateway con la API activada: *Configure → API → Settings* →
   ✅ *Enable ActiveX and Socket Clients*, ❌ *Read-Only API* (si quieres enviar órdenes),
   puerto según la tabla y `127.0.0.1` (o la IP de Docker) en *Trusted IPs*.

| Aplicación | Paper | Live |
|---|---|---|
| TWS | 7497 | 7496 |
| IB Gateway | 4002 | 4001 |
| Contenedor `ib-gateway` (red Docker) | 4004 | 4003 |

### 9.2 Seguridad paper/live

- El modo lo decide el **puerto**: `IBKR_PORT_PAPER` / `IBKR_PORT_LIVE`. Se rechaza un puerto live
  conocido en modo paper y viceversa.
- Tras conectar se comprueba la **cuenta**: en paper debe empezar por `D` (`DU…`); en live, no.
  Si no cuadra, se desconecta y se muestra el error en el dashboard.
- `live` exige `CONFIRM_LIVE_TRADING=true` igual que con Alpaca, y el dashboard pide escribir
  CONFIRMAR para cambiar de modo **o de broker** estando en live.
- `IBKR_READONLY=true` conecta en solo lectura (útil para probar datos sin poder enviar órdenes).

### 9.3 Configuración

```env
IBKR_HOST=127.0.0.1
IBKR_PORT_PAPER=4002      # 7497 si usas TWS
IBKR_PORT_LIVE=4001       # 7496 si usas TWS
IBKR_CLIENT_ID=1          # distinto por proceso: bot=1, API=2, optimizador=3
IBKR_ACCOUNT=             # opcional, si tu login gestiona varias cuentas
```

Después elige el broker desde el dashboard (*Broker activo → Interactive Brokers*) o con la API:

```bash
curl -X POST localhost:8000/config -H 'Content-Type: application/json' -d '{"broker": "ibkr"}'
curl localhost:8000/brokers      # estado de conexión
```

`data_provider` permite combinar, p.ej., operar en IBKR con datos de Alpaca (`"data_provider": "alpaca"`).

### 9.4 Docker con IB Gateway (opcional)

El servicio `ib-gateway` (imagen [`gnzsnz/ib-gateway`](https://github.com/gnzsnz/ib-gateway-docker)) solo
se levanta con el perfil `ibkr`:

```bash
# .env: TWS_USERID / TWS_PASSWORD (usuario de la cuenta PAPER), TRADING_MODE=paper
docker compose --profile ibkr up -d --build
```

- Bot y API se conectan a `ib-gateway:4004` (paper) / `ib-gateway:4003` (live), con client id 1 y 2.
- En el host, el Gateway queda en `127.0.0.1:4002` (paper), `127.0.0.1:4001` (live) y VNC en `5900`
  si defines `VNC_SERVER_PASSWORD`.
- Con 2FA (cuentas reales) tendrás que aprobar el login en la app IBKR Mobile; IB Gateway se reinicia a
  diario (`AUTO_RESTART_TIME`) y el bot se reconecta solo en la siguiente iteración.
- Si prefieres TWS en tu ordenador: `IBKR_DOCKER_HOST=host.docker.internal`, `IBKR_DOCKER_PORT_PAPER=7497`,
  `IBKR_DOCKER_PORT_LIVE=7496` y añade la IP de Docker a *Trusted IPs*.

### 9.5 Detalles y limitaciones

- Símbolos: `AAPL` → acción `SMART/USD`; `BTC/USD` → cripto `PAXOS` (configurable con `IBKR_*_EXCHANGE`).
- Históricos con `reqHistoricalData` troceado (p.ej. 50 días por petición en barras de 1 h) y pausa de
  `IBKR_HIST_PACING_SEC` entre bloques para respetar el *pacing* de IBKR (~60 peticiones / 10 min).
- Órdenes a mercado: acciones `DAY`, cripto `IOC`. Acciones en cantidades enteras.
- El kill switch usa `reqGlobalCancel`, que cancela **todas** las órdenes abiertas de la cuenta (también
  las manuales), y después liquida todas las posiciones a mercado.
- El PnL diario llega por suscripción `reqPnL`: la primera lectura puede ser 0.
- Al cambiar de broker, el bot deja de gestionar las posiciones abiertas en el anterior.
- El workflow programado de GitHub no puede conectar con TWS/Gateway: sigue usando Alpaca.
