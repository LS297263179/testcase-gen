"""V2 Ingestion Layer - 归一化多源输入 → (raw_text, images)。

复用 V1 core.reader（read_text/read_excel/image_to_base64/is_image/get_image_media_type）。
支持：Markdown/TXT/Excel 文本 + 图片（多模态）。PDF/Word 延后（V1 亦未支持，符合范围控制）。
对应蓝图 Ingestion Layer。
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.reader import get_image_media_type, image_to_base64, is_image, read_excel, read_text

logger = logging.getLogger("v2.ingestion")

EXCEL_EXTS = {".xlsx", ".xls"}


def normalize_text(text: str) -> str:
    """归一化文本：统一换行符、压缩连续空行、去首尾空白（Markdown/纯文本通用）"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    prev_blank = False
    for line in text.split("\n"):
        if not line.strip():
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(line.rstrip())
            prev_blank = False
    return "\n".join(out).strip()


def ingest_paths(paths: list[str] | None) -> tuple[str, list[dict]]:
    """把文件路径列表归一化为 (合并文本, 图片列表)。

    图片 → base64 dict（多模态输入）；Excel → 文本；其余按 Markdown/TXT 读。
    单个文件读取失败只跳过并告警，不中断整体摄取。
    """
    text_parts: list[str] = []
    images: list[dict] = []
    for p in paths or []:
        ps = str(p)
        suffix = Path(ps).suffix.lower()
        try:
            if is_image(ps):
                images.append(
                    {
                        "data": image_to_base64(ps),
                        "media_type": get_image_media_type(ps),
                        "filename": Path(ps).name,
                    }
                )
            elif suffix in EXCEL_EXTS:
                text_parts.append(read_excel(ps))
            else:
                text_parts.append(read_text(ps))
        except Exception as e:  # noqa: BLE001 - 单文件摄取失败降级为跳过
            logger.warning("摄取文件失败，跳过 %s: %s", ps, e)
    return normalize_text("\n\n".join(t for t in text_parts if t)), images


def ingest(text: str | None = None, paths: list[str] | None = None) -> tuple[str, list[dict]]:
    """统一入口：直接文本 + 文件路径 → (raw_text, images)"""
    file_text, images = ingest_paths(paths)
    parts = [normalize_text(text or "")]
    if file_text:
        parts.append(file_text)
    return normalize_text("\n\n".join(p for p in parts if p)), images
