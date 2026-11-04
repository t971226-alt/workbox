"""Real files and transactional failures for the four same-name actions."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
from threading import Event
from concurrent.futures import CancelledError


def verify(root):
    from desktop_service import DesktopService
    from production import source_digest
    from duplicate_intake_ui import DuplicateMaterialDialog
    import intake_history
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    def service(name):
        r=root/name;r.mkdir();s=DesktopService(r/'data/workbox.db')
        s.workspaces.configure_materials(r/'saved',complete_setup=True)
        return s,r
    def intake(s,p,action='overwrite'):
        plan=s.receive_interactive([str(p)],linked=True)['material_preview']
        result=s.confirm_direct_materials(plan,plan['entries'],plan['batch']+' '+plan['token'][:6],'collect')
        if result.get('material_conflicts'):
            result=s.resolve_duplicate_intake(result['material_plan'],{g['key']:dict(action=action,project_id=g['candidates'][0]['id']) for g in result['material_conflicts']})
        return result
    s,r=service('materials');p=r/'覆盖验证系统.docx';kind='软件说明书.docx'
    p.write_bytes(b'v1');intake(s,p);pid=s.production.rows()[0]['id']
    v1=s.production.history(pid,kind)[0]
    p.write_bytes(b'v2');intake(s,p);v2=s.production.history(pid,kind)[0]
    with s.db.connect() as c:
        export=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0])
        work=Path(c.execute('SELECT path FROM work_files WHERE id=?',(v2['work_file_id'],)).fetchone()[0])
        c.execute('INSERT INTO material_reviews(version_id,passed,note) VALUES (?,1,?)',(v1['id'],'earlier history stays'))
        c.execute('INSERT INTO material_reviews(version_id,passed,note) VALUES (?,1,?)',(v2['id'],'replaced version'))
        sid=c.execute("INSERT INTO material_submissions(note) VALUES ('replaced submission')").lastrowid
        c.execute('INSERT INTO material_submission_items VALUES (?,?)',(sid,v2['id']))
        c.execute('INSERT INTO material_submission_voids(submission_id,version_id,note) VALUES (?,?,?)',(sid,v2['id'],'old void'))
        c.execute('INSERT INTO material_version_inputs VALUES (?,?,?)',(v1['id'],'历史依赖',v2['id']))
    p.write_bytes(b'v3');done=intake(s,p,'replace');assert done['copied_count']==1,done
    versions=s.production.history(pid,kind)
    assert len(versions)==2 and versions[1]['id']==v1['id'] and versions[0]['sha256']==source_digest(p)
    assert versions[1]['reviews'][0]['note']=='earlier history stays'
    with s.db.connect() as c:
        assert c.execute('SELECT source_version_id FROM material_version_inputs WHERE version_id=?',(v1['id'],)).fetchone()[0] is None
    assert Path(v1['path']).read_bytes()==b'v1' and not Path(v2['path']).exists()
    assert not export.exists() and not work.exists() and p.read_bytes()==b'v3'
    with s.db.connect() as c:
        assert not c.execute('SELECT 1 FROM material_versions WHERE id=?',(v2['id'],)).fetchone()
    # Retries are idempotent and must not discard the newly committed version.
    assert s.production.retry_import_files(done['import_token'])['copied_count']==1
    assert len(s.production.history(pid,kind))==2
    stale=versions[0];p.write_bytes(b'v4')
    try:s.production.receive(pid,kind,p,replace_current=True,expected_version_id=v1['id'])
    except ValueError:pass
    else:raise AssertionError('stale destructive selection was accepted')
    assert s.production.history(pid,kind)[0]['id']==stale['id']
    # Failure after publication, deletion scheduling and DB updates rolls all metadata back.
    with patch.object(s.production,'_sync',side_effect=OSError('isolated commit failure')):
        failed=intake(s,p,'replace')
    assert failed['pending_count']==1 and len(s.production.history(pid,kind))==2
    assert Path(stale['path']).read_bytes()==b'v3'
    restarted=DesktopService(s.db.path);assert restarted.production.retry_import_files(failed['import_token'])['copied_count']==1
    assert not Path(stale['path']).exists() and Path(v1['path']).is_file()
    # Cancellation is not permission to retire any old bytes.
    before=restarted.production.history(pid,kind);event=Event();event.set()
    try:restarted.production.receive(pid,kind,p,replace_current=True,expected_version_id=before[0]['id'],cancel_event=event)
    except CancelledError:pass
    else:raise AssertionError('cancellation was ignored')
    assert restarted.production.history(pid,kind)==before
    # Shared references and externally changed bytes must never be removed.
    from version_overwrite import queue,finalize
    shared=r/'shared.docx';shared.write_bytes(b'shared')
    with restarted.db.connect() as c:
        c.execute('INSERT INTO material_versions(project_id,kind,path,filename,sha256,size) VALUES (?,?,?,?,?,?)',
                  (pid,'shared.docx',str(shared),shared.name,source_digest(shared),6))
        queue(c,shared,source_digest(shared),6,source=p)
    finalize(restarted.production);assert shared.read_bytes()==b'shared'
    altered=r/'altered.docx';altered.write_bytes(b'old')
    with restarted.db.connect() as c:queue(c,altered,source_digest(altered),3,source=p)
    altered.write_bytes(b'edited');finalize(restarted.production);assert altered.read_bytes()==b'edited'
    # Explicit input from the managed current archive is still protected.
    with restarted.db.connect() as c:
        from version_overwrite import queue,finalize
        queue(c,before[0]['path'],before[0]['sha256'],before[0]['size'],source=before[0]['path'])
    finalize(restarted.production);assert Path(before[0]['path']).is_file()
    p.write_bytes(b'copy');copied=intake(restarted,p,'new')
    assert len(restarted.production.rows())==2 and copied['created_project_ids']
    skipped_direct=intake(restarted,p,'skip')
    assert skipped_direct['copied_count']==0 and skipped_direct['skipped_count']==1
    # The skip workflow is resolved by the controller before a direct plan is submitted.
    from duplicate_intake import DuplicateIntake
    duplicate=DuplicateIntake(restarted);plan=duplicate.prepare([str(p)],project_id=pid,material_kind=kind)
    skipped=duplicate.resolve(plan,{g['key']:dict(action='skip',project_id=pid) for g in plan['groups']})
    assert skipped['copied']==0 and len(restarted.production.rows())==2
    with restarted.db.connect() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()
    # All four options remain explicit; automatic batch updates retain history.
    dialog=DuplicateMaterialDialog(dict(name='覆盖验证系统',files=[str(p)],candidates=[restarted.production.rows()[0]]),allow_overwrite_all=True)
    assert dialog.overwrite_button.text()=='保存为新版本'
    assert dialog.replace_button.text()=='直接覆盖旧版本' and dialog.new_button.text()=='另存为独立副本'
    assert not dialog.replace_button.autoDefault() and not dialog.replace_button.isDefault()
    dialog.replace_button.click();assert dialog.decision['action']=='replace' and not dialog.apply_to_all
    dialog.close()

    s,r=service('raw');p=r/'原包覆盖系统.zip'
    def package(content):
        with ZipFile(p,'w') as z:z.writestr('data.txt',content)
    def raw(action='overwrite'):
        result=s.receive_interactive([str(p)],zip_mode='original')
        if result.get('material_conflicts'):
            return s.resolve_duplicate_intake(result['material_plan'],{g['key']:dict(action=action,project_id=g['candidates'][0]['id']) for g in result['material_conflicts']})
        return result
    package(b'v1');raw();pid=s.production.rows()[0]['id'];z1=s.production.rows()[0]['original_zips'][0]
    package(b'v2');raw();z2=s.production.rows()[0]['original_zips'][0]
    package(b'v3');save=intake_history.save
    def fail_commit(store,result,**kwargs):
        if kwargs.get('connection') is not None and result.get('copied_count'):
            raise OSError('isolated raw receipt commit failure')
        return save(store,result,**kwargs)
    with patch('intake_history.save',side_effect=fail_commit):failed=raw('replace')
    assert failed['pending_count']==1 and Path(z2['path']).is_file()
    assert len(s.production.rows()[0]['original_zips'])==2
    s=DesktopService(s.db.path);done=s.production.retry_import_files(failed['import_token'])
    assert done['copied_count']==1,done
    zips=s.production.rows()[0]['original_zips']
    assert len(zips)==2 and zips[1]['id']==z1['id'] and not Path(z2['path']).exists()
    assert Path(z1['path']).is_file() and p.is_file()
    assert s.production.retry_import_files(done['import_token'])['copied_count']==1
    package(b'skipped');skipped=raw('skip')
    assert skipped['copied_count']==0 and len(s.production.rows()[0]['original_zips'])==2
    with s.db.connect() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()
    package(b'new independent');copy=raw('new')
    assert copy['copied_count']==1 and len(s.production.rows())==2
    return dict(direct_overwrite_preserves_earlier_history_and_source=True,
                direct_overwrite_removes_replaced_version_copies=True,
                direct_overwrite_rollback_retry_cancel_and_stale_guard=True,
                raw_overwrite_commit_retry_and_history_preservation=True,
                four_same_name_actions_are_explicit=True)
