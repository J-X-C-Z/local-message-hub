import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

export class EventStore {
  constructor(filename, retentionDays = 30) {
    mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(filename);
    this.retentionDays = retentionDays;
    this.db.exec(`
      PRAGMA busy_timeout=5000;
      PRAGMA journal_mode=WAL;
      PRAGMA secure_delete=ON;
      CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        received_at INTEGER NOT NULL,
        sender TEXT NOT NULL,
        msg_type TEXT NOT NULL,
        payload TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS events_received ON events(received_at);
      CREATE TABLE IF NOT EXISTS receiver_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS event_updates (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL
      );
    `);
    this.deleteExpired = this.db.prepare('DELETE FROM events WHERE received_at < ?');
    this.insertEvent = this.db.prepare(`INSERT OR IGNORE INTO events
      (dedup_key, received_at, sender, msg_type, payload) VALUES (?, ?, ?, ?, ?)`);
    this.writeState = this.db.prepare('INSERT INTO receiver_state (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE receiver_state.value<>excluded.value');
    this.lastPrune = 0;
    this.recordUpdate=this.db.prepare('INSERT INTO event_updates(event_id) VALUES (?)');
    this.getEventByKey=this.db.prepare('SELECT id,payload FROM events WHERE dedup_key=?');
    this.updateEvent=this.db.prepare('UPDATE events SET payload=?,sender=?,msg_type=? WHERE id=?');
    this.prune();
  }

  prune(force = true) {
    const now = Date.now();
    const cutoff = now - this.retentionDays * 86400000;
    if (force || now - this.lastPrune >= 60000) { this.deleteExpired.run(cutoff); this.lastPrune = now; }
    return cutoff;
  }

  add(dedupKey, payload) {
    const cutoff = this.prune(false);
    const timestamp = Number(payload.CreateTime);
    if (Number.isFinite(timestamp) && timestamp * 1000 < cutoff) return false;
    const result = this.insertEvent.run(dedupKey, Date.now(), String(payload.FromUserName || ''), String(payload.MsgType || ''), JSON.stringify(payload));
    return result.changes > 0;
  }

  list({ after_id = 0, limit = 50, sender, msg_type } = {}) {
    this.prune();
    const clauses = ['id > ?'];
    const args = [after_id];
    if (sender !== undefined) { clauses.push('sender = ?'); args.push(sender); }
    if (msg_type !== undefined) { clauses.push('msg_type = ?'); args.push(msg_type); }
    args.push(limit);
    const rows = this.db.prepare(`SELECT * FROM events WHERE ${clauses.join(' AND ')} ORDER BY id LIMIT ?`).all(...args);
    return {
      events: rows.map(row => ({ id: row.id, received_at: row.received_at, message: JSON.parse(row.payload) })),
      next_after_id: rows.at(-1)?.id ?? after_id,
      source: '仅包含本服务启动接收后、企业微信推送给该应用的消息和事件；不包含普通员工聊天记录。',
    };
  }

  upsertLocal(dedupKey, payload) {
    if(payload.Source!=='local-database')throw new Error('仅允许修复本地消息缓存');
    const existing=this.getEventByKey.get(dedupKey);
    if(!existing)return this.add(dedupKey,payload);
    let serialized=JSON.stringify(payload);
    if(serialized===existing.payload)return false;
    if(payload.Platform==='wechat'){
      const old=JSON.parse(existing.payload), merged={...payload};
      for(const key of ['Image','Sticker','Attachment']){
        // The first pass decodes text only. Missing media is not a change;
        // keep pending/on-demand states too, until a media pass inspects them.
        if(old[key]&&(payload[key]===undefined||(old[key].status==='ready'&&payload[key]?.status!=='ready'&&payload[key]?.source!=='wechat-file')))merged[key]=old[key];
      }
      if (merged.Video?.id && merged.Attachment?.id===merged.Video.id) delete merged.Attachment;
      if(old.ChatRecord?.media_checked&&payload.ChatRecord&&!payload.ChatRecord.media_checked)merged.ChatRecord=old.ChatRecord;
      serialized=JSON.stringify(merged);
    }
    if(serialized===existing.payload)return false;
    // Preserve ID and arrival time: re-decoding cached history must not create
    // new unread messages or move the conversation to today's date.
    this.updateEvent.run(serialized,String(payload.FromUserName||''),String(payload.MsgType||''),existing.id);
    this.recordUpdate.run(existing.id);
    return true;
  }

  close() { this.db.close(); }

  setReceiverState(key, value) {
    this.writeState.run(key, String(value));
  }
}
