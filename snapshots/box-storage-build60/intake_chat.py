"""Persistent local intake conversations; task creation reuses the original import token."""
import json
from uuid import uuid4
from contextlib import contextmanager

from requirement_intake import validate_rows


SCHEMA = '''
CREATE TABLE IF NOT EXISTS intake_conversations (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL,
 draft_text TEXT NOT NULL DEFAULT '', defaults_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS intake_chat_state (
 id INTEGER PRIMARY KEY CHECK(id=1), active_id INTEGER REFERENCES intake_conversations(id)
);
CREATE TABLE IF NOT EXISTS intake_turns (
 id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES intake_conversations(id),
 source_kind TEXT NOT NULL, source_text TEXT NOT NULL, plan_json TEXT NOT NULL,
 notes_json TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('needs-review','creating','committed','discarded','failed')),
 error TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_intake_turns_conversation ON intake_turns(conversation_id,id);
'''


class IntakeChat:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def ui_connection(self):
        with self.db.connect() as connection:
            connection.execute('PRAGMA busy_timeout=100')
            yield connection

    def conversations(self):
        with self.db.connect() as connection:
            return [dict(r) for r in connection.execute('SELECT * FROM intake_conversations ORDER BY id DESC')]

    def active(self):
        with self.db.connect() as connection:
            row = connection.execute('''SELECT c.* FROM intake_conversations c JOIN intake_chat_state s
                ON s.active_id=c.id WHERE s.id=1''').fetchone()
            return dict(row) if row else None

    def select(self, cid):
        with self.ui_connection() as connection:
            row = connection.execute('SELECT * FROM intake_conversations WHERE id=?', (cid,)).fetchone()
            if not row:
                raise ValueError('会话不存在。')
            connection.execute('INSERT INTO intake_chat_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET active_id=excluded.active_id', (cid,))
            return dict(row)

    def create(self, title='新需求会话'):
        with self.ui_connection() as connection:
            cid = connection.execute('INSERT INTO intake_conversations(title) VALUES (?)', (title[:80],)).lastrowid
            connection.execute('INSERT INTO intake_chat_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET active_id=excluded.active_id', (cid,))
        return cid

    def save_draft(self, cid, text, defaults):
        if len(text) > 200000:
            raise ValueError('单次需求最多200,000字符，请拆分。')
        with self.ui_connection() as connection:
            connection.execute('UPDATE intake_conversations SET draft_text=?,defaults_json=? WHERE id=?',
                               (text, json.dumps(defaults, ensure_ascii=False), cid))

    def add(self, cid, source, rows, notes=(), *, source_kind='text', error=''):
        if len(source) > 200000:
            raise ValueError('单次需求最多200,000字符。')
        with self.ui_connection() as connection:
            turn = connection.execute('''INSERT INTO intake_turns(conversation_id,source_kind,source_text,plan_json,
                notes_json,token,state,error) VALUES (?,?,?,?,?,?,?,?)''',
                (cid, source_kind, source, json.dumps(rows, ensure_ascii=False), json.dumps(list(notes), ensure_ascii=False),
                 uuid4().hex, 'failed' if error else 'needs-review', error)).lastrowid
            if source_kind == 'text':
                connection.execute('UPDATE intake_conversations SET draft_text=? WHERE id=?', ('', cid))
            connection.execute("UPDATE intake_conversations SET title=? WHERE id=? AND title='新需求会话'",
                               (source.strip().splitlines()[0][:60] if source.strip() else '需求整理', cid))
        return turn

    def begin_query(self, cid, source, spec):
        with self.ui_connection() as c:
            tid = c.execute('''INSERT INTO intake_turns(conversation_id,source_kind,source_text,plan_json,notes_json,token,state,result_json)
                VALUES (?,'query',?,'[]','[]',?,'creating',?)''',
                (cid, source, uuid4().hex, json.dumps(dict(spec=spec), ensure_ascii=False))).lastrowid
            c.execute("UPDATE intake_conversations SET draft_text='',title=CASE WHEN title='新需求会话' THEN ? ELSE title END WHERE id=?", (source[:60], cid))
        return tid

    def run_query(self, turn_id, *, cancel_event=None, progress=None):
        from native_files import cancelled
        from workspace_queries import answer
        turn = self.turn(turn_id)
        if turn['source_kind'] != 'query':
            raise ValueError('这条记录不是查询。')
        try:
            cancelled(cancel_event)
            result = answer(self.service.production, turn['result']['spec'])
            cancelled(cancel_event)
            with self.db.connect() as c:
                result['as_of'] = c.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
                c.execute("UPDATE intake_turns SET state='committed',error='',result_json=? WHERE id=?",
                          (json.dumps(result, ensure_ascii=False), turn_id))
            return dict(query_result=result, conversation_id=turn['conversation_id'], conversation_turn_id=turn_id)
        except Exception as error:
            with self.db.connect() as c:
                c.execute("UPDATE intake_turns SET state='failed',error=? WHERE id=?", (str(error), turn_id))
            raise

    def restart_query(self, turn_id, spec):
        with self.ui_connection() as c:
            c.execute("UPDATE intake_turns SET state='creating',error='',result_json=? WHERE id=? AND source_kind='query'",
                      (json.dumps(dict(spec=spec), ensure_ascii=False), turn_id))

    @staticmethod
    def _decode(row):
        value = dict(row)
        for key, output in [('plan_json', 'rows'), ('notes_json', 'notes'), ('result_json', 'result')]:
            value[output] = json.loads(value.pop(key))
        return value

    def turn(self, turn_id):
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM intake_turns WHERE id=?', (turn_id,)).fetchone()
            if not row:
                raise ValueError('需求记录不存在。')
            return self._decode(row)

    def turns(self, cid, limit=20):
        with self.db.connect() as connection:
            rows = connection.execute('SELECT * FROM intake_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?', (cid, limit)).fetchall()
            return [self._decode(r) for r in reversed(rows)]

    def count(self, cid):
        with self.db.connect() as connection:
            return connection.execute('SELECT COUNT(*) FROM intake_turns WHERE conversation_id=?', (cid,)).fetchone()[0]

    def edit(self, turn_id, rows):
        rows = validate_rows(rows)
        with self.ui_connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            turn = connection.execute('SELECT state,token FROM intake_turns WHERE id=?', (turn_id,)).fetchone()
            if not turn or turn['state'] not in ('needs-review', 'failed'):
                raise ValueError('这条需求已处理或正在建立任务，不能修改。')
            if connection.execute('SELECT 1 FROM production_imports WHERE token=?', (turn['token'],)).fetchone():
                raise ValueError('任务已建立，不能改写原清单，请在材料表调整具体要求。')
            connection.execute("UPDATE intake_turns SET plan_json=?,state='needs-review',error='' WHERE id=?",
                               (json.dumps(rows, ensure_ascii=False), turn_id))

    def discard(self, turn_id):
        with self.ui_connection() as connection:
            changed = connection.execute("UPDATE intake_turns SET state='discarded' WHERE id=? AND state IN ('needs-review','failed')", (turn_id,))
            if not changed.rowcount:
                raise ValueError('这条需求已经处理。')

    def recover(self):
        """Run once before the controller starts any jobs, reconciling a previous interrupted run."""
        with self.db.connect() as connection:
            rows = connection.execute("SELECT id,token,state,source_kind FROM intake_turns WHERE state IN ('creating','failed')").fetchall()
            for row in rows:
                if row['source_kind'] == 'query':
                    if row['state'] == 'creating':
                        connection.execute("UPDATE intake_turns SET state='failed',error='上次查询未完成，可重新查询。' WHERE id=?", (row['id'],))
                    continue
                imported = connection.execute('SELECT result_json FROM production_imports WHERE token=?', (row['token'],)).fetchone()
                if imported:
                    connection.execute("UPDATE intake_turns SET state='committed',result_json=?,error='' WHERE id=?", (imported[0], row['id']))
                elif row['state'] == 'creating':
                    connection.execute("UPDATE intake_turns SET state='failed',error=? WHERE id=?", ('上次建立任务未完成，清单已保留，可核对后重试。', row['id']))

    def create_tasks(self, turn_id, *, retry_files=False, cancel_event=None, progress=None):
        from native_files import cancelled
        turn = self.turn(turn_id)
        if turn['state'] == 'discarded':
            raise ValueError('这条需求已取消，请重新整理。')
        if turn['state'] == 'committed' and not retry_files:
            return dict(turn['result'], conversation_turn_id=turn_id)
        rows = validate_rows(turn['rows'])
        cancelled(cancel_event)
        with self.db.connect() as connection:
            changed = connection.execute("UPDATE intake_turns SET state='creating',error='' WHERE id=? AND state!='creating'", (turn_id,))
            if not changed.rowcount:
                raise ValueError('这条需求正在处理，请等待结果。')
        try:
            result = self.service.production.import_plan(rows, token=turn['token'], source_text=turn['source_text'],
                                                         cancel_event=cancel_event, progress=progress)
            with self.db.connect() as connection:
                connection.execute("UPDATE intake_turns SET state='committed',result_json=?,error='' WHERE id=?", (json.dumps(result), turn_id))
            return dict(result, conversation_turn_id=turn_id)
        except Exception as error:
            with self.db.connect() as connection:
                imported = connection.execute('SELECT result_json FROM production_imports WHERE token=?', (turn['token'],)).fetchone()
                connection.execute('UPDATE intake_turns SET state=?,error=?,result_json=? WHERE id=?',
                    ('committed' if imported else 'failed', str(error), imported[0] if imported else '{}', turn_id))
            raise
