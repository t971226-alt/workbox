"""Preflight raw ZIP duplicates without extracting or creating tasks."""
from pathlib import Path
from file_formats import ARCHIVES
from uuid import uuid4
from datetime import datetime
import json
from contextlib import ExitStack
from native_files import ZipLease,cancelled
from production import normalized,source_digest
from duplicate_intake import DuplicateIntake
from intake_link import register_original_zip
from concurrent.futures import CancelledError


class OriginalZipIntake:
    def __init__(self,service):self.service=service

    def rows(self,ids):
        with self.service.db.connect() as c:return [self.service.production._project(c,pid) for pid in ids]

    def snapshot(self,rows):
        return dict(tasks=DuplicateIntake._snapshot(rows),zips={str(r['id']):[(z['id'],z['path'],z['sha256']) for z in r.get('original_zips',[])] for r in rows})

    def prepare(self,paths,project_id=None):
        s=self.service;entries=[];groups={}
        active=s.list_projects()
        for path in paths:
            p=Path(path)
            if p.suffix.casefold() not in ARCHIVES:raise ValueError('原包收录支持 ZIP、7z 和 RAR。')
            routing=s.file_routing(p)
            ids=[project_id] if project_id is not None else [r['id'] for r in routing['candidates']]
            if not ids and not routing.get('requires_choice'):
                ids=[r['id'] for r in active if normalized(r['name'])==normalized(p.stem)]
            if not ids and p.suffix.casefold()=='.zip' and s.is_bundle_candidate(p.name):
                from bundle_analyzer import analyze_bundle
                bundle=analyze_bundle(p,active)
                if bundle['state']=='matched':ids=bundle['project_ids']
            rows=self.rows(ids)
            keys=[]
            for row in rows:
                key=normalized(row['name']);keys.append(key)
                g=groups.setdefault(key,dict(key=key,name=row['name'],candidates={},files=[],raw_original=True))
                g['candidates'][row['id']]=row;g['files'].append(str(p))
            entries.append(dict(path=str(p),sha256=source_digest(p),groups=list(dict.fromkeys(keys))))
        for g in groups.values():
            g['candidates']=list(g['candidates'].values());g['files']=list(dict.fromkeys(g['files']));g['snapshot']=self.snapshot(g['candidates'])
        return dict(raw_original=True,token=uuid4().hex,entries=entries,groups=list(groups.values()),paths=list(map(str,paths)),project_id=project_id)

    def receive(self,paths,project_id=None,**kwargs):
        plan=self.prepare(paths,project_id)
        if plan['groups']:return dict(material_conflicts=plan['groups'],material_plan=plan)
        from raw_intake_engine import begin
        return begin(self.service,plan,{},cancel_event=kwargs.get('cancel_event'),progress=kwargs.get('progress'))

    def resolve(self,plan,decisions,**kwargs):
        from contextlib import nullcontext
        with getattr(self.service.db,'_operation_lock',nullcontext()):
            return self._resolve(plan,decisions,**kwargs)

    def _resolve(self,plan,decisions,*,cancel_event=None,progress=None):
        from raw_intake_engine import begin
        return begin(self.service,plan,decisions,cancel_event=cancel_event,progress=progress)
