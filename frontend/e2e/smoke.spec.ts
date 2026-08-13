/**
 * E2E 冒烟：桌面与移动项目串行使用同一套后端状态，但使用各自唯一的测试名和文档名。
 */
import { expect, test, type Page } from '@playwright/test'

const PASSWORD = 'e2e-password-123'

test.describe.configure({ mode: 'serial' })

async function authenticate(page: Page, mobile: boolean): Promise<void> {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '拾墨' })).toBeVisible()

  await page.getByLabel('访问密码').fill(PASSWORD)
  const confirm = page.getByLabel('确认密码')
  if (await confirm.isVisible()) {
    await confirm.fill(PASSWORD)
    await page.getByRole('button', { name: '初始化并进入' }).click()
  } else {
    await page.getByRole('button', { name: '登录', exact: true }).click()
  }

  if (mobile) {
    await expect(page.getByRole('navigation', { name: '移动工作台' })).toBeVisible()
  } else {
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  }
}

async function openDirectory(page: Page, mobile: boolean): Promise<void> {
  const sidebar = page.locator('aside.sidebar')
  if (mobile && !(await sidebar.isVisible())) {
    await page
      .getByRole('navigation', { name: '移动工作台' })
      .getByRole('button', { name: '目录', exact: true })
      .click()
  }
  await expect(sidebar).toBeVisible()
}

async function runSmoke(page: Page, mobile: boolean, documentName: string): Promise<void> {
  await authenticate(page, mobile)
  await openDirectory(page, mobile)

  const sidebar = page.locator('aside.sidebar')
  await sidebar.getByRole('button', { name: '笔记', exact: true }).click()
  await sidebar.getByPlaceholder('笔记名（自动补 .md）').fill(documentName)
  await sidebar.getByRole('button', { name: '确定' }).click()
  await expect(page.getByRole('heading', { name: `${documentName}.md` })).toBeVisible()
  if (mobile) {
    await sidebar.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(sidebar).not.toBeVisible()
  }

  const editor = page.locator('.cm-content')
  await editor.click()
  await page.keyboard.press('ControlOrMeta+ArrowDown')
  await page.keyboard.type(`E2E 独特检索词 ${documentName}。`)
  await expect(page.locator('.save-state')).toHaveText(/已保存/, { timeout: 5_000 })

  await openDirectory(page, mobile)
  await sidebar.getByRole('button', { name: '搜索', exact: true }).click()
  await sidebar.getByRole('textbox', { name: '全文搜索' }).fill(documentName)
  await expect(sidebar.getByRole('option').first()).toContainText(documentName, { timeout: 5_000 })

  await sidebar.getByRole('button', { name: '文件', exact: true }).click()
  const row = sidebar.locator('[data-tree-row]').filter({ hasText: `${documentName}.md` })
  await row.hover()
  page.once('dialog', (dialog) => void dialog.accept())
  await row.getByTitle('删除文件').click()
  await expect(sidebar.locator('[data-tree-row]').filter({ hasText: `${documentName}.md` })).toHaveCount(0, {
    timeout: 5_000,
  })

  await sidebar.getByRole('button', { name: '回收站', exact: true }).click()
  await expect(sidebar.locator('.trash-row').filter({ hasText: `${documentName}.md` })).toBeVisible()
}

async function runTemplateFlow(page: Page, mobile: boolean, documentName: string): Promise<void> {
  await authenticate(page, mobile)

  if (mobile) {
    await page
      .getByRole('navigation', { name: '移动工作台' })
      .getByRole('button', { name: '模板', exact: true })
      .click()
  } else {
    await page
      .getByRole('navigation', { name: '主导航' })
      .getByRole('button', { name: '模板中心' })
      .click()
  }

  await expect(page.getByRole('heading', { name: '模板中心' })).toBeVisible()
  await page.getByRole('button', { name: /会议纪要/ }).click()
  await expect(page.getByRole('heading', { name: '会议纪要', exact: true })).toBeVisible()
  await page.getByRole('button', { name: /使用此模板/ }).click()

  const dialog = page.getByRole('dialog', { name: '使用模板创建文档' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('文档名').fill(documentName)
  await dialog.getByRole('treeitem', { name: '知识库根目录', exact: true }).click()
  await dialog.getByRole('button', { name: '创建文档' }).click()

  await expect(page.getByRole('heading', { name: `${documentName}.md`, exact: true })).toBeVisible()
  await expect(page.locator('.cm-content')).toContainText(documentName)
  await expect(page.locator('.cm-content')).toContainText('会议议题')
}

test('[desktop] 初始化、笔记、搜索与回收站', async ({ page }) => {
  await runSmoke(page, false, 'E2E桌面测试')
})

test('[desktop] 从会议模板在根目录创建文档', async ({ page }) => {
  await runTemplateFlow(page, false, 'E2E桌面会议')
})

test('[mobile] 登录、目录抽屉、笔记、搜索与回收站', async ({ page }) => {
  await runSmoke(page, true, 'E2E移动测试')
})

test('[mobile] 从底部模板入口在根目录创建文档', async ({ page }) => {
  await runTemplateFlow(page, true, 'E2E移动会议')
})
