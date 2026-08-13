<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'

const emit = defineEmits<{ (e: 'done'): void }>()
const props = defineProps<{ initialized: boolean }>()

const password = ref('')
const confirm = ref('')
const busy = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  error.value = ''
  if (!password.value || password.value.length < 6) {
    error.value = '密码至少需要 6 位'
    return
  }
  if (!props.initialized && password.value !== confirm.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  try {
    if (props.initialized) {
      await api.login(password.value)
    } else {
      await api.init(password.value)
    }
    emit('done')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="login-card" @submit.prevent="submit">
      <div class="login-brand">
        <h1>拾墨</h1>
        <small>收集知识碎片</small>
      </div>
      <p class="login-hint">
        {{ initialized ? '输入访问密码继续' : '首次使用，请设置访问密码' }}
      </p>
      <label class="login-field">
        <span>访问密码</span>
        <input v-model="password" type="password" placeholder="请输入访问密码" autocomplete="current-password" autofocus />
      </label>
      <label v-if="!initialized" class="login-field">
        <span>确认密码</span>
        <input
          v-model="confirm"
          type="password"
          placeholder="请再次输入密码"
          autocomplete="new-password"
        />
      </label>
      <p v-if="error" class="login-error" role="alert">{{ error }}</p>
      <button class="btn btn--primary" type="submit" :disabled="busy">{{ busy ? '请稍候…' : initialized ? '登录' : '初始化并进入' }}</button>
    </form>
  </div>
</template>
