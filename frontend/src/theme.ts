/* 主题切换：日光 / 午夜（思源 daylight / midnight）
   - 用户选择持久化于 localStorage('shimo:theme')
   - 'system' 模式跟随 prefers-color-scheme 并实时响应系统切换
   - 解析结果写入 <html data-theme>，tokens.css 据此换色 */

export type ThemeMode = 'system' | 'daylight' | 'midnight'
export type ThemeName = 'daylight' | 'midnight'

const STORAGE_KEY = 'shimo:theme'

function systemTheme(): ThemeName {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'midnight' : 'daylight'
}

export function readThemeMode(): ThemeMode {
  const v = localStorage.getItem(STORAGE_KEY)
  return v === 'daylight' || v === 'midnight' || v === 'system' ? v : 'system'
}

function apply(mode: ThemeMode): void {
  const effective = mode === 'system' ? systemTheme() : mode
  document.documentElement.dataset.theme = effective
  // 通知 mermaid 等依赖主题的渲染器重绘
  window.dispatchEvent(new CustomEvent('shimo-theme-changed', { detail: effective }))
}

/** 入口初始化：应用持久化选择并订阅系统主题变化 */
export function initTheme(): void {
  apply(readThemeMode())
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (readThemeMode() === 'system') apply('system')
  })
}

/** 在日光 / 午夜之间切换（显式选择，持久化） */
export function toggleTheme(): void {
  const current = document.documentElement.dataset.theme === 'midnight' ? 'midnight' : 'daylight'
  const next: ThemeName = current === 'midnight' ? 'daylight' : 'midnight'
  localStorage.setItem(STORAGE_KEY, next)
  apply(next)
}

/** 当前生效主题（供图标显示） */
export function currentTheme(): ThemeName {
  return document.documentElement.dataset.theme === 'midnight' ? 'midnight' : 'daylight'
}
