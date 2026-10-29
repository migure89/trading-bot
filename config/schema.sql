-- Esquema inicial de la base de datos de configuración dinámica.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,           -- valor serializado en JSON
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Historial de parámetros optimizados por Optuna.
CREATE TABLE IF NOT EXISTS optimization_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    params       TEXT NOT NULL,         -- JSON
    metrics      TEXT NOT NULL,         -- JSON
    applied      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Registro de operaciones enviadas por el bot.
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    qty         REAL NOT NULL,
    price       REAL,
    mode        TEXT NOT NULL,
    broker      TEXT,                   -- alpaca | ibkr
    order_id    TEXT,
    pnl         REAL,                   -- PnL realizado (solo ventas ejecutadas)
    status      TEXT,                   -- filled | partially_filled | pending
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Migraciones de configuración ya aplicadas (una vez por BD).
CREATE TABLE IF NOT EXISTS migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Señales y pesos objetivo de la estrategia en sombra (solo registro: nunca se envían órdenes).
CREATE TABLE IF NOT EXISTS shadow_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT NOT NULL,
    as_of       TEXT,                   -- fecha (NY) de la última barra diaria cerrada usada
    payload     TEXT NOT NULL,          -- JSON con pesos, cambios y régimen
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
