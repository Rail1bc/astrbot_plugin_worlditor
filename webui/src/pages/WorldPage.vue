<template>
  <div class="page world-page">
    <!-- 顶栏：当前地块 -->
    <header class="loc-header">
      <h1>{{ scene ? scene.location.name : watching ? "围观模式" : "世界" }}</h1>
      <p>
        {{
          scene
            ? scene.description
            : watching
              ? "只读围观：点击相邻地块可浏览"
              : "加载中……"
        }}
      </p>
      <span v-if="store.connected" class="live-dot" title="实时连接"></span>
    </header>

    <!-- 地图：纯玩家有限视角（当前地块 + 四周相邻） -->
    <div class="map-wrap">
      <WorldMap
        :locations="store.world?.locations || []"
        :entities="store.world?.entities || []"
        :center="center"
        :player-pos="myPos"
        :browsable="watching"
        @select-entity="openEntity"
        @select-location="browseTo"
      />
    </div>

    <!-- 同地块角色条 -->
    <div v-if="peers.length" class="peer-bar">
      <div
        v-for="p in peers"
        :key="p.entity.id"
        class="peer-chip"
        @click="openEntity(p.entity)"
      >
        <span class="peer-name">{{ p.entity.name }}</span>
        <span class="kind-tag">{{ p.entity.kind }}</span>
      </div>
    </div>

    <!-- 底部动作区（围观模式只读，隐藏） -->
    <div v-if="!watching" class="action-dock">
      <div class="dir-pad">
        <button
          v-for="(path, i) in scene?.paths || []"
          :key="i"
          class="dir-btn"
          :class="'dir-' + path.direction"
          @click="move(path.direction, path.path_index)"
        >
          <span class="dir-label">{{ dirLabel(path.direction) }}</span>
          <span class="dir-target">{{ path.target_name || path.label }}</span>
        </button>
      </div>
      <form class="say-form" @submit.prevent="say">
        <input v-model="sayText" placeholder="说点什么……" maxlength="200" />
        <button type="submit" class="btn btn-primary" :disabled="busy">说</button>
      </form>
    </div>

    <!-- 实体交互弹窗 -->
    <EntityModal
      :visible="modalEntity !== null"
      :entity="modalEntity"
      @close="modalEntity = null"
      @interacted="refreshAll"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getScene, getState, eventsUrl } from "../api";
import { McpClient } from "../mcpc";
import { notifyError, store } from "../store";
import WorldMap from "../components/WorldMap.vue";
import EntityModal from "../components/EntityModal.vue";

const mcp = new McpClient();
const sayText = ref("");
const busy = ref(false);
const modalEntity = ref(null);
const watching = ref(false); // 围观模式（read 档无实体）
let es = null;

const myPos = computed(() =>
  store.entity
    ? { map_id: store.entity.map_id, row: store.entity.row, col: store.entity.col }
    : null,
);
const peers = computed(() => store.peers);
// 围观者浏览中心（点击相邻地块切换；玩家视角固定跟随自己）
const browseCenter = ref(null);
// 视野中心：玩家位置优先；围观者用出生点/浏览位置
const spawnPos = computed(() => {
  const m = store.world?.maps?.[0];
  return m ? { map_id: m.id, row: m.spawn_row, col: m.spawn_col } : null;
});
const center = computed(() => browseCenter.value || myPos.value || spawnPos.value);

function browseTo(loc) {
  browseCenter.value = { map_id: loc.map_id, row: loc.row, col: loc.col };
}

const DIR_LABELS = { up: "北", right: "东", down: "南", left: "西" };
const dirLabel = (d) => DIR_LABELS[d] || d;

async function ensureMcp() {
  if (!mcp.sessionId) await mcp.initialize();
}

async function refreshScene() {
  try {
    const data = await getScene();
    watching.value = false;
    store.entity = data.entity;
    store.scene = data.scene;
    store.peers = data.peers;
  } catch (e) {
    if (e.status === 400 && String(e.message).includes("entity_id")) {
      // read 档围观：无自己的实体
      watching.value = true;
      store.entity = null;
      store.scene = null;
      store.peers = [];
    } else {
      notifyError(e.message);
    }
  }
}

async function refreshWorld() {
  try {
    store.world = await getState();
  } catch (e) {
    notifyError(e.message);
  }
}

async function refreshAll() {
  await Promise.all([refreshScene(), refreshWorld()]);
}

async function move(direction, pathIndex) {
  if (busy.value || watching.value) return;
  busy.value = true;
  try {
    await ensureMcp();
    const res = await mcp.callTool("world_move", {
      direction,
      path: pathIndex,
    });
    if (res.scene) {
      store.scene = res.scene;
      store.entity = { ...store.entity, map_id: res.scene.map_id, row: res.scene.row, col: res.scene.col };
      store.peers = (res.entities || []).map((e) => ({ entity: e }));
    } else if (res.text) {
      notifyError(res.text);
    }
  } catch (e) {
    notifyError(e.message || "移动失败");
  } finally {
    busy.value = false;
  }
}

async function say() {
  const text = sayText.value.trim();
  if (!text || busy.value || watching.value) return;
  sayText.value = "";
  try {
    await ensureMcp();
    const res = await mcp.callTool("world_say", { text, scope: "cell" });
    if (res.text && !res.text.startsWith("你说")) notifyError(res.text);
  } catch (e) {
    notifyError(e.message || "说话失败");
  }
}

async function openEntity(entity) {
  // 优先用同地块角色条里的实体（含 actions）
  const local = store.peers.find((p) => p.entity.id === entity.id);
  if (local) {
    modalEntity.value = { ...local.entity, actions: local.actions || [] };
    return;
  }
  // 围观者/远处实体：拉取该实体场景（后端返回实体自身 actions）
  try {
    const data = await getScene(entity.id);
    modalEntity.value = data.entity;
  } catch (e) {
    modalEntity.value = { ...entity, actions: [] };
  }
}

function handleSseEvent(payload) {
  store.log.unshift(payload);
  if (store.log.length > 200) store.log.pop();
  // 自己的位置变化 → 刷新场景（增量 + 快照兜底，B11）
  const e = payload.entity;
  if (
    e &&
    store.entity &&
    e.id === store.entity.id &&
    (payload.event === "on_entity_move" || payload.event === "on_entity_enter")
  ) {
    refreshScene();
  }
  if (payload.event === "on_world_edited") {
    refreshWorld();
  }
}

function connectEvents() {
  es = new EventSource(eventsUrl());
  es.onopen = () => {
    store.connected = true;
    refreshScene(); // 重连后拉快照兜底
  };
  es.onerror = () => {
    store.connected = false;
  };
  es.onmessage = (e) => {
    try {
      handleSseEvent(JSON.parse(e.data));
    } catch {
      /* ignore */
    }
  };
}

onMounted(async () => {
  await refreshAll();
  if (!watching.value) {
    connectEvents(); // 围观者无 play 档凭据，不连 SSE
  }
});

onUnmounted(() => {
  es && es.close();
});
</script>
