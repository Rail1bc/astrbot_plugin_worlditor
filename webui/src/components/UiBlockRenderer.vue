<template>
  <div class="ui-block">
    <div v-if="block.title" class="ui-title">{{ block.title }}</div>

    <!-- text / confirm：文本 -->
    <p v-if="block.kind === 'text' || block.kind === 'confirm'" class="ui-text">{{ block.text }}</p>

    <!-- menu：标题 + 文本 + 按钮 -->
    <template v-if="block.kind === 'menu'">
      <p v-if="block.text" class="ui-text">{{ block.text }}</p>
    </template>

    <!-- list：条目列表 -->
    <ul v-if="block.kind === 'list'" class="ui-list">
      <li
        v-for="(item, i) in block.items"
        :key="i"
        class="ui-item"
        :class="{ clickable: item.action }"
        @click="item.action && emit('action', item.action, item.args || {})"
      >
        <span>{{ item.label }}</span>
        <span v-if="item.action" class="ui-item-arrow">›</span>
      </li>
    </ul>

    <!-- form：字段表单 -->
    <form v-if="block.kind === 'form'" class="ui-form" @submit.prevent="submitForm">
      <label v-for="(field, i) in block.fields" :key="i" class="ui-field">
        <span>{{ field.label || field.name }}</span>
        <input
          v-model="formValues[field.name]"
          :type="field.type === 'number' ? 'number' : 'text'"
          :required="!!field.required"
        />
      </label>
      <button
        v-if="block.actions && block.actions.length"
        type="submit"
        class="btn btn-primary"
      >
        {{ block.actions[0].label || '提交' }}
      </button>
    </form>

    <!-- character：角色卡（B1） -->
    <div v-if="block.kind === 'character'" class="ui-character">
      <div class="avatar">{{ avatarEmoji }}</div>
      <dl class="ui-attrs">
        <template v-for="(attr, i) in block.data && block.data.attrs" :key="i">
          <dt>{{ attr.label }}</dt>
          <dd>{{ attr.value }}</dd>
        </template>
      </dl>
    </div>

    <!-- custom：自定义组件（v4.1 先显示 fallback_text；组件加载 v4.2） -->
    <div v-if="block.kind === 'custom'" class="ui-custom">
      <p class="ui-text">{{ fallbackText }}</p>
    </div>

    <!-- 动作按钮（menu/text/confirm/list 通用） -->
    <div v-if="showActions" class="ui-actions">
      <button
        v-for="(action, i) in block.actions"
        :key="i"
        class="btn"
        :class="action.action === 'bye' ? 'btn-ghost' : 'btn-primary'"
        @click="emit('action', action.action, action.args || {})"
      >
        {{ action.label }}
      </button>
    </div>

    <!-- 子块（B9 钩子注入点 / 复合界面） -->
    <div v-for="(child, i) in block.blocks" :key="i" class="ui-children">
      <UiBlockRenderer :block="child" @action="emit('action', $event.action, $event.args)" />
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from "vue";

const props = defineProps({
  block: { type: Object, required: true },
});
const emit = defineEmits(["action"]);

// form 不重复渲染 actions（提交按钮内置）；character/custom 无按钮
const showActions = computed(
  () =>
    props.block.actions &&
    props.block.actions.length &&
    props.block.kind !== "form",
);

const formValues = reactive({});
function submitForm() {
  const action = props.block.actions && props.block.actions[0];
  if (action) {
    emit("action", action.action, { ...(action.args || {}), ...formValues });
  }
}

const fallbackText = computed(
  () =>
    (props.block.data && props.block.data.fallback_text) ||
    "（自定义界面组件暂不可用）",
);

const avatarEmoji = computed(() => {
  const kind = props.block.data && props.block.data.avatar;
  return kind || "🧍";
});
</script>
