<template>
  <div class="app">
    <!-- 未登录 → 登录/注册 -->
    <AuthPage v-if="!hasToken" />

    <template v-else>
      <main class="app-main">
        <component :is="currentPage" />
      </main>

      <!-- 底部导航（移动端优先） -->
      <nav class="tabbar">
        <button
          v-for="tab in tabs"
          :key="tab.path"
          class="tab"
          :class="{ on: route === tab.path }"
          @click="goto(tab.path)"
        >
          <span class="tab-icon">{{ tab.icon }}</span>
          <span>{{ tab.label }}</span>
        </button>
      </nav>

      <button class="logout-btn" title="退出登录" @click="doLogout">⎋</button>

      <!-- 全局错误提示 -->
      <Transition name="fade">
        <div v-if="store.error" class="toast">{{ store.error }}</div>
      </Transition>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { logout, setToken } from "./api";
import { store } from "./store";
import AuthPage from "./pages/AuthPage.vue";
import WorldPage from "./pages/WorldPage.vue";
import BagPage from "./pages/BagPage.vue";
import MePage from "./pages/MePage.vue";
import LogPage from "./pages/LogPage.vue";

const route = ref(location.hash.replace(/^#/, "") || "/world");
const PAGES = {
  "/world": WorldPage,
  "/bag": BagPage,
  "/me": MePage,
  "/log": LogPage,
};
const tabs = [
  { path: "/world", label: "世界", icon: "🗺" },
  { path: "/bag", label: "背包", icon: "🎒" },
  { path: "/me", label: "角色", icon: "🧍" },
  { path: "/log", label: "日志", icon: "📜" },
];

const hasToken = computed(() => Boolean(store.token));
const currentPage = computed(() => PAGES[route.value] || WorldPage);

function goto(path) {
  location.hash = path;
}

function doLogout() {
  logout().catch(() => {});
  setToken("");
  store.token = "";
  store.entity = null;
  store.scene = null;
  store.world = null;
  store.log = [];
  location.hash = "#/auth";
}

onMounted(() => {
  store.token = localStorage.getItem("worlditor_token") || "";
  window.addEventListener("hashchange", () => {
    route.value = location.hash.replace(/^#/, "") || "/world";
  });
});
</script>
