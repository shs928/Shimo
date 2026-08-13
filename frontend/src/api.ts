/** 后端 API 客户端：认证、文件树、读写、移动、回收站、附件、元信息、WikiLink 解析。 */
import type {
  FileContent,
  MovePlan,
  NodeInfo,
  TemplateCatalog,
  TemplateDetail,
  TemplateDraft,
} from './types'

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

async function requestBlob(url: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(url, { credentials: 'same-origin' })
  if (res.status === 401) window.dispatchEvent(new CustomEvent('auth-expired'))
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* keep statusText */
    }
    throw new Error(detail)
  }
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : plain || 'template.md'
  return { blob: await res.blob(), filename }
}

export const api = {
  status: () => request<{ initialized: boolean; authenticated: boolean }>('/api/v1/auth/status'),

  /** 实时事件 SSE（文件树/文件变化）；返回原始 Response 由调用方解析 */
  events: (signal?: AbortSignal) =>
    fetch('/api/v1/events', { credentials: 'same-origin', signal }),

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
    request<MovePlan & { affected_links: number; affected_files: string[] }>('/api/v1/files/move/preview', {
      method: 'POST',
      body: JSON.stringify({ src, dst }),
    }),

  move: (src: string, dst: string, refactorLinks = false) =>
    request<NodeInfo>('/api/v1/files/move', {
      method: 'POST',
      body: JSON.stringify({ src, dst, refactor_links: refactorLinks }),
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

  /** 版本历史：列表 / 读取 / diff / 恢复 */
  historyList: (path: string) =>
    request<{ path: string; versions: Array<{ sha1: string; saved_at: string }> }>(
      `/api/v1/history?path=${encodeURIComponent(path)}`,
    ),

  historyGet: (path: string, sha1: string) =>
    request<{ path: string; sha1: string; content: string }>(
      `/api/v1/history/version?path=${encodeURIComponent(path)}&sha1=${encodeURIComponent(sha1)}`,
    ),

  historyDiff: (path: string, sha1: string) =>
    request<{ path: string; sha1: string; diff: string }>(
      `/api/v1/history/diff?path=${encodeURIComponent(path)}&sha1=${encodeURIComponent(sha1)}`,
    ),

  historyRestore: (path: string, sha1: string) =>
    request<{ ok: boolean; path: string; content: string }>(
      `/api/v1/history/restore?path=${encodeURIComponent(path)}&sha1=${encodeURIComponent(sha1)}`,
      { method: 'POST' },
    ),

  /** 文档只读预览（PDF/Word/TXT/CSV 纯文本提取；扫描件 has_text=false + ocr_status） */
  documentPreview: (path: string) =>
    request<{
      path: string
      name: string
      size: number
      chars: number
      truncated: boolean
      has_text: boolean
      ocr: boolean
      ocr_status: string | null
      ocr_progress: number
      ocr_error: string
      raw_url: string
      text: string
    }>(`/api/v1/documents/preview?path=${encodeURIComponent(path)}`),

  /** OCR 识别失败后重新入队（failed → pending） */
  ocrRetry: (path: string) =>
    request<{ status: string }>(`/api/v1/documents/ocr-retry?path=${encodeURIComponent(path)}`, {
      method: 'POST',
    }),

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
    request<{
      files: number
      links: number
      unresolved_links: number
      tokenizer: string
      failures: Array<{ path: string; subsystem: string; error: string; attempts: number; updated_at: string }>
      failure_count: number
    }>('/api/v1/index/stats'),

  indexRetryFailed: () =>
    request<{ retried: number; cleared: number; still_failed: number }>('/api/v1/index/retry-failed', {
      method: 'POST',
    }),

  indexRebuild: () =>
    request<{ indexed: number; failed: Array<{ path: string; error: string }>; tokenizer: string }>(
      '/api/v1/index/rebuild',
      { method: 'POST' },
    ),

  /** 模板中心：内置模板与 vault/templates 自定义模板统一目录。 */
  templates: () => request<TemplateCatalog>('/api/v1/templates'),

  templateDetail: (id: string) =>
    request<TemplateDetail>(`/api/v1/templates/detail?id=${encodeURIComponent(id)}`),

  templateApply: (id: string, path: string, title?: string) =>
    request<{ path: string }>('/api/v1/templates/apply', {
      method: 'POST',
      body: JSON.stringify({ id, path, title: title ?? '' }),
    }),

  templateCreate: (draft: TemplateDraft) =>
    request<TemplateDetail>('/api/v1/templates/custom', {
      method: 'POST',
      body: JSON.stringify(draft),
    }),

  templateUpdate: (id: string, patch: Partial<Omit<TemplateDraft, 'name'>>) =>
    request<TemplateDetail>('/api/v1/templates/custom', {
      method: 'PUT',
      body: JSON.stringify({ id, ...patch }),
    }),

  templateMove: (id: string, name?: string, category?: string) =>
    request<TemplateDetail>('/api/v1/templates/move', {
      method: 'POST',
      body: JSON.stringify({ id, name: name ?? null, category: category ?? null }),
    }),

  templateCopy: (id: string, name?: string, category?: string) =>
    request<TemplateDetail>('/api/v1/templates/copy', {
      method: 'POST',
      body: JSON.stringify({ id, name: name ?? null, category: category ?? null }),
    }),

  templateDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/v1/templates/custom?id=${encodeURIComponent(id)}`, { method: 'DELETE' }),

  templateCategoryCreate: (name: string) =>
    request<{ categories: string[]; custom_categories?: string[] }>('/api/v1/templates/categories', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  templateCategoryMove: (name: string, newName: string) =>
    request<{ categories: string[]; custom_categories?: string[] }>('/api/v1/templates/categories/move', {
      method: 'POST',
      body: JSON.stringify({ name, new_name: newName }),
    }),

  templateCategoryDelete: (name: string, force = false) =>
    request<{ categories: string[]; custom_categories?: string[] }>(
      `/api/v1/templates/categories?name=${encodeURIComponent(name)}&force=${force ? 'true' : 'false'}`,
      { method: 'DELETE' },
    ),

  templateImport: (files: File[], category: string, strategy: 'skip' | 'rename' | 'overwrite') => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request<{ imported: number; skipped: number; templates: TemplateDetail[] }>(
      `/api/v1/templates/import?category=${encodeURIComponent(category)}&strategy=${strategy}`,
      { method: 'POST', body: form },
    )
  },

  templateExport: (id: string) => requestBlob(`/api/v1/templates/export?id=${encodeURIComponent(id)}`),

  templateExportAll: () => requestBlob('/api/v1/templates/export-all'),

  aiStatus: () =>
    request<{
      enabled: boolean
      chat_configured: boolean
      embed_configured: boolean
      embedding_active: boolean
      agent_configured: boolean
      providers: number
      chunks: number
      embedded: number
      has_vectors: boolean
      worker_alive: boolean
      mcp_servers: number
    }>('/api/v1/ai/status'),

  aiConfig: () =>
    request<{
      enabled: boolean
      providers: Array<{ id: string; name: string; base_url: string; models: string[]; has_key: boolean }>
      chat: { provider_id: string; model: string; temperature: number; max_tokens: number; max_history_messages: number }
      embedding: { provider_id: string; model: string; batch: number }
      rerank: { enabled: boolean; provider_id: string; model: string }
      vision: { provider_id: string; model: string }
      ocr: { enabled: boolean }
      agent: { provider_id: string; model: string; max_iterations: number; system_prompt: string; tools: Record<string, boolean> }
      mcp: { servers: Array<{ name: string; url: string }> }
    }>('/api/v1/ai/config'),

  aiSaveConfig: (payload: {
    enabled?: boolean
    providers?: Array<{ id: string; name: string; base_url: string; api_key?: string; models?: string[] }>
    chat?: Record<string, unknown>
    embedding?: Record<string, unknown>
    rerank?: Record<string, unknown>
    vision?: Record<string, unknown>
    agent?: Record<string, unknown>
    mcp?: { servers: Array<{ name: string; url: string }> }
  }) =>
    request<{
      enabled: boolean
      chat_configured: boolean
      embed_configured: boolean
      providers: number
      embedding_changed: boolean
    }>('/api/v1/ai/config', { method: 'POST', body: JSON.stringify(payload) }),

  aiTest: () =>
    request<{ ok: boolean; message: string }>('/api/v1/ai/test', { method: 'POST' }),

  aiRebuild: () =>
    request<{ reindexed: number; pending: number; embedded: number; total: number; chunks: number }>(
      '/api/v1/ai/rebuild',
      { method: 'POST' },
    ),

  aiEmbeddingStat: () =>
    request<{ running: boolean; pending: number; embedded: number; total: number; last_error: string | null; backoff_seconds: number }>(
      '/api/v1/ai/embedding-stat',
    ),

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

  aiChatSessions: () =>
    request<{ sessions: Array<{ id: string; title: string; updated_at: string }> }>('/api/v1/ai/chat/session'),

  aiChatGetSession: (sessionId: string) =>
    request<{ session: { id: string; title: string; messages: unknown[]; updated_at: string } | null }>(
      `/api/v1/ai/chat/session/${encodeURIComponent(sessionId)}`,
    ),

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

  aiAgentCreateSession: () =>
    request<{ session_id: string }>('/api/v1/ai/agent/session', { method: 'POST' }),

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
