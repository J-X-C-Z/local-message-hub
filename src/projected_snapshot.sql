-- Projected v9 schema. No triggers, attachment bytes, provider state or operational tables.
PRAGMA user_version=9;
CREATE TABLE conversations (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
    title TEXT, kind TEXT, avatar_url TEXT, member_count INTEGER,
    last_message TEXT, last_timestamp INTEGER, message_count INTEGER NOT NULL DEFAULT 0,
    extra TEXT, PRIMARY KEY(platform, account_id, id)
);
CREATE TABLE messages (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
    conversation_id TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
    timestamp INTEGER NOT NULL DEFAULT 0, type TEXT, text TEXT,
    is_self INTEGER NOT NULL DEFAULT 0, media_url TEXT, file_name TEXT,
    raw_type TEXT, preview TEXT, extra TEXT, PRIMARY KEY(platform, account_id, id)
);
CREATE TABLE contacts (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT,
    avatar_url TEXT, kind TEXT, extra TEXT, PRIMARY KEY(platform, account_id, id)
);
CREATE TABLE conversation_metadata (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', archived INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL, PRIMARY KEY(platform, account_id, conversation_id)
);
CREATE TABLE contact_identities (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
    identity_id TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(platform, account_id, contact_id)
);
