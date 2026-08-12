<script setup lang="ts">
import { onMounted } from 'vue'
import { api } from '../api'
import { refreshTrash, refreshTree, state } from '../store'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

onMounted(() => {
  void refreshTrash().catch((e) => emit('notify', (e as Error).message, 'error'))
})

async function restore(path: string): Promise<void> {
  try {
    await api.restore(path)
    await refreshTrash()
    await refreshTree()
    emit('notify', '已恢复', 'info')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function purge(): Promise<void> {
  if (!window.confirm('永久清空回收站？此操作不可撤销。')) return
  try {
    await api.purgeTrash()
    await refreshTrash()
    await refreshTree()
    emit('notify', '回收站已清空', 'info')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}
</script>

<template>
  <div class="trash-panel">
    <div class="tree-header">
      <span>回收站</span>
      <button class="icon-btn danger" title="清空回收站" @click="purge">清空</button>
    </div>
    <div v-if="state.trash.length === 0" class="tree-empty">回收站为空</div>
    <div v-for="item in state.trash" :key="item.path" class="trash-row">
      <span class="trash-path" :title="item.path">{{ item.type === 'dir' ? '📁' : '📄' }} {{ item.path }}</span>
      <button class="icon-btn" title="恢复" @click="restore(item.path)">↩</button>
    </div>
  </div>
</template>
