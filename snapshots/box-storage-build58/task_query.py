"""Read-only query semantics over current, active production task snapshots."""
from datetime import date,timedelta,datetime,timezone
from pathlib import Path


def missing(row):
    return [m for m in row['materials'] if not m.get('collection_saved') or m.get('collection_issue')]


def current_originals(row):
    newest={}
    for z in sorted(row.get('original_zips',[]),key=lambda z:z.get('id',0),reverse=True):newest.setdefault(Path(z['path']).name.casefold(),z)
    return list(newest.values())


def status(row):
    if any(m.get('collection_issue') for m in row['materials']) or row.get('failed_import_count'):return '有异常'
    if any(not Path(z['path']).is_file() for z in current_originals(row)):return '有异常'
    if row.get('pending_import_count'):return '待确认'
    if not row['required_count']:return '原包已收录' if row.get('original_zips') else '要求待确认'
    if missing(row) or row['collection_count']<row['required_count']:return '待补材料'
    return '已收齐'


def unfinished(row):return status(row) not in ('已收齐','原包已收录')


def completion_time(row):
    if status(row)=='原包已收录':return receipt_time(row)
    return (row.get('collected_at') or '') if not unfinished(row) else ''


def receipt_time(row):
    return row.get('last_received_at') or max([m.get('created_at','') for m in row['materials'] if m.get('id')]+[z.get('created_at','') for z in row.get('original_zips',[])],default='')


def query(rows,local_date,*,view='unfinished',text='',category=None,state=None,period=None,day=None,start=None,end=None,spec=None):
    from workspace_queries import matches
    needle=text.strip().casefold()
    # A conversation query already defines its scope, including collected but
    # unchecked tasks or all tasks. Do not silently narrow its advertised result.
    candidates=[r for r in rows if (spec is not None or (unfinished(r) if view=='unfinished' else bool(receipt_time(r))))
        and (not needle or needle in (r['name']+' '+f"WB-{r['id']:06d}"+' '+r['batch_name']).casefold())
        and (category is None or r['package_name']==category)
        and (state is None or status(r)==state)
        and (spec is None or matches(r,spec,local_date))]
    def row_day(r):return local_date(receipt_time(r) if view=='completed' else r.get('created_at'))
    today=(day or datetime.now(timezone(timedelta(hours=8))).date()).isoformat()
    recent=max((row_day(r) for r in candidates),default='')
    period=period or ('latest' if view=='completed' else 'all')
    if period=='latest':start=end=recent
    elif period=='today':start=end=today
    elif period=='yesterday':start=end=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
    elif period=='week':start=(date.fromisoformat(today)-timedelta(days=6)).isoformat();end=today
    elif period=='month':start=today[:8]+'01';end=today
    elif period=='all':start=end=None
    return [r for r in candidates if (start is None or row_day(r)>=start) and (end is None or row_day(r)<=end)],recent
