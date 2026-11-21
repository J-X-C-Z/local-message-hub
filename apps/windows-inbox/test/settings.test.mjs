import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {validateSettings}=createRequire(import.meta.url)('../desktop/settings-schema.cjs');
const base={corpId:'wwtest',agentId:'42',callbackToken:'testtoken',callbackAesKey:'a'.repeat(43)};
test('仅接收配置无需应用 Secret，并固定回环地址及参数边界',()=>{
 const config=validateSettings(base);assert.equal(config.secret,'');assert.equal(config.callbackPort,8787);assert.equal(config.callbackHost,'127.0.0.1');
 assert.throws(()=>validateSettings({...base,callbackHost:'0.0.0.0'}));
 assert.throws(()=>validateSettings({...base,callbackPort:0}));
 assert.throws(()=>validateSettings({...base,agentId:'x'}));
 assert.throws(()=>validateSettings({...base,callbackAesKey:'bad'}));
 assert.throws(()=>validateSettings({...base,retentionDays:0}));
 assert.throws(()=>validateSettings({...base,enableWrites:true}));
});
