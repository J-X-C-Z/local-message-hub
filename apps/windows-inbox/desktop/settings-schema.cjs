const allowed = new Set(['corpId','agentId','callbackToken','callbackAesKey','callbackPort','callbackHost','retentionDays','secret','externalSecret','appChatIds']);
function validateSettings(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('配置格式无效');
  for (const key of Object.keys(input)) if (!allowed.has(key)) throw new Error('未知配置字段');
  const text = (key, max=256) => {
    const value = input[key] === undefined ? '' : input[key];
    if (typeof value !== 'string' || value.length>max || /[\r\n\0]/.test(value)) throw new Error(`${key} 字段无效`);
    return value.trim();
  };
  const corpId=text('corpId'); const agentId=text('agentId');
  const callbackToken=text('callbackToken'); const callbackAesKey=text('callbackAesKey');
  if (!/^[a-zA-Z0-9_-]{2,128}$/.test(corpId)) throw new Error('请填写有效的企业 CorpID');
  if (!/^[1-9]\d{0,12}$/.test(agentId) || !Number.isSafeInteger(Number(agentId))) throw new Error('AgentId 必须为有效正整数');
  if (!/^[a-zA-Z0-9]{1,32}$/.test(callbackToken)) throw new Error('回调 Token 需为 1–32 位英文字母或数字');
  if (!/^[A-Za-z0-9+/]{43}$/.test(callbackAesKey)) throw new Error('EncodingAESKey 必须为 43 位 Base64 字符');
  const callbackHost=text('callbackHost') || '127.0.0.1';
  if (callbackHost !== '127.0.0.1') throw new Error('本地应用只允许回环监听地址');
  const callbackPort=Number(input.callbackPort ?? 8787);
  if (!Number.isInteger(callbackPort) || callbackPort<1024 || callbackPort>65535) throw new Error('接收端口须为 1024–65535');
  const retentionDays=Number(input.retentionDays ?? 30);
  if (!Number.isInteger(retentionDays) || retentionDays<1 || retentionDays>3650) throw new Error('消息保留期须为 1–3650 天');
  const secret=text('secret',512);const externalSecret=text('externalSecret',512);const appChatIds=text('appChatIds',10000);
  if(appChatIds && !/^[A-Za-z0-9_, -]+$/.test(appChatIds)) throw new Error('应用群 ID 只允许字母、数字、下划线、连字符及英文逗号');
  return {corpId,agentId,callbackToken,callbackAesKey,callbackPort,callbackHost,retentionDays,secret,externalSecret,appChatIds};
}
module.exports={validateSettings};
