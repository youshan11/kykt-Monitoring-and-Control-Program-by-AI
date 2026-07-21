const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chatForm");
const inputEl = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const configFile = document.getElementById("configFile");
const configStatus = document.getElementById("configStatus");
const tdmsFiles = document.getElementById("tdmsFiles");
const conversationsEl = document.getElementById("conversations");
const configHistoryEl = document.getElementById("configHistory");
const newConversationButton = document.getElementById("newConversation");
const configDraftBanner = document.getElementById("configDraftBanner");
const configDraftText = document.getElementById("configDraftText");
const confirmConfigDraftButton = document.getElementById("confirmConfigDraft");
const discardConfigDraftButton = document.getElementById("discardConfigDraft");
const previewConfigButton = document.getElementById("previewConfig");
const previewModal = document.getElementById("previewModal");
const previewTitle = document.getElementById("previewTitle");
const previewMeta = document.getElementById("previewMeta");
const previewContent = document.getElementById("previewContent");
const refreshPreviewButton = document.getElementById("refreshPreview");
const closePreviewButton = document.getElementById("closePreview");

let busy = false;
let lastMessagesJson = "";
let refreshTimer = null;
let pendingAgent = false;
let pendingStartedAt = null;
let pendingText = null;
let activeConversationId = null;
let currentConfigDraft = { active: false };

function hasActiveDraft() {
  return Boolean(currentConfigDraft && currentConfigDraft.active);
}

function updateControls() {
  const draftActive = hasActiveDraft();
  sendButton.disabled = busy;
  newConversationButton.disabled = busy || draftActive;
  configFile.disabled = busy || draftActive;
  previewConfigButton.disabled = busy;
  document.querySelector(".upload-button")?.classList.toggle("disabled", busy || draftActive);

  document.querySelectorAll(".quick-actions button").forEach((button) => {
    button.disabled = busy || (draftActive && button.dataset.blockDraft === "true");
  });
  document.querySelectorAll(".conversation-action, .conversation-open, .config-history-select").forEach((button) => {
    button.disabled = busy || draftActive;
  });
  document.querySelectorAll(".config-history-delete").forEach((button) => {
    button.disabled = busy || draftActive || button.dataset.active === "true";
  });
  document.querySelectorAll(".tdms-delete").forEach((button) => {
    button.disabled = busy;
  });
  confirmConfigDraftButton.disabled = busy || !draftActive;
  discardConfigDraftButton.disabled = busy || !draftActive;
}

function setBusy(value) {
  busy = value;
  updateControls();
}

function formatSize(bytes) {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return seconds + " 秒";
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return minutes + " 分 " + rest + " 秒";
}

function renderConfigDraft(draft) {
  currentConfigDraft = draft && draft.active ? draft : { active: false };
  if (!hasActiveDraft()) {
    configDraftBanner.hidden = true;
    configDraftText.textContent = "";
    updateControls();
    return;
  }

  const baseName = currentConfigDraft.base_display_filename || "sampling_config.docx";
  const size = currentConfigDraft.size ? ` · ${formatSize(currentConfigDraft.size)}` : "";
  configDraftText.textContent = `基于 ${baseName} 的草稿未保存${size}。确认前不能启动采样、上传配置或切换配置历史。`;
  configDraftBanner.hidden = false;
  updateControls();
}

function pendingMessageText() {
  const startedAt = pendingStartedAt || Date.now();
  return `${pendingText || "agent 正在处理..."} ${formatElapsed(Date.now() - startedAt)}`;
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
  updateControls();
}

function renderPreview(preview) {
  previewTitle.textContent = preview.title || "配置文档预览";
  const size = preview.size ? formatSize(preview.size) : "未知大小";
  const modifiedAt = preview.modified_at ? formatDate(preview.modified_at) : "未知时间";
  const source = preview.is_draft ? "未保存草稿" : "当前正式配置";
  previewMeta.textContent = `${source} · ${preview.display_filename || "sampling_config.docx"} · ${size} · ${modifiedAt}`;
  previewContent.textContent = preview.content || "(没有可预览的文本内容)";
}

async function loadPreview() {
  previewTitle.textContent = "配置文档预览";
  previewMeta.textContent = "读取中...";
  previewContent.textContent = "读取中...";
  const payload = await requestJson("/api/config-preview");
  renderPreview(payload.preview || {});
}

async function openPreview() {
  previewModal.hidden = false;
  document.body.classList.add("preview-open");
  try {
    await loadPreview();
  } catch (error) {
    previewTitle.textContent = "配置文档预览";
    previewMeta.textContent = "读取失败";
    previewContent.textContent = error.message;
  }
}

function closePreview() {
  previewModal.hidden = true;
  document.body.classList.remove("preview-open");
}

function renderConfigHistory(configs) {
  configHistoryEl.innerHTML = "";
  if (!configs.length) {
    configHistoryEl.textContent = "暂无配置历史";
    return;
  }

  for (const config of configs) {
    const row = document.createElement("div");
    row.className = `config-history-row ${config.active ? "active" : ""}`;

    const info = document.createElement("div");
    info.className = "config-history-info";

    const name = document.createElement("div");
    name.className = "config-history-name";
    name.textContent = config.filename || config.id;
    name.title = config.filename || config.id;

    const meta = document.createElement("div");
    meta.className = "config-history-meta";
    meta.textContent = `${formatDate(config.uploaded_at)} · ${formatSize(config.size || 0)}`;

    info.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "config-history-actions";

    if (config.active) {
      const active = document.createElement("span");
      active.className = "config-history-active";
      active.textContent = "当前";
      actions.appendChild(active);
    } else {
      const select = document.createElement("button");
      select.type = "button";
      select.className = "config-history-select";
      select.textContent = "使用";
      select.addEventListener("click", () => selectConfigHistory(config));
      actions.appendChild(select);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "config-history-delete danger";
    remove.textContent = "删除";
    remove.dataset.active = config.active ? "true" : "false";
    remove.disabled = Boolean(config.active);
    remove.title = config.active ? "当前正在使用的配置不能删除" : "删除配置历史";
    remove.addEventListener("click", () => deleteConfigHistory(config));
    actions.appendChild(remove);

    row.append(info, actions);
    configHistoryEl.appendChild(row);
  }
  updateControls();
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

async function loadConfigHistory() {
  const payload = await requestJson("/api/config-history");
  renderConfigHistory(payload.configs || []);
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
  await Promise.all([loadMessages(), loadTdms(), loadStatus(), loadConfigHistory(), loadConversations()]);
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
  renderConfigDraft(status.config_draft || { active: false });
  const name = status.display_filename || (status.active_config ? status.active_config.split("/").pop() : "sampling_config.docx");
  configStatus.textContent = status.config_exists
    ? `当前配置：${name} · ${formatSize(status.config_size)}${hasActiveDraft() ? " · 有未保存草稿" : ""}`
    : "当前配置不存在";
}

function renderTdmsFiles(files) {
  tdmsFiles.innerHTML = "";
  if (!files.length) {
    tdmsFiles.textContent = "暂无 TDMS 文件";
    return;
  }
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "file-row";

    const download = document.createElement("a");
    download.className = "file-download";
    download.href = `/api/tdms/${encodeURIComponent(file.name)}`;
    download.textContent = `${file.name} · ${formatSize(file.size)}`;
    download.title = file.modified_at ? formatDate(file.modified_at) : file.name;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "tdms-delete danger";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteTdmsFile(file));

    row.append(download, remove);
    tdmsFiles.appendChild(row);
  }
  updateControls();
}

async function loadTdms() {
  const payload = await requestJson("/api/tdms");
  renderTdmsFiles(payload.files || []);
}

async function deleteTdmsFile(file) {
  if (busy) return;
  if (!confirm(`删除 TDMS 文件“${file.name}”？`)) return;
  setBusy(true);
  try {
    const payload = await requestJson(`/api/tdms/${encodeURIComponent(file.name)}`, {
      method: "DELETE",
    });
    renderTdmsFiles(payload.files || []);
  } catch (error) {
    alert(error.message);
    await loadTdms().catch(() => {});
  } finally {
    setBusy(false);
  }
}

async function deleteConfigHistory(config) {
  if (busy || config.active) return;
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
  if (!confirm(`删除配置历史“${config.filename || config.id}”？`)) return;
  setBusy(true);
  try {
    const payload = await requestJson(`/api/config-history/${encodeURIComponent(config.id)}`, {
      method: "DELETE",
    });
    renderConfigHistory(payload.configs || []);
  } catch (error) {
    alert(error.message);
    await loadConfigHistory().catch(() => {});
  } finally {
    setBusy(false);
  }
}

async function selectConfigHistory(config) {
  if (busy || config.active) return;
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson(`/api/config-history/${encodeURIComponent(config.id)}/select`, {
      method: "POST",
    });
    await Promise.all([loadMessages(), loadStatus(), loadConfigHistory(), loadConversations()]);
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(false);
    stopAutoRefresh();
  }
}

async function sendMessage(message) {
  if (!message || busy) return;
  appendLocalMessage("user", message);
  pendingAgent = true;
  pendingStartedAt = Date.now();
  pendingText = "agent 正在处理...";
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
    pendingText = null;
    setBusy(false);
    stopAutoRefresh();
    await refreshDynamicContent().catch(() => {});
  }
}

async function runSamplingAction(button) {
  if (busy) return;
  const action = button.dataset.samplingAction;
  const message = button.dataset.message || button.textContent.trim();
  if (!action || !message) return;

  appendLocalMessage("user", message);
  pendingAgent = true;
  pendingStartedAt = Date.now();
  pendingText = "采样控制正在执行...";
  renderMessages(JSON.parse(lastMessagesJson), true);
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson(`/api/sampling/${action}`, {
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
    pendingText = null;
    setBusy(false);
    stopAutoRefresh();
    await refreshDynamicContent().catch(() => {});
  }
}

async function createNewConversation() {
  if (busy) return;
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
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
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
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
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
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
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    return;
  }
  if (!confirm(`删除对话“${conversation.title || conversation.id}”？`)) return;
  const payload = await requestJson(`/api/conversations/${encodeURIComponent(conversation.id)}`, {
    method: "DELETE",
  });
  activeConversationId = payload.active_conversation_id || activeConversationId;
  lastMessagesJson = "";
  renderMessages(payload.messages || [], true);
  renderConversations(payload.conversations || []);
}

async function confirmConfigDraft() {
  if (busy || !hasActiveDraft()) return;
  const defaultName = currentConfigDraft.suggested_filename || `sampling_config_${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}.docx`;
  const filename = prompt("输入新配置文件名", defaultName);
  if (filename === null) return;
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson("/api/config-draft/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    await refreshDynamicContent();
  } catch (error) {
    alert(error.message);
    await refreshDynamicContent().catch(() => {});
  } finally {
    setBusy(false);
    stopAutoRefresh();
  }
}

async function discardConfigDraft() {
  if (busy || !hasActiveDraft()) return;
  if (!confirm("放弃本次配置修改？正式配置不会改变。")) return;
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson("/api/config-draft/discard", { method: "POST" });
    await refreshDynamicContent();
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(false);
    stopAutoRefresh();
  }
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value.trim());
});

document.querySelectorAll(".quick-actions button").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.samplingAction) {
      runSamplingAction(button);
      return;
    }
    sendMessage(button.dataset.message);
  });
});

newConversationButton.addEventListener("click", () => {
  createNewConversation().catch((error) => alert(error.message));
});

confirmConfigDraftButton.addEventListener("click", () => {
  confirmConfigDraft().catch((error) => alert(error.message));
});

discardConfigDraftButton.addEventListener("click", () => {
  discardConfigDraft().catch((error) => alert(error.message));
});

previewConfigButton.addEventListener("click", () => {
  openPreview();
});

refreshPreviewButton.addEventListener("click", () => {
  loadPreview().catch((error) => {
    previewMeta.textContent = "读取失败";
    previewContent.textContent = error.message;
  });
});

closePreviewButton.addEventListener("click", closePreview);

previewModal.addEventListener("click", (event) => {
  if (event.target === previewModal) closePreview();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !previewModal.hidden) closePreview();
});

configFile.addEventListener("change", async () => {
  const file = configFile.files[0];
  if (!file) return;
  if (hasActiveDraft()) {
    alert("当前有未确认的配置草稿，请先确认或放弃修改。");
    configFile.value = "";
    return;
  }
  const form = new FormData();
  form.append("config", file);
  setBusy(true);
  startAutoRefresh(1000);
  try {
    await requestJson("/api/upload-config", { method: "POST", body: form });
    await Promise.all([loadMessages(), loadStatus(), loadConfigHistory(), loadConversations()]);
  } catch (error) {
    alert(error.message);
  } finally {
    configFile.value = "";
    setBusy(false);
    stopAutoRefresh();
  }
});

document.getElementById("refreshFiles").addEventListener("click", loadTdms);

Promise.all([loadMessages(), loadStatus(), loadTdms(), loadConfigHistory(), loadConversations()]).catch((error) => {
  console.error(error);
});

window.setInterval(() => {
  if (!busy) {
    refreshDynamicContent().catch((error) => console.error(error));
  }
}, 5000);
