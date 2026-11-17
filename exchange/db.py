import sqlite3


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, jurisdiction TEXT NOT NULL,
    classification TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fields (
    source_id TEXT NOT NULL REFERENCES sources(id), name TEXT NOT NULL,
    meaning TEXT NOT NULL, kind TEXT NOT NULL, sensitive INTEGER NOT NULL,
    PRIMARY KEY(source_id,name)
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    schema_version INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
    received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, valid_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', recipient_entity TEXT, issued_at TEXT
);
CREATE TABLE IF NOT EXISTS policies (
    jurisdiction TEXT NOT NULL, classification TEXT NOT NULL, purpose TEXT NOT NULL,
    decision TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(jurisdiction,classification,purpose)
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
    PRIMARY KEY(id,revision)
);
CREATE TABLE IF NOT EXISTS product_inputs (
    product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY(product_id,product_revision,asset_id),
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    recipient TEXT NOT NULL, recipient_entity TEXT REFERENCES entities(id),
    purpose TEXT NOT NULL, created_at TEXT NOT NULL,
    input_digest TEXT NOT NULL, policy_revisions TEXT NOT NULL,
    grant_paths TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL REFERENCES entities(id),
    name TEXT NOT NULL, from_at TEXT NOT NULL, to_at TEXT,
    UNIQUE(entity_id,name,from_at)
);
CREATE TABLE IF NOT EXISTS entity_relations (
    id TEXT PRIMARY KEY, predecessor_id TEXT NOT NULL REFERENCES entities(id),
    successor_id TEXT NOT NULL REFERENCES entities(id), kind TEXT NOT NULL,
    at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    withdrawn_at TEXT, withdrawn_by TEXT,
    CHECK(predecessor_id<>successor_id),
    CHECK(kind IN ('merger','acquisition','split')),
    CHECK(status IN ('active','withdrawn'))
);
CREATE TABLE IF NOT EXISTS authorizations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    grant_id TEXT NOT NULL REFERENCES grants(id), transition TEXT NOT NULL,
    scope TEXT NOT NULL, decision TEXT NOT NULL, path TEXT NOT NULL DEFAULT '[]',
    decided_by TEXT NOT NULL, at TEXT NOT NULL,
    CHECK(transition IN ('merger','acquisition','split')),
    CHECK(decision IN ('continue','withdraw'))
);
CREATE TABLE IF NOT EXISTS grant_successions (
    grant_id TEXT NOT NULL REFERENCES grants(id), old_recipient TEXT NOT NULL,
    new_entity TEXT NOT NULL REFERENCES entities(id), transition TEXT NOT NULL,
    authorization_id TEXT NOT NULL REFERENCES authorizations(id),
    at TEXT NOT NULL, actor TEXT NOT NULL,
    PRIMARY KEY(grant_id,new_entity)
);
CREATE INDEX IF NOT EXISTS entity_names_lookup ON entity_names(name,to_at);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL
);
"""


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    _migrate(connection)
    return connection


# 既有台账数据库已按旧表结构建表，CREATE TABLE IF NOT EXISTS 不会补列。
MIGRATIONS = {
    "grants": (
        ("recipient_entity", "TEXT REFERENCES entities(id)"),
        ("issued_at", "TEXT"),
    ),
    "deliveries": (
        ("recipient_entity", "TEXT REFERENCES entities(id)"),
        ("grant_paths", "TEXT NOT NULL DEFAULT '{}'"),
    ),
    "entity_relations": (
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("withdrawn_at", "TEXT"),
        ("withdrawn_by", "TEXT"),
    ),
    "authorizations": (
        ("path", "TEXT NOT NULL DEFAULT '[]'"),
    ),
}


def _migrate(connection):
    for table, columns in MIGRATIONS.items():
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(" + table + ")")}
        for name, definition in columns:
            if name not in existing:
                connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
    connection.commit()
