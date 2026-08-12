<script setup lang="ts">
import { onMounted } from 'vue'
import { FileText, Folder, RotateCcw, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import { refreshTrash, refreshTree, state } from '../store'
import PanelHeader from './PanelHeader.vue'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

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
    <PanelHeader title="回收站" :count="state.trash.length">
      <template #actions>
        <IconButton title="清空回收站" kind="danger" :disabled="state.trash.length === 0" @click="purge">
          <Trash2 :size="13" />
        </IconButton>
      </template>
    </PanelHeader>
    <EmptyState v-if="state.trash.length === 0" class="empty-state--fill">
      <template #icon><Trash2 :size="22" /></template>
      回收站为空
    </EmptyState>
    <div v-for="item in state.trash" :key="item.path" class="trash-row">
      <span class="trash-path" :title="item.path">
        <span class="tree-icon">
          <Folder v-if="item.type === 'dir'" :size="13" />
          <FileText v-else :size="13" />
        </span>
        {{ item.path }}
      </span>
      <IconButton title="恢复" @click="restore(item.path)"><RotateCcw :size="13" /></IconButton>
    </div>
  </div>
</template>
