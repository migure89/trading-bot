"""Dashboard Streamlit: broker (Alpaca/IBKR), control Paper/Live, kill switch, métricas y parámetros IA.

Se comunica exclusivamente con la API FastAPI (API_URL), totalmente desacoplado.
Ejecutar: streamlit run dashboard/app.py
"""
from __future__ import annotations

import hmac
import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

# Mismo criterio que core/env.py (el dashboard se lanza con `streamlit run` y puede no tener
# la raíz del proyecto en sys.path): .env del directorio actual y de la raíz, sin pisar el entorno.
if os.getenv("SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    for _env in (os.getenv("ENV_FILE"), ".env", os.path.join(os.path.dirname(__file__), "..", ".env")):
        if _env and os.path.isfile(_env):
            load_dotenv(_env, override=False)

# Constantes compartidas con la API (rangos de validación): raíz del proyecto en sys.path.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from config.settings import RISK_PCT_MAX  # noqa: E402

RISK_STEP = 0.1  # el slider empieza en este valor (la API exige risk_pct > 0)

API_URL = os.getenv("API_URL", "http://localhost:8000")
# Mismo token que la API (API_TOKEN); se envía en cada petición si está definido.
API_TOKEN = os.getenv("API_TOKEN", "").strip()
# Contraseña opcional para abrir el dashboard (Streamlit no trae autenticación propia).
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

st.set_page_config(page_title="Trading Bot", page_icon="📈", layout="wide")


def require_password() -> None:
    """Pide DASHBOARD_PASSWORD (si está definida) antes de mostrar nada."""
    if not DASHBOARD_PASSWORD or st.session_state.get("auth_ok"):
        return
    st.title("🔒 Trading Bot")
    typed = st.text_input("Contraseña del dashboard", type="password", key="dashboard_password")
    if typed:
        if hmac.compare_digest(typed.encode(), DASHBOARD_PASSWORD.encode()):
            st.session_state["auth_ok"] = True
            st.rerun()
        st.error("Contraseña incorrecta")
    st.stop()


require_password()


def call_api(method: str, path: str, **kw) -> tuple[object | None, str | None]:
    """Llama a la API y devuelve ``(datos, error)``; ``error`` es un texto legible o ``None``."""
    if API_TOKEN:
        kw["headers"] = {**kw.get("headers", {}), "Authorization": f"Bearer {API_TOKEN}"}
    try:
        r = requests.request(method, f"{API_URL}{path}", timeout=15, **kw)
    except requests.RequestException as exc:
        return None, f"No se puede conectar con la API ({API_URL}): {exc}"
    try:
        data = r.json()
    except ValueError:  # respuesta no JSON (p.ej. proxy o 500 en texto plano)
        data = None
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        return None, f"{r.status_code}: {detail or r.text[:300] or r.reason}"
    return data, None


def api(method: str, path: str, **kw):
    """Lectura: muestra el error en la página y devuelve los datos o ``None``."""
    data, err = call_api(method, path, **kw)
    if err:
        st.error(err)
    return data


def flash(kind: str, msg: str) -> None:
    """Mensaje que sobrevive a ``st.rerun()`` y se muestra arriba en la siguiente ejecución."""
    st.session_state["flash"] = (kind, msg)


def act(method: str, path: str, ok_msg: str, **kw) -> bool:
    """Acción del usuario: guarda el resultado (éxito o error) como flash y recarga la página.

    Antes se hacía ``api(...)`` seguido de ``st.rerun()``, que borraba el ``st.error`` antes de
    que el usuario pudiera verlo (p.ej. un kill switch que fallaba sin avisar).
    """
    _, err = call_api(method, path, **kw)
    if err:
        flash("error", f"❌ No se pudo completar la acción: {err}")
    else:
        flash("success", ok_msg)
    st.rerun()
    return err is None  # solo se alcanza fuera de Streamlit (st.rerun lanza una excepción)


_flash = st.session_state.pop("flash", None)
if _flash:
    getattr(st, _flash[0])(_flash[1])

status = api("GET", "/status") or {}
cfg = api("GET", "/config") or {}
metrics = api("GET", "/metrics") or {}

# ---------------- barra lateral: modo y kill switch ----------------------
with st.sidebar:
    st.header("⚙️ Control")
    mode = cfg.get("mode", "paper")
    st.markdown(f"**Modo actual:** {'🔴 LIVE' if mode == 'live' else '🟢 PAPER'}")
    target = st.radio("Modo de ejecución", ["paper", "live"], index=0 if mode == "paper" else 1,
                      horizontal=True)
    if target != mode:
        if target == "live":
            st.warning("Vas a operar con DINERO REAL. Requiere CONFIRM_LIVE_TRADING=true en el servidor.")
            confirm = st.text_input("Escribe CONFIRMAR para continuar")
            if st.button("Activar LIVE", type="primary", disabled=confirm != "CONFIRMAR"):
                act("POST", "/config", "Modo LIVE activado", json={"mode": "live"})
        elif st.button("Volver a PAPER"):
            act("POST", "/config", "Modo PAPER activado", json={"mode": "paper"})

    # ---- broker activo y estado de conexión ----
    st.divider()
    brokers_info = api("GET", "/brokers") or {}
    labels = {"alpaca": "Alpaca", "ibkr": "Interactive Brokers"}
    current_broker = cfg.get("broker", "alpaca")
    conn = status.get("connection") or {}
    dot = "🟢 conectado" if conn.get("connected") else "🔴 desconectado"
    st.markdown(f"**Broker:** {labels.get(current_broker, current_broker)} · {dot}")
    if current_broker == "ibkr":
        st.caption(f"{conn.get('host', '?')}:{conn.get('port') or conn.get('port_paper', '?')} · "
                   f"client id {conn.get('client_id', '?')} · cuenta {conn.get('account') or '—'}")
    if conn.get("last_error"):
        st.caption(f"⚠️ {conn['last_error']}")
    options = brokers_info.get("available") or list(labels)
    new_broker = st.selectbox("Broker activo", options, index=options.index(current_broker)
                              if current_broker in options else 0, format_func=lambda b: labels.get(b, b))
    if new_broker != current_broker:
        st.info("Las posiciones abiertas en el broker anterior dejarán de gestionarse por el bot.")
        payload = {"broker": new_broker}
        ok = True
        if mode == "live":
            st.warning("Estás en LIVE: el nuevo broker operará con DINERO REAL.")
            typed = st.text_input("Escribe CONFIRMAR para cambiar de broker", key="confirm_broker")
            ok = typed == "CONFIRMAR"
            payload["confirm_live"] = True
        if st.button(f"Cambiar a {labels.get(new_broker, new_broker)}", disabled=not ok):
            act("POST", "/config", f"Broker cambiado a {labels.get(new_broker, new_broker)}", json=payload)
    dp_opts = ["auto", "alpaca", "ibkr"]
    current_dp = cfg.get("data_provider", "auto")
    new_dp = st.selectbox("Datos de mercado", dp_opts, index=dp_opts.index(current_dp),
                          format_func=lambda d: "Auto (mismo que el broker)" if d == "auto" else labels[d])
    if new_dp != current_dp and st.button("Guardar proveedor de datos"):
        act("POST", "/config", "Proveedor de datos guardado", json={"data_provider": new_dp})
    with st.expander("Conexiones"):
        for name, info in (brokers_info.get("connections") or {}).items():
            if info.get("connected"):
                state = "🟢"
            else:
                state = "🔴" if info.get("initialized") else "⚪ sin usar"
            st.write(f"{labels.get(name, name)}: {state}")

    st.divider()
    enabled = cfg.get("bot_enabled", False)
    st.markdown(f"**Bot:** {'▶️ activo' if enabled else '⏸️ pausado'}")
    if st.button("⏸️ Pausar bot" if enabled else "▶️ Arrancar bot"):
        act("POST", "/toggle-bot", "Bot pausado" if enabled else "Bot arrancado",
            json={"enabled": not enabled})
    st.divider()
    if st.button("🛑 KILL SWITCH: pausar y cerrar todo", type="primary", key="kill"):
        st.session_state["kill_confirm"] = True
    if st.session_state.get("kill_confirm"):
        st.error(f"⚠️ Esto **pausa el bot**, cancela **todas** las órdenes abiertas y cierra **todas** las "
                 f"posiciones a mercado en {labels.get(current_broker, current_broker)} "
                 f"({'LIVE, dinero real' if mode == 'live' else 'paper'}).")
        k1, k2 = st.columns(2)
        if k1.button("Sí, cerrar todo", type="primary", key="kill_yes"):
            st.session_state["kill_confirm"] = False
            act("POST", "/toggle-bot", "🛑 Kill switch ejecutado: bot pausado y posiciones cerradas",
                json={"enabled": False, "close_positions": True})
        if k2.button("Cancelar", key="kill_no"):
            st.session_state["kill_confirm"] = False
            st.rerun()
    if st.button("✖️ Cancelar órdenes abiertas"):
        act("POST", "/cancel-orders", "Órdenes canceladas")

st.title("📈 Plataforma de Trading Cuantitativo")
if status.get("error"):
    st.info(f"{labels.get(status.get('broker'), 'Broker')} no disponible: {status['error']}")

# ---------------- métricas ------------------------------------------------
NA = "—"


def money(value) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) else NA


acc = status.get("account")          # None si el broker no responde: se muestra "—", no $0.00
positions = status.get("positions") or []
c1, c2, c3, c4 = st.columns(4)
if acc:
    pct = acc.get("daily_pnl_pct")
    c1.metric("Capital (equity)", money(acc.get("equity")))
    c2.metric("PnL diario", money(acc.get("daily_pnl")),
              f"{pct:.2f}%" if isinstance(pct, (int, float)) else None)
    c3.metric("Posiciones abiertas", len(positions))
    c4.metric("PnL flotante", money(status.get("unrealized_pl")))
else:
    c1.metric("Capital (equity)", NA)
    c2.metric("PnL diario", NA)
    c3.metric("Posiciones abiertas", NA)
    c4.metric("PnL flotante", NA)

tab_pos, tab_perf, tab_ai = st.tabs(["Posiciones", "Rendimiento", "Parámetros IA"])

with tab_pos:
    if positions:
        dfp = pd.DataFrame(positions)
        st.dataframe(dfp, use_container_width=True, hide_index=True)
        fig = px.bar(dfp, x="symbol", y="unrealized_pl", color="unrealized_pl",
                     color_continuous_scale="RdYlGn", title="PnL no realizado por posición")
        st.plotly_chart(fig, use_container_width=True)
        for p in positions:
            if st.button(f"Cerrar {p['symbol']} ({p['qty']})", key=f"close_{p['symbol']}"):
                act("POST", "/close-position", f"Posición {p['symbol']} cerrada",
                    json={"symbol": p["symbol"]})
    else:
        st.write("Sin posiciones abiertas.")

with tab_perf:
    trades = pd.DataFrame(metrics.get("trades", []))
    m1, m2, m3 = st.columns(3)
    m1.metric("Operaciones", metrics.get("num_trades", 0))
    m2.metric("Win rate", f"{metrics.get('win_rate', 0) * 100:.1f}%")
    pf = metrics.get("profit_factor")
    m3.metric("Profit factor", f"{pf:.2f}" if pf else "—")
    if not trades.empty:
        trades["created_at"] = pd.to_datetime(trades["created_at"])
        trades = trades.sort_values("created_at")
        trades["cum_pnl"] = trades["pnl"].fillna(0).cumsum()
        fig = go.Figure(go.Scatter(x=trades["created_at"], y=trades["cum_pnl"], mode="lines+markers"))
        fig.update_layout(title="PnL realizado acumulado")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(trades, use_container_width=True, hide_index=True)
    else:
        st.write("Todavía no hay operaciones registradas.")

with tab_ai:
    params = cfg.get("strategy_params", {})
    st.subheader("Parámetros activos (optimizados por Optuna)")
    with st.form("params"):
        cols = st.columns(3)
        new = {}
        for i, (k, v) in enumerate(params.items()):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                new[k] = v               # valor no numérico: se conserva tal cual
                continue
            new[k] = cols[i % 3].number_input(k, value=v)
        # Mismo rango que la API (RISK_PCT_MAX); el valor guardado se recorta solo para dibujarlo.
        current_risk = min(max(float(cfg.get("risk_pct", 1.0)), RISK_STEP), RISK_PCT_MAX)
        risk = st.slider("% riesgo por operación", RISK_STEP, RISK_PCT_MAX, current_risk, RISK_STEP)
        symbols = st.text_input("Activos (separados por coma)", ",".join(cfg.get("symbols", [])))
        if st.form_submit_button("Guardar (hot-reload)"):
            act("POST", "/config", "Configuración guardada; el bot la aplicará en la próxima iteración.",
                json={"strategy_params": new, "risk_pct": risk,
                      "symbols": [s.strip() for s in symbols.split(",") if s.strip()]})
    hist = metrics.get("optimization_history", [])
    if hist:
        dfh = pd.json_normalize(hist)
        st.subheader("Historial de optimizaciones")
        if "metrics.sharpe" in dfh:
            st.plotly_chart(px.line(dfh.iloc[::-1], x="created_at", y="metrics.sharpe", color="symbol",
                                    markers=True, title="Sharpe fuera de muestra"), use_container_width=True)
        st.dataframe(dfh, use_container_width=True, hide_index=True)
