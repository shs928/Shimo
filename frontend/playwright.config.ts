import { defineConfig, devices } from '@playwright/test'

const python = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'
const cleanE2eState =
  'node -e "const fs=require(\'node:fs\');for(const dir of [\'.e2e-data\',\'.e2e-vault\'])fs.rmSync(dir,{recursive:true,force:true})"'
const webServerCommand = `${cleanE2eState} && npm --prefix frontend run build && ${python} -m app`

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8858',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      grep: /\[desktop\]/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile',
      grep: /\[mobile\]/,
      use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: {
    command: webServerCommand,
    url: 'http://127.0.0.1:8858/health/live',
    reuseExistingServer: false,
    timeout: 120_000,
    cwd: '..',
    env: {
      SHIMO_PORT: '8858',
      SHIMO_VAULT_PATH: '.e2e-vault',
      SHIMO_DATA_PATH: '.e2e-data',
    },
  },
})
