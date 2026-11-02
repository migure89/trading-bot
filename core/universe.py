"""Universo dinámico para la sombra ``dyn_momentum``: los valores más líquidos de EE. UU.

Pasos (todo solo lectura sobre la API de datos de Alpaca; nunca toca órdenes):

1. **Activos**: lista cruda ``GET /v2/assets`` (``TradingClient.get``) porque el modelo de alpaca-py
   falla con exchanges nuevos (p. ej. TXSE). Se quedan acciones/ETFs activos y negociables, fuera
   de OTC, con ticker estándar y sin ETFs/ETNs apalancados, inversos ni de volatilidad.
2. **Liquidez** (una vez por semana): barras diarias de ~35 días en lotes de 200 símbolos;
   precio > 5 $, volumen medio en dólares de 20 sesiones > 20 M$, sin saltos diarios > 5x
   (o < 0,2x) y los ``top`` (100) de mayor volumen medio en dólares.
3. **Historial** (una vez al día): ~300 días naturales de cierres de ese universo; se descartan las
   series con saltos > 5x (tickers reutilizados, splits mal ajustados).

Caché en disco (``SHADOW_CACHE_DIR``, por defecto ``<dir de settings.db>/cache/universe``, fuera
del repositorio): la lista de activos, el progreso del escaneo de liquidez (se reanuda si se agota
el presupuesto de tiempo), el universo de la semana y el historial del día. Las peticiones usan
los timeouts y reintentos de ``core.alpaca_http`` y se espacian para respetar el límite de Alpaca.
El presupuesto (``SHADOW_SCAN_BUDGET_SEC``, 90 s por defecto) se comprueba entre lotes: si se agota
se lanza ``ScanBudgetExceeded`` y el bot reintenta más tarde desde donde se quedó.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from core.alpaca_http import configure_client
from data.base import NY
from monitoring.logger import get_logger

log = get_logger("universe")

BATCH_SIZE = 200
TOP_N = 100
MIN_PRICE = 5.0
MIN_ADV = 20e6
ADV_WINDOW = 20
JUMP_MAX = 5.0                      # |cierre/cierre previo| > 5x o < 1/5 => serie descartada
LIQUIDITY_DAYS = 35                 # días naturales para ADV20
HISTORY_DAYS = 300                  # días naturales (~205 sesiones: 127 de ventana + semanas previas)
ASSETS_MAX_AGE_DAYS = 7
DEFAULT_BUDGET_SEC = 90.0
MIN_REQUEST_INTERVAL = 0.35         # s entre peticiones (< 200 peticiones/min del plan gratuito)
VALID_EXCHANGES = {"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS", "NYSEARCA"}
TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
# ETFs/ETNs apalancados, inversos o de volatilidad (por nombre) + tickers conocidos sin pistas en el nombre.
EXCLUDE_NAME_RE = re.compile(
    r"(\b\d+(\.\d+)?\s?x\b|-\d+x|\bultra|leverag|inverse|\bbear\b|\bbull\b|\bshort\b|\bvix\b"
    r"|(?<!low )(?<!minimum )\bvolatility|daily target|\bdaily\b.*\b(long|short)\b)", re.I)
EXCLUDE_TICKERS = frozenset({"TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU", "TECL", "TNA", "TZA", "UVXY",
                             "SVXY", "VXX", "VIXY", "VIXM", "UVIX", "SVIX", "FNGU", "FNGD", "LABU", "LABD",
                             "BITX", "NVDL", "TSLL", "MSTU", "MSTX", "CONL", "QLD", "SSO", "TMF", "TMV"})
# Liquidez, letras del Tesoro y bonos a corto plazo: casi no se mueven, así que un momentum dividido
# por la volatilidad los pondría siempre arriba (p. ej. SGOV). Lista configurable con
# SHADOW_EXCLUDE_TICKERS (se suma a esta) + heurística por nombre (solo en fondos/ETFs).
CASH_LIKE_TICKERS = frozenset({
    "SGOV", "BIL", "SHV", "SHY", "TFLO", "USFR", "BILS", "ICSH", "JPST", "MINT", "VGSH", "SCHO", "GBIL",
    "CLIP", "XHLF", "TBIL", "XBIL", "TBLL", "OBIL", "BOXX", "FLOT", "FLRN", "NEAR", "GSY", "VUSB", "PULS",
    "FTSM", "SPTS", "BSV", "USTB", "SHM", "BILZ", "TBUX", "ULST", "JMST", "XONE", "UTWO", "IBTF",
    "SUB", "VNLA", "LDUR", "FLTR", "MEAR", "JAAA", "PAAA", "CLOI", "AAA"})
FUND_NAME_RE = re.compile(r"\b(etf|etn|fund)\b", re.I)
CASH_NAME_RE = re.compile(
    r"(\bt-?bills?\b|\bmoney market\b|\bbox etf\b|\bcash management\b"
    r"|\b(\d+(-\d+)?|one|three|six|twelve)[- ]month (t-?bill|treasury|bill|box)\b"
    r"|\b0-[1-5] year\b|\b1-3 year\b|\bultra[- ]short\b|\bshort[- ](term|duration|maturity)\b"
    r"|\bfloating rate\b|\bclo\b)", re.I)
# Además del patrón, el nombre debe sonar a renta fija (evita p. ej. "Short-Term REIT").
BOND_NAME_RE = re.compile(
    r"(bond|treasury|t-?bill|\bbill\b|income|muni|tax-exempt|fixed|government|maturity|corporate"
    r"|duration|\bnote\b|credit|loan|high yield|tips|inflation|money market|\bclo\b|\bbox\b)", re.I)
INVALID_SYMBOL_RE = re.compile(r"invalid symbol: ([A-Z0-9.\-/]+)")


class ScanBudgetExceeded(TimeoutError):
    """Se agotó el presupuesto de tiempo del escaneo (el progreso queda en la caché)."""


def budget_from_env() -> float:
    raw = os.getenv("SHADOW_SCAN_BUDGET_SEC", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_BUDGET_SEC
    except ValueError:
        value = -1
    if not value > 0:
        log.warning("SHADOW_SCAN_BUDGET_SEC inválido; se usa el valor por defecto",
                    extra={"value": raw, "default": DEFAULT_BUDGET_SEC})
        return DEFAULT_BUDGET_SEC
    return value


def default_cache_dir() -> Path:
    raw = os.getenv("SHADOW_CACHE_DIR", "").strip()
    if raw:
        return Path(raw)
    from config.settings import default_db_path

    return default_db_path().resolve().parent / "cache" / "universe"


def extra_excluded_from_env() -> frozenset[str]:
    """Tickers adicionales a excluir (``SHADOW_EXCLUDE_TICKERS=AAA,BBB``)."""
    raw = os.getenv("SHADOW_EXCLUDE_TICKERS", "")
    return frozenset(t.strip().upper() for t in raw.split(",") if t.strip())


def cash_like(symbol: str, name: str | None, extra: frozenset[str] = frozenset()) -> bool:
    """True si es un ETF de liquidez, letras del Tesoro o bonos a corto plazo."""
    if symbol in CASH_LIKE_TICKERS or symbol in extra:
        return True
    name = name or ""
    return bool(FUND_NAME_RE.search(name) and CASH_NAME_RE.search(name) and BOND_NAME_RE.search(name))


def excluded_asset(symbol: str, name: str | None, extra: frozenset[str] | None = None) -> bool:
    """True si no entra en el universo: ETF/ETN apalancado, inverso, de volatilidad o casi liquidez."""
    extra = extra_excluded_from_env() if extra is None else extra
    return (symbol in EXCLUDE_TICKERS or bool(EXCLUDE_NAME_RE.search(name or ""))
            or cash_like(symbol, name, extra))


def filters_signature() -> str:
    """Huella de los filtros de activos: si cambian, se invalidan las cachés de activos y universo."""
    raw = "|".join([EXCLUDE_NAME_RE.pattern, CASH_NAME_RE.pattern, FUND_NAME_RE.pattern, BOND_NAME_RE.pattern,
                    ",".join(sorted(EXCLUDE_TICKERS | CASH_LIKE_TICKERS | extra_excluded_from_env()))])
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def filter_assets(raw: list[dict]) -> list[str]:
    out, extra = set(), extra_excluded_from_env()
    for a in raw:
        sym = str(a.get("symbol") or "")
        if (a.get("status") == "active" and a.get("tradable") and a.get("exchange") in VALID_EXCHANGES
                and a.get("class", a.get("asset_class", "us_equity")) == "us_equity"
                and TICKER_RE.match(sym) and not excluded_asset(sym, a.get("name"), extra)):
            out.add(sym)
    return sorted(out)


def has_jumps(closes: pd.DataFrame, limit: float = JUMP_MAX) -> pd.Series:
    """Por columna: True si algún cierre/cierre previo es > ``limit`` o < 1/``limit``."""
    ratio = closes / closes.shift(1)
    return ((ratio > limit) | (ratio < 1.0 / limit)).any()


def liquidity_table(bars: pd.DataFrame, window: int = ADV_WINDOW) -> pd.DataFrame:
    """bars largas (symbol, timestamp, close, volume) -> por símbolo: last_close, adv, n, jump."""
    if bars.empty:
        return pd.DataFrame(columns=["last_close", "adv", "n", "jump"])
    close = bars.pivot_table(index="timestamp", columns="symbol", values="close").sort_index()
    dollar = (bars.assign(dv=bars["close"] * bars["volume"])
              .pivot_table(index="timestamp", columns="symbol", values="dv").sort_index())
    tail_c, tail_dv = close.tail(window), dollar.tail(window)
    return pd.DataFrame({
        "last_close": close.ffill().iloc[-1],
        "adv": tail_dv.mean(),
        "n": tail_c.notna().sum(),
        "jump": has_jumps(close.tail(window + 1)),
    })


def rank_universe(table: pd.DataFrame, top: int = TOP_N, window: int = ADV_WINDOW) -> list[str]:
    ok = table[(table["last_close"] > MIN_PRICE) & (table["adv"] > MIN_ADV)
               & (table["n"] >= window) & ~table["jump"].astype(bool)]
    return ok.sort_values("adv", ascending=False).head(top).index.tolist()


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class UniverseScanner:
    """Escáner de liquidez + historial de cierres. Los clientes se inyectan en los tests."""

    def __init__(self, api_key: str | None = None, secret_key: str | None = None,
                 cache_dir: str | Path | None = None, budget_sec: float | None = None,
                 trading_client=None, data_client=None, top: int = TOP_N,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 min_interval: float = MIN_REQUEST_INTERVAL) -> None:
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.budget_sec = budget_from_env() if budget_sec is None else float(budget_sec)
        self._trading, self._data = trading_client, data_client
        self.top, self._clock, self._sleep, self.min_interval = top, clock, sleep, min_interval
        self._last_request: float | None = None
        self._deadline: float | None = None
        self.stats: dict = {}

    # ---- clientes (solo lectura) --------------------------------------------------
    def _require_keys(self) -> None:
        if not (self.api_key and self.secret_key):
            raise RuntimeError("el universo dinámico necesita APCA_API_KEY_ID y APCA_API_SECRET_KEY")

    @property
    def trading_client(self):
        if self._trading is None:
            from alpaca.trading.client import TradingClient

            self._require_keys()
            # paper=True: solo se lee /v2/assets (misma lista que en live). Nunca se envían órdenes.
            self._trading = configure_client(TradingClient(self.api_key, self.secret_key, paper=True))
        return self._trading

    @property
    def data_client(self):
        if self._data is None:
            from alpaca.data.historical import StockHistoricalDataClient

            self._require_keys()
            self._data = configure_client(StockHistoricalDataClient(self.api_key, self.secret_key))
        return self._data

    # ---- utilidades ------------------------------------------------------------------
    def _check_budget(self, stage: str) -> None:
        if self._deadline is not None and self._clock() > self._deadline:
            raise ScanBudgetExceeded(f"presupuesto de {self.budget_sec:g}s agotado ({stage}); "
                                     "se reanuda en el siguiente intento")

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _path(self, name: str) -> Path:
        return self.cache_dir / name

    def _read_json(self, name: str) -> dict | None:
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_json(self, name: str, data: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(name + ".tmp")
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        tmp.replace(self._path(name))

    # ---- 1) activos ------------------------------------------------------------------
    def assets(self, today: date) -> list[str]:
        cached = self._read_json("assets.json")
        if (cached and cached.get("filters") == filters_signature()
                and (today - date.fromisoformat(cached["fetched"])).days < ASSETS_MAX_AGE_DAYS):
            return cached["symbols"]
        self._check_budget("activos")
        self._pace()
        raw = self.trading_client.get("/assets", {"asset_class": "us_equity", "status": "active"})
        symbols = filter_assets(list(raw or []))
        if not symbols:
            raise RuntimeError("Alpaca no devolvió activos negociables")
        self._write_json("assets.json", {"fetched": today.isoformat(), "symbols": symbols,
                                         "filters": filters_signature()})
        return symbols

    # ---- barras por lotes ---------------------------------------------------------------
    def fetch_bars(self, symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
        """Barras diarias SIP ajustadas en formato largo (symbol, timestamp, OHLCV)."""
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        batch = list(symbols)
        for _ in range(len(batch) + 1):
            if not batch:
                return pd.DataFrame(columns=["symbol", "timestamp", "close", "volume"])
            self._pace()
            try:
                req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day, start=start, end=end,
                                       adjustment=Adjustment.ALL, feed=DataFeed.SIP)
                df = self.data_client.get_stock_bars(req).df
                break
            except Exception as exc:
                m = INVALID_SYMBOL_RE.search(str(exc))
                if not m or m.group(1) not in batch:
                    raise
                batch.remove(m.group(1))          # Alpaca rechaza el lote entero por un ticker inválido
        else:
            raise RuntimeError("demasiados símbolos inválidos en el lote")
        if df is None or df.empty:
            return pd.DataFrame(columns=["symbol", "timestamp", "close", "volume"])
        return df.reset_index()[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]

    # ---- 2) universo semanal -------------------------------------------------------------
    def universe(self, now: datetime) -> dict:
        today = now.astimezone(NY).date()
        week = iso_week(today)
        cached = self._read_json("universe.json")
        signature = filters_signature()
        if (cached and cached.get("week") == week and cached.get("symbols")
                and cached.get("filters") == signature):
            return cached
        symbols = self.assets(today)
        end = now.astimezone(UTC) - timedelta(minutes=16)      # sin restricción SIP de 15 min
        start = end - timedelta(days=LIQUIDITY_DAYS)
        progress = self._read_json("scan_progress.json")
        if (not progress or progress.get("week") != week or progress.get("n_symbols") != len(symbols)
                or progress.get("filters") != signature):
            progress = {"week": week, "n_symbols": len(symbols), "filters": signature, "done": [], "rows": {}}
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        for i, batch in enumerate(batches):
            if i in progress["done"]:
                continue
            self._check_budget(f"liquidez, lote {i + 1}/{len(batches)}")
            table = liquidity_table(self.fetch_bars(batch, start, end))
            progress["rows"].update({s: [float(r.last_close), float(r.adv), int(r.n), bool(r.jump)]
                                     for s, r in table.iterrows()})
            progress["done"].append(i)
            self._write_json("scan_progress.json", progress)
        table = pd.DataFrame.from_dict(progress["rows"], orient="index",
                                       columns=["last_close", "adv", "n", "jump"])
        top = rank_universe(table, self.top)
        if not top:
            raise RuntimeError("ningún valor supera los filtros de liquidez")
        out = {"week": week, "filters": signature,
               "scanned_at": now.astimezone(UTC).isoformat(timespec="seconds"),
               "symbols": top, "adv": {s: round(float(table.at[s, "adv"]), 0) for s in top},
               "candidates": len(symbols), "liquid": int(((table["adv"] > MIN_ADV)
                                                         & (table["last_close"] > MIN_PRICE)).sum())}
        self._write_json("universe.json", out)
        self._path("scan_progress.json").unlink(missing_ok=True)
        log.info("universo dinámico actualizado", extra={"week": week, "size": len(top),
                                                         "candidates": len(symbols)})
        return out

    # ---- 3) historial diario -----------------------------------------------------------------
    def closes(self, now: datetime) -> tuple[pd.DataFrame, dict]:
        """Cierres diarios (fila = día UTC, columna = símbolo) del universo y metadatos del escaneo."""
        t0 = self._clock()
        self._deadline = t0 + self.budget_sec
        try:
            uni = self.universe(now)
            today = now.astimezone(NY).date().isoformat()
            hist_path = self._path("history.parquet")
            meta = self._read_json("history.json") or {}
            if (meta.get("day") == today and meta.get("symbols") == uni["symbols"]
                    and hist_path.exists()):
                panel = pd.read_parquet(hist_path)
            else:
                end = now.astimezone(UTC) - timedelta(minutes=16)
                start = end - timedelta(days=HISTORY_DAYS)
                frames = []
                syms = uni["symbols"]
                for i in range(0, len(syms), BATCH_SIZE):
                    self._check_budget("historial")
                    frames.append(self.fetch_bars(syms[i:i + BATCH_SIZE], start, end))
                bars = pd.concat(frames) if frames else pd.DataFrame()
                if bars.empty:
                    raise RuntimeError("sin barras para el universo dinámico")
                panel = bars.pivot_table(index="timestamp", columns="symbol", values="close").sort_index()
                panel.index = pd.DatetimeIndex(panel.index).tz_convert(UTC)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                panel.to_parquet(hist_path)
                self._write_json("history.json", {"day": today, "symbols": uni["symbols"]})
            jumps = has_jumps(panel)
            dropped = sorted(jumps[jumps].index)
            panel = panel.drop(columns=dropped)
            self.stats = {"week": uni["week"], "scanned_at": uni.get("scanned_at"),
                          "size": int(panel.shape[1]), "candidates": uni.get("candidates"),
                          "liquid": uni.get("liquid"), "dropped_jumps": dropped,
                          "elapsed_sec": round(self._clock() - t0, 2)}
            return panel, self.stats
        finally:
            self._deadline = None


def scanner_from_env() -> UniverseScanner:
    return UniverseScanner()


__all__ = ["ScanBudgetExceeded", "UniverseScanner", "cash_like", "excluded_asset", "filter_assets",
           "has_jumps", "liquidity_table", "rank_universe", "scanner_from_env"]
