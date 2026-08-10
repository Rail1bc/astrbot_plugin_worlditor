// app.js — 有向图世界调试页
// 定位：管理员在 dashboard 内验证世界与移动逻辑的调试工具（非正式用户入口）。
// 约束（沙箱 iframe）：无原生 alert/confirm（自绘 modal）、无同源 localStorage
// （沙箱不含 allow-same-origin，故 player_id 只存内存、刷新即重新注册）、
// 文本一律 textContent 转义、纯 ES module 无构建。

const bridge = window.AstrBotPluginPage;
const $ = (sel) => document.querySelector(sel);

// ---------- 状态 ----------
let playerId = null;
let world = null; // { locations: [], exits: [], player, agent }

// ---------- 自绘弹窗 ----------
function openModal(title, body, actions) {
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

function hideModal() {
  $("#modal-mask").hidden = true;
}

$("#modal-close").addEventListener("click", hideModal);
$("#modal-mask").addEventListener("click", (event) => {
  if (event.target === $("#modal-mask")) {
    hideModal();
  }
});

// ---------- 后端调用（bridge 端点均为插件相对路径，如 "world/state"） ----------
async function registerPlayer() {
  const data = await bridge.apiPost("world/player/register", {});
  playerId = data.player_id;
  $("#player-id").textContent = `玩家 ${playerId}`;
  return data;
}

async function loadWorld() {
  world = await bridge.apiGet("world/state", { player_id: playerId });
  return world;
}

async function moveTo(exitId) {
  const scene = await bridge.apiPost("world/move", {
    player_id: playerId,
    exit_id: exitId,
  });
  // scene 即新场景（SceneView.as_dict），原地更新本地 world.player 后整体重绘
  world.player = {
    ...world.player,
    location_id: scene.location.id,
    location_name: scene.location.name,
    scene,
  };
  render();
}

// ---------- 图渲染（有向图，无空间语义；布局坐标仅为提示） ----------
function renderGraph() {
  const container = $("#graph");
  const errorEl = $("#map-error");
  container.textContent = "";
  errorEl.hidden = true;

  if (!world || !Array.isArray(world.locations) || world.locations.length === 0) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "世界为空。";
    container.appendChild(p);
    return;
  }

  // 坐标：优先取 layout；未设坐标的节点用确定性网格兜底布局（无需力导向库）
  const cols = Math.ceil(Math.sqrt(world.locations.length));
  const cell = 220;
  const locations = world.locations.map((l, index) => {
    if (
      l.layout &&
      typeof l.layout.x === "number" &&
      typeof l.layout.y === "number"
    ) {
      return { ...l, x: l.layout.x, y: l.layout.y };
    }
    const row = Math.floor(index / cols);
    const col = index % cols;
    return { ...l, x: col * cell + 100, y: row * cell + 100 };
  });

  const xs = locations.map((l) => l.x);
  const ys = locations.map((l) => l.y);
  const pad = 130;
  const width = Math.max(...xs) - Math.min(...xs) + pad * 2;
  const height = Math.max(...ys) - Math.min(...ys) + pad * 2;

  const SVG = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.classList.add("graph-svg");

  // 箭头标记（隐藏目标出口用虚线样式 + 独立箭头）
  const defs = document.createElementNS(SVG, "defs");
  for (const [markerId, pathClass] of [
    ["arrowhead", "arrow-path"],
    ["arrowhead-hidden", "arrow-path-hidden"],
  ]) {
    const marker = document.createElementNS(SVG, "marker");
    marker.setAttribute("id", markerId);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "10");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("orient", "auto-start-reverse");
    const arrow = document.createElementNS(SVG, "path");
    arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    arrow.setAttribute("class", pathClass);
    marker.appendChild(arrow);
    defs.appendChild(marker);
  }
  svg.appendChild(defs);

  // 边：按 (from,to) 分组，同组多条出边用垂直于连线的偏移曲线区分
  const fromById = new Map(locations.map((l) => [l.id, l]));
  const byPair = new Map();
  for (const e of world.exits || []) {
    const key = `${e.from_id}\u0000${e.to_id}`;
    if (!byPair.has(key)) {
      byPair.set(key, []);
    }
    byPair.get(key).push(e);
  }
  for (const group of byPair.values()) {
    const from = fromById.get(group[0].from_id);
    const to = fromById.get(group[0].to_id);
    if (!from || !to) {
      continue;
    }
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const len = Math.hypot(dx, dy) || 1;
    const px = -dy / len;
    const py = dx / len; // 连线方向垂直单位向量
    const count = group.length;
    const spacing = 34;
    group.forEach((e, index) => {
      const offset = count === 1 ? 0 : (index - (count - 1) / 2) * spacing;
      const midX = (from.x + to.x) / 2 + px * offset;
      const midY = (from.y + to.y) / 2 + py * offset;
      const d = `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;
      const hidden = e.reveal_target === false;
      const pathEl = document.createElementNS(SVG, "path");
      pathEl.setAttribute("d", d);
      pathEl.setAttribute(
        "marker-end",
        `url(#${hidden ? "arrowhead-hidden" : "arrowhead"})`,
      );
      pathEl.classList.add("edge");
      if (hidden) {
        pathEl.classList.add("edge-hidden");
      }
      svg.appendChild(pathEl);

      const labelEl = document.createElementNS(SVG, "text");
      labelEl.setAttribute("x", midX);
      labelEl.setAttribute("y", midY - 8);
      labelEl.setAttribute("text-anchor", "middle");
      labelEl.classList.add("edge-label");
      if (hidden) {
        labelEl.classList.add("edge-label-hidden");
      }
      labelEl.textContent = e.label;
      svg.appendChild(labelEl);
    });
  }

  // 节点
  const currentId = world.player ? world.player.location_id : null;
  const agentId = world.agent ? world.agent.location_id : null;
  for (const l of locations) {
    const g = document.createElementNS(SVG, "g");
    g.classList.add("node");
    if (l.id === currentId) {
      g.classList.add("node-current");
    } else if (l.id === agentId) {
      g.classList.add("node-agent");
    }

    const circle = document.createElementNS(SVG, "circle");
    circle.setAttribute("cx", l.x);
    circle.setAttribute("cy", l.y);
    circle.setAttribute("r", 26);
    g.appendChild(circle);

    const name = document.createElementNS(SVG, "text");
    name.setAttribute("x", l.x);
    name.setAttribute("y", l.y - 36);
    name.setAttribute("text-anchor", "middle");
    name.classList.add("node-name");
    name.textContent = l.name;
    g.appendChild(name);

    const idText = document.createElementNS(SVG, "text");
    idText.setAttribute("x", l.x);
    idText.setAttribute("y", l.y + 4);
    idText.setAttribute("text-anchor", "middle");
    idText.classList.add("node-id");
    idText.textContent = l.id;
    g.appendChild(idText);

    if (l.id === agentId && l.id !== currentId) {
      const badge = document.createElementNS(SVG, "text");
      badge.setAttribute("x", l.x);
      badge.setAttribute("y", l.y + 44);
      badge.setAttribute("text-anchor", "middle");
      badge.classList.add("agent-badge");
      badge.textContent = "Agent";
      g.appendChild(badge);
    }
    svg.appendChild(g);
  }

  container.appendChild(svg);
}

// ---------- 出口列表渲染 ----------
function renderExits() {
  const container = $("#exits");
  const emptyEl = $("#exits-empty");
  container.textContent = "";
  const scene = world && world.player ? world.player.scene : null;
  const exits = scene && Array.isArray(scene.exits) ? scene.exits : [];
  emptyEl.hidden = exits.length > 0;
  for (const exit of exits) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "exit-btn";

    const label = document.createElement("span");
    label.className = "exit-label";
    label.textContent = exit.label;

    const target = document.createElement("span");
    target.className = "exit-target";
    if (exit.target_name) {
      target.textContent = exit.target_name;
    } else {
      target.textContent = "???";
      target.classList.add("exit-target-hidden");
    }
    btn.appendChild(label);
    btn.appendChild(target);
    btn.addEventListener("click", () => void handleMove(exit.exit_id));
    container.appendChild(btn);
  }
}

async function handleMove(exitId) {
  try {
    await moveTo(exitId);
  } catch (error) {
    openModal("移动失败", error?.message || String(error));
  }
}

// ---------- 渲染 ----------
function render() {
  renderGraph();
  renderExits();
  const locEl = $("#loc-name");
  if (world && world.player) {
    locEl.textContent = world.player.location_name;
  } else if (playerId) {
    locEl.textContent = "玩家已失效，请重新注册";
  } else {
    locEl.textContent = "未注册";
  }
}

// ---------- 初始化 ----------
async function init() {
  try {
    await bridge.ready();
    await registerPlayer();
    await loadWorld();
    render();
  } catch (error) {
    openModal("初始化失败", error?.message || String(error));
  }
}

$("#btn-refresh").addEventListener("click", async () => {
  try {
    await loadWorld();
    render();
  } catch (error) {
    openModal("载入失败", error?.message || String(error));
  }
});

$("#btn-reregister").addEventListener("click", async () => {
  try {
    await registerPlayer();
    await loadWorld();
    render();
  } catch (error) {
    openModal("重新注册失败", error?.message || String(error));
  }
});

// 页面卸载尽力注销（超时清理兜底）
window.addEventListener("pagehide", () => {
  if (playerId) {
    try {
      void bridge.apiPost("world/player/deregister", { player_id: playerId });
    } catch {
      // 忽略
    }
  }
});

init();
