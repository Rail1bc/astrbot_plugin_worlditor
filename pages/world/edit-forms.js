// edit-forms.js — 地块 / 出口的创建与编辑表单（自绘 modal 内）
// 上帝视角：地块永远真名；出口只有「隐藏目的地」开关与方向槽位，无 ??? 概念。

import {
  apiPost,
  cellKey,
  computePositions,
  confirmModal,
  DIR_OFFSETS,
  firstFreeCell,
  hideModal,
  OPPOSITE_DIR,
  openModal,
  state,
} from "./shared.js";

const DIRECTIONS = [
  ["up", "上"],
  ["right", "右"],
  ["down", "下"],
  ["left", "左"],
];

// ---------- 出口方向自动推荐 ----------
// 新建出口的方向语义（编辑表格的相邻约束）：
// ① 反向边存在（to→from）→ 推荐其反方向（保证 A右是B ⇔ B左是A）
// ② 否则目标主位与出发地块相邻 → 按相对位置推导方向
// ③ 否则 → 出发地块首个空闲方向（非相邻连接则表格生成分身）
// ①②结果与同 from 其它出边方向冲突时回退 ③，避免槽位重叠。

function usedDirections(fromId, exceptId = null) {
  const used = new Set();
  for (const e of state.world?.exits || []) {
    if (e.from_id === fromId && e.id !== exceptId) {
      used.add(e.direction);
    }
  }
  return used;
}

function pickFreeDirection(fromId, exceptId = null) {
  const used = usedDirections(fromId, exceptId);
  for (const [dir] of DIRECTIONS) {
    if (!used.has(dir)) {
      return dir;
    }
  }
  return "up";
}

function recommendedDirection(fromId, toId, exceptId = null) {
  const world = state.world;
  const exits = (world && world.exits) || [];
  const reverse = exits.find((e) => e.from_id === toId && e.to_id === fromId);
  if (reverse) {
    const dir = OPPOSITE_DIR[reverse.direction] || "up";
    return usedDirections(fromId, exceptId).has(dir)
      ? pickFreeDirection(fromId, exceptId)
      : dir;
  }
  const pos = computePositions((world && world.locations) || []);
  const from = pos.get(fromId);
  const target = pos.get(toId);
  if (from && target) {
    for (const [dir, [dc, dr]] of Object.entries(DIR_OFFSETS)) {
      if (target[0] === from[0] + dc && target[1] === from[1] + dr) {
        return usedDirections(fromId, exceptId).has(dir)
          ? pickFreeDirection(fromId, exceptId)
          : dir;
      }
    }
  }
  return pickFreeDirection(fromId, exceptId);
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

function formMsg(form) {
  const msg = document.createElement("div");
  msg.className = "form-msg";
  msg.hidden = true;
  form.appendChild(msg);
  return {
    show(text) {
      msg.textContent = text;
      msg.hidden = false;
    },
  };
}

function currentLayoutValue(loc) {
  if (loc && loc.layout && typeof loc.layout.x === "number") {
    return [String(loc.layout.x), String(loc.layout.y)];
  }
  return ["", ""];
}

// 新建地块默认落位：优先最近点选地块（lastNodeId）的邻格，否则环形扫描空闲格
function defaultCellForNewLocation() {
  const locations = (state.world && state.world.locations) || [];
  const pos = computePositions(locations);
  const occupied = new Set([...pos.values()].map(([c, r]) => cellKey(c, r)));
  const prefers = [];
  const [lc, lr] = pos.get(state.lastNodeId) || [];
  if (Number.isFinite(lc)) {
    for (const [dc, dr] of Object.values(DIR_OFFSETS)) {
      prefers.push([lc + dc, lr + dr]);
    }
  }
  const [c, r] = firstFreeCell(occupied, prefers);
  return [String(c), String(r)];
}

// ---------- 地块表单 ----------
export function locationForm(loc, onSubmit) {
  const form = document.createElement("form");
  form.className = "form";

  const idInput = textInput(loc ? loc.id : "", "唯一标识，如 town_plaza");
  if (loc) {
    idInput.disabled = true;
  }
  const nameInput = textInput(loc ? loc.name : "", "地块名称");
  const descInput = textareaInput(loc ? loc.description : "");
  // 新建：默认落在空闲格（优先最近点选地块的邻格）；编辑：取当前 layout
  const [curCol, curRow] = loc
    ? currentLayoutValue(loc)
    : defaultCellForNewLocation();
  const colInput = numberInput(curCol, 1);
  const rowInput = numberInput(curRow, 1);

  form.appendChild(field("id", idInput));
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", descInput));
  form.appendChild(field("列（col，可选）", colInput));
  form.appendChild(field("行（row，可选）", rowInput));
  const msg = formMsg(form);

  const actions = [];
  if (loc) {
    actions.push({
      label: "删除",
      onClick: () => {
        confirmModal(
          `删除地块「${loc.name}」？`,
          "将级联删除所有以它为起点或终点的出口。",
          async () => {
            try {
              await apiPost("world/location/delete", { id: loc.id });
              hideModal();
              onSubmit();
            } catch (error) {
              // 确认弹窗已替换 modal 内容，失败改弹错误提示
              openModal("删除失败", error?.message || String(error));
            }
          },
          "删除"
        );
      },
    });
  }
  actions.push({
    label: loc ? "保存" : "创建",
    primary: true,
    onClick: () =>
      void submitLocation(loc, {
        idInput,
        nameInput,
        descInput,
        colInput,
        rowInput,
        msg,
        onSubmit,
      }),
  });

  return { el: form, actions };
}

async function submitLocation(loc, f) {
  const { idInput, nameInput, descInput, colInput, rowInput, msg, onSubmit } = f;
  const name = nameInput.value.trim();
  if (!name) {
    msg.show("地块名称不能为空");
    return;
  }
  const col = colInput.value.trim();
  const row = rowInput.value.trim();
  const body = loc
    ? { id: loc.id, name, description: descInput.value }
    : { id: idInput.value.trim(), name, description: descInput.value };
  if (!loc && !body.id) {
    msg.show("id 不能为空");
    return;
  }
  if ((col === "") !== (row === "")) {
    msg.show("列与行必须同时提供，或同时留空");
    return;
  }
  if (col !== "") {
    body.layout = { x: Number(col), y: Number(row) };
  } else if (loc) {
    body.layout = null; // 显式清空坐标
  }
  try {
    await apiPost(loc ? "world/location/update" : "world/location/create", body);
    hideModal();
    onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 出口表单 ----------
export function exitForm(exit, onSubmit) {
  const form = document.createElement("form");
  form.className = "form";

  const locations = (state.world && state.world.locations) || [];
  const options = locations.map((l) => [l.id, `${l.name}（${l.id}）`]);

  const idInput = textInput(exit ? exit.id : "", "唯一标识，如 town_plaza_cafe");
  if (exit) {
    idInput.disabled = true;
  }
  const defaultFrom = exit
    ? exit.from_id
    : state.lastNodeId && locations.some((l) => l.id === state.lastNodeId)
      ? state.lastNodeId
      : locations[0] ? locations[0].id : "";
  const fromInput = selectInput(options, defaultFrom);
  if (exit) {
    fromInput.disabled = true;
  }
  const toInput = selectInput(options, exit ? exit.to_id : "");
  const labelInput = textInput(exit ? exit.label : "", "出口标签，如「沿着东街走向咖啡店」");

  const revealCheck = document.createElement("input");
  revealCheck.type = "checkbox";
  revealCheck.className = "form-check";
  revealCheck.checked = exit ? exit.reveal_target === false : false;

  const directionWrap = document.createElement("div");
  directionWrap.className = "form-radio-group";
  const radios = [];
  for (const [value, text] of DIRECTIONS) {
    const label = document.createElement("label");
    label.className = "form-radio";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "direction";
    radio.value = value;
    if (exit) {
      radio.checked = exit.direction === value;
    }
    label.appendChild(radio);
    label.appendChild(document.createTextNode(text));
    directionWrap.appendChild(label);
    radios.push(radio);
  }
  const applyRecommendedDirection = () => {
    const dir = recommendedDirection(fromInput.value, toInput.value, exit?.id ?? null);
    const radio = radios.find((r) => r.value === dir);
    if (radio) {
      radio.checked = true;
    }
  };
  if (!exit) {
    // 新建：初始按反向边/相邻关系自动推荐；切换 from/to 时重新推荐
    applyRecommendedDirection();
    fromInput.addEventListener("change", applyRecommendedDirection);
    toInput.addEventListener("change", applyRecommendedDirection);
  } else {
    // 编辑：保留原方向；仅当变更目标导致方向与同 from 其它出边冲突时重新推荐
    toInput.addEventListener("change", () => {
      const checked = radios.find((r) => r.checked);
      if (checked && usedDirections(exit.from_id, exit.id).has(checked.value)) {
        applyRecommendedDirection();
      }
    });
  }

  form.appendChild(field("id", idInput));
  form.appendChild(field("出发地块", fromInput));
  form.appendChild(field("目标地块", toInput));
  form.appendChild(field("出口标签", labelInput));
  form.appendChild(field("隐藏目的地", revealCheck));
  form.appendChild(field("方向", directionWrap));
  const msg = formMsg(form);

  const actions = [];
  if (exit) {
    actions.push({
      label: "删除",
      onClick: () => {
        confirmModal(
          `删除出口「${exit.label}」？`,
          "删除后该出口将无法再通行。",
          async () => {
            try {
              await apiPost("world/exit/delete", { id: exit.id });
              hideModal();
              onSubmit();
            } catch (error) {
              // 确认弹窗已替换 modal 内容，失败改弹错误提示
              openModal("删除失败", error?.message || String(error));
            }
          },
          "删除"
        );
      },
    });
  }
  actions.push({
    label: exit ? "保存" : "创建",
    primary: true,
    onClick: () =>
      void submitExit(exit, { idInput, fromInput, toInput, labelInput, revealCheck, directionWrap, msg, onSubmit }),
  });

  return { el: form, actions };
}

async function submitExit(exit, f) {
  const { idInput, fromInput, toInput, labelInput, revealCheck, directionWrap, msg, onSubmit } = f;
  const label = labelInput.value.trim();
  if (!label) {
    msg.show("出口标签不能为空");
    return;
  }
  const direction = directionWrap.querySelector('input[name="direction"]:checked');
  const body = exit
    ? {
        id: exit.id,
        to_id: toInput.value,
        label,
        reveal_target: !revealCheck.checked,
        direction: direction ? direction.value : "up",
      }
    : {
        id: idInput.value.trim(),
        from_id: fromInput.value,
        to_id: toInput.value,
        label,
        reveal_target: !revealCheck.checked,
        direction: direction ? direction.value : "up",
      };
  if (!body.id) {
    msg.show("id 不能为空");
    return;
  }
  try {
    await apiPost(exit ? "world/exit/update" : "world/exit/create", body);
    hideModal();
    onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

