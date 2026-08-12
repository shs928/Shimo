/**
 * 搜索高亮：把命中片段拆成文本分片（命中 / 未命中），由 Vue 插值渲染。
 * 不使用 v-html，查询词永不被当作 HTML（XSS 安全）。
 */
export interface HighlightPart {
  seg: string
  hit: boolean
}

export function splitHighlight(text: string, query: string): HighlightPart[] {
  const q = (query ?? '').trim()
  if (!q) return [{ seg: text, hit: false }]
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  let re: RegExp
  try {
    re = new RegExp(`(${escaped})`, 'gi')
  } catch {
    return [{ seg: text, hit: false }]
  }
  const parts = text.split(re).filter(Boolean)
  const lowerQ = q.toLowerCase()
  return parts.map((seg) => ({ seg, hit: seg.toLowerCase() === lowerQ }))
}
