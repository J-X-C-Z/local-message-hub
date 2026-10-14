const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'lock.js'), 'utf8');

function createPage(fetch) {
  let submit;
  const input = {value: 'synthetic passphrase', disabled: false, focused: false,
    focus() { this.focused = true; }};
  const button = {disabled: false, textContent: '解锁'};
  const attributes = new Map();
  const form = {
    addEventListener(name, handler) { assert.equal(name, 'submit'); submit = handler; },
    querySelector(selector) { return selector === 'input' ? input : button; },
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); },
  };
  const error = {textContent: ''};
  const location = {path: '', replace(value) { this.path = value; }};
  const document = {querySelector(selector) {
    return selector === '#unlock-form' ? form : selector === '#lock-error' ? error : null;
  }};
  class FormData {
    get(name) { return name === 'passphrase' && !input.disabled ? input.value : null; }
  }
  vm.runInNewContext(source, {document, window: {}, fetch, FormData, location});
  return {form, input, button, error, attributes, location, submit};
}

async function main() {
  let finishRequest;
  let requests = 0;
  let submittedPassphrase;
  const failed = createPage((_url, options) => {
    requests++;
    submittedPassphrase = JSON.parse(options.body).passphrase;
    return new Promise(resolve => { finishRequest = resolve; });
  });
  const event = {preventDefault() {}};
  const attempt = failed.submit(event);
  const repeatedAttempt = failed.submit(event);
  assert.equal(requests, 1, 'repeated Enter submits should not make parallel unlock requests');
  assert.equal(submittedPassphrase, 'synthetic passphrase', 'disabled form controls must not drop the unlock passphrase');
  assert.equal(failed.button.textContent, '正在验证…');
  assert.equal(failed.input.disabled, true);
  assert.equal(failed.attributes.get('aria-busy'), 'true');
  finishRequest({ok: false, json: async () => ({error: '口令错误'})});
  await Promise.all([attempt, repeatedAttempt]);
  assert.equal(failed.error.textContent, '口令错误');
  assert.equal(failed.input.value, '', 'failed credentials should be cleared');
  assert.equal(failed.input.focused, true);
  assert.equal(failed.input.disabled, false);
  assert.equal(failed.button.textContent, '解锁');
  assert.equal(failed.attributes.has('aria-busy'), false);

  const success = createPage(async () => ({ok: true}));
  await success.submit(event);
  assert.equal(success.location.path, '/');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
