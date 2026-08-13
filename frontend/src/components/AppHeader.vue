<script setup lang="ts">
import { computed, ref } from 'vue'
import { LayoutTemplate, List, X } from 'lucide-vue-next'
import { closeTab, state } from '../store'
import type { Tab } from '../store'
import IconButton from './IconButton.vue'

const props = defineProps<{ templateActive?: boolean }>()

const emit = defineEmits<{
  (e: 'logout'): void
  (e: 'notify', message: string, kind: 'info' | 'error'): void
  (e: 'select', path: string): void
}>()

const listOpen = ref(false)

const activeTab = computed<Tab | null>(() => state.tabs.find((t) => t.path === state.activePath) ?? null)

function tabClass(tab: Tab): Record<string, boolean> {
  return {
    active: !props.templateActive && state.activePath === tab.path,
    dirty: tab.saveState === 'dirty',
    saving: tab.saveState === 'saving',
    conflict: tab.saveState === 'conflict',
    error: tab.saveState === 'error',
  }
}

function dotClass(tab: Tab): Record<string, boolean> {
  return {
    dot: tab.saveState !== 'saved',
    conflict: tab.saveState === 'conflict',
    error: tab.saveState === 'error',
  }
}

function closeTabSafe(path: string): void {
  const tab = state.tabs.find((t) => t.path === path)
  if (tab && tab.saveState !== 'saved' && !window.confirm(`「${tab.name}」有未保存的修改，关闭后将丢失？`)) {
    return
  }
  closeTab(path)
}

function switchTab(path: string): void {
  state.activePath = path
  listOpen.value = false
  emit('select', path)
}

function onTabKeydown(e: KeyboardEvent, path: string): void {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    switchTab(path)
  }
}
</script>

<template>
  <header class="topbar">
    <!-- 桌面：标签页 -->
    <div class="tabs" role="tablist" aria-label="打开的笔记">
      <div
        v-for="tab in state.tabs"
        :key="tab.path"
        class="tab"
        :class="tabClass(tab)"
        role="tab"
        :aria-selected="!templateActive && state.activePath === tab.path"
        :title="tab.path"
        tabindex="0"
        @click="switchTab(tab.path)"
        @keydown="onTabKeydown($event, tab.path)"
      >
        {{ tab.name }}
        <span v-if="tab.saveState !== 'saved'" class="tab-dot" :class="dotClass(tab)" />
        <span
          class="tab-close"
          role="button"
          :aria-label="`关闭 ${tab.name}`"
          @click.stop="closeTabSafe(tab.path)"
        >
          <X :size="12" />
        </span>
      </div>
    </div>

    <!-- 移动端：当前文稿 + 已打开笔记列表 -->
    <div class="mobile-doc">
      <button class="mobile-doc-title" @click="listOpen = !listOpen">
        <LayoutTemplate v-if="templateActive" :size="15" />
        <List v-else :size="15" />
        <span>{{ templateActive ? '模板中心' : activeTab?.name ?? '拾墨' }}</span>
      </button>
    </div>
    <div v-if="listOpen" class="tablist-sheet">
      <div
        v-for="tab in state.tabs"
        :key="tab.path"
        class="tablist-item"
        :class="{ on: state.activePath === tab.path }"
        @click="switchTab(tab.path)"
      >
        <span class="tab-dot" :class="dotClass(tab)" />
        <span class="tablist-name">{{ tab.name }}</span>
        <IconButton title="关闭" @click.stop="closeTabSafe(tab.path)"><X :size="13" /></IconButton>
      </div>
      <div v-if="state.tabs.length === 0" class="panel-empty">尚未打开笔记</div>
    </div>
  </header>
</template>
