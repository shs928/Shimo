"""发行目录打包脚本：把 PyInstaller onedir 产物打成 zip（保留顶层目录名）。

用法：python scripts/zip_dist.py <src_dir> <out_zip>
不依赖系统 tar/7z，避免各平台/Shell 工具差异（如 Git Bash 的 GNU tar 不支持 zip）。
"""
import sys
import zipfile
from pathlib import Path


def main() -> None:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src.parent))
    print(f"zip 完成：{out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
