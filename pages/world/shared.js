// shared.js — 通用工具：DOM、后端调用（非 2xx / error 信封抛错）、自绘弹窗、共享状态
// 沙箱 iframe 约束：无 alert/confirm（模态自绘）、无同源 localStorage（player_id 仅内存）。

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// 跨模块共享状态（app.js 写入，视图模块读取）
export const state = {
  playerId: null,
  world: null, // { maps, locations, templates, player, agent, spawn }
  mode: "edit", // "edit" | "play"
  editMode: "edit", // 编辑视图子模式："view"（只读）| "edit"（表单 + 创建）
  selection: null, // 详情栏选中项：{kind:"location"|"cell"|"slot"|"gap"|"templates", ...}
  detailOpen: true, // 详情栏是否展开
};

// ---------- 坐标与方向（与引擎 world/v3model.py 常量一致） ----------
// 地块身份 = (map_id, row, col)。网格：整数 (row, col)，col 向右、row 向下。
export const DIRECTIONS = ["up", "right", "down", "left"];
export const DIR_LABELS = { up: "上", right: "右", down: "下", left: "左" };
// 方向 ↔ 坐标偏移（行, 列）：up=行-1 / down=行+1 / left=列-1 / right=列+1。
// 与引擎 DIR_OFFSETS 一致。
export const DIR_OFFSETS = {
  up: [-1, 0],
  down: [1, 0],
  left: [0, -1],
  right: [0, 1],
};
export const OPPOSITE_DIR = {
  up: "down",
  right: "left",
  down: "up",
  left: "right",
};

export const cellKey = (col, row) => `${col},${row}`;

// 地块 → (col,row) 索引（v3 身份即坐标，无需 layout 兜底）
export function locAt(locations) {
  const m = new Map();
  for (const loc of locations || []) {
    m.set(cellKey(loc.col, loc.row), loc);
  }
  return m;
}

// ---------- 场景 / 路径解析辅助 ----------
// 解析目标坐标到地块名；不可解析（目标地图非当前图 / 地块不存在）返回 null（= 死引用）。
export function targetName(byLoc, target, defaultMapId = "") {
  if (target.map_id && target.map_id !== defaultMapId) {
    return null;
  }
  const loc = byLoc.get(cellKey(target.col, target.row));
  return loc ? loc.name : null;
}

// 路径主目标名（targets[0]）；无目标 → null
export function mainTarget(path) {
  return Array.isArray(path.targets) && path.targets.length > 0
    ? path.targets[0]
    : null;
}

export function pathDead(byLoc, path) {
  const t = mainTarget(path);
  return !t || targetName(byLoc, t) === null;
}

// 时段文本取首时段首条（编辑视图无时钟，仅展示用）
export function scheduleText(schedule) {
  const p = schedule?.periods?.[0];
  const it = p?.items?.[0];
  return it?.text ?? "";
}

const bridge = window.AstrBotPluginPage;

function ensureOk(data) {
  // bridge 在非 2xx 时可能抛错或返回 error 信封，统一转成 Error
  if (data && typeof data === "object" && data.status === "error") {
    const message = typeof data.message === "string" ? data.message : "请求失败";
    throw new Error(message);
  }
  return data;
}

export async function apiGet(path, query = {}) {
  return ensureOk(await bridge.apiGet(path, query));
}

export async function apiPost(path, body = {}) {
  return ensureOk(await bridge.apiPost(path, body));
}

// ---------- 自绘弹窗 ----------
export function openModal(title, body, actions) {
  $("#modal-title").textContent = title;
  const bodyEl = $("#modal-body");
  bodyEl.textContent = "";
  if (typeof body === "string") {
    bodyEl.textContent = body;
  } else if (body) {
    bodyEl.appendChild(body);
  }
  const actionsEl = $("#modal-actions");
  actionsEl.textContent = "";
  const defaultActions = [{ label: "知道了" }];
  (actions || defaultActions).forEach((action) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = action.primary ? "btn btn-primary" : "btn";
    btn.textContent = action.label;
    btn.addEventListener("click", () => {
      hideModal();
      if (action.onClick) action.onClick();
    });
    actionsEl.appendChild(btn);
  });
  $("#modal-mask").hidden = false;
}

export function hideModal() {
  $("#modal-mask").hidden = true;
}

export function confirmModal(title, body, onConfirm, confirmLabel = "确认") {
  openModal(title, body, [
    { label: "取消" },
    { label: confirmLabel, primary: true, onClick: onConfirm },
  ]);
}

// 关闭按钮 / 蒙层点击关闭（app.js 初始化时绑定一次）
export function bindModalDismiss() {
  $("#modal-close").addEventListener("click", hideModal);
  $("#modal-mask").addEventListener("click", (event) => {
    if (event.target === $("#modal-mask")) {
      hideModal();
    }
  });
}
