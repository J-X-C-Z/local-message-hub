import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitUntil(check, timeoutMs = 12_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return false;
}

function hasHealthyApi(port) {
  return new Promise((resolve) => {
    const request = http.get({ host: '127.0.0.1', port, path: '/health', agent: false }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.once('error', () => resolve(false));
  });
}

test('QCE restarts once after an unexpected child exit and stops cleanly', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qce-restart-'));
  const bootstrap = path.join(tmp, 'fake-qce.cjs');
  const counter = path.join(tmp, 'starts');
  const apiPort = await freePort();
  const bridgePort = await freePort();
  const keys = ['QCE_RUST_SERVER_PATH', 'QCE_SERVER_PORT', 'QCE_BRIDGE_PORT',
    'QCE_CONFIG_DIR', 'QCE_LOG_FILE', 'QCE_NO_AUTO_OPEN', 'LMH_QCE_FAKE_MODE',
    'LMH_QCE_FAKE_STARTS', 'NODE_OPTIONS'];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  const originalLog = console.log;
  let server;
  try {
    fs.writeFileSync(bootstrap, `
      if (process.env.LMH_QCE_FAKE_MODE === '1') {
        const fs = require('node:fs');
        const http = require('node:http');
        const counterPath = process.env.LMH_QCE_FAKE_STARTS;
        const starts = Number(fs.readFileSync(counterPath, 'utf8'));
        fs.writeFileSync(counterPath, String(starts + 1));
        const server = http.createServer((_request, response) => response.end('ok'));
        server.listen(Number(process.env.QCE_SERVER_PORT), '127.0.0.1', () => {
          if (starts === 0) setTimeout(() => process.exit(71), 150);
        });
      }
    `);
    fs.writeFileSync(counter, '0');
    fs.writeFileSync(path.join(tmp, 'security.json'), JSON.stringify({ accessToken: 'synthetic-only' }));
    process.env.QCE_RUST_SERVER_PATH = process.execPath;
    process.env.QCE_SERVER_PORT = String(apiPort);
    process.env.QCE_BRIDGE_PORT = String(bridgePort);
    process.env.QCE_CONFIG_DIR = tmp;
    process.env.QCE_LOG_FILE = path.join(tmp, 'runtime.log');
    process.env.QCE_NO_AUTO_OPEN = '1';
    process.env.LMH_QCE_FAKE_MODE = '1';
    process.env.LMH_QCE_FAKE_STARTS = counter;
    process.env.NODE_OPTIONS = [previous.NODE_OPTIONS, `--require=${JSON.stringify(bootstrap)}`]
      .filter(Boolean).join(' ');

    console.log = () => {};
    const { startRustApiServer } = await import(`../../runtime/rustBridge.mjs?restart-test=${Date.now()}`);
    const core = { context: { logger: { log() {}, logError() {} } } };
    server = await startRustApiServer(core, undefined);
    assert.equal(await waitUntil(async () => {
      if (Number(fs.readFileSync(counter, 'utf8')) < 2) return false;
      return hasHealthyApi(apiPort);
    }), true, 'QCE did not return after the first child exited');
    assert.equal(fs.readFileSync(counter, 'utf8'), '2');
  } finally {
    if (server) await server.stop();
    console.log = originalLog;
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
