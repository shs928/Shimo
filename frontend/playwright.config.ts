import { defineConfig, devices } from '@playwright/test'

// E2E：需要后端运行（webServer 自动启动 venv 后端 + 已构建的 frontend/dist）
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8858',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: '.venv/bin/python -m app',
    url: 'http://127.0.0.1:8858/health/live',
    reuseExistingServer: false,
    timeout: 30_000,
    cwd: '..',
    env: {
      SHIMO_PORT: '8858',
      SHIMO_VAULT_PATH: '.e2e-vault',
      SHIMO_DATA_PATH: '.e2e-data',
    },
  },
})
