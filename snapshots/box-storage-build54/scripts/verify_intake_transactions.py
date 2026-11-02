"""Runtime consistency checks for the distributed program, isolated data only."""
from pathlib import Path
from zipfile import ZipFile
from threading import Event
from concurrent.futures import CancelledError


def verify(root):
    from desktop_service import DesktopService
    import intake_history as history
    import intake_link
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    def service(label):
        r=root/label;r.mkdir();s=DesktopService(r/'data/workbox.db');s.workspaces.configure_materials(r/'saved',complete_setup=True);return s,r
    def package(p):
        with ZipFile(p,'w') as z:z.writestr('readme.txt',b'isolated transaction bytes')
    def intake(s,p):
        plan=s.receive_interactive([str(p)],linked=True)['material_preview'];plan['batch']+=' '+plan['token'][:6]
        r=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
        if r.get('material_conflicts'):r=s.resolve_duplicate_intake(r['material_plan'],{g['key']:dict(action='overwrite',project_id=g['candidates'][0]['id']) for g in r['material_conflicts']})
        return r
    def fail(*args,**kwargs):raise OSError('isolated commit interruption')
    s,r=service('publication');p=r/'事务回滚系统.docx';p.write_bytes(b'old');intake(s,p)
    with s.db.connect() as c:old=dict(c.execute('SELECT * FROM collected_materials').fetchone())
    sync=s.production._sync;p.write_bytes(b'new');s.production._sync=fail
    try:failed=intake(s,p)
    finally:s.production._sync=sync
    with s.db.connect() as c:assert dict(c.execute('SELECT * FROM collected_materials').fetchone())==old
    assert Path(old['path']).read_bytes()==b'old' and s.production.rows()[0]['collection_count']==1
    restarted=DesktopService(s.db.path);assert restarted.production.retry_import_files(failed['import_token'])['copied_count']==1
    assert restarted.production.rows()[0]['collection_count']==1
    s,r=service('atomic_zip');p=r/'原包事务系统.zip';package(p);first=s.receive_interactive([str(p)],zip_mode='original')
    pending=s.receive_interactive([str(p)],zip_mode='original');g=pending['material_conflicts'][0];decision={g['key']:dict(action='new',project_id=first['project_ids'][0])}
    save=history.save
    def fail_completion(store,result,**kwargs):
        if result.get('copied_count'):raise OSError('isolated receipt commit failure')
        return save(store,result,**kwargs)
    history.save=fail_completion
    try:failed=s.resolve_duplicate_intake(pending['material_plan'],decision)
    finally:history.save=save
    assert failed['copied_count']==0 and failed['pending_count']==1
    with s.db.connect() as c:assert c.execute('SELECT count(*) FROM original_zip_receipts').fetchone()[0]==1
    p.unlink();restarted=DesktopService(s.db.path);done=restarted.production.retry_import_files(failed['import_token'])
    assert done['copied_count']==1 and len(restarted.production.rows())==2 and Path(done['files'][0]['saved_path']).is_file()
    assert restarted.resolve_duplicate_intake(pending['material_plan'],decision)['copied_count']==1
    s,r=service('cancel_batch');a=r/'批量甲系统.zip';b=r/'批量乙系统.zip';package(a);package(b);event=Event();register=intake_link.register_original_zip
    def cancel_after_copy(*args,**kwargs):
        result=register(*args,**kwargs);event.set();return result
    intake_link.register_original_zip=cancel_after_copy
    try:
        try:s.receive_interactive([str(a),str(b)],zip_mode='original',cancel_event=event)
        except CancelledError:pass
        else:raise AssertionError('cancellation was not observed')
    finally:intake_link.register_original_zip=register
    restarted=DesktopService(s.db.path);pending=history.latest(restarted.production)
    assert pending['copied_count']==1 and pending['pending_count']==1
    assert restarted.production.retry_import_files(pending['import_token'])['copied_count']==2
    s,r=service('mixed_batch');a=r/'混合已有系统.zip';bad=r/'损坏.zip';package(a);bad.write_bytes(b'broken');first=s.receive_interactive([str(a)],zip_mode='original')
    pending=s.receive_interactive([str(a),str(bad)],zip_mode='original');g=pending['material_conflicts'][0]
    done=s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    assert done['copied_count']==1 and done['pending_count']==1 and len(done['files'])==2
    assert len(s.production.rows())==2 and all(row['original_zips'] for row in s.production.rows())
    return dict(material_rollback_preserves_published_path=True,zip_receipt_and_journal_commit_atomically=True,
        batch_cancel_preserves_completed_and_pending=True,mixed_bad_zip_has_no_untracked_new_task=True,
        saved_copy_recovers_without_original=True)
