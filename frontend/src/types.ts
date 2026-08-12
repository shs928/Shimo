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
