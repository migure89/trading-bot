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

4. Define `API_TOKEN` (y, si vas a exponer el dashboard, `DASHBOARD_PASSWORD`); ver la sección 2.1.

> Las claves de paper y live son distintas en Alpaca: para pasar a live cambia las claves en `.env`
> **y** pon `CONFIRM_LIVE_TRADING=true`. Sin esa variable, la API devuelve 403 y el bot no opera en real.
> La misma regla se aplica a Interactive Brokers (ver sección 9).

> **Fuera de Docker** el `.env` se carga automáticamente al importar la plataforma (`core/env.py`),
> desde el directorio actual o la raíz del proyecto, antes de leer `SETTINGS_DB_PATH`, `LOG_LEVEL`
> o `LOG_FILE`. Las variables ya exportadas en la shell tienen prioridad; `ENV_FILE=/ruta/.env`
> permite usar otro fichero.

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

Los tres servicios comparten la BD en el volumen `botdata`. El `.env` **no** se copia en la imagen
(`.dockerignore`); las variables se inyectan al arrancar con `env_file`, y el CI lo comprueba en cada PR. Para Interactive Brokers con el
contenedor de IB Gateway incluido: `docker compose --profile ibkr up -d --build` (ver sección 9). El bot arranca **pausado**:
actívalo desde el dashboard o con `curl -X POST localhost:8000/toggle-bot -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' -d '{"enabled": true}'`.

### 2.1 Seguridad (API_TOKEN y puertos)

La API de control puede liquidar posiciones, cambiar de broker o de riesgo, así que:

- **`API_TOKEN`**: si está definido en `.env`, todas las rutas salvo `/health` exigen
  `Authorization: Bearer <token>` o `X-API-Key: <token>` (si no, 401). El dashboard lo envía solo.
  Si no lo defines, la API arranca **sin autenticación** y lo avisa en el log con un `WARNING`.
  Genera uno con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **`DASHBOARD_PASSWORD`**: contraseña opcional para abrir el dashboard (Streamlit no tiene
  autenticación propia). Para acceso remoto, mejor un proxy con TLS y autenticación o una VPN.
- **Puertos**: `docker compose` publica 8000, 8501 y 9100 **solo en `127.0.0.1`** del host
  (`BIND_ADDR`). En local, el exporter escucha en `METRICS_ADDR` (por defecto `127.0.0.1`).
- `/status` y `/brokers` enmascaran el número de cuenta de IBKR (`DU****567`).

```bash
curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/status
```

## 3. Ejecución local

Versión de Python: **3.12** (la misma que el CI y la imagen Docker). `requirements.txt` contiene las
dependencias de ejecución con rangos acotados por versión mayor y `requirements-dev.txt` añade las de
tests y lint; Dependabot (`.github/dependabot.yml`) propone las actualizaciones.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt         # o requirements.txt si solo vas a ejecutar
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
`risk_pct`, `timeframe`, `loop_interval_sec`, `lookback_days`, `regular_hours_only`, `strategy_params`.
El bot las relee en **cada iteración**; si cambia `mode`, se recrea el `TradingClient` de Alpaca con
`paper=True/False` o se reconecta a IBKR en el puerto paper/live; si cambia `broker`, la siguiente
iteración ya opera con el nuevo broker. Las BDs antiguas se migran solas al arrancar.

```bash
curl -X POST localhost:8000/config -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"risk_pct": 0.5, "symbols": ["AAPL", "BTC/USD"]}'
```

### 4.1 Sesión regular (`regular_hours_only`)

Por defecto (`true`) las barras intradía de **acciones** usadas por el bot, el backtest y el optimizador
son solo de la **sesión regular** (09:30–16:00 ET), igual que `useRTH` en IBKR. El pre-market y el
after-hours tienen poco volumen y distorsionan las señales, el ATR y el Sharpe.

- **Alpaca:** se filtra con el calendario de mercado de Alpaca (festivos y cierres anticipados); si no
  está disponible se usa 09:30–16:00 ET de lunes a viernes y se registra un único WARNING. Las barras de
  `1Hour` se construyen a partir de barras de 30 min alineadas con la apertura (09:30, 10:30 … 15:30–16:00:
  7 barras por día, como en IBKR). Cripto y `1Day` no se filtran.
- **IBKR:** el valor se pasa como `useRTH` (`IBKR_USE_RTH` solo se usa cuando no llega la opción).
- `"regular_hours_only": false` vuelve al horario extendido. `ALPACA_REGULAR_HOURS_ONLY` fija el valor por
  defecto del proveedor de Alpaca cuando se usa fuera del bot.
- **Sharpe:** se anualiza con las barras reales (mediana de barras por día × 252 días, o 365 si hay
  barras en fin de semana, como en cripto): 7 × 252 en `1Hour` con sesión regular, ~16 × 252 con horario
  extendido. Antes se usaba siempre 7 × 252.

## 5. Estrategia y riesgo

- **Entrada:** EMA rápida > EMA lenta, RSI < sobrecompra y cierre < Bollinger superior.
- **Salida:** cruce bajista de EMAs o RSI > sobrecompra.
- **Tamaño:** `(equity × risk_pct%) / (ATR × atr_stop_mult)`, limitado al equity.
- **Registro de operaciones:** tras enviar la orden, el bot espera hasta `ORDER_FILL_TIMEOUT` s
  (10 por defecto) y guarda el **precio medio y la cantidad ejecutados** y el estado (`filled`,
  `partially_filled` o `pending` si aún no se ha llenado). Las órdenes rechazadas o canceladas sin
  ejecutar no cuentan como trades. En las ventas se guarda el **PnL realizado**:
  `(precio de venta − avg_entry_price del broker) × cantidad` (sin comisiones).

### 5.1 Validación de la configuración

`POST /config` rechaza con **422** claves desconocidas, `timeframe` fuera de
`1Min|5Min|15Min|1Hour|1Day`, listas de `symbols` vacías o con símbolos inválidos (se normalizan a
mayúsculas y sin duplicados), `lookback_days` > 3650, `risk_pct` fuera de (0, 10],
`regular_hours_only` que no sea booleano y `strategy_params`
con claves desconocidas, tipos erróneos o reglas incoherentes (`ema_fast < ema_slow`,
`rsi_oversold < rsi_overbought`). El kill switch se cambia con `/toggle-bot`, no con `/config`.

## 6. Autoaprendizaje

`optimization/optimizer.py` divide el histórico reciente en entrenamiento (70 %) y validación (30 %),
busca parámetros con Optuna (TPE) maximizando una puntuación compuesta (Sharpe + Profit Factor +
Win Rate, penalizando drawdowns > 25 %) y **solo aplica** los nuevos si superan a los actuales fuera de
muestra **y** cumplen los criterios de aceptación (si no, el resultado sale con `applicable: false` y
`rejected_reason`). Todo queda en `optimization_runs`. Usa el proveedor de datos de la configuración o el que
indiques: `python -m optimization.optimizer --provider ibkr` (con un `IBKR_CLIENT_ID` libre, p.ej. 3). El workflow `backtest_scheduled.yml` lo ejecuta en días
laborables y publica los resultados como artefacto.

- **Errores por símbolo:** si un símbolo falla (sin datos, sin claves de Alpaca para acciones, IBKR
  caído…) se registra y se sigue con el resto; el resultado incluye `errors: [{symbol, error}]`. El
  proceso sale con código 2 si no se pudo optimizar ningún símbolo o el proveedor no está disponible.
- **Criterios de aceptación** (CLI o entorno; se vuelven a evaluar con `--apply-report`):

  | Criterio | CLI | Entorno | Por defecto |
  |---|---|---|---|
  | Operaciones OOS mínimas | `--min-oos-trades` | `OPT_MIN_OOS_TRADES` | 8 |
  | Sharpe OOS mínimo | `--min-oos-sharpe` | `OPT_MIN_OOS_SHARPE` | 0 |
  | Retorno OOS mínimo (0.01 = 1 %) | `--min-oos-return` | `OPT_MIN_OOS_RETURN` | 0 |
  | Brecha máx. `train_score` − puntuación OOS | `--max-train-oos-gap` | `OPT_MAX_TRAIN_OOS_GAP` | 2.0 |

- **Salida:** stdout (o `--output fichero.json`) contiene solo el JSON
  `{"results", "errors", "skipped", "applied", "criteria"}`; cada resultado incluye `improved`,
  `applicable` y `rejected_reason`. Los logs van por stderr.
- **Cómo se aplican los parámetros:**
  1. *Junto al bot* (recomendado): `python -m optimization.optimizer --trials 100` sobre la BD real
     (p.ej. un cron en la misma máquina); solo aplica si mejora fuera de muestra y el bot lo lee en caliente.
  2. *Desde el CI:* el workflow corre con `--no-apply` sobre una BD temporal. Para optimizar la
     configuración real, pasa su JSON en el input `config_json` o en la variable de repositorio
     `OPTIMIZER_CONFIG_JSON` (p.ej. la salida de `GET /config`). El resumen del job muestra los
     resultados; para aplicarlos, descarga el artefacto y ejecuta junto al bot
     `python -m optimization.optimizer --apply-report optimization.json` (valida los parámetros
     antes de guardarlos).

## 7. API

Todas las rutas salvo `/health` exigen `API_TOKEN` si está definido (sección 2.1).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Healthcheck (pública, sin token) |
| GET | `/status` | Broker activo y estado de conexión, balance, PnL diario y flotante, posiciones, modo |
| GET | `/brokers` | Brokers disponibles, activo, proveedor de datos y conexión de cada uno |
| GET/POST | `/config` | Lee / actualiza configuración (live exige confirmación; cambiar de broker en live exige `confirm_live: true`, si no 409) |
| POST | `/toggle-bot` | `{"enabled": bool?, "close_positions": bool}` — kill switch del broker activo |
| POST | `/cancel-orders` | Cancela todas las órdenes abiertas del broker activo |
| POST | `/close-position` | Cierra una posición concreta |
| GET | `/metrics` | Métricas, operaciones e historial de optimización |

### 7.1 Monitorización (Prometheus)

| Métrica | Descripción |
|---|---|
| `bot_enabled`, `bot_live_mode` | Estado configurado; se actualiza al principio de cada iteración aunque el broker falle |
| `bot_info{broker,mode}` | Broker y modo configurados (valor 1) |
| `bot_broker_connected{broker}` | 1 si el broker activo responde, 0 ante cualquier fallo (incluida la falta de credenciales) |
| `bot_paused` | 1 si el bot está en pausa (kill switch) |
| `bot_last_iteration_timestamp_seconds` | Última iteración del bucle, en pausa o activo (sirve para detectar cuelgues) |
| `bot_last_success_timestamp_seconds` | Última iteración de trading correcta; **no avanza en pausa** |
| `bot_last_healthcheck_timestamp_seconds` | Último health-check correcto con el bot en pausa |
| `bot_equity_usd`, `bot_daily_pnl_usd`, `bot_open_positions` | Cuenta del broker (en cada iteración activa y en cada health-check en pausa) |
| `bot_orders_total`, `bot_errors_total`, `bot_iterations_total` | Contadores |

Con el bot **en pausa** no se opera: solo se hace un **health-check de solo lectura** (modo, cuenta y
posiciones; nunca órdenes ni cancelaciones) cada `PAUSED_HEALTHCHECK_SECONDS` segundos (por defecto
300; `0` lo desactiva), que actualiza `bot_broker_connected`, la equity y las posiciones. Si falla en
pausa, escribe un único WARNING (sin traceback) y pone `bot_broker_connected` a 0. Si el broker falla
con el bot activo, el bot reintenta con backoff
exponencial (`loop_interval_sec` × 2ⁿ, máximo 5 min) y solo escribe un ERROR cuando cambia el error;
al recuperarse escribe `broker reconectado`.

**Logs:** todos los procesos (bot, API/uvicorn, optimizador, ib_async, optuna) escriben **una línea
JSON por evento** (`ts`, `level`, `logger`, `msg`, campos extra y `exc` con el traceback). `LOG_FILE`
duplica la salida en un fichero y `LOG_LEVELS` ajusta niveles por logger. El optimizador escribe los
logs por stderr y el resultado por stdout.

Ejemplo de reglas de alerta:

```yaml
groups:
  - name: trading-bot
    rules:
      - alert: BotColgado
        expr: time() - bot_last_iteration_timestamp_seconds > 600
        for: 5m
        labels: {severity: critical}
        annotations: {summary: "El bot no completa una iteración desde hace más de 10 min"}
      - alert: BrokerDesconectado
        expr: bot_enabled == 1 and on() max(bot_broker_connected) == 0
        for: 5m
        labels: {severity: critical}
        annotations: {summary: "Bot activo pero el broker no responde"}
      - alert: BrokerDesconectadoEnPausa
        expr: bot_paused == 1 and on() max(bot_broker_connected) == 0
        for: 15m
        labels: {severity: warning}
        annotations: {summary: "Bot en pausa y el health-check no llega al broker"}
      - alert: BotSinOperar
        expr: bot_enabled == 1 and time() - bot_last_success_timestamp_seconds > 900
        for: 5m
        labels: {severity: critical}
        annotations: {summary: "Bot activo sin una iteración de trading correcta en 15 min"}
```

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
curl -X POST localhost:8000/config -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' -d '{"broker": "ibkr"}'
curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/brokers      # estado de conexión
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
- `useRTH` sigue `regular_hours_only` (por defecto `true`, ver sección 4.1).
- Órdenes a mercado: acciones `DAY`, cripto `IOC`. Acciones en cantidades enteras.
- El kill switch usa `reqGlobalCancel`, que cancela **todas** las órdenes abiertas de la cuenta (también
  las manuales), y después liquida todas las posiciones a mercado.
- El PnL diario llega por suscripción `reqPnL`: la primera lectura puede ser 0.
- Al cambiar de broker, el bot deja de gestionar las posiciones abiertas en el anterior.
- El workflow programado de GitHub no puede conectar con TWS/Gateway: sigue usando Alpaca.
