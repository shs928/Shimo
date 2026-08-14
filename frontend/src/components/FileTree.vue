<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Home,
  Link,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from 'lucide-vue-next'
import AppDialog from './AppDialog.vue'
import { api } from '../api'
import {
  basename,
  createDir,
  createFile,
  deletePath,
  openTab,
  refreshTree,
  renameOpenTab,
  state,
} from '../store'
import type { NodeInfo } from '../types'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void; (e: 'navigated'): void }>()

const newName = ref('')
const creating = ref<'file' | 'dir' | null>(null)
/** 当前工作目录（新建/上传的目标位置），空 = 根目录 */
const activeDir = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const treeEl = ref<HTMLElement | null>(null)
const dragging = ref(false)
/** 拖拽中的文件类型，用于投放提示文案 */
const dragKind = ref<'image' | 'doc' | 'other'>('other')
/** 链接导入弹窗 */
const importUrlOpen = ref(false)
const importUrlValue = ref('')
const importUrlBusy = ref(false)
const importUrlError = ref('')

const IMPORT_ACCEPT = '.pdf,.docx,.txt,.csv,.md'

function openImportUrlDialog(): void {
  importUrlValue.value = ''
  importUrlError.value = ''
  importUrlOpen.value = true
}

async function submitImportUrl(): Promise<void> {
  const url = importUrlValue.value.trim()
  if (!url) {
    importUrlError.value = '请输入网页链接'
    return
  }
  importUrlBusy.value = true
  importUrlError.value = ''
  try {
    const res = await api.importUrl(url, activeDir.value)
    importUrlOpen.value = false
    await refreshTree()
    await reloadActiveDir()
    emit('notify', `已导入：${res.title || res.name}`, 'info')
  } catch (e) {
    importUrlError.value = (e as Error).message
  } finally {
    importUrlBusy.value = false
  }
}

/** 扁平化的树行（含深度），供渲染与键盘导航 */
const flat = computed(() => renderTree(state.root))

function isExpanded(path: string): boolean {
  return state.expanded.has(path)
}

function toggle(path: string): void {
  if (state.expanded.has(path)) state.expanded.delete(path)
  else state.expanded.add(path)
}

/** 当前目录面包屑（根 → … → 当前） */
const dirCrumbs = computed(() => {
  const parts = activeDir.value ? activeDir.value.split('/') : []
  let acc = ''
  return parts.map((p) => {
    acc = acc ? `${acc}/${p}` : p
    return { name: p, path: acc }
  })
})

function dirLabel(): string {
  return activeDir.value || '知识库根目录'
}

async function uploadFiles(files: FileList | File[] | null): Promise<void> {
  if (!files || !files.length || uploading.value) return
  uploading.value = true
  let ok = 0
  let skipped = 0
  try {
    for (const file of Array.from(files)) {
      const ext = (file.name.split('.').pop() ?? '').toLowerCase()
      if (!['pdf', 'docx', 'txt', 'csv', 'md'].includes(ext)) {
        skipped++
        emit('notify', `跳过不支持的类型：${file.name}`, 'error')
        continue
      }
      try {
        await api.importFile(file, activeDir.value)
        ok++
        emit('notify', `已导入：${file.name}`, 'info')
      } catch (e) {
        skipped++
        emit('notify', `导入失败：${file.name} — ${(e as Error).message}`, 'error')
      }
    }
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
    await refreshTree()
    await reloadActiveDir()
  }
  if (ok && skipped) emit('notify', `导入完成：成功 ${ok} 个，跳过/失败 ${skipped} 个`, 'info')
}

function onFilePicked(event: Event): void {
  const input = event.target as HTMLInputElement
  void uploadFiles(input.files)
}

function onDragOver(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (!files || !files.length) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
  dragging.value = true
  dragKind.value = Array.from(files).some((f) => f.type.startsWith('image/')) ? 'image' : 'doc'
}

function onDragLeave(): void {
  dragging.value = false
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  dragging.value = false
  void uploadFiles(event.dataTransfer?.files ?? null)
}

async function openDir(path: string): Promise<void> {
  activeDir.value = path
  toggle(path)
  if (isExpanded(path) && !state.treeChildren.has(path)) {
    try {
      const { entries } = await api.tree(path)
      state.treeChildren.set(path, entries)
    } catch (e) {
      emit('notify', (e as Error).message, 'error')
    }
  }
}

function goRoot(): void {
  activeDir.value = ''
}

function childrenOf(dir: NodeInfo | null): NodeInfo[] {
  if (!dir) return state.root
  return state.treeChildren.get(dir.path) ?? []
}

function renderTree(dirs: NodeInfo[]): Array<{ node: NodeInfo; depth: number }> {
  const out: Array<{ node: NodeInfo; depth: number }> = []
  const walk = (items: NodeInfo[], depth: number) => {
    for (const item of items) {
      out.push({ node: item, depth })
      if (item.type === 'dir' && isExpanded(item.path)) {
        walk(childrenOf(item), depth + 1)
      }
    }
  }
  walk(dirs, 0)
  return out
}

function targetBase(): string {
  return activeDir.value ? activeDir.value + '/' : ''
}

async function handleCreate(type: 'file' | 'dir'): Promise<void> {
  let name = newName.value.trim()
  if (!name) return
  // 笔记自动补 .md 扩展名
  if (type === 'file' && !name.toLowerCase().endsWith('.md')) name += '.md'
  const path = targetBase() + name
  try {
    if (type === 'file') {
      await createFile(path, `# ${name.replace(/\.md$/i, '')}\n\n`)
    } else {
      await createDir(path)
    }
    newName.value = ''
    creating.value = null
    await reloadActiveDir()
    emit('notify', '已创建', 'info')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function reloadActiveDir(): Promise<void> {
  if (!activeDir.value) return
  try {
    const { entries } = await api.tree(activeDir.value)
    state.treeChildren.set(activeDir.value, entries)
  } catch {
    /* 目录可能已被删除，忽略 */
  }
}

function onTreeRefresh(): void {
  void refreshTree()
  void reloadActiveDir()
}

onMounted(() => window.addEventListener('tree-refresh', onTreeRefresh))
onBeforeUnmount(() => window.removeEventListener('tree-refresh', onTreeRefresh))

async function openFile(path: string): Promise<void> {
  try {
    await openTab(path)
    emit('navigated')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function onRowClick(node: NodeInfo): Promise<void> {
  if (node.type === 'dir') await openDir(node.path)
  else await openFile(node.path)
}

/** 树键盘导航：↑↓ 移动、→ 展开、← 折叠、Enter 打开 */
function onTreeKeydown(e: KeyboardEvent): void {
  const rows = Array.from(treeEl.value?.querySelectorAll<HTMLElement>('[data-tree-row]') ?? [])
  const idx = rows.indexOf(document.activeElement as HTMLElement)
  const row = flat.value[idx]
  if (!row || idx < 0) return
  const node = row.node
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      rows[Math.min(idx + 1, rows.length - 1)]?.focus()
      break
    case 'ArrowUp':
      e.preventDefault()
      rows[Math.max(idx - 1, 0)]?.focus()
      break
    case 'ArrowRight':
      if (node.type === 'dir' && !isExpanded(node.path)) {
        e.preventDefault()
        void openDir(node.path)
      }
      break
    case 'ArrowLeft':
      if (node.type === 'dir' && isExpanded(node.path)) {
        e.preventDefault()
        toggle(node.path)
      }
      break
    case 'Enter':
    case ' ':
      e.preventDefault()
      void onRowClick(node)
      break
  }
}

async function rename(path: string, name: string): Promise<void> {
  const input = window.prompt('重命名：', name)
  if (!input) return
  const next = input.trim()
  if (!next || next === name) return
  let target = next
  if (path.endsWith('.md') && !target.toLowerCase().endsWith('.md')) target += '.md'
  const dst = path.slice(0, path.length - name.length) + target
  try {
    const plan = await api.movePreview(path, dst)
    if (!plan.valid) {
      emit('notify', plan.message || '目标已存在', 'error')
      return
    }
    // 引用同步更新：预览给出受影响数量，用户确认后执行
    let refactor = false
    if (plan.affected_links > 0) {
      refactor = window.confirm(
        `重命名后将同步更新 ${plan.affected_links} 处引用（${plan.affected_files.length} 个文件）。继续？`,
      )
    }
    await api.move(path, dst, refactor)
    renameOpenTab(path, dst)
    await refreshTree()
    await reloadActiveDir()
    emit('notify', refactor ? `已重命名，并更新 ${plan.affected_links} 处引用` : '已重命名', 'info')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function remove(path: string): Promise<void> {
  if (!window.confirm(`移入回收站？\n${path}`)) return
  try {
    await deletePath(path)
    if (activeDir.value.startsWith(path + '/') || activeDir.value === path) activeDir.value = ''
    await reloadActiveDir()
    emit('notify', '已移入回收站', 'info')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}
</script>

<template>
  <div
    ref="treeEl"
    class="file-tree"
    :class="{ dragover: dragging }"
    role="tree"
    aria-label="知识库文件树"
    @drop="onDrop"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @keydown="onTreeKeydown"
  >
    <!-- 当前目录面包屑 + 返回根目录 -->
    <div class="tree-pathbar">
      <button class="tree-pathseg" title="返回根目录" @click="goRoot">
        <Home :size="12" /> 根目录
      </button>
      <template v-for="c in dirCrumbs" :key="c.path">
        <span class="tree-pathsep">/</span>
        <button class="tree-pathseg" :title="c.path" @click="openDir(c.path)">{{ c.name }}</button>
      </template>
    </div>

    <!-- 新建 / 上传目标目录 -->
    <div class="tree-createbar">
      <button class="btn" title="新建笔记" @click="creating = creating === 'file' ? null : 'file'">
        <Plus :size="13" /> 笔记
      </button>
      <button class="btn" title="新建文件夹" @click="creating = creating === 'dir' ? null : 'dir'">
        <FolderPlus :size="13" /> 文件夹
      </button>
      <button
        class="btn"
        title="上传文档（PDF/Word/TXT/CSV/MD）"
        :disabled="uploading"
        @click="fileInput?.click()"
      >
        <Upload :size="13" /> 上传
      </button>
      <button class="btn" title="从网页链接导入（HTML/PDF）" @click="openImportUrlDialog">
        <Link :size="13" /> 链接
      </button>
      <input ref="fileInput" type="file" class="hidden-input" :accept="IMPORT_ACCEPT" multiple @change="onFilePicked" />
      <span class="tree-target" :title="`新建/上传目标：${dirLabel()}`">⇣ {{ dirLabel() }}</span>
    </div>

    <div v-if="creating" class="create-row">
      <input
        v-model="newName"
        :placeholder="creating === 'file' ? '笔记名（自动补 .md）' : '文件夹名'"
        @keyup.enter="handleCreate(creating)"
        @keyup.esc="creating = null"
      />
      <button class="btn btn--primary" @click="handleCreate(creating)">确定</button>
    </div>

    <div v-if="!state.rootLoaded" class="tree-empty">加载中…</div>
    <div v-else-if="state.root.length === 0" class="tree-empty">
      知识库为空，点击上方「笔记」新建
    </div>

    <div class="tree-root">
      <div
        v-for="row in flat"
        :key="row.node.path"
        class="tree-row"
        :class="{ active: row.node.type === 'file' && state.activePath === row.node.path }"
        :style="{ paddingLeft: 8 + row.depth * 14 + 'px' }"
        :data-tree-row="row.node.path"
        role="treeitem"
        :aria-level="row.depth + 1"
        :aria-expanded="row.node.type === 'dir' ? isExpanded(row.node.path) : undefined"
        :aria-selected="row.node.type === 'file' ? state.activePath === row.node.path : undefined"
        tabindex="0"
        @click="onRowClick(row.node)"
      >
        <template v-if="row.node.type === 'dir'">
          <span class="tree-caret" title="展开/折叠">
            <ChevronRight v-if="!isExpanded(row.node.path)" :size="13" />
            <ChevronDown v-else :size="13" />
          </span>
          <span class="tree-icon">
            <FolderOpen v-if="isExpanded(row.node.path)" :size="14" />
            <Folder v-else :size="14" />
          </span>
          <span class="tree-dir">{{ row.node.name }}</span>
          <span class="tree-ops">
            <button class="icon-btn" title="重命名" @click.stop="rename(row.node.path, row.node.name)">
              <Pencil :size="12" />
            </button>
            <button class="icon-btn icon-btn--danger" title="删除文件夹" @click.stop="remove(row.node.path)">
              <Trash2 :size="12" />
            </button>
          </span>
        </template>
        <template v-else>
          <span class="tree-caret" />
          <span class="tree-icon"><FileText :size="14" /></span>
          <button class="tree-file" @click.stop="openFile(row.node.path)">{{ basename(row.node.path) }}</button>
          <span class="tree-ops">
            <button class="icon-btn" title="重命名" @click.stop="rename(row.node.path, row.node.name)">
              <Pencil :size="12" />
            </button>
            <button class="icon-btn icon-btn--danger" title="删除文件" @click.stop="remove(row.node.path)">
              <Trash2 :size="12" />
            </button>
          </span>
        </template>
      </div>
    </div>

    <!-- 拖拽导入反馈 -->
    <div v-if="dragging" class="tree-drop-hint">
      {{ dragKind === 'image' ? '松开以上传图片' : '松开以导入文档' }} → {{ dirLabel() }}
    </div>

    <!-- 链接导入弹窗 -->
    <AppDialog
      :open="importUrlOpen"
      title="从链接导入"
      :description="`目标目录：${dirLabel()}（HTML 存为 Markdown，PDF 走文档解析）`"
      :busy="importUrlBusy"
      @close="importUrlOpen = false"
    >
      <form class="dialog-form" @submit.prevent="submitImportUrl">
        <label>
          <span>网页链接</span>
          <input
            v-model="importUrlValue"
            type="url"
            placeholder="https://example.com/article"
            autocomplete="off"
            :disabled="importUrlBusy"
            autofocus
          />
        </label>
        <p v-if="importUrlError" class="field-error" role="alert">{{ importUrlError }}</p>
      </form>
      <template #footer>
        <button class="btn" :disabled="importUrlBusy" @click="importUrlOpen = false">取消</button>
        <button class="btn btn--primary" :disabled="importUrlBusy" @click="submitImportUrl">
          {{ importUrlBusy ? '导入中…' : '导入' }}
        </button>
      </template>
    </AppDialog>
  </div>
</template>
