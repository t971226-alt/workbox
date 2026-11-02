"""Read-only intake summaries and ledger receipts; no inferred review or submission."""
import json
from pathlib import Path
from collections import OrderedDict


def recognition_summary(plan,entries=None):
    from production import normalized
    entries=plan['entries'] if entries is None else entries
    groups=OrderedDict()
    for e in entries:
        key=(e.get('batch_key',''),normalized(e.get('name','')) or e['path'])
        groups.setdefault(key,[]).append(e)
    existing=sum(bool(any(e.get('known_ids') or e.get('existing') for e in g)) for g in groups.values())
    unclear=sum(not g[0].get('name') or g[0].get('package')=='待确认' for g in groups.values())
    absent=sum(max(0,len(g[0].get('kinds',[]))-len({e['kind'] for e in g})) for g in groups.values())
    classes=list(dict.fromkeys(e.get('package','待确认') for e in entries))
    return f'本次 {len(groups)} 个软件 · {len(entries)} 份材料 · 新建 {len(groups)-existing} 个 · 已有 {existing} 个。\n分类：'+('、'.join(classes) or '请选择分类')+(f'；还有 {unclear} 个软件需确认名称或分类。' if unclear else '。')


def attempts(store,c,pid,kinds):
    from production import infer_kind
    records=c.execute("""SELECT f.*,i.result_json,i.created_at AS imported_at FROM production_import_files f
        JOIN production_imports i ON i.token=f.token WHERE f.project_id=? ORDER BY i.created_at DESC,f.rowid DESC""",(pid,)).fetchall()
    latest={};plans={}
    for r in records:
        item=dict(r);token=item['token']
        if token not in plans:plans[token]=json.loads(item['result_json'])
        kind=next((b['kind'] for b in plans[token].get('material_bindings',[]) if b['project_id']==pid and b['path']==item['source_path']),None)
        if not kind:
            name=c.execute('SELECT name FROM projects WHERE id=?',(pid,)).fetchone()[0]
            kind=infer_kind(Path(item['source_path']).name,kinds,name)
        if kind in kinds and kind not in latest:latest[kind]=item
    return latest


def enrich_row(store,c,row,receipts):
    latest=attempts(store,c,row['id'],[m['kind'] for m in row['materials']]);issues=[]
    for m in row['materials']:
        saved=receipts.get(m['kind']);m['collection_saved']=bool(m['id'] and saved and saved['version_id']==m['id'] and Path(saved['path']).is_file() and Path(m['path']).is_file() and not m.get('collection_issue'))
        a=latest.get(m['kind'])
        # A newer successfully published version resolves an older failed attempt.
        resolved=bool(a and m.get('created_at','')>a['imported_at'] and m['collection_saved'])
        if a and a['state'] not in ('complete','superseded') and not resolved:
            m['import_issue']={k:a[k] for k in ('token','project_id','source_path','state','error')};issues.append(m['import_issue'])
    row['import_issues']=issues;row['failed_import_count']=sum(i['state']=='failed' for i in issues);row['pending_import_count']=len(issues)
    row['missing_material_count']=sum(not m['collection_saved'] and not m.get('import_issue') for m in row['materials'])
    row['original_zips']=[dict(r) for r in c.execute('SELECT * FROM original_zip_receipts WHERE project_id=? ORDER BY id DESC',(row['id'],))]
    if row['original_zips'] and not row['materials']:row['production_state']='原包待核对'


def import_receipt(store,token):
    from intake_history import load_raw,view
    raw=load_raw(store,token)
    if raw is not None:return view(store,raw)
    with store.db.connect() as c:
        saved=c.execute('SELECT result_json FROM production_imports WHERE token=?',(token,)).fetchone()
        if not saved:raise ValueError('收录记录不存在。')
        result=json.loads(saved[0]);files=[]
        for r in c.execute('SELECT f.*,p.name FROM production_import_files f JOIN projects p ON p.id=f.project_id WHERE token=? ORDER BY f.project_id,f.rowid',(token,)):
            item=dict(r);kind=next((b['kind'] for b in result.get('material_bindings',[]) if b['project_id']==item['project_id'] and b['path']==item['source_path']),None)
            if not kind:kind=store.match_kind(c,item['project_id'],Path(item['source_path']).name)
            from retry_intake import paused
            item.update(kind=kind or Path(item['source_path']).name)
            if item['state']=='complete' and not item['saved_path']:
                item['error']='旧收录记录未保存历史版本位置，无法确认当时文件。'
            from retry_intake import superseded
            if superseded(store,c,item,result):item.update(state='superseded',error='已有新版材料，旧失败项停止重试；如需使用旧文件，请重新拖入并选择替换。')
            if item['state'] not in ('complete','superseded') and paused(c,item['project_id']):
                item.update(state='paused',error='任务已撤销或归档，暂停重试；恢复任务后可继续。')
            files.append(item)
    result.update(copied_count=sum(f['state']=='complete' for f in files),pending_count=sum(f['state'] not in ('complete','paused','superseded') for f in files),paused_count=sum(f['state']=='paused' for f in files),superseded_count=sum(f['state']=='superseded' for f in files))
    return dict(result,files=files)


def register_original_zip(service,result,source,*,connection=None):
    path=result.get('working_zip_path')
    if not path:return result
    ids=list(dict.fromkeys([result['project_id']] if result.get('project_id') else result.get('original_project_ids',[]) or result.get('project_ids',[]) or result.get('created_project_ids',[])))
    if not ids:return result
    import hashlib
    with open(path,'rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else service.db.connect()) as c:
        files=[]
        for pid in ids:
            if not c.execute('SELECT 1 FROM production_tasks WHERE project_id=?',(pid,)).fetchone():
                service.production._set_requirements(c,pid,[],'原包待核对','','直接收录 ZIP 原包；材料未核对')
            c.execute('INSERT OR IGNORE INTO original_zip_receipts(project_id,path,sha256) VALUES (?,?,?)',(pid,path,digest))
            name=c.execute('SELECT name FROM projects WHERE id=?',(pid,)).fetchone()[0]
            files.append(dict(project_id=pid,name=name,kind=Path(source).suffix.lstrip('.').upper()+'原包',source_path=str(source),saved_path=path,state='complete',error=''))
    result.update(project_ids=ids,raw_zip_intake=True,files=files)
    return result


def reclassification_preview(store,pid):
    from direct_material_intake import DirectMaterialIntake
    with store.db.connect() as c:row=store._project(c,pid)
    profiles=store.service.classifications.profiles(True);mapping=[]
    for m in row['materials']:
        if not m['id']:continue
        _,kind=DirectMaterialIntake.role_and_name(Path(m['filename']),profiles,[row['name']])
        mapping.append(dict(old_kind=m['kind'],kind=kind,version_id=m['id']))
    if not mapping:raise ValueError('此任务没有已保存的材料，请先收录材料或解压原包。')
    if len({m['kind'] for m in mapping})!=len(mapping):raise ValueError('同类型材料存在多份版本，请先核对材料要求，不能自动重分类。')
    kinds={m['kind'] for m in mapping}
    choices=[p for p in profiles if kinds.issubset(p['kinds'])]
    if not choices:raise ValueError('现有材料未匹配到明确规则，请先新建合适的分类规则。原记录保持不变。')
    exact=[p for p in choices if set(p['kinds'])==kinds]
    selected=exact[0]['name'] if len(exact)==1 else choices[0]['name']
    return dict(project_id=pid,name=row['name'],package=row['package_name'],snapshot=[(m['kind'],m['id']) for m in row['materials']],mapping=mapping,profiles=choices,selected=selected)


def apply_reclassification(store,preview,profile_name,*,cancel_event=None,progress=None):
    from native_files import cancelled
    profiles=store.service.classifications.profiles(True)
    profile=next((p for p in profiles if p['name']==profile_name and p in preview['profiles']),None)
    if not profile:raise ValueError('规则已修改或停用，请重新识别。')
    pid=preview['project_id']
    with store.db.connect() as c:
        c.execute('BEGIN IMMEDIATE');store.db._assert_projects_editable(c,pid);row=store._project(c,pid)
        if [(m['kind'],m['id']) for m in row['materials']]!=[tuple(x) for x in preview['snapshot']]:raise ValueError('材料已变化，请重新识别。')
        materials={m['id']:m for m in row['materials'] if m['id']}
        for item in preview['mapping']:
            cancelled(cancel_event);store._verify(materials[item['version_id']],cancel_event=cancel_event)
        store._set_requirements(c,pid,profile['kinds'],profile['name'],row['category'],'用户核对重新识别预览后调整分类；旧材料和检查历史保留')
        c.execute('DELETE FROM automatic_material_tasks WHERE project_id=?',(pid,))
        for item in sorted(preview['mapping'],key=lambda x:Path(x['kind']).suffix.lower()=='.pdf'):
            cancelled(cancel_event);m=materials[item['version_id']]
            store.record_work_file(c,dict(id=m.get('work_file_id'),project_id=pid,path=m['path'],filename=m['filename'],sha256=m['sha256'],size=m['size']),kind=item['kind'],cancel_event=cancel_event,progress=progress)
    return dict(reclassified=True,material_intake=True,batch_ids=[row['batch_id']],project_ids=[pid],created_project_ids=[],copied_count=len(preview['mapping']))
