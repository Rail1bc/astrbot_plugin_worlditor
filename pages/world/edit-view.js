// edit-view.js — 编辑模式：交替网格表格（地块/连接块/占位小格）+ 右侧详情栏
// 表格轨道：奇数轨 = 地块（正方形），偶数轨 = 连接块（长方形）/ 占位小格。
// 地块格只显示名字与 id（+ 出生点徽标）；连接块内画表示方向的线条（单向箭头 /
// 双向双箭头 / 空块=不连接），不画方向文字；非相邻连接（目的地可自选）以虚线
// 强调条显示在出发方向的连接块内（替代原「分身」概念，无展开/收起按钮）。
// 点击空地块（编辑模式）→ 新建地块；点击地块 → 详情栏（含创建连接）；点击
// 连接块 / 连接条 → 查看 / 编辑该出口。右侧详情栏可收起/展开。
// 查看模式只读；编辑模式可增删改。

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
const CELL = 88; // 地块轨道（正方形）
const CONN = 64; // 连接轨道（连接块 / 占位小格）
const BAR = 48; // 连接条长度
const BAR_H = 22; // 连接条高度（含箭头）

let onMutate = null; // app 传入：mutation 后 re-fetch 世界并整体重绘
let currentWorld = null;

const spawnBadgeText = (spawn, id) => {
  if (spawn.agent === id && spawn.player === id) return "出生点";
  if (spawn.agent === id) return "Agent 出生点";
  if (spawn.player === id) return "玩家出生点";
  return "";
};

export function initEditView(options) {
  onMutate = options.onMutate;
  $("#edit-mode-view").addEventListener("click", () => setEditMode("view"));
  $("#edit-mode-edit").addEventListener("click", () => setEditMode("edit"));
  $("#btn-toggle-detail").addEventListener("click", toggleDetail);
  $("#detail-close").addEventListener("click", toggleDetail);
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
        ? "编辑模式：点击空地块可新建地块；点击地块可创建连接（目的地可自选）；点击连接块或连接条可编辑。"
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
  container.textContent = "";
  errorEl.hidden = true;

  if (!world || !Array.isArray(world.locations) || world.locations.length === 0) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "世界为空。";
    container.appendChild(p);
    renderPanel(world);
    return;
  }

  const pos = computePositions(world.locations);
  const locAt = new Map();
  for (const l of world.locations) {
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

  // 表格：奇数轨 = 地块（CELL），偶数轨 = 连接（CONN）
  const table = document.createElement("div");
  table.className = "edit-table";
  table.style.gridTemplateColumns = `repeat(${maxCol - minCol}, ${CELL}px ${CONN}px) ${CELL}px`;
  table.style.gridTemplateRows = `repeat(${maxRow - minRow}, ${CELL}px ${CONN}px) ${CELL}px`;
  container.appendChild(table);

  const tCol = (c) => 2 * (c - minCol) + 1;
  const tRow = (r) => 2 * (r - minRow) + 1;
  const spawn = world.spawn || {};

  // ---------- 地块格（奇数-奇数轨道） ----------
  for (let c = minCol; c <= maxCol; c++) {
    for (let r = minRow; r <= maxRow; r++) {
      const loc = locAt.get(cellKey(c, r));
      const cell = document.createElement("div");
      cell.style.gridColumn = String(tCol(c));
      cell.style.gridRow = String(tRow(r));
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
      table.appendChild(cell);
    }
  }

  // ---------- 占位小格（偶数-偶数轨道）：无意义，纯排版 ----------
  for (let c = minCol; c < maxCol; c++) {
    for (let r = minRow; r < maxRow; r++) {
      const ph = document.createElement("div");
      ph.className = "ph-cell";
      ph.style.gridColumn = String(tCol(c) + 1);
      ph.style.gridRow = String(tRow(r) + 1);
      table.appendChild(ph);
    }
  }

  // ---------- 横向连接块（偶数列-奇数行） ----------
  for (let c = minCol; c < maxCol; c++) {
    for (let r = minRow; r <= maxRow; r++) {
      const key = `h:${r}:${c}`;
      renderBlock(table, blockMap.get(key) || [], tCol(c) + 1, tRow(r), true, key);
    }
  }

  // ---------- 纵向连接块（奇数列-偶数行） ----------
  for (let c = minCol; c <= maxCol; c++) {
    for (let r = minRow; r < maxRow; r++) {
      const key = `v:${c}:${r}`;
      renderBlock(table, blockMap.get(key) || [], tCol(c), tRow(r) + 1, false, key);
    }
  }

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

function renderBlock(table, group, tCol, tRow, isH, key) {
  const block = document.createElement("div");
  block.className = "conn-block";
  block.style.gridColumn = String(tCol);
  block.style.gridRow = String(tRow);
  if (group.length === 0) {
    block.classList.add("conn-block-empty");
    block.title = "该方向没有连接";
  } else {
    block.classList.add("has-exits");
  }
  if (state.selection?.kind === "block" && state.selection.key === key) {
    block.classList.add("selected");
  }
  block.addEventListener("click", () => selectBlock(key));
  if (group.length === 0) {
    table.appendChild(block);
    return;
  }
  for (const bar of collectBars(group, isH)) {
    const isSelected =
      state.selection?.kind === "exit" &&
      bar.items.some((item) => state.selection.id === item.exit.id);
    block.appendChild(connBar(bar, isH, isSelected, key));
  }
  table.appendChild(block);
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
      state.editMode === "edit"
        ? blockEditEl(info, byId, bus, pos)
        : blockViewEl(info, byId, bus)
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
