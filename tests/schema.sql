-- Mirrored from the production databases on 2026-09-27.
-- Regenerate from the real sqlite_master when a table drifts; do not
-- hand-edit. A previous hand-written fixture invented column names and
-- therefore asserted against a schema that does not exist in production.
--
-- brain.db tables
CREATE TABLE cache (
                prompt TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                timestamp DATETIME NOT NULL
            );
CREATE TABLE flybrain_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                schema_version INTEGER NOT NULL DEFAULT 1,
                data BLOB NOT NULL,
                updated_at TEXT NOT NULL
            );
CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                text TEXT NOT NULL
            );
CREATE TABLE memory_graph (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_key TEXT NOT NULL UNIQUE,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                source TEXT,
                target TEXT,
                affinity REAL NOT NULL DEFAULT 0.0,
                weight REAL NOT NULL DEFAULT 1.0,
                touch_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            , gmif_logical_form TEXT, gmif_validation_type TEXT, gmif_extraction_confidence REAL DEFAULT 0.0, gmif_validation_confidence REAL DEFAULT 0.0, gmif_source_chunks TEXT, gmif_level TEXT, gmif_classified_at TEXT, gmif_classified_by TEXT, node_gmif_type TEXT, node_gmif_confidence REAL DEFAULT 0.0, node_gmif_evidence TEXT);
CREATE TABLE response_cache (
                normalized_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
CREATE TABLE topic_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_key TEXT,
                updated_at TEXT NOT NULL
            );

-- config.db tables
CREATE TABLE config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    description TEXT,
    is_sensitive BOOLEAN DEFAULT FALSE,
    UNIQUE(category, key)
);
CREATE TABLE config_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    display_order INTEGER DEFAULT 0
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
