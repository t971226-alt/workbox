"""User-owned material presets; task snapshots never follow later preset edits."""
import json
from pathlib import Path
from material_templates import PRESETS

SCHEMA = '''
CREATE TABLE IF NOT EXISTS material_profiles(
 id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE COLLATE NOCASE,kinds TEXT NOT NULL,
 aliases TEXT NOT NULL DEFAULT '{}',mode TEXT NOT NULL DEFAULT 'review',
 position INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS task_profile_snapshots(
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),profile_id INTEGER REFERENCES material_profiles(id),
 aliases TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS batch_processing_modes(
 batch_id INTEGER PRIMARY KEY REFERENCES batches(id),mode TEXT NOT NULL DEFAULT 'review');
CREATE TABLE IF NOT EXISTS profile_changes(
 id INTEGER PRIMARY KEY,profile_id INTEGER,before_json TEXT,after_json TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE TABLE IF NOT EXISTS profile_intake_options(
 profile_id INTEGER PRIMARY KEY REFERENCES material_profiles(id),
 intake_mode TEXT NOT NULL DEFAULT 'materials',extensions TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS task_raw_rules(
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),rule_name TEXT NOT NULL,
 extensions TEXT NOT NULL,directory TEXT NOT NULL DEFAULT '');
'''
MODES = {'review': '统计初始未提交材料', 'collect': '统计整理已完成材料'}


class Classifications:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as c:
            c.executescript(SCHEMA)
            from file_families import SCHEMA as FAMILY_SCHEMA
            c.executescript(FAMILY_SCHEMA)
            from name_patterns import SCHEMA as PATTERN_SCHEMA
            c.executescript(PATTERN_SCHEMA)
            if not c.execute('SELECT 1 FROM material_profiles').fetchone() and not c.execute('SELECT 1 FROM profile_changes').fetchone():
                for i,(name,kinds) in enumerate(PRESETS.items()):
                    if kinds:
                        c.execute('INSERT INTO material_profiles(name,kinds,position) VALUES (?,?,?)',(name,json.dumps(kinds,ensure_ascii=False),i))

    @staticmethod
    def decode(row):
        r=dict(row);r['kinds']=json.loads(r['kinds']);r['aliases']=json.loads(r['aliases']);return r

    def profiles(self, active_only=False, *, include_archives=False):
        with self.db.connect() as c:
            records=[self.decode(r) for r in c.execute('''SELECT p.*,COALESCE(o.intake_mode,'materials') AS intake_mode,
                COALESCE(o.extensions,'[]') AS extensions FROM material_profiles p
                LEFT JOIN profile_intake_options o ON o.profile_id=p.id
                WHERE (?=0 OR p.active=1) ORDER BY p.position,p.id''',(int(active_only),))]
            for r in records:
                r['extensions']=json.loads(r['extensions'])
                r['family_map']={x[0]:x[1] for x in c.execute('SELECT kind,family FROM profile_file_families WHERE profile_id=?',(r['id'],))}
                from name_patterns import read
                r['name_patterns']=read(c,'profile_name_patterns','profile_id',r['id'])
            return records if include_archives else [r for r in records if r['intake_mode']=='materials']

    def packages(self):
        return {'待确认': [], **{r['name']:r['kinds'] for r in self.profiles(True)}}

    @staticmethod
    def validate(name,kinds,aliases=None,mode='review'):
        from production import Production, normalized
        if not isinstance(name,str) or not name.strip() or len(name.strip())>40 or any(x in name for x in '/\\<>:"|?*') or name.strip() in ('全部','待确认','自定义'):
            raise ValueError('分类名称需要1至40个字符，不能包含路径字符或使用保留名称。')
        kinds=Production._clean(kinds,'材料清单',40)
        if any(not Path(k).suffix or Path(k).name!=k or any(ch in k for ch in '/\\<>:"|?*') for k in kinds):
            raise ValueError('每项请填写材料名及扩展名，例如 软件说明书.docx；不能包含路径。')
        if mode not in MODES:raise ValueError('请选择有效的处理方式。')
        aliases=aliases or {}
        if not isinstance(aliases,dict) or any(k not in kinds or not isinstance(v,list) or any(not isinstance(t,str) or not t.strip() or len(t)>60 for t in v) for k,v in aliases.items()):
            raise ValueError('识别关键词无效。')
        # Overlapping same-format aliases are ambiguous; refuse before saving.
        seen={}
        for kind,terms in aliases.items():
            for term in terms:
                key=(Path(kind).suffix.casefold(),normalized(term))
                if key in seen and seen[key]!=kind:raise ValueError('相同文件格式的识别关键词不能重复。')
                seen[key]=kind
        return name.strip(),kinds,aliases

    def save(self, name, kinds, aliases=None, mode='review', profile_id=None, active=True, *, intake_mode='materials',extensions=None,family_map=None,name_patterns=None,connection=None):
        from file_formats import ARCHIVES
        if intake_mode not in ('materials','original'):raise ValueError('收录规则无效。')
        extensions=list(dict.fromkeys(extensions or []))
        if intake_mode=='original' and (not extensions or any(x not in ARCHIVES for x in extensions)):
            raise ValueError('请选择允许的压缩包格式。')
        name,kinds,aliases=self.validate(name,kinds,aliases,mode)
        from file_families import validate as validate_families
        if family_map is not None:validate_families(kinds,family_map)
        from name_patterns import validate as validate_patterns
        if name_patterns is not None:validate_patterns(kinds,name_patterns)
        from contextlib import nullcontext
        with (nullcontext(connection) if connection is not None else self.db.connect()) as c:
            if connection is None:c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM material_profiles WHERE id=?',(profile_id,)).fetchone() if profile_id else None
            if profile_id and not old:raise ValueError('分类已不存在，请刷新。')
            if c.execute('SELECT 1 FROM material_profiles WHERE lower(name)=lower(?) AND id!=?',(name.strip(),profile_id or -1)).fetchone():raise ValueError('分类名称已经存在。')
            values=(name.strip(),json.dumps(kinds,ensure_ascii=False),json.dumps(aliases,ensure_ascii=False),mode,int(active))
            if old:c.execute('UPDATE material_profiles SET name=?,kinds=?,aliases=?,mode=?,active=? WHERE id=?',(*values,profile_id))
            else:
                pos=c.execute('SELECT COALESCE(MAX(position),0)+1 FROM material_profiles').fetchone()[0]
                profile_id=c.execute('INSERT INTO material_profiles(name,kinds,aliases,mode,active,position) VALUES (?,?,?,?,?,?)',(*values,pos)).lastrowid
            new=dict(c.execute('SELECT * FROM material_profiles WHERE id=?',(profile_id,)).fetchone())
            c.execute('INSERT OR REPLACE INTO profile_intake_options VALUES (?,?,?)',(profile_id,intake_mode,json.dumps(extensions)))
            if family_map is not None:
                c.execute('DELETE FROM profile_file_families WHERE profile_id=?',(profile_id,))
                c.executemany('INSERT INTO profile_file_families VALUES (?,?,?)',[(profile_id,k,f) for k,f in family_map.items()])
            else:
                c.execute('DELETE FROM profile_file_families WHERE profile_id=? AND kind NOT IN ('+','.join('?' for _ in kinds)+')',(profile_id,*kinds))
            if name_patterns is not None:
                c.execute('DELETE FROM profile_name_patterns WHERE profile_id=?',(profile_id,))
                c.executemany('INSERT INTO profile_name_patterns VALUES (?,?,?)',[(profile_id,k,json.dumps(p,ensure_ascii=False)) for k,p in name_patterns.items()])
            else:c.execute('DELETE FROM profile_name_patterns WHERE profile_id=? AND kind NOT IN ('+','.join('?' for _ in kinds)+')',(profile_id,*kinds))
            c.execute('INSERT INTO profile_changes(profile_id,before_json,after_json) VALUES (?,?,?)',(profile_id,json.dumps(dict(old) if old else {},ensure_ascii=False),json.dumps(new,ensure_ascii=False)))
        return profile_id

    def remove(self, profile_id):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM material_profiles WHERE id=?',(profile_id,)).fetchone()
            if not row:raise ValueError('分类不存在。')
            used=c.execute('SELECT 1 FROM task_profile_snapshots WHERE profile_id=?',(profile_id,)).fetchone() or c.execute('SELECT 1 FROM production_tasks WHERE package_name=?',(row['name'],)).fetchone()
            if used:c.execute('UPDATE material_profiles SET active=0 WHERE id=?',(profile_id,))
            else:
                c.execute('DELETE FROM profile_intake_options WHERE profile_id=?',(profile_id,))
                c.execute('DELETE FROM profile_file_families WHERE profile_id=?',(profile_id,))
                c.execute('DELETE FROM profile_name_patterns WHERE profile_id=?',(profile_id,))
                c.execute('DELETE FROM material_profiles WHERE id=?',(profile_id,))
            c.execute('INSERT INTO profile_changes(profile_id,before_json,after_json) VALUES (?,?,?)',(profile_id,json.dumps(dict(row),ensure_ascii=False),json.dumps({'action':'停用' if used else '删除'})))
        return '已停用，历史批次保留' if used else '已删除'

    def move(self, profile_id, offset):
        if offset not in (-1,1):raise ValueError('排序方向无效。')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');ids=[r[0] for r in c.execute('SELECT id FROM material_profiles ORDER BY position,id')]
            if profile_id not in ids:raise ValueError('分类不存在。')
            i=ids.index(profile_id);j=i+offset
            if not 0<=j<len(ids):return
            ids[i],ids[j]=ids[j],ids[i]
            c.executemany('UPDATE material_profiles SET position=? WHERE id=?',enumerate(ids))

    def resolve(self, rows):
        profiles=self.profiles();by_name={r['name']:r for r in profiles}
        result=[]
        for raw in rows:
            row=dict(raw)
            unknown=row.get('warning','').removeprefix('材料要求待确认：')
            profile=by_name.get(row.get('package')) or by_name.get(unknown)
            if profile and profile['active']:
                row.update(package=profile['name'],kinds=list(profile['kinds']),warning='')
            elif profile:
                row.update(package='待确认',kinds=[],warning='此分类已停用，请选择启用的分类或手动填写材料清单。')
            result.append(row)
        return result

    def snapshot(self,c,pid,package,kinds):
        profile=c.execute('SELECT * FROM material_profiles WHERE name=?',(package,)).fetchone()
        if profile and json.loads(profile['kinds'])==list(kinds):
            c.execute('INSERT OR REPLACE INTO task_profile_snapshots VALUES (?,?,?)',(pid,profile['id'],profile['aliases']))
        else:c.execute('INSERT OR REPLACE INTO task_profile_snapshots VALUES (?,NULL,\'{}\')',(pid,))
        c.execute('DELETE FROM task_file_families WHERE project_id=?',(pid,))
        c.execute('DELETE FROM task_name_patterns WHERE project_id=?',(pid,))
        if profile and json.loads(profile['kinds'])==list(kinds):
            c.executemany('INSERT INTO task_file_families VALUES (?,?,?)',[(pid,r[0],r[1]) for r in c.execute('SELECT kind,family FROM profile_file_families WHERE profile_id=?',(profile['id'],)).fetchall()])
            c.executemany('INSERT INTO task_name_patterns VALUES (?,?,?)',[(pid,r[0],r[1]) for r in c.execute('SELECT kind,pattern FROM profile_name_patterns WHERE profile_id=?',(profile['id'],)).fetchall()])

    def default_mode(self,c,packages):
        modes={r[0] for name in packages for r in c.execute('SELECT mode FROM material_profiles WHERE name=?',(name,))}
        return next(iter(modes)) if len(modes)==1 else 'review'
