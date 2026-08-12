<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { state } from '../store'

const props = defineProps<{ tabPath: string }>()
const headings = ref<Array<{ level: number; text: string; slug: string; line: number }>>([])
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await api.fileOutline(props.tabPath)
    headings.value = res.headings
  } catch {
    headings.value = []
  } finally {
    loading.value = false
  }
}

function jump(slug: string): void {
  window.dispatchEvent(new CustomEvent('outline-jump', { detail: slug }))
}

function onSaved(e: Event): void {
  if ((e as CustomEvent<string>).detail === props.tabPath) void load()
}

watch(() => props.tabPath, () => void load(), { immediate: true })
onMounted(() => window.addEventListener('tab-saved', onSaved))
onBeforeUnmount(() => window.removeEventListener('tab-saved', onSaved))
</script>

<template>
  <div class="outline-panel">
    <div class="panel-title">大纲</div>
    <div v-if="loading" class="panel-empty">加载中…</div>
    <div v-else-if="headings.length === 0" class="panel-empty">无标题</div>
    <div
      v-for="(h, i) in headings"
      :key="i"
      class="outline-item"
      :style="{ paddingLeft: 8 + (h.level - 1) * 14 + 'px' }"
      :title="h.text"
      @click="jump(h.slug)"
    >
      {{ h.text }}
    </div>
  </div>
</template>
