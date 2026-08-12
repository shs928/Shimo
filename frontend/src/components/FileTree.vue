<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
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
const expanded = ref(new Set<string>())
/** 当前工作目录（新建文件的目标位置），空 = 根目录 */
const activeDir = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const IMPORT_ACCEPT = '.pdf,.docx,.txt,.csv,.md'

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
        const r = await api.importFile(file, activeDir.value)
        ok++
        if (r.path.endsWith('.md') && r.parsed_chars === 0) {
          // 导入 .md 不打开标签，避免覆盖正在编辑的标签
        }
        emit('notify', `已导入：${r.name}`, 'info')
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

function onDrop(event: DragEvent): void {
  event.preventDefault()
  if (uploading.value) return
  void uploadFiles(event.dataTransfer?.files ?? null)
}

function onDragOver(event: DragEvent): void {
  event.preventDefault()
}

function toggle(path: string): void {
  const next = new Set(expanded.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expanded.value = next
}

function isExpanded(path: string): boolean {
  return expanded.value.has(path)
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

function dirLabel(): string {
  return activeDir.value || '知识库根目录'
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
    await api.move(path, dst)
    renameOpenTab(path, dst)
    await refreshTree()
    await reloadActiveDir()
    emit('notify', '已重命名', 'info')
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
  <div class="file-tree" @drop="onDrop" @dragover="onDragOver">
    <div class="tree-header">
      <span class="tree-title" :title="dirLabel()">📁 {{ dirLabel() }}</span>
      <div class="tree-actions">
        <button class="icon-btn" title="上传文档（PDF/Word/TXT/CSV/MD）" :disabled="uploading" @click="fileInput?.click()">⇧</button>
        <input ref="fileInput" type="file" class="hidden-input" :accept="IMPORT_ACCEPT" multiple @change="onFilePicked" />
        <button class="icon-btn" title="新建笔记" @click="creating = creating === 'file' ? null : 'file'">＋</button>
        <button class="icon-btn" title="新建文件夹" @click="creating = creating === 'dir' ? null : 'dir'">📁＋</button>
      </div>
    </div>

    <div v-if="creating" class="create-row">
      <input
        v-model="newName"
        :placeholder="creating === 'file' ? '笔记名（自动补 .md）' : '文件夹名'"
        @keyup.enter="handleCreate(creating)"
        @keyup.esc="creating = null"
      />
      <button @click="handleCreate(creating)">确定</button>
    </div>

    <div v-if="!state.rootLoaded" class="tree-empty">加载中…</div>
    <div v-else-if="state.root.length === 0" class="tree-empty">
      知识库为空，点击上方 ＋ 新建笔记
    </div>

    <div v-for="row in renderTree(state.root)" :key="row.node.path" class="tree-row" :style="{ paddingLeft: 6 + row.depth * 14 + 'px' }">
      <template v-if="row.node.type === 'dir'">
        <span class="tree-caret" @click="openDir(row.node.path)">{{ isExpanded(row.node.path) ? '▾' : '▸' }}</span>
        <span class="tree-dir" @click="openDir(row.node.path)">{{ row.node.name }}</span>
        <span class="tree-del" title="重命名" @click.stop="rename(row.node.path, row.node.name)">✎</span>
        <span class="tree-del" title="删除文件夹" @click.stop="remove(row.node.path)">🗑</span>
      </template>
      <template v-else>
        <span class="tree-caret" />
        <span
          class="tree-file"
          :class="{ active: state.activePath === row.node.path }"
          @click="openFile(row.node.path)"
        >{{ basename(row.node.path) }}</span>
        <span class="tree-del" title="重命名" @click.stop="rename(row.node.path, row.node.name)">✎</span>
        <span class="tree-del" title="删除文件" @click.stop="remove(row.node.path)">🗑</span>
      </template>
    </div>
  </div>
</template>
