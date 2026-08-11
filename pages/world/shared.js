// shared.js — 通用工具：DOM、后端调用（非 2xx / error 信封抛错）、自绘弹窗、共享状态
// 沙箱 iframe 约束：无 alert/confirm（模态自绘）、无同源 localStorage（player_id 仅内存）。

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// 跨模块共享状态（app.js 写入，视图模块读取）
export const state = {
  playerId: null,
  world: null, // { locations, exits, player, agent, spawn }
  mode: "edit", // "edit" | "play"
  lastNodeId: null, // 编辑视图最近点选的地块，新建出口时作为默认 from_id
  collapsedExits: new Set(), // 编辑视图已收起的出口（分身折叠进出发地块格）
};

// ---------- 网格布局（编辑视图表格） ----------
// 地块主位 = layout 整数坐标（col=x、row=y）；出口方向决定「目标相邻格」；
// 目标主位不在该格时，该格显示目标的地块分身（可收起）。

export const DIR_OFFSETS = {
  up: [0, -1],
  right: [1, 0],
  down: [0, 1],
  left: [-1, 0],
};

export const OPPOSITE_DIR = {
  up: "down",
  right: "left",
  down: "up",
  left: "right",
};

export const cellKey = (col, row) => `${col},${row}`;

// 计算每个地块的主位：layout 整数坐标优先，缺坐标的确定性兜底到首个空闲格
export function computePositions(locations) {
  const pos = new Map(); // id -> [col, row]
  const occupied = new Set();
  for (const l of locations || []) {
    if (l.layout && Number.isFinite(l.layout.x) && Number.isFinite(l.layout.y)) {
      const cell = [Math.round(l.layout.x), Math.round(l.layout.y)];
      pos.set(l.id, cell);
      occupied.add(cellKey(cell[0], cell[1]));
    }
  }
  for (const l of locations || []) {
    if (!pos.has(l.id)) {
      const cell = firstFreeCell(occupied);
      pos.set(l.id, cell);
      occupied.add(cellKey(cell[0], cell[1]));
    }
  }
  return pos;
}

// 分身列表：每条出口的要求格 = from + direction 偏移；目标主位不在该格 → 分身
export function computeAvatars(world, pos) {
  const avatars = [];
  for (const e of world?.exits || []) {
    const from = pos.get(e.from_id);
    const target = pos.get(e.to_id);
    if (!from || !target) {
      continue;
    }
    const [dc, dr] = DIR_OFFSETS[e.direction] || DIR_OFFSETS.up;
    const col = from[0] + dc;
    const row = from[1] + dr;
    const adjacent = target[0] === col && target[1] === row;
    if (!adjacent) {
      avatars.push({ exit: e, col, row, targetId: e.to_id });
    }
  }
  return avatars;
}

// 首个空闲格：优先候选列表（如 lastNodeId 的邻格），否则从 (0,0) 向外环形扫描
export function firstFreeCell(occupied, prefers = []) {
  for (const [c, r] of prefers) {
    if (!occupied.has(cellKey(c, r))) {
      return [c, r];
    }
  }
  for (let radius = 0; radius < 50; radius++) {
    for (let dc = -radius; dc <= radius; dc++) {
      for (let dr = -radius; dr <= radius; dr++) {
        if (Math.max(Math.abs(dc), Math.abs(dr)) !== radius) {
          continue;
        }
        if (!occupied.has(cellKey(dc, dr))) {
          return [dc, dr];
        }
      }
    }
  }
  return [0, 0];
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
