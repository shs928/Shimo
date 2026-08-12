# 拾墨 Shimo — Windows onedir 构建脚本
# 前置：Python 3.10+、Node 18+、已安装 pyinstaller（pip install pyinstaller）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build_win.ps1

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "=== [1/3] 构建前端 ==="
Push-Location frontend
npm install
npm run build
Pop-Location

Write-Host "=== [2/3] 安装后端依赖（含 pyinstaller） ==="
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

Write-Host "=== [3/3] PyInstaller onedir 打包 ==="
& ".venv\Scripts\python.exe" -m PyInstaller shimo.spec --noconfirm

Write-Host "完成：dist\Shimo\Shimo.exe"
