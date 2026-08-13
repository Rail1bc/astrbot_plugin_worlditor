// edit-forms.js — 右侧详情栏内容构建（查看模式只读 / 编辑模式表单）
// v3 模型：地块身份 = (row, col)；连接内嵌 4 方向槽位（每槽多条平行路径，每条路径
// targets 有序：首个 = 主目标，其余 = 意外路径加权随机）；文本一律 TextSchedule
// （默认纯文本框，高级模式可编辑时段 / 多条 / 权重）。上帝视角：地块永远真名，
// 但死引用路径（主目标缺失）标红提示。

import {
  apiPost,
  confirmModal,
  DIR_LABELS,
  DIRECTIONS,
  openModal,
  scheduleText,
  pathDead,
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

function openErrorModal(title, error) {
  // 轻量错误弹窗（避免与 confirmModal 相互覆盖的复杂度）
  const el = document.createElement("div");
  el.className = "detail-stack";
  el.appendChild(hintEl(error?.message || String(error)));
  openModal(title, el);
}

function promptModal(title, placeholder, initial, onOk, okLabel = "确定") {
  const wrap = document.createElement("div");
  wrap.className = "form";
  const input = textInput(initial, placeholder);
  wrap.appendChild(input);
  openModal(title, wrap, [
    { label: "取消" },
    {
      label: okLabel,
      primary: true,
      onClick: () => {
        const value = input.value.trim();
        if (value) {
          onOk(value);
        }
      },
    },
  ]);
}

// ---------- TextSchedule 编辑器 ----------
// 简单模式：单个全天时段单条文本 → 纯文本框；存在多时段 / 多条 / 权重时自动切高级。
// get()：空文本 → null；简单单条 → 纯字符串；高级 → {periods:[...]}。
const FULL_DAY = { start: "00:00", end: "24:00" };

function isSimpleSchedule(value) {
  const periods = value?.periods;
  if (!Array.isArray(periods) || periods.length !== 1) {
    return false;
  }
  const p = periods[0];
  if (!p || p.start !== "00:00" || p.end !== "24:00") {
    return false;
  }
  const items = Array.isArray(p.items) ? p.items : [];
  return items.length === 1 && items[0].weight === 1;
}

function scheduleEditor(value) {
  const box = document.createElement("div");
  box.className = "schedule-editor";
  const data = normalizeSchedule(value);
  let advanced = !isSimpleSchedule(data);

  const simpleWrap = document.createElement("div");
  const ta = textareaInput(data.periods[0]?.items[0]?.text ?? "");
  ta.rows = 2;
  simpleWrap.appendChild(ta);

  const advWrap = document.createElement("div");
  advWrap.hidden = !advanced;
  advWrap.className = "schedule-advanced";

  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "btn btn-mini";
  toggleBtn.textContent = advanced ? "简化为单条" : "高级：时段与多条";

  function rebuildAdvanced() {
    advWrap.textContent = "";
    const periods = (advanced ? data.periods : null) || [];
    const list = document.createElement("div");
    list.className = "schedule-periods";
    if (periods.length === 0) {
      list.appendChild(hintEl("暂无时段，点击「添加时段」"));
    }
    periods.forEach((p, pi) => {
      list.appendChild(periodEl(p, pi));
    });
    advWrap.appendChild(list);
    const addPeriod = document.createElement("button");
    addPeriod.type = "button";
    addPeriod.className = "btn";
    addPeriod.textContent = "添加时段";
    addPeriod.addEventListener("click", () => {
      data.periods.push({ start: "00:00", end: "24:00", items: [] });
      rebuildAdvanced();
    });
    advWrap.appendChild(addPeriod);

    function periodEl(p, pi) {
      const wrap = document.createElement("div");
      wrap.className = "schedule-period";
      const head = document.createElement("div");
      head.className = "schedule-period-head";
      const start = textInput(p.start, "起");
      start.className = "form-input form-input-sm";
      const end = textInput(p.end, "止");
      end.className = "form-input form-input-sm";
      head.appendChild(start);
      head.appendChild(document.createTextNode("–"));
      head.appendChild(end);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-mini";
      del.textContent = "删";
      del.addEventListener("click", () => {
        data.periods.splice(pi, 1);
        rebuildAdvanced();
      });
      head.appendChild(del);
      wrap.appendChild(head);

      const items = document.createElement("div");
      items.className = "schedule-items";
      (p.items || []).forEach((it, ii) => {
        items.appendChild(
          itemEl(p, pi, ii, () => {
            p.items.splice(ii, 1);
            rebuildAdvanced();
          })
        );
      });
      const addItem = document.createElement("button");
      addItem.type = "button";
      addItem.className = "btn btn-mini";
      addItem.textContent = "＋ 条目";
      addItem.addEventListener("click", () => {
        p.items.push({ text: "", weight: 1 });
        rebuildAdvanced();
      });
      wrap.appendChild(items);
      wrap.appendChild(addItem);

      // 输入同步回 data（文本 / 时间 / 权重）
      start.addEventListener("input", () => {
        p.start = start.value.trim() || "00:00";
      });
      end.addEventListener("input", () => {
        p.end = end.value.trim() || "24:00";
      });
      return wrap;

      function itemEl(p, pi, ii, onRemove) {
        const row = document.createElement("div");
        row.className = "schedule-item";
        const t = textInput(it.text, "文本");
        const w = numberInput(it.weight, 0.1);
        w.style.width = "64px";
        row.appendChild(t);
        row.appendChild(w);
        const del = document.createElement("button");
        del.type = "button";
        del.className = "btn btn-mini";
        del.textContent = "删";
        del.addEventListener("click", onRemove);
        row.appendChild(del);
        t.addEventListener("input", () => {
          it.text = t.value;
        });
        w.addEventListener("input", () => {
          const n = Number(w.value);
          it.weight = Number.isFinite(n) && n > 0 ? n : 1;
        });
        return row;
      }
    }
  }

  rebuildAdvanced();

  toggleBtn.addEventListener("click", () => {
    advanced = !advanced;
    if (advanced) {
      // 从简单文本框进入高级：保留当前文本为单时段单条
      data.periods = [
        { start: "00:00", end: "24:00", items: [{ text: ta.value, weight: 1 }] },
      ];
      rebuildAdvanced();
    }
    simpleWrap.hidden = advanced;
    advWrap.hidden = !advanced;
    toggleBtn.textContent = advanced ? "简化为单条" : "高级：时段与多条";
  });
  simpleWrap.hidden = advanced;

  box.appendChild(simpleWrap);
  box.appendChild(advWrap);
  box.appendChild(toggleBtn);

  return {
    el: box,
    get() {
      if (!advanced) {
        const text = ta.value.trim();
        return text || null; // 空 → null（清空 / 无标签）
      }
      const periods = [];
      for (const p of data.periods || []) {
        const items = (p.items || [])
          .map((it) => ({
            text: it.text,
            weight: Number.isFinite(Number(it.weight)) && Number(it.weight) > 0 ? Number(it.weight) : 1,
          }))
          .filter((it) => it.text);
        if (items.length === 0) {
          continue;
        }
        periods.push({
          start: p.start || "00:00",
          end: p.end || "24:00",
          items,
        });
      }
      return periods.length > 0 ? { periods } : null;
    },
  };
}

function normalizeSchedule(value) {
  if (!value) {
    return { periods: [{ ...FULL_DAY, items: [{ text: "", weight: 1 }] }] };
  }
  if (typeof value === "string") {
    return { periods: [{ ...FULL_DAY, items: [{ text: value, weight: 1 }] }] };
  }
  const periods = Array.isArray(value.periods)
    ? value.periods.map((p) => ({
        start: p.start || "00:00",
        end: p.end || "24:00",
        items: Array.isArray(p.items) ? p.items.map((it) => ({ text: it.text, weight: it.weight })) : [],
      }))
    : [];
  return periods.length > 0
    ? { periods }
    : { periods: [{ ...FULL_DAY, items: [{ text: "", weight: 1 }] }] };
}

// ---------- 目标列表编辑器 ----------
function targetsEditor(targets) {
  const box = document.createElement("div");
  box.className = "targets-editor";
  const list = box.appendChild(document.createElement("div"));
  const note = hintEl("首个目标为主目标（展示名），其余为意外路径（按权重随机抽取）。");
  box.appendChild(note);

  function rebuild() {
    list.textContent = "";
    targets.forEach((t, i) => {
      const row = document.createElement("div");
      row.className = "target-row";
      const r = numberInput(t.row, 1);
      r.className = "form-input form-input-sm";
      const c = numberInput(t.col, 1);
      c.className = "form-input form-input-sm";
      const w = numberInput(t.weight, 0.1);
      w.className = "form-input form-input-sm";
      w.style.width = "64px";
      r.title = "行";
      c.title = "列";
      w.title = "意外路径权重";
      r.addEventListener("input", () => {
        t.row = Number(r.value);
      });
      c.addEventListener("input", () => {
        t.col = Number(c.value);
      });
      w.addEventListener("input", () => {
        const n = Number(w.value);
        t.weight = Number.isFinite(n) && n > 0 ? n : 1;
      });
      row.appendChild(r);
      row.appendChild(c);
      row.appendChild(w);
      const up = document.createElement("button");
      up.type = "button";
      up.className = "btn btn-mini";
      up.textContent = "↑";
      up.title = "上移（提升为主目标方向）";
      up.disabled = i === 0;
      up.addEventListener("click", () => {
        [targets[i - 1], targets[i]] = [targets[i], targets[i - 1]];
        rebuild();
      });
      row.appendChild(up);
      const down = document.createElement("button");
      down.type = "button";
      down.className = "btn btn-mini";
      down.textContent = "↓";
      down.title = "下移";
      down.disabled = i === targets.length - 1;
      down.addEventListener("click", () => {
        [targets[i + 1], targets[i]] = [targets[i], targets[i + 1]];
        rebuild();
      });
      row.appendChild(down);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-mini";
      del.textContent = "删";
      del.addEventListener("click", () => {
        targets.splice(i, 1);
        rebuild();
      });
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  rebuild();

  const add = document.createElement("button");
  add.type = "button";
  add.className = "btn";
  add.textContent = "＋ 目标";
  add.addEventListener("click", () => {
    targets.push({ row: 0, col: 0, weight: 1 });
    rebuild();
  });
  box.appendChild(add);

  return {
    el: box,
    get() {
      return targets
        .map((t) => ({
          row: t.row,
          col: t.col,
          weight: Number.isFinite(Number(t.weight)) && Number(t.weight) > 0 ? Number(t.weight) : 1,
        }))
        .filter((t) => Number.isInteger(t.row) && Number.isInteger(t.col));
    },
  };
}

// ---------- 路径编辑器 ----------
function pathEditor(path) {
  const box = document.createElement("div");
  box.className = "path-editor";
  const label = scheduleEditor(path.label || null);
  box.appendChild(field("路径文本", label.el));
  const hide = checkboxInput(path.reveal_target === false);
  box.appendChild(field("隐藏目标名（玩家显示 ???）", hide));
  const targets = targetsEditor((path.targets || []).map((t) => ({ ...t })));
  box.appendChild(sectionTitle("目标"));
  box.appendChild(targets.el);
  return {
    el: box,
    get() {
      return {
        label: label.get(),
        reveal_target: !hide.checked,
        targets: targets.get(),
      };
    },
  };
}

// ---------- 地块（查看 / 编辑） ----------
function slotSummary(slot, byLoc) {
  if (!slot || !slot.enabled) {
    return "禁用";
  }
  const paths = slot.paths || [];
  const dead = paths.filter((p) => pathDead(byLoc, p)).length;
  return paths.length === 0 ? "启用 · 无路径" : `启用 · ${paths.length} 条路径${dead ? ` · ${dead} 条死引用` : ""}`;
}

export function locationViewEl(loc, byLoc, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("名称", loc.name));
  box.appendChild(kv("坐标", `(${loc.row}, ${loc.col})`));
  if (loc.description) {
    box.appendChild(kv("描述", scheduleText(loc.description)));
  }
  box.appendChild(sectionTitle("连接槽位"));
  for (const d of DIRECTIONS) {
    const slot = loc.connections?.[d];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    text.textContent = `${DIR_LABELS[d]} · ${slotSummary(slot, byLoc)}`;
    if (slot?.enabled && (slot.paths || []).some((p) => pathDead(byLoc, p))) {
      text.classList.add("text-dead");
    }
    const dir = document.createElement("span");
    dir.className = "exit-item-dir";
    dir.textContent = DIR_LABELS[d];
    btn.appendChild(text);
    btn.appendChild(dir);
    btn.addEventListener("click", () => bus.onSelectSlot(loc.row, loc.col, d));
    box.appendChild(btn);
  }
  return box;
}

export function locationEditEl(loc, byLoc, world, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";

  box.appendChild(sectionTitle("地块信息"));
  const form = document.createElement("div");
  form.className = "form";
  const nameInput = textInput(loc.name, "地块名称");
  const desc = scheduleEditor(loc.description);
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", desc.el));
  form.appendChild(kv("坐标（只读）", `(${loc.row}, ${loc.col})`));
  const msg = formMsg(form);
  const actions = document.createElement("div");
  actions.className = "form-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "保存";
  saveBtn.addEventListener("click", () =>
    void submitLocationUpdate(loc, { nameInput, desc, msg, bus })
  );
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  box.appendChild(form);

  box.appendChild(sectionTitle("移动地块"));
  const moveForm = document.createElement("div");
  moveForm.className = "form form-row";
  const toRow = numberInput("", 1);
  toRow.className = "form-input form-input-sm";
  const toCol = numberInput("", 1);
  toCol.className = "form-input form-input-sm";
  moveForm.appendChild(toRow);
  moveForm.appendChild(toCol);
  const moveBtn = document.createElement("button");
  moveBtn.type = "button";
  moveBtn.className = "btn";
  moveBtn.textContent = "移动到此坐标";
  moveBtn.addEventListener("click", () =>
    void submitLocationMove(loc, { toRow, toCol, msg, bus })
  );
  moveForm.appendChild(moveBtn);
  box.appendChild(moveForm);

  box.appendChild(sectionTitle("保存为模板"));
  const tplForm = document.createElement("div");
  tplForm.className = "form";
  const tplId = textInput(`${loc.row}_${loc.col}`, "模板 id，如 cafe_tpl");
  const tplName = textInput(loc.name, "模板名称");
  tplForm.appendChild(field("模板 id", tplId));
  tplForm.appendChild(field("模板名称", tplName));
  const tplBtn = document.createElement("button");
  tplBtn.type = "button";
  tplBtn.className = "btn";
  tplBtn.textContent = "捕获模板";
  tplBtn.addEventListener("click", () =>
    void submitTemplateCapture(loc, { tplId, tplName, msg })
  );
  tplForm.appendChild(tplBtn);
  box.appendChild(tplForm);

  box.appendChild(sectionTitle("连接槽位"));
  for (const d of DIRECTIONS) {
    const slot = loc.connections?.[d];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    text.textContent = `${DIR_LABELS[d]} · ${slotSummary(slot, byLoc)}`;
    if (slot?.enabled && (slot.paths || []).some((p) => pathDead(byLoc, p))) {
      text.classList.add("text-dead");
    }
    const edit = document.createElement("span");
    edit.className = "exit-item-dir";
    edit.textContent = "编辑";
    btn.appendChild(text);
    btn.appendChild(edit);
    btn.addEventListener("click", () => bus.onSelectSlot(loc.row, loc.col, d));
    box.appendChild(btn);
  }

  const danger = document.createElement("div");
  danger.className = "form-actions";
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn btn-danger";
  delBtn.textContent = "删除地块";
  delBtn.addEventListener("click", () => {
    confirmModal(
      `删除地块「${loc.name}」？`,
      "将级联删除指向它的连接目标（指向它的主目标路径整条移除，意外目标移除）。",
      async () => {
        try {
          await apiPost("world/location/delete", { row: loc.row, col: loc.col });
          state.selection = null;
          bus.onSubmit();
        } catch (error) {
          openErrorModal("删除失败", error);
        }
      },
      "删除"
    );
  });
  danger.appendChild(delBtn);
  box.appendChild(danger);
  return box;
}

async function submitLocationUpdate(loc, f) {
  const { nameInput, desc, msg, bus } = f;
  const name = nameInput.value.trim();
  if (!name) {
    msg.show("地块名称不能为空");
    return;
  }
  const body = { row: loc.row, col: loc.col, name, description: desc.get() };
  try {
    await apiPost("world/location/update", body);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

async function submitLocationMove(loc, f) {
  const { toRow, toCol, msg, bus } = f;
  const row = Number(toRow.value);
  const col = Number(toCol.value);
  if (!Number.isInteger(row) || !Number.isInteger(col)) {
    msg.show("目标坐标必须是整数");
    return;
  }
  try {
    await apiPost("world/location/move", {
      row: loc.row,
      col: loc.col,
      to_row: row,
      to_col: col,
    });
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

async function submitTemplateCapture(loc, f) {
  const { tplId, tplName, msg } = f;
  const id = tplId.value.trim();
  const name = tplName.value.trim();
  if (!id) {
    msg.show("模板 id 不能为空");
    return;
  }
  if (!name) {
    msg.show("模板名称不能为空");
    return;
  }
  try {
    await apiPost("world/template/create", {
      id,
      name,
      row: loc.row,
      col: loc.col,
    });
    msg.show("已保存为模板");
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 空地块（新建地块） ----------
export function cellCreateEl(world, row, col, bus) {
  const form = document.createElement("div");
  form.className = "form";
  form.appendChild(kv("坐标（已锁定）", `(${row}, ${col})`));

  const templates = Array.isArray(world?.templates) ? world.templates : [];
  const tplInput = selectInput(
    [["", "（不使用模板）"], ...templates.map((t) => [t.id, t.name])],
    ""
  );
  form.appendChild(field("从模板创建", tplInput));

  const nameInput = textInput("", "地块名称（模板已选时可留空）");
  const desc = scheduleEditor(null);
  form.appendChild(field("名称", nameInput));
  form.appendChild(field("描述", desc.el));
  const msg = formMsg(form);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-primary";
  btn.textContent = "创建地块";
  btn.addEventListener("click", () =>
    void submitCellCreate({ tplInput, nameInput, desc, row, col, msg, bus })
  );
  form.appendChild(btn);
  return form;
}

async function submitCellCreate(f) {
  const { tplInput, nameInput, desc, row, col, msg, bus } = f;
  const templateId = tplInput.value;
  const name = nameInput.value.trim();
  if (!templateId && !name) {
    msg.show("不使用模板时名称不能为空");
    return;
  }
  const body = { row, col, name };
  if (templateId) {
    body.template_id = templateId;
  } else {
    body.description = desc.get();
  }
  try {
    await apiPost("world/location/create", body);
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 连接槽位（查看 / 编辑） ----------
export function slotViewEl(loc, direction, slot, byLoc, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("地块", loc.name));
  box.appendChild(kv("方向", DIR_LABELS[direction]));
  box.appendChild(kv("启用", slot.enabled ? "是" : "否"));
  box.appendChild(sectionTitle("路径"));
  const paths = slot.paths || [];
  if (paths.length === 0) {
    box.appendChild(hintEl("该方向没有任何路径。"));
  }
  paths.forEach((p, i) => {
    const item = document.createElement("div");
    item.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    const label = scheduleText(p.label) || "（无标签）";
    const dead = pathDead(byLoc, p);
    text.textContent = `#${i} ${label}`;
    if (dead) {
      text.classList.add("text-dead");
      text.textContent += "（死引用）";
    }
    item.appendChild(text);
    box.appendChild(item);
  });
  return box;
}

export function slotEditEl(loc, direction, slot, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  box.appendChild(kv("地块", loc.name));
  box.appendChild(kv("方向（固定）", DIR_LABELS[direction]));
  const enabledInput = checkboxInput(slot.enabled === true);
  box.appendChild(field("启用该方向", enabledInput));

  box.appendChild(sectionTitle("平行路径"));
  const paths = (slot.paths || []).map((p) => ({
    label: p.label,
    reveal_target: p.reveal_target !== false,
    targets: (p.targets || []).map((t) => ({ ...t })),
  }));
  const editors = [];
  const list = document.createElement("div");
  list.className = "detail-stack";

  function rebuild() {
    list.textContent = "";
    editors.length = 0;
    paths.forEach((p, i) => {
      const wrap = document.createElement("div");
      wrap.className = "path-block";
      const head = document.createElement("div");
      head.className = "path-block-head";
      const title = document.createElement("span");
      title.className = "path-block-title";
      title.textContent = `路径 #${i}${i === 0 ? "（主路径）" : ""}`;
      head.appendChild(title);
      const up = document.createElement("button");
      up.type = "button";
      up.className = "btn btn-mini";
      up.textContent = "↑";
      up.disabled = i === 0;
      up.addEventListener("click", () => {
        [paths[i - 1], paths[i]] = [paths[i], paths[i - 1]];
        rebuild();
      });
      head.appendChild(up);
      const down = document.createElement("button");
      down.type = "button";
      down.className = "btn btn-mini";
      down.textContent = "↓";
      down.disabled = i === paths.length - 1;
      down.addEventListener("click", () => {
        [paths[i + 1], paths[i]] = [paths[i], paths[i + 1]];
        rebuild();
      });
      head.appendChild(down);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-mini";
      del.textContent = "删";
      del.addEventListener("click", () => {
        paths.splice(i, 1);
        rebuild();
      });
      head.appendChild(del);
      wrap.appendChild(head);
      const editor = pathEditor(p);
      editors[i] = editor;
      wrap.appendChild(editor.el);
      list.appendChild(wrap);
    });
  }
  rebuild();

  box.appendChild(list);
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "btn";
  addBtn.textContent = "＋ 路径";
  addBtn.addEventListener("click", () => {
    paths.push({ label: null, reveal_target: true, targets: [] });
    rebuild();
  });
  box.appendChild(addBtn);

  const msg = formMsg(box);
  const actions = document.createElement("div");
  actions.className = "form-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = "保存槽位";
  saveBtn.addEventListener("click", () =>
    void submitSlotUpdate(loc, direction, enabledInput, editors, msg, bus)
  );
  actions.appendChild(saveBtn);
  box.appendChild(actions);
  return box;
}

async function submitSlotUpdate(loc, direction, enabledInput, editors, msg, bus) {
  const clean = [];
  for (const editor of editors) {
    const p = editor.get();
    const targets = (p.targets || []).filter(
      (t) => Number.isInteger(Number(t.row)) && Number.isInteger(Number(t.col))
    );
    if (targets.length === 0) {
      continue; // 无有效目标 → 丢弃该路径（保存后即删）
    }
    clean.push({
      label: p.label,
      reveal_target: p.reveal_target !== false,
      targets,
    });
  }
  try {
    await apiPost("world/connection/update", {
      row: loc.row,
      col: loc.col,
      direction,
      enabled: enabledInput.checked,
      paths: clean,
    });
    bus.onSubmit();
  } catch (error) {
    msg.show(error?.message || String(error));
  }
}

// ---------- 间隙（两侧槽位） ----------
// key 形如 h:row:col / v:row:col；h 间隙两侧 = 左 (row,col) 的 right 槽 + 右 (row,col+1)
// 的 left 槽；v 间隙两侧 = 上 (row,col) 的 down 槽 + 下 (row+1,col) 的 up 槽。
function gapSides(world, key) {
  const [kind, a, b] = key.split(":");
  const byLoc = new Map();
  for (const l of world.locations || []) {
    byLoc.set(`${l.col},${l.row}`, l);
  }
  const sides = [];
  if (kind === "h") {
    const r = Number(a);
    const c = Number(b);
    const left = byLoc.get(`${c},${r}`);
    const right = byLoc.get(`${c + 1},${r}`);
    if (left) sides.push({ loc: left, direction: "right" });
    if (right) sides.push({ loc: right, direction: "left" });
  } else {
    const r = Number(a);
    const c = Number(b);
    const top = byLoc.get(`${c},${r}`);
    const bottom = byLoc.get(`${c},${r + 1}`);
    if (top) sides.push({ loc: top, direction: "down" });
    if (bottom) sides.push({ loc: bottom, direction: "up" });
  }
  return { kind, sides };
}

export function gapViewEl(world, key, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  const { kind, sides } = gapSides(world, key);
  const byLoc = locAtMap(world);
  box.appendChild(kv("方位", kind === "h" ? "横向间隙" : "纵向间隙"));
  if (sides.length === 0) {
    box.appendChild(hintEl("该间隙两侧都没有地块。"));
    return box;
  }
  for (const { loc, direction } of sides) {
    const slot = loc.connections?.[direction];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    text.textContent = `${loc.name} ${DIR_LABELS[direction]} · ${slotSummary(slot, byLoc)}`;
    if (slot?.enabled && (slot.paths || []).some((p) => pathDead(byLoc, p))) {
      text.classList.add("text-dead");
    }
    const edit = document.createElement("span");
    edit.className = "exit-item-dir";
    edit.textContent = "查看";
    btn.appendChild(text);
    btn.appendChild(edit);
    btn.addEventListener("click", () => bus.onSelectSlot(loc.row, loc.col, direction));
    box.appendChild(btn);
  }
  return box;
}

export function gapEditEl(world, key, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  const { kind, sides } = gapSides(world, key);
  const byLoc = locAtMap(world);
  box.appendChild(kv("方位", kind === "h" ? "横向间隙" : "纵向间隙"));
  if (sides.length === 0) {
    box.appendChild(hintEl("该间隙两侧都没有地块。"));
    return box;
  }
  for (const { loc, direction } of sides) {
    const slot = loc.connections?.[direction];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    text.textContent = `${loc.name} ${DIR_LABELS[direction]} · ${slotSummary(slot, byLoc)}`;
    if (slot?.enabled && (slot.paths || []).some((p) => pathDead(byLoc, p))) {
      text.classList.add("text-dead");
    }
    const edit = document.createElement("span");
    edit.className = "exit-item-dir";
    edit.textContent = "编辑";
    btn.appendChild(text);
    btn.appendChild(edit);
    btn.addEventListener("click", () => bus.onSelectSlot(loc.row, loc.col, direction));
    box.appendChild(btn);
  }
  return box;
}

function locAtMap(world) {
  const m = new Map();
  for (const l of world.locations || []) {
    m.set(`${l.col},${l.row}`, l);
  }
  return m;
}

// ---------- 模板 ----------
export function templatesEl(world, bus) {
  const box = document.createElement("div");
  box.className = "detail-stack";
  const templates = Array.isArray(world?.templates) ? world.templates : [];
  box.appendChild(
    hintEl("模板 = 复制预设（name / description / 4 槽位连接全部复制）。在地块详情 →「保存为模板」捕获；下方可重命名 / 删除。")
  );
  if (templates.length === 0) {
    box.appendChild(hintEl("暂无模板。"));
    return box;
  }
  for (const t of templates) {
    const item = document.createElement("div");
    item.className = "exit-item";
    const text = document.createElement("span");
    text.className = "exit-item-text";
    text.textContent = t.name;
    const actions = document.createElement("span");
    actions.className = "exit-item-dir";
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "btn btn-mini";
    rename.textContent = "重命名";
    rename.addEventListener("click", () => {
      promptModal(`重命名模板「${t.name}」`, "新名称", t.name, async (value) => {
        try {
          await apiPost("world/template/update", { id: t.id, name: value });
          bus.onSubmit();
        } catch (error) {
          openErrorModal("重命名失败", error);
        }
      });
    });
    actions.appendChild(rename);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-mini";
    del.textContent = "删除";
    del.addEventListener("click", () => {
      confirmModal(
        `删除模板「${t.name}」？`,
        "已应用该模板的地块不受影响。",
        async () => {
          try {
            await apiPost("world/template/delete", { id: t.id });
            bus.onSubmit();
          } catch (error) {
            openErrorModal("删除失败", error);
          }
        },
        "删除"
      );
    });
    actions.appendChild(del);
    item.appendChild(text);
    item.appendChild(actions);
    box.appendChild(item);
  }
  return box;
}
