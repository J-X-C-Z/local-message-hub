"use strict";

// This is a synthetic DOM + HTTP protocol test, not a browser emulator.
const http = require("node:http");
const vm = require("node:vm");
const assert = require("node:assert/strict");

class Element {
  constructor(tag) {
    this.tag = tag;
    this.kids = [];
    this.events = {};
    this.value = "";
  }
  set textContent(value) { this.value = String(value); }
  get textContent() { return this.value; }
  set innerHTML(_) { throw new Error("not allowed"); }
  append(child) { this.kids.push(child); }
  replaceChildren() { this.kids = []; }
  addEventListener(name, fun) { this.events[name] = fun; }
}

function eventually(condition) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 6500;
    function check() {
      if (condition()) resolve();
      else if (Date.now() > deadline) reject(new Error("fixture not ready"));
      else setTimeout(check, 10);
    }
    check();
  });
}

async function run(input) {
  assert.match(input.origin, /^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/);
  assert.match(input.cookie, /^lmh_guarded_browser=[a-f0-9]{64}$/);
  const port = Number(input.origin.split(":").at(-1));
  assert(port > 0 && port <= 65535);
  let cookie = input.cookie;
  const paths = new Set(["/api/sources","/api/settings","/api/conversations","/api/messages"]);
  function wire(path) {
    return new Promise((resolve, reject) => {
      const req = http.request({ hostname: "127.0.0.1", port, method: "GET", path,
        headers: { Cookie: cookie, Connection: "close", "Sec-Fetch-Site": "same-origin" }
      }, res => {
        const out = [];
        res.on("data", chunk => out.push(chunk));
        res.on("end", () => {
          resolve({ status: res.statusCode, content: Buffer.concat(out).toString("utf8") });
        });
      });
      req.setTimeout(4000, () => req.destroy(new Error("timeout")));
      req.on("error", reject);
      req.end();
    });
  }

  const script = await wire("/api/_reader/reader.js");
  assert.equal(script.status, 200);
  const ids = Object.fromEntries(["status","selected","messages","conversations","reload"]
    .map(key => [key, new Element(key)]));
  const requests = [];
  const context = {
    URLSearchParams,
    document: {
      createElement: tag => new Element(tag),
      getElementById: id => ids[id]
    },
    fetch: async (path, options) => {
      assert(paths.has(path.split("?")[0]));
      assert.equal(options.method, "GET");
      assert.equal(options.mode, "same-origin");
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.redirect, "error");
      assert.equal(options.cache, "no-store");
      assert.equal(options.headers, undefined);
      requests.push(path);
      const response = await wire(path);
      return { ok: response.status === 200, status: response.status, json: async () => JSON.parse(response.content) };
    }
  };
  vm.runInNewContext(script.content, context, { codeGeneration: { strings: false, wasm: false }, timeout: 1000 });
  await eventually(() => ids.conversations.kids.length === 1);
  assert.equal(ids.conversations.kids[0].kids[0].textContent, "<em>合成标题</em>");
  ids.conversations.kids[0].kids[0].events.click();
  await eventually(() => ids.messages.kids.length === 1);
  assert.equal(ids.messages.kids[0].kids[0].textContent, "<b>合成正文</b>");
  assert.equal(ids.selected.textContent, "<em>合成标题</em>");
  assert.deepEqual(requests, [
    "/api/sources","/api/settings","/api/conversations?limit=40",
    "/api/messages?platform=qq&account_id=fixture-a&conversation_id=same-room&limit=40"
  ]);
  cookie = "lmh_guarded_browser=" + "0".repeat(64);
  ids.reload.events.click();
  await eventually(() => ids.status.textContent.includes("未授权"));
  assert.equal(ids.messages.kids.length, 0);
  assert.equal(ids.conversations.kids.length, 0);
  assert.equal(ids.selected.textContent, "");
  assert.equal(requests.at(-1), "/api/sources");
}

(async () => {
  const chunks = [];
  for await (const part of process.stdin) chunks.push(part);
  await run(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  console.log("Node synthetic DOM ran extracted reader script against actual local API; escaped literal text and cleared on 401.");
})().catch(() => {
  // Never emit raw private test context to ordinary logs.
  console.error("guarded reader protocol fixture failed");
  process.exitCode = 1;
});
