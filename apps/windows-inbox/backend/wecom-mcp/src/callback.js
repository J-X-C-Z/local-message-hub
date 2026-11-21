import http from 'node:http';
import { createHash } from 'node:crypto';
import { XMLParser, XMLValidator } from 'fast-xml-parser';
import { decodeAesKey, decryptCallback, verifySignature } from './crypto.js';

const parser = new XMLParser({ ignoreAttributes: true, parseTagValue: false, trimValues: false, processEntities: true });

function parseXml(text) {
  if (/<!DOCTYPE|<!ENTITY/i.test(text) || XMLValidator.validate(text) !== true) throw new Error('XML 无效');
  const value = parser.parse(text)?.xml;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('XML 根节点无效');
  return value;
}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error('回调超过 1 MiB');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

export function createCallbackServer(config, store) {
  if (!config.callbackToken) throw new Error('请配置 WECOM_CALLBACK_TOKEN');
  decodeAesKey(config.callbackAesKey);
  const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname !== config.callbackPath) { res.writeHead(404).end('not found'); return; }
    if (!['GET', 'POST'].includes(req.method)) { res.writeHead(405, { Allow: 'GET, POST' }).end('method not allowed'); return; }
    try {
      const signature = url.searchParams.get('msg_signature') || '';
      const timestamp = url.searchParams.get('timestamp') || '';
      const nonce = url.searchParams.get('nonce') || '';
      if (!/^\d{1,12}$/.test(timestamp) || !nonce || nonce.length > 256) throw new Error('参数无效');
      // 接受平台近期重试；超出 10 分钟的请求拒绝重放。
      if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 600) throw new Error('回调已过期');
      const encrypted = req.method === 'GET' ? url.searchParams.get('echostr') : parseXml(await readBody(req)).Encrypt;
      if (typeof encrypted !== 'string') throw new Error('缺少密文');
      verifySignature(config.callbackToken, timestamp, nonce, encrypted, signature);
      const plain = decryptCallback(encrypted, config.callbackAesKey, config.corpId);
      if (req.method === 'GET') { store.setReceiverState?.('last_verified_at', Date.now()); res.end(plain); return; }
      const message = parseXml(plain);
      if (message.ToUserName !== config.corpId) throw new Error('消息企业 ID 不匹配');
      // 不同应用可共用同一数据库，去重键包含应用标识。
      const agent = String(message.AgentID || config.agentId);
      if (message.AgentID !== undefined && agent !== String(config.agentId)) throw new Error('消息应用 ID 不匹配');
      const dedupKey = message.MsgId
        ? `${config.corpId}:${agent}:msg:${message.MsgId}`
        : `${config.corpId}:${agent}:event:${createHash('sha256').update(plain).digest('hex')}`;
      store.add(dedupKey, message);
      store.setReceiverState?.('last_callback_at', Date.now());
      res.end('success');
    } catch {
      // 不输出明文、签名、回调 Token 或解密细节。
      res.writeHead(400).end('invalid callback');
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  return server;
}
