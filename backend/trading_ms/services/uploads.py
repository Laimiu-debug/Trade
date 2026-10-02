"""Bound uploaded screenshots before any provider call or database mutation."""
from fastapi import HTTPException
import io
from PIL import Image, UnidentifiedImageError
import re

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024


async def read_screenshot(file) -> tuple[bytes, str]:
    try:
        contents = await file.read(MAX_SCREENSHOT_BYTES + 1)
    finally:
        await file.close()
    if not contents or len(contents) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, '截图为空或超过 8 MiB 限制')
    mime = (file.content_type or 'image/png').lower()
    if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise HTTPException(415, '请选择 PNG、JPEG 或 WebP 截图')
    return contents, mime


async def read_review_image(file) -> tuple[bytes, str]:
    try:
        contents = await file.read(MAX_SCREENSHOT_BYTES + 1)
    finally:
        await file.close()
    if not contents or len(contents) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, '图片为空或超过 8 MiB 限制')
    try:
        with Image.open(io.BytesIO(contents)) as image:
            extensions = {'PNG': 'png', 'JPEG': 'jpg', 'GIF': 'gif', 'WEBP': 'webp'}
            extension = extensions.get(image.format)
            if not extension or image.width * image.height > 25_000_000:
                raise HTTPException(400, '图片格式或尺寸不受支持')
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, '图片文件校验失败') from exc
    return contents, extension


def attachment_name(url: str) -> str | None:
    if not isinstance(url, str): return None
    for prefix in ('/journal-app/uploads/', '/uploads/'):
        if url.startswith(prefix):
            name = url[len(prefix):]
            if name not in {'.', '..'} and re.fullmatch(r'[A-Za-z0-9._-]+', name):
                return name
    return None


def attachment_url(url: str) -> str:
    name = attachment_name(url)
    return '/journal-app/uploads/' + name if name else url
