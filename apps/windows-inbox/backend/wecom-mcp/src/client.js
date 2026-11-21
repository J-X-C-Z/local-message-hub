const BASE = 'https://qyapi.weixin.qq.com/cgi-bin/';
const TOKEN_ERRORS = new Set([40014, 42001]);

export class WecomError extends Error {
  constructor(code) {
    super(`企业微信 API 错误 ${code}；请按官方错误码检查权限、可信 IP、参数和应用可见范围。`);
    this.name = 'WecomError';
    this.code = code;
  }
}

export class WecomClient {
  constructor(config, { fetchImpl = fetch, now = Date.now } = {}) {
    this.config = config;
    this.fetch = fetchImpl;
    this.now = now;
    this.tokens = new Map();
    this.pending = new Map();
  }

  async json(url, options = {}) {
    let response;
    try {
      response = await this.fetch(url, { ...options, redirect: 'error', signal: AbortSignal.timeout(15000) });
    } catch {
      // 不返回可能携带 access_token / corpsecret 的 URL 或底层异常。
      throw new Error('企业微信请求失败或超时；请检查网络后再试。写入请求可能已生效，请先核实状态。');
    }
    if (!response.ok) throw new Error(`企业微信 HTTP 错误 ${response.status}；写入请求请先核实状态再重试。`);
    try { return await response.json(); } catch { throw new Error('企业微信返回了无效的 JSON'); }
  }

  async token(scope) {
    const cached = this.tokens.get(scope);
    if (cached && cached.expiresAt > this.now()) return cached.value;
    if (this.pending.has(scope)) return this.pending.get(scope);
    const pending = (async () => {
      const secret = scope === 'contact' ? this.config.contactSecret : scope === 'external' ? this.config.externalSecret : this.config.secret;
      const url = new URL('gettoken', BASE);
      url.search = new URLSearchParams({ corpid: this.config.corpId, corpsecret: secret });
      const data = await this.json(url);
      if (data.errcode) throw new WecomError(data.errcode);
      if (!data.access_token || !Number.isFinite(data.expires_in) || data.expires_in <= 0) throw new Error('企业微信 token 响应无效');
      this.tokens.set(scope, { value: data.access_token, expiresAt: this.now() + Math.max(1, data.expires_in - 120) * 1000 });
      return data.access_token;
    })();
    this.pending.set(scope, pending);
    try { return await pending; } finally { this.pending.delete(scope); }
  }

  async request(endpoint, { method = 'GET', query = {}, body, scope = 'app' } = {}, retried = false) {
    if (!/^[a-z_]+(?:\/[a-z_]+)+$/.test(endpoint)) throw new Error('非法 API 路径');
    const url = new URL(endpoint, BASE);
    const token = await this.token(scope);
    url.search = new URLSearchParams({ ...query, access_token: token });
    const data = await this.json(url, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    if (TOKEN_ERRORS.has(data.errcode) && !retried) {
      if (this.tokens.get(scope)?.value === token) this.tokens.delete(scope);
      return this.request(endpoint, { method, query, body, scope }, true);
    }
    if (data.errcode) throw new WecomError(data.errcode);
    // 凭证只存在服务端，任何工具返回都不暴露 access_token。
    const { access_token, ...result } = data;
    return result;
  }
}
