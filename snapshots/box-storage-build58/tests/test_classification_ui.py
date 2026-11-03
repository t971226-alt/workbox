import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from compact_ui import CompactPanel
from theme import install_theme


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


@pytest.fixture
def panel(app,tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    s.production.create_batch('设备软件',['设备异常监测系统'],['软件说明书.docx'])
    path=tmp_path/'设备异常监测系统.docx';path.write_bytes(b'isolated word');s.receive_path(path)
    install_theme(s);p=CompactPanel(s);p.production_page.show_legacy_workspace();p.show();app.processEvents()
    p.production_page.job_requested.connect(lambda caption,op:(op(),p.production_page.refresh()))
    yield p
    p.conversation_page.shutdown();p.document_pane.shutdown();p.hide();p.deleteLater();install_theme()


def test_actual_expansion_review_submission_and_mode_switch(panel,app,monkeypatch):
    page=panel.production_page;row=page.rows[0];pid=row['id'];kind=row['materials'][0]['kind']
    assert page.grouped.isVisible() and not page.table.isVisible()
    QTest.mouseClick(page.grouped.task_buttons[pid],Qt.LeftButton);app.processEvents()
    assert pid in page.grouped.open_tasks
    control=page.grouped.file_controls[(pid,kind,'review')]
    QTest.mouseClick(control,Qt.LeftButton);app.processEvents()
    assert page.rows[0]['reviewed_count']==1
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Yes)
    QTest.mouseClick(page.grouped.file_controls[(pid,kind,'submit')],Qt.LeftButton);app.processEvents()
    assert page.rows[0]['submitted_count']==1
    QTest.mouseClick(page.processing_switch,Qt.LeftButton);app.processEvents()
    assert page.rows[0]['production_state']=='已收纳' and not page.grouped.file_controls
    QTest.mouseClick(page.processing_switch,Qt.LeftButton);app.processEvents()
    assert page.rows[0]['production_state']=='已提交' and page.rows[0]['reviewed_count']==1


def test_type_filter_filename_search_and_batch_state(panel,app):
    page=panel.production_page
    page.search.setText('软件说明书.docx');assert len(page.rows)==1
    page.search.clear()
    index=next(i for i in range(page.type_tabs.count()) if page.type_tabs.tabData(i)=='五件材料')
    page.type_tabs.setCurrentIndex(index);assert not page.rows and page.empty_label.isVisible()
    page.type_tabs.setCurrentIndex(0);assert len(page.rows)==1
    pid=page.rows[0]['id'];bid=page.rows[0]['batch_id']
    page.grouped.toggle_task(pid,bid);page.refresh();assert pid in page.grouped.open_tasks
    page.grouped.toggle_batch(bid);page.refresh();assert bid in page.grouped.closed_batches


def test_editor_describes_changes_and_keeps_existing_tasks(panel,app,monkeypatch):
    from classification_ui import ClassificationDialog
    s=panel.service;before=s.production.rows()
    d=ClassificationDialog(s,panel);d.new();d.name.setText('报告归档');d.mode.setCurrentIndex(d.mode.findData('collect'))
    d.add_material('报告.docx','验收报告');messages=[]
    def confirm(parent,title,message,*args,**kwargs):
        messages.append((title,message));return QMessageBox.Save
    monkeypatch.setattr(QMessageBox,'question',confirm)
    d.save_button.click()
    assert s.classifications.packages()['报告归档']==['报告.docx']
    assert s.production.rows()==before
    assert not messages and '1 份' in d.summary.text()
    d.add_material('补充报告.pdf');d.save_button.click()
    assert messages and '新批次' in messages[0][1] and '新增' in messages[0][1]
    d.close();d.deleteLater()


def test_cancel_inline_submit_and_editor_change_do_not_write(panel,app,monkeypatch):
    page=panel.production_page;row=page.rows[0];pid=row['id'];kind=row['materials'][0]['kind']
    page.store.review(row['materials'][0]['id'],True);page.refresh();page.grouped.toggle_task(pid,row['batch_id'])
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.No)
    page.grouped.file_controls[(pid,kind,'submit')].click();app.processEvents()
    assert page.rows[0]['submitted_count']==0
    from classification_ui import ClassificationDialog
    d=ClassificationDialog(panel.service,panel);old=d.current['name'];d.name.setText('未保存名称');d.select(1)
    assert d.current['name']==old and old in panel.service.classifications.packages()
    d.dirty=False;d.close();d.deleteLater()


def test_header_and_switch_fit_compact_window_and_dark_mode(panel,app):
    from theme import set_theme
    for mode in ('light','dark'):
        set_theme(mode,panel.service)
        for width in (870,760):
            panel.resize(width,640);app.processEvents()
            page=panel.production_page
            for control in (page.processing_switch,page.receive_button,page.more_button,page.type_tabs):
                assert panel.rect().contains(control.mapTo(panel,control.rect().bottomRight()))
            assert page.processing_switch.toolTip().startswith('当前批次')


def test_expanded_material_rows_and_type_labels_do_not_overlap(panel,app):
    from PySide6.QtGui import QFontMetrics
    page=panel.production_page;row=page.rows[0]
    page.store.configure_task(row['id'],['软件说明书.docx','软件说明书.pdf','登记申请信息.txt'],reason='布局核对')
    page.refresh();page.grouped.toggle_task(row['id'],row['batch_id'])
    for _ in range(6):app.processEvents()
    controls=[page.grouped.file_controls[(row['id'],kind,'review')] for kind in ['软件说明书.docx','软件说明书.pdf','登记申请信息.txt']]
    positions=[c.mapTo(page.grouped.body,c.rect().center()).y() for c in controls]
    assert positions[1]-positions[0]>=28 and positions[2]-positions[1]>=28
    for i in range(page.type_tabs.count()):
        if page.type_tabs.isTabVisible(i):
            assert page.type_tabs.tabRect(i).width()>=QFontMetrics(page.type_tabs.font()).horizontalAdvance(page.type_tabs.tabText(i))


def test_collapse_keeps_draft_rows_width_and_persists(panel,app):
    chat=panel.conversation_page;page=panel.production_page
    chat.composer.setPlainText('尚未发送的软件名称\n第二个名称')
    chat.workspace_splitter.setSizes([290,550]);app.processEvents()
    before=chat.workspace_splitter.sizes()[0];rows=page.store.rows();narrow=page.width()
    QTest.mouseClick(chat.collapse_button,Qt.LeftButton);app.processEvents()
    assert chat.chat_body.isHidden() and chat.expand_button.isVisible()
    assert page.width()>narrow and page.store.rows()==rows
    assert panel.service.get_setting('conversation_collapsed') is True
    assert chat.composer.toPlainText()=='尚未发送的软件名称\n第二个名称'
    # A fresh page reads the saved preference; rebuilding never writes the draft.
    from intake_chat_ui import IntakeConversationPage
    from PySide6.QtWidgets import QWidget
    fresh=IntakeConversationPage(panel.service);fresh.attach_ledger(QWidget());fresh.resize(870,640);fresh.show();app.processEvents()
    assert fresh.chat_body.isHidden() and not fresh.collapsed_rail.isHidden()
    fresh.expand_button.click();app.processEvents()
    assert abs(fresh.workspace_splitter.sizes()[0]-before)<=2
    fresh.shutdown();fresh.deleteLater()
    panel.resize(760,520);app.processEvents()
    QTest.mouseClick(chat.expand_button,Qt.LeftButton);app.processEvents()
    assert chat.chat_body.isVisible() and chat.collapsed_rail.isHidden()
    assert chat.workspace_splitter.sizes()[0]>=250
    assert panel.service.get_setting('conversation_collapsed') is False
    assert panel.service.get_setting('conversation_width')>=250
    assert before>=250 and chat.composer.toPlainText().startswith('尚未发送')


def test_inline_plus_opens_blank_editor_and_doubleclick_edits_selected(panel,app,monkeypatch):
    from classification_ui import ClassificationDialog
    page=panel.production_page;seen=[]
    def inspect(dialog):
        seen.append((dialog.current,dialog.name.text(),dialog.materials.rowCount()));return 0
    monkeypatch.setattr(ClassificationDialog,'exec',inspect)
    page.build_category_menu();page.add_class_action.trigger()
    assert seen.pop()==(None,'',0)
    index=next(i for i in range(page.type_tabs.count()) if page.type_tabs.tabData(i)=='五件材料')
    QTest.mouseDClick(page.type_tabs,Qt.LeftButton,pos=page.type_tabs.tabRect(index).center());app.processEvents()
    current,name,count=seen.pop()
    assert current['name']==name=='五件材料' and count==5


def test_inline_minus_cancels_then_disables_used_class_without_data_loss(panel,app,monkeypatch):
    page=panel.production_page;before=page.store.rows()
    assert not page.remove_type_button.isEnabled()
    index=next(i for i in range(page.type_tabs.count()) if page.type_tabs.tabData(i)=='单手册 Word')
    page.type_tabs.setCurrentIndex(index);assert page.remove_type_button.isEnabled()
    messages=[]
    def cancel(*args,**kwargs):messages.append(args[2]);return QMessageBox.No
    monkeypatch.setattr(QMessageBox,'question',cancel);page.remove_current_classification()
    assert '单手册 Word' in panel.service.classifications.packages()
    assert '已有批次' in messages[0]
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Yes)
    page.remove_current_classification();app.processEvents()
    assert '单手册 Word' not in panel.service.classifications.packages()
    assert page.store.rows()==before and len(page.rows)==1
    assert any(p['name']=='单手册 Word' and not p['active'] for p in panel.service.classifications.profiles())
    assert page.type_tabs.tabData(page.type_tabs.currentIndex()) is None
    assert not page.remove_type_button.isEnabled()


def test_inline_edit_saved_rules_refresh_tabs_and_future_intake(panel,app,monkeypatch):
    from classification_ui import ClassificationDialog
    page=panel.production_page;before=page.store.rows()
    index=next(i for i in range(page.type_tabs.count()) if page.type_tabs.tabData(i)=='五件材料')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Save)
    def edit(dialog):
        assert dialog.current['name']=='五件材料'
        dialog.name.setText('交付文档');dialog.add_material('验收报告.pdf','验收单');dialog.save();return 0
    monkeypatch.setattr(ClassificationDialog,'exec',edit)
    page.edit_classification_tab(index)
    assert panel.service.classifications.packages()['交付文档'][-1]=='验收报告.pdf'
    assert panel.conversation_page.preset.findText('交付文档')>=0
    assert any(page.type_tabs.tabData(i)=='交付文档' for i in range(page.type_tabs.count()))
    assert page.store.rows()==before


def test_inline_buttons_fit_many_tabs_in_narrow_light_and_dark(panel,app):
    from theme import set_theme
    for i in range(8):panel.service.classifications.save('长名称分类'+str(i),['报告.docx'])
    page=panel.production_page;page.refresh()
    for mode in ('light','dark'):
        set_theme(mode,panel.service)
        for collapsed in (False,True):
            panel.conversation_page.set_chat_collapsed(collapsed)
            panel.resize(760,520)
            for _ in range(4):app.processEvents()
            for button in (page.category_button,):
                assert button.isVisible() and panel.rect().contains(button.mapTo(panel,button.rect().bottomRight()))
                assert button.width()>=26 and button.accessibleName()
            assert page.type_tabs.geometry().right()<page.add_type_button.geometry().left()
