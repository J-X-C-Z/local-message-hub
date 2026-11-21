import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { existsSync, readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import {timingSafeEqual} from 'node:crypto';
import { LiveIntegration, readIntegrationConfig, loadLocalEnvironment, mcpDirectory } from './integration.mjs';
import { openMessageStream, readUpdateWatermark } from './stream.mjs';
import {serveLocalMedia,serveLocalTrack} from '../local-media.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
const bundledPublicDir = path.join(directory, 'dist');
const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs':'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.png':'image/png' };
const publicFiles = new Set(['/', '/index.html', '/style.css', '/inbox-layout.css', '/app.js', '/pane-resizer.js', '/view-performance.mjs', '/attachments-ui.js', '/integration-ui.js', '/integration.css']);
publicFiles.add('/rich-ui.js');publicFiles.add('/emoji-map.js');

function openDatabase(dbPath) {
  if (!existsSync(dbPath)) return null;
  let db;
  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    db.exec('PRAGMA busy_timeout=3000;');
    db.prepare('SELECT id, received_at, sender, msg_type, payload FROM events LIMIT 0').all();
    return db;
  } catch { db?.close(); return null; }
}

const toEvents = rows => rows.map(row => ({ id: row.id, received_at: row.received_at, message: JSON.parse(row.payload) }));

function readInboxPreview(db, cutoff) {
  // The preview, history boundary and revision boundary must share a snapshot.
  db.exec('BEGIN');
  try {
    const newest = db.prepare('SELECT COALESCE(MAX(id),0) AS id FROM events').get().id;
    const updates = readUpdateWatermark(db);
    const rows = db.prepare(`WITH chat AS MATERIALIZED (
      SELECT id, COALESCE(json_extract(payload,'$.Platform')='wechat',0) AS wechat,
        COALESCE(json_extract(payload,'$.ConversationType')='official',0) AS official,
        COALESCE('conversation:'||NULLIF(json_extract(payload,'$.ConversationId'),''),'sender:'||sender) AS room,
        COALESCE(NULLIF(CAST(json_extract(payload,'$.CreateTime') AS REAL),0),received_at/1000.0) AS sent,
        COALESCE(json_extract(payload,'$.System')=1,0) AS system
      FROM events WHERE received_at>=?
    ), ranked AS MATERIALIZED (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY wechat,room ORDER BY system,sent DESC,id DESC) AS summary_rank
      FROM chat
    ), leading AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY wechat,official ORDER BY system,sent DESC,id DESC) AS rank_in_section
      FROM ranked WHERE summary_rank=1
    ), recent AS (
      SELECT chat.id,ROW_NUMBER() OVER(PARTITION BY chat.wechat,chat.room ORDER BY chat.sent DESC,chat.id DESC) AS rank
      FROM leading top JOIN chat ON chat.wechat=top.wechat AND chat.room=top.room WHERE top.rank_in_section=1
    ), selected AS (
      SELECT id FROM ranked WHERE summary_rank=1
      UNION
      SELECT id FROM recent WHERE rank<=100
    ) SELECT e.id,e.received_at,e.payload FROM selected JOIN events e USING(id) ORDER BY e.id`).all(cutoff);
    db.exec('COMMIT');
    return {events:toEvents(rows), history_through_id:newest, update_watermark:updates};
  } catch(error) { db.exec('ROLLBACK'); throw error; }
}

export function createWorkbenchServer({ dbPath = path.resolve(directory, '../wecom-mcp/data/wecom.db'), snapshotPath = path.join(directory, 'data/client-snapshot.json'), publicDir = bundledPublicDir, port = 8790, retentionDays = 30, integration = new LiveIntegration(readIntegrationConfig()), accessToken = '', onPreview } = {}) {
  let db = null;
  const streams = new Set();
  const assetCache = new Map();
  if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 3650) throw new Error('保留天数必须为 1 到 3650');
  const server = http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Content-Security-Policy', "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
    const json = (status, data) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(data)); };
    if(accessToken){
      const supplied=Buffer.from(String(req.headers['x-workbench-key']||''));const expected=Buffer.from(accessToken);
      if(supplied.length!==expected.length||!timingSafeEqual(supplied,expected)){json(403,{error:'仅允许桌面应用访问'});return;}
    }
    // 本地只读工作台：拒绝跨源请求与非本机 Host，防止第三方网页读取回调数据。
    const activePort = server.address()?.port || port;
    const permittedHosts = new Set([`127.0.0.1:${activePort}`, `localhost:${activePort}`]);
    if (!permittedHosts.has(req.headers.host)) { json(403, { error: '仅允许本地工作台地址' }); return; }
    if (req.headers.origin && ![`http://127.0.0.1:${activePort}`, `http://localhost:${activePort}`].includes(req.headers.origin)) { json(403, { error: '不允许跨源访问' }); return; }
    if (req.headers['sec-fetch-site'] === 'cross-site') { json(403, { error: '不允许跨站访问' }); return; }
    if (req.method !== 'GET' && req.method !== 'HEAD') { json(405, { error: '只读服务' }); return; }
    let url;
    try { url = new URL(req.url, `http://127.0.0.1:${port}`); } catch { json(400, { error: 'URL 无效' }); return; }
    if (url.pathname.startsWith('/api/')) {
      if(url.pathname==='/api/media'){await serveLocalMedia(req,res,{directory:path.dirname(dbPath),id:url.searchParams.get('id')});return;}
      if(url.pathname==='/api/video'||url.pathname==='/api/audio'){await serveLocalTrack(req,res,{directory:path.dirname(dbPath),id:url.searchParams.get('id'),kind:url.pathname.slice(5)});return;}
      db ||= openDatabase(dbPath);
      if (url.pathname === '/api/wechat/avatars' || url.pathname === '/api/wechat/moments') {
        if (!accessToken) { json(403, { error: '请在桌面收件台查看' }); return; }
        try {
          const row = db?.prepare("SELECT value FROM receiver_state WHERE key='wechat_social'").get();
          const social = row ? JSON.parse(row.value) : null;
          if (url.pathname.endsWith('/avatars')) { json(200, { avatars: social?.avatars ?? [] }); return; }
          const offset = Number(url.searchParams.get('offset') ?? 0);
          if (!Number.isSafeInteger(offset) || offset < 0 || offset > 500) { json(400, { error: '分页参数无效' }); return; }
          const posts = social?.posts;
          json(200, { ready: Array.isArray(posts), posts: posts?.slice(offset, offset + 12) ?? [], next_offset: offset + 12 < (posts?.length ?? 0) ? offset + 12 : null });
        } catch { json(503, { error: '本机缓存暂时不可读' }); }
        return;
      }
      if (url.pathname === '/api/status') { json(200, { local_backend: true, source:db?.prepare("SELECT value FROM receiver_state WHERE key='source'").get()?.value||'callback', snapshot_available: existsSync(snapshotPath), live_available: Boolean(db), api_configured: integration.config.configured, checklist: integration.config.checklist, mode: 'receive-only', transport: 'sse', database_scan_ms: 500, retention_days: retentionDays, archive_available: false }); return; }
      if(url.pathname==='/api/local-groups'){
        const rows=db?.prepare("SELECT payload FROM events WHERE received_at >= ? ORDER BY id").all(Date.now()-retentionDays*86400000)||[];
        const groups=new Map();
        for(const row of rows){const m=JSON.parse(row.payload);if(m.Source!=='local-database'||m.ConversationType!=='group'||(url.searchParams.get('platform')&&((m.Platform==='wechat'?'wechat':'wecom')!==url.searchParams.get('platform'))))continue;let group=groups.get(m.ConversationId);if(!group){group={id:m.ConversationId,name:m.ConversationName,messages:0};groups.set(group.id,group);}group.messages++;}
        json(200,{groups:[...groups.values()]});return;
      }
      if (url.pathname === '/api/stream') {
        const resume = req.headers['last-event-id'];
        const cursor = resume ? /^(\d+)(?::(\d+))?$/.exec(resume) : null;
        const afterId = Number(cursor?.[1] ?? url.searchParams.get('after_id') ?? 0);
        const rawAfterUpdate = cursor?.[2] ?? url.searchParams.get('after_update_id');
        const afterUpdateId = rawAfterUpdate === null ? null : Number(rawAfterUpdate);
        if ((resume && !cursor) || !Number.isSafeInteger(afterId) || afterId < 0 || (afterUpdateId !== null && (!Number.isSafeInteger(afterUpdateId) || afterUpdateId < 0))) { json(400, { error: '消息游标无效' }); return; }
        if (req.method !== 'GET') { json(405, { error: '流仅支持 GET' }); return; }
        if (streams.size >= 32) { json(429, { error: '本机订阅连接过多' }); return; }
        const close = openMessageStream(req, res, { getDatabase: () => { db ||= openDatabase(dbPath); return db; }, afterId, afterUpdateId, retentionDays });
        streams.add(close); res.on('close', () => streams.delete(close)); return;
      }
      if (url.pathname === '/api/client-snapshot') {
        try {
          if (!existsSync(snapshotPath)) { json(404, { error: '尚无客户端快照' }); return; }
          const snapshot = JSON.parse(readFileSync(snapshotPath, 'utf8'));
          if (snapshot.source !== 'desktop-ui-snapshot' || !Array.isArray(snapshot.conversations)) throw new Error('无效快照');
          json(200, snapshot);
        } catch { json(500, { error: '本地客户端快照读取失败' }); }
        return;
      }
      if (['/api/verify', '/api/groups', '/api/group'].includes(url.pathname)) {
        try {
          if (url.pathname === '/api/verify') { json(200, await integration.verify()); return; }
          const type = url.searchParams.get('type') || 'customer';
          const cursor = url.searchParams.get('cursor') || '';
          const owner = url.searchParams.get('owner') || '';
          const id = url.searchParams.get('id') || '';
          if (!['customer', 'app'].includes(type) || cursor.length > 4096 || owner.length > 256 || id.length > 256 || /[|\s]/.test(owner) || (url.pathname === '/api/group' && !id)) { json(400, { error: '群组查询参数无效' }); return; }
          json(200, url.pathname === '/api/groups' ? await integration.groups(type, cursor, owner) : await integration.group(type, id));
        } catch (error) { json(integration.config.configured ? 502 : 503, { error: error.message }); }
        return;
      }
      if (url.pathname === '/api/inbox-preview') {
        if (!db) {json(503,{error:'本机数据库尚未就绪'});return;}
        try {
          const preview=readInboxPreview(db,Date.now()-retentionDays*86400000);
          if(onPreview&&req.method==='GET')res.once('finish',onPreview);
          json(200,preview);
        }
        catch { json(500,{error:'会话预览读取失败'}); }
        return;
      }
      if (url.pathname !== '/api/messages') { json(404, { error: '接口不存在' }); return; }
      if (!db) { json(503, { error: '回调数据库尚未就绪' }); return; }
      const afterId = Number(url.searchParams.get('after_id') || 0);
      const limit = Number(url.searchParams.get('limit') || 200);
      const recent = url.searchParams.get('recent') === '1';
      const through = url.searchParams.has('through_id') ? Number(url.searchParams.get('through_id')) : Number.MAX_SAFE_INTEGER;
      const history = through !== Number.MAX_SAFE_INTEGER;
      if (!Number.isSafeInteger(afterId) || afterId < 0 || !Number.isSafeInteger(through) || through < 0 || !Number.isInteger(limit) || limit < 1 || limit > (history?600:200)) { json(400, { error: '分页参数无效' }); return; }
      try {
        const cutoff = Date.now() - retentionDays * 86400000;
        const updateWatermark = recent || url.searchParams.get('include_update_watermark') === '1' ? readUpdateWatermark(db) : undefined;
        let effectiveAfter = afterId;
        if (recent) {
          const oldest = db.prepare('SELECT id FROM events WHERE received_at >= ? ORDER BY id DESC LIMIT 1 OFFSET 9999').get(cutoff);
          if (oldest) effectiveAfter = Math.max(effectiveAfter, oldest.id - 1);
        }
        const rows = db.prepare('SELECT id, received_at, payload FROM events WHERE id > ? AND id <= ? AND received_at >= ? ORDER BY id LIMIT ?').all(effectiveAfter, through, cutoff, limit + 1);
        const page = rows.slice(0, limit);
        json(200, {
          events: toEvents(page),
          next_after_id: page.at(-1)?.id ?? effectiveAfter,
          update_watermark: updateWatermark,
          has_more: rows.length > limit,
          source: 'wecom-application-callback',
        });
      } catch { json(500, { error: '数据库读取失败，请检查文件是否为 MCP 回调数据库' }); }
      return;
    }
    if (!publicFiles.has(url.pathname)&&!/^\/emoji\/[a-f0-9]{16}\.png$/.test(url.pathname)) { res.writeHead(404).end('not found'); return; }
    const filename = path.join(publicDir, url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
    try {
      let content=assetCache.get(filename);
      if(!content){content=readFileSync(filename);assetCache.set(filename,content);}
      res.writeHead(200, { 'Content-Type': MIME[path.extname(filename)] || 'application/octet-stream', 'Content-Length': content.length });
      res.end(req.method === 'HEAD' ? undefined : content);
    } catch { res.writeHead(404).end('not found'); }
  });
  server.on('close', () => db?.close());
  server.closeStreams = () => { for (const close of streams) close(); streams.clear(); };
  server.requestTimeout = 10000;
  server.headersTimeout = 8000;
  return server;
}
