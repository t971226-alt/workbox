"""The approved seamless polished-metal BOX badge, shared by every app surface."""
from pathlib import Path
from functools import lru_cache
from base64 import b64encode
from PySide6.QtGui import QImage,QPainter

APPROVED_SHA256='fbdda46fb251c38de82caa13f2c1fe488b802fda86c0c7e533f9ad28fefb4aac'

def artwork_path():
    # PyInstaller sets this module's __file__ inside its bundled data directory.
    return Path(__file__).resolve().parent/'assets/workbox-metal.png'

@lru_cache(maxsize=1)
def artwork_image():
    image=QImage(str(artwork_path()))
    if image.isNull():raise RuntimeError('BOX 图标资源缺失，请重新安装完整程序。')
    return image.convertToFormat(QImage.Format_ARGB32_Premultiplied)

def draw_orb(painter,bounds):
    painter.save()
    try:
        painter.setRenderHint(QPainter.SmoothPixmapTransform,True)
        painter.drawImage(bounds,artwork_image())
    finally:painter.restore()

def draw_mark(painter,bounds):
    draw_orb(painter,bounds)

def svg_bytes(*,background=True):
    """Self-contained SVG wrapper retains the approved raster's metal reflections."""
    data=b64encode(artwork_path().read_bytes()).decode('ascii')
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="76" height="76" viewBox="0 0 76 76"><image width="76" height="76" '
        'xlink:href="data:image/png;base64,'+data+'"/></svg>\n').encode('utf-8')
