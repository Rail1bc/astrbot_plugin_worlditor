<template>
  <div class="page">
    <header class="page-header">
      <h1>角色</h1>
    </header>

    <div v-if="entity" class="me-card">
      <div class="me-avatar">🧍</div>
      <h2>{{ entity.name }}</h2>
      <span class="kind-tag">{{ entity.kind }}</span>
      <p class="me-pos">
        {{ entity.map_id }} ({{ entity.row }}, {{ entity.col }})
      </p>
      <p v-if="entity.desc" class="modal-desc">{{ entity.desc }}</p>
    </div>

    <h3 class="section-title">属性（attrs）</h3>
    <dl v-if="attrsList.length" class="kv-list">
      <template v-for="[k, v] in attrsList" :key="k">
        <dt>{{ k }}</dt>
        <dd>{{ v }}</dd>
      </template>
    </dl>
    <p v-else class="empty">暂无属性。</p>

    <h3 class="section-title">状态（state）</h3>
    <dl v-if="stateList.length" class="kv-list">
      <template v-for="[k, v] in stateList" :key="k">
        <dt>{{ k }}</dt>
        <dd>{{ v }}</dd>
      </template>
    </dl>
    <p v-else class="empty">暂无状态。</p>

    <h3 class="section-title">修改密码</h3>
    <form class="pwd-form" @submit.prevent="doChangePassword">
      <input v-model="oldPwd" type="password" placeholder="原密码" required />
      <input v-model="newPwd" type="password" placeholder="新密码（至少 6 位）" required minlength="6" />
      <button type="submit" class="btn btn-primary">修改</button>
    </form>
    <p v-if="pwdMsg" class="result-text">{{ pwdMsg }}</p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { changePassword, getScene, logout } from "../api";
import { notifyError, store } from "../store";

const entity = ref(null);
const oldPwd = ref("");
const newPwd = ref("");
const pwdMsg = ref("");

const attrsList = computed(() => Object.entries(entity.value?.attrs || {}));
const stateList = computed(() => Object.entries(entity.value?.state || {}));

async function load() {
  try {
    const data = await getScene();
    entity.value = data.entity;
  } catch (e) {
    notifyError(e.message);
  }
}

async function doChangePassword() {
  try {
    await changePassword(oldPwd.value, newPwd.value);
    pwdMsg.value = "密码已修改，请重新登录";
    oldPwd.value = "";
    newPwd.value = "";
    await logout();
    store.token = "";
    location.hash = "#/auth";
  } catch (e) {
    notifyError(e.message);
  }
}

onMounted(load);
</script>
