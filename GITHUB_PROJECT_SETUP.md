# GITHUB_PROJECT_SETUP — Épicas e Issues

Guía para crear el backlog del proyecto en GitHub. Cada issue incluye título, labels, descripción y Definition of Done.

## Labels sugeridas

`epic`, `infra`, `alpaca`, `docker`, `config`, `core`, `strategy`, `indicators`, `backtesting`, `ml`, `optuna`, `api`, `dashboard`, `ui`, `ci`, `devops`, `monitoring`, `observability`, `brokers`, `ibkr`, `execution`, `data`, `qa`

## Creación automática con GitHub CLI

```bash
# Requiere: gh auth login  (ejecutar en la raíz del repo)
for l in epic infra alpaca docker config core strategy indicators backtesting ml optuna api dashboard ui ci devops monitoring observability brokers ibkr execution data qa; do gh label create "$l" --force; done
gh issue create --title "EPIC 1: Infraestructura y Alpaca" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[1.1] Setup del repositorio, .env, Docker y conexión con alpaca-py" --label "infra,alpaca,docker" --body "$(echo -e "Preparar la base del proyecto: estructura de carpetas, gestión de credenciales por variables de entorno, contenedores y clientes de \`alpaca-py\` (\`StockHistoricalDataClient\`, \`CryptoHistoricalDataClient\`, \`TradingClient\`).\n\n**Definition of Done**\n- [ ] Existe \`.env.example\` y \`.env\` está en \`.gitignore\`; no hay claves en el código\n- [ ] \`docker compose up\` levanta bot, API y dashboard\n- [ ] \`data/alpaca_data.py\` descarga barras de acciones y cripto\n- [ ] \`execution/broker.py\` crea el \`TradingClient\` con \`paper=True/False\`\n- [ ] README documenta credenciales y despliegue")"
gh issue create --title "[1.2] Configuración dinámica Hot-Reload con SQLite" --label "config,core" --body "$(echo -e "Persistir la configuración (modo, activos, % riesgo, parámetros de indicadores, estado del bot) en \`config/settings.db\` a partir de \`config/schema.sql\`, y releerla en cada iteración del motor.\n\n**Definition of Done**\n- [ ] La BD se crea y siembra automáticamente al arrancar\n- [ ] Cambios desde API/dashboard se aplican sin reiniciar\n- [ ] Pasar a \`live\` sin \`CONFIRM_LIVE_TRADING=true\` falla con error explícito\n- [ ] Validación de \`risk_pct\` y claves desconocidas\n- [ ] Tests en \`tests/test_settings.py\`")"
gh issue create --title "EPIC 2: Estrategia y Backtesting" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[2.1] Módulo de indicadores técnicos" --label "strategy,indicators" --body "$(echo -e "Implementar EMA, SMA, RSI, MACD, Bollinger Bands y ATR configurables (pandas puro, compatible con la API de pandas-ta) y la estrategia EMA+RSI.\n\n**Definition of Done**\n- [ ] Los seis indicadores están implementados y parametrizados\n- [ ] RSI acotado en [0,100], Bollinger ordenadas, ATR > 0 (tests)\n- [ ] \`generate_signals\` devuelve posición 0/1 sin look-ahead\n- [ ] Tests en \`tests/test_indicators.py\`")"
gh issue create --title "[2.2] Motor de backtesting con datos históricos de Alpaca" --label "backtesting" --body "$(echo -e "Motor vectorizado pandas/numpy con comisiones, reconstrucción de operaciones y métricas: Sharpe, Profit Factor, Max Drawdown, Win Rate y retorno total.\n\n**Definition of Done**\n- [ ] \`run_backtest\` devuelve curva de equity, operaciones y métricas\n- [ ] Las señales se desplazan una barra (sin sesgo de anticipación)\n- [ ] Comisiones configurables en bps\n- [ ] Tests en \`tests/test_backtest_optimizer.py\`")"
gh issue create --title "EPIC 3: Autoaprendizaje (Optuna)" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[3.1] Integración de Optuna para reoptimización de hiperparámetros" --label "ml,optuna" --body "$(echo -e "Espacio de búsqueda para los parámetros de la estrategia y función objetivo compuesta. Ejecutable con \`python -m optimization.optimizer\`.\n\n**Definition of Done**\n- [ ] Estudio TPE reproducible (seed)\n- [ ] Restricción \`ema_slow > ema_fast\`\n- [ ] Validación train/test fuera de muestra\n- [ ] CLI con \`--trials\` y \`--no-apply\`")"
gh issue create --title "[3.2] Feedback loop entre métricas de trading y ajuste de la estrategia" --label "ml,core" --body "$(echo -e "Cerrar el ciclo: evaluar parámetros actuales, reoptimizar por símbolo, aplicar solo si mejoran fuera de muestra y guardar en la BD para que el bot los use en caliente.\n\n**Definition of Done**\n- [ ] Cada ejecución queda en \`optimization_runs\`\n- [ ] Solo se aplican parámetros que mejoran la puntuación OOS\n- [ ] El bot usa los nuevos parámetros en la siguiente iteración\n- [ ] Workflow programado \`backtest_scheduled.yml\` publica artefactos")"
gh issue create --title "EPIC 4: API FastAPI y Dashboard Streamlit" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[4.1] Endpoints de control y telemetría en FastAPI" --label "api" --body "$(echo -e "\`GET /status\`, \`GET/POST /config\`, \`POST /toggle-bot\` (kill switch con cierre de posiciones), \`POST /close-position\`, \`GET /metrics\`.\n\n**Definition of Done**\n- [ ] Swagger disponible en \`/docs\`\n- [ ] \`POST /config\` con live no confirmado devuelve 403\n- [ ] \`/toggle-bot\` con \`close_positions\` liquida todo y cancela órdenes\n- [ ] \`/status\` responde aunque Alpaca no esté disponible\n- [ ] Tests en \`tests/test_api.py\`")"
gh issue create --title "[4.2] Dashboard visual en Streamlit (Control Paper/Live, Kill Switch, métricas)" --label "dashboard,ui" --body "$(echo -e "Dashboard desacoplado que consume la API: selector Paper/Live con confirmación, \`st.metric\` de capital/PnL/posiciones, gráficas Plotly, tabla de posiciones con cierre de emergencia y panel de parámetros optimizados.\n\n**Definition of Done**\n- [ ] Pasar a live exige escribir CONFIRMAR\n- [ ] Botón de kill switch global y cierre por posición\n- [ ] Gráficas de PnL acumulado e historial de optimización\n- [ ] Formulario de parámetros que guarda vía API (hot-reload)")"
gh issue create --title "EPIC 5: CI/CD y Monitoreo" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[5.1] Workflows de GitHub Actions (tests y linter)" --label "ci,devops" --body "$(echo -e "\`ci.yml\` ejecuta ruff y pytest en cada push/PR; plantillas de issues en \`.github/ISSUE_TEMPLATE\`.\n\n**Definition of Done**\n- [ ] CI verde en \`main\`\n- [ ] Los tests no requieren credenciales (mocks de Alpaca)\n- [ ] Secrets \`APCA_API_KEY_ID\`/\`APCA_API_SECRET_KEY\` configurados para el workflow programado")"
gh issue create --title "[5.2] Logging estructurado en JSON y exporter de métricas" --label "monitoring,observability" --body "$(echo -e "Logs JSON de una línea con campos extra y exporter Prometheus (equity, PnL diario, posiciones, órdenes, errores, modo).\n\n**Definition of Done**\n- [ ] Todos los logs del bot/optimizador son JSON válido\n- [ ] \`/metrics\` de Prometheus en el puerto 9100\n- [ ] Contadores de órdenes por símbolo/lado y de errores\n- [ ] Test del formateador JSON")"
gh issue create --title "EPIC 6: Integración con Interactive Brokers" --label epic --body "Épica. Ver GITHUB_PROJECT_SETUP.md"
gh issue create --title "[6.1] Interfaz común de broker y datos con selección en caliente" --label "core,brokers" --body "$(echo -e "Abstraer el broker (\`execution/base.py\`, \`BaseBroker\`) y el proveedor de datos (\`data/base.py\`, \`DataProvider\`) para que bot, API, dashboard y optimizador no dependan de Alpaca. Registros \`BrokerRegistry\` / \`DataProviderRegistry\` y claves \`broker\` y \`data_provider\` en la configuración (hot-reload).\n\n**Definition of Done**\n- [ ] \`AlpacaBroker\` y \`AlpacaDataProvider\` implementan la interfaz sin cambiar su comportamiento\n- [ ] \`broker\` (\`alpaca\`/\`ibkr\`) y \`data_provider\` (\`auto\`/\`alpaca\`/\`ibkr\`) validados en \`SettingsStore\`\n- [ ] El bot cambia de broker en la siguiente iteración sin reiniciar\n- [ ] \`trades.broker\` registrado, con migración automática de BDs antiguas\n- [ ] Tests existentes de Alpaca siguen en verde")"
gh issue create --title "[6.2] Conexión con TWS / IB Gateway y broker IBKR (ib_async)" --label "ibkr,brokers,execution" --body "$(echo -e "\`connectors/ibkr.py\` con \`ib_async\` en un hilo con event loop propio (compartido por broker y datos), host/puerto/client id por variables de entorno y paper/live según puerto (7497/7496 TWS, 4002/4001 Gateway, 4004/4003 Docker). \`execution/ibkr_broker.py\` con cuenta, posiciones, órdenes a mercado, cancelación y kill switch.\n\n**Definition of Done**\n- [ ] Modo live exige \`CONFIRM_LIVE_TRADING=true\` también en IBKR\n- [ ] Se rechazan puertos cruzados y cuentas que no cuadran con el modo (\`DU…\` = paper)\n- [ ] Reconexión automática si TWS/Gateway se reinicia\n- [ ] \`close_all\` = \`reqGlobalCancel\` + liquidación de todas las posiciones\n- [ ] Tests con un IB falso, sin red (\`tests/test_ibkr.py\`)")"
gh issue create --title "[6.3] Históricos de IBKR para bot, backtesting y Optuna" --label "ibkr,data,backtesting" --body "$(echo -e "\`data/ibkr_data.py\`: \`reqHistoricalData\` troceado por tamaño de barra con pausa entre bloques (pacing), normalizado al mismo DataFrame OHLCV UTC que Alpaca. \`python -m optimization.optimizer --provider ibkr\`.\n\n**Definition of Done**\n- [ ] Acciones (\`TRADES\`) y cripto (\`AGGTRADES\`) en todos los timeframes de la plataforma\n- [ ] Sin duplicados entre bloques y orden cronológico\n- [ ] El motor de backtesting y Optuna funcionan con datos IBKR\n- [ ] Pacing configurable con \`IBKR_HIST_PACING_SEC\`")"
gh issue create --title "[6.4] API, dashboard y Docker para Interactive Brokers" --label "api,dashboard,docker,ibkr" --body "$(echo -e "Selección del broker activo y del proveedor de datos desde API y dashboard, estado de conexión visible y servicio opcional \`ib-gateway\` (imagen \`gnzsnz/ib-gateway\`, perfil \`ibkr\`) en \`docker-compose.yml\`.\n\n**Definition of Done**\n- [ ] \`GET /brokers\`, \`POST /cancel-orders\` y \`connection\` en \`GET /status\`\n- [ ] Cambiar de broker en live exige \`confirm_live: true\` (409 si falta) y escribir CONFIRMAR en el dashboard\n- [ ] \`docker compose --profile ibkr up -d\` levanta IB Gateway con credenciales por variables de entorno\n- [ ] Client ids distintos por servicio (bot=1, API=2, optimizador=3)\n- [ ] README y \`.env.example\` documentan la configuración")"
gh issue create --title "[6.5] Validación end-to-end en cuenta paper de IBKR" --label "ibkr,qa" --body "$(echo -e "Probar la integración contra una cuenta paper real (TWS o IB Gateway) antes de plantear live.\n\n**Definition of Done**\n- [ ] \`main.py --once\` con \`broker=ibkr\` lee cuenta, posiciones y datos\n- [ ] Compra/venta de una acción en paper y aparición en el dashboard\n- [ ] Kill switch probado en paper (cancela órdenes y cierra posiciones)\n- [ ] Revisar órdenes cripto en PAXOS (puede requerir \`cashQty\` en compras)\n- [ ] Verificar suscripciones de datos de mercado compartidas con la cuenta paper")"
```

---

## EPIC 1: Infraestructura y Alpaca

**Labels:** `epic`

### Issue 1.1: Setup del repositorio, .env, Docker y conexión con alpaca-py

**Labels:** `infra`, `alpaca`, `docker`

**Descripción**

Preparar la base del proyecto: estructura de carpetas, gestión de credenciales por variables de entorno, contenedores y clientes de `alpaca-py` (`StockHistoricalDataClient`, `CryptoHistoricalDataClient`, `TradingClient`).

**Definition of Done**

- [ ] Existe `.env.example` y `.env` está en `.gitignore`; no hay claves en el código
- [ ] `docker compose up` levanta bot, API y dashboard
- [ ] `data/alpaca_data.py` descarga barras de acciones y cripto
- [ ] `execution/broker.py` crea el `TradingClient` con `paper=True/False`
- [ ] README documenta credenciales y despliegue

### Issue 1.2: Configuración dinámica Hot-Reload con SQLite

**Labels:** `config`, `core`

**Descripción**

Persistir la configuración (modo, activos, % riesgo, parámetros de indicadores, estado del bot) en `config/settings.db` a partir de `config/schema.sql`, y releerla en cada iteración del motor.

**Definition of Done**

- [ ] La BD se crea y siembra automáticamente al arrancar
- [ ] Cambios desde API/dashboard se aplican sin reiniciar
- [ ] Pasar a `live` sin `CONFIRM_LIVE_TRADING=true` falla con error explícito
- [ ] Validación de `risk_pct` y claves desconocidas
- [ ] Tests en `tests/test_settings.py`

---

## EPIC 2: Estrategia y Backtesting

**Labels:** `epic`

### Issue 2.1: Módulo de indicadores técnicos

**Labels:** `strategy`, `indicators`

**Descripción**

Implementar EMA, SMA, RSI, MACD, Bollinger Bands y ATR configurables (pandas puro, compatible con la API de pandas-ta) y la estrategia EMA+RSI.

**Definition of Done**

- [ ] Los seis indicadores están implementados y parametrizados
- [ ] RSI acotado en [0,100], Bollinger ordenadas, ATR > 0 (tests)
- [ ] `generate_signals` devuelve posición 0/1 sin look-ahead
- [ ] Tests en `tests/test_indicators.py`

### Issue 2.2: Motor de backtesting con datos históricos de Alpaca

**Labels:** `backtesting`

**Descripción**

Motor vectorizado pandas/numpy con comisiones, reconstrucción de operaciones y métricas: Sharpe, Profit Factor, Max Drawdown, Win Rate y retorno total.

**Definition of Done**

- [ ] `run_backtest` devuelve curva de equity, operaciones y métricas
- [ ] Las señales se desplazan una barra (sin sesgo de anticipación)
- [ ] Comisiones configurables en bps
- [ ] Tests en `tests/test_backtest_optimizer.py`

---

## EPIC 3: Autoaprendizaje (Optuna)

**Labels:** `epic`

### Issue 3.1: Integración de Optuna para reoptimización de hiperparámetros

**Labels:** `ml`, `optuna`

**Descripción**

Espacio de búsqueda para los parámetros de la estrategia y función objetivo compuesta. Ejecutable con `python -m optimization.optimizer`.

**Definition of Done**

- [ ] Estudio TPE reproducible (seed)
- [ ] Restricción `ema_slow > ema_fast`
- [ ] Validación train/test fuera de muestra
- [ ] CLI con `--trials` y `--no-apply`

### Issue 3.2: Feedback loop entre métricas de trading y ajuste de la estrategia

**Labels:** `ml`, `core`

**Descripción**

Cerrar el ciclo: evaluar parámetros actuales, reoptimizar por símbolo, aplicar solo si mejoran fuera de muestra y guardar en la BD para que el bot los use en caliente.

**Definition of Done**

- [ ] Cada ejecución queda en `optimization_runs`
- [ ] Solo se aplican parámetros que mejoran la puntuación OOS
- [ ] El bot usa los nuevos parámetros en la siguiente iteración
- [ ] Workflow programado `backtest_scheduled.yml` publica artefactos

---

## EPIC 4: API FastAPI y Dashboard Streamlit

**Labels:** `epic`

### Issue 4.1: Endpoints de control y telemetría en FastAPI

**Labels:** `api`

**Descripción**

`GET /status`, `GET/POST /config`, `POST /toggle-bot` (kill switch con cierre de posiciones), `POST /close-position`, `GET /metrics`.

**Definition of Done**

- [ ] Swagger disponible en `/docs`
- [ ] `POST /config` con live no confirmado devuelve 403
- [ ] `/toggle-bot` con `close_positions` liquida todo y cancela órdenes
- [ ] `/status` responde aunque Alpaca no esté disponible
- [ ] Tests en `tests/test_api.py`

### Issue 4.2: Dashboard visual en Streamlit (Control Paper/Live, Kill Switch, métricas)

**Labels:** `dashboard`, `ui`

**Descripción**

Dashboard desacoplado que consume la API: selector Paper/Live con confirmación, `st.metric` de capital/PnL/posiciones, gráficas Plotly, tabla de posiciones con cierre de emergencia y panel de parámetros optimizados.

**Definition of Done**

- [ ] Pasar a live exige escribir CONFIRMAR
- [ ] Botón de kill switch global y cierre por posición
- [ ] Gráficas de PnL acumulado e historial de optimización
- [ ] Formulario de parámetros que guarda vía API (hot-reload)

---

## EPIC 5: CI/CD y Monitoreo

**Labels:** `epic`

### Issue 5.1: Workflows de GitHub Actions (tests y linter)

**Labels:** `ci`, `devops`

**Descripción**

`ci.yml` ejecuta ruff y pytest en cada push/PR; plantillas de issues en `.github/ISSUE_TEMPLATE`.

**Definition of Done**

- [ ] CI verde en `main`
- [ ] Los tests no requieren credenciales (mocks de Alpaca)
- [ ] Secrets `APCA_API_KEY_ID`/`APCA_API_SECRET_KEY` configurados para el workflow programado

### Issue 5.2: Logging estructurado en JSON y exporter de métricas

**Labels:** `monitoring`, `observability`

**Descripción**

Logs JSON de una línea con campos extra y exporter Prometheus (equity, PnL diario, posiciones, órdenes, errores, modo).

**Definition of Done**

- [ ] Todos los logs del bot/optimizador son JSON válido
- [ ] `/metrics` de Prometheus en el puerto 9100
- [ ] Contadores de órdenes por símbolo/lado y de errores
- [ ] Test del formateador JSON

---

## EPIC 6: Integración con Interactive Brokers

**Labels:** `epic`

### Issue 6.1: Interfaz común de broker y datos con selección en caliente

**Labels:** `core`, `brokers`

**Descripción**

Abstraer el broker (`execution/base.py`, `BaseBroker`) y el proveedor de datos (`data/base.py`, `DataProvider`) para que bot, API, dashboard y optimizador no dependan de Alpaca. Registros `BrokerRegistry` / `DataProviderRegistry` y claves `broker` y `data_provider` en la configuración (hot-reload).

**Definition of Done**

- [ ] `AlpacaBroker` y `AlpacaDataProvider` implementan la interfaz sin cambiar su comportamiento
- [ ] `broker` (`alpaca`/`ibkr`) y `data_provider` (`auto`/`alpaca`/`ibkr`) validados en `SettingsStore`
- [ ] El bot cambia de broker en la siguiente iteración sin reiniciar
- [ ] `trades.broker` registrado, con migración automática de BDs antiguas
- [ ] Tests existentes de Alpaca siguen en verde

### Issue 6.2: Conexión con TWS / IB Gateway y broker IBKR (ib_async)

**Labels:** `ibkr`, `brokers`, `execution`

**Descripción**

`connectors/ibkr.py` con `ib_async` en un hilo con event loop propio (compartido por broker y datos), host/puerto/client id por variables de entorno y paper/live según puerto (7497/7496 TWS, 4002/4001 Gateway, 4004/4003 Docker). `execution/ibkr_broker.py` con cuenta, posiciones, órdenes a mercado, cancelación y kill switch.

**Definition of Done**

- [ ] Modo live exige `CONFIRM_LIVE_TRADING=true` también en IBKR
- [ ] Se rechazan puertos cruzados y cuentas que no cuadran con el modo (`DU…` = paper)
- [ ] Reconexión automática si TWS/Gateway se reinicia
- [ ] `close_all` = `reqGlobalCancel` + liquidación de todas las posiciones
- [ ] Tests con un IB falso, sin red (`tests/test_ibkr.py`)

### Issue 6.3: Históricos de IBKR para bot, backtesting y Optuna

**Labels:** `ibkr`, `data`, `backtesting`

**Descripción**

`data/ibkr_data.py`: `reqHistoricalData` troceado por tamaño de barra con pausa entre bloques (pacing), normalizado al mismo DataFrame OHLCV UTC que Alpaca. `python -m optimization.optimizer --provider ibkr`.

**Definition of Done**

- [ ] Acciones (`TRADES`) y cripto (`AGGTRADES`) en todos los timeframes de la plataforma
- [ ] Sin duplicados entre bloques y orden cronológico
- [ ] El motor de backtesting y Optuna funcionan con datos IBKR
- [ ] Pacing configurable con `IBKR_HIST_PACING_SEC`

### Issue 6.4: API, dashboard y Docker para Interactive Brokers

**Labels:** `api`, `dashboard`, `docker`, `ibkr`

**Descripción**

Selección del broker activo y del proveedor de datos desde API y dashboard, estado de conexión visible y servicio opcional `ib-gateway` (imagen `gnzsnz/ib-gateway`, perfil `ibkr`) en `docker-compose.yml`.

**Definition of Done**

- [ ] `GET /brokers`, `POST /cancel-orders` y `connection` en `GET /status`
- [ ] Cambiar de broker en live exige `confirm_live: true` (409 si falta) y escribir CONFIRMAR en el dashboard
- [ ] `docker compose --profile ibkr up -d` levanta IB Gateway con credenciales por variables de entorno
- [ ] Client ids distintos por servicio (bot=1, API=2, optimizador=3)
- [ ] README y `.env.example` documentan la configuración

### Issue 6.5: Validación end-to-end en cuenta paper de IBKR

**Labels:** `ibkr`, `qa`

**Descripción**

Probar la integración contra una cuenta paper real (TWS o IB Gateway) antes de plantear live.

**Definition of Done**

- [ ] `main.py --once` con `broker=ibkr` lee cuenta, posiciones y datos
- [ ] Compra/venta de una acción en paper y aparición en el dashboard
- [ ] Kill switch probado en paper (cancela órdenes y cierra posiciones)
- [ ] Revisar órdenes cripto en PAXOS (puede requerir `cashQty` en compras)
- [ ] Verificar suscripciones de datos de mercado compartidas con la cuenta paper
