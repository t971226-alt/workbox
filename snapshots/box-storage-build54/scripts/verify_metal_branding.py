"""Verify the approved bitmap survives bundling and all native icon surfaces."""
from pathlib import Path
import hashlib


def verify(root):
    from PySide6.QtCore import Qt,QRectF
    from PySide6.QtGui import QImage,QPainter,QColor,QFont
    from branding import icon_image,brand_icon,ICON_SIZES
    from orb_artwork import artwork_path,APPROVED_SHA256
    from floating import FloatingBall
    from PySide6.QtWidgets import QApplication
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    assert hashlib.sha256(artwork_path().read_bytes()).hexdigest()==APPROVED_SHA256
    icon=brand_icon();assert len(icon.availableSizes())==len(ICON_SIZES)
    ball=FloatingBall()
    try:
        actual=QImage(76,76,QImage.Format_ARGB32_Premultiplied);actual.fill(Qt.transparent);ball.render(actual)
        assert actual==icon_image(76)
        sheet=QImage(1000,360,QImage.Format_ARGB32_Premultiplied);p=QPainter(sheet);p.setRenderHint(QPainter.SmoothPixmapTransform)
        for y,bg,fg in [(0,'#f2f3f5','#30343a'),(180,'#252a30','#e2e7ec')]:
            p.fillRect(0,y,1000,180,QColor(bg))
            for x,size in zip((55,170,290,420,570,770),(16,24,32,48,76,128)):
                image=icon_image(size);assert not image.isNull() and image.pixelColor(0,0).alpha()==0
                p.drawImage(QRectF(x-size/2,y+70-size/2,size,size),image)
                f=QFont('Microsoft YaHei UI');f.setPixelSize(13);p.setFont(f);p.setPen(QColor(fg));p.drawText(QRectF(x-40,y+140,80,24),Qt.AlignCenter,str(size)+' px')
        p.end();assert sheet.save(str(root/'金属图标-实际尺寸.png'))
    finally:ball.hide();ball.deleteLater();QApplication.processEvents()
    return dict(approved_metal_artwork_is_bundled_and_shared_by_orb_and_icons=True)
