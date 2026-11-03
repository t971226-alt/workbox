from pathlib import Path
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QDialog
from desktop_service import DesktopService
from intake_link import import_receipt,reclassification_preview,apply_reclassification
from requirement_intake import proposal
from tests.test_automatic_material_intake import make_files


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True);return s


def commit(s,plan):return s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')


def test_failed_files_are_distinct_from_absent_and_retry_only_selected(service,tmp_path,monkeypatch):
    files=[]
    for name in ['甲系统','乙系统']:
        p=tmp_path/(name+'.docx');p.write_bytes(name.encode());files.append(p)
    plan=service.receive_interactive(list(map(str,files)),linked=True)['material_preview']
    receive=service.production.receive
    def failed(*a,**kw):raise OSError('isolated copy failure')
    monkeypatch.setattr(service.production,'receive',failed);result=commit(service,plan)
    rows=service.production.rows();assert all(r['failed_import_count']==1 and r['missing_material_count']==0 and r['collection_count']==0 for r in rows)
    monkeypatch.setattr(service.production,'receive',receive)
    retry=service.production.retry_import_files(result['import_token'],project_id=rows[0]['id'],source_path=str(files[0]))
    assert retry['copied_count']==1 and retry['pending_count']==1
    rows=service.production.rows();assert rows[0]['collection_count']==1 and not rows[0]['import_issues'] and rows[1]['failed_import_count']==1
    receipt=import_receipt(service.production,result['import_token'])
    assert [f['state'] for f in receipt['files']]==['complete','failed'] and Path(receipt['files'][0]['saved_path']).is_file()


def test_linked_repeat_keeps_same_row_version_review_and_submission(service,tmp_path):
    p=tmp_path/'重复联动系统.docx';p.write_bytes(b'unchanged')
    first=commit(service,service.receive_interactive([str(p)],linked=True)['material_preview'])
    row=service.production.rows()[0];service.production.set_batch_mode(row['batch_id'],'review');vid=row['materials'][0]['id'];service.production.review(vid,True);service.production.submit([vid])
    plan=service.receive_interactive([str(p)],linked=True)['material_preview'];assert plan['entries'][0]['existing']['id']==row['id']
    result=commit(service,plan);assert not result.get('material_conflicts');after=service.production.rows()
    assert len(after)==1 and after[0]['materials'][0]['id']==vid and after[0]['production_state']=='已收齐' and after[0]['submitted_count']==1
    assert result['created_project_ids']==[] and result['project_ids']==[row['id']]


def test_legacy_reclassification_keeps_task_old_versions_and_submission_history(service,tmp_path):
    files=make_files(tmp_path/'legacy','旧分类管理系统');kinds=[f.name for f in files]
    result=service.production.import_plan([dict(proposal('旧分类管理系统',batch='legacy'),package='自定义',kinds=kinds,source_files=list(map(str,files)),source_kinds={str(f):f.name for f in files})],token='legacy')
    old=service.production.rows()[0];assert old['collection_count']==5;service.production.set_batch_mode(old['batch_id'],'review')
    for m in old['materials']:service.production.review(m['id'],True)
    service.production.submit([m['id'] for m in old['materials']])
    preview=reclassification_preview(service.production,old['id']);assert preview['selected']=='五件材料'
    apply_reclassification(service.production,preview,'五件材料')
    rows=service.production.rows();assert len(rows)==1 and rows[0]['id']==old['id'] and rows[0]['package_name']=='五件材料' and rows[0]['collection_count']==5
    assert rows[0]['reviewed_count']==rows[0]['submitted_count']==0
    with service.db.connect() as c:
        assert c.execute('SELECT count(*) FROM material_submission_items').fetchone()[0]==5
        assert c.execute('SELECT count(*) FROM material_versions').fetchone()[0]==10
    assert all(Path(m['path']).is_file() for m in old['materials'])
    with pytest.raises(ValueError,match='变化'):apply_reclassification(service.production,preview,'五件材料')


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


@pytest.mark.parametrize('accept',[True,False])
def test_actual_composer_paste_requires_rule_and_keeps_cancelled_text(service,app,accept):
    from intake_chat_ui import IntakeConversationPage
    from intake_link_ui import ClassificationRuleDialog
    from theme import install_theme
    from PySide6.QtCore import QMimeData
    install_theme(service);page=IntakeConversationPage(service);page.show();raw='1. 粘贴规则甲系统\n2. 粘贴规则乙系统';mime=QMimeData();mime.setText(raw);seen=[]
    def choose():
        d=QApplication.activeModalWidget();assert isinstance(d,ClassificationRuleDialog);seen.append(True)
        assert not service.production.rows()
        if accept:d.rules.setCurrentIndex(d.rules.findData('五件材料'));d.confirm_button.click()
        else:d.cancel_button.click()
    timer=QTimer();timer.setSingleShot(True);timer.timeout.connect(choose);timer.start(50)
    try:
        # Exercise Qt's paste handler without modifying the user's OS clipboard.
        page.composer.insertFromMimeData(mime);app.processEvents()
        if not seen:
            from PySide6.QtTest import QTest
            QTest.qWait(100)
        assert seen and not service.production.rows()
        if accept:
            turn=service.intake_chat.turns(page.current_id)[0];assert len(turn['rows'])==2 and all(r['package']=='五件材料' and len(r['kinds'])==5 for r in turn['rows'])
        else:assert page.composer.toPlainText()==raw and not service.intake_chat.turns(page.current_id) if page.current_id else page.composer.toPlainText()==raw
    finally:
        timer.stop();page.shutdown();page.hide();page.deleteLater();install_theme()


def test_rule_picker_can_create_and_select_a_rule(service,app):
    from intake_link_ui import ClassificationRuleDialog
    from quick_classification_ui import QuickClassificationDialog
    picker=ClassificationRuleDialog(service);seen=[]
    def create():
        d=QApplication.activeModalWidget();assert isinstance(d,QuickClassificationDialog);seen.append(True)
        d.editor.name.setText('本批自建规则');d.save_button.click()
    QTimer.singleShot(50,create);picker.create_rule()
    assert seen and picker.rules.currentData()=='本批自建规则'
    picker.confirm_button.click();assert picker.result()==QDialog.Accepted and picker.rule=='本批自建规则'
    assert not service.production.rows();picker.deleteLater()


def test_raw_zip_then_extracted_materials_fill_same_ledger_row(service,tmp_path):
    from zipfile import ZipFile
    files=make_files(tmp_path/'input','原包联动管理系统');z=tmp_path/'原包联动管理系统.zip'
    with ZipFile(z,'w') as out:
        for f in files:out.write(f,'原包联动管理系统/'+f.name)
    service.receive_interactive([str(z)],zip_mode='original')
    before=service.production.rows();assert len(before)==1 and before[0]['production_state']=='原包已收录' and before[0]['collection_count']==0
    plan=service.receive_interactive([str(z)],zip_mode='extract',linked=True)['material_preview']
    assert {e['package'] for e in plan['entries']}=={'五件材料'}
    result=commit(service,plan);after=service.production.rows()
    assert len(after)==1 and after[0]['id']==before[0]['id'] and after[0]['collection_count']==5
    assert after[0]['package_name']=='五件材料' and result['created_project_ids']==[]
