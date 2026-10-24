const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  URL, URLSearchParams,
  localStorage: {getItem: () => 'light'},
  location: {hash: ''},
  document: {addEventListener() {}, querySelector: () => null},
  window: {addEventListener() {}},
  matchMedia: () => ({addEventListener() {}}),
  setInterval: () => 1,
  clearInterval() {}
};
vm.runInNewContext(source.replace('\ninit();\n', `
globalThis.__test = {state, views, init};
setTheme = () => {};
renderShell = () => {globalThis.renderedViews.push(state.view);};
loadView = async () => {globalThis.loadedViews.push(state.view);};
refreshSources = async () => {};
api = async () => ({});
syncNotificationStream = () => {};
`), context);

(async () => {
  const {state, views, init} = context.__test;
  const cases = [...Object.keys(views).map(view => [`#${view}`, view]),
    ['', 'chat'], ['#unknown', 'chat'], ['#constructor', 'chat'], ['#qq-login', 'sources'],
    ['#campus/daily', 'campus'], ['#campus/sources', 'campus'], ['#campus/constructor', 'chat']];
  for (const [hash, expected] of cases) {
    context.location.hash = hash;
    context.renderedViews = [];context.loadedViews = [];
    state.view = 'settings';
    await init();
    assert.equal(state.view, expected, hash);
    assert.ok(context.renderedViews.every(view => view === expected), hash);
    assert.deepEqual(Array.from(context.loadedViews), [expected], hash);
    if (hash === '#campus/daily') assert.equal(state.campus.section, 'daily');
  }
  assert.doesNotMatch(source, /\/api\/qq\/(?:start|login-status|history-import)/);
  console.log('PASS supported page deep links, reload, unknown hash fallback, and legacy QQ route without login');
})().catch(error => {console.error(error);process.exitCode = 1;});
