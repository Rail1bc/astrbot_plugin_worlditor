// 全局状态（响应式）：token / 我的实体 / 场景 / 世界快照 / SSE 日志。

import { reactive } from "vue";

export const store = reactive({
  token: "",
  entity: null, // 我的实体（player/agent）
  scene: null, // 我的场景（含路径）
  peers: [], // 同地块实体（含 actions）
  world: null, // 世界快照 {maps, locations, entities}
  log: [], // SSE 事件日志（新在前）
  connected: false, // SSE 连接状态
  error: "", // 全局错误提示
});

export function notifyError(message) {
  store.error = message;
  setTimeout(() => {
    if (store.error === message) store.error = "";
  }, 4000);
}

export function entityById(id) {
  if (!store.world) return null;
  return store.world.entities.find((e) => e.id === id) || null;
}
