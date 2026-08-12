/**
 * E2E 冒烟：初始化 → 新建笔记 → 搜索 → 回收站。
 * 依赖后端 + 已构建前端（见 playwright.config.ts webServer）。
 */
import { expect, test } from '@playwright/test'

const PASSWORD = 'e2e-password-123'

test.describe.configure({ mode: 'serial' })

test('初始化 → 新建笔记 → 搜索 → 回收站', async ({ page }) => {
  // 初始化
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '拾墨' })).toBeVisible()
  await page.getByRole('textbox', { name: '访问密码' }).fill(PASSWORD)
  await page.getByRole('textbox', { name: '确认密码' }).fill(PASSWORD)
  await page.getByRole('button', { name: '初始化并进入' }).click()
  await expect(page.getByText('目录索引')).toBeVisible()

  // 新建笔记
  await page.getByRole('button', { name: '笔记', exact: true }).click()
  await page.getByPlaceholder('笔记名（自动补 .md）').fill('E2E测试')
  await page.getByRole('button', { name: '确定' }).click()
  await expect(page.getByRole('heading', { name: 'E2E测试.md' })).toBeVisible()

  // 编辑器输入（CodeMirror）
  const editor = page.locator('.cm-content')
  await editor.click()
  await page.keyboard.press('ControlOrMeta+ArrowDown')
  await page.keyboard.type('E2E 独特检索词 uniqe2e42。')
  await expect(page.locator('.save-state')).toHaveText(/已保存/, { timeout: 5000 })

  // 搜索
  await page.getByRole('button', { name: '搜索' }).click()
  await page.getByRole('textbox', { name: '全文搜索' }).fill('uniqe2e42')
  await expect(page.getByRole('option').first()).toContainText('E2E测试', { timeout: 5000 })

  // 删除 → 回收站
  await page.getByRole('button', { name: '文件' }).click()
  const row = page.locator('[data-tree-row]').filter({ hasText: 'E2E测试.md' })
  await row.hover()
  // 注册对话框处理后再点击删除
  const dialogPromise = page.waitForEvent('dialog')
  await row.getByTitle('删除文件').click()
  const dialog = await dialogPromise
  await dialog.accept()
  // 树中该行应消失
  await expect(page.locator('[data-tree-row]').filter({ hasText: 'E2E测试.md' })).toHaveCount(0, { timeout: 5000 })

  await page.getByRole('button', { name: '回收站' }).click()
  await expect(page.locator('.trash-row').filter({ hasText: 'E2E测试.md' })).toBeVisible()
})
