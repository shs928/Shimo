/** 全局状态：认证、文件树、标签页与保存状态。 */
import { reactive } from 'vue'
import { api } from './api'
import type { NodeInfo, SaveState } from './types'

export interface Tab {
  path: string
  name: string
  /** 判别联合：md → 在线编辑；doc → 只读文本预览 */
  kind: 'md' | 'doc'
  /** 当前工作内容（内存态，可能未保存）；doc 为预览文本（只读） */
  content: string
  /** 最后一次成功保存的内容，用于判定 dirty 状态 */
  savedContent: string
  etag: string | null
  saveState: SaveState
  error: string
  /** 文档预览元信息（kind === 'doc' 时存在） */
  docMeta?: {
    size: number
    chars: number
    truncated: boolean
    has_text: boolean
    ocr: boolean
    ocr_status: string | null
    ocr_progress: number
    ocr_error: string
  }
}

const DOC_EXT = /\.(pdf|docx|txt|csv)$/i

export function isDocumentPath(path: string): boolean {
  return DOC_EXT.test(path)
}

export const state = reactive({
  authReady: false,
  initialized: false,
  authenticated: false,
  rootLoaded: false,
  root: [] as NodeInfo[],
  expanded: new Set<string>(),
  /** 子目录懒加载缓存；refreshTree 时整体清空，保证一致性 */
  treeChildren: new Map<string, NodeInfo[]>(),
  tabs: [] as Tab[],
  activePath: '' as string,
  showTrash: false,
  trash: [] as NodeInfo[],
})

/** 刷新根目录树，并清空子目录缓存 */
export async function refreshTree(): Promise<void> {
  const { entries } = await api.tree('')
  state.root = entries
  state.treeChildren.clear()
  state.rootLoaded = true
}

export function activeTab(): Tab | null {
  return state.tabs.find((t) => t.path === state.activePath) ?? null
}

export function findTab(path: string): Tab | undefined {
  return state.tabs.find((t) => t.path === path)
}

export async function openTab(path: string): Promise<void> {
  const existing = findTab(path)
  if (existing) {
    state.activePath = path
    return
  }
  if (isDocumentPath(path)) {
    // 文档：只读预览（无 ETag、无编辑、无 dirty）
    const pv = await api.documentPreview(path)
    state.tabs.push({
      path,
      name: pv.name,
      kind: 'doc',
      content: pv.text,
      savedContent: pv.text,
      etag: null,
      saveState: 'saved',
      error: '',
      docMeta: {
        size: pv.size,
        chars: pv.chars,
        truncated: pv.truncated,
        has_text: pv.has_text,
        ocr: pv.ocr,
        ocr_status: pv.ocr_status,
        ocr_progress: pv.ocr_progress,
        ocr_error: pv.ocr_error,
      },
    })
  } else {
    const fc = await api.readFile(path)
    state.tabs.push({
      path,
      name: basename(path),
      kind: 'md',
      content: fc.content,
      savedContent: fc.content,
      etag: fc.etag,
      saveState: 'saved',
      error: '',
    })
  }
  state.activePath = path
}

export function closeTab(path: string): void {
  const idx = state.tabs.findIndex((t) => t.path === path)
  if (idx < 0) return
  state.tabs.splice(idx, 1)
  if (state.activePath === path) {
    const next = state.tabs[Math.max(0, idx - 1)]
    state.activePath = next?.path ?? ''
  }
}

/** 关闭指定路径及其所有子路径的标签（用于删除目录时） */
export function closePathAndDescendants(path: string): void {
  state.tabs = state.tabs.filter((t) => t.path !== path && !t.path.startsWith(path + '/'))
  if (!state.tabs.some((t) => t.path === state.activePath)) {
    state.activePath = state.tabs[0]?.path ?? ''
  }
}

/** 文件/目录重命名或移动后同步打开的标签路径 */
export function renameOpenTab(oldPath: string, newPath: string): void {
  for (const t of state.tabs) {
    if (t.path === oldPath) {
      t.path = newPath
      t.name = basename(newPath)
    }
  }
  if (state.activePath === oldPath) state.activePath = newPath
}

export function basename(path: string): string {
  const seg = path.split('/')
  return seg[seg.length - 1] || path
}

export async function createFile(path: string, initial = ''): Promise<void> {
  await api.create(path, 'file', initial)
  await refreshTree()
  await openTab(path)
}

export async function createDir(path: string): Promise<void> {
  await api.create(path, 'dir')
  await refreshTree()
}

export async function deletePath(path: string): Promise<void> {
  await api.remove(path)
  closePathAndDescendants(path)
  await refreshTree()
}

export async function refreshTrash(): Promise<void> {
  const { entries } = await api.trash()
  state.trash = entries
}
