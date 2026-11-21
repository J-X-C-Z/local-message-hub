import path from 'node:path';

function flag(value, fallback = false) {
  if (value === undefined || value === '') return fallback;
  if (!['true', 'false'].includes(value)) throw new Error('布尔环境变量必须为 true 或 false');
  return value === 'true';
}

export function loadConfig(env = process.env, { receiveOnly = false } = {}) {
  const corpId = env.WECOM_CORP_ID;
  const secret = env.WECOM_CORP_SECRET;
  if (!corpId || corpId.includes('replace') || (!receiveOnly && (!secret || secret === 'replace_me'))) {
    throw new Error(receiveOnly ? '请配置真实的 WECOM_CORP_ID' : '请配置真实的 WECOM_CORP_ID 与 WECOM_CORP_SECRET');
  }
  const agentId = Number(env.WECOM_AGENT_ID);
  if (!Number.isSafeInteger(agentId) || agentId <= 0) throw new Error('WECOM_AGENT_ID 必须为正整数');
  const port = Number(env.WECOM_CALLBACK_PORT || 8787);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('回调端口无效');
  const retentionDays = Number(env.WECOM_EVENT_RETENTION_DAYS || 30);
  if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 3650) {
    throw new Error('WECOM_EVENT_RETENTION_DAYS 必须为 1 到 3650');
  }
  const callbackPath = env.WECOM_CALLBACK_PATH || '/wecom/callback';
  if (!/^\/[a-zA-Z0-9/_-]+$/.test(callbackPath)) throw new Error('回调路径无效');
  return {
    corpId, secret, agentId,
    contactSecret: env.WECOM_CONTACT_SECRET || secret,
    externalSecret: env.WECOM_EXTERNAL_CONTACT_SECRET || secret,
    writes: !receiveOnly && flag(env.WECOM_ENABLE_WRITES),
    broadcast: flag(env.WECOM_ALLOW_BROADCAST),
    dbPath: path.resolve(env.WECOM_DB_PATH || './data/wecom.db'),
    callbackToken: env.WECOM_CALLBACK_TOKEN || '',
    callbackAesKey: env.WECOM_CALLBACK_AES_KEY || '',
    callbackHost: env.WECOM_CALLBACK_HOST || '127.0.0.1',
    callbackPort: port, callbackPath, retentionDays,
  };
}
