# -*- mode: python ; coding: utf-8 -*-
# 拾墨 Shimo — PyInstaller 打包配置（Windows onedir）
# 构建：pyinstaller shimo.spec
# 产物：dist/Shimo/Shimo.exe（onedir，含 frontend/dist 静态资源）
#
# 易踩坑点（均已在此处理）：
# - "app.main" 必须显式 hidden import：uvicorn 以工厂字符串 "app.main:create_app"
#   导入应用，PyInstaller 静态分析无法发现，漏掉则 exe 启动即 ModuleNotFoundError。
# - rapidocr_onnxruntime / pypdfium2 需 collect_all：OCR 模型 *.onnx、config.yaml
#   与 pdfium DLL 是包内数据，默认不会进包（exe 里 OCR 必挂）。
# - upx=False：UPX 压缩 onnxruntime 等原生 DLL 可能导致运行时加载失败。
# - keyring 后端经 entry point 发现，冻结包内显式带上 Windows 后端。

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)


def _collect(package: str):
    """收集包的数据 / 二进制 / 隐藏导入（含子包，如 pypdfium2_raw）。"""
    datas, binaries, hiddenimports = collect_all(package)
    return datas, binaries, hiddenimports


extra_datas, extra_binaries, extra_hidden = [], [], []
for _pkg in ("rapidocr_onnxruntime", "pypdfium2", "pypdfium2_raw"):
    _d, _b, _h = _collect(_pkg)
    extra_datas += _d
    extra_binaries += _b
    extra_hidden += _h

a = Analysis(
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=extra_binaries,
    datas=[
        # 前端构建产物（FastAPI 静态托管）
        (str(ROOT / "frontend" / "dist"), "frontend/dist"),
        *extra_datas,
    ],
    hiddenimports=[
        # uvicorn 运行时字符串导入应用工厂
        "app.main",
        # keyring 后端（冻结包内 entry point 发现不可靠，显式带上）
        "keyring.backends.Windows",
        "keyring.backends.fail",
        *extra_hidden,
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shimo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 便携版保留控制台便于查看日志；正式发布可改 False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Shimo",
)
