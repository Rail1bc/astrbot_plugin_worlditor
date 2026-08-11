// edit-forms.js — 右侧详情栏内容构建（查看模式只读 / 编辑模式表单）
// 编辑模式：点击空地块 → 新建地块；点击地块 → 详情栏含「创建连接」（方向 + 目的地
// 可自选，目的地非相邻时该出口为特殊连接，以虚线强调条显示在出发方向连接块内）；
// 点击有连接的连接块 → 查看并编辑块内出口。创建连接只从地块发起，空连接块不可点击。
// 上帝视角：地块永远真名；出口只有「隐藏目的地」开关与方向槽位，无 ??? 概念。

import {
  apiPost,
  confirmModal,
  DIR_LABELS,
  DIR_OFFSETS,
  DIRECTIONS,
  openModal,
  state,
} from "./shared.js";

// ---------- 基础元素 ----------
function hintEl(text) {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = text;
  return p;
}

function sectionTitle(text) {
  const el = document.createElement("div");
  el.className = "detail-sub";
  el.textContent = text;
  return el;
}

function kv(label, value) {
  const row = document.createElement("div");
  row.className = "kv";
  const k = document.createElement("span");
  k.className = "kv-key";
  k.textContent = label;
  const v = document.createElement("span");
  v.className = "kv-value";
  v.textContent = value;
  row.appendChild(k);
  row.appendChild(v);
  return row;
}

function field(labelText, input) {
  const wrap = document.createElement("div");
  wrap.className = "form-field";
  const label = document.createElement("span");
  label.className = "form-label";
  label.textContent = labelText;
  wrap.appendChild(label);
  wrap.appendChild(input);
  return wrap;
}

function textInput(value = "", placeholder = "") {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "form-input";
  input.value = value;
  input.placeholder = placeholder;
  return input;
}

function textareaInput(value = "") {
  const input = document.createElement("textarea");
  input.className = "form-input";
  input.rows = 3;
  input.value = value;
  return input;
}

function numberInput(value = "", step = "any") {
  const input = document.createElement("input");
  input.type = "number";
  input.step = step;
  input.className = "form-input";
  input.value = value;
  return input;
}

function selectInput(options, selected = "") {
  const input = document.createElement("select");
  input.className = "form-input";
  for (const [value, text] of options) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    input.appendChild(opt);
  }
  input.value = selected;
  return input;
}

function checkboxInput(checked = false) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = "form-check";
  input.checked = checked;
  return input;
}

function formMsg(container) {
  const msg = document.createElement("div");
  msg.className = "form-msg";
  msg.hidden = true;
  container.appendChild(msg);
  return {
    show(text) {
      msg.textContent = text;
      msg.hidden = false;
    },
  };
}

function locName(byId, id) {
  const l = byId.get(id);
  return l ? l.name : id;
}

function layoutText(loc) {
  const layout = loc.layout || {};
  if (Number.isFinite(layout.x) && Number.isFinite(layout.y)) {
    return `列 ${layout.x}，行 ${layout.y}`;
  }
  return "（未设置）";
}

function uniqueExitId(base) {
  const exits = state.world?.exits || [];
  if (!exits.some((e) => e.id === base)) {
    return base;
  }
  let i = 2;
  while (exits.some((e) => e.id === `${base}_${i}`)) {
    i++;
  }
  return `${base}_${i}`;
}

// 从 fromId 出发，同 from 出边尚未占用的首个方向
function firstFreeDirection(fromId) {
  const used = new Set();
  for (const e of state.world?.exits || []) {
    if (e.from_id === fromId) {
      used.add(e.direction);
    }
  }
  for (const d of DIRECTIONS) {
    if (!used.has(d)) {
      return d;
    }
  }
  return "up";
}

// ---------- 出口行（可点击跳转） ----------
function exitItemEl(e, byId, bus) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "exit-item";
  const text = document.createElement("span");
  text.className = "exit-item-text";
  text.textContent = `${locName(byId, e.from_id)} → ${locName(byId, e.to_id)} · ${e.label}`;
  const dir = document.createElement("span");
  dir.className = "exit-item-dir";
  dir.textContent = DIR_LABELS[e.direction] || e.direction;
  btn.appendChild(text);
  btn.appendChild(dir);
  btn.addEventListener("click", () => bus.onSelectExit(e.id));
  return btn;
}

function exitListEl(exits, byId, bus) {
  const list = document.createElement("div");
  list.className = "detail-stack";
  if (exits.length === 0) {
    list.appendChild(hintEl("这里没有任何出口。"));
  }
  for (const e of exits) {
    list.appendChild(exitItemEl(e, byId, bus));
  }
  return list;
}

// ---------- 地块（查看 / 编辑） ----------
export function locationViewEl(loc, exitsFrom, byId, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("名称", loc.name));
  box.appendChild(kv("id", loc.id));
  box.appendChild(kv("位置", layoutText(loc)));
  if (loc.description) {
    box.appendChild(kv("描述", loc.description));
  }
  box.appendChild(sectionTitle("出口"));
  box.appendChild(exitListEl(exitsFrom, byId, bus));
  return box;
}

export function locationEditEl(loc, exitsFrom, byId, bus, pos) {
  const box = document.createElement("div");
  box.className = "detail-stack";

  // ① 创建连接（点击地块的主操作）：方向 + 目的地可自选
  box.appendChild(sectionTitle("创建连接"));
  box.appendChild(exitCreateEl(loc.id, byId, bus, null, pos));

  // ② 地块信息
  box.appendChild(sectionTitle("地块信息"));
  const form = document.createElement("div");
  form.className = "form";
  const nameInput = textInput(loc.name, "地块名称");
  const descInput = textareaInput(loc.description);
  const layout = loc.layout || {};
  const colInput = numberInput(Number.isFinite(layout.x) ? layout.x : "", 1);
  const rowInput = numberInput(Number.isFinite(layout.y) ? layout.y : "", 1);
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", descInput));
  form.appendChild(field("列（col）", colInput));
  form.appendChild(field("行（row）", rowInput));
  const msg = formMsg(form);
  const actions = document.createElement("div");
  actions.className = "form-actions";
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn";
  delBtn.textContent = "删除地块";
  delBtn.addEventListener("click", () => {
    confirmModal(
      `删除地块「${loc.name}」？`,
      "将级联删除所有以它为起点或终点的出口。",
      async () => {
        try {
          await apiPost("world/location/delete", { id: loc.id });
          state.selection = null;
          bus.onSubmit();
        } catch (error) {
          openErrorModal("删除失败", error);
        }
      },
      "删除"
    );
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "保存";
  saveBtn.addEventListener("click", () =>
    void submitLocationUpdate(loc, { nameInput, descInput, colInput, rowInput, msg, bus })
  );
  actions.appendChild(delBtn);
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  box.appendChild(form);

  // ③ 出口列表
  box.appendChild(sectionTitle("出口"));
  box.appendChild(exitListEl(exitsFrom, byId, bus));
  return box;
}

async function submitLocationUpdate(loc, f) {
  const { nameInput, descInput, colInput, rowInput, msg, bus } = f;
  const name = nameInput.value.trim();
  if (!name) {
    msg.show("地块名称不能为空");
    return;
  }
  const col = colInput.value.trim();
  const row = rowInput.value.trim();
  const body = { id: loc.id, name, description: descInput.value };
  if ((col === "") !== (row === "")) {
    msg.show("列与行必须同时提供，或同时留空");
    return;
  }
  if (col !== "") {
    body.layout = { x: Number(col), y: Number(row) };
  } else {
    body.layout = null; // 显式清空坐标
  }
  try {
    await apiPost("world/location/update", body);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 空地块（新建地块） ----------
export function slotCreateEl(locations, col, row, byId, bus) {
  const form = document.createElement("div");
  form.className = "form";
  form.appendChild(kv("位置", `列 ${col}，行 ${row}（已锁定）`));
  const idInput = textInput(`loc_${col}_${row}`, "唯一标识，如 town_plaza");
  const nameInput = textInput("", "地块名称");
  const descInput = textareaInput("");
  form.appendChild(field("id", idInput));
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", descInput));
  const msg = formMsg(form);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-primary";
  btn.textContent = "创建地块";
  btn.addEventListener("click", () =>
    void submitSlotCreate({ idInput, nameInput, descInput, col, row, msg, bus })
  );
  form.appendChild(btn);
  return form;
}

async function submitSlotCreate(f) {
  const { idInput, nameInput, descInput, col, row, msg, bus } = f;
  const id = idInput.value.trim();
  const name = nameInput.value.trim();
  if (!id) {
    msg.show("id 不能为空");
    return;
  }
  if (!name) {
    msg.show("地块名称不能为空");
    return;
  }
  try {
    await apiPost("world/location/create", {
      id,
      name,
      description: descInput.value,
      layout: { x: col, y: row },
    });
    bus.onCreatedLocation(id);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 出口（查看 / 编辑） ----------
export function exitViewEl(exit, byId, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("方向", DIR_LABELS[exit.direction] || exit.direction));
  box.appendChild(kv("出发", locName(byId, exit.from_id)));
  box.appendChild(kv("目标", locName(byId, exit.to_id)));
  box.appendChild(kv("出口标签", exit.label));
  box.appendChild(kv("隐藏目的地", exit.reveal_target === false ? "是" : "否"));
  if (exit.reveal_target === false) {
    box.appendChild(hintEl("该出口隐藏目的地，玩家视图显示「???」。"));
  }
  return box;
}

export function exitEditEl(exit, byId, bus) {
  const form = document.createElement("div");
  form.className = "form";
  form.appendChild(kv("id", exit.id));
  form.appendChild(kv("出发", locName(byId, exit.from_id)));
  const toInput = selectInput(
    [...byId.values()].map((l) => [l.id, l.name]),
    exit.to_id
  );
  const labelInput = textInput(exit.label, "出口标签");
  const dirInput = selectInput(DIRECTIONS.map((d) => [d, DIR_LABELS[d]]), exit.direction);
  const revealInput = checkboxInput(exit.reveal_target === false);
  form.appendChild(field("目标地块", toInput));
  form.appendChild(field("出口标签", labelInput));
  form.appendChild(field("方向", dirInput));
  form.appendChild(field("隐藏目的地", revealInput));
  const msg = formMsg(form);
  const actions = document.createElement("div");
  actions.className = "form-actions";
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn";
  delBtn.textContent = "删除出口";
  delBtn.addEventListener("click", () => {
    confirmModal(
      `删除出口「${exit.label}」？`,
      "删除后该出口将无法再通行。",
      async () => {
        try {
          await apiPost("world/exit/delete", { id: exit.id });
          state.selection = null;
          bus.onSubmit();
        } catch (error) {
          openErrorModal("删除失败", error);
        }
      },
      "删除"
    );
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "保存";
  saveBtn.addEventListener("click", () =>
    void submitExitUpdate(exit, { toInput, labelInput, dirInput, revealInput, msg, bus })
  );
  actions.appendChild(delBtn);
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  return form;
}

async function submitExitUpdate(exit, f) {
  const { toInput, labelInput, dirInput, revealInput, msg, bus } = f;
  const label = labelInput.value.trim();
  if (!label) {
    msg.show("出口标签不能为空");
    return;
  }
  const body = {
    id: exit.id,
    to_id: toInput.value,
    label,
    direction: dirInput.value,
    reveal_target: !revealInput.checked,
  };
  try {
    await apiPost("world/exit/update", body);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 创建连接 ----------
// fromId 固定；fixedDir 为 null 时方向可四选（默认首个空闲方向），否则锁定。
// 目的地可自选：与 from 相邻 → 普通连接；否则特殊连接（虚线显示在出发方向连接块内）。
export function exitCreateEl(fromId, byId, bus, fixedDir, pos) {
  const form = document.createElement("div");
  form.className = "form";
  form.appendChild(kv("出发", locName(byId, fromId)));

  const dirInput = selectInput(
    DIRECTIONS.map((d) => [d, DIR_LABELS[d]]),
    fixedDir || firstFreeDirection(fromId)
  );
  if (fixedDir) {
    dirInput.disabled = true;
  }
  const toInput = selectInput([...byId.values()].map((l) => [l.id, l.name]), "");
  const labelInput = textInput("", "出口标签，如「沿着东街走向咖啡店」");
  const revealInput = checkboxInput(false);
  const msg = formMsg(form);

  // 方向指向相邻地块时自动选中该目标（可再手动改成任意目标 → 特殊连接）
  const pickAdjacent = () => {
    const fromPos = pos.get(fromId);
    if (!fromPos) {
      return;
    }
    const [dc, dr] = DIR_OFFSETS[dirInput.value] || DIR_OFFSETS.up;
    const tc = fromPos[0] + dc;
    const tr = fromPos[1] + dr;
    for (const [id, cell] of pos) {
      if (id !== fromId && cell[0] === tc && cell[1] === tr) {
        toInput.value = id;
        return;
      }
    }
  };
  if (!fixedDir) {
    dirInput.addEventListener("change", pickAdjacent);
  }
  pickAdjacent();

  form.appendChild(field("方向", dirInput));
  form.appendChild(field("目标地块（可自选）", toInput));
  form.appendChild(field("出口标签", labelInput));
  form.appendChild(field("隐藏目的地", revealInput));
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-primary";
  btn.textContent = "创建连接";
  btn.addEventListener("click", () =>
    void submitExitCreate(fromId, { dirInput, toInput, labelInput, revealInput, msg, bus })
  );
  form.appendChild(btn);
  return form;
}

async function submitExitCreate(fromId, f) {
  const { dirInput, toInput, labelInput, revealInput, msg, bus } = f;
  const label = labelInput.value.trim();
  if (!label) {
    msg.show("出口标签不能为空");
    return;
  }
  const toId = toInput.value;
  if (!toId) {
    msg.show("请选择目标地块");
    return;
  }
  const direction = dirInput.value;
  const body = {
    id: uniqueExitId(`${fromId}_${direction}_${toId}`),
    from_id: fromId,
    to_id: toId,
    label,
    reveal_target: !revealInput.checked,
    direction,
  };
  try {
    await apiPost("world/exit/create", body);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 连接块（查看 / 编辑） ----------
function describeBlock(info, byId) {
  const names = [];
  if (info.kind === "h") {
    if (info.left) names.push(locName(byId, info.left.id));
    if (info.right) names.push(locName(byId, info.right.id));
    return `${names.join(" 与 ")} 之间（横向）`;
  }
  if (info.top) names.push(locName(byId, info.top.id));
  if (info.bottom) names.push(locName(byId, info.bottom.id));
  return `${names.join(" 与 ")} 之间（纵向）`;
}

export function blockViewEl(info, byId, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("方位", describeBlock(info, byId)));
  if (info.exits.length === 0) {
    box.appendChild(hintEl("该方向没有任何连接。"));
  }
  for (const e of info.exits) {
    box.appendChild(exitItemEl(e, byId, bus));
  }
  return box;
}

export function blockEditEl(info, byId, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("方位", describeBlock(info, byId)));
  box.appendChild(sectionTitle("出口"));
  if (info.exits.length === 0) {
    box.appendChild(hintEl("该方向没有任何连接。"));
  }
  for (const e of info.exits) {
    box.appendChild(exitItemEl(e, byId, bus));
  }
  return box;
}

function openErrorModal(title, error) {
  // 轻量错误弹窗（避免与 confirmModal 相互覆盖的复杂度）
  const el = document.createElement("div");
  el.className = "detail-stack";
  el.appendChild(hintEl(error?.message || String(error)));
  openModal(title, el);
}
