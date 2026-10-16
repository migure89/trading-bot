"""Dashboard Streamlit: control Paper/Live, kill switch, métricas y parámetros IA.

Se comunica exclusivamente con la API FastAPI (API_URL), totalmente desacoplado.
Ejecutar: streamlit run dashboard/app.py
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Trading Bot", page_icon="📈", layout="wide")


def api(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API_URL}{path}", timeout=15, **kw)
        if r.status_code >= 400:
            st.error(f"{r.status_code}: {r.json().get('detail', r.text)}")
            return None
        return r.json()
    except requests.RequestException as exc:
        st.error(f"No se puede conectar con la API ({API_URL}): {exc}")
        return None


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
                if api("POST", "/config", json={"mode": "live"}):
                    st.success("Modo LIVE activado")
                    st.rerun()
        elif st.button("Volver a PAPER"):
            api("POST", "/config", json={"mode": "paper"})
            st.rerun()

    enabled = cfg.get("bot_enabled", False)
    st.markdown(f"**Bot:** {'▶️ activo' if enabled else '⏸️ pausado'}")
    if st.button("⏸️ Pausar bot" if enabled else "▶️ Arrancar bot"):
        api("POST", "/toggle-bot", json={"enabled": not enabled})
        st.rerun()
    st.divider()
    if st.button("🛑 KILL SWITCH: pausar y cerrar todo", type="primary"):
        api("POST", "/toggle-bot", json={"enabled": False, "close_positions": True})
        st.rerun()

st.title("📈 Plataforma de Trading Cuantitativo")
if status.get("error"):
    st.info(f"Alpaca no disponible: {status['error']}")

# ---------------- métricas ------------------------------------------------
acc = status.get("account") or {}
positions = status.get("positions") or []
c1, c2, c3, c4 = st.columns(4)
c1.metric("Capital (equity)", f"${acc.get('equity', 0):,.2f}")
c2.metric("PnL diario", f"${acc.get('daily_pnl', 0):,.2f}", f"{acc.get('daily_pnl_pct', 0):.2f}%")
c3.metric("Posiciones abiertas", len(positions))
c4.metric("PnL flotante", f"${status.get('unrealized_pl', 0):,.2f}")

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
                api("POST", "/close-position", json={"symbol": p["symbol"]})
                st.rerun()
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
            new[k] = cols[i % 3].number_input(k, value=float(v) if isinstance(v, float) else int(v))
        risk = st.slider("% riesgo por operación", 0.1, 5.0, float(cfg.get("risk_pct", 1.0)), 0.1)
        symbols = st.text_input("Activos (separados por coma)", ",".join(cfg.get("symbols", [])))
        if st.form_submit_button("Guardar (hot-reload)"):
            api("POST", "/config", json={"strategy_params": new, "risk_pct": risk,
                                         "symbols": [s.strip() for s in symbols.split(",") if s.strip()]})
            st.success("Configuración guardada; el bot la aplicará en la próxima iteración.")
    hist = metrics.get("optimization_history", [])
    if hist:
        dfh = pd.json_normalize(hist)
        st.subheader("Historial de optimizaciones")
        if "metrics.sharpe" in dfh:
            st.plotly_chart(px.line(dfh.iloc[::-1], x="created_at", y="metrics.sharpe", color="symbol",
                                    markers=True, title="Sharpe fuera de muestra"), use_container_width=True)
        st.dataframe(dfh, use_container_width=True, hide_index=True)
