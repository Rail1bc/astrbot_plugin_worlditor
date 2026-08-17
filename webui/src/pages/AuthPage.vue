<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>🌍 世界编辑器</h1>
      <p class="auth-sub">进入世界，与 AI 同场。</p>

      <div class="seg wide">
        <button :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <input v-model="username" placeholder="用户名" required minlength="2" maxlength="24" />
        <input v-model="password" type="password" placeholder="密码" required minlength="6" />
        <button type="submit" class="btn btn-primary" :disabled="busy">
          {{ busy ? "请稍候……" : mode === "login" ? "登录" : "注册并进入" }}
        </button>
      </form>

      <button class="btn btn-ghost watch-btn" :disabled="busy" @click="watch">
        以围观者身份进入（read 档）
      </button>

      <details class="agent-register">
        <summary>agent 接入</summary>
        <form class="auth-form" @submit.prevent="submitAgent">
          <input v-model="agentName" placeholder="agent 名称" minlength="2" maxlength="24" />
          <button type="submit" class="btn btn-primary" :disabled="busy">注册 agent 凭据</button>
        </form>
        <p v-if="agentToken" class="result-text">凭据：{{ agentToken }}</p>
      </details>

      <p v-if="error" class="error-text">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { login, register, registerAgent, readToken, setToken } from "../api";
import { store } from "../store";

const mode = ref("login");
const username = ref("");
const password = ref("");
const agentName = ref("");
const agentToken = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const data =
      mode.value === "login"
        ? await login(username.value, password.value)
        : await register(username.value, password.value);
    enter(data.token);
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function watch() {
  error.value = "";
  busy.value = true;
  try {
    const data = await readToken();
    enter(data.token);
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function submitAgent() {
  error.value = "";
  busy.value = true;
  try {
    const data = await registerAgent(agentName.value);
    agentToken.value = data.token.token;
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

function enter(tokenData) {
  // 接口返回 {ok, token: {token, ...}}——兼容传对象或字符串
  const t = typeof tokenData === "string" ? tokenData : tokenData && tokenData.token;
  if (!t) {
    error.value = "未获取到有效凭据";
    return;
  }
  setToken(t);
  store.token = t;
  location.hash = "#/world";
}
</script>
