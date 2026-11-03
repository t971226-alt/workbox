import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from workspace_chrome import WorkspaceComboBox,WorkspaceOptionDelegate
from theme import set_theme,install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

def test_empty_history_does_not_open_fake_one_item_popup(app):
    c=WorkspaceComboBox();c.setProperty('history',True);c.addItem('会话记录',None);c.show();app.processEvents()
    c.showPopup();app.processEvents()
    assert not c.view().isVisible() and '还没有会话记录' in c.toolTip()
    c.close();c.deleteLater()

@pytest.mark.parametrize('mode',['light','dark'])
def test_selected_popup_is_neutral_and_keyboard_selection_still_works(app,mode):
    set_theme(mode);c=WorkspaceComboBox();c.addItem('第一批软件',1);c.addItem('第二批软件',2);c.resize(230,32);c.show();app.processEvents();c.showPopup()
    for _ in range(4):app.processEvents()
    assert c.view().isVisible() and isinstance(c.itemDelegate(),WorkspaceOptionDelegate)
    image=c.view().grab().toImage()
    # ClearType may color individual glyph edges; a native blue selection fills a row.
    blue=sum((p:=image.pixelColor(x,y)).blue()>p.red()+30 and p.blue()>p.green()+20 for y in range(image.height()) for x in range(image.width()))
    assert blue<image.width()*image.height()*.02
    QTest.keyClick(c.view(),Qt.Key_Down);QTest.keyClick(c.view(),Qt.Key_Return);app.processEvents()
    assert c.currentData()==2
    c.hidePopup();c.close();c.deleteLater();install_theme()
