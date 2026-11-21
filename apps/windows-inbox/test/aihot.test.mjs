import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AIHOT_HOME,readerAddress} from '../desktop/aihot-desktop.cjs';

test('the reader accepts HTTPS news and original sources',()=>{
 for(const address of [AIHOT_HOME,'https://aihot.news/items/example','https://example.org/article?a=1#section','https://mp.weixin.qq.com/s/news']){
  assert.equal(readerAddress(address)?.href,address);
 }
});

test('the reader refuses non-web links, credentials, IP literals and common local addresses',()=>{
 for(const address of [null,42,'not a link','file:///C:/temp/example.html','mailto:hello@example.org','weixin://article',
  'wecom-inbox://workbench/','javascript:alert(1)','http://example.org','https://example.org:444/',
  'https://user:pass@example.org/','https://localhost/','https://machine.local/','https://box.lan./','https://127.0.0.1/',
  'https://2130706433/','https://[::1]/','https://[2001:db8::1]/'])assert.equal(readerAddress(address),null,String(address));
});
