import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { ComponentPublicInstance } from 'vue'
import type { TemplateDetail, TemplateSummary } from '../../src/types'
import DirectoryPicker from '../../src/components/DirectoryPicker.vue'
import MarkdownPreview from '../../src/components/MarkdownPreview.vue'
import MobileWorkbenchBar from '../../src/components/MobileWorkbenchBar.vue'
import TemplateCenter from '../../src/components/TemplateCenter.vue'

const apiMock = vi.hoisted(() => ({
  templates: vi.fn(),
  templateDetail: vi.fn(),
  templateApply: vi.fn(),
  tree: vi.fn(),
}))

vi.mock('../../src/api', () => ({ api: apiMock }))

const meeting: TemplateSummary = {
  id: 'builtin:meeting-notes',
  source: 'builtin',
  title: '会议纪要',
  description: '记录会议议题、结论和行动项。',
  category: '工作',
  tags: ['会议', '协作'],
  icon: 'team',
  path: null,
  updated_at: '',
}

const personal: TemplateSummary = {
  id: 'custom:templates/学习/读书卡.md',
  source: 'custom',
  title: '读书卡',
  description: '整理阅读收获。',
  category: '学习',
  tags: ['阅读'],
  icon: 'book',
  path: 'templates/学习/读书卡.md',
  updated_at: '2026-08-13T09:00:00Z',
}

const meetingDetail: TemplateDetail = {
  ...meeting,
  content: '# {{title}}\n\n## 行动项\n\n- [ ] 跟进事项',
}

const catalog = {
  templates: [meeting, personal],
  categories: ['工作', '学习'],
}

let wrappers: VueWrapper<ComponentPublicInstance>[] = []

function trackedMount<T extends ComponentPublicInstance>(component: Parameters<typeof mount>[0], options?: Parameters<typeof mount>[1]): VueWrapper<T> {
  const wrapper = mount(component, options) as VueWrapper<T>
  wrappers.push(wrapper as VueWrapper<ComponentPublicInstance>)
  return wrapper
}

function buttonByText(wrapper: VueWrapper<ComponentPublicInstance>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  if (!button) throw new Error(`找不到按钮：${text}`)
  return button
}

beforeEach(() => {
  apiMock.templates.mockReset().mockResolvedValue(catalog)
  apiMock.templateDetail.mockReset().mockResolvedValue(meetingDetail)
  apiMock.templateApply.mockReset().mockResolvedValue({ path: '新会议.md' })
  apiMock.tree.mockReset().mockResolvedValue({ entries: [] })
})

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount())
  wrappers = []
  document.body.innerHTML = ''
})

describe('TemplateCenter', () => {
  it('加载目录，并按搜索词和来源筛选模板', async () => {
    const wrapper = trackedMount(TemplateCenter)
    await flushPromises()

    expect(apiMock.templates).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('会议纪要')
    expect(wrapper.text()).toContain('读书卡')

    await wrapper.get('input[type="search"]').setValue('阅读')
    expect(wrapper.text()).not.toContain('会议纪要')
    expect(wrapper.text()).toContain('读书卡')

    await wrapper.get('input[type="search"]').setValue('')
    const sourceButtons = wrapper.findAll('[aria-label="模板来源"] button')
    await sourceButtons.find((item) => item.text() === '内置')?.trigger('click')
    expect(wrapper.text()).toContain('会议纪要')
    expect(wrapper.text()).not.toContain('读书卡')

    await sourceButtons.find((item) => item.text() === '我的')?.trigger('click')
    expect(wrapper.text()).not.toContain('会议纪要')
    expect(wrapper.text()).toContain('读书卡')
  })

  it('显示加载错误并可重试', async () => {
    apiMock.templates
      .mockRejectedValueOnce(new Error('网络断开'))
      .mockResolvedValueOnce(catalog)

    const wrapper = trackedMount(TemplateCenter)
    await flushPromises()

    expect(wrapper.text()).toContain('模板加载失败：网络断开')
    await buttonByText(wrapper, '重新加载').trigger('click')
    await flushPromises()

    expect(apiMock.templates).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('会议纪要')
    expect(wrapper.text()).not.toContain('模板加载失败')
  })

  it('打开模板详情，并在根目录使用模板后发出 created', async () => {
    const wrapper = trackedMount(TemplateCenter, { attachTo: document.body })
    await flushPromises()

    await buttonByText(wrapper, '会议纪要').trigger('click')
    await flushPromises()

    expect(apiMock.templateDetail).toHaveBeenCalledWith('builtin:meeting-notes')
    expect(wrapper.get('h1').text()).toBe('会议纪要')
    expect(wrapper.text()).toContain('行动项')

    await buttonByText(wrapper, '使用此模板').trigger('click')
    await nextTick()

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    const nameInput = dialog?.querySelector<HTMLInputElement>('input')
    expect(nameInput).not.toBeNull()
    if (!nameInput) return
    nameInput.value = '新会议'
    nameInput.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    const createButton = Array.from(dialog?.querySelectorAll('button') ?? [])
      .find((button) => button.textContent?.includes('创建文档'))
    expect(createButton).toBeTruthy()
    createButton?.click()
    await flushPromises()

    expect(apiMock.templateApply).toHaveBeenCalledWith('builtin:meeting-notes', '新会议.md', '新会议')
    expect(wrapper.emitted('created')).toEqual([['新会议.md']])
  })
})

describe('MarkdownPreview', () => {
  it('渲染 Markdown，并移除脚本与事件处理属性', async () => {
    const wrapper = trackedMount(MarkdownPreview, {
      props: {
        content: '# 安全预览\n\n<strong onclick="alert(1)">正文</strong><script>alert(2)</script><img src="x" onerror="alert(3)">',
        currentPath: 'templates/demo.md',
      },
    })
    await flushPromises()

    const html = wrapper.get('article').element.innerHTML
    expect(html).toContain('<h1 id="安全预览">安全预览</h1>')
    expect(html).toContain('<strong>正文</strong>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onclick')
    expect(html).not.toContain('onerror')
  })
})

describe('DirectoryPicker', () => {
  it('支持选择根目录，并按需加载子目录', async () => {
    apiMock.tree.mockImplementation(async (path = '') => {
      if (!path) {
        return {
          entries: [
            { name: '项目', path: '项目', type: 'dir', size: 0, mtime_ns: 1 },
            { name: '忽略.md', path: '忽略.md', type: 'file', size: 10, mtime_ns: 1 },
          ],
        }
      }
      return {
        entries: [{ name: '子目录', path: '项目/子目录', type: 'dir', size: 0, mtime_ns: 1 }],
      }
    })

    const wrapper = trackedMount(DirectoryPicker, { props: { modelValue: '项目' } })
    await flushPromises()

    expect(apiMock.tree).toHaveBeenNthCalledWith(1, '')
    expect(wrapper.text()).toContain('知识库根目录')
    expect(wrapper.text()).toContain('项目')
    expect(wrapper.text()).not.toContain('忽略.md')

    await wrapper.get('button[aria-label="展开 项目"]').trigger('click')
    await flushPromises()
    expect(apiMock.tree).toHaveBeenNthCalledWith(2, '项目')
    expect(wrapper.text()).toContain('子目录')

    const root = wrapper.findAll('[role="treeitem"]').find((item) => item.text().includes('知识库根目录'))
    await root?.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toContainEqual([''])
  })
})

describe('MobileWorkbenchBar', () => {
  it('发出模板事件，并在 sheet 关闭后把焦点还给触发按钮', async () => {
    const wrapper = trackedMount(MobileWorkbenchBar, {
      attachTo: document.body,
      props: { sheet: 'none', templateActive: true },
    })

    await buttonByText(wrapper, '模板').trigger('click')
    expect(wrapper.emitted('templates')).toEqual([[]])

    const indexButton = buttonByText(wrapper, '目录')
    indexButton.element.focus()
    await indexButton.trigger('click')
    expect(wrapper.emitted('open')).toEqual([['index']])

    await wrapper.setProps({ sheet: 'index' })
    const sheetControl = document.createElement('button')
    document.body.append(sheetControl)
    sheetControl.focus()
    expect(document.activeElement).toBe(sheetControl)

    await wrapper.setProps({ sheet: 'none' })
    await nextTick()
    expect(document.activeElement).toBe(indexButton.element)
  })
})
