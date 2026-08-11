// app.js — 世界编辑器页入口：装配与模式切换（编辑 / 玩家）
// 单页双模式：编辑=全图可视化 + 编辑；玩家=当前地块 + 1 跳十字视图。
// 约束（沙箱 iframe）：无原生 alert/confirm、无同源 localStorage，纯 ES module 无构建。

import { bindModalDismiss, openModal, state, $ } from "./shared.js";
import { initEditView, renderEdit } from "./edit-view.js";
import { renderPlay } from "./play-view.js";

const bridge = window.AstrBotPluginPage;

// ---------- 玩家生命周期（隐形实体，仅内存，刷新即重新注册） ----------
async function registerPlayer() {
  const data = await bridge.apiPost("world/player/register", {});
  state.playerId = data.player_id;
  $("#player-id").textContent = `玩家 ${data.player_id}`;
}

async function loadWorld() {
  state.world = await bridge.apiGet("world/state", { player_id: state.playerId });
}

async function refreshWorld() {
  await loadWorld();
  render();
}

async function moveTo(exitId) {
  const scene = await bridge.apiPost("world/move", {
    player_id: state.playerId,
    exit_id: exitId,
  });
  // scene 即新场景（SceneView.as_dict），原地更新本地 world.player 后整体重绘
  state.world.player = {
    ...state.world.player,
    location_id: scene.location.id,
    location_name: scene.location.name,
    scene,
  };
  render();
}

// ---------- 渲染 ----------
function render() {
  if (state.mode === "edit") {
    renderEdit(state.world);
  } else {
    renderPlay(state.world, moveTo);
  }
  const locEl = $("#loc-name");
  if (state.world && state.world.player) {
    locEl.textContent = state.world.player.location_name;
  } else if (state.playerId) {
    locEl.textContent = "玩家已失效，请重新注册";
  } else {
    locEl.textContent = "未注册";
  }
}

function switchMode(mode) {
  state.mode = mode;
  $("#mode-edit").classList.toggle("active", mode === "edit");
  $("#mode-play").classList.toggle("active", mode === "play");
  $("#mode-edit").setAttribute("aria-selected", mode === "edit" ? "true" : "false");
  $("#mode-play").setAttribute("aria-selected", mode === "play" ? "true" : "false");
  $("#view-edit").hidden = mode !== "edit";
  $("#view-play").hidden = mode !== "play";
  render();
}

// ---------- 初始化 ----------
async function init() {
  bindModalDismiss();

  $("#btn-refresh").addEventListener("click", async () => {
    try {
      await refreshWorld();
    } catch (error) {
      openModal("载入失败", error?.message || String(error));
    }
  });
  $("#btn-reregister").addEventListener("click", async () => {
    try {
      await registerPlayer();
      await refreshWorld();
    } catch (error) {
      openModal("重新注册失败", error?.message || String(error));
    }
  });
  $("#mode-edit").addEventListener("click", () => switchMode("edit"));
  $("#mode-play").addEventListener("click", () => switchMode("play"));
  initEditView({ onMutate: refreshWorld });

  try {
    await bridge.ready();
    await registerPlayer();
    await loadWorld();
    render();
  } catch (error) {
    openModal("初始化失败", error?.message || String(error));
  }
}

// 页面卸载尽力注销（超时清理兜底）
window.addEventListener("pagehide", () => {
  if (state.playerId) {
    try {
      void bridge.apiPost("world/player/deregister", { player_id: state.playerId });
    } catch {
      // 忽略
    }
  }
});

init();
