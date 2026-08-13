/** 与后端 JSON 响应对应的类型定义。 */

export interface NodeInfo {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number
  mtime_ns: number
  etag?: string | null
}

export interface FileContent {
  path: string
  content: string
  etag: string
  mtime_ns: number
  size: number
  bom: boolean
  newline: string
  /** 保存成功但索引失败时的非阻塞警告 */
  index_warning?: string | null
}

export interface MovePlan {
  src: string
  dst: string
  exists: boolean
  kind: string
  valid: boolean
  message: string
}

export interface AuthStatus {
  initialized: boolean
  authenticated: boolean
}

export type SaveState = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict'

export type TemplateSource = 'builtin' | 'custom'

export interface TemplateSummary {
  id: string
  source: TemplateSource
  title: string
  description: string
  category: string
  tags: string[]
  icon: string
  path?: string | null
  updated_at: string
}

export interface TemplateDetail extends TemplateSummary {
  content: string
}

export interface TemplateCatalog {
  templates: TemplateSummary[]
  categories: string[]
  custom_categories?: string[]
}

export interface TemplateDraft {
  name: string
  title: string
  description: string
  category: string
  tags: string[]
  icon: string
  content: string
}
