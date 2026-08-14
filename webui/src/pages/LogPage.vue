<template>
  <div class="page">
    <header class="page-header">
      <h1>世界日志</h1>
      <div class="seg">
        <button :class="{ on: scope === 'all' }" @click="scope = 'all'">全图</button>
        <button :class="{ on: scope === 'cell' }" @click="scope = 'cell'">当前地块</button>
      </div>
    </header>

    <p v-if="!store.connected" class="conn-warn">⚠ 实时连接断开，重连中……</p>

    <ul class="log-list">
      <li v-for="(item, i) in filtered" :key="i" class="log-item">
        <span class="log-time">{{ timeOf(item.ts) }}</span>
        <span class="log-event">{{ eventLabel(item) }}</span>
        <span class="log-text">{{ eventText(item) }}</span>
      </li>
    </ul>
    <p v-if="!filtered.length" class="empty">还没有事件。</p>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { store } from "../store";

const scope = ref("all");

const filtered = computed(() => {
  if (scope.value === "all") return store.log;
  if (!store.entity) return [];
  const { map_id, row, col } = store.entity;
  return store.log.filter((item) => {
    const e = item.entity;
    return e && e.map_id === map_id && e.row === row && e.col === col;
  });
});

function timeOf(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

const EVENT_LABELS = {
  on_say: "说话",
  on_entity_move: "移动",
  on_entity_enter: "进入",
  on_interact: "交互",
  on_item_used: "使用物品",
  on_entity_changed: "变化",
  on_entity_removed: "移除",
  on_world_edited: "世界编辑",
};

function eventLabel(item) {
  return EVENT_LABELS[item.event] || item.event;
}

function eventText(item) {
  const name = item.entity ? item.entity.name : "";
  switch (item.event) {
    case "on_say":
      return `${name}：「${item.text}」`;
    case "on_entity_move":
      return `${name} 从 ${fmtPos(item.from)} 移动到 ${fmtPos(item.to)}`;
    case "on_entity_enter":
      return `${name} 进入了 ${item.map_id} (${item.row}, ${item.col})`;
    case "on_interact": {
      const req = item.request || {};
      return `${name} 对 ${req.target_id || "?"} 执行了「${req.action}」`;
    }
    case "on_item_used":
      return `${name} 使用了 ${item.item_id}`;
    case "on_entity_removed":
      return `${item.entity?.name || "实体"} 被移除`;
    case "on_world_edited":
      return `世界被编辑：${JSON.stringify(item.what)}`;
    default:
      return JSON.stringify(item);
  }
}

function fmtPos(pos) {
  if (!pos || pos.length < 3) return "?";
  return `${pos[0]}(${pos[1]}, ${pos[2]})`;
}
</script>
