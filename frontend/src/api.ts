/** 后端 API 客户端：认证、文件树、读写、移动、回收站、附件、元信息、WikiLink 解析。 */
import type { NodeInfo, FileContent, MovePlan } from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...init,
    headers: {
      // FormData 不手动设置 Content-Type（浏览器自动带 boundary）
      ...(isForm ? {} : { 'Content-Type': 'application/json' }),
      ...(init?.headers ?? {}),
    },
  })
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth-expired'))
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* keep statusText */
    }
    const err = new Error(detail) as Error & { status: number }
    err.status = res.status
    throw err
  }
  return res.json() as Promise<T>
}

export const api = {
  status: () => request<{ initialized: boolean; authenticated: boolean }>('/api/v1/auth/status'),

  init: (password: string) =>
    request<{ ok: boolean }>('/api/v1/auth/init', { method: 'POST', body: JSON.stringify({ password }) }),

  login: (password: string) =>
    request<{ ok: boolean }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),

  logout: () => request<{ ok: boolean }>('/api/v1/auth/logout', { method: 'POST' }),

  tree: (path = '') => request<{ entries: NodeInfo[] }>(`/api/v1/tree?path=${encodeURIComponent(path)}`),

  readFile: (path: string) =>
    request<FileContent>(`/api/v1/files/content?path=${encodeURIComponent(path)}`),

  saveFile: (path: string, content: string, etag: string | null) =>
    request<FileContent>(`/api/v1/files/content?path=${encodeURIComponent(path)}`, {
      method: 'PUT',
      headers: etag ? { 'If-Match': etag } : {},
      body: JSON.stringify({ content }),
    }),

  create: (path: string, type: 'file' | 'dir', initialContent = '') =>
    request<NodeInfo>('/api/v1/files', {
      method: 'POST',
      body: JSON.stringify({ path, type, initial_content: initialContent }),
    }),

  movePreview: (src: string, dst: string) =>
    request<MovePlan>('/api/v1/files/move/preview', {
      method: 'POST',
      body: JSON.stringify({ src, dst }),
    }),

  move: (src: string, dst: string) =>
    request<NodeInfo>('/api/v1/files/move', {
      method: 'POST',
      body: JSON.stringify({ src, dst }),
    }),

  remove: (path: string) =>
    request<{ ok: boolean }>(`/api/v1/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),

  trash: () => request<{ entries: NodeInfo[] }>('/api/v1/trash'),

  restore: (path: string, target?: string) =>
    request<NodeInfo>('/api/v1/trash/restore', {
      method: 'POST',
      body: JSON.stringify({ path, target: target ?? null }),
    }),

  purgeTrash: () => request<{ purged: number }>('/api/v1/trash/purge', { method: 'POST' }),

  uploadAttachment: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ name: string; relative_path: string; url: string }>('/api/v1/attachments', {
      method: 'POST',
      body: form,
    })
  },

  /** 导入文档（PDF/Word/TXT/CSV/MD）到知识库目录，进入 AI 索引 */
  importFile: (file: File, dir = '') => {
    const form = new FormData()
    form.append('file', file)
    const q = dir ? `?dir=${encodeURIComponent(dir)}` : ''
    return request<{ path: string; name: string; size: number; parsed_chars: number }>(`/api/v1/import${q}`, {
      method: 'POST',
      body: form,
    })
  },

  fileMeta: (path: string) =>
    request<{
      path: string
      title: string
      frontmatter: Record<string, unknown>
      has_frontmatter: boolean
      size: number
      mtime_ns: number
      etag: string
    }>(`/api/v1/files/meta?path=${encodeURIComponent(path)}`),

  fileOutline: (path: string) =>
    request<{ headings: Array<{ level: number; text: string; slug: string; line: number }> }>(
      `/api/v1/files/outline?path=${encodeURIComponent(path)}`,
    ),

  /** WikiLink 目标解析：当前目录优先，其次根目录，最后唯一文件名；歧义返回 null */
  resolveWiki: (link: string, dir: string) =>
    request<{ path: string | null }>(
      `/api/v1/wiki/resolve?link=${encodeURIComponent(link)}&dir=${encodeURIComponent(dir)}`,
    ),

  search: (q: string, limit = 30) =>
    request<{ results: Array<{ path: string; title: string; snippet: string; rank: number }> }>(
      `/api/v1/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  backlinks: (path: string) =>
    request<{
      backlinks: Array<{
        source_path: string
        title: string | null
        anchor: string
        alias: string
        link_type: string
        line: number
        context: string
      }>
    }>(`/api/v1/backlinks?path=${encodeURIComponent(path)}`),

  outgoing: (path: string) =>
    request<{
      links: Array<{
        target_raw: string
        target_path: string | null
        anchor: string
        alias: string
        link_type: string
        line: number
        context: string
        resolved: number
      }>
    }>(`/api/v1/outgoing?path=${encodeURIComponent(path)}`),

  graph: (path: string) =>
    request<{
      nodes: Array<{ id: string; label: string }>
      edges: Array<{ source: string; target: string }>
    }>(`/api/v1/graph?path=${encodeURIComponent(path)}`),

  indexStats: () =>
    request<{ files: number; links: number; unresolved_links: number; tokenizer: string }>(
      '/api/v1/index/stats',
    ),

  indexRebuild: () =>
    request<{ indexed: number; failed: Array<{ path: string; error: string }>; tokenizer: string }>(
      '/api/v1/index/rebuild',
      { method: 'POST' },
    ),

  aiStatus: () =>
    request<{
      enabled: boolean
      chat_configured: boolean
      embed_configured: boolean
      agent_configured: boolean
      providers: number
      chunks: number
      embedded: number
      has_vectors: boolean
      embedding_running: boolean
      mcp_servers: number
    }>('/api/v1/ai/status'),

  aiConfig: () =>
    request<{
      enabled: boolean
      providers: Array<{ id: string; name: string; base_url: string; models: string[]; has_key: boolean }>
      chat: { provider_id: string; model: string; temperature: number; max_tokens: number; max_history_messages: number }
      embedding: { provider_id: string; model: string; batch: number }
      rerank: { enabled: boolean; provider_id: string; model: string }
      agent: { provider_id: string; model: string; max_iterations: number; system_prompt: string; tools: Record<string, boolean> }
      mcp: { servers: Array<{ name: string; url: string }> }
    }>('/api/v1/ai/config'),

  aiSaveConfig: (payload: {
    enabled?: boolean
    providers?: Array<{ id: string; name: string; base_url: string; api_key?: string; models?: string[] }>
    chat?: Record<string, unknown>
    embedding?: Record<string, unknown>
    rerank?: Record<string, unknown>
    agent?: Record<string, unknown>
    mcp?: { servers: Array<{ name: string; url: string }> }
  }) =>
    request<{ enabled: boolean; chat_configured: boolean; embed_configured: boolean; providers: number }>(
      '/api/v1/ai/config',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  aiTest: () =>
    request<{ ok: boolean; message: string }>('/api/v1/ai/test', { method: 'POST' }),

  aiRebuild: () =>
    request<{ reindexed: number; pending: number; embedded: number; total: number; chunks: number }>(
      '/api/v1/ai/rebuild',
      { method: 'POST' },
    ),

  aiEmbeddingStat: () =>
    request<{ running: boolean; pending: number; embedded: number; total: number }>('/api/v1/ai/embedding-stat'),

  aiListModels: (providerId: string) =>
    request<{ ok: boolean; models: string[]; message: string }>('/api/v1/ai/list-models', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId }),
    }),

  aiSemanticSearch: (query: string, k = 5) =>
    request<{ query: string; results: Array<{ file_path: string; heading: string; line_start: number; line_end: number; text: string; score: number }> }>(
      '/api/v1/ai/semantic-search',
      { method: 'POST', body: JSON.stringify({ query, k }) },
    ),

  /** 流式聊天（rag | free）：返回原始 Response，由调用方解析 SSE */
  aiChat: (message: string, mode = 'rag', sessionId?: string, signal?: AbortSignal) =>
    fetch('/api/v1/ai/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, mode, session_id: sessionId ?? null }),
      signal,
    }),

  aiNewSession: () =>
    request<{ session_id: string }>('/api/v1/ai/chat/session', { method: 'POST' }),

  aiClearSession: (sessionId: string) =>
    request<{ ok: boolean }>(`/api/v1/ai/chat/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

  /** 选中文本 AI 操作（流式） */
  aiAction: (text: string, action: string, signal?: AbortSignal) =>
    fetch('/api/v1/ai/action', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, action }),
      signal,
    }),

  /** Agent 对话（流式 SSE） */
  aiAgentChat: (message: string, sessionId?: string, signal?: AbortSignal) =>
    fetch('/api/v1/ai/agent/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId ?? null }),
      signal,
    }),

  aiAgentConfirm: (requestId: string, decision: 'allow' | 'deny') =>
    request<{ ok: boolean }>('/api/v1/ai/agent/confirm', {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, decision }),
    }),

  aiAgentSessions: () =>
    request<{ sessions: Array<{ id: string; title: string; updated_at: string }> }>('/api/v1/ai/agent/sessions'),

  aiAgentSession: (sessionId: string) =>
    request<{ session: { id: string; title: string; messages: unknown[]; updated_at: string } | null }>(
      `/api/v1/ai/agent/session/${encodeURIComponent(sessionId)}`,
    ),

  aiAgentDeleteSession: (sessionId: string) =>
    request<{ ok: boolean }>(`/api/v1/ai/agent/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

  aiAgentTools: () =>
    request<{
      tools: Array<{ name: string; description: string; enabled: boolean; write: boolean }>
    }>('/api/v1/ai/agent/lsTools'),

  aiAgentSystemPrompt: () =>
    request<{ system_prompt: string }>('/api/v1/ai/agent/system-prompt'),

  aiMCPStatus: () =>
    request<{ servers: Array<{ name: string; url: string; connected: boolean; tools: number }> }>(
      '/api/v1/ai/agent/mcp/status',
    ),
}
