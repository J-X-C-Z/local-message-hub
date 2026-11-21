import path from 'node:path';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { WecomClient } from '../wecom-mcp/src/client.js';

export const mcpDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../wecom-mcp');
export function readIntegrationConfig(env = process.env) {
  const has = key => Boolean(env[key]?.trim() && !/replace_me|你的/.test(env[key]));
  const configured = has('WECOM_CORP_ID') && has('WECOM_CORP_SECRET') && /^\d+$/.test(env.WECOM_AGENT_ID || '') && Number(env.WECOM_AGENT_ID) > 0;
  const appChatIds = [...new Set((env.WECOM_APP_CHAT_IDS || '').split(',').map(s => s.trim()).filter(Boolean))];
  return {
    configured,
    corpId: env.WECOM_CORP_ID,
    secret: env.WECOM_CORP_SECRET,
    agentId: Number(env.WECOM_AGENT_ID),
    contactSecret: env.WECOM_CONTACT_SECRET || env.WECOM_CORP_SECRET,
    externalSecret: env.WECOM_EXTERNAL_CONTACT_SECRET || env.WECOM_CORP_SECRET,
    appChatIds,
    checklist: {
      corp_id: has('WECOM_CORP_ID'), agent_id: /^\d+$/.test(env.WECOM_AGENT_ID || '') && Number(env.WECOM_AGENT_ID) > 0,
      app_secret: has('WECOM_CORP_SECRET'),
      callback_token: has('WECOM_CALLBACK_TOKEN'),
      callback_aes_key: /^[A-Za-z0-9+/]{43}$/.test(env.WECOM_CALLBACK_AES_KEY || ''),
      external_secret: has('WECOM_EXTERNAL_CONTACT_SECRET'),
      app_group_ids: appChatIds.length,
    },
  };
}

export function loadLocalEnvironment() {
  const filename = path.join(mcpDirectory, '.env');
  if (existsSync(filename)) process.loadEnvFile(filename);
}

export class LiveIntegration {
  constructor(config, client = config.configured ? new WecomClient(config) : null) {
    this.config = config;
    this.client = client;
  }
  requireClient() {
    if (!this.client) throw new Error('尚未配置企业 ID、AgentId 与自建应用 Secret，请先运行本地配置脚本');
    return this.client;
  }
  async verify() {
    const data = await this.requireClient().request('agent/get', { query: { agentid: this.config.agentId } });
    return { connected: true, agent_id: data.agentid, name: data.name || '企业微信应用', scope: '当前应用权限范围' };
  }
  async groups(type, cursor = '', owner = '') {
    const client = this.requireClient();
    if (type === 'customer') {
      const result = await client.request('externalcontact/groupchat/list', { scope: 'external', method: 'POST', body: {
        status_filter: 0, limit: 100, ...(cursor ? { cursor } : {}), ...(owner ? { owner_filter: { userid_list: [owner] } } : {}),
      } });
      return { groups: (result.group_chat_list || []).map(group => ({ id: group.chat_id, name: group.chat_id, status: group.status, type })), next_cursor: result.next_cursor || '', source: '客户群官方接口', messages_available: false };
    }
    if (type !== 'app') throw new Error('群组类型无效');
    const groups = await Promise.all(this.config.appChatIds.map(async id => {
      try {
        const result = await client.request('appchat/get', { query: { chatid: id } });
        const group = result.chat_info || {};
        return { id, name: group.name || id, owner: group.owner, member_count: group.userlist?.length || 0, type };
      } catch (error) { return { id, name: id, type, error: error.message }; }
    }));
    return { groups, next_cursor: '', source: '配置的应用群 ID', messages_available: false };
  }
  async group(type, id) {
    const client = this.requireClient();
    if (type === 'app') {
      if (!this.config.appChatIds.includes(id)) throw new Error('该群不在已配置的应用群 ID 列表中');
      const result = await client.request('appchat/get', { query: { chatid: id } });
      const info = result.chat_info || {};
      return { id, name: info.name || id, owner: info.owner, type,
        members: (info.userlist || []).map(userid => ({ userid, name: userid })),
        message_note: '应用群聊 API 仅提供群信息与主动发送，无法读取群内消息。' };
    }
    if (type !== 'customer') throw new Error('群组类型无效');
    const result = await client.request('externalcontact/groupchat/get', { scope: 'external', method: 'POST', body: { chat_id: id, need_name: 1 } });
    const info = result.group_chat || {};
    return { id, name: info.name || id, owner: info.owner, notice: info.notice || '', type,
      members: (info.member_list || []).map(member => ({ userid: member.userid, name: member.name || member.userid, type: member.type, join_time: member.join_time })),
      message_note: '客户群接口提供群资料与成员，不返回群聊天记录；聊天记录需会话内容存档。' };
  }
}
