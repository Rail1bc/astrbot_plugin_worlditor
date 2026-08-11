// edit-forms.js — 地块 / 出口的创建与编辑表单（自绘 modal 内）
// 上帝视角：地块永远真名；出口只有「隐藏目的地」开关与方向槽位，无 ??? 概念。

import { apiPost, confirmModal, hideModal, openModal, state } from "./shared.js";

const DIRECTIONS = [
  ["up", "上"],
  ["right", "右"],
  ["down", "下"],
  ["left", "左"],
];

// 同 from 的出边方向互异（编辑器保证）：新建时默认选空闲方向，避免意外冲突
function refreshDirectionDefault(fromId, radios) {
  const exits = (state.world && state.world.exits) || [];
  const used = new Set();
  for (const e of exits) {
    if (e.from_id === fromId) {
      used.add(e.direction);
    }
  }
  const checked = radios.find((r) => r.checked);
  if (checked && !used.has(checked.value)) {
    return; // 当前选中方向未被占用，保持
  }
  const free = radios.find((r) => !used.has(r.value));
  if (free) {
    free.checked = true;
  }
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

function numberInput(value = "") {
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
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
  const [curX, curY] = currentLayoutValue(loc);
  const xInput = numberInput(curX);
  const yInput = numberInput(curY);

  form.appendChild(field("id", idInput));
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", descInput));
  form.appendChild(field("布局 x（可选）", xInput));
  form.appendChild(field("布局 y（可选）", yInput));
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
      void submitLocation(loc, { idInput, nameInput, descInput, xInput, yInput, msg, onSubmit }),
  });

  return { el: form, actions };
}

async function submitLocation(loc, f) {
  const { idInput, nameInput, descInput, xInput, yInput, msg, onSubmit } = f;
  const name = nameInput.value.trim();
  if (!name) {
    msg.show("地块名称不能为空");
    return;
  }
  const x = xInput.value.trim();
  const y = yInput.value.trim();
  const body = loc
    ? { id: loc.id, name, description: descInput.value }
    : { id: idInput.value.trim(), name, description: descInput.value };
  if (!loc && !body.id) {
    msg.show("id 不能为空");
    return;
  }
  if ((x === "") !== (y === "")) {
    msg.show("布局 x 与 y 必须同时提供，或同时留空");
    return;
  }
  if (x !== "") {
    body.layout = { x: Number(x), y: Number(y) };
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
  if (!exit) {
    // 新建：默认选出发地块第一个空闲方向；切换出发地块冲突时自动改选
    refreshDirectionDefault(fromInput.value, radios);
    fromInput.addEventListener("change", () =>
      refreshDirectionDefault(fromInput.value, radios)
    );
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

