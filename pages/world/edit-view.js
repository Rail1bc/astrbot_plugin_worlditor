// edit-view.js — 编辑模式：固定边界网格视图 + 右键拖动画布 + 缩略图
// 地图 = 固定大小的网格（内容边界外任意方向再延伸 EDGE 个空地块）；地块身份 = (row, col)，
// 直接落位在网格轨道上；连接不再是独立实体，而是每个地块 4 方向槽位的平行路径，绘制在
// 地块间隙中的 SVG 连线上（一个槽位一条路径一根线）：
//   - 路径主目标 = 该方向的相邻地块：连线跨过间隙到相邻地块（带箭头）
//   - 主目标非相邻：带箭头虚线 + 末端小标记格（非常规连接）
//   - 主目标不存在（死引用）：红色虚线 + 红标记格（区别于「显式禁用」——禁用的槽不画）
//   同间隙内多条路径（含对侧槽位）沿间隙垂直方向均布错开，互不遮挡。
// 查看模式只显示已存在的地块与连接；编辑模式额外显示全部空地块（可点击新建 / 从模板
// 创建）；点击间隙空白编辑两侧槽位。坐标只读（移动走「移动地块」工具）。
// 视图隐藏横竖滚动条：右键拖动 / 滚轮平移，Ctrl/⌘+滚轮以光标为中心缩放；右下角
// 缩略图显示全图与当前视口范围（可收起/展开、可拖动，点击或拖动可移动视口）。

import {
  $,
  state,
  DIR_OFFSETS,
  locAt,
  cellKey,
  pathDead,
  mainTarget,
  targetName,
  scheduleText,
} from "./shared.js";
import {
  locationViewEl,
  locationEditEl,
  cellCreateEl,
  slotViewEl,
  slotEditEl,
  gapViewEl,
  gapEditEl,
  templatesEl,
} from "./edit-forms.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// 尺寸常量（与 style.css 一致）
const CELL = 120; // 地块格边长
const GAP = 46; // 地块间隙（连接线所在区域）
const PITCH = CELL + GAP; // 一个地块周期
const EDGE = 10; // 内容边界外延伸的空地块圈数（地图固定大小）
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const ZOOM_STEP = 1.25;
const MARKER_SIZE = 14; // 非常规连接末端小标记格
const MARKER_OFF = 6; // 标记格中心相对箭头尖的距离
const MINIMAP_MAX = 190; // 缩略图最大边长

let onMutate = null; // app 传入：mutation 后 re-fetch 世界并整体重绘
let currentWorld = null;
let graphEl = null;
let canvasEl = null;
let gridEl = null;
let zoom = 1;
let mapW = 0; // 画布（地图）尺寸（base px）
let mapH = 0;
let contentW = 0; // 内容（实际地块范围）尺寸与原点
let contentH = 0;
let contentOx = 0;
let contentOy = 0;
let bounds = { minCol: 0, minRow: 0, maxCol: 0, maxRow: 0 };
let minimapEl = null;
let minimapViewRect = null;
let minimapCollapsed = false;
let minimapPos = null;
let suppressClickUntil = 0; // 触控拖动后抑制误点
let panState = null; // 右键拖动状态
let touchPan = null;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const maxScrollX = () => Math.max(0, mapW * zoom - graphEl.clientWidth);
const maxScrollY = () => Math.max(0, mapH * zoom - graphEl.clientHeight);

function clampScroll() {
  graphEl.scrollLeft = clamp(graphEl.scrollLeft, 0, maxScrollX());
  graphEl.scrollTop = clamp(graphEl.scrollTop, 0, maxScrollY());
}

// 编辑模式网格背景：每个地块周期画出四周边线（PITCH = CELL + GAP）
const GRID_LINE = "color-mix(in srgb, var(--border) 30%, transparent)";
const GRID_PATTERN = [
  `linear-gradient(to right, ${GRID_LINE} 0 1px, transparent 1px ${CELL}px, ${GRID_LINE} ${CELL}px ${CELL + 1}px, transparent ${CELL + 1}px ${PITCH}px)`,
  `linear-gradient(to bottom, ${GRID_LINE} 0 1px, transparent 1px ${CELL}px, ${GRID_LINE} ${CELL}px ${CELL + 1}px, transparent ${CELL + 1}px ${PITCH}px)`,
].join(", ");

export function initEditView(options) {
  onMutate = options.onMutate;
  graphEl = $("#graph");
  $("#edit-mode-view").addEventListener("click", () => setEditMode("view"));
  $("#edit-mode-edit").addEventListener("click", () => setEditMode("edit"));
  $("#btn-toggle-detail").addEventListener("click", toggleDetail);
  $("#detail-close").addEventListener("click", toggleDetail);
  $("#btn-templates").addEventListener("click", selectTemplates);

  // 缩放控件
  $("#zoom-out").addEventListener("click", () => setZoom(zoom / ZOOM_STEP));
  $("#zoom-in").addEventListener("click", () => setZoom(zoom * ZOOM_STEP));
  $("#zoom-pct").addEventListener("click", () => setZoom(1));
  $("#zoom-fit").addEventListener("click", fitView);

  graphEl.addEventListener("wheel", onWheel, { passive: false });
  graphEl.addEventListener("contextmenu", (e) => e.preventDefault());
  graphEl.addEventListener("mousedown", onMouseDown);
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
  graphEl.addEventListener("touchstart", onTouchStart, { passive: true });
  graphEl.addEventListener("touchmove", onTouchMove, { passive: false });
  graphEl.addEventListener("touchend", onTouchEnd);
  // 触控拖动结束后抑制紧随其后的点击（避免拖动画布误选地块/连接）
  graphEl.addEventListener(
    "click",
    (e) => {
      if (Date.now() < suppressClickUntil) {
        e.stopPropagation();
      }
    },
    true
  );
  window.addEventListener("resize", () => {
    if (!canvasEl) return;
    clampScroll();
    updateMinimap();
    anchorMinimap();
  });
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
        ? "编辑模式：点击空地块新建 / 点击连接线编辑槽位 / 点击间隙编辑两侧槽位 / 右键拖动平移，Ctrl+滚轮缩放。"
        : "查看模式：只读浏览，点击地块 / 连接查看详情；右键拖动平移地图。";
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

// 方向槽位所在的间隙键：right→h:row:col（col 与 col+1 之间的横向间隙），
// left→h:row:col-1，down→v:col:row，up→v:col:row-1。
export function slotGapKey(row, col, dir) {
  switch (dir) {
    case "right":
      return `h:${row}:${col}`;
    case "left":
      return `h:${row}:${col - 1}`;
    case "down":
      return `v:${row}:${col}`;
    default:
      return `v:${row - 1}:${col}`;
  }
}

export function renderEdit(world) {
  currentWorld = world;
  const container = graphEl;
  const errorEl = $("#map-error");
  const saveLeft = container.scrollLeft;
  const saveTop = container.scrollTop;
  const hadCanvas = canvasEl !== null;
  container.textContent = "";
  errorEl.hidden = true;

  const locations = Array.isArray(world?.locations) ? world.locations : null;
  canvasEl = null;
  gridEl = null;
  minimapEl = null;
  minimapViewRect = null;
  if (!locations) {
    container.appendChild(hintEl("世界数据不可用。"));
    renderPanel(world);
    return;
  }

  const byLoc = locAt(locations);

  // 内容边界（实际地块范围）
  let wMinC = 0;
  let wMaxC = -1;
  let wMinR = 0;
  let wMaxR = -1;
  for (const l of locations) {
    if (wMaxC < 0) {
      wMinC = wMaxC = l.col;
      wMinR = wMaxR = l.row;
    } else {
      if (l.col < wMinC) wMinC = l.col;
      if (l.col > wMaxC) wMaxC = l.col;
      if (l.row < wMinR) wMinR = l.row;
      if (l.row > wMaxR) wMaxR = l.row;
    }
  }

  // 地图固定边界：内容边界外各延伸 EDGE 个空地块
  let minCol;
  let maxCol;
  let minRow;
  let maxRow;
  if (wMaxC < 0) {
    minCol = -EDGE;
    maxCol = EDGE - 1;
    minRow = -EDGE;
    maxRow = EDGE - 1;
  } else {
    minCol = wMinC - EDGE;
    maxCol = wMaxC + EDGE;
    minRow = wMinR - EDGE;
    maxRow = wMaxR + EDGE;
  }
  bounds = { minCol, minRow, maxCol, maxRow };
  mapW = (maxCol - minCol + 1) * PITCH;
  mapH = (maxRow - minRow + 1) * PITCH;
  if (wMaxC < 0) {
    contentW = mapW;
    contentH = mapH;
    contentOx = 0;
    contentOy = 0;
  } else {
    contentW = (wMaxC - wMinC + 1) * PITCH;
    contentH = (wMaxR - wMinR + 1) * PITCH;
    contentOx = (wMinC - minCol) * PITCH;
    contentOy = (wMinR - minRow) * PITCH;
  }

  const canvas = document.createElement("div");
  canvas.className = "world-canvas";
  canvasEl = canvas;
  canvas.addEventListener("click", onCanvasClick);

  // 网格背景（编辑模式显示；查看模式只显示地块与连接）
  if (state.editMode === "edit") {
    const grid = document.createElement("div");
    grid.className = "world-grid";
    grid.style.backgroundImage = GRID_PATTERN;
    canvas.appendChild(grid);
    gridEl = grid;
  }

  // 连接层（SVG 连线，位于地块格之下）
  canvas.appendChild(buildConnectionLayer(world, byLoc));

  // 地块格：查看模式只渲染已存在地块；编辑模式另渲染全部空地块
  for (let c = minCol; c <= maxCol; c++) {
    for (let r = minRow; r <= maxRow; r++) {
      const loc = byLoc.get(cellKey(c, r));
      if (!loc && state.editMode !== "edit") {
        continue;
      }
      const cell = document.createElement("div");
      if (loc) {
        cell.className = "loc-cell";
        const nameEl = document.createElement("div");
        nameEl.className = "loc-cell-name";
        nameEl.textContent = loc.name;
        cell.appendChild(nameEl);
        const idEl = document.createElement("div");
        idEl.className = "loc-cell-id";
        idEl.textContent = `(${loc.row}, ${loc.col})`;
        cell.appendChild(idEl);
        const badgeText = cellBadge(world, loc);
        if (badgeText) {
          const badge = document.createElement("span");
          badge.className = "spawn-badge";
          badge.textContent = badgeText;
          cell.appendChild(badge);
        }
        if (
          state.selection?.kind === "location" &&
          state.selection.row === loc.row &&
          state.selection.col === loc.col
        ) {
          cell.classList.add("selected");
        }
        cell.addEventListener("click", (e) => {
          if (e.button !== 0) return;
          selectLocation(loc);
        });
      } else {
        cell.className = "loc-cell loc-cell-empty";
        cell.title = "空地（点击新建）";
        cell.addEventListener("click", (e) => {
          if (e.button !== 0) return;
          selectCell(r, c);
        });
      }
      renderCell(cell, (c - minCol) * PITCH, (r - minRow) * PITCH, CELL, CELL);
    }
  }

  if (locations.length === 0) {
    const p = hintEl(
      state.editMode === "edit" ? "世界为空：点击任意空地块创建第一个地块。" : "世界为空。"
    );
    p.classList.add("world-hint");
    container.appendChild(p);
  }

  container.appendChild(canvas);
  buildMinimap(world, byLoc);
  applyCanvas();
  if (hadCanvas) {
    container.scrollLeft = clamp(saveLeft, 0, maxScrollX());
    container.scrollTop = clamp(saveTop, 0, maxScrollY());
  } else {
    fitView();
  }
  renderPanel(world);
}

const renderCell = (el, x, y, w, h) => {
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  el.style.width = `${w}px`;
  el.style.height = `${h}px`;
  canvasEl.appendChild(el);
};

function cellBadge(world, loc) {
  const spawn = world.spawn;
  if (spawn && loc.row === spawn.row && loc.col === spawn.col) {
    return "出生点";
  }
  const agent = world.agent;
  if (agent && loc.row === agent.row && loc.col === agent.col) {
    return "Agent";
  }
  const player = world.player;
  if (player && loc.row === player.row && loc.col === player.col) {
    return "玩家";
  }
  return "";
}

// ---------- 连接层 ----------
// 收集每个间隙的槽位路径条目（gapMap: key → {kind, row, col, stubs: []}）
function collectGapStubs(world, byLoc) {
  const gapMap = new Map();
  // 间隙锚点 = 间隙条左上角所在地块坐标（slotGapKey 的 key 中 row/col），
  // 不是来源地块：up/left 的间隙位于来源地块的上一格/左一格。
  const gapAnchor = (row, col, dir) =>
    dir === "left" ? [row, col - 1] : dir === "up" ? [row - 1, col] : [row, col];
  const addStub = (key, r, c, stub) => {
    let entry = gapMap.get(key);
    if (!entry) {
      entry = { row: r, col: c, stubs: [] };
      gapMap.set(key, entry);
    }
    entry.stubs.push(stub);
  };
  for (const loc of world.locations || []) {
    for (const dir of ["up", "right", "down", "left"]) {
      const slot = loc.connections?.[dir];
      if (!slot || !slot.enabled || !Array.isArray(slot.paths) || slot.paths.length === 0) {
        continue;
      }
      const [dr, dc] = DIR_OFFSETS[dir];
      for (const path of slot.paths) {
        const dead = pathDead(byLoc, path);
        const t = mainTarget(path);
        const adjacent =
          !dead && !!t && t.row === loc.row + dr && t.col === loc.col + dc;
        const targetLabel = dead
          ? null
          : targetName(byLoc, t, world.spawn?.map_id);
        const stub = { loc, direction: dir, path, dead, adjacent, targetLabel };
        const [anchorRow, anchorCol] = gapAnchor(loc.row, loc.col, dir);
        addStub(slotGapKey(loc.row, loc.col, dir), anchorRow, anchorCol, stub);
      }
    }
  }
  return gapMap;
}

function buildConnectionLayer(world, byLoc) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${mapW} ${mapH}`);
  svg.setAttribute("width", mapW);
  svg.setAttribute("height", mapH);
  svg.classList.add("conn-layer");

  const gapMap = collectGapStubs(world, byLoc);

  // 横向间隙：c ∈ [minCol, maxCol-1]，r ∈ [minRow, maxRow]
  for (let c = bounds.minCol; c < bounds.maxCol; c++) {
    for (let r = bounds.minRow; r <= bounds.maxRow; r++) {
      const entry = gapMap.get(`h:${r}:${c}`);
      if (entry) {
        renderGap(svg, entry, true);
      }
    }
  }
  // 纵向间隙：c ∈ [minCol, maxCol]，r ∈ [minRow, maxRow-1]
  for (let c = bounds.minCol; c <= bounds.maxCol; c++) {
    for (let r = bounds.minRow; r < bounds.maxRow; r++) {
      const entry = gapMap.get(`v:${r}:${c}`);
      if (entry) {
        renderGap(svg, entry, false);
      }
    }
  }
  return svg;
}

// 在间隙内绘制槽位路径线；多条线沿垂直方向均布错开，互不遮挡
function renderGap(svg, entry, isH) {
  const { row, col, stubs } = entry;
  const cx = (col - bounds.minCol) * PITCH;
  const cy = (row - bounds.minRow) * PITCH;
  stubs.forEach((stub, i) => {
    drawStub(svg, stub, isH, cx, cy, (i + 1) / (stubs.length + 1));
  });
}

function drawStub(svg, stub, isH, cx, cy, frac) {
  const { loc, direction, path, dead, adjacent, targetLabel } = stub;
  const source = isH ? (direction === "right" ? "l" : "r") : direction === "down" ? "t" : "b";
  // 平行线沿间隙条全程均布（条长 = CELL）：横向间隙为上下方向堆叠、纵向间隙为左右方向堆叠
  const perp = frac * CELL;
  let x1 = 0;
  let y1 = 0;
  let x2 = 0;
  let y2 = 0;
  let arrow = null; // {x, y, dir}
  let marker = null; // {x, y}

  if (isH) {
    const y = cy + perp;
    const gx1 = cx + CELL;
    const gx2 = cx + CELL + GAP;
    const mid = gx1 + GAP / 2;
    if (source === "l") {
      x1 = gx1;
      y1 = y;
      x2 = adjacent ? gx2 : mid;
      y2 = y;
      arrow = { x: x2, y, dir: "r" };
      if (!adjacent) {
        marker = { x: mid + MARKER_OFF, y };
      }
    } else {
      x1 = gx2;
      y1 = y;
      x2 = adjacent ? gx1 : mid;
      y2 = y;
      arrow = { x: x2, y, dir: "l" };
      if (!adjacent) {
        marker = { x: mid - MARKER_OFF, y };
      }
    }
  } else {
    const x = cx + perp;
    const gy1 = cy + CELL;
    const gy2 = cy + CELL + GAP;
    const mid = gy1 + GAP / 2;
    if (source === "t") {
      x1 = x;
      y1 = gy1;
      x2 = x;
      y2 = adjacent ? gy2 : mid;
      arrow = { x, y: y2, dir: "d" };
      if (!adjacent) {
        marker = { x, y: mid + MARKER_OFF };
      }
    } else {
      x1 = x;
      y1 = gy2;
      x2 = x;
      y2 = adjacent ? gy1 : mid;
      arrow = { x, y: y2, dir: "u" };
      if (!adjacent) {
        marker = { x, y: mid - MARKER_OFF };
      }
    }
  }

  const g = document.createElementNS(SVG_NS, "g");
  g.classList.add("conn-line");
  if (dead) {
    g.classList.add("conn-dead");
  } else if (!adjacent) {
    g.classList.add("conn-special");
  }
  const sel = state.selection;
  if (
    sel?.kind === "slot" &&
    sel.row === loc.row &&
    sel.col === loc.col &&
    sel.direction === direction
  ) {
    g.classList.add("selected");
  }

  const ln = document.createElementNS(SVG_NS, "line");
  ln.setAttribute("x1", x1);
  ln.setAttribute("y1", y1);
  ln.setAttribute("x2", x2);
  ln.setAttribute("y2", y2);
  g.appendChild(ln);

  if (arrow) {
    const poly = document.createElementNS(SVG_NS, "polygon");
    poly.setAttribute("points", arrowPoints(arrow));
    g.appendChild(poly);
  }
  if (marker) {
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", marker.x - MARKER_SIZE / 2);
    rect.setAttribute("y", marker.y - MARKER_SIZE / 2);
    rect.setAttribute("width", MARKER_SIZE);
    rect.setAttribute("height", MARKER_SIZE);
    rect.setAttribute("rx", 3);
    rect.classList.add("spec-marker");
    g.appendChild(rect);
  }

  g.addEventListener("click", (event) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    selectSlot(loc.row, loc.col, direction);
  });
  const targetText = targetLabel || "（死引用：目标缺失）";
  const title = document.createElementNS(SVG_NS, "title");
  title.textContent = `${scheduleText(path.label) || "（无标签）"} → ${targetText}`;
  g.appendChild(title);
  svg.appendChild(g);
}

function arrowPoints(a) {
  const A = 9;
  const W = 4;
  if (a.dir === "r") return `${a.x - A},${a.y - W} ${a.x - A},${a.y + W} ${a.x},${a.y}`;
  if (a.dir === "l") return `${a.x + A},${a.y - W} ${a.x + A},${a.y + W} ${a.x},${a.y}`;
  if (a.dir === "d") return `${a.x - W},${a.y - A} ${a.x + W},${a.y - A} ${a.x},${a.y}`;
  return `${a.x - W},${a.y + A} ${a.x + W},${a.y + A} ${a.x},${a.y}`;
}

// ---------- 平移 / 缩放 ----------
function onMouseDown(event) {
  if (event.button !== 2) {
    return;
  }
  event.preventDefault();
  panState = {
    x: event.clientX,
    y: event.clientY,
    sl: graphEl.scrollLeft,
    st: graphEl.scrollTop,
  };
  graphEl.classList.add("panning");
}

function onMouseMove(event) {
  if (!panState) {
    return;
  }
  graphEl.scrollLeft = clamp(panState.sl - (event.clientX - panState.x), 0, maxScrollX());
  graphEl.scrollTop = clamp(panState.st - (event.clientY - panState.y), 0, maxScrollY());
  updateMinimap();
}

function onMouseUp() {
  if (!panState) {
    return;
  }
  panState = null;
  graphEl.classList.remove("panning");
}

function onTouchStart(event) {
  if (event.touches.length !== 1) {
    return;
  }
  const t = event.touches[0];
  touchPan = { x: t.clientX, y: t.clientY, sl: graphEl.scrollLeft, st: graphEl.scrollTop, moved: false };
}

function onTouchMove(event) {
  if (!touchPan || event.touches.length !== 1) {
    return;
  }
  const t = event.touches[0];
  const dx = t.clientX - touchPan.x;
  const dy = t.clientY - touchPan.y;
  if (Math.abs(dx) + Math.abs(dy) > 8) {
    touchPan.moved = true;
    event.preventDefault();
    graphEl.scrollLeft = clamp(touchPan.sl - dx, 0, maxScrollX());
    graphEl.scrollTop = clamp(touchPan.st - dy, 0, maxScrollY());
    updateMinimap();
  }
}

function onTouchEnd() {
  if (touchPan?.moved) {
    suppressClickUntil = Date.now() + 400;
  }
  touchPan = null;
}

function onWheel(event) {
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    const rect = graphEl.getBoundingClientRect();
    const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    setZoom(zoom * factor, event.clientX - rect.left, event.clientY - rect.top);
    return;
  }
  // 普通滚轮平移（Shift + 滚轮 → 横向）
  event.preventDefault();
  let dx = event.deltaX;
  let dy = event.deltaY;
  if (event.shiftKey) {
    dx += event.deltaY;
    dy = 0;
  }
  graphEl.scrollLeft = clamp(graphEl.scrollLeft + dx, 0, maxScrollX());
  graphEl.scrollTop = clamp(graphEl.scrollTop + dy, 0, maxScrollY());
  updateMinimap();
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
  clampScroll();
  updateZoomPct();
  updateMinimap();
}

function fitView() {
  if (!canvasEl || contentW <= 0) {
    return;
  }
  const rect = graphEl.getBoundingClientRect();
  const target = Math.min((rect.width - 40) / contentW, (rect.height - 40) / contentH);
  zoom = clamp(target, ZOOM_MIN, ZOOM_MAX);
  applyCanvas();
  updateZoomPct();
  centerOnContent();
  updateMinimap();
}

function centerOnContent() {
  if (!canvasEl) {
    return;
  }
  const left = contentOx * zoom + (contentW * zoom - graphEl.clientWidth) / 2;
  const top = contentOy * zoom + (contentH * zoom - graphEl.clientHeight) / 2;
  graphEl.scrollLeft = clamp(left, 0, maxScrollX());
  graphEl.scrollTop = clamp(top, 0, maxScrollY());
}

// 画布尺寸 = 地图固定边界；视觉缩放全部由 transform: scale(zoom) 完成，
// 布局尺寸不乘 zoom（避免与 transform 双重缩放）。
function applyCanvas() {
  if (!canvasEl) {
    return;
  }
  canvasEl.style.width = `${mapW}px`;
  canvasEl.style.height = `${mapH}px`;
  canvasEl.style.transform = `scale(${zoom})`;
  if (gridEl) {
    gridEl.style.width = `${mapW}px`;
    gridEl.style.height = `${mapH}px`;
    gridEl.style.backgroundSize = `${PITCH}px ${PITCH}px`;
    gridEl.style.backgroundPosition = "0 0";
  }
}

// 编辑模式点击网格背景 → 命中地块 / 空地块 / 间隙（两侧槽位）
function onCanvasClick(event) {
  if (state.editMode !== "edit" || event.button !== 0) {
    return;
  }
  if (event.target !== canvasEl && event.target !== gridEl) {
    return;
  }
  if (!canvasEl || !currentWorld || !Array.isArray(currentWorld.locations)) {
    return;
  }
  const rect = graphEl.getBoundingClientRect();
  const bx = (event.clientX - rect.left + graphEl.scrollLeft) / zoom;
  const by = (event.clientY - rect.top + graphEl.scrollTop) / zoom;
  const px = bounds.minCol + Math.floor(bx / PITCH);
  const py = bounds.minRow + Math.floor(by / PITCH);
  if (px < bounds.minCol || px > bounds.maxCol || py < bounds.minRow || py > bounds.maxRow) {
    return;
  }
  const lx = bx - (px - bounds.minCol) * PITCH;
  const ly = by - (py - bounds.minRow) * PITCH;
  const inCellX = lx < CELL;
  const inCellY = ly < CELL;
  if (inCellX && inCellY) {
    // 地块轨道：命中已有地块 → 详情；否则新建
    const loc = byLocAt(px, py);
    if (loc) {
      selectLocation(loc);
    } else {
      selectCell(py, px);
    }
  } else if (!inCellX) {
    selectGap(`h:${py}:${px}`); // 横向间隙
  } else {
    selectGap(`v:${py}:${px}`); // 纵向间隙（角落命中横向间隙，忽略）
  }
}

function byLocAt(col, row) {
  const locations = currentWorld?.locations || [];
  for (const l of locations) {
    if (l.col === col && l.row === row) {
      return l;
    }
  }
  return null;
}

function updateZoomPct() {
  const el = $("#zoom-pct");
  if (el) {
    el.textContent = `${Math.round(zoom * 100)}%`;
  }
}

// ---------- 缩略图 ----------
function buildMinimap(world, byLoc) {
  if (minimapEl) {
    minimapEl.remove();
    minimapEl = null;
  }
  const m = document.createElement("div");
  m.className = "minimap";
  if (minimapCollapsed) {
    m.classList.add("collapsed");
  }

  const head = document.createElement("div");
  head.className = "minimap-head";
  const title = document.createElement("span");
  title.className = "minimap-title";
  title.textContent = "全图";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "minimap-toggle";
  toggle.textContent = minimapCollapsed ? "＋" : "－";
  toggle.title = minimapCollapsed ? "展开全图" : "收起全图";
  head.appendChild(title);
  head.appendChild(toggle);

  const body = document.createElement("div");
  body.className = "minimap-body";
  body.hidden = minimapCollapsed;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add("minimap-svg");
  body.appendChild(svg);

  m.appendChild(head);
  m.appendChild(body);
  graphEl.appendChild(m);
  minimapEl = m;
  minimapViewRect = null;

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMinimap();
  });
  head.addEventListener("mousedown", startMoveMinimap);
  body.addEventListener("mousedown", startMinimapPan);

  const scale = Math.min(MINIMAP_MAX / mapW, MINIMAP_MAX / mapH);
  const svgW = Math.max(28, Math.round(mapW * scale));
  const svgH = Math.max(28, Math.round(mapH * scale));
  body.style.width = `${svgW}px`;
  body.style.height = `${svgH}px`;
  svg.setAttribute("viewBox", `0 0 ${mapW} ${mapH}`);
  svg.setAttribute("width", svgW);
  svg.setAttribute("height", svgH);

  const bg = document.createElementNS(SVG_NS, "rect");
  bg.setAttribute("x", 0);
  bg.setAttribute("y", 0);
  bg.setAttribute("width", mapW);
  bg.setAttribute("height", mapH);
  bg.classList.add("minimap-bg");
  svg.appendChild(bg);

  for (const loc of byLoc.values()) {
    const dot = document.createElementNS(SVG_NS, "rect");
    dot.setAttribute("x", (loc.col - bounds.minCol) * PITCH + 2);
    dot.setAttribute("y", (loc.row - bounds.minRow) * PITCH + 2);
    dot.setAttribute("width", CELL - 4);
    dot.setAttribute("height", CELL - 4);
    dot.setAttribute("rx", 3);
    dot.classList.add("minimap-loc");
    svg.appendChild(dot);
  }

  minimapViewRect = document.createElementNS(SVG_NS, "rect");
  minimapViewRect.classList.add("minimap-view");
  svg.appendChild(minimapViewRect);
  updateMinimap();
  anchorMinimap();
}

// 缩略图定位（fixed 视口坐标）：未拖动时锚定图区域右下角，拖动后沿用保存的位置
function anchorMinimap() {
  if (!minimapEl) {
    return;
  }
  if (minimapPos) {
    minimapEl.style.left = `${minimapPos.left}px`;
    minimapEl.style.top = `${minimapPos.top}px`;
    return;
  }
  const gr = graphEl.getBoundingClientRect();
  minimapEl.style.left = `${gr.right - minimapEl.offsetWidth - 14}px`;
  minimapEl.style.top = `${gr.bottom - minimapEl.offsetHeight - 14}px`;
}

function toggleMinimap() {
  minimapCollapsed = !minimapCollapsed;
  minimapEl.classList.toggle("collapsed", minimapCollapsed);
  const body = minimapEl.querySelector(".minimap-body");
  body.hidden = minimapCollapsed;
  const toggle = minimapEl.querySelector(".minimap-toggle");
  toggle.textContent = minimapCollapsed ? "＋" : "－";
  toggle.title = minimapCollapsed ? "展开全图" : "收起全图";
}

function startMoveMinimap(event) {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  const rect = minimapEl.getBoundingClientRect();
  const offX = event.clientX - rect.left;
  const offY = event.clientY - rect.top;
  const move = (ev) => {
    const gr = graphEl.getBoundingClientRect();
    const maxL = gr.right - rect.width - 4;
    const maxT = gr.bottom - rect.height - 4;
    const left = clamp(ev.clientX - offX, gr.left + 4, maxL);
    const top = clamp(ev.clientY - offY, gr.top + 4, maxT);
    minimapEl.style.left = `${left}px`;
    minimapEl.style.top = `${top}px`;
    minimapPos = { left, top };
  };
  window.addEventListener("mousemove", move);
  window.addEventListener(
    "mouseup",
    () => window.removeEventListener("mousemove", move),
    { once: true }
  );
}

function startMinimapPan(event) {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  moveMinimapViewport(event);
  window.addEventListener("mousemove", moveMinimapViewport);
  window.addEventListener(
    "mouseup",
    () => window.removeEventListener("mousemove", moveMinimapViewport),
    { once: true }
  );
}

function moveMinimapViewport(event) {
  const body = minimapEl.querySelector(".minimap-body");
  const rect = body.getBoundingClientRect();
  const fx = (event.clientX - rect.left) / rect.width;
  const fy = (event.clientY - rect.top) / rect.height;
  const mx = fx * mapW;
  const my = fy * mapH;
  graphEl.scrollLeft = clamp(mx * zoom - graphEl.clientWidth / 2, 0, maxScrollX());
  graphEl.scrollTop = clamp(my * zoom - graphEl.clientHeight / 2, 0, maxScrollY());
  updateMinimap();
}

function updateMinimap() {
  if (!minimapViewRect || !graphEl) {
    return;
  }
  const vw = graphEl.clientWidth / zoom;
  const vh = graphEl.clientHeight / zoom;
  minimapViewRect.setAttribute("x", graphEl.scrollLeft / zoom);
  minimapViewRect.setAttribute("y", graphEl.scrollTop / zoom);
  minimapViewRect.setAttribute("width", vw);
  minimapViewRect.setAttribute("height", vh);
}

// ---------- 选择与详情栏 ----------
function selectLocation(loc) {
  state.selection = { kind: "location", row: loc.row, col: loc.col };
  renderEdit(currentWorld);
}

function selectCell(row, col) {
  state.selection = { kind: "cell", row, col };
  renderEdit(currentWorld);
}

function selectSlot(row, col, direction) {
  state.selection = { kind: "slot", row, col, direction };
  renderEdit(currentWorld);
}

function selectGap(key) {
  state.selection = { kind: "gap", key };
  renderEdit(currentWorld);
}

function selectTemplates() {
  state.selection = { kind: "templates" };
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
    onSelectSlot: (row, col, direction) => selectSlot(row, col, direction),
    onSelectTemplates: () => selectTemplates(),
  };

  const sel = state.selection;
  if (!sel) {
    $("#detail-title").textContent = "详情";
    body.appendChild(hintEl("未选择任何地块或连接。"));
    return;
  }

  const locations = (world && world.locations) || [];
  const byLoc = locAt(locations);

  if (sel.kind === "location") {
    const loc = byLoc.get(cellKey(sel.col, sel.row));
    $("#detail-title").textContent = "地块";
    if (!loc) {
      body.appendChild(hintEl("该地块已不存在。"));
      return;
    }
    body.appendChild(
      state.editMode === "edit"
        ? locationEditEl(loc, byLoc, world, bus)
        : locationViewEl(loc, byLoc, bus)
    );
  } else if (sel.kind === "cell") {
    $("#detail-title").textContent = "新建地块";
    body.appendChild(cellCreateEl(world, sel.row, sel.col, bus));
  } else if (sel.kind === "slot") {
    const loc = byLoc.get(cellKey(sel.col, sel.row));
    $("#detail-title").textContent = "连接槽位";
    if (!loc) {
      body.appendChild(hintEl("该地块已不存在。"));
      return;
    }
    const slot = loc.connections?.[sel.direction];
    if (!slot) {
      body.appendChild(hintEl("该方向槽位已不存在。"));
      return;
    }
    body.appendChild(
      state.editMode === "edit"
        ? slotEditEl(loc, sel.direction, slot, bus)
        : slotViewEl(loc, sel.direction, slot, byLoc, bus)
    );
  } else if (sel.kind === "gap") {
    $("#detail-title").textContent = "间隙两侧槽位";
    body.appendChild(
      state.editMode === "edit" ? gapEditEl(world, sel.key, bus) : gapViewEl(world, sel.key, bus)
    );
  } else if (sel.kind === "templates") {
    $("#detail-title").textContent = "地块模板";
    body.appendChild(templatesEl(world, bus));
  }
}

function hintEl(text) {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = text;
  return p;
}
