"""Actual runtime regression checks, always using isolated caller-owned data."""
from pathlib import Path
from zipfile import ZipFile


def verify(root):
    from desktop_service import DesktopService
    from intake_link import import_receipt
    from production import source_digest
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    def intake(paths):
        plan=s.receive_interactive(list(map(str,paths)),linked=True)['material_preview']
        return s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
    a=root/'校验甲系统.docx';b=root/'校验乙系统.docx';a.write_bytes(b'A');b.write_bytes(b'B')
    receive=s.production.receive
    def failed(*args,**kwargs):raise OSError('isolated retry fixture')
    s.production.receive=failed
    try:r=intake([a,b])
    finally:s.production.receive=receive
    token=r['import_token'];pid,other=r['project_ids']
    a.write_bytes(b'changed');s.production.delete_tasks([other],confirmed=True)
    retried=s.production.retry_import_files(token)
    files=import_receipt(s.production,token)['files']
    assert [f['state'] for f in files]==['source_changed','paused']
    assert retried['paused_count']==1 and retried['pending_count']==1
    s.production.retry_import_files(token,project_id=pid,source_path=str(a),accepted_source=source_digest(a))
    s.production.restore_tasks([other]);assert s.production.retry_import_files(token)['copied_count']==2
    old=import_receipt(s.production,token)['files'][0];assert Path(old['saved_path']).read_bytes()==b'changed'
    a.write_bytes(b'newer');pending=intake([a]);g=pending['material_conflicts'][0]
    s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=pid)})
    assert Path(import_receipt(s.production,token)['files'][0]['saved_path']).read_bytes()==b'changed'
    z=root/'原包验证系统.zip'
    with ZipFile(z,'w') as out:out.writestr('readme.txt',b'raw')
    first=s.receive_interactive([str(z)],zip_mode='original')
    pending=s.receive_interactive([str(z)],zip_mode='original');g=pending['material_conflicts'][0]
    decision={g['key']:dict(action='new',project_id=first['project_ids'][0])}
    created=s.resolve_duplicate_intake(pending['material_plan'],decision)
    assert created['created_project_ids'] and created['project_ids']!=first['project_ids']
    assert Path(created['files'][0]['saved_path']).read_bytes()==z.read_bytes()
    assert s.resolve_duplicate_intake(pending['material_plan'],decision)==created
    assert not (s.db.path.parent/'材料收件缓存').exists()
    return dict(retry_requires_source_confirmation=True,withdrawn_retry_paused_and_restore_resumes=True,
        original_zip_duplicate_choice_and_separate_task=True,historical_intake_receipt_pins_version=True)
