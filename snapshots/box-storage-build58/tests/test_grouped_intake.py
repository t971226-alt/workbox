from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app

def test_grouped_intake_end_to_end(app,tmp_path):
    from scripts.verify_grouped_intake import verify
    assert all(verify(tmp_path/'grouped').values())

def test_types_keep_family_variants_and_multiple_slots():
    from intake_folders import type_draft
    from name_patterns import validate,match
    p=type_draft('两份Word与运行包',{'word':2,'archive':1,'.exe':1});validate(p['kinds'],p['name_patterns'])
    assert len(p['kinds'])==4 and list(p['family_map'].values()).count('word')==2
    assert match('任意名称.rar','软件系统',p['kinds'],p['family_map'],p['name_patterns']).endswith('.zip')
    assert match('任意名称.doc','软件系统',p['kinds'],p['family_map'],p['name_patterns']) is None

def test_preset_plus_type_keeps_original_roles():
    from intake_folders import type_draft
    base=dict(name='原清单',kinds=['说明.docx','代码.docx'],family_map={'说明.docx':'word','代码.docx':'word'},name_patterns={'说明.docx':{'mode':'same'},'代码.docx':{'mode':'marker','marker':'代码'}})
    p=type_draft('加原包',{'archive':1},base)
    assert p['kinds'][:2]==base['kinds'] and p['name_patterns']['代码.docx']==base['name_patterns']['代码.docx'] and base['kinds']==['说明.docx','代码.docx']

def test_folder_names_cannot_escape_selected_root(app,tmp_path):
    from desktop_service import DesktopService
    from intake_folders import type_draft,validate_records
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    for name in ['../outside','C:\\outside','CON','bad.','a/b']:
        with pytest.raises(ValueError):validate_records(s,tmp_path/'saved',[dict(name=name,rule=type_draft('规则',{'archive':1}))])
