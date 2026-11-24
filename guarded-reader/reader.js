"use strict";

(() => {
  const sourcesPath = "/api/sources";
  const settingsPath = "/api/settings";
  const conversationsPath = "/api/conversations";
  const messagesPath = "/api/messages";
  const readable = new Set([sourcesPath, settingsPath, conversationsPath, messagesPath]);
  const status = document.getElementById("status");
  const conversations = document.getElementById("conversations");
  const messages = document.getElementById("messages");
  const selected = document.getElementById("selected");
  let version = 0;

  function clearMessages() {
    messages.replaceChildren();
    selected.textContent = "";
  }

  function clearAll() {
    conversations.replaceChildren();
    clearMessages();
  }

  async function read(path, params) {
    if (!readable.has(path)) throw new Error("unsupported");
    const suffix = params ? "?" + new URLSearchParams(params).toString() : "";
    const response = await fetch(path + suffix, {
      method: "GET",
      mode: "same-origin",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error"
    });
    if (!response.ok) throw new Error(response.status === 401 || response.status === 423 ? "guard" : "unavailable");
    return response.json();
  }

  function failed(error, request) {
    if (request !== version) return;
    clearAll();
    status.textContent = error.message === "guard"
      ? "当前会话未授权或已锁定。请用本地应用重新授权后再读取。"
      : "无法读取当前档案。";
  }

  function appendText(list, text) {
    const item = document.createElement("li");
    const content = document.createElement("p");
    content.textContent = typeof text === "string" ? text : "";
    item.append(content);
    list.append(item);
  }

  async function display(row) {
    const request = ++version;
    clearMessages();
    status.textContent = "正在读取消息…";
    try {
      const page = await read(messagesPath, {
        platform: row.platform,
        account_id: row.account_id,
        conversation_id: row.id,
        limit: "40"
      });
      if (request !== version) return;
      if (!Array.isArray(page.items)) throw new Error("unavailable");
      selected.textContent = row.title || row.id;
      let shown = 0;
      for (const item of page.items.slice(0, 40)) {
        if (!item || item.type !== "text" || typeof item.text !== "string") continue;
        appendText(messages, item.text);
        shown += 1;
      }
      status.textContent = shown ? "已读取已存档文本。" : "当前页没有可显示的文本消息。";
    } catch (error) {
      failed(error, request);
    }
  }

  async function load() {
    const request = ++version;
    clearAll();
    status.textContent = "正在读取本地档案…";
    try {
      const source = await read(sourcesPath);
      if (request !== version) return;
      const settings = await read(settingsPath);
      if (request !== version) return;
      if (!Array.isArray(source.sources) || !settings.capabilities?.chat?.enabled) {
        throw new Error("unavailable");
      }
      const page = await read(conversationsPath, { limit: "40" });
      if (request !== version) return;
      if (!Array.isArray(page.items)) throw new Error("unavailable");
      let count = 0;
      for (const row of page.items.slice(0, 40)) {
        if (!row || typeof row.id !== "string" || typeof row.account_id !== "string"
            || typeof row.platform !== "string") continue;
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = typeof row.title === "string" && row.title ? row.title : row.id;
        button.addEventListener("click", () => display(row));
        item.append(button);
        conversations.append(item);
        count += 1;
      }
      status.textContent = count ? "请选择要读取的会话。" : "没有可读取的已存档会话。";
    } catch (error) {
      failed(error, request);
    }
  }

  document.getElementById("reload").addEventListener("click", load);
  load();
})();
