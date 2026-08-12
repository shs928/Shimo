"""安全图片下载与校验（SSRF 防护）。

- 仅允许 http/https。
- DNS 解析后拒绝 loopback / private / link-local / multicast / reserved / unspecified。
- 重定向逐跳重新校验目标 IP。
- 流式下载 + 最大字节数上限。
- Content-Type 必须为 image/*。
- Pillow 校验真实图片格式；按真实格式保存扩展名，不统一伪装 PNG。
"""
from __future__ import annotations

import base64
import io
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB

# Pillow 格式 → 扩展名
_PIL_EXT = {
    "PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp",
    "BMP": ".bmp", "AVIF": ".avif", "TIFF": ".tiff",
}


class DownloadError(RuntimeError):
    pass


def _ip_allowed(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_url(url: str) -> str:
    """校验 URL 协议并解析主机；拒绝非 http(s) 与危险目标。返回 host。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DownloadError(f"仅支持 http/https：{url[:60]}")
    host = parsed.hostname
    if not host:
        raise DownloadError("URL 缺少主机名")
    return host


def _check_host(host: str) -> None:
    """解析 DNS 并校验所有解析结果 IP。"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DownloadError(f"域名解析失败：{host}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise DownloadError(f"域名无解析结果：{host}")
    for ip in ips:
        if not _ip_allowed(ip):
            raise DownloadError(f"拒绝访问非公网地址：{host}（{ip}）")


def _validate_redirect(url: str) -> None:
    """重定向目标同样校验协议与目标 IP。"""
    _check_host(_check_url(url))


def download_image(url: str, max_bytes: int = _MAX_IMAGE_BYTES) -> bytes:
    """流式下载图片：逐跳校验重定向、Content-Type image/*、大小上限。"""
    import httpx

    _validate_redirect(url)
    try:
        with httpx.Client(follow_redirects=False, timeout=60) as client:
            current = url
            for _ in range(5):  # 最多 5 跳
                resp = client.get(current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise DownloadError("重定向缺少目标")
                    current = str(httpx.URL(current).join(location))
                    _validate_redirect(current)
                    continue
                if resp.status_code != 200:
                    raise DownloadError(f"下载失败：HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if not ctype.startswith("image/"):
                    raise DownloadError(f"非图片 Content-Type：{ctype or '未知'}")
                # 流式读取并限制大小
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError(f"图片超过 {max_bytes // (1024 * 1024)}MB 上限")
                    chunks.append(chunk)
                return b"".join(chunks)
            raise DownloadError("重定向次数过多")
    except DownloadError:
        raise
    except httpx.HTTPError as exc:
        raise DownloadError(f"下载失败：{exc}") from exc


def validate_image(data: bytes, max_bytes: int = _MAX_IMAGE_BYTES) -> str:
    """Pillow 校验真实图片格式；返回扩展名（含点）。"""
    if not data:
        raise DownloadError("图片数据为空")
    if len(data) > max_bytes:
        raise DownloadError(f"图片超过 {max_bytes // (1024 * 1024)}MB 上限")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt = (img.format or "").upper()
    except Exception as exc:
        raise DownloadError(f"不是有效的图片文件：{exc}") from exc
    ext = _PIL_EXT.get(fmt)
    if ext is None:
        raise DownloadError(f"不支持的图片格式：{fmt or '未知'}")
    return ext


def decode_b64(data: str, max_bytes: int = _MAX_IMAGE_BYTES) -> bytes:
    """base64 图片解码：大小限制 + 真实格式校验。"""
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise DownloadError("base64 图片数据无效") from exc
    if not raw:
        raise DownloadError("图片数据为空")
    if len(raw) > max_bytes:
        raise DownloadError(f"图片超过 {max_bytes // (1024 * 1024)}MB 上限")
    return raw
