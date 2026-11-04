"""Exercise recovery and tamper checks inside the distributed executable."""
from pathlib import Path
from zipfile import ZipFile


def verify(root):
    from desktop_service import DesktopService
    from intake_history import latest
    from intake_link import import_receipt
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    def intake(p):
        plan=s.receive_interactive([str(p)],linked=True)['material_preview'];plan['batch']+=' '+plan['token'][:6];r=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
        if r.get('material_conflicts'):r=s.resolve_duplicate_intake(r['material_plan'],{g['key']:dict(action='overwrite',project_id=g['candidates'][0]['id']) for g in r['material_conflicts']})
        return r
    def failed(*args,**kwargs):raise OSError('isolated disk-full simulation')
    old=root/'old/旧队列保护系统.docx';new=root/'new/旧队列保护系统.docx';old.parent.mkdir();new.parent.mkdir();old.write_bytes(b'old')
    receive=s.production.receive;s.production.receive=failed
    try:pending=intake(old)
    finally:s.production.receive=receive
    new.write_bytes(b'approved newer');r=intake(new);row=s.production.rows()[0];vid=row['materials'][0]['id'];s.production.review(vid,True);s.production.submit([vid])
    assert not any(p['token']==pending['import_token'] for p in s.production.pending_imports())
    assert s.production.retry_import_files(pending['import_token'])['superseded_count']==1
    assert s.production.rows()[0]['materials'][0]['id']==vid and s.production.rows()[0]['submitted_count']==1
    p=root/'交付副本校验系统.docx';p.write_bytes(b'approved');r=intake(p);pid=r['project_ids'][0]
    with s.db.connect() as c:target=Path(c.execute('SELECT path FROM collected_materials WHERE project_id=?',(pid,)).fetchone()[0])
    vid=next(r for r in s.production.rows() if r['id']==pid)['materials'][0]['id'];s.production.review(vid,True);target.write_bytes(b'external change')
    row=next(r for r in s.production.rows() if r['id']==pid);assert row['collection_count']==0 and row['production_state']=='材料副本异常'
    assert s.production.scan()['errors']
    try:s.production.submit([vid])
    except ValueError as e:assert '分类副本' in str(e)
    else:raise AssertionError('changed published copy was submitted')
    z=root/'原包失败恢复系统.zip'
    with ZipFile(z,'w') as out:out.writestr('readme.txt',b'raw bytes')
    first=s.receive_interactive([str(z)],zip_mode='original');pending=s.receive_interactive([str(z)],zip_mode='original');g=pending['material_conflicts'][0]
    copy=s._working_zip_copy;s._working_zip_copy=failed
    try:r=s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    finally:s._working_zip_copy=copy
    assert r['copied_count']==0 and r['pending_count']==1 and r['files'][0]['state']=='failed';task_count=len(s.production.rows())
    restarted=DesktopService(s.db.path);assert latest(restarted.production)['import_token']==r['import_token']
    assert import_receipt(restarted.production,r['import_token'])['files'][0]['error']=='isolated disk-full simulation'
    done=restarted.production.retry_import_files(r['import_token']);assert done['copied_count']==1 and not done['pending_count']
    assert len(restarted.production.rows())==task_count and Path(done['files'][0]['saved_path']).read_bytes()==z.read_bytes()
    return dict(stale_retry_cannot_replace_newer_submission=True,published_copy_changes_block_submission=True,
        raw_zip_failure_retries_without_duplicate_task=True,last_raw_receipt_survives_restart=True)
