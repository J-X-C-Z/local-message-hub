// 数据库提交后最多约 500 ms 检测到增量；每个客户端按自己的游标补发。
function hasUpdateLog(db) {
  return Boolean(db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_updates'").get());
}

export function readUpdateWatermark(db) {
  return hasUpdateLog(db) ? db.prepare('SELECT COALESCE(MAX(seq), 0) AS seq FROM event_updates').get().seq : 0;
}

export function openMessageStream(req, res, { getDatabase, afterId, afterUpdateId = null, retentionDays, intervalMs = 500 }) {
  let cursor = afterId;
  let changeCursor = afterUpdateId;
  let closed = false;
  let blocked = false;
  let reportedError = false;
  let statusSignature = '';
  let lastHeartbeat = 0;
  let preparedDatabase=null,queries=null;
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive', 'X-Accel-Buffering': 'no',
  });
  blocked = !res.write('retry: 2000\n\n');
  function write(event, data, id) {
    if (closed || blocked) return false;
    blocked = !res.write(`${id === undefined ? '' : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    return true;
  }
  const tick = () => {
    if (closed || blocked) return;
    try {
      const db = getDatabase();
      if(db&&db!==preparedDatabase){
        queries = {
          hasState: db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='receiver_state'"),
          state: null,
          messages: db.prepare('SELECT id,received_at,payload FROM events WHERE id > ? AND received_at >= ? ORDER BY id LIMIT 200'),
          updates: null,
        };
        preparedDatabase=db;
      }
      const now = Date.now();
      const status = { database_ready: Boolean(db), receiver_running: false, last_verified_at: null, last_callback_at: null };
      if (db) {
        const hasState = queries.hasState.get();
        if (hasState) {
          queries.state ||= db.prepare("SELECT key,value FROM receiver_state WHERE key <> 'wechat_social'");
          const raw = Object.fromEntries(queries.state.all().map(row => [row.key, row.value]));
          const values = Object.fromEntries(Object.entries(raw).map(([key,value])=>[key,Number(value)]));
          status.source=raw.source||'callback';status.error=raw.local_error||'';
          status.wechat={status:raw.wechat_status||'未启动',error:raw.wechat_error||'',total:values.wechat_total||0,conversations:values.wechat_conversations||0,locked:values.wechat_locked||0,social_revision:raw.wechat_social_revision||'',fallback:raw.wechat_watch_fallback==='1'};
          status.fallback=raw.local_watch_fallback==='1';
          status.receiver_running = values.heartbeat_at > now - 15000;
          status.last_verified_at = values.last_verified_at || null;
          status.last_callback_at = values.last_callback_at || null;
        }
      }
      if (db && !queries.updates && hasUpdateLog(db)) {
        queries.updates = db.prepare(`SELECT u.seq,e.id,e.received_at,e.payload
          FROM event_updates u LEFT JOIN events e ON e.id=u.event_id AND e.id<=? AND e.received_at>=?
          WHERE u.seq>? ORDER BY u.seq LIMIT 200`);
      }
      if (db && changeCursor === null) changeCursor = readUpdateWatermark(db);
      const signature = JSON.stringify(status);
      if (signature !== statusSignature || reportedError) {
        if (write('receiver-status', status)) { statusSignature = signature; reportedError = false; }
      }
      if (db && !blocked) {
        const cutoff = now - retentionDays * 86400000;
        const rows = queries.messages.all(cursor, cutoff);
        if (rows.length) {
          const events = rows.map(row => ({ id: row.id, received_at: row.received_at, message: JSON.parse(row.payload) }));
          const next = rows.at(-1).id;
          if (write('messages', { events, next_after_id: next, next_after_update_id: changeCursor, source: 'wecom-application-callback' }, `${next}:${changeCursor}`)) cursor = next;
        }
        if (!blocked && queries.updates) {
          const updates = queries.updates.all(cursor, cutoff, changeCursor);
          if (updates.length) {
            const latest = new Map();
            for (const row of updates) if (row.id !== null) latest.set(row.id, row);
            const events = [...latest.values()].map(row => ({ id: row.id, received_at: row.received_at, message: JSON.parse(row.payload) }));
            const next = updates.at(-1).seq;
            if (write('message-updates', { events, next_after_update_id: next }, `${cursor}:${next}`)) changeCursor = next;
          }
        }
      }
      if (now - lastHeartbeat > 10000 && !blocked) { blocked = !res.write(': heartbeat\n\n'); lastHeartbeat = now; }
    } catch {
      if (!reportedError && write('receiver-error', { error: '消息数据库暂时不可读，正在重试' })) reportedError = true;
    }
  };
  res.on('drain', () => { blocked = false; });
  const timer = setInterval(tick, intervalMs).unref();
  const close = () => { if (closed) return; closed = true; clearInterval(timer); };
  res.on('close', close); req.on('error', close);
  tick();
  return () => { close(); res.end(); };
}
