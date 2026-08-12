<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { AlertTriangle, Bot, Copy, Feather, FileText, LogOut, RefreshCw, Search, StickyNote, Trash2, X } from 'lucide-vue-next'
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
import AppHeader from './components/AppHeader.vue'
import MobileWorkbenchBar, { type SheetKind } from './components/MobileWorkbenchBar.vue'
import IconButton from './components/IconButton.vue'
import EmptyState from './components/EmptyState.vue'
import DocumentPreview from './components/DocumentPreview.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import { activeTab, openTab, refreshTree, state } from './store'
import { loadWorkspace, saveWorkspace } from './workspace'

const booting = ref(true)
const notifyMsg = ref('')
const notifyKind = ref<'info' | 'error'>('info')

/** 目录索引（文件 / 搜索 / 回收站合并为单一 mode） */
const indexMode = ref<'files' | 'search' | 'trash'>('files')
/** 右侧两个领域：边注（上下文）/ 助手（AI） */
const rightDomain = ref<'marginalia' | 'assistant'>('marginalia')
const marginaliaView = ref<'meta' | 'backlinks' | 'outgoing' | 'graph' | 'history'>('meta')
const assistantView = ref<'ai' | 'agent'>('ai')
const rightView = computed(() =>
  rightDomain.value === 'marginalia' ? marginaliaView.value : assistantView.value,
)
/** 桌面左侧是否展开 */
const leftOpen = ref(true)
/** 移动端 sheet：同一时刻只允许一个 */
const mobileSheet = ref<SheetKind>('none')

const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)
const mobileMq = window.matchMedia('(max-width: 768px)')

const currentTab = computed(() => activeTab())

/** 索引失败项（非阻塞警告条） */
interface HealthFailure {
  path: string
  subsystem: string
  error: string
  attempts: number
  updated_at: string
}
const healthFailures = ref<HealthFailure[]>([])
const healthBusy = ref(false)

async function loadHealth(): Promise<void> {
  try {
    const s = await api.indexStats()
    healthFailures.value = s.failures ?? []
  } catch {
    /* 诊断信息不可用不阻断 */
  }
}

async function retryHealth(): Promise<void> {
  if (healthBusy.value) return
  healthBusy.value = true
  try {
    const r = await api.indexRetryFailed()
    notify(`索引重试完成：成功 ${r.cleared}，仍失败 ${r.still_failed}`, r.still_failed ? 'error' : 'info')
    await loadHealth()
  } catch (e) {
    notify(e as Error)
  } finally {
    healthBusy.value = false
  }
}

function notify(message: unknown, kind: 'info' | 'error' = 'error'): void {
  notifyMsg.value = String(message)
  notifyKind.value = kind
  window.setTimeout(() => (notifyMsg.value = ''), 5000)
}

async function boot(): Promise<void> {
  try {
    const status = await api.status()
    state.initialized = status.initialized
    state.authenticated = status.authenticated
    if (status.authenticated) {
      await refreshTree()
      await loadHealth()
    }
  } catch (e) {
    notify(e as Error)
  } finally {
    booting.value = false
  }
}

async function onAuthed(): Promise<void> {
  state.authenticated = true
  try {
    await refreshTree()
    await loadHealth()
  } catch (e) {
    notify(e as Error)
  }
  restoreWorkspace()
  void connectEvents()
}

/** 工作区恢复：重新打开上次的标签与面板状态（不恢复未提交正文）。 */
async function restoreWorkspace(): Promise<void> {
  const ws = loadWorkspace()
  if (!ws) return
  // 逐个恢复标签；失效路径安全跳过
  for (const path of ws.tabs) {
    try {
      await openTab(path)
    } catch {
      /* 文件已不存在：跳过 */
    }
  }
  // 恢复面板状态
  leftOpen.value = ws.leftOpen
  if (['files', 'search', 'trash'].includes(ws.indexMode)) indexMode.value = ws.indexMode
  if (['marginalia', 'assistant'].includes(ws.rightDomain)) rightDomain.value = ws.rightDomain
  marginaliaView.value = ws.marginaliaView
  assistantView.value = ws.assistantView
  for (const p of ws.expanded) state.expanded.add(p)
  // 恢复活动路径（失效则回退到第一个标签）
  if (ws.activePath && state.tabs.some((t) => t.path === ws.activePath)) {
    state.activePath = ws.activePath
  } else if (state.tabs.length) {
    state.activePath = state.tabs[0].path
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
  disconnectEvents()
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
    case 'saved':
      return '已保存'
    case 'dirty':
      return '有修改'
    case 'saving':
      return '保存中…'
    case 'error':
      return '保存失败'
    case 'conflict':
      return '冲突'
  }
})

/** 关闭所有移动端 sheet（焦点由 MobileWorkbenchBar 归还触发按钮） */
function closeSheets(): void {
  mobileSheet.value = 'none'
}

/* ---------- 实时事件（外部文件变化） ---------- */

let eventsController: AbortController | null = null

function handleEvent(payload: Record<string, unknown>): void {
  if (payload.type === 'tree_changed') {
    void refreshTree().catch(() => undefined)
  } else if (payload.type === 'file_changed') {
    const path = String(payload.path ?? '')
    const tab = state.tabs.find((t) => t.path === path)
    if (!tab || state.activePath !== path) return
    if (tab.saveState !== 'saved') {
      // dirty：进入冲突状态，不自动覆盖
      tab.saveState = 'conflict'
      notify(`「${tab.name}」已被外部修改，且当前有未保存内容，请处理冲突`, 'error')
      return
    }
    // clean：自动重载并提示
    void openTab(path)
      .then(() => notify(`文件已在外部更新：${path}`, 'info'))
      .catch(() => undefined)
  }
}

async function connectEvents(): Promise<void> {
  eventsController?.abort()
  eventsController = new AbortController()
  try {
    const resp = await api.events(eventsController.signal)
    if (!resp.ok || !resp.body) return
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    void (async () => {
      try {
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
          const events = buffer.split('\n\n')
          buffer = events.pop() ?? ''
          for (const ev of events) {
            const line = ev.trim()
            if (!line.startsWith('data:')) continue
            try {
              handleEvent(JSON.parse(line.slice(5).trim()))
            } catch {
              /* 忽略不完整帧 */
            }
          }
        }
      } catch {
        /* 断开由登录态控制重连 */
      }
    })()
  } catch {
    /* SSE 不可用时静默降级 */
  }
}

function disconnectEvents(): void {
  eventsController?.abort()
  eventsController = null
}

function onMobileOpen(which: 'index' | 'marginalia' | 'assistant'): void {
  if (which === 'index') {
    mobileSheet.value = 'index'
  } else {
    rightDomain.value = which
    mobileSheet.value = which
  }
}

function switchDomain(domain: 'marginalia' | 'assistant'): void {
  rightDomain.value = domain
  // 移动端在 sheet 内切换领域时保持 sheet 打开
  if (mobileSheet.value === 'marginalia' || mobileSheet.value === 'assistant') {
    mobileSheet.value = domain
  }
}

/** 文稿头：点击路径复制 */
async function copyPath(): Promise<void> {
  if (!currentTab.value) return
  try {
    await navigator.clipboard.writeText(currentTab.value.path)
    notify('路径已复制', 'info')
  } catch {
    notify('复制失败', 'error')
  }
}

function onMobileMqChange(e: MediaQueryListEvent): void {
  isMobile.value = e.matches
  if (!e.matches) mobileSheet.value = 'none'
}

onMounted(() => {
  void boot()
  window.addEventListener('beforeunload', onBeforeUnload)
  mobileMq.addEventListener('change', onMobileMqChange)
  // 工作区自动保存（防抖；只存路径元数据，不存未提交正文）
  watch(
    () => [
      state.tabs.map((t) => t.path),
      state.activePath,
      leftOpen.value,
      indexMode.value,
      rightDomain.value,
      marginaliaView.value,
      assistantView.value,
      Array.from(state.expanded),
    ],
    () => {
      saveWorkspace({
        tabs: state.tabs.map((t) => t.path),
        activePath: state.activePath,
        leftOpen: leftOpen.value,
        indexMode: indexMode.value,
        rightDomain: rightDomain.value,
        marginaliaView: marginaliaView.value,
        assistantView: assistantView.value,
        expanded: state.expanded,
      })
    },
  )
  window.addEventListener('auth-expired', () => {
    disconnectEvents()
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
  mobileMq.removeEventListener('change', onMobileMqChange)
  disconnectEvents()
})
</script>

<template>
  <div v-if="booting" class="boot">正在启动…</div>

  <LoginView v-else-if="!state.authenticated" :initialized="state.initialized" @done="onAuthed" />

  <div v-else class="app">
    <AppHeader @notify="notify" />

    <div class="main">
      <!-- 墨脊：品牌 + 导航（唯一记忆点） -->
      <nav class="spine" aria-label="主导航">
        <span class="spine-brand" title="拾墨 · 收集知识碎片">拾墨</span>
        <button
          class="spine-btn"
          :class="{ on: leftOpen }"
          title="目录索引"
          @click="leftOpen = !leftOpen"
        >
          <FileText :size="17" />
        </button>
        <button
          class="spine-btn"
          :class="{ on: rightDomain === 'marginalia' }"
          title="边注"
          @click="switchDomain('marginalia')"
        >
          <StickyNote :size="17" />
        </button>
        <button
          class="spine-btn"
          :class="{ on: rightDomain === 'assistant' }"
          title="助手"
          @click="switchDomain('assistant')"
        >
          <Bot :size="17" />
        </button>
        <div class="spine-spacer" />
        <button class="spine-btn" title="退出登录" @click="onLogout">
          <LogOut :size="16" />
        </button>
      </nav>

      <!-- 目录索引（左侧面板，移动端为左抽屉） -->
      <aside
        class="sidebar"
        :class="{ open: mobileSheet === 'index' }"
        v-show="leftOpen || isMobile"
      >
        <div class="sidebar-head">
          <span class="sidebar-title">目录索引</span>
          <IconButton class="sidebar-close" title="关闭" @click="closeSheets"><X :size="14" /></IconButton>
        </div>
        <div class="index-tabs" role="tablist" aria-label="目录索引">
          <button
            class="index-tab"
            :class="{ on: indexMode === 'files' }"
            @click="indexMode = 'files'"
          >
            <FileText :size="12" /> 文件
          </button>
          <button
            class="index-tab"
            :class="{ on: indexMode === 'search' }"
            @click="indexMode = 'search'"
          >
            <Search :size="12" /> 搜索
          </button>
          <button
            class="index-tab"
            :class="{ on: indexMode === 'trash' }"
            @click="indexMode = 'trash'"
          >
            <Trash2 :size="12" /> 回收站
          </button>
        </div>
        <!-- 索引失败警告条（非阻塞） -->
        <div v-if="healthFailures.length" class="health-bar">
          <div class="health-bar-head">
            <AlertTriangle :size="12" />
            <span>索引失败 {{ healthFailures.length }} 项</span>
            <button class="health-retry" :disabled="healthBusy" @click="retryHealth">
              <RefreshCw :size="11" :class="{ spin: healthBusy }" /> 重试
            </button>
          </div>
          <div class="health-bar-items">
            <div v-for="(f, i) in healthFailures.slice(0, 3)" :key="i" class="health-bar-item" :title="f.error">
              {{ f.path }}
            </div>
          </div>
        </div>
        <div class="sidebar-scroll">
          <FileTree v-if="indexMode === 'files'" @notify="notify" @navigated="closeSheets" />
          <SearchPanel v-else-if="indexMode === 'search'" @notify="notify" />
          <TrashPanel v-else @notify="notify" />
        </div>
        <div v-if="indexMode === 'files' && currentTab && currentTab.kind === 'md'" class="sidebar-outline">
          <OutlinePanel :tab-path="currentTab.path" />
        </div>
      </aside>

      <div v-if="mobileSheet === 'index'" class="scrim" @click="closeSheets" />

      <!-- 中央文稿区 -->
      <section class="content">
        <template v-if="currentTab">
          <div class="doc-header">
            <h1 class="doc-title">{{ currentTab.name }}</h1>
            <button class="doc-path" :title="`${currentTab.path}（点击复制）`" @click="copyPath">
              <Copy :size="11" /> {{ currentTab.path }}
            </button>
            <span v-if="currentTab.kind === 'md'" class="save-state" :class="currentTab.saveState">{{ saveStateLabel }}</span>
            <span v-else class="save-state saved">只读</span>
          </div>
          <EditorView v-if="currentTab.kind === 'md'" :key="currentTab.path" :tab-path="currentTab.path" @notify="notify" />
          <DocumentPreview v-else :key="currentTab.path" :tab-path="currentTab.path" />
        </template>
        <EmptyState v-else>
          <template #icon><Feather :size="28" /></template>
          从左侧选择或新建一篇 Markdown 笔记
        </EmptyState>
      </section>

      <!-- 右侧面板：边注 / 助手（移动端为右抽屉） -->
      <aside
        class="right-panel"
        :class="{
          open: mobileSheet === 'marginalia' || mobileSheet === 'assistant',
          'right-panel--full': isMobile && rightDomain === 'marginalia' && marginaliaView === 'graph',
        }"
      >
        <div class="right-head">
          <div class="domain-tabs">
            <button
              class="domain-tab"
              :class="{ on: rightDomain === 'marginalia' }"
              @click="switchDomain('marginalia')"
            >
              <StickyNote :size="12" /> 边注
            </button>
            <button
              class="domain-tab"
              :class="{ on: rightDomain === 'assistant' }"
              @click="switchDomain('assistant')"
            >
              <Bot :size="12" /> 助手
            </button>
          </div>
          <div class="page-tabs" v-if="rightDomain === 'marginalia'">
            <button
              v-for="t in [
                { id: 'meta', label: '属性' },
                { id: 'backlinks', label: '反链' },
                { id: 'outgoing', label: '链接' },
                { id: 'graph', label: '图谱' },
                { id: 'history', label: '历史' },
              ] as const"
              :key="t.id"
              class="page-tab"
              :class="{ on: marginaliaView === t.id }"
              @click="marginaliaView = t.id"
            >
              {{ t.label }}
            </button>
          </div>
          <div class="page-tabs" v-else>
            <button class="page-tab" :class="{ on: assistantView === 'ai' }" @click="assistantView = 'ai'">
              AI 问答
            </button>
            <button class="page-tab" :class="{ on: assistantView === 'agent' }" @click="assistantView = 'agent'">
              Agent
            </button>
          </div>
        </div>

        <div class="right-body">
          <template v-if="rightDomain === 'marginalia'">
            <EmptyState v-if="!currentTab" class="empty-state--fill">
              <template #icon><StickyNote :size="24" /></template>
              打开一篇笔记以查看边注
            </EmptyState>
            <EmptyState v-else-if="currentTab.kind === 'doc'" class="empty-state--fill">
              <template #icon><StickyNote :size="24" /></template>
              文档预览不支持边注，请打开 Markdown 笔记
            </EmptyState>
            <MetaPanel v-else-if="marginaliaView === 'meta'" :tab-path="currentTab.path" />
            <BacklinksPanel v-else-if="marginaliaView === 'backlinks'" :tab-path="currentTab.path" @notify="notify" />
            <OutgoingPanel v-else-if="marginaliaView === 'outgoing'" :tab-path="currentTab.path" @notify="notify" />
            <LocalGraphPanel v-else-if="marginaliaView === 'graph'" :tab-path="currentTab.path" @notify="notify" />
            <HistoryPanel v-else :tab-path="currentTab.path" @notify="notify" />
          </template>
          <template v-else>
            <AiPanel v-if="assistantView === 'ai'" @notify="notify" />
            <AgentChat v-else @notify="notify" />
          </template>
        </div>
      </aside>

      <div v-if="mobileSheet === 'marginalia' || mobileSheet === 'assistant'" class="scrim" @click="closeSheets" />
    </div>

    <MobileWorkbenchBar v-if="isMobile" :sheet="mobileSheet" @open="onMobileOpen" @close="closeSheets" />

    <div v-if="notifyMsg" class="toast" :class="`toast--${notifyKind}`">{{ notifyMsg }}</div>
  </div>
</template>
