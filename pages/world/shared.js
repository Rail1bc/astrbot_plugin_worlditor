// shared.js — 通用工具：DOM、后端调用（非 2xx / error 信封抛错）、自绘弹窗、共享状态
// 沙箱 iframe 约束：无 alert/confirm（模态自绘）、无同源 localStorage（player_id 仅内存）。

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// 跨模块共享状态（app.js 写入，视图模块读取）
export const state = {
  playerId: null,
  world: null, // { locations, exits, player, agent }
  mode: "edit", // "edit" | "play"
  lastNodeId: null, // 编辑视图最近点选的地块，新建出口时作为默认 from_id
};

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
