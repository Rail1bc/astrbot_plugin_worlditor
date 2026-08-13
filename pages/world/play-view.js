// play-view.js — 玩家模式：当前地块 + 4 方向槽位（每槽平行路径逐一列出可选）
// 地图 div（#play-map）：中间是只含地块名称的小块，上/右/下/左各放一个方向槽位格，
// 格内是该方向所有可选路径的按钮列表（每条显示 label + 主目标名，隐藏目标显示 ???）；
// 无路径的方向槽位不渲染；槽位格收缩到内容大小并居中，长名在格内换行；目标名只取
// scene.paths 的 target_name，绝不全图查名；连线层只画中心到有路径方向的简单线段；
// 当前地块说明文本渲染在与地图 div 平级的独立信息 div（#play-info），内部滚动。
// 平级信息 div（#play-info）：当前地块说明文本、内部滚动。

import { $, DIR_LABELS } from "./shared.js";

const SVG = "http://www.w3.org/2000/svg";
const ORDER = ["up", "right", "down", "left"];

// 棋盘自适应：正方形尺寸 = 地图区域的宽与「高减去兄弟元素（hint）」的较小值
function fitBoard(board) {
  if (!board) {
    return;
  }
  const map = $("#play-map");
  if (!map) {
    return;
  }
  let used = 0;
  for (const child of map.children) {
    if (child !== board) {
      used += child.offsetHeight;
    }
  }
  const size = Math.max(
    80,
    Math.min(map.clientWidth, map.clientHeight - used - 14)
  );
  board.style.width = `${size}px`;
  board.style.height = `${size}px`;
}

const resizeObserver =
  typeof ResizeObserver !== "undefined"
    ? new ResizeObserver(() => fitBoard(document.querySelector("#play-map .play-board")))
    : null;

// 槽位中心在 3×3 网格中的百分比坐标（与 CSS grid 对齐）
const SIDE = 100 / 6; // 1/6：第 1 列/行的中心
const DIR_POS = {
  up: [50, SIDE],
  right: [100 - SIDE, 50],
  down: [50, 100 - SIDE],
  left: [SIDE, 50],
};

export function renderPlay(world, moveTo) {
  const mapEl = $("#play-map");
  const infoEl = $("#play-info");
  const errorEl = $("#play-error");
  mapEl.textContent = "";
  infoEl.textContent = "";
  infoEl.hidden = true;
  errorEl.hidden = true;

  if (!world || !world.player || !world.player.scene) {
    errorEl.textContent = "玩家尚未就绪，请点击「重新注册」。";
    errorEl.hidden = false;
    return;
  }

  const scene = world.player.scene;
  const loc = scene.location;
  const paths = Array.isArray(scene.paths) ? scene.paths : [];

  // 按方向分组（保持 scene 内给出的顺序 = 槽内索引）
  const byDir = { up: [], right: [], down: [], left: [] };
  for (const p of paths) {
    if (Object.prototype.hasOwnProperty.call(byDir, p.direction)) {
      byDir[p.direction].push(p);
    }
  }

  // 十字棋盘（正方形，尺寸按地图区域动态计算），内放中心小块、连线层与方向槽位格
  const board = document.createElement("div");
  board.className = "play-board";
  mapEl.appendChild(board);
  fitBoard(board);
  if (resizeObserver) {
    resizeObserver.observe(mapEl);
  }

  // 中心小块：只含地块名称，无说明文本
  const center = document.createElement("div");
  center.className = "play-center";
  center.textContent = loc.name;
  board.appendChild(center);

  // 连线层：中心到每个有路径的方向槽位一条无箭头线段
  const lines = document.createElementNS(SVG, "svg");
  lines.setAttribute("viewBox", "0 0 100 100");
  lines.setAttribute("preserveAspectRatio", "none");
  lines.classList.add("play-lines");
  for (const dir of ORDER) {
    if (byDir[dir].length > 0) {
      const line = document.createElementNS(SVG, "line");
      const [x2, y2] = DIR_POS[dir];
      line.setAttribute("x1", 50);
      line.setAttribute("y1", 50);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
      lines.appendChild(line);
    }
  }
  board.appendChild(lines);

  // 方向槽位格：该方向的全部平行路径逐一列出（每条 = 一个可选按钮）
  for (const dir of ORDER) {
    const dirPaths = byDir[dir];
    if (dirPaths.length === 0) {
      continue;
    }
    const cell = document.createElement("div");
    cell.className = `play-cell play-slot play-${dir}`;
    const head = document.createElement("div");
    head.className = "play-dir-label";
    head.textContent = DIR_LABELS[dir];
    cell.appendChild(head);
    for (const p of dirPaths) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "play-path-btn";
      const label = document.createElement("span");
      label.className = "play-path-label";
      label.textContent = p.label || "（无标签）";
      const target = document.createElement("span");
      target.className = "play-path-target";
      if (p.target_name) {
        target.textContent = p.target_name;
      } else {
        target.textContent = "???";
        target.classList.add("play-unknown");
      }
      btn.appendChild(label);
      btn.appendChild(target);
      btn.addEventListener("click", () => void moveTo(p.direction, p.path));
      cell.appendChild(btn);
    }
    board.appendChild(cell);
  }

  // 平级信息 div：当前地块说明文本（时段已由引擎解析）
  if (scene.description) {
    infoEl.textContent = scene.description;
    infoEl.hidden = false;
  }

  if (paths.length === 0) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "这里没有任何路径，你似乎被困住了。";
    mapEl.appendChild(hint);
    fitBoard(board);
  }
}
