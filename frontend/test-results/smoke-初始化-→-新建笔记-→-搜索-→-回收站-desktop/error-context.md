# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: smoke.spec.ts >> 初始化 → 新建笔记 → 搜索 → 回收站
- Location: e2e/smoke.spec.ts:11:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('目录索引')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('目录索引')

```

```yaml
- heading "拾墨" [level=1]
- text: 收集知识碎片
- paragraph: 首次使用，请设置访问密码
- textbox "访问密码": e2e-password-123
- textbox "确认密码": e2e-password-123
- paragraph: Internal Server Error
- button "初始化并进入"
```

# Test source

```ts
  1  | /**
  2  |  * E2E 冒烟：初始化 → 新建笔记 → 搜索 → 回收站。
  3  |  * 依赖后端 + 已构建前端（见 playwright.config.ts webServer）。
  4  |  */
  5  | import { expect, test } from '@playwright/test'
  6  | 
  7  | const PASSWORD = 'e2e-password-123'
  8  | 
  9  | test.describe.configure({ mode: 'serial' })
  10 | 
  11 | test('初始化 → 新建笔记 → 搜索 → 回收站', async ({ page }) => {
  12 |   // 初始化
  13 |   await page.goto('/')
  14 |   await expect(page.getByRole('heading', { name: '拾墨' })).toBeVisible()
  15 |   await page.getByRole('textbox', { name: '访问密码' }).fill(PASSWORD)
  16 |   await page.getByRole('textbox', { name: '确认密码' }).fill(PASSWORD)
  17 |   await page.getByRole('button', { name: '初始化并进入' }).click()
> 18 |   await expect(page.getByText('目录索引')).toBeVisible()
     |                                        ^ Error: expect(locator).toBeVisible() failed
  19 | 
  20 |   // 新建笔记
  21 |   await page.getByRole('button', { name: '笔记', exact: true }).click()
  22 |   await page.getByPlaceholder('笔记名（自动补 .md）').fill('E2E测试')
  23 |   await page.getByRole('button', { name: '确定' }).click()
  24 |   await expect(page.getByRole('heading', { name: 'E2E测试.md' })).toBeVisible()
  25 | 
  26 |   // 编辑器输入（CodeMirror）
  27 |   const editor = page.locator('.cm-content')
  28 |   await editor.click()
  29 |   await page.keyboard.press('ControlOrMeta+ArrowDown')
  30 |   await page.keyboard.type('E2E 独特检索词 uniqe2e42。')
  31 |   await expect(page.locator('.save-state')).toHaveText(/已保存/, { timeout: 5000 })
  32 | 
  33 |   // 搜索
  34 |   await page.getByRole('button', { name: '搜索' }).click()
  35 |   await page.getByRole('textbox', { name: '全文搜索' }).fill('uniqe2e42')
  36 |   await expect(page.getByRole('option').first()).toContainText('E2E测试', { timeout: 5000 })
  37 | 
  38 |   // 删除 → 回收站
  39 |   await page.getByRole('button', { name: '文件' }).click()
  40 |   const row = page.locator('[data-tree-row]').filter({ hasText: 'E2E测试.md' })
  41 |   await row.hover()
  42 |   // 注册对话框处理后再点击删除
  43 |   const dialogPromise = page.waitForEvent('dialog')
  44 |   await row.getByTitle('删除文件').click()
  45 |   const dialog = await dialogPromise
  46 |   await dialog.accept()
  47 |   // 树中该行应消失
  48 |   await expect(page.locator('[data-tree-row]').filter({ hasText: 'E2E测试.md' })).toHaveCount(0, { timeout: 5000 })
  49 | 
  50 |   await page.getByRole('button', { name: '回收站' }).click()
  51 |   await expect(page.locator('.trash-row').filter({ hasText: 'E2E测试.md' })).toBeVisible()
  52 | })
  53 | 
```