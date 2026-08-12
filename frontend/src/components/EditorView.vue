<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, keymap, lineNumbers, placeholder } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { api } from '../api'
import { activeTab, state } from '../store'
import { renderAfter, renderMarkdown } from '../md'
import AiActionMenu from './AiActionMenu.vue'

const props = defineProps<{ tabPath: string }>()
const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

const host = ref<HTMLElement | null>(null)
const previewEl = ref<HTMLElement | null>(null)
const wrapEl = ref<HTMLElement | null>(null)
const mode = ref<'edit' | 'preview' | 'split'>('edit')

/** 右键 AI 操作菜单状态（选区 + 屏幕坐标） */
const actionMenu = ref<{ x: number; y: number; from: number; to: number; text: string } | null>(null)

/** 拖拽投放反馈 */
const dragging = ref(false)
const dragKind = ref<'image' | 'doc'>('doc')

let view: EditorView | null = null
let saveTimer: number | undefined
let renderTimer: number | undefined
let saveInFlight = false
let savePending = false
let resizeObserver: ResizeObserver | null = null

/** 响应式 isMobile（matchMedia，非一次性取值） */
const mobileMq = window.matchMedia('(max-width: 768px)')
const isMobile = ref(mobileMq.matches)
/** 中央区域过窄时禁用分屏 */
const centerNarrow = ref(false)
/** 减少动态偏好 */
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
/** 主题变化时重渲染预览（Mermaid 明暗适配） */
const darkMq = window.matchMedia('(prefers-color-scheme: dark)')

function pickMode(next: 'edit' | 'preview' | 'split'): void {
  // 移动端 / 中央过窄不支持分屏，回退为阅读模式
  if ((isMobile.value || centerNarrow.value) && next === 'split') next = 'preview'
  mode.value = next
}

const editorTheme = EditorView.theme({
  '&': { height: '100%', fontSize: '13.5px', backgroundColor: 'transparent', color: 'var(--color-text)' },
  '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.7' },
  '.cm-gutters': {
    backgroundColor: 'var(--color-raised)',
    color: 'var(--color-text-faint)',
    borderRight: '1px solid var(--color-rule)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--color-raised)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--color-raised)', color: 'var(--color-accent)' },
  '.cm-selectionBackground': { backgroundColor: 'var(--color-selection) !important' },
  '.cm-cursor': { borderLeftColor: 'var(--color-accent)' },
  '.cm-placeholder': { color: 'var(--color-text-faint)' },
  '&.cm-focused': { outline: 'none' },
})

/** 粘贴：只有图片才接管（同步返回，普通文本不受影响） */
function pasteUpload(event: ClipboardEvent): boolean {
  const files = Array.from(event.clipboardData?.files ?? [])
  const image = files.find((f) => f.type.startsWith('image/'))
  if (!image || !view) return false
  event.preventDefault()
  void uploadAndInsert(image)
  return true
}

/** 拖拽：图片 → 插入编辑器；文档 → 导入到当前笔记所在目录 */
function onEditorDragOver(event: DragEvent): boolean {
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (!files.length || !view) return false
  event.preventDefault()
  dragging.value = true
  dragKind.value = files.some((f) => f.type.startsWith('image/')) ? 'image' : 'doc'
  return true
}

function onEditorDragLeave(): void {
  dragging.value = false
}

function onEditorDrop(event: DragEvent): boolean {
  dragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (!files.length || !view) return false
  event.preventDefault()
  const image = files.find((f) => f.type.startsWith('image/'))
  if (image) {
    void uploadAndInsert(image)
    return true
  }
  const doc = files.find((f) => /\.(pdf|docx|txt|csv|md)$/i.test(f.name))
  if (doc) void importDocument(doc)
  return true
}

async function importDocument(file: File): Promise<void> {
  emit('notify', '正在导入文档…', 'info')
  try {
    const tab = activeTab()
    const dir = tab ? tab.path.split('/').slice(0, -1).join('/') : ''
    const r = await api.importFile(file, dir)
    emit('notify', `已导入：${r.name}`, 'info')
    window.dispatchEvent(new CustomEvent('tree-refresh'))
  } catch (e) {
    emit('notify', `导入失败：${(e as Error).message}`, 'error')
  }
}

/** 右键：选中非空文本时弹出 AI 操作菜单 */
function onContextMenu(event: MouseEvent, ev: EditorView): boolean {
  const sel = ev.state.selection.main
  if (sel.empty) return false
  const text = ev.state.doc.sliceString(sel.from, sel.to).trim()
  if (!text) return false
  event.preventDefault()
  actionMenu.value = { x: event.clientX, y: event.clientY, from: sel.from, to: sel.to, text }
  return true
}

/** 将 AI 操作结果写回编辑器（替换选中或插入到选中后）并触发保存 */
function applyActionResult(result: string, replace: boolean): void {
  const menu = actionMenu.value
  if (!menu || !view) return
  view.dispatch({
    changes: replace
      ? { from: menu.from, to: menu.to, insert: result }
      : { from: menu.to, insert: `\n${result}` },
  })
  scheduleSave()
}

async function uploadAndInsert(image: File): Promise<void> {
  emit('notify', '正在上传图片…', 'info')
  try {
    const { relative_path: rel } = await api.uploadAttachment(image)
    const insert = `![${image.name}](${rel})\n\n`
    view?.dispatch({ changes: { from: view.state.selection.main.from, insert } })
    scheduleSave()
    emit('notify', '图片已插入', 'info')
  } catch (e) {
    emit('notify', `图片上传失败：${(e as Error).message}`, 'error')
  }
}

function buildState(content: string): EditorState {
  return EditorState.create({
    doc: content,
    extensions: [
      lineNumbers(),
      history(),
      keymap.of([
        { key: 'Mod-s', run: () => { void runSave(); return true } },
        ...defaultKeymap,
        ...historyKeymap,
        indentWithTab,
      ]),
      markdown({ base: markdownLanguage }),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      placeholder('开始书写…'),
      editorTheme,
      EditorView.domEventHandlers({
        paste: pasteUpload,
        drop: onEditorDrop,
        dragover: onEditorDragOver,
        dragleave: onEditorDragLeave,
        contextmenu: onContextMenu,
      }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          // 实时同步到 tab.content，保证预览与未保存内容一致
          const tab = activeTab()
          if (tab) {
            tab.content = update.view.state.doc.toString()
            tab.saveState = tab.content === tab.savedContent ? 'saved' : 'dirty'
          }
          scheduleSave()
        }
      }),
    ],
  })
}

function mountEditor(): void {
  if (!host.value || view) return
  const tab = activeTab()
  if (!tab) return
  view = new EditorView({ state: buildState(tab.content), parent: host.value })
}

function unmountEditor(): void {
  view?.destroy()
  view = null
}

/** 将编辑器当前内容同步回 tab（切换/销毁前调用，避免静默丢失） */
function syncContent(): void {
  const tab = activeTab()
  if (tab && view) {
    tab.content = view.state.doc.toString()
    tab.saveState = tab.content === tab.savedContent ? 'saved' : 'dirty'
  }
}

function scheduleSave(): void {
  const tab = activeTab()
  if (!tab) return
  tab.saveState = tab.content === tab.savedContent ? 'saved' : 'dirty'
  if (tab.saveState === 'saved') return
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => void runSave(), 1200)
}

/** 串行化保存：保存进行中再有修改时标记 pending，完成后自动补一次 */
async function runSave(): Promise<void> {
  if (saveInFlight) {
    savePending = true
    return
  }
  saveInFlight = true
  try {
    await doSave()
  } finally {
    saveInFlight = false
    if (savePending) {
      savePending = false
      saveTimer = window.setTimeout(() => void runSave(), 300)
    }
  }
}

async function doSave(): Promise<void> {
  const tab = activeTab()
  if (!tab) return
  syncContent()
  if (tab.content === tab.savedContent && tab.saveState !== 'error') {
    tab.saveState = 'saved'
    return
  }
  tab.saveState = 'saving'
  try {
    const saved = await api.saveFile(tab.path, tab.content, tab.etag)
    tab.content = saved.content
    tab.savedContent = saved.content
    tab.etag = saved.etag
    tab.saveState = 'saved'
    if (saved.index_warning) {
      emit('notify', `已保存，但${saved.index_warning}`, 'error')
    }
    window.dispatchEvent(new CustomEvent('tab-saved', { detail: tab.path }))
  } catch (e) {
    const err = e as Error & { status?: number }
    tab.saveState = err.status === 412 ? 'conflict' : 'error'
    tab.error = err.message
    emit('notify', `保存失败：${err.message}`, 'error')
  }
}

const rendered = computed(() => {
  const tab = activeTab()
  return tab ? renderMarkdown(tab.content, tab.path).html : ''
})

/** 预览渲染：写 HTML 后异步补全 KaTeX/Mermaid */
async function updatePreview(): Promise<void> {
  if (!previewEl.value) return
  previewEl.value.innerHTML = rendered.value
  window.clearTimeout(renderTimer)
  renderTimer = window.setTimeout(() => void renderAfter(previewEl.value as HTMLElement), 30)
}

function onOutlineJump(e: Event): void {
  const slug = (e as CustomEvent<string>).detail
  if (mode.value === 'edit') mode.value = 'split'
  window.setTimeout(() => {
    document
      .getElementById(slug)
      ?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
  }, 60)
}

watch(mode, () => {
  if (mode.value === 'preview') {
    syncContent()
    unmountEditor()
  } else if (mode.value === 'edit' || mode.value === 'split') {
    requestAnimationFrame(() => {
      unmountEditor()
      mountEditor()
    })
  }
})

watch(rendered, () => void updatePreview())

function onMobileMqChange(e: MediaQueryListEvent): void {
  isMobile.value = e.matches
  if (e.matches && mode.value === 'split') mode.value = 'preview'
}

function onDarkMqChange(): void {
  void updatePreview()
}

onMounted(() => {
  mountEditor()
  void updatePreview()
  window.addEventListener('outline-jump', onOutlineJump)
  mobileMq.addEventListener('change', onMobileMqChange)
  darkMq.addEventListener('change', onDarkMqChange)
  // 中央宽度不足时禁用分屏
  resizeObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      centerNarrow.value = entry.contentRect.width < 760
    }
  })
  if (wrapEl.value) resizeObserver.observe(wrapEl.value)
})

onBeforeUnmount(() => {
  window.clearTimeout(saveTimer)
  // 有未保存修改时，卸载前同步内容并立即触发保存（fire-and-forget）
  if (activeTab()?.saveState !== 'saved') {
    syncContent()
    if (activeTab()?.saveState === 'dirty') void runSave()
  }
  window.removeEventListener('outline-jump', onOutlineJump)
  mobileMq.removeEventListener('change', onMobileMqChange)
  darkMq.removeEventListener('change', onDarkMqChange)
  window.clearTimeout(renderTimer)
  resizeObserver?.disconnect()
  unmountEditor()
})
</script>

<template>
  <div ref="wrapEl" class="editor-wrap">
    <div class="editor-toolbar">
      <div class="seg">
        <button :class="{ on: mode === 'edit' }" @click="pickMode('edit')">编辑</button>
        <button :class="{ on: mode === 'preview' }" @click="pickMode('preview')">阅读</button>
        <button
          v-if="!isMobile && !centerNarrow"
          :class="{ on: mode === 'split' }"
          title="分屏（中央区域过窄时不可用）"
          @click="pickMode('split')"
        >
          分屏
        </button>
      </div>
      <span class="toolbar-hint">支持 [[WikiLink]]、$公式$、```mermaid 图表；粘贴图片自动上传</span>
      <button class="btn save-btn" @click="runSave">保存</button>
    </div>
    <div class="editor-body" :class="[mode, { dragover: dragging }]">
      <div v-show="mode === 'edit' || mode === 'split'" ref="host" class="cm-host" />
      <div v-show="mode === 'preview' || mode === 'split'" class="preview">
        <div ref="previewEl" class="preview-inner" />
      </div>
      <div v-if="dragging" class="drop-overlay">
        {{ dragKind === 'image' ? '松开以上传图片' : '松开以导入文档到当前目录' }}
      </div>
    </div>
    <AiActionMenu
      v-if="actionMenu"
      :x="actionMenu.x"
      :y="actionMenu.y"
      :selected-text="actionMenu.text"
      @apply="applyActionResult"
      @close="actionMenu = null"
      @notify="(m, k) => emit('notify', m, k)"
    />
  </div>
</template>
