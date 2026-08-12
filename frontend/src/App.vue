<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Files, Search, Trash2, LogOut, X, Menu, Bot } from 'lucide-vue-next'
import { api } from './api'
import LoginView from './components/LoginView.vue'
import FileTree from './components/FileTree.vue'
import EditorView from './components/EditorView.vue'
import TrashPanel from './components/TrashPanel.vue'
import OutlinePanel from './components/OutlinePanel.vue'
import MetaPanel from './components/MetaPanel.vue'
import SearchPanel from './components/SearchPanel.vue'
import BacklinksPanel from './components/BacklinksPanel.vue'
import OutgoingPanel from './components/OutgoingPanel.vue'
import LocalGraphPanel from './components/LocalGraphPanel.vue'
import AiPanel from './components/AiPanel.vue'
import AgentChat from './components/AgentChat.vue'
import { activeTab, closeTab, openTab, refreshTree, state } from './store'

const booting = ref(true)
const notifyMsg = ref('')
const notifyKind = ref<'info' | 'error'>('info')
const leftView = ref<'files' | 'search'>('files')
const rightView = ref<'meta' | 'backlinks' | 'outgoing' | 'graph' | 'ai' | 'agent'>('meta')
const mobileNav = ref(false)
const mobileRight = ref(false)

/** 当前活动标签（响应式） */
const currentTab = computed(() => state.tabs.find((t) => t.path === state.activePath) ?? null)

async function boot(): Promise<void> {
  try {
    const status = await api.status()
    state.initialized = status.initialized
    state.authenticated = status.authenticated
    if (status.authenticated) {
      await refreshTree()
    }
  } catch (e) {
    notify(e as Error)
  } finally {
    booting.value = false
  }
}

function notify(message: unknown, kind: 'info' | 'error' = 'error'): void {
  notifyMsg.value = String(message)
  notifyKind.value = kind
  window.setTimeout(() => (notifyMsg.value = ''), 5000)
}

async function onAuthed(): Promise<void> {
  state.authenticated = true
  try {
    await refreshTree()
  } catch (e) {
    notify(e as Error)
  }
}

function clearWorkspace(): void {
  state.tabs = []
  state.activePath = ''
  state.root = []
  state.rootLoaded = false
  state.treeChildren.clear()
  state.trash = []
  state.showTrash = false
}

function onLogout(): void {
  void api.logout().then(() => {
    state.authenticated = false
    clearWorkspace()
  })
}

function onBeforeUnload(e: BeforeUnloadEvent): void {
  if (state.tabs.some((t) => t.saveState !== 'saved')) {
    e.preventDefault()
    e.returnValue = ''
  }
}

const saveStateLabel = computed(() => {
  const tab = activeTab()
  if (!tab) return ''
  switch (tab.saveState) {
    case 'saved': return '已保存'
    case 'dirty': return '有修改'
    case 'saving': return '保存中…'
    case 'error': return '保存失败'
    case 'conflict': return '冲突'
  }
})

function switchLeft(view: 'files' | 'search'): void {
  leftView.value = view
  mobileNav.value = true
}

function closeAllDrawers(): void {
  mobileNav.value = false
  mobileRight.value = false
}

function closeTabSafe(path: string): void {
  const tab = state.tabs.find((t) => t.path === path)
  if (tab && tab.saveState !== 'saved' && !window.confirm(`「${tab.name}」有未保存的修改，关闭后将丢失？`)) {
    return
  }
  closeTab(path)
}

onMounted(() => {
  void boot()
  window.addEventListener('beforeunload', onBeforeUnload)
  window.addEventListener('auth-expired', () => {
    state.authenticated = false
    clearWorkspace()
  })
  window.addEventListener('open-note', (e) => {
    const rel = (e as CustomEvent<string>).detail
    void openTab(rel).catch((err) => notify(err as Error))
  })
  window.addEventListener('wiki-open', (e) => {
    const { target, anchor, dir } = (e as CustomEvent<{ target: string; anchor: string; dir: string }>).detail
    void api
      .resolveWiki(target, dir)
      .then((res) => {
        if (!res.path) {
          notify(`未找到笔记：${target}`, 'error')
          return
        }
        return openTab(res.path).then(() => {
          if (anchor) {
            window.setTimeout(
              () => window.dispatchEvent(new CustomEvent('outline-jump', { detail: anchor })),
              200,
            )
          }
        })
      })
      .catch((err) => notify(err as Error))
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div v-if="booting" class="boot">正在启动…</div>

  <LoginView v-else-if="!state.authenticated" :initialized="state.initialized" @done="onAuthed" />

  <div v-else class="app">
    <header class="topbar">
      <div class="tabs">
        <button
          v-for="tab in state.tabs"
          :key="tab.path"
          class="tab"
          :class="{ active: state.activePath === tab.path, dirty: tab.saveState !== 'saved' }"
          :title="tab.path"
          @click="state.activePath = tab.path"
        >
          {{ tab.name }}
          <span v-if="tab.saveState !== 'saved'" class="tab-dot dot" />
          <span class="tab-close" @click.stop="closeTabSafe(tab.path)">×</span>
        </button>
      </div>
      <div class="top-actions">
        <button class="icon-btn" title="退出登录" @click="onLogout"><LogOut :size="16" /></button>
      </div>
    </header>

    <div class="main">
      <!-- 活动栏 -->
      <nav class="activity-rail">
        <button
          class="rail-btn"
          :class="{ on: leftView === 'files' }"
          title="文件"
          @click="switchLeft('files')"
        >
          <Files :size="18" />
        </button>
        <button
          class="rail-btn"
          :class="{ on: leftView === 'search' }"
          title="搜索"
          @click="switchLeft('search')"
        >
          <Search :size="18" />
        </button>
        <button class="rail-btn" title="回收站" @click="state.showTrash = !state.showTrash">
          <Trash2 :size="18" />
        </button>
        <div class="rail-spacer" />
        <button class="rail-btn rail-mobile-close" title="关闭面板" @click="closeAllDrawers">
          <Menu :size="18" />
        </button>
      </nav>

      <!-- 左侧面板（移动端左抽屉） -->
      <aside class="sidebar" :class="{ open: mobileNav }">
        <div class="sidebar-head">
          <span class="sidebar-title">{{ leftView === 'files' ? '文件' : '搜索' }}</span>
          <button class="icon-btn sidebar-close" title="关闭" @click="mobileNav = false"><X :size="14" /></button>
        </div>
        <div class="sidebar-scroll">
          <template v-if="!state.showTrash">
            <FileTree v-if="leftView === 'files'" @notify="notify" @navigated="closeAllDrawers" />
            <SearchPanel v-else @notify="notify" />
          </template>
          <TrashPanel v-else @notify="notify" />
        </div>
        <div v-if="!state.showTrash && leftView === 'files' && currentTab" class="sidebar-outline">
          <OutlinePanel :tab-path="currentTab.path" />
        </div>
      </aside>

      <div v-if="mobileNav" class="scrim" @click="closeAllDrawers" />

      <!-- 中央编辑区 -->
      <section class="content">
        <template v-if="currentTab">
          <div class="breadcrumb">
            {{ currentTab.path }}
            <span class="save-state" :class="currentTab.saveState">{{ saveStateLabel }}</span>
          </div>
          <EditorView :key="currentTab.path" :tab-path="currentTab.path" @notify="notify" />
        </template>
        <div v-else class="empty-state">
          从左侧选择或新建一个 Markdown 笔记开始
        </div>
      </section>

      <!-- 右侧上下文面板（移动端右抽屉） -->
      <aside v-if="currentTab" class="context-sidebar" :class="{ open: mobileRight }">
        <div class="context-tabs">
          <button :class="{ on: rightView === 'meta' }" @click="rightView = 'meta'">属性</button>
          <button :class="{ on: rightView === 'backlinks' }" @click="rightView = 'backlinks'">反链</button>
          <button :class="{ on: rightView === 'outgoing' }" @click="rightView = 'outgoing'">链接</button>
          <button :class="{ on: rightView === 'graph' }" @click="rightView = 'graph'">图谱</button>
          <button :class="{ on: rightView === 'ai' }" class="ai-tab" title="AI 问答" @click="rightView = 'ai'">
            <Bot :size="13" /> AI
          </button>
          <button :class="{ on: rightView === 'agent' }" class="ai-tab" title="AI Agent" @click="rightView = 'agent'">
            <Bot :size="13" /> Agent
          </button>
          <button class="icon-btn context-close" title="关闭" @click="mobileRight = false"><X :size="14" /></button>
        </div>
        <div class="context-body">
          <MetaPanel v-if="rightView === 'meta'" :tab-path="currentTab.path" />
          <BacklinksPanel v-else-if="rightView === 'backlinks'" :tab-path="currentTab.path" @notify="notify" />
          <OutgoingPanel v-else-if="rightView === 'outgoing'" :tab-path="currentTab.path" @notify="notify" />
          <LocalGraphPanel v-else-if="rightView === 'graph'" :tab-path="currentTab.path" @notify="notify" />
          <AiPanel v-else-if="rightView === 'ai'" @notify="notify" />
          <AgentChat v-else @notify="notify" />
        </div>
      </aside>

      <div v-if="mobileRight" class="scrim" @click="closeAllDrawers" />
    </div>

    <div v-if="notifyMsg" class="toast" :class="notifyKind">{{ notifyMsg }}</div>
  </div>
</template>
