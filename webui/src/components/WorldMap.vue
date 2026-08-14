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

        <!-- 实体（名称 + kind 标签，B1） -->
        <template v-for="e in visibleEntities" :key="e.id">
          <circle
            :cx="x(e.col) + CELL / 2"
            :cy="y(e.row) + CELL - 6"
            :r="6"
            class="entity-dot"
            :class="entityClass(e)"
            @click.stop="emit('select-entity', e)"
          />
          <text
            :x="x(e.col) + CELL / 2"
            :y="y(e.row) + CELL + 14"
            class="entity-name"
            text-anchor="middle"
            @click.stop="emit('select-entity', e)"
          >
            {{ e.name }}
          </text>
        </template>

        <!-- 我的位置 -->
        <circle
          v-if="playerPos"
          :cx="x(playerPos.col) + CELL / 2"
          :cy="y(playerPos.row) + CELL / 2"
          :r="16"
          class="player-ring"
        />
      </g>
    </svg>

    <div class="map-controls">
      <button class="btn-icon" @click="zoomBy(1.3)">＋</button>
      <button class="btn-icon" @click="zoomBy(1 / 1.3)">－</button>
      <button class="btn-icon" @click="fit">⛶</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

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
const scale = ref(1);

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
  scale.value = Math.min(4, Math.max(0.2, scale.value * factor));
}

function fit() {
  scale.value = 1;
  pan.value = { x: 0, y: 0 };
}
</script>
