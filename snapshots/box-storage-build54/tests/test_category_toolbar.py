import pytest
from PySide6.QtWidgets import QApplication,QToolButton
from PySide6.QtGui import QFontMetrics
from desktop_service import DesktopService
from compact_ui import CompactPanel
from theme import install_theme,set_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

def test_overflow_menu_reaches_hidden_class_and_wraps_long_selection(app,tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    long_name='长分类名称'*8
    for i in range(8):s.classifications.save('分类'+str(i),['报告.docx'])
    s.classifications.save(long_name,['报告.docx']);install_theme(s);p=CompactPanel(s);p.show();page=p.production_page;page.show_legacy_workspace()
    try:
        for mode in ('light','dark'):
            set_theme(mode,s);p.resize(760,520)
            for _ in range(4):app.processEvents()
            assert page.category_button.isVisible() and page.remove_type_button.isHidden()
            assert not any(b.isVisible() for b in page.type_tabs.findChildren(QToolButton))
            visible=[i for i in range(page.type_tabs.count()) if page.type_tabs.isTabVisible(i)]
            hidden=[i for i in range(page.type_tabs.count()) if not page.type_tabs.isTabVisible(i)]
            assert hidden
            for i in visible:
                assert page.type_tabs.rect().contains(page.type_tabs.tabRect(i))
                assert page.type_tabs.tabRect(i).width()>=QFontMetrics(page.type_tabs.font()).horizontalAdvance(page.type_tabs.tabText(i))
            page.build_category_menu()
            index=next(i for i in hidden if page.type_tabs.tabData(i)==long_name)
            action=next(a for a in page.category_menu.actions() if a.text()==page.type_tabs.tabText(index))
            action.trigger()
            for _ in range(4):app.processEvents()
            assert page.package_filter==long_name and page.type_tabs.isTabVisible(index)
            assert page.type_tabs.height()==44
            assert p.width()==760 and p.height()==520
            page.select_category_tab(0);app.processEvents()
        assert p.more_button.height()==22 and p.layout().contentsMargins().top()==3
    finally:p.conversation_page.shutdown();p.document_pane.shutdown();p.close();p.deleteLater();install_theme()
