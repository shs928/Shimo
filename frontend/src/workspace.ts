/**
 * 工作区状态持久化：打开标签（不含正文）、活动路径、面板模式、树展开。
 *
 * - 浏览器生成 device_id（localStorage，跨会话稳定）。
 * - 桌面 / 移动端状态隔离（不同 key）。
 * - 只保存路径元数据，不保存未提交正文。
 * - 恢复时逐路径验证，失效路径安全跳过。
 */
import type { NodeInfo } from './types'

export interface WorkspaceState {
  tabs: string[]
  activePath: string
  leftOpen: boolean
  indexMode: 'files' | 'search' | 'trash'
  rightDomain: 'marginalia' | 'assistant'
  marginaliaView: 'meta' | 'backlinks' | 'outgoing' | 'graph' | 'history'
  assistantView: 'ai' | 'agent'
  expanded: string[]
  savedAt: number
}

const DEVICE_KEY = 'shimo.device_id'
const WS_PREFIX = 'shimo.workspace.'
const SAVE_DEBOUNCE = 500

export function getDeviceId(): string {
  let id = localStorage.getItem(DEVICE_KEY)
  if (!id) {
    id = crypto.randomUUID?.() ?? `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(DEVICE_KEY, id)
  }
  return id
}

export function isMobileView(): boolean {
  return window.matchMedia('(max-width: 768px)').matches
}

function wsKey(): string {
  // device_id（浏览器身份）+ 设备类型（桌面/移动隔离）
  return `${WS_PREFIX}${getDeviceId()}.${isMobileView() ? 'mobile' : 'desktop'}`
}

let saveTimer: number | undefined

export function saveWorkspace(state: {
  tabs: string[]
  activePath: string
  leftOpen: boolean
  indexMode: 'files' | 'search' | 'trash'
  rightDomain: 'marginalia' | 'assistant'
  marginaliaView: 'meta' | 'backlinks' | 'outgoing' | 'graph' | 'history'
  assistantView: 'ai' | 'agent'
  expanded: Set<string>
}): void {
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    const payload: WorkspaceState = {
      tabs: state.tabs,
      activePath: state.activePath,
      leftOpen: state.leftOpen,
      indexMode: state.indexMode,
      rightDomain: state.rightDomain,
      marginaliaView: state.marginaliaView,
      assistantView: state.assistantView,
      expanded: Array.from(state.expanded),
      savedAt: Date.now(),
    }
    try {
      localStorage.setItem(wsKey(), JSON.stringify(payload))
    } catch {
      /* 存储不可用时静默降级 */
    }
  }, SAVE_DEBOUNCE)
}

export function loadWorkspace(): WorkspaceState | null {
  try {
    const raw = localStorage.getItem(wsKey())
    if (!raw) return null
    const data = JSON.parse(raw) as WorkspaceState
    if (!Array.isArray(data.tabs)) return null
    return data
  } catch {
    return null
  }
}

export function clearWorkspaceState(): void {
  try {
    localStorage.removeItem(wsKey())
  } catch {
    /* ignore */
  }
}
