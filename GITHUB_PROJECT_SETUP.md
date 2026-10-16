# GITHUB_PROJECT_SETUP — Épicas e Issues

Guía para crear el backlog del proyecto en GitHub. Cada issue incluye título, labels, descripción y Definition of Done.

## Labels sugeridas

`epic`, `infra`, `alpaca`, `docker`, `config`, `core`, `strategy`, `indicators`, `backtesting`, `ml`, `optuna`, `api`, `dashboard`, `ui`, `ci`, `devops`, `monitoring`, `observability`

## Creación automática con GitHub CLI

```bash
# Requiere: gh auth login  (ejecutar en la raíz del repo)
for l in epic infra alpaca docker config core strategy indicators backtesting ml optuna api dashboard ui ci devops monitoring observability; do gh label create "$l" --force; done
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
