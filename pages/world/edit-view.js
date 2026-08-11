// edit-view.js — 编辑模式：可滚动画布 + 缩放，交替网格（地块/连接块/占位小格）+ 右侧详情栏
// 画布模型：绝对定位像素渲染（地块/连接块/占位小格按坐标摆放），外层 #graph 滚动平移；
// Ctrl/⌘+滚轮（含触控板捏合）以光标为中心缩放，工具条提供 − / + / 百分比 / 适应按钮。
// 画布尺寸 = 内容边界 + 四周固定留白（PAD）：空白延伸有限，只有新增地块（内容生长）
// 才驱动画布变大——点击内容边界外的空地块（HALO）或网格背景就近槽位新建即扩展世界。
// 视口与内容无交集时自动回到内容（保证已有地块总在可视范围内）。地块格只显示名字与 id
// （+ 出生点徽标）；连接块内画表示方向的线条（单向箭头 / 双向双箭头 / 空块=不连接），不画
// 方向文字；非相邻连接（目的地可自选）以虚线强调条显示在出发方向的连接块内（替代原
// 「分身」概念，无展开/收起按钮）。查看模式只读；编辑模式可增删改。

import { $, state, DIR_OFFSETS, computePositions, cellKey } from "./shared.js";
import {
  locationViewEl,
  locationEditEl,
  slotCreateEl,
  blockViewEl,
  blockEditEl,
  exitViewEl,
  exitEditEl,
} from "./edit-forms.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// 尺寸常量（与 style.css 的轨道尺寸一致）
const CELL = 120; // 地块轨道（正方形，更大便于阅读名字）
const CONN = 36; // 连接轨道（连接块 / 占位小格，更窄）
const GAP = 5; // 格间距
const PITCH = CELL + GAP + CONN + GAP; // 一个「地块轨 + 连接轨」周期
const BAR = CONN - 8; // 连接条长度（贴合窄连接块内宽）
const BAR_H = 22; // 连接条高度（含箭头）
const HALO = 2; // 内容边界外渲染的空地块圈数（可点击新建 → 世界向外生长）
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const ZOOM_STEP = 1.25;
const PAD = 800; // 内容四周固定留白（base px）：空白延伸有限，新增地块驱动画布生长

let onMutate = null; // app 传入：mutation 后 re-fetch 世界并整体重绘
let currentWorld = null;
let graphEl = null;
let canvasEl = null;
let gridEl = null;
let zoom = 1;
let contentW = 0; // 内容尺寸（base px），renderEdit 时计算
let contentH = 0;
let bounds = { minCol: 0, minRow: 0 }; // 供背景点击 → 槽位坐标换算

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const spawnBadgeText = (spawn, id) => {
  if (spawn.agent === id && spawn.player === id) return "出生点";
  if (spawn.agent === id) return "Agent 出生点";
  if (spawn.player === id) return "玩家出生点";
  return "";
};

// 轨道像素坐标（内容相对，base px）：地块轨宽 CELL、连接轨宽 CONN，轨间 GAP。
const locX = (rc) => rc * PITCH;
const connHX = (rc) => rc * PITCH + CELL + GAP;
const locY = (rr) => rr * PITCH;
const connVY = (rr) => rr * PITCH + CELL + GAP;
// 画布内绝对坐标（内容原点在 (PAD, PAD)）
const X = (rc) => PAD + locX(rc);
const HX = (rc) => PAD + connHX(rc);
const Y = (rr) => PAD + locY(rr);
const VY = (rr) => PAD + connVY(rr);

// 网格背景：交替轨道边界线（地块轨 / 连接轨），随缩放保持清晰
const GRID_LINE = "color-mix(in srgb, var(--border) 28%, transparent)";
const GRID_PATTERN = [
  `linear-gradient(to right, ${GRID_LINE} 0 1px, transparent 1px ${CELL}px, ${GRID_LINE} ${CELL}px ${CELL + GAP}px, transparent ${CELL + GAP}px ${CELL + GAP + CONN}px, ${GRID_LINE} ${CELL + GAP + CONN}px ${PITCH}px)`,
  `linear-gradient(to bottom, ${GRID_LINE} 0 1px, transparent 1px ${CELL}px, ${GRID_LINE} ${CELL}px ${CELL + GAP}px, transparent ${CELL + GAP}px ${CELL + GAP + CONN}px, ${GRID_LINE} ${CELL + GAP + CONN}px ${PITCH}px)`,
].join(", ");

export function initEditView(options) {
  onMutate = options.onMutate;
  graphEl = $("#graph");
  $("#edit-mode-view").addEventListener("click", () => setEditMode("view"));
  $("#edit-mode-edit").addEventListener("click", () => setEditMode("edit"));
  $("#btn-toggle-detail").addEventListener("click", toggleDetail);
  $("#detail-close").addEventListener("click", toggleDetail);

  // 缩放控件
  $("#zoom-out").addEventListener("click", () => setZoom(zoom / ZOOM_STEP));
  $("#zoom-in").addEventListener("click", () => setZoom(zoom * ZOOM_STEP));
  $("#zoom-pct").addEventListener("click", () => setZoom(1));
  $("#zoom-fit").addEventListener("click", fitView);
  graphEl.addEventListener("wheel", onWheel, { passive: false });
  graphEl.addEventListener("click", onBackgroundClick);
  updateZoomPct();

  setEditMode(state.editMode);
}

function setEditMode(mode) {
  state.editMode = mode;
  $("#edit-mode-view").classList.toggle("active", mode === "view");
  $("#edit-mode-edit").classList.toggle("active", mode === "edit");
  $("#edit-mode-view").setAttribute("aria-selected", mode === "view" ? "true" : "false");
  $("#edit-mode-edit").setAttribute("aria-selected", mode === "edit" ? "true" : "false");
  $("#view-edit").classList.toggle("mode-edit", mode === "edit");
  const hint = $("#edit-hint");
  if (hint) {
    hint.textContent =
      mode === "edit"
        ? "编辑模式：点击空地块可新建；点击网格背景可就近新建；点击地块可创建连接（目的地可自选）；点击连接块或连接条可编辑。Ctrl/滚轮缩放，滚动平移。"
        : "查看模式：只读浏览，点击地块 / 连接查看详情。";
  }
  renderEdit(currentWorld);
}

function toggleDetail() {
  state.detailOpen = !state.detailOpen;
  const btn = $("#btn-toggle-detail");
  if (btn) {
    btn.textContent = state.detailOpen ? "收起详情栏" : "展开详情栏";
  }
  renderEdit(currentWorld);
}

// 出口所在连接块的键：right→h:row:col（col 与 col+1 之间的横向间隙），
// left→h:row:col-1，down→v:col:row，up→v:col:row-1。
export function exitBlockKey(sc, sr, dir) {
  switch (dir) {
    case "right":
      return `h:${sr}:${sc}`;
    case "left":
      return `h:${sr}:${sc - 1}`;
    case "down":
      return `v:${sc}:${sr}`;
    default:
      return `v:${sc}:${sr - 1}`;
  }
}

// 解析连接块键 → 两侧地块 + 块内出口（导出供 edit-forms 的详情栏使用）
export function blockInfo(world, key, pos, locAt) {
  const [kind, a, b] = key.split(":");
  let left = null;
  let right = null;
  let top = null;
  let bottom = null;
  if (kind === "h") {
    const r = Number(a);
    const c = Number(b);
    left = locAt.get(cellKey(c, r)) || null;
    right = locAt.get(cellKey(c + 1, r)) || null;
  } else {
    const c = Number(a);
    const r = Number(b);
    top = locAt.get(cellKey(c, r)) || null;
    bottom = locAt.get(cellKey(c, r + 1)) || null;
  }
  const exits = [];
  for (const e of world?.exits || []) {
    const from = pos.get(e.from_id);
    if (!from) {
      continue;
    }
    if (exitBlockKey(from[0], from[1], e.direction) === key) {
      exits.push(e);
    }
  }
  return { key, kind, left, right, top, bottom, exits };
}

export function renderEdit(world) {
  currentWorld = world;
  const container = $("#graph");
  const errorEl = $("#map-error");
  const saveLeft = container.scrollLeft;
  const saveTop = container.scrollTop;
  const hadCanvas = canvasEl !== null;
  container.textContent = "";
  errorEl.hidden = true;

  const locations = Array.isArray(world?.locations) ? world.locations : null;
  canvasEl = null;
  gridEl = null;
  if (!locations) {
    container.appendChild(hintEl("世界数据不可用。"));
    renderPanel(world);
    return;
  }

  const pos = computePositions(locations);
  const locAt = new Map();
  for (const l of locations) {
    locAt.set(cellKey(pos.get(l.id)[0], pos.get(l.id)[1]), l);
  }

  // 地块边界
  let minCol = 0;
  let maxCol = 0;
  let minRow = 0;
  let maxRow = 0;
  let first = true;
  for (const [, [c, r]] of pos) {
    if (first) {
      minCol = maxCol = c;
      minRow = maxRow = r;
      first = false;
    } else {
      minCol = Math.min(minCol, c);
      maxCol = Math.max(maxCol, c);
      minRow = Math.min(minRow, r);
      maxRow = Math.max(maxRow, r);
    }
  }
  // 出口方向可能把连接块顶到边界外（如最右列地块向右的出口）：扩边界保证连接轨道存在
  for (const e of world.exits || []) {
    const from = pos.get(e.from_id);
    if (!from) {
      continue;
    }
    const [c, r] = from;
    const [dc, dr] = DIR_OFFSETS[e.direction] || DIR_OFFSETS.up;
    if (dc > 0 && maxCol === c) {
      maxCol++;
    } else if (dc < 0 && minCol === c) {
      minCol--;
    } else if (dr > 0 && maxRow === r) {
      maxRow++;
    } else if (dr < 0 && minRow === r) {
      minRow--;
    }
  }

  bounds = { minCol, minRow };
  const nCols = maxCol - minCol + 1;
  const nRows = maxRow - minRow + 1;
  contentW = CELL + (nCols - 1) * PITCH;
  contentH = CELL + (nRows - 1) * PITCH;

  const canvas = document.createElement("div");
  canvas.className = "world-canvas";
  const grid = document.createElement("div");
  grid.className = "world-grid";
  grid.style.backgroundImage = GRID_PATTERN;
  canvas.appendChild(grid);
  canvasEl = canvas;
  gridEl = grid;

  const spawn = world.spawn || {};
  const renderCell = (el, x, y, w, h) => {
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.style.width = `${w}px`;
    el.style.height = `${h}px`;
    canvas.appendChild(el);
  };

  // ---------- 地块格（含 HALO 空地块，可点击新建） ----------
  for (let c = minCol - HALO; c <= maxCol + HALO; c++) {
    for (let r = minRow - HALO; r <= maxRow + HALO; r++) {
      const loc = locAt.get(cellKey(c, r));
      const cell = document.createElement("div");
      if (loc) {
        cell.className = "loc-cell";
        const nameEl = document.createElement("div");
        nameEl.className = "loc-cell-name";
        nameEl.textContent = loc.name;
        cell.appendChild(nameEl);
        const idEl = document.createElement("div");
        idEl.className = "loc-cell-id";
        idEl.textContent = loc.id;
        cell.appendChild(idEl);
        const badgeText = spawnBadgeText(spawn, loc.id);
        if (badgeText) {
          const badge = document.createElement("span");
          badge.className = "spawn-badge";
          badge.textContent = badgeText;
          cell.appendChild(badge);
        }
        if (state.selection?.kind === "location" && state.selection.id === loc.id) {
          cell.classList.add("selected");
        }
        cell.addEventListener("click", () => selectLocation(loc));
      } else {
        cell.className = "loc-cell loc-cell-empty";
        cell.title = "空地";
        if (state.editMode === "edit") {
          cell.addEventListener("click", () => selectSlot(c, r));
        }
      }
      renderCell(cell, X(c - minCol), Y(r - minRow), CELL, CELL);
    }
  }

  // ---------- 占位小格（偶数-偶数轨道）：无意义，纯排版 ----------
  for (let c = minCol - HALO; c <= maxCol + HALO - 1; c++) {
    for (let r = minRow - HALO; r <= maxRow + HALO - 1; r++) {
      const ph = document.createElement("div");
      ph.className = "ph-cell";
      renderCell(ph, HX(c - minCol), VY(r - minRow), CONN, CONN);
    }
  }

  // 出口按连接块分组
  const blockMap = new Map(); // key -> [{exit, side, special}]
  for (const e of world.exits || []) {
    const from = pos.get(e.from_id);
    if (!from) {
      continue;
    }
    const [dc, dr] = DIR_OFFSETS[e.direction] || DIR_OFFSETS.up;
    const tc = from[0] + dc;
    const tr = from[1] + dr;
    const key = exitBlockKey(from[0], from[1], e.direction);
    const destPos = pos.get(e.to_id);
    // 目标主位不在出发方向的相邻格 → 特殊连接（虚线强调条，目的地自选）
    const special = !destPos || destPos[0] !== tc || destPos[1] !== tr;
    const side =
      e.direction === "right"
        ? "r"
        : e.direction === "left"
          ? "l"
          : e.direction === "down"
            ? "d"
            : "u";
    if (!blockMap.has(key)) {
      blockMap.set(key, []);
    }
    blockMap.get(key).push({ exit: e, side, special });
  }

  // ---------- 横向连接块（地块轨之间的间隙） ----------
  for (let c = minCol - HALO; c <= maxCol + HALO - 1; c++) {
    for (let r = minRow - HALO; r <= maxRow + HALO; r++) {
      const key = `h:${r}:${c}`;
      renderBlock(canvas, blockMap.get(key) || [], HX(c - minCol), Y(r - minRow), true, key);
    }
  }

  // ---------- 纵向连接块（连接轨之间的间隙） ----------
  for (let c = minCol - HALO; c <= maxCol + HALO; c++) {
    for (let r = minRow - HALO; r <= maxRow + HALO - 1; r++) {
      const key = `v:${c}:${r}`;
      renderBlock(canvas, blockMap.get(key) || [], X(c - minCol), VY(r - minRow), false, key);
    }
  }

  if (locations.length === 0) {
    const p = hintEl(
      state.editMode === "edit"
        ? "世界为空：点击网格（编辑模式）创建第一个地块。"
        : "世界为空。"
    );
    p.classList.add("world-hint");
    container.appendChild(p);
  }

  container.appendChild(canvas);
  applyCanvas();
  if (hadCanvas) {
    container.scrollLeft = saveLeft;
    container.scrollTop = saveTop;
  } else {
    centerContent();
  }
  ensureContentVisible();
  renderPanel(world);
}

// 块内连接条集合：同侧多条普通边合并为一条方向条（两侧都有 → 双向条）；
// 特殊连接（目的地自选）各自独立为虚线强调条。
function collectBars(group, isH) {
  const norm = { r: [], l: [], d: [], u: [] };
  const specials = [];
  for (const item of group) {
    if (item.special) {
      specials.push(item);
    } else {
      norm[item.side].push(item);
    }
  }
  const bars = [];
  const pushSide = (side, arrowSide) => {
    if (norm[side].length > 0) {
      bars.push({ side: arrowSide, items: norm[side], special: false });
    }
  };
  if (isH) {
    if (norm.r.length > 0 && norm.l.length > 0) {
      bars.push({ side: "b", items: [...norm.r, ...norm.l], special: false });
    } else {
      pushSide("r", "r");
      pushSide("l", "l");
    }
  } else {
    if (norm.d.length > 0 && norm.u.length > 0) {
      bars.push({ side: "b", items: [...norm.d, ...norm.u], special: false });
    } else {
      pushSide("d", "d");
      pushSide("u", "u");
    }
  }
  for (const s of specials) {
    bars.push({ side: s.side, items: [s], special: true });
  }
  return bars;
}

function renderBlock(canvas, group, x, y, isH, key) {
  const block = document.createElement("div");
  block.className = "conn-block";
  block.style.left = `${x}px`;
  block.style.top = `${y}px`;
  block.style.width = `${isH ? CONN : CELL}px`;
  block.style.height = `${isH ? CELL : CONN}px`;
  if (group.length === 0) {
    // 空连接块不可点击：创建连接只能从地块面板发起（延伸出去）
    block.classList.add("conn-block-empty");
    block.title = "该方向没有连接；创建连接请先点击地块";
    canvas.appendChild(block);
    return;
  }
  block.classList.add("has-exits");
  if (state.selection?.kind === "block" && state.selection.key === key) {
    block.classList.add("selected");
  }
  block.addEventListener("click", () => selectBlock(key));
  for (const bar of collectBars(group, isH)) {
    const isSelected =
      state.selection?.kind === "exit" &&
      bar.items.some((item) => state.selection.id === item.exit.id);
    block.appendChild(connBar(bar, isH, isSelected, key));
  }
  canvas.appendChild(block);
}

// 方向条：横向 / 纵向，单向箭头或双向双箭头；特殊连接为虚线强调条
function connBar(bar, isH, isSelected, blockKey) {
  const [w, h] = isH ? [BAR, BAR_H] : [BAR_H, BAR];
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  svg.classList.add("conn-bar");
  if (bar.special) {
    svg.classList.add("conn-special");
  }
  if (isSelected) {
    svg.classList.add("selected");
  }
  const mid = (isH ? h : w) / 2;
  const line = document.createElementNS(SVG_NS, "line");
  if (isH) {
    line.setAttribute("x1", 2);
    line.setAttribute("y1", mid);
    line.setAttribute("x2", w - 2);
    line.setAttribute("y2", mid);
  } else {
    line.setAttribute("x1", mid);
    line.setAttribute("y1", 2);
    line.setAttribute("x2", mid);
    line.setAttribute("y2", h - 2);
  }
  svg.appendChild(line);
  const addArrow = (points) => {
    const poly = document.createElementNS(SVG_NS, "polygon");
    poly.setAttribute("points", points);
    svg.appendChild(poly);
  };
  const s = bar.side;
  if (isH) {
    if (s === "r" || s === "b") {
      addArrow(`${w - 9},${mid - 4} ${w - 9},${mid + 4} ${w - 2},${mid}`);
    }
    if (s === "l" || s === "b") {
      addArrow(`${9},${mid - 4} ${9},${mid + 4} ${2},${mid}`);
    }
  } else {
    if (s === "d" || s === "b") {
      addArrow(`${mid - 4},${h - 9} ${mid + 4},${h - 9} ${mid},${h - 2}`);
    }
    if (s === "u" || s === "b") {
      addArrow(`${mid - 4},${9} ${mid + 4},${9} ${mid},${2}`);
    }
  }
  // 单条连接 → 点击直接选中该出口；多条合并 → 选中整个连接块
  const singleExit = bar.items.length === 1 ? bar.items[0].exit : null;
  svg.addEventListener("click", (event) => {
    event.stopPropagation();
    if (singleExit) {
      selectExit(singleExit.id);
    } else {
      selectBlock(blockKey);
    }
  });
  svg.title = bar.items.map((item) => item.exit.label).join(" / ");
  return svg;
}

// ---------- 缩放 / 平移 / 无限延伸 ----------
function onWheel(event) {
  if (!event.ctrlKey && !event.metaKey) {
    return; // 普通滚轮 = 滚动平移（浏览器默认）
  }
  event.preventDefault();
  const rect = graphEl.getBoundingClientRect();
  const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  setZoom(zoom * factor, event.clientX - rect.left, event.clientY - rect.top);
}

function setZoom(next, cx, cy) {
  const rect = graphEl.getBoundingClientRect();
  if (cx == null) {
    cx = rect.width / 2;
    cy = rect.height / 2;
  }
  const old = zoom;
  zoom = clamp(next, ZOOM_MIN, ZOOM_MAX);
  const k = zoom / old;
  if (canvasEl) {
    // 保持光标下的内容点不动
    graphEl.scrollLeft = (cx + graphEl.scrollLeft) * k - cx;
    graphEl.scrollTop = (cy + graphEl.scrollTop) * k - cy;
  }
  applyCanvas();
  updateZoomPct();
  ensureContentVisible();
}

function fitView() {
  if (!currentWorld || !Array.isArray(currentWorld.locations) || contentW <= 0) {
    return;
  }
  const rect = graphEl.getBoundingClientRect();
  const target = Math.min((rect.width - 48) / contentW, (rect.height - 48) / contentH);
  setZoom(clamp(target, ZOOM_MIN, ZOOM_MAX));
  const cL = PAD * zoom;
  const cT = PAD * zoom;
  graphEl.scrollLeft = Math.max(0, cL + (contentW * zoom - graphEl.clientWidth) / 2);
  graphEl.scrollTop = Math.max(0, cT + (contentH * zoom - graphEl.clientHeight) / 2);
}

function centerContent() {
  if (!canvasEl) {
    return;
  }
  graphEl.scrollLeft = Math.max(0, PAD * zoom + (contentW * zoom - graphEl.clientWidth) / 2);
  graphEl.scrollTop = Math.max(0, PAD * zoom + (contentH * zoom - graphEl.clientHeight) / 2);
}

// 画布尺寸 = 内容 + 四周固定留白（未缩放），视觉缩放全部由 transform: scale(zoom) 完成；
// 布局尺寸不能乘 zoom，否则与 transform 叠加造成双重缩放，滚动范围与网格线都会失真。
function applyCanvas() {
  if (!canvasEl) {
    return;
  }
  const baseW = contentW + 2 * PAD;
  const baseH = contentH + 2 * PAD;
  canvasEl.style.width = `${baseW}px`;
  canvasEl.style.height = `${baseH}px`;
  canvasEl.style.transform = `scale(${zoom})`;
  gridEl.style.width = `${baseW}px`;
  gridEl.style.height = `${baseH}px`;
  gridEl.style.backgroundSize = `${PITCH}px ${PITCH}px`;
  // 网格周期从内容原点（PAD）起算，与地块/连接轨道对齐
  gridEl.style.backgroundPosition = `${PAD}px ${PAD}px`;
}

// 视口与内容无交集 → 回到内容：保证已有地块总在可视范围内（修复 hidden 状态或
// 内容变小后滚动位置错乱导致的「找不到地块」）
function ensureContentVisible() {
  if (!canvasEl) {
    return;
  }
  const vw = graphEl.clientWidth;
  const vh = graphEl.clientHeight;
  if (vw <= 0 || vh <= 0) {
    return; // 视图尚未布局（hidden）时不动滚动位置
  }
  const vL = graphEl.scrollLeft;
  const vT = graphEl.scrollTop;
  const cL = PAD * zoom;
  const cT = PAD * zoom;
  const cR = cL + contentW * zoom;
  const cB = cT + contentH * zoom;
  if (cR <= vL || cB <= vT || cL >= vL + vw || cT >= vT + vh) {
    centerContent();
  }
}

function updateZoomPct() {
  const el = $("#zoom-pct");
  if (el) {
    el.textContent = `${Math.round(zoom * 100)}%`;
  }
}

// 编辑模式点击网格背景 → 就近槽位新建地块
function onBackgroundClick(event) {
  if (state.editMode !== "edit") {
    return;
  }
  if (event.target !== gridEl) {
    return;
  }
  if (!currentWorld || !Array.isArray(currentWorld.locations)) {
    return;
  }
  const rect = graphEl.getBoundingClientRect();
  const bx = (event.clientX - rect.left + graphEl.scrollLeft) / zoom - PAD;
  const by = (event.clientY - rect.top + graphEl.scrollTop) / zoom - PAD;
  const rc = Math.floor(bx / PITCH);
  const rr = Math.floor(by / PITCH);
  selectSlot(bounds.minCol + rc, bounds.minRow + rr);
}

// ---------- 选择与详情栏 ----------
function selectLocation(loc) {
  state.selection = { kind: "location", id: loc.id };
  renderEdit(currentWorld);
}

function selectSlot(col, row) {
  state.selection = { kind: "slot", col, row };
  renderEdit(currentWorld);
}

function selectBlock(key) {
  state.selection = { kind: "block", key };
  renderEdit(currentWorld);
}

function selectExit(id) {
  state.selection = { kind: "exit", id };
  renderEdit(currentWorld);
}

function renderPanel(world) {
  const panel = $("#detail-panel");
  const body = $("#detail-body");
  body.textContent = "";
  panel.hidden = !state.detailOpen;
  if (!state.detailOpen) {
    return;
  }

  const bus = {
    onSubmit: () => onMutate(),
    onCreatedLocation: (id) => {
      state.selection = { kind: "location", id };
    },
    onSelectExit: (id) => selectExit(id),
  };

  const sel = state.selection;
  if (!sel) {
    $("#detail-title").textContent = "详情";
    body.appendChild(hintEl("未选择任何地块或连接。"));
    return;
  }

  const locations = (world && world.locations) || [];
  const exits = (world && world.exits) || [];
  const byId = new Map(locations.map((l) => [l.id, l]));
  const pos = computePositions(locations);
  const locAt = new Map();
  for (const l of locations) {
    locAt.set(cellKey(pos.get(l.id)[0], pos.get(l.id)[1]), l);
  }

  if (sel.kind === "location") {
    const loc = byId.get(sel.id);
    $("#detail-title").textContent = "地块";
    if (!loc) {
      body.appendChild(hintEl("该地块已不存在。"));
      return;
    }
    const exitsFrom = exits.filter((e) => e.from_id === loc.id);
    body.appendChild(
      state.editMode === "edit"
        ? locationEditEl(loc, exitsFrom, byId, bus, pos)
        : locationViewEl(loc, exitsFrom, byId, bus)
    );
  } else if (sel.kind === "slot") {
    $("#detail-title").textContent = "新建地块";
    body.appendChild(slotCreateEl(locations, sel.col, sel.row, byId, bus));
  } else if (sel.kind === "block") {
    $("#detail-title").textContent = "连接";
    const info = blockInfo(world, sel.key, pos, locAt);
    body.appendChild(
      state.editMode === "edit" ? blockEditEl(info, byId, bus) : blockViewEl(info, byId, bus)
    );
  } else if (sel.kind === "exit") {
    const exit = exits.find((e) => e.id === sel.id);
    $("#detail-title").textContent = "出口";
    if (!exit) {
      body.appendChild(hintEl("该出口已不存在。"));
      return;
    }
    body.appendChild(
      state.editMode === "edit" ? exitEditEl(exit, byId, bus) : exitViewEl(exit, byId, bus)
    );
  }
}

function hintEl(text) {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = text;
  return p;
}
