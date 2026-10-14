const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  Intl,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}},
  window: {addEventListener() {}},
  matchMedia: () => ({addEventListener() {}}),
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__render = renderSelfSentDailyChart;\n'), context);

const days = Array.from({length: 37}, (_, index) => ({
  date: new Date(Date.UTC(2024, 0, 1 + index)).toISOString().slice(0, 10),
  count: index === 36 ? 2 : index === 35 ? 1 : 0,
}));
const markup = context.__render({baseline: 1, total_messages: 3, items: days});
assert.match(markup, /最近 31 天/);
assert.match(markup, /2024-01-07/);
assert.match(markup, /2024-02-06/);
assert.match(markup, /200\.0%/);
assert.equal((markup.match(/<rect /g) || []).length, 31);
assert.equal((markup.match(/\d+\.\d+,\d+\.\d+/g) || []).length, 31);
assert.doesNotMatch(markup, /sentinel|private message body/i);
console.log('PASS daily self-send chart bounds recent days and renders baseline/trend without content');
