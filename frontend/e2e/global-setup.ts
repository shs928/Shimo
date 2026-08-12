/** E2E 全局准备：清理后端数据/知识库目录，保证每次测试从全新状态开始。 */
import { rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// 项目根 = frontend/e2e 的上级两级
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

export default function globalSetup(): void {
  rmSync(path.join(root, '.e2e-data'), { recursive: true, force: true })
  rmSync(path.join(root, '.e2e-vault'), { recursive: true, force: true })
}
