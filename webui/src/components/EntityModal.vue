<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="visible" class="modal-mask" @click.self="close">
        <div class="modal-drawer">
          <header class="modal-header">
            <div>
              <h2>{{ title }}</h2>
              <span v-if="kindLabel" class="kind-tag">{{ kindLabel }}</span>
            </div>
            <button class="btn-icon" @click="close">✕</button>
          </header>
          <p v-if="desc" class="modal-desc">{{ desc }}</p>

          <div class="modal-body">
            <!-- 实体默认菜单：可用动作 -->
            <template v-if="!result">
              <div v-if="actions.length" class="ui-actions">
                <button
                  v-for="(a, i) in actions"
                  :key="i"
                  class="btn btn-primary"
                  @click="runAction(a.action, a.args)"
                >
                  {{ a.label }}
                </button>
              </div>
              <p v-else class="ui-text">这个实体没有什么可做的。</p>
            </template>

            <!-- 交互结果：按 UiBlock schema 渲染（B10） -->
            <template v-else>
              <p v-if="result.text" class="ui-text result-text">{{ result.text }}</p>
              <UiBlockRenderer v-if="result.ui" :block="result.ui" @action="onUiAction" />
              <button class="btn btn-ghost back-btn" @click="back">← 返回</button>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, ref } from "vue";
import { notifyError, store } from "../store";
import { McpClient } from "../mcpc";
import UiBlockRenderer from "./UiBlockRenderer.vue";

const props = defineProps({
  visible: { type: Boolean, default: false },
  entity: { type: Object, default: null }, // 目标实体（peers 项）
});
const emit = defineEmits(["close", "interacted"]);

const mcp = new McpClient();
const result = ref(null);
const pending = ref(false);

const title = computed(() => (props.entity ? props.entity.name : ""));
const desc = computed(() => (props.entity ? props.entity.desc : ""));
const kindLabel = computed(() => (props.entity ? props.entity.kind : ""));
const actions = computed(
  () => (props.entity && props.entity.actions) || [],
);

async function ensureMcp() {
  if (!mcp.sessionId) await mcp.initialize();
}

async function runAction(action, args = {}) {
  if (pending.value || !props.entity) return;
  pending.value = true;
  try {
    await ensureMcp();
    const res = await mcp.callTool("world_interact", {
      target_id: props.entity.id,
      action,
      args,
    });
    result.value = res;
    emit("interacted");
  } catch (e) {
    notifyError(e.message || "交互失败");
  } finally {
    pending.value = false;
  }
}

async function onUiAction(action, args = {}) {
  await runAction(action, args);
}

function back() {
  result.value = null;
}

function close() {
  result.value = null;
  emit("close");
}
</script>
