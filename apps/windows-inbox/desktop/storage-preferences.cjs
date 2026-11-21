const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

function attachmentMode(directory) {
  try {
    const preferences = JSON.parse(fs.readFileSync(path.join(directory, 'storage-preferences.json'), 'utf8'));
    if (preferences.attachmentMode === 'offline') return 'offline';
  } catch {}
  return 'on_demand';
}

function setAttachmentMode(directory, mode) {
  if (!['on_demand', 'offline'].includes(mode)) throw new Error('附件保存方式无效');
  fs.mkdirSync(directory, {recursive: true});
  const filename = path.join(directory, 'storage-preferences.json');
  const temporary = path.join(directory, `.storage-${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temporary, JSON.stringify({attachmentMode: mode}), {flag: 'wx'});
    fs.renameSync(temporary, filename);
  } finally {
    fs.rmSync(temporary, {force: true});
  }
}

module.exports = { attachmentMode, setAttachmentMode };
