<template>
  <div class="world-map">
    <svg :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`" class="map-svg">
      <g>
        <!-- 有限视野：玩家当前地块 + 四周相邻地块（存在才显示） -->
        <template v-for="loc in visibleLocations" :key="keyOf(loc)">
          <rect
            :x="gx(loc.col)"
            :y="gy(loc.row)"
            :width="CELL"
            :height="CELL"
            class="loc-cell"
            :class="{
              current: isCenter(loc),
              browsable: browsable && !isCenter(loc),
            }"
            @click="browsable && !isCenter(loc) && emit('select-location', loc)"
          />
          <text
            :x="gx(loc.col) + CELL / 2"
            :y="gy(loc.row) + CELL / 2 + 4"
            class="loc-name"
            text-anchor="middle"
          >
            {{ loc.name }}
          </text>
          <!-- 格内实体：小圆点（名称/标签在同地块角色条，B1） -->
          <circle
            v-for="e in entitiesIn(loc)"
            :key="e.id"
            :cx="gx(loc.col) + CELL - 14"
            :cy="gy(loc.row) + 14"
            :r="8"
            class="entity-dot"
            :class="entityClass(e)"
            @click.stop="emit('select-entity', e)"
          />
          <!-- 当前格内的玩家标记 -->
          <circle
            v-if="playerPos && isCenter(loc)"
            :cx="gx(loc.col) + 14"
            :cy="gy(loc.row) + 14"
            :r="10"
            class="player-mark"
          />
        </template>
      </g>
    </svg>
  </div>
</template>

<script setup>
import { computed } from "vue";

// 纯玩家有限视角：以 center 为中心渲染 3x3 视野（当前地块 + 四周相邻），
// 无全图、无缩放平移控件。
const CELL = 100;
const VIEW_W = 300;
const VIEW_H = 300;

const props = defineProps({
  locations: { type: Array, default: () => [] },
  entities: { type: Array, default: () => [] },
  center: { type: Object, default: null }, // {map_id, row, col} 视野中心
  playerPos: { type: Object, default: null },
  browsable: { type: Boolean, default: false }, // 围观模式：点击相邻地块浏览
});
const emit = defineEmits(["select-entity", "select-location"]);

const gx = (col) => (col - (props.center?.col ?? 0) + 1) * CELL;
const gy = (row) => (row - (props.center?.row ?? 0) + 1) * CELL;

const keyOf = (loc) => `${loc.map_id}:${loc.row}:${loc.col}`;

const visibleLocations = computed(() => {
  if (!props.center) return [];
  const { row, col } = props.center;
  return props.locations.filter(
    (l) =>
      l.map_id === props.center.map_id &&
      Math.abs(l.row - row) <= 1 &&
      Math.abs(l.col - col) <= 1,
  );
});

function isCenter(loc) {
  return (
    props.center &&
    loc.row === props.center.row &&
    loc.col === props.center.col
  );
}

function entitiesIn(loc) {
  return props.entities.filter(
    (e) =>
      e.map_id === loc.map_id && e.row === loc.row && e.col === loc.col,
  );
}

function entityClass(e) {
  if (e.kind === "player") return "kind-player";
  if (e.kind === "agent") return "kind-agent";
  return "kind-npc";
}
</script>
