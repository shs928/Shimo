/** 前端单元测试：store tab 分流、搜索安全高亮、工作区持久化。 */
import { describe, expect, it, vi, beforeEach } from 'vitest'

describe('splitHighlight（安全高亮）', () => {
  it('无查询时原样返回', async () => {
    const { splitHighlight: f } = await import('../../src/highlight')
    expect(f('hello', '')).toEqual([{ seg: 'hello', hit: false }])
  })

  it('命中片段被标记', async () => {
    const { splitHighlight: f } = await import('../../src/highlight')
    const parts = f('笔记一与笔记二', '笔记')
    const hits = parts.filter((p) => p.hit)
    expect(hits.length).toBe(2)
    expect(hits.every((h) => h.seg === '笔记')).toBe(true)
  })

  it('正则特殊字符不报错（用户输入安全）', async () => {
    const { splitHighlight: f } = await import('../../src/highlight')
    const parts = f('a+b*c', 'a+b*c')
    expect(parts.some((p) => p.hit)).toBe(true)
  })

  it('查询词不参与 HTML 渲染（纯文本分片）', async () => {
    const { splitHighlight: f } = await import('../../src/highlight')
    const parts = f('<img src=x onerror=alert(1)>', '<img')
    const hit = parts.find((p) => p.hit)
    expect(hit?.seg).toBe('<img') // 原样文本分片，非 innerHTML
  })
})

describe('store tab 判别联合', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('isDocumentPath 识别文档扩展名', async () => {
    const { isDocumentPath } = await import('../../src/store')
    expect(isDocumentPath('a.pdf')).toBe(true)
    expect(isDocumentPath('a.docx')).toBe(true)
    expect(isDocumentPath('a.txt')).toBe(true)
    expect(isDocumentPath('a.csv')).toBe(true)
    expect(isDocumentPath('a.md')).toBe(false)
    expect(isDocumentPath('a.png')).toBe(false)
  })

  it('openTab 对 md 走 readFile，对文档走 documentPreview', async () => {
    const api = await import('../../src/api')
    const readFile = vi.fn().mockResolvedValue({
      path: 'a.md', content: '# A', etag: 'e1', mtime_ns: 0, size: 1, bom: false, newline: '\n',
    })
    const documentPreview = vi.fn().mockResolvedValue({
      path: 'a.pdf', name: 'a.pdf', size: 100, chars: 50, truncated: false, text: 'pdf text',
    })
    vi.spyOn(api.api, 'readFile').mockImplementation(readFile)
    vi.spyOn(api.api, 'documentPreview').mockImplementation(documentPreview)

    const store = await import('../../src/store')
    await store.openTab('a.md')
    expect(store.state.tabs[0].kind).toBe('md')
    expect(readFile).toHaveBeenCalledTimes(1)

    await store.openTab('a.pdf')
    expect(store.state.tabs[1].kind).toBe('doc')
    expect(store.state.tabs[1].content).toBe('pdf text')
    expect(store.state.tabs[1].saveState).toBe('saved')
    expect(documentPreview).toHaveBeenCalledTimes(1)
  })

  it('closePathAndDescendants 关闭子路径标签', async () => {
    const store = await import('../../src/store')
    store.state.tabs = [
      { path: 'dir/a.md', name: 'a', kind: 'md', content: '', savedContent: '', etag: null, saveState: 'saved', error: '' },
      { path: 'dir/sub/b.md', name: 'b', kind: 'md', content: '', savedContent: '', etag: null, saveState: 'saved', error: '' },
      { path: 'other.md', name: 'o', kind: 'md', content: '', savedContent: '', etag: null, saveState: 'saved', error: '' },
    ]
    store.state.activePath = 'dir/a.md'
    store.closePathAndDescendants('dir')
    expect(store.state.tabs.map((t) => t.path)).toEqual(['other.md'])
  })
})

describe('workspace 持久化', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('device_id 生成且稳定', async () => {
    const ws = await import('../../src/workspace')
    const a = ws.getDeviceId()
    const b = ws.getDeviceId()
    expect(a).toBeTruthy()
    expect(a).toBe(b)
  })

  it('保存与恢复往返', async () => {
    const ws = await import('../../src/workspace')
    ws.saveWorkspace({
      tabs: ['a.md', 'b.md'],
      activePath: 'b.md',
      leftOpen: false,
      indexMode: 'files',
      rightDomain: 'assistant',
      marginaliaView: 'meta',
      assistantView: 'ai',
      expanded: new Set(['dir']),
    })
    await new Promise((r) => setTimeout(r, 600)) // 防抖 500ms
    const state = ws.loadWorkspace()
    expect(state?.tabs).toEqual(['a.md', 'b.md'])
    expect(state?.activePath).toBe('b.md')
    expect(state?.expanded).toEqual(['dir'])
  })

  it('桌面与移动端隔离', async () => {
    const ws = await import('../../src/workspace')
    ws.saveWorkspace({ tabs: ['desktop.md'], activePath: '', leftOpen: true, indexMode: 'files', rightDomain: 'marginalia', marginaliaView: 'meta', assistantView: 'ai', expanded: new Set() })
    await new Promise((r) => setTimeout(r, 600))
    // 模拟移动端视口
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }))
    const mobile = ws.loadWorkspace()
    expect(mobile).toBeNull()
    vi.unstubAllGlobals()
  })
})
