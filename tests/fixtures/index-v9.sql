-- Synthetic normalized v9 schema (the five relations this stage reads).
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
INSERT INTO conversations(platform,account_id,id,title,last_timestamp) VALUES('wechat','owner','room','Fixture conversation',7);
INSERT INTO contacts(platform,account_id,id,name,kind) VALUES('wechat','owner','friend','Fixture contact','friend');
INSERT INTO messages(platform, account_id,id,conversation_id,sender_id,timestamp,type,text) VALUES
  ('wechat','owner','m1','room','friend',6,'text','earlier'),
  ('wechat','owner','m2','room','friend',7,'text','example first'),
  ('wechat','owner','m3','room','friend',7,'text','example second');

-- Only normalized IDs such as m1 and m2 may cross the plugin API.
UPDATE messages SET extra='{"_hub_wechat_server_id":"7654321"}' WHERE id='m1';
UPDATE messages SET extra='{"wechat_quote":{"target_server_id":"7654321"}}' WHERE id='m2';
