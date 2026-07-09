const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chatForm");
const inputEl = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const configFile = document.getElementById("configFile");
const configStatus = document.getElementById("configStatus");
const tdmsFiles = document.getElementById("tdmsFiles");
const conversationsEl = document.getElementById("conversations");
const newConversationButton = document.getElementById("newConversation");

let busy = false;
let lastMessagesJson = "";
let refreshTimer = null;
let pendingAgent = false;
let pendingStartedAt = null;
let activeConversationId = null;

function setBusy(value) {
  busy = value;
  sendButton.disabled = value;
  newConversationButton.disabled = value;
  document.querySelectorAll(".quick-actions button").forEach((button) => {
    button.disabled = value;
  });
  document.querySelectorAll(".conversation-action, .conversation-open").forEach((button) => {
    button.disabled = value;
  });
}

function formatSize(bytes) {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return seconds + " 秒";
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return minutes + " 分 " + rest + " 秒";
}

function pendingMessageText() {
  const startedAt = pendingStartedAt || Date.now();
  return "agent 正在处理... " + formatElapsed(Date.now() - startedAt);
}

function renderMessages(messages, force = false) {
  const displayMessages = pendingAgent
    ? [
        ...(messages || []),
        {
          role: "system",
          content: pendingMessageText(),
          created_at: "",
        },
      ]
    : messages || [];
  const nextMessagesJson = JSON.stringify(displayMessages);
  if (!force && nextMessagesJson === lastMessagesJson) return;
  lastMessagesJson = nextMessagesJson;

  const nearBottom =
    messagesEl.scrollTop + messagesEl.clientHeight >= messagesEl.scrollHeight - 80;
  messagesEl.innerHTML = "";
  for (const message of displayMessages) {
    const item = document.createElement("article");
    item.className = `message ${message.role}`;
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = `${message.role} · ${message.created_at || ""}`;
    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content || "";
    item.append(meta, content);
    messagesEl.appendChild(item);
  }
  if (nearBottom || force) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function renderConversations(conversations) {
  conversationsEl.innerHTML = "";
  if (!conversations.length) {
    conversationsEl.textContent = "暂无对话记录";
    return;
  }

  for (const conversation of conversations) {
    const row = document.createElement("div");
    row.className = `conversation-row ${conversation.active ? "active" : ""}`;

    const open = document.createElement("button");
    open.type = "button";
    open.className = "conversation-open";
    open.textContent = conversation.title || conversation.id;
    open.title = conversation.updated_at || "";
    open.addEventListener("click", () => selectConversation(conversation.id));

    const meta = document.createElement("span");
    meta.className = "conversation-meta";
    meta.textContent = `${conversation.message_count || 0} 条`;

    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "conversation-action";
    rename.textContent = "重命名";
    rename.addEventListener("click", () => renameConversation(conversation));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "conversation-action danger";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteConversation(conversation));

    row.append(open, meta, rename, remove);
    conversationsEl.appendChild(row);
  }
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function loadMessages() {
  const payload = await requestJson("/api/messages");
  activeConversationId = payload.active_conversation_id || activeConversationId;
  renderMessages(payload.messages || []);
}

async function loadConversations() {
  const payload = await requestJson("/api/conversations");
  activeConversationId = payload.active_conversation_id || activeConversationId;
  renderConversations(payload.conversations || []);
}

function appendLocalMessage(role, content) {
  const messages = lastMessagesJson ? JSON.parse(lastMessagesJson) : [];
  messages.push({
    role,
    content,
    created_at: new Date().toISOString(),
  });
  renderMessages(messages, true);
}

async function refreshDynamicContent() {
  await Promise.all([loadMessages(), loadTdms(), loadStatus(), loadConversations()]);
}

function startAutoRefresh(intervalMs = 2000) {
  if (refreshTimer) return;
  refreshTimer = window.setInterval(() => {
    refreshDynamicContent().catch((error) => console.error(error));
  }, intervalMs);
}

function stopAutoRefresh() {
  if (!refreshTimer) return;
  window.clearInterval(refreshTimer);
  refreshTimer = null;
}

async function loadStatus() {
  const status = await requestJson("/api/status");
  const name = status.active_config ? status.active_config.split("/").pop() : "sampling_config.docx";
  configStatus.textContent = status.config_exists
    ? `当前配置：${name} · ${formatSize(status.config_size)}`
    : "当前配置不存在";
}

async function loadTdms() {
  const payload = await requestJson("/api/tdms");
  const files = payload.files || [];
  tdmsFiles.innerHTML = "";
  if (!files.length) {
    tdmsFiles.textContent = "暂无 TDMS 文件";
    return;
  }
  for (const file of files) {
    const row = document.createElement("a");
    row.className = "file-row";
    row.href = `/api/tdms/${encodeURIComponent(file.name)}`;
    row.textContent = `${file.name} · ${formatSize(file.size)}`;
    tdmsFiles.appendChild(row);
  }
}

async function sendMessage(message) {
  if (!message || busy) return;
  appendLocalMessage("user", message);
  pendingAgent = true;
  pendingStartedAt = Date.now();
  renderMessages(JSON.parse(lastMessagesJson), true);
  inputEl.value = "";
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    await refreshDynamicContent();
  } catch (error) {
    alert(error.message);
    await loadMessages().catch(() => {});
  } finally {
    pendingAgent = false;
    pendingStartedAt = null;
    setBusy(false);
    stopAutoRefresh();
    await refreshDynamicContent().catch(() => {});
  }
}

async function createNewConversation() {
  if (busy) return;
  const payload = await requestJson("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  activeConversationId = payload.conversation.id;
  lastMessagesJson = "";
  renderMessages([], true);
  await loadConversations();
}

async function selectConversation(conversationId) {
  if (busy || conversationId === activeConversationId) return;
  const payload = await requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/select`, {
    method: "POST",
  });
  activeConversationId = payload.conversation.id;
  lastMessagesJson = "";
  renderMessages(payload.messages || [], true);
  renderConversations(payload.conversations || []);
}

async function renameConversation(conversation) {
  if (busy) return;
  const title = prompt("输入新的对话名称", conversation.title || "");
  if (title === null) return;
  const payload = await requestJson(`/api/conversations/${encodeURIComponent(conversation.id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  renderConversations(payload.conversations || []);
}

async function deleteConversation(conversation) {
  if (busy) return;
  if (!confirm(`删除对话“${conversation.title || conversation.id}”？`)) return;
  const payload = await requestJson(`/api/conversations/${encodeURIComponent(conversation.id)}`, {
    method: "DELETE",
  });
  activeConversationId = payload.active_conversation_id || activeConversationId;
  lastMessagesJson = "";
  renderMessages(payload.messages || [], true);
  renderConversations(payload.conversations || []);
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value.trim());
});

document.querySelectorAll(".quick-actions button").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.message));
});

newConversationButton.addEventListener("click", () => {
  createNewConversation().catch((error) => alert(error.message));
});

configFile.addEventListener("change", async () => {
  const file = configFile.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("config", file);
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson("/api/upload-config", { method: "POST", body: form });
    await Promise.all([loadMessages(), loadStatus(), loadConversations()]);
  } catch (error) {
    alert(error.message);
  } finally {
    configFile.value = "";
    setBusy(false);
    stopAutoRefresh();
  }
});

document.getElementById("refreshFiles").addEventListener("click", loadTdms);

Promise.all([loadMessages(), loadStatus(), loadTdms(), loadConversations()]).catch((error) => {
  console.error(error);
});

window.setInterval(() => {
  if (!busy) {
    refreshDynamicContent().catch((error) => console.error(error));
  }
}, 5000);
