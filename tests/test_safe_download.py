"""3.5 安全下载器测试：SSRF 防护、MIME、大小、Pillow 校验、格式扩展名。"""
from __future__ import annotations

import io

import pytest

from app.services.safe_download import (
    DownloadError,
    _check_host,
    decode_b64,
    download_image,
    validate_image,
)


def test_check_host_rejects_private_and_loopback():
    for bad in ["127.0.0.1", "localhost", "::1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "0.0.0.0"]:
        with pytest.raises(DownloadError, match="非公网"):
            _check_host(bad)


def test_check_host_allows_public():
    # 只验证不抛错（不依赖外网可达性；仅做 DNS 解析校验逻辑）
    try:
        _check_host("1.1.1.1")
    except DownloadError as exc:
        # 域名解析失败也算通过路径（此处是字面 IP，不应失败）
        pytest.fail(f"公网 IP 不应被拒绝：{exc}")


def test_validate_image_rejects_non_image():
    with pytest.raises(DownloadError, match="不是有效"):
        validate_image(b"not an image at all")
    with pytest.raises(DownloadError, match="为空"):
        validate_image(b"")


def test_validate_image_detects_real_format():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    assert validate_image(buf.getvalue()) == ".png"

    buf2 = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf2, format="JPEG")
    assert validate_image(buf2.getvalue()) == ".jpg"


def test_validate_image_size_limit():
    with pytest.raises(DownloadError, match="上限"):
        validate_image(b"\x00" * 1024, max_bytes=100)


def test_decode_b64_rejects_invalid():
    with pytest.raises(DownloadError, match="无效"):
        decode_b64("!!!not-base64!!!")
    with pytest.raises(DownloadError, match="为空"):
        decode_b64("")
    with pytest.raises(DownloadError, match="上限"):
        decode_b64(__import__("base64").b64encode(b"x" * 1024).decode(), max_bytes=100)


def test_download_image_rejects_non_http():
    with pytest.raises(DownloadError, match="仅支持 http"):
        download_image("file:///etc/passwd")
    with pytest.raises(DownloadError, match="仅支持 http"):
        download_image("ftp://example.com/x.png")


def test_download_image_rejects_private_target(monkeypatch):
    """重定向到内网地址：逐跳校验拒绝。"""
    import httpx

    calls = []

    class FakeResp:
        status_code = 302
        headers = {"location": "http://127.0.0.1:8080/steal.png"}

        def iter_bytes(self, *a, **k):
            return iter([])

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(url)
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(DownloadError):
        download_image("https://1.1.1.1/redirect")  # 字面公网 IP 通过首检，重定向到内网被拒
    assert calls  # 确实发起了第一次请求


def test_download_image_rejects_non_image_content_type(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}

        def iter_bytes(self, *a, **k):
            return iter([b"<html>"])

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(DownloadError, match="非图片"):
        download_image("https://1.1.1.1/not-image")
