import { createDecipheriv, createHash, timingSafeEqual } from 'node:crypto';

export function callbackSignature(token, timestamp, nonce, encrypted) {
  return createHash('sha1').update([token, timestamp, nonce, encrypted].sort().join('')).digest('hex');
}

export function verifySignature(token, timestamp, nonce, encrypted, signature) {
  if (!/^[a-f0-9]{40}$/i.test(signature || '')) throw new Error('回调签名无效');
  const expected = Buffer.from(callbackSignature(token, timestamp, nonce, encrypted), 'hex');
  if (!timingSafeEqual(expected, Buffer.from(signature, 'hex'))) throw new Error('回调签名无效');
}

export function decodeAesKey(encoded) {
  if (!/^[A-Za-z0-9+/]{43}$/.test(encoded)) throw new Error('EncodingAESKey 必须为 43 位 Base64 字符');
  const key = Buffer.from(encoded + '=', 'base64');
  if (key.length !== 32) throw new Error('EncodingAESKey 长度无效');
  return key;
}

export function decryptCallback(encrypted, encodedKey, corpId) {
  const key = decodeAesKey(encodedKey);
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(encrypted) || encrypted.length % 4 !== 0) throw new Error('回调密文格式无效');
  const ciphertext = Buffer.from(encrypted, 'base64');
  if (ciphertext.length === 0 || ciphertext.length % 16 !== 0) throw new Error('回调密文长度无效');
  const decipher = createDecipheriv('aes-256-cbc', key, key.subarray(0, 16));
  decipher.setAutoPadding(false);
  const padded = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  const pad = padded.at(-1);
  if (pad < 1 || pad > 32 || padded.length < pad || !padded.subarray(-pad).every(x => x === pad)) throw new Error('回调填充无效');
  const plain = padded.subarray(0, -pad);
  if (plain.length < 20) throw new Error('回调明文长度无效');
  const length = plain.readUInt32BE(16);
  if (length > plain.length - 20) throw new Error('回调消息长度无效');
  const receiver = plain.subarray(20 + length).toString('utf8');
  if (receiver !== corpId) throw new Error('回调企业 ID 不匹配');
  return plain.subarray(20, 20 + length).toString('utf8');
}
