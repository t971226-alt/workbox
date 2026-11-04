"""Shared approved BOX artwork and Windows icon generation."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from orb_artwork import draw_orb, draw_mark, svg_bytes


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def icon_image(size: int) -> QImage:
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 4096:
        raise ValueError('图标边长必须是 1–4096 之间的整数')
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        draw_orb(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return image


def brand_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(icon_image(size)))
    return icon


def write_ico(path: Path) -> None:
    """写入七档无损 PNG 帧，兼容 Windows 高 DPI 图标选择。"""
    frames = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not icon_image(size).save(buffer, 'PNG'):
            raise RuntimeError(f'无法生成 {size}px 图标')
        frames.append(bytes(buffer.data()))
    header = struct.pack('<HHH', 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = []
    for size, frame in zip(ICON_SIZES, frames):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0,
                                   1, 32, len(frame), offset))
        offset += len(frame)
    Path(path).write_bytes(header + b''.join(entries) + b''.join(frames))
