// play-view.js — 玩家模式：当前地块 + 有出边连接的 1 跳目标
// 十字布局（上/右/下/左）；无连接的槽位不可见；所有边一视同仁（无箭头简单连线，
// 不查反向边、不画方向）；隐藏目标显示 ???；无回环（自环出口照常占槽位）；
// 违规地图（出度>4 或同方向冲突）折叠「+N」展开全部出口列表。

import { $ } from "./shared.js";

const SVG = "http://www.w3.org/2000/svg";
const ORDER = ["up", "right", "down", "left"];

// 槽位中心在 3×3 网格中的百分比坐标（与 CSS grid 对齐）
const SIDE = 100 / 6; // 1/6：第 1 列/行的中心
const DIR_POS = {
  up: [50, SIDE],
  right: [100 - SIDE, 50],
  down: [50, 100 - SIDE],
  left: [SIDE, 50],
};

export function renderPlay(world, playerId, moveTo) {
  const container = $("#play-grid");
  const errorEl = $("#play-error");
  container.textContent = "";
  errorEl.hidden = true;

  if (!world || !world.player || !world.player.scene) {
    errorEl.textContent = "玩家尚未就绪，请点击「重新注册」。";
    errorEl.hidden = false;
    return;
  }

  const scene = world.player.scene;
  const loc = scene.location;
  const exits = Array.isArray(scene.exits) ? scene.exits : [];

  // 槽位分配：正常地图每方向至多一条；多余（出度>4 或同方向冲突）走「+N」折叠
  const slots = { up: null, right: null, down: null, left: null };
  const extra = [];
  for (const e of exits) {
    if (
      Object.prototype.hasOwnProperty.call(slots, e.direction) &&
      slots[e.direction] === null
    ) {
      slots[e.direction] = e;
    } else {
      extra.push(e);
    }
  }

  // 十字棋盘（正方形），内放中心格、连线层与槽位格
  const board = document.createElement("div");
  board.className = "play-board";
  container.appendChild(board);

  const center = document.createElement("div");
  center.className = "play-cell play-center";
  const cName = document.createElement("div");
  cName.className = "play-loc-name";
  cName.textContent = loc.name;
  const cDesc = document.createElement("div");
  cDesc.className = "play-loc-desc";
  cDesc.textContent = loc.description;
  const cId = document.createElement("div");
  cId.className = "play-pid";
  cId.textContent = `玩家 ${playerId}`;
  center.appendChild(cName);
  center.appendChild(cDesc);
  center.appendChild(cId);
  board.appendChild(center);

  // 连线层：中心到每个占位槽位一条无箭头线段
  const lines = document.createElementNS(SVG, "svg");
  lines.setAttribute("viewBox", "0 0 100 100");
  lines.setAttribute("preserveAspectRatio", "none");
  lines.classList.add("play-lines");
  for (const dir of ORDER) {
    if (slots[dir]) {
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

  // 槽位格：无连接的槽位不渲染任何元素
  for (const dir of ORDER) {
    const exit = slots[dir];
    if (!exit) {
      continue;
    }
    const cell = document.createElement("div");
    cell.className = `play-cell play-slot play-${dir}`;
    const tName = document.createElement("div");
    tName.className = "play-target-name";
    if (exit.target_name) {
      tName.textContent = exit.target_name;
    } else {
      tName.textContent = "???";
      tName.classList.add("play-unknown");
    }
    const tLabel = document.createElement("div");
    tLabel.className = "play-target-label";
    tLabel.textContent = exit.label;
    cell.appendChild(tName);
    cell.appendChild(tLabel);
    cell.addEventListener("click", () => void moveTo(exit.exit_id));
    board.appendChild(cell);
  }

  if (exits.length === 0) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "这里没有任何出口，你似乎被困住了。";
    container.appendChild(hint);
  }

  // 违规地图折叠兜底：展开全部出口列表（保留 exit_id），可收回
  if (extra.length > 0) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn play-more";
    btn.textContent = `＋${extra.length} 条出边`;
    btn.addEventListener("click", () => {
      const list = document.createElement("div");
      list.className = "play-extra-list";
      for (const e of extra) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "exit-btn";
        const label = document.createElement("span");
        label.className = "exit-label";
        label.textContent = e.label;
        const target = document.createElement("span");
        target.className = "exit-target";
        if (e.target_name) {
          target.textContent = e.target_name;
        } else {
          target.textContent = "???";
          target.classList.add("exit-target-hidden");
        }
        item.appendChild(label);
        item.appendChild(target);
        item.addEventListener("click", () => void moveTo(e.exit_id));
        list.appendChild(item);
      }
      const close = document.createElement("button");
      close.type = "button";
      close.className = "btn";
      close.textContent = "收起";
      close.addEventListener("click", () => {
        list.remove();
        btn.hidden = false;
      });
      list.appendChild(close);
      btn.hidden = true;
      container.appendChild(list);
    });
    container.appendChild(btn);
  }
}
