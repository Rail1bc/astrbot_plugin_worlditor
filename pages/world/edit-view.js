// edit-view.js — 编辑模式：网格表格（上帝视角）+ 可视化编辑交互
// 表格：每个地块一个格子（layout 整数坐标=列/行）；连接必须相邻——出口方向决定
// 目标相邻格；目标主位不在该格时该格显示目标「分身」（虚线框，可收起/展开）；
// 间隙内画方向标签（↑↓←→ + 出口 label，隐藏目标仍显示真名——上帝视角）；
// 出生点徽标（agent / 玩家注册起始）；重名地块悬浮全体高亮。
// 点击主格 → 地块表单；点击分身 → 出口表单。

import { $, openModal, state } from "./shared.js";
import { exitForm, locationForm } from "./edit-forms.js";
import {
  DIR_OFFSETS,
  cellKey,
  computeAvatars,
  computePositions,
} from "./shared.js";

const DIR_CHAR = { up: "↑", right: "→", down: "↓", left: "←" };

const CELL_W = 170;
const CELL_H = 104;
const GAP = 34;
const PAD = 20;

let onMutate = null;

export function initEditView(options) {
  onMutate = options.onMutate;
  $("#btn-new-location").addEventListener("click", () => openLocationForm(null));
  $("#btn-new-exit").addEventListener("click", () => openExitForm(null));
  $("#btn-toggle-avatars").addEventListener("click", toggleAllAvatars);
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

  const pos = computePositions(world.locations);
  const allAvatars = computeAvatars(world, pos);
  const collapsed = state.collapsedExits;

  // 非收起分身按格分组（一格可能叠多个分身）
  const avatarByCell = new Map();
  for (const a of allAvatars) {
    if (collapsed.has(a.exit.id)) {
      continue;
    }
    const k = cellKey(a.col, a.row);
    if (!avatarByCell.has(k)) {
      avatarByCell.set(k, []);
    }
    avatarByCell.get(k).push(a);
  }

  // 占用的格：主位 + 非收起分身（决定网格范围）
  const occupiedCells = new Set();
  for (const [, cell] of pos) {
    occupiedCells.add(cellKey(cell[0], cell[1]));
  }
  for (const k of avatarByCell.keys()) {
    occupiedCells.add(k);
  }

  let minCol = 0;
  let maxCol = 0;
  let minRow = 0;
  let maxRow = 0;
  for (const k of occupiedCells) {
    const [c, r] = k.split(",").map(Number);
    minCol = Math.min(minCol, c);
    maxCol = Math.max(maxCol, c);
    minRow = Math.min(minRow, r);
    maxRow = Math.max(maxRow, r);
  }
  const nCols = maxCol - minCol + 1;
  const nRows = maxRow - minRow + 1;

  // 像素换算（含内边距；绝对定位元素相对 padding 盒）
  const colCx = (gcol) => PAD + gcol * (CELL_W + GAP) + CELL_W / 2;
  const rowCy = (grow) => PAD + grow * (CELL_H + GAP) + CELL_H / 2;
  const gapX = (gcol) => PAD + (gcol + 1) * CELL_W + gcol * GAP + GAP / 2;
  const gapY = (grow) => PAD + (grow + 1) * CELL_H + grow * GAP + GAP / 2;

  const grid = document.createElement("div");
  grid.className = "edit-grid";
  grid.style.gridTemplateColumns = `repeat(${nCols}, ${CELL_W}px)`;
  grid.style.gridTemplateRows = `repeat(${nRows}, ${CELL_H}px)`;
  grid.style.width = `${nCols * CELL_W + (nCols - 1) * GAP + PAD * 2}px`;
  grid.style.padding = `${PAD}px`;
  container.appendChild(grid);

  const cellEls = new Map(); // location_id -> 元素列表（同名联动高亮用）
  const byId = new Map(world.locations.map((l) => [l.id, l]));
  const spawn = world.spawn || {};

  // ---------- 主地块格 ----------
  for (const l of world.locations) {
    const [col, row] = pos.get(l.id);
    const cell = document.createElement("div");
    cell.className = "grid-cell";
    cell.style.gridColumn = `${col - minCol + 1}`;
    cell.style.gridRow = `${row - minRow + 1}`;

    const nameEl = document.createElement("div");
    nameEl.className = "grid-cell-name";
    nameEl.textContent = l.name;
    cell.appendChild(nameEl);

    const idEl = document.createElement("div");
    idEl.className = "grid-cell-id";
    idEl.textContent = l.id;
    cell.appendChild(idEl);

    // 出生点徽标
    if (spawn.agent === l.id || spawn.player === l.id) {
      const badge = document.createElement("span");
      badge.className = "spawn-badge";
      if (spawn.agent === l.id && spawn.player === l.id) {
        badge.textContent = "出生点";
      } else if (spawn.agent === l.id) {
        badge.textContent = "Agent 出生点";
      } else {
        badge.textContent = "玩家出生点";
      }
      cell.appendChild(badge);
    }

    // 已收起的出口：折叠成出发地块格内的标签（点击展开）
    for (const a of allAvatars) {
      if (!collapsed.has(a.exit.id) || a.exit.from_id !== l.id) {
        continue;
      }
      const target = byId.get(a.targetId);
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "collapsed-tag";
      tag.textContent = `${DIR_CHAR[a.exit.direction] || "→"} ${target ? target.name : a.targetId}`;
      tag.title = a.exit.label;
      tag.addEventListener("click", (event) => {
        event.stopPropagation();
        collapsed.delete(a.exit.id);
        onMutate();
      });
      cell.appendChild(tag);
    }

    // 重名地块悬浮全体高亮（识别重名）
    const sameName = world.locations.filter((o) => o.name === l.name);
    const highlight = () => {
      for (const o of sameName) {
        for (const el of cellEls.get(o.id) || []) {
          el.classList.add("cell-highlight");
        }
      }
    };
    const unhighlight = () => {
      for (const o of sameName) {
        for (const el of cellEls.get(o.id) || []) {
          el.classList.remove("cell-highlight");
        }
      }
    };
    cell.addEventListener("mouseenter", highlight);
    cell.addEventListener("mouseleave", unhighlight);
    cell.addEventListener("click", () => openLocationForm(l));
    if (!cellEls.has(l.id)) {
      cellEls.set(l.id, []);
    }
    cellEls.get(l.id).push(cell);
    grid.appendChild(cell);
  }

  // ---------- 分身格 ----------
  for (const [k, avatars] of avatarByCell) {
    const [col, row] = k.split(",").map(Number);
    const cell = document.createElement("div");
    cell.className = "grid-cell grid-cell-avatar";
    cell.style.gridColumn = `${col - minCol + 1}`;
    cell.style.gridRow = `${row - minRow + 1}`;
    for (const a of avatars) {
      const target = byId.get(a.targetId);
      const rowEl = document.createElement("div");
      rowEl.className = "avatar-row";
      const nameEl = document.createElement("span");
      nameEl.className = "avatar-name";
      nameEl.textContent = target ? target.name : a.targetId;
      const badge = document.createElement("span");
      badge.className = "avatar-badge";
      badge.textContent = "分身";
      const fold = document.createElement("button");
      fold.type = "button";
      fold.className = "avatar-fold";
      fold.textContent = "收起";
      fold.addEventListener("click", (event) => {
        event.stopPropagation();
        collapsed.add(a.exit.id);
        onMutate();
      });
      rowEl.appendChild(nameEl);
      rowEl.appendChild(badge);
      rowEl.appendChild(fold);
      rowEl.addEventListener("click", () => openExitForm(a.exit));
      cell.appendChild(rowEl);
    }
    grid.appendChild(cell);
  }

  // ---------- 间隙连接标签：每条出口画在 from→目标格 的间隙中点（按间隙分组堆叠） ----------
  const gapGroups = new Map();
  for (const e of world.exits || []) {
    if (collapsed.has(e.id)) {
      continue;
    }
    const from = pos.get(e.from_id);
    const target = pos.get(e.to_id);
    if (!from || !target) {
      continue;
    }
    const [dc, dr] = DIR_OFFSETS[e.direction] || DIR_OFFSETS.up;
    const tc = from[0] + dc;
    const tr = from[1] + dr;
    let gapKey;
    let gRow;
    let gCol;
    if (tc > from[0]) {
      gapKey = `h:${from[1]}:${from[0]}`;
      gRow = from[1];
      gCol = from[0];
    } else if (tc < from[0]) {
      gapKey = `h:${from[1]}:${tc}`;
      gRow = from[1];
      gCol = tc;
    } else if (tr < from[1]) {
      gapKey = `v:${tr}:${from[0]}`;
      gRow = tr;
      gCol = from[0];
    } else {
      gapKey = `v:${from[1]}:${from[0]}`;
      gRow = from[1];
      gCol = from[0];
    }
    if (!gapGroups.has(gapKey)) {
      gapGroups.set(gapKey, []);
    }
    gapGroups.get(gapKey).push({ exit: e, gRow, gCol });
  }

  for (const group of gapGroups.values()) {
    const { gRow, gCol } = group[0];
    const horizontal =
      group[0].exit.direction === "left" || group[0].exit.direction === "right";
    const n = group.length;
    group.forEach((item, i) => {
      const { exit } = item;
      const chip = document.createElement("div");
      chip.className =
        exit.reveal_target === false ? "edge-chip edge-chip-hidden" : "edge-chip";
      chip.textContent = `${DIR_CHAR[exit.direction] || "→"} ${exit.label}`;
      chip.title = exit.label;
      chip.style.transform = "translate(-50%, -50%)";
      if (horizontal) {
        chip.style.left = `${gapX(gCol - minCol)}px`;
        chip.style.top = `${rowCy(gRow - minRow) + (i - (n - 1) / 2) * 26}px`;
      } else {
        chip.style.left = `${colCx(gCol - minCol) + (i - (n - 1) / 2) * 168}px`;
        chip.style.top = `${gapY(gRow - minRow)}px`;
      }
      grid.appendChild(chip);
    });
  }

  syncAvatarToggleLabel(world, allAvatars);
}

function syncAvatarToggleLabel(world, allAvatars) {
  const btn = $("#btn-toggle-avatars");
  if (!btn) {
    return;
  }
  const ids = allAvatars.map((a) => a.exit.id);
  const allCollapsed =
    ids.length > 0 && ids.every((id) => state.collapsedExits.has(id));
  btn.textContent = allCollapsed ? "展开全部分身" : "收起全部分身";
}

function toggleAllAvatars() {
  const world = state.world;
  if (!world) {
    return;
  }
  const all = computeAvatars(world, computePositions(world.locations)).map(
    (a) => a.exit.id
  );
  const allCollapsed =
    all.length > 0 && all.every((id) => state.collapsedExits.has(id));
  if (allCollapsed) {
    for (const id of all) {
      state.collapsedExits.delete(id);
    }
  } else {
    for (const id of all) {
      state.collapsedExits.add(id);
    }
  }
  onMutate();
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
