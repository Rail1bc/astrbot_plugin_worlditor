<template>
  <div class="world-map" ref="root">
    <svg
      :viewBox="viewBox"
      class="map-svg"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointerleave="onPointerUp"
    >
      <g>
        <!-- 地块 -->
        <template v-for="loc in visibleLocations" :key="keyOf(loc)">
          <rect
            :x="x(loc.col)"
            :y="y(loc.row)"
            :width="CELL"
            :height="CELL"
            class="loc-cell"
            :class="{ current: isCurrent(loc) }"
            @click.stop
          />
          <text
            :x="x(loc.col) + CELL / 2"
            :y="y(loc.row) + CELL / 2 + 4"
            class="loc-name"
            text-anchor="middle"
          >
            {{ loc.name }}
          </text>
        </template>

        <!-- 我的位置 -->
        <circle
          v-if="playerPos"
          :cx="x(playerPos.col) + CELL / 2"
          :cy="y(playerPos.row) + CELL / 2"
          :r="17"
          class="player-ring"
        />

        <!-- 实体：仅小圆点标记（不直接绘制在地块上，B1 实体列表保持简单；
             名称与 kind 标签在同地块角色条展示），点击打开交互弹窗 -->
        <template v-for="e in visibleEntities" :key="e.id">
          <circle
            :cx="x(e.col) + CELL / 2"
            :cy="y(e.row) + CELL - 8"
            :r="8"
            class="entity-dot"
            :class="entityClass(e)"
            @click.stop="emit('select-entity', e)"
          />
        </template>
      </g>
    </svg>

    <div class="map-controls">
      <button class="btn-icon" @click="zoomBy(1.3)">＋</button>
      <button class="btn-icon" @click="zoomBy(1 / 1.3)">－</button>
      <button class="btn-icon" @click="fitAll">⛶</button>
      <button v-if="playerPos" class="btn-icon" @click="focusPlayer">◎</button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";

const props = defineProps({
  locations: { type: Array, default: () => [] },
  entities: { type: Array, default: () => [] },
  playerPos: { type: Object, default: null }, // {map_id, row, col}
  currentPos: { type: Object, default: null },
});
const emit = defineEmits(["select-entity"]);

const CELL = 46;
const PAD = 30;

const root = ref(null);
const pan = ref({ x: 0, y: 0 });
const scale = ref(2);

const bounds = computed(() => {
  let minR = 0, maxR = 0, minC = 0, maxC = 0;
  for (const loc of props.locations) {
    minR = Math.min(minR, loc.row);
    maxR = Math.max(maxR, loc.row);
    minC = Math.min(minC, loc.col);
    maxC = Math.max(maxC, loc.col);
  }
  return { minR, maxR, minC, maxC };
});

const x = (col) => PAD + (col - bounds.value.minC) * CELL;
const y = (row) => PAD + (row - bounds.value.minR) * CELL;

const viewBox = computed(() => {
  const w = (bounds.value.maxC - bounds.value.minC + 1) * CELL + PAD * 2;
  const h = (bounds.value.maxR - bounds.value.minR + 1) * CELL + PAD * 2;
  return `${pan.value.x} ${pan.value.y} ${w / scale.value} ${h / scale.value}`;
});

const keyOf = (loc) => `${loc.map_id}:${loc.row}:${loc.col}`;
const visibleLocations = computed(() =>
  props.locations.filter((l) => l.map_id === (props.currentPos?.map_id || "default")),
);
const visibleEntities = computed(() =>
  props.entities.filter(
    (e) => e.map_id === (props.currentPos?.map_id || "default"),
  ),
);

function isCurrent(loc) {
  return (
    props.currentPos &&
    loc.row === props.currentPos.row &&
    loc.col === props.currentPos.col
  );
}

function entityClass(e) {
  if (e.kind === "player") return "kind-player";
  if (e.kind === "agent") return "kind-agent";
  return "kind-npc";
}

// 默认聚焦玩家位置（局部视野，而非全图平铺）
function focusPlayer() {
  if (!props.playerPos) return;
  const w = (bounds.value.maxC - bounds.value.minC + 1) * CELL + PAD * 2;
  const h = (bounds.value.maxR - bounds.value.minR + 1) * CELL + PAD * 2;
  scale.value = 2;
  const px = x(props.playerPos.col) + CELL / 2;
  const py = y(props.playerPos.row) + CELL / 2;
  pan.value = { x: px - w / (2 * scale.value), y: py - h / (2 * scale.value) };
}

function fitAll() {
  scale.value = 1;
  pan.value = { x: 0, y: 0 };
}

watch(
  () => props.playerPos,
  () => {
    nextTick(focusPlayer);
  },
  { immediate: true },
);

// 平移 / 缩放
let dragging = false;
let last = { x: 0, y: 0 };

function onPointerDown(e) {
  dragging = true;
  last = { x: e.clientX, y: e.clientY };
  root.value?.setPointerCapture?.(e.pointerId);
}

function onPointerMove(e) {
  if (!dragging) return;
  const dx = e.clientX - last.x;
  const dy = e.clientY - last.y;
  last = { x: e.clientX, y: e.clientY };
  pan.value = { x: pan.value.x - dx, y: pan.value.y - dy };
}

function onPointerUp() {
  dragging = false;
}

function zoomBy(factor) {
  scale.value = Math.min(6, Math.max(0.2, scale.value * factor));
}
</script>
