/**
 * Markdown 渲染管线（预览端）：
 *   frontmatter 剥离 → WikiLink 预处理 → KaTeX 标记 → Mermaid 标记
 *   → marked 渲染 → DOMPurify 消毒 → 后处理（锚点 id / KaTeX / Mermaid）
 *
 * 安全：所有输出经 DOMPurify 消毒；原始 HTML 中脚本与事件属性被移除；
 * Mermaid 使用 strict 安全级别。相对路径图片/附件统一解析为 /api/v1/raw/。
 *
 * WikiLink：渲染为带 data 属性的可点击元素，点击后由 App 层调用
 * /api/v1/wiki/resolve 解析真实路径（当前目录 → 根目录 → 唯一文件名）。
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

const WIKILINK_RE = /!?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g
const KATEX_BLOCK_RE = /^\$\$([\s\S]+?)\$\$/gm
const KATEX_INLINE_RE = /(?<!\$)\$(?!\s|\d)([^$\n]+?)\$(?!\$)/g
const MERMAID_FENCE_RE = /^```mermaid\s*\n([\s\S]*?)```/gm

export function slugify(text: string): string {
  return (
    text
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_\u4e00-\u9fff]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '') || 'section'
  )
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** WikiLink → 可点击解析链接（图片嵌入直接渲染相对路径图片） */
function preprocessWikilinks(text: string, currentPath: string): string {
  const dir = currentPath.includes('/') ? currentPath.slice(0, currentPath.lastIndexOf('/')) : ''
  return text.replace(WIKILINK_RE, (whole, target: string, anchor?: string, alias?: string) => {
    const isEmbed = whole.startsWith('!')
    const label = alias || target
    if (isEmbed) {
      const ext = /\.(png|jpe?g|gif|webp|svg|avif|bmp)$/i.test(target)
      if (ext) return `![](/api/v1/raw/${encodeURIComponent(target.trim())})`
      return `> 嵌入: ${label}`
    }
    const anchorSlug = anchor ? slugify(anchor.trim()) : ''
    return `<a href="#" class="wiki-link" data-target="${escapeAttr(target.trim())}" data-dir="${escapeAttr(dir)}" data-anchor="${escapeAttr(anchorSlug)}">${escapeAttr(label)}</a>`
  })
}

/** $..$ / $$..$$ → 占位标记，渲染后由 KaTeX 填充（避免与其他 `$` 误伤） */
function preprocessKatex(text: string): string {
  return text
    .replace(KATEX_BLOCK_RE, (_m, tex: string) => `\n\n<div class="math-block" data-katex="${escapeAttr(tex.trim())}"></div>\n\n`)
    .replace(KATEX_INLINE_RE, (_m, tex: string) => `<span class="math-inline" data-katex="${escapeAttr(tex.trim())}"></span>`)
}

function preprocessMermaid(text: string): string {
  return text.replace(MERMAID_FENCE_RE, (_m, code: string) => `\n\n<div class="mermaid">${escapeAttr(code.trim())}</div>\n\n`)
}

function parseFrontmatter(text: string): { body: string; data: Record<string, unknown> } {
  if (!text.startsWith('---')) return { body: text, data: {} }
  const lines = text.split('\n')
  const endIdx = lines.slice(1).findIndex((l) => l.trim() === '---')
  if (endIdx < 0) return { body: text, data: {} }
  const raw = lines.slice(1, endIdx + 1).join('\n')
  const data: Record<string, unknown> = {}
  for (const line of raw.split('\n')) {
    const m = line.match(/^([\w-]+):\s*(.+)$/)
    if (m) data[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
  return { body: lines.slice(endIdx + 2).join('\n'), data }
}

/** 渲染为已消毒 HTML 字符串（KaTeX/Mermaid 在 renderAfter 中异步补全） */
export function renderMarkdown(text: string, currentPath: string): { html: string; meta: Record<string, unknown> } {
  const { body, data } = parseFrontmatter(text)
  let prepared = body
  prepared = preprocessWikilinks(prepared, currentPath)
  prepared = preprocessKatex(prepared)
  prepared = preprocessMermaid(prepared)

  let html = marked.parse(prepared) as string
  html = DOMPurify.sanitize(html, {
    ADD_ATTR: ['data-katex'],
    ADD_TAGS: ['math'],
    FORBID_TAGS: ['style'],
  })
  return { html, meta: data }
}

/** 后处理：图片相对路径、WikiLink 点击、标题锚点、KaTeX、Mermaid */
export async function renderAfter(root: HTMLElement): Promise<void> {
  // 相对路径图片 / 附件解析为可访问 URL
  root.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src')
    if (src) img.setAttribute('src', resolveAssetUrl(src))
  })

  // 直接 Markdown 链接指向 .md（非 WikiLink）：在当前应用内打开
  root.querySelectorAll('a[href^="/api/v1/raw/"]').forEach((a) => {
    a.addEventListener('click', (ev) => {
      const href = (a.getAttribute('href') || '').split('#')[0]
      const rel = decodeURIComponent(href.replace('/api/v1/raw/', ''))
      if (!rel.endsWith('.md')) return
      ev.preventDefault()
      window.dispatchEvent(new CustomEvent('open-note', { detail: rel }))
    })
  })

  // WikiLink：点击时由 App 层调用后端解析
  root.querySelectorAll('a.wiki-link').forEach((node) => {
    const a = node as HTMLElement
    a.addEventListener('click', (ev) => {
      ev.preventDefault()
      window.dispatchEvent(
        new CustomEvent('wiki-open', {
          detail: {
            target: a.dataset.target || '',
            anchor: a.dataset.anchor || '',
            dir: a.dataset.dir || '',
          },
        }),
      )
    })
  })

  // 标题锚点（与后端 outline slug 规则一致）
  const used = new Map<string, number>()
  root.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h) => {
    const base = slugify(h.textContent || 'section')
    const n = used.get(base) ?? 0
    used.set(base, n + 1)
    h.id = n === 0 ? base : `${base}-${n + 1}`
  })

  // KaTeX
  const katexNodes = root.querySelectorAll<HTMLElement>('[data-katex]')
  if (katexNodes.length > 0) {
    try {
      const katex = (await import('katex')).default
      katexNodes.forEach((el) => {
        const tex = el.dataset.katex || ''
        const display = el.classList.contains('math-block')
        try {
          el.innerHTML = katex.renderToString(tex, { displayMode: display, throwOnError: false })
        } catch {
          el.textContent = tex
        }
      })
    } catch {
      katexNodes.forEach((el) => (el.textContent = el.dataset.katex || ''))
    }
  }

  // Mermaid（strict 安全级别；主题跟随明暗偏好）
  const mermaidNodes = root.querySelectorAll<HTMLElement>('.mermaid')
  if (mermaidNodes.length > 0) {
    try {
      const mermaid = (await import('mermaid')).default
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: isDarkTheme() ? 'dark' : 'default',
      })
      for (const el of mermaidNodes) {
        try {
          const { svg } = await mermaid.render(`mmd-${Math.random().toString(36).slice(2, 8)}`, el.textContent || '')
          el.innerHTML = svg
        } catch {
          el.textContent = '（Mermaid 图表解析失败）'
        }
      }
    } catch {
      mermaidNodes.forEach((el) => (el.textContent = '（Mermaid 渲染不可用）'))
    }
  }
}

/** 当前是否为深色主题（prefers-color-scheme） */
export function isDarkTheme(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** 相对路径（assets/...、图片等）解析为可访问 URL */
export function resolveAssetUrl(src: string): string {
  if (!src) return src
  if (/^(https?:|data:|blob:|#|\/)/i.test(src)) return src
  return `/api/v1/raw/${src}`
}
