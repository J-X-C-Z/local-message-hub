const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const overlay = {innerHTML: ''};
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener: () => {},
    querySelector: selector => selector === '#overlay' ? overlay : null,
  },
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  setTimeout,
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__previewMedia = previewMedia;\n'), context);

context.__previewMedia('/local-media/fixture', 'voice', 'voice.wma');
assert.match(overlay.innerHTML, /<audio src="\/local-media\/fixture" controls autoplay><\/audio>/);
assert.match(overlay.innerHTML, /href="\/local-media\/fixture" download[^>]*>.*下载 voice\.wma/);

overlay.innerHTML = '';
context.__previewMedia('/local-media/fixture', 'voice', 'voice.mp3');
assert.match(overlay.innerHTML, /<audio src="\/local-media\/fixture" controls autoplay><\/audio>/);
assert.doesNotMatch(overlay.innerHTML, /download/);

overlay.innerHTML = '';
context.__previewMedia('/local-media/fixture', 'voice', 'voice.silk');
assert.doesNotMatch(overlay.innerHTML, /<audio/);
assert.match(overlay.innerHTML, /href="\/local-media\/fixture" download[^>]*>.*下载 voice\.silk/);

console.log('PASS WMA playback, SILK download fallback and MP3 playback');
