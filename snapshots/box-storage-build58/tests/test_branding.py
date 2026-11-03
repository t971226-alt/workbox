import struct
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

import branding


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_vector_assets_are_valid_and_match_runtime():
    root = Path(__file__).resolve().parents[1]
    for path in (root / 'assets/workbox.svg', root / 'website/public/favicon.svg'):
        data = path.read_bytes()
        assert QSvgRenderer(QByteArray(data)).isValid()
        assert data == branding.svg_bytes()


@pytest.mark.parametrize('size', [16, 24, 32, 48, 64, 128, 256])
def test_metal_icon_has_opaque_center_and_transparent_corners(qapp, size):
    image = branding.icon_image(size)
    assert image.size().width() == image.size().height() == size
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(size // 2, size // 8).alpha() > 240
    assert image.pixelColor(size//2,size//2).alpha()>240
    values=[image.pixelColor(x,y).lightness() for y in range(size) for x in range(size) if image.pixelColor(x,y).alpha()>240]
    assert max(values)-min(values)>70


def test_windows_ico_contains_seven_decodable_native_sizes(qapp, tmp_path):
    path = tmp_path / 'WorkBox.ico'
    branding.write_ico(path)
    data = path.read_bytes()
    assert struct.unpack_from('<HHH', data) == (0, 1, 7)
    for index, size in enumerate(branding.ICON_SIZES):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + index * 16
        )
        assert (width or 256, height or 256) == (size, size)
        assert (planes, bits) == (1, 32)
        image = QImage.fromData(data[offset:offset + length], 'PNG')
        assert not image.isNull()
        assert image.width() == image.height() == size
    assert not QImage(str(path)).isNull()


def test_runtime_icon_exposes_native_sizes(qapp):
    from ui import app_icon
    icon = app_icon()
    assert {(s.width(), s.height()) for s in icon.availableSizes()} == {
        (size, size) for size in branding.ICON_SIZES
    }
    assert not icon.pixmap(16, 16).isNull()


def test_floating_mark_keeps_badge_and_progress_outside_the_symbol(qapp):
    from floating import FloatingBall
    ball = FloatingBall()
    try:
        def render():
            image = QImage(76, 76, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            ball.render(image)
            return image

        idle = render()
        ball.set_summary(8)
        counted = render()
        ball.set_progress(True, 67)
        progressing = render()
        assert ball.size().width() == ball.size().height() == 76
        assert idle != counted and idle != progressing
        assert idle.copy(26, 26, 24, 24) == counted.copy(26, 26, 24, 24)
        assert idle.copy(26, 26, 24, 24) == progressing.copy(26, 26, 24, 24)
        # Full artwork fidelity is checked independently against the approved
        # preview below; these assertions check the status overlays only.
    finally:
        ball.close()


def test_floating_ball_matches_the_approved_metal_asset_in_both_themes(qapp):
    from floating import FloatingBall
    from theme import install_theme, set_theme
    reference = branding.icon_image(76)
    assert not reference.isNull()
    ball = FloatingBall()
    try:
        for mode in ('light', 'dark'):
            install_theme(); set_theme(mode, persist=False)
            actual = QImage(76, 76, QImage.Format_ARGB32_Premultiplied)
            actual.fill(Qt.transparent)
            ball.render(actual)
            assert actual == reference.convertToFormat(actual.format())
    finally:
        ball.close(); ball.deleteLater(); install_theme()


def test_original_approved_artwork_bytes_are_preserved():
    import hashlib
    from orb_artwork import artwork_path,APPROVED_SHA256
    assert hashlib.sha256(artwork_path().read_bytes()).hexdigest()==APPROVED_SHA256


@pytest.mark.parametrize('size', [0, -1, 4097, 2.5])
def test_raster_size_is_bounded(size):
    with pytest.raises(ValueError):
        branding.icon_image(size)
