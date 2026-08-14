<template>
  <div class="page">
    <header class="page-header">
      <h1>背包</h1>
      <span class="sub">{{ items.length }} 种物品</span>
    </header>

    <div v-if="items.length" class="bag-grid">
      <div v-for="(item, i) in items" :key="i" class="bag-cell" @click="use(item)">
        <div class="item-icon">{{ iconOf(item) }}</div>
        <div class="item-name">{{ itemName(item) }}</div>
        <div class="item-count">× {{ item.count }}</div>
        <div v-if="item.def?.use_action" class="item-use">点击使用</div>
      </div>
    </div>
    <p v-else class="empty">背包空空如也——去广场找商贩·阿福看看吧。</p>

    <p v-if="resultText" class="result-text">{{ resultText }}</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { getBag } from "../api";
import { McpClient } from "../mcpc";
import { notifyError } from "../store";

const mcp = new McpClient();
const items = ref([]);
const resultText = ref("");

async function ensureMcp() {
  if (!mcp.sessionId) await mcp.initialize();
}

async function load() {
  try {
    const data = await getBag();
    items.value = data.items || [];
  } catch (e) {
    notifyError(e.message);
  }
}

async function use(item) {
  if (!item.def?.use_action) {
    resultText.value = `${itemName(item)}：${item.def?.desc || "不能直接使用"}`;
    return;
  }
  try {
    await ensureMcp();
    const res = await mcp.callTool("world_use", { item_id: item.item_id });
    resultText.value = res.text || "使用完成";
    await load();
  } catch (e) {
    notifyError(e.message || "使用失败");
  }
}

function iconOf(item) {
  const icons = { apple: "🍎", megaphone: "📢" };
  return icons[item.item_id] || "📦";
}

function itemName(item) {
  return item.def?.name || item.item_id;
}

onMounted(load);
</script>
