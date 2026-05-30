import { createApi } from "./api.js";

const bridge = window.AstrBotPluginPage;
const root = document.documentElement;
const themeMediaQuery =
  typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
const THEME_STORAGE_KEY = "permission-controller-theme-mode";
const GROUP_TOUCH_STORAGE_KEY = "permission-controller-group-touch-times";

let api = null;
let groups = [];
let currentGroup = null;
let themePreference = loadThemePreference();

const els = {
  groupList: document.getElementById("groupList"),
  groupForm: document.getElementById("groupForm"),
  groupSearchInput: document.getElementById("groupSearchInput"),
  currentGroupTitle: document.getElementById("currentGroupTitle"),
  currentGroupMeta: document.getElementById("currentGroupMeta"),
  toastLayer: document.getElementById("toastLayer"),
  toggleThemeBtn: document.getElementById("toggleThemeBtn"),
  refreshGroupsBtn: document.getElementById("refreshGroupsBtn"),
  resetGroupBtn: document.getElementById("resetGroupBtn"),
  saveGroupBtn: document.getElementById("saveGroupBtn"),
};

function loadThemePreference() {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (["light", "dark", "auto"].includes(stored)) return stored;
  } catch {}
  return "auto";
}

function loadGroupTouchTimes() {
  try {
    const raw = window.localStorage.getItem(GROUP_TOUCH_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {}
  return {};
}

function saveGroupTouchTimes(times) {
  try {
    window.localStorage.setItem(GROUP_TOUCH_STORAGE_KEY, JSON.stringify(times));
  } catch {}
}

function touchGroupConfig(groupId) {
  const target = String(groupId || "").trim();
  if (!target) return;
  const times = loadGroupTouchTimes();
  times[target] = Date.now();
  saveGroupTouchTimes(times);
}

function groupTouchTime(group) {
  const backendTime = Number(group?.config_updated_at || 0);
  if (backendTime) return backendTime;
  const times = loadGroupTouchTimes();
  return Number(times[String(group?.group_id || "")] || 0);
}

function sortGroupsByRecentConfig(groupList) {
  return [...groupList].sort((a, b) => {
    const recentDiff = groupTouchTime(b) - groupTouchTime(a);
    if (recentDiff) return recentDiff;
    return String(a.group_name || a.group_id || "").localeCompare(
      String(b.group_name || b.group_id || ""),
      "zh-Hans-CN",
    );
  });
}

function saveThemePreference() {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
  } catch {}
}

function themeButtonLabel() {
  if (themePreference === "dark") return "主题：深色";
  if (themePreference === "light") return "主题：浅色";
  return "主题：自动";
}

function applyTheme() {
  let effective = themePreference;
  if (effective === "auto") {
    effective = themeMediaQuery && themeMediaQuery.matches ? "dark" : "light";
  }
  root.setAttribute("data-theme", effective);
  if (els.toggleThemeBtn) els.toggleThemeBtn.textContent = themeButtonLabel();
}

function cycleTheme() {
  themePreference =
    themePreference === "auto" ? "light" : themePreference === "light" ? "dark" : "auto";
  saveThemePreference();
  applyTheme();
}

function toast(message, kind = "info") {
  if (!els.toastLayer) return;
  const node = document.createElement("div");
  node.className = `toast toast-${kind}`;
  node.textContent = message;
  els.toastLayer.appendChild(node);
  requestAnimationFrame(() => node.classList.add("show"));
  setTimeout(() => {
    node.classList.remove("show");
    setTimeout(() => node.remove(), 240);
  }, 2600);
}

function normalizeListText(value) {
  return String(value || "")
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderGroupList() {
  const keyword = String(els.groupSearchInput?.value || "").trim().toLowerCase();
  const visibleGroups = sortGroupsByRecentConfig(groups).filter((group) => {
    const text = `${group.group_name || ""} ${group.group_id || ""}`.toLowerCase();
    return !keyword || text.includes(keyword);
  });

  els.groupList.innerHTML = "";
  if (!visibleGroups.length) {
    els.groupList.classList.add("empty-state");
    els.groupList.textContent = "未找到群聊。请确认机器人已接入 QQ 平台，或先在配置中添加群号。";
    return;
  }

  els.groupList.classList.remove("empty-state");
  visibleGroups.forEach((group) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "group-item";
    if (String(currentGroup?.group_info?.group_id || "") === String(group.group_id)) {
      card.classList.add("active");
    }
    card.addEventListener("click", () => loadGroupConfig(group.group_id));

    const avatar = document.createElement("img");
    avatar.className = "group-avatar";
    avatar.src = group.avatar || "";
    avatar.alt = group.group_name || group.group_id;
    avatar.onerror = () => {
      avatar.style.display = "none";
    };

    const body = document.createElement("span");
    body.className = "group-item-body";
    const name = document.createElement("span");
    name.className = "group-name";
    name.textContent = group.group_name || `群 ${group.group_id}`;
    const meta = document.createElement("span");
    meta.className = "group-meta";
    const touchedAt = groupTouchTime(group);
    meta.textContent = touchedAt
      ? `群号：${group.group_id} · 最近配置：${new Date(touchedAt).toLocaleString()}`
      : `群号：${group.group_id}`;
    body.appendChild(name);
    body.appendChild(meta);

    card.appendChild(avatar);
    card.appendChild(body);
    els.groupList.appendChild(card);
  });
}

function renderGroupForm(payload) {
  currentGroup = payload;
  const info = payload.group_info || {};
  const config = payload.config || {};
  els.currentGroupTitle.textContent = info.group_name || `群 ${info.group_id || ""}`;
  els.currentGroupMeta.textContent = info.group_id ? `群号：${info.group_id}` : "请选择左侧群聊";

  els.groupForm.innerHTML = "";
  els.groupForm.classList.remove("empty-state");

  const enabledField = document.createElement("div");
  enabledField.className = "field field-bool";
  enabledField.innerHTML = `
    <div>
      <div class="field-label">整群放行</div>
      <div class="field-hint">开启后，该群所有成员都可调用机器人。</div>
    </div>
    <label class="switch">
      <input id="groupEnabledInput" type="checkbox" ${config.group_enabled ? "checked" : ""} />
      <span class="switch-slider"></span>
    </label>
  `;

  const usersField = document.createElement("div");
  usersField.className = "field";
  usersField.innerHTML = `
    <div class="field-label">本群允许用户</div>
    <div class="field-hint">每行一个 QQ 号。保存后会自动写入“用户QQ-群号”规则。</div>
    <textarea id="allowedUsersInput" rows="8" spellcheck="false" placeholder="每行一个 QQ 号"></textarea>
  `;
  usersField.querySelector("textarea").value = Array.isArray(config.allowed_users)
    ? config.allowed_users.join("\n")
    : "";

  els.groupForm.appendChild(enabledField);
  els.groupForm.appendChild(usersField);
  renderGroupList();
}

function collectGroupForm() {
  return {
    group_enabled: Boolean(document.getElementById("groupEnabledInput")?.checked),
    allowed_users: normalizeListText(document.getElementById("allowedUsersInput")?.value),
  };
}

async function loadBootstrap() {
  els.groupList.classList.add("empty-state");
  els.groupList.textContent = "群列表同步中…";
  els.groupForm.classList.add("empty-state");
  els.groupForm.textContent = "请从左侧选择一个群聊。";
  try {
    await refreshGroups({ silent: true });
  } catch (err) {
    const data = await api.safeGet("settings/bootstrap");
    groups = data.groups || [];
    renderGroupList();
    toast("同步群列表失败，已使用缓存配置：" + err.message, "error");
  }
  const firstGroup = sortGroupsByRecentConfig(groups)[0];
  if (firstGroup) {
    await loadGroupConfig(firstGroup.group_id);
  }
}

async function refreshGroups(options = {}) {
  groups = await api.safePost("settings/groups/refresh", {});
  renderGroupList();
  if (!options.silent) toast("群列表已同步", "success");
}

async function loadGroupConfig(groupId) {
  const target = String(groupId || "").trim();
  if (!target) return;
  const data = await api.safeGet("settings/group", { group_id: target });
  renderGroupForm(data);
}

async function saveGroupConfig() {
  const groupId = String(currentGroup?.group_info?.group_id || "").trim();
  if (!groupId) {
    toast("请先选择群聊", "error");
    return;
  }
  els.saveGroupBtn.disabled = true;
  try {
    const data = await api.safePost("settings/group", {
      group_id: groupId,
      config: collectGroupForm(),
    });
    renderGroupForm(data);
    touchGroupConfig(groupId);
    await refreshGroups({ silent: true });
    toast("群配置已保存", "success");
  } catch (err) {
    toast("保存失败：" + err.message, "error");
  } finally {
    els.saveGroupBtn.disabled = false;
  }
}

async function resetGroupConfig() {
  const groupId = String(currentGroup?.group_info?.group_id || "").trim();
  if (!groupId) {
    toast("请先选择群聊", "error");
    return;
  }
  if (!window.confirm("确定清空该群的整群放行和允许用户吗？")) return;
  els.resetGroupBtn.disabled = true;
  try {
    const data = await api.safePost("settings/group/reset", { group_id: groupId });
    renderGroupForm(data);
    touchGroupConfig(groupId);
    await refreshGroups({ silent: true });
    toast("群配置已重置", "success");
  } catch (err) {
    toast("重置失败：" + err.message, "error");
  } finally {
    els.resetGroupBtn.disabled = false;
  }
}

function bindEvents() {
  els.toggleThemeBtn?.addEventListener("click", cycleTheme);
  els.refreshGroupsBtn?.addEventListener("click", () => refreshGroups().catch((err) => toast(err.message, "error")));
  els.saveGroupBtn?.addEventListener("click", saveGroupConfig);
  els.resetGroupBtn?.addEventListener("click", resetGroupConfig);
  els.groupSearchInput?.addEventListener("input", renderGroupList);
  if (themeMediaQuery) {
    const handler = () => {
      if (themePreference === "auto") applyTheme();
    };
    if (themeMediaQuery.addEventListener) {
      themeMediaQuery.addEventListener("change", handler);
    } else if (themeMediaQuery.addListener) {
      themeMediaQuery.addListener(handler);
    }
  }
}

function init() {
  applyTheme();
  bindEvents();
  if (!bridge) {
    els.groupForm.textContent = "无法获取 AstrBot 页面桥接（window.AstrBotPluginPage）。";
    els.groupForm.classList.add("empty-state");
    return;
  }
  try {
    api = createApi(bridge);
  } catch (err) {
    els.groupForm.textContent = "初始化失败：" + err.message;
    return;
  }
  loadBootstrap().catch((err) => {
    els.groupForm.textContent = "加载失败：" + err.message;
    els.groupForm.classList.add("empty-state");
    toast("加载失败：" + err.message, "error");
  });
}

init();
