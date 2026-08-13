<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  ArrowLeft,
  BookOpen,
  BriefcaseBusiness,
  CalendarDays,
  ClipboardList,
  Clock3,
  Copy,
  Download,
  FilePlus2,
  FileText,
  FlaskConical,
  FolderCog,
  LayoutTemplate,
  ListChecks,
  ListTodo,
  Loader2,
  Microscope,
  NotebookTabs,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Target,
  Trash2,
  Upload,
  UsersRound,
} from 'lucide-vue-next'
import { api } from '../api'
import type { TemplateDetail, TemplateDraft, TemplateSource, TemplateSummary } from '../types'
import AppDialog from './AppDialog.vue'
import DirectoryPicker from './DirectoryPicker.vue'
import EmptyState from './EmptyState.vue'
import MarkdownPreview from './MarkdownPreview.vue'

const props = defineProps<{
  currentNote?: { path: string; name: string; content: string } | null
}>()
const emit = defineEmits<{
  (e: 'notify', message: string, kind: 'info' | 'error'): void
  (e: 'created', path: string): void
  (e: 'edit', path: string): void
  (e: 'moved', oldPath: string, newPath: string): void
  (e: 'deleted', path: string): void
  (e: 'reset-template-tabs'): void
}>()

type DialogKind = 'none' | 'apply' | 'create' | 'metadata' | 'move' | 'copy' | 'delete' | 'categories' | 'import'
type SourceFilter = 'all' | TemplateSource

type IconComponent = typeof LayoutTemplate
const ICONS: Record<string, IconComponent> = {
  calendar: CalendarDays,
  'calendar-days': CalendarDays,
  clipboard: ClipboardList,
  briefcase: BriefcaseBusiness,
  'briefcase-business': BriefcaseBusiness,
  book: BookOpen,
  'book-open': BookOpen,
  tasks: ListTodo,
  'list-checks': ListChecks,
  target: Target,
  research: FlaskConical,
  microscope: Microscope,
  'notebook-tabs': NotebookTabs,
  'rotate-ccw': RotateCcw,
  team: UsersRound,
  users: UsersRound,
  file: FileText,
  'file-text': FileText,
}

const loading = ref(true)
const loadError = ref('')
const templates = ref<TemplateSummary[]>([])
const categories = ref<string[]>([])
const customCategories = ref<string[]>([])
const query = ref('')
const source = ref<SourceFilter>('all')
const category = ref('')
const sortBy = ref<'recommended' | 'title' | 'updated'>('recommended')
const detail = ref<TemplateDetail | null>(null)
const detailLoading = ref(false)
const dialog = ref<DialogKind>('none')
const busy = ref(false)
const dialogError = ref('')
const importInput = ref<HTMLInputElement | null>(null)
const importFiles = ref<File[]>([])
const importStrategy = ref<'skip' | 'rename' | 'overwrite'>('rename')

const draft = reactive<TemplateDraft>({
  name: '',
  title: '',
  description: '',
  category: '',
  tags: [],
  icon: 'file-text',
  content: '# {{title}}\n\n',
})
const tagsText = ref('')
const createThenEdit = ref(false)
const createFromCurrent = ref(false)
const applyName = ref('')
const applyDirectory = ref('')
const moveName = ref('')
const moveCategory = ref('')
const categoryName = ref('')
const categoryRenameFrom = ref('')
const categoryRenameTo = ref('')
const categoryDelete = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLocaleLowerCase('zh-CN')
  const result = templates.value.filter((item) => {
    if (source.value !== 'all' && item.source !== source.value) return false
    if (category.value && item.category !== category.value) return false
    if (!q) return true
    return [item.title, item.description, item.category, ...item.tags]
      .join('\n')
      .toLocaleLowerCase('zh-CN')
      .includes(q)
  })
  if (sortBy.value === 'title') return result.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'))
  if (sortBy.value === 'updated') return result.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  return result.sort((a, b) => Number(b.source === 'builtin') - Number(a.source === 'builtin'))
})

const dialogTitle = computed(() => {
  switch (dialog.value) {
    case 'apply': return '使用模板创建文档'
    case 'create': return createFromCurrent.value ? '保存当前笔记为模板' : '新建自定义模板'
    case 'metadata': return '编辑模板信息'
    case 'move': return '改名或移动模板'
    case 'copy': return '复制为自定义模板'
    case 'delete': return '删除模板'
    case 'categories': return '管理模板分类'
    case 'import': return '导入 Markdown 模板'
    default: return ''
  }
})

function iconFor(name: string): IconComponent {
  return ICONS[name] ?? LayoutTemplate
}

function formatUpdated(value: string): string {
  if (!value) return '内置模板'
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(time)
}

function filenameFromTitle(title: string): string {
  return `${title.trim().replace(/[<>:"/\\|?*]/g, '-').replace(/[. ]+$/g, '') || '新文档'}.md`
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function closeDialog(): void {
  dialog.value = 'none'
  dialogError.value = ''
}

async function loadCatalog(keepSelection = true): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const result = await api.templates()
    templates.value = result.templates
    categories.value = result.categories
    customCategories.value = result.custom_categories ?? Array.from(new Set(
      result.templates.filter((item) => item.source === 'custom' && item.category).map((item) => item.category),
    ))
    if (category.value && !categories.value.includes(category.value)) category.value = ''
    if (keepSelection && detail.value) {
      const exists = templates.value.some((item) => item.id === detail.value?.id)
      if (exists) await openDetail(detail.value.id)
      else detail.value = null
    }
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function openDetail(id: string): Promise<void> {
  detailLoading.value = true
  try {
    detail.value = await api.templateDetail(id)
  } catch (e) {
    emit('notify', `模板加载失败：${(e as Error).message}`, 'error')
  } finally {
    detailLoading.value = false
  }
}

function backToList(): void {
  detail.value = null
}

function resetDraft(): void {
  Object.assign(draft, {
    name: '', title: '', description: '', category: category.value, tags: [], icon: 'file-text', content: '# {{title}}\n\n',
  })
  tagsText.value = ''
  createThenEdit.value = false
  dialogError.value = ''
}

function openCreate(fromCurrent = false): void {
  resetDraft()
  createFromCurrent.value = fromCurrent
  if (fromCurrent && props.currentNote) {
    draft.name = filenameFromTitle(props.currentNote.name.replace(/\.md$/i, ''))
    draft.title = props.currentNote.name.replace(/\.md$/i, '')
    draft.description = `由「${props.currentNote.name}」保存`
    draft.content = props.currentNote.content
  }
  dialog.value = 'create'
}

function openMetadata(): void {
  if (!detail.value || detail.value.source !== 'custom') return
  Object.assign(draft, {
    name: detail.value.path?.split('/').pop() ?? filenameFromTitle(detail.value.title),
    title: detail.value.title,
    description: detail.value.description,
    category: detail.value.category,
    tags: [...detail.value.tags],
    icon: detail.value.icon,
    content: detail.value.content,
  })
  tagsText.value = detail.value.tags.join('，')
  dialogError.value = ''
  dialog.value = 'metadata'
}

function openApply(): void {
  if (!detail.value) return
  applyName.value = filenameFromTitle(detail.value.title)
  applyDirectory.value = ''
  dialogError.value = ''
  dialog.value = 'apply'
}

function openMove(): void {
  if (!detail.value || detail.value.source !== 'custom') return
  moveName.value = detail.value.path?.split('/').pop()?.replace(/\.md$/i, '') ?? detail.value.title
  moveCategory.value = detail.value.category
  dialogError.value = ''
  dialog.value = 'move'
}

function openCopy(): void {
  if (!detail.value) return
  moveName.value = `${detail.value.title} 副本`
  moveCategory.value = detail.value.category
  dialogError.value = ''
  dialog.value = 'copy'
}

async function submitCreate(): Promise<void> {
  dialogError.value = ''
  const title = draft.title.trim()
  if (!title) {
    dialogError.value = '请填写模板标题'
    return
  }
  draft.name = draft.name.trim() || filenameFromTitle(title)
  draft.tags = tagsText.value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean)
  busy.value = true
  try {
    const created = await api.templateCreate({ ...draft })
    closeDialog()
    await loadCatalog(false)
    detail.value = created
    emit('notify', '模板已创建', 'info')
    if (createThenEdit.value && created.path) emit('edit', created.path)
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitMetadata(): Promise<void> {
  if (!detail.value) return
  dialogError.value = ''
  draft.tags = tagsText.value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean)
  busy.value = true
  try {
    const oldPath = detail.value.path ?? ''
    detail.value = await api.templateUpdate(detail.value.id, {
      title: draft.title,
      description: draft.description,
      category: draft.category,
      tags: draft.tags,
      icon: draft.icon,
    })
    if (oldPath && detail.value.path && oldPath !== detail.value.path) emit('moved', oldPath, detail.value.path)
    closeDialog()
    await loadCatalog()
    emit('notify', '模板信息已更新', 'info')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitApply(): Promise<void> {
  if (!detail.value) return
  dialogError.value = ''
  let name = applyName.value.trim()
  if (!name) {
    dialogError.value = '请填写文档名'
    return
  }
  if (!name.toLowerCase().endsWith('.md')) name += '.md'
  const path = applyDirectory.value ? `${applyDirectory.value}/${name}` : name
  busy.value = true
  try {
    const result = await api.templateApply(detail.value.id, path, name.replace(/\.md$/i, ''))
    closeDialog()
    emit('notify', '已从模板创建文档', 'info')
    emit('created', result.path)
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitMove(): Promise<void> {
  if (!detail.value) return
  dialogError.value = ''
  busy.value = true
  try {
    const oldPath = detail.value.path ?? ''
    const moved = await api.templateMove(detail.value.id, moveName.value.trim(), moveCategory.value)
    closeDialog()
    detail.value = moved
    if (oldPath && moved.path && oldPath !== moved.path) emit('moved', oldPath, moved.path)
    await loadCatalog()
    emit('notify', '模板位置已更新', 'info')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitCopy(): Promise<void> {
  if (!detail.value) return
  dialogError.value = ''
  busy.value = true
  try {
    const copied = await api.templateCopy(detail.value.id, moveName.value.trim(), moveCategory.value)
    closeDialog()
    await loadCatalog(false)
    detail.value = copied
    emit('notify', '已复制为自定义模板', 'info')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitDelete(): Promise<void> {
  if (!detail.value) return
  dialogError.value = ''
  busy.value = true
  try {
    const deletedPath = detail.value.path ?? ''
    await api.templateDelete(detail.value.id)
    closeDialog()
    if (deletedPath) emit('deleted', deletedPath)
    detail.value = null
    await loadCatalog(false)
    emit('notify', '模板已移入回收站', 'info')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function exportTemplate(item = detail.value): Promise<void> {
  if (!item) return
  try {
    const result = await api.templateExport(item.id)
    downloadBlob(result.blob, result.filename)
  } catch (e) {
    emit('notify', `导出失败：${(e as Error).message}`, 'error')
  }
}

async function exportAll(): Promise<void> {
  try {
    const result = await api.templateExportAll()
    downloadBlob(result.blob, result.filename)
  } catch (e) {
    emit('notify', `导出失败：${(e as Error).message}`, 'error')
  }
}

function pickImport(event: Event): void {
  const input = event.target as HTMLInputElement
  importFiles.value = Array.from(input.files ?? [])
  input.value = ''
  if (!importFiles.value.length) return
  moveCategory.value = category.value
  importStrategy.value = 'rename'
  dialogError.value = ''
  dialog.value = 'import'
}

async function submitImport(): Promise<void> {
  if (!importFiles.value.length) return
  dialogError.value = ''
  busy.value = true
  try {
    const result = await api.templateImport(importFiles.value, moveCategory.value, importStrategy.value)
    closeDialog()
    importFiles.value = []
    await loadCatalog(false)
    emit('notify', `已导入 ${result.imported} 个模板${result.skipped ? `，跳过 ${result.skipped} 个` : ''}`, 'info')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function createCategory(): Promise<void> {
  if (!categoryName.value.trim()) return
  dialogError.value = ''
  busy.value = true
  try {
    const result = await api.templateCategoryCreate(categoryName.value.trim())
    categories.value = result.categories
    await loadCatalog()
    categoryName.value = ''
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function renameCategory(): Promise<void> {
  if (!categoryRenameFrom.value || !categoryRenameTo.value.trim()) return
  dialogError.value = ''
  busy.value = true
  try {
    const result = await api.templateCategoryMove(categoryRenameFrom.value, categoryRenameTo.value.trim())
    categories.value = result.categories
    categoryRenameFrom.value = ''
    categoryRenameTo.value = ''
    emit('reset-template-tabs')
    await loadCatalog()
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function deleteCategory(): Promise<void> {
  if (!categoryDelete.value) return
  dialogError.value = ''
  busy.value = true
  try {
    const result = await api.templateCategoryDelete(categoryDelete.value, true)
    categories.value = result.categories
    categoryDelete.value = ''
    emit('reset-template-tabs')
    await loadCatalog()
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

function onTemplatesChanged(): void {
  void loadCatalog()
}

onMounted(() => {
  void loadCatalog(false)
  window.addEventListener('templates-changed', onTemplatesChanged)
})
onBeforeUnmount(() => window.removeEventListener('templates-changed', onTemplatesChanged))
</script>

<template>
  <section class="template-center" aria-labelledby="template-center-title">
    <template v-if="!detail">
      <header class="template-hero">
        <div class="template-hero-copy">
          <span class="template-eyebrow"><LayoutTemplate :size="14" /> 内容模板</span>
          <h1 id="template-center-title">模板中心</h1>
          <p>从清晰的结构开始写作，也可以把自己的最佳实践沉淀为模板。</p>
        </div>
        <div class="template-hero-actions">
          <button class="btn" @click="dialog = 'categories'"><FolderCog :size="14" /> 分类管理</button>
          <button class="btn" @click="importInput?.click()"><Upload :size="14" /> 导入</button>
          <button class="btn" @click="exportAll"><Download :size="14" /> 导出我的模板</button>
          <button class="btn" :disabled="!currentNote" @click="openCreate(true)"><FilePlus2 :size="14" /> 当前笔记存为模板</button>
          <button class="btn btn--primary" @click="openCreate(false)"><Plus :size="14" /> 新建模板</button>
          <input ref="importInput" class="hidden-input" type="file" accept=".md,text/markdown" multiple @change="pickImport" />
        </div>
      </header>

      <div class="template-toolbar">
        <label class="template-search">
          <Search :size="15" />
          <span class="sr-only">搜索模板</span>
          <input v-model="query" type="search" placeholder="搜索标题、描述或标签" />
        </label>
        <div class="seg template-source" aria-label="模板来源">
          <button :class="{ on: source === 'all' }" @click="source = 'all'">全部</button>
          <button :class="{ on: source === 'builtin' }" @click="source = 'builtin'">内置</button>
          <button :class="{ on: source === 'custom' }" @click="source = 'custom'">我的</button>
        </div>
        <label class="template-sort">
          <span>排序</span>
          <select v-model="sortBy">
            <option value="recommended">推荐优先</option>
            <option value="title">按名称</option>
            <option value="updated">最近更新</option>
          </select>
        </label>
      </div>

      <div class="template-categories" aria-label="模板分类">
        <button :class="{ on: !category }" @click="category = ''">全部分类</button>
        <button v-for="item in categories" :key="item" :class="{ on: category === item }" @click="category = item">
          {{ item }}
        </button>
      </div>

      <div v-if="loading" class="template-status"><Loader2 :size="18" class="spin" /> 正在整理模板…</div>
      <EmptyState v-else-if="loadError" class="template-empty">
        <template #icon><LayoutTemplate :size="28" /></template>
        模板加载失败：{{ loadError }}
        <template #actions><button class="btn" @click="loadCatalog(false)"><RefreshCw :size="14" /> 重新加载</button></template>
      </EmptyState>
      <EmptyState v-else-if="templates.length === 0" class="template-empty">
        <template #icon><LayoutTemplate :size="28" /></template>
        还没有可用模板，先新建一个属于自己的模板。
        <template #actions><button class="btn btn--primary" @click="openCreate(false)"><Plus :size="14" /> 新建模板</button></template>
      </EmptyState>
      <EmptyState v-else-if="filtered.length === 0" class="template-empty">
        <template #icon><Search :size="28" /></template>
        没有符合当前筛选条件的模板。
        <template #actions><button class="btn" @click="query = ''; category = ''; source = 'all'">清除筛选</button></template>
      </EmptyState>

      <div v-else class="template-grid" aria-live="polite">
        <button v-for="item in filtered" :key="item.id" class="template-card" @click="openDetail(item.id)">
          <span class="template-card-icon"><component :is="iconFor(item.icon)" :size="20" /></span>
          <span class="template-card-main">
            <span class="template-card-title">{{ item.title }}</span>
            <span class="template-card-desc">{{ item.description || '使用结构化模板快速开始写作。' }}</span>
            <span class="template-card-meta">
              <span>{{ item.category || '未分类' }}</span>
              <span>{{ item.source === 'builtin' ? '内置' : '我的模板' }}</span>
              <span>{{ formatUpdated(item.updated_at) }}</span>
            </span>
          </span>
          <span class="template-card-arrow">查看</span>
        </button>
      </div>
    </template>

    <template v-else>
      <header class="template-detail-head">
        <button class="template-back" @click="backToList"><ArrowLeft :size="16" /> 返回模板列表</button>
        <div class="template-detail-title">
          <span class="template-card-icon"><component :is="iconFor(detail.icon)" :size="21" /></span>
          <div>
            <span class="template-eyebrow">{{ detail.category || '未分类' }} · {{ detail.source === 'builtin' ? '内置模板' : '我的模板' }}</span>
            <h1>{{ detail.title }}</h1>
            <p>{{ detail.description || '使用结构化模板快速开始写作。' }}</p>
          </div>
        </div>
        <div class="template-detail-actions">
          <button class="btn" @click="exportTemplate()"><Download :size="14" /> 导出</button>
          <button class="btn" @click="openCopy"><Copy :size="14" /> {{ detail.source === 'builtin' ? '复制到我的模板' : '复制' }}</button>
          <template v-if="detail.source === 'custom'">
            <button class="btn" @click="openMetadata"><Pencil :size="14" /> 信息</button>
            <button class="btn" @click="openMove"><FolderCog :size="14" /> 改名/移动</button>
            <button v-if="detail.path" class="btn" @click="emit('edit', detail.path)"><FileText :size="14" /> 编辑正文</button>
            <button class="btn btn--danger-quiet" @click="dialog = 'delete'"><Trash2 :size="14" /> 删除</button>
          </template>
          <button class="btn btn--primary" @click="openApply"><FilePlus2 :size="14" /> 使用此模板</button>
        </div>
      </header>

      <div v-if="detailLoading" class="template-status"><Loader2 :size="18" class="spin" /> 正在加载模板…</div>
      <div v-else class="template-detail-body">
        <aside class="template-facts">
          <h2>模板信息</h2>
          <dl>
            <div><dt>来源</dt><dd>{{ detail.source === 'builtin' ? '拾墨内置' : '我的模板' }}</dd></div>
            <div><dt>分类</dt><dd>{{ detail.category || '未分类' }}</dd></div>
            <div><dt>更新</dt><dd>{{ formatUpdated(detail.updated_at) }}</dd></div>
            <div v-if="detail.tags.length"><dt>标签</dt><dd>{{ detail.tags.join('、') }}</dd></div>
          </dl>
          <p><Clock3 :size="13" /> 使用时自动替换标题、日期与时间变量。</p>
        </aside>
        <MarkdownPreview :content="detail.content" :current-path="detail.path || 'template.md'" />
      </div>
    </template>

    <AppDialog :open="dialog !== 'none'" :title="dialogTitle" :busy="busy" :wide="dialog === 'apply' || dialog === 'categories'" @close="closeDialog">
      <form v-if="dialog === 'apply'" class="dialog-form" @submit.prevent="submitApply">
        <label><span>文档名</span><input v-model="applyName" autocomplete="off" /></label>
        <DirectoryPicker v-model="applyDirectory" />
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </form>

      <form v-else-if="dialog === 'create' || dialog === 'metadata'" class="dialog-form" @submit.prevent="dialog === 'create' ? submitCreate() : submitMetadata()">
        <label v-if="dialog === 'create'"><span>文件名</span><input v-model="draft.name" placeholder="自动使用模板标题" autocomplete="off" /></label>
        <label><span>模板标题</span><input v-model="draft.title" required autocomplete="off" /></label>
        <label><span>简短描述</span><textarea v-model="draft.description" rows="3" maxlength="240" /></label>
        <div class="dialog-form-grid">
          <label><span>分类</span><input v-model="draft.category" list="template-category-options" autocomplete="off" /></label>
          <label><span>图标</span><select v-model="draft.icon"><option v-for="(_, key) in ICONS" :key="key" :value="key">{{ key }}</option></select></label>
        </div>
        <datalist id="template-category-options"><option v-for="item in categories" :key="item" :value="item" /></datalist>
        <label><span>标签（逗号分隔）</span><input v-model="tagsText" autocomplete="off" /></label>
        <label v-if="dialog === 'create' && !createFromCurrent"><span>初始正文</span><textarea v-model="draft.content" class="mono" rows="7" /></label>
        <label v-if="dialog === 'create'" class="check-row"><input v-model="createThenEdit" type="checkbox" /> 创建后立即编辑正文</label>
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </form>

      <form v-else-if="dialog === 'move' || dialog === 'copy'" class="dialog-form" @submit.prevent="dialog === 'move' ? submitMove() : submitCopy()">
        <label><span>模板名称</span><input v-model="moveName" required autocomplete="off" /></label>
        <label><span>保存分类</span><input v-model="moveCategory" list="template-category-options" autocomplete="off" /></label>
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </form>

      <div v-else-if="dialog === 'delete'" class="dialog-confirm">
        <span class="dialog-danger-icon"><Trash2 :size="20" /></span>
        <div><strong>将「{{ detail?.title }}」移入回收站？</strong><p>模板不会永久删除，可从回收站恢复。</p></div>
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </div>

      <div v-else-if="dialog === 'categories'" class="category-manager">
        <section>
          <h3>新建分类</h3>
          <div class="inline-form"><input v-model="categoryName" placeholder="分类名称" /><button class="btn" :disabled="busy" @click="createCategory">新建</button></div>
        </section>
        <section>
          <h3>重命名分类</h3>
          <div class="inline-form"><select v-model="categoryRenameFrom"><option value="">选择分类</option><option v-for="item in customCategories" :key="item" :value="item">{{ item }}</option></select><input v-model="categoryRenameTo" placeholder="新名称" /><button class="btn" :disabled="busy" @click="renameCategory">重命名</button></div>
        </section>
        <section>
          <h3>删除分类</h3>
          <p>删除分类会将其中模板一起移入回收站。</p>
          <div class="inline-form"><select v-model="categoryDelete"><option value="">选择分类</option><option v-for="item in customCategories" :key="item" :value="item">{{ item }}</option></select><button class="btn btn--danger" :disabled="busy || !categoryDelete" @click="deleteCategory">删除分类</button></div>
        </section>
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </div>

      <form v-else-if="dialog === 'import'" class="dialog-form" @submit.prevent="submitImport">
        <p class="import-summary">已选择 {{ importFiles.length }} 个 Markdown 文件</p>
        <label><span>导入分类</span><input v-model="moveCategory" list="template-category-options" autocomplete="off" /></label>
        <label><span>同名处理</span><select v-model="importStrategy"><option value="rename">自动改名</option><option value="skip">跳过</option><option value="overwrite">覆盖（保留历史）</option></select></label>
        <p v-if="dialogError" class="field-error" role="alert">{{ dialogError }}</p>
      </form>

      <template #footer>
        <button class="btn" :disabled="busy" @click="closeDialog">取消</button>
        <button v-if="dialog === 'apply'" class="btn btn--primary" :disabled="busy" @click="submitApply">{{ busy ? '创建中…' : '创建文档' }}</button>
        <button v-else-if="dialog === 'create'" class="btn btn--primary" :disabled="busy" @click="submitCreate">{{ busy ? '保存中…' : '创建模板' }}</button>
        <button v-else-if="dialog === 'metadata'" class="btn btn--primary" :disabled="busy" @click="submitMetadata">{{ busy ? '保存中…' : '保存信息' }}</button>
        <button v-else-if="dialog === 'move'" class="btn btn--primary" :disabled="busy" @click="submitMove">{{ busy ? '处理中…' : '确认移动' }}</button>
        <button v-else-if="dialog === 'copy'" class="btn btn--primary" :disabled="busy" @click="submitCopy">{{ busy ? '复制中…' : '确认复制' }}</button>
        <button v-else-if="dialog === 'delete'" class="btn btn--danger" :disabled="busy" @click="submitDelete">{{ busy ? '删除中…' : '移入回收站' }}</button>
        <button v-else-if="dialog === 'import'" class="btn btn--primary" :disabled="busy" @click="submitImport">{{ busy ? '导入中…' : '开始导入' }}</button>
      </template>
    </AppDialog>
  </section>
</template>
