// edit-view.js — 编辑模式：全图可视化（上帝视角）+ 可视化编辑交互
// 地块永远显示真名（无 ???）；边画方向信息（单向箭头 / 双向双箭头）；
// 重名地块悬浮全体高亮（识别重名）；点击节点/边 → 编辑表单。

import { $, openModal, state } from "./shared.js";
import { exitForm, locationForm } from "./edit-forms.js";

const SVG = "http://www.w3.org/2000/svg";

let onMutate = null;

export function initEditView(options) {
  onMutate = options.onMutate;
  $("#btn-new-location").addEventListener("click", () => openLocationForm(null));
  $("#btn-new-exit").addEventListener("click", () => openExitForm(null));
}

function buildLayout(locations) {
  // 坐标：优先取 layout；未设坐标的节点用确定性网格兜底（无需力导向库）
  const cols = Math.ceil(Math.sqrt(locations.length));
  const cell = 220;
  return locations.map((l, index) => {
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
}

function hasReverse(exits, exit) {
  return exits.some((o) => o.from_id === exit.to_id && o.to_id === exit.from_id);
}

export function renderEdit(world) {
  const container = $("#graph");
  const errorEl = $("#map-error");
  container.textContent = "";
  errorEl.hidden = true;

  if (!world || !Array.isArray(world.locations) || world.locations.length === 0) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "世界为空，点「＋ 新建地块」开始。";
    container.appendChild(p);
    return;
  }

  const locations = buildLayout(world.locations);
  const xs = locations.map((l) => l.x);
  const ys = locations.map((l) => l.y);
  const pad = 130;
  const width = Math.max(...xs) - Math.min(...xs) + pad * 2;
  const height = Math.max(...ys) - Math.min(...ys) + pad * 2;

  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.classList.add("graph-svg");

  // 箭头标记（auto-start-reverse：同一标记同时用于起点/终点，双向边双箭头）
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
      const double = hasReverse(world.exits, e);

      // 命中层：加宽透明描边便于点击
      const hit = document.createElementNS(SVG, "path");
      hit.setAttribute("d", d);
      hit.classList.add("edge-hit");
      hit.addEventListener("click", () => openExitForm(e));
      svg.appendChild(hit);

      const pathEl = document.createElementNS(SVG, "path");
      pathEl.setAttribute("d", d);
      if (double) {
        pathEl.setAttribute(
          "marker-start",
          `url(#${hidden ? "arrowhead-hidden" : "arrowhead"})`
        );
      }
      pathEl.setAttribute(
        "marker-end",
        `url(#${hidden ? "arrowhead-hidden" : "arrowhead"})`
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
  const nodeEls = new Map();

  for (const l of locations) {
    const g = document.createElementNS(SVG, "g");
    g.classList.add("node");
    if (l.id === currentId) {
      g.classList.add("node-current");
    } else if (l.id === agentId) {
      g.classList.add("node-agent");
    }

    // 同名分组悬浮联动（编辑视图专用，识别重名地块）
    const sameName = world.locations.filter((o) => o.name === l.name);
    const highlight = () => {
      for (const o of sameName) {
        const el = nodeEls.get(o.id);
        if (el) {
          el.classList.add("node-highlight");
        }
      }
    };
    const unhighlight = () => {
      for (const o of sameName) {
        const el = nodeEls.get(o.id);
        if (el) {
          el.classList.remove("node-highlight");
        }
      }
    };
    g.addEventListener("mouseenter", highlight);
    g.addEventListener("mouseleave", unhighlight);
    g.addEventListener("click", () => openLocationForm(l));
    nodeEls.set(l.id, g);

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

function openLocationForm(loc) {
  state.lastNodeId = loc ? loc.id : null;
  const form = locationForm(loc, () => onMutate());
  openModal(loc ? "编辑地块" : "新建地块", form.el, form.actions);
}

function openExitForm(exit) {
  const form = exitForm(exit, () => onMutate());
  openModal(exit ? "编辑出口" : "新建出口", form.el, form.actions);
}
