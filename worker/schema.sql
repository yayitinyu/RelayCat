-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, -- Telegram User ID
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_verified BOOLEAN DEFAULT FALSE,
    is_banned BOOLEAN DEFAULT FALSE,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);

-- Message Routes table (for reply mapping)
CREATE TABLE IF NOT EXISTS message_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    admin_message_id INTEGER,
    user_message_id INTEGER,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);

-- Index for fast lookup
CREATE INDEX IF NOT EXISTS idx_routes_admin_msg ON message_routes(admin_message_id);

-- Rules table (optional for now, but good to have)
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT DEFAULT 'message_content',
    pattern TEXT NOT NULL,
    action TEXT DEFAULT 'block',
    is_active BOOLEAN DEFAULT TRUE,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);

-- Settings table
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    description TEXT
);
