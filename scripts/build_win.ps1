# 拾墨 Shimo — Windows onedir 构建脚本（发行版）
# 前置：Python 3.10+、Node 18+
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build_win.ps1 [-Version v0.1.0]
# 产物：dist\Shimo-<版本>-win64.zip（解压后双击 Shimo.exe 即用，不含任何源码 .py 文件）
param(
    [string]$Version = "v0.1.0"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "=== [1/4] 构建前端（frontend/dist） ==="
Push-Location frontend
if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
npm run build
Pop-Location

Write-Host "=== [2/4] 安装后端依赖（含 pyinstaller） ==="
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

Write-Host "=== [3/4] PyInstaller onedir 打包 ==="
& ".venv\Scripts\python.exe" -m PyInstaller shimo.spec --noconfirm

# 源码泄漏检查：项目源码（app\ 包）不应以 .py 形式出现在发行包内（均编译进 PYZ 归档）。
# 注意：第三方库自带的 .py 数据文件（如 cv2 的包装代码）属正常，不在此检查范围。
$pyFiles = Get-ChildItem "dist\Shimo" -Recurse -Filter *.py -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\app\\' }
if ($pyFiles) {
    Write-Host "[警告] dist\Shimo 下发现 $($pyFiles.Count) 个项目源码 .py 文件：" -ForegroundColor Yellow
    $pyFiles | Select-Object -First 10 | ForEach-Object { Write-Host "  $($_.FullName)" }
} else {
    Write-Host "[通过] 发行包内未发现项目源码 .py 文件（第三方库自带 .py 数据不在此列）。"
}

Write-Host "=== [4/4] 打包 zip ==="
$zipPath = "dist\Shimo-$Version-win64.zip"
& ".venv\Scripts\python.exe" scripts\zip_dist.py "dist\Shimo" $zipPath
if ($LASTEXITCODE -ne 0) { throw "zip 打包失败" }

Write-Host ""
Write-Host "完成：$zipPath"
Write-Host "发布给用户：解压后双击 Shimo.exe，浏览器自动打开 http://127.0.0.1:8848"
Write-Host "（首次运行 Windows SmartScreen 可能提示未知发布者，选择“更多信息 → 仍要运行”）"
