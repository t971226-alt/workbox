"""Read-only local conversation queries over the existing production ledger."""
from datetime import date, datetime, timedelta, timezone
import re


def today_local():
    return datetime.now(timezone(timedelta(hours=8))).date()


STATES = [
    ('待整理',r'待整理|尚未完成'),
    ('有异常',r'有异常|文件异常|保存失败'),
    ('要求待确认',r'要求待确认|待识别|待确认要求'),
    ('原包已收录',r'原包已收录|已收录原包'),
    ('待确认',r'待确认'),
    ('未收齐',r'未收齐|没收齐|没有收齐|材料未齐|材料不齐|缺材料|还缺|待补材料'),
    ('已收齐',r'已收齐|收齐了|已完成|做完了|已收纳|收纳完成'),
]
HELP='可以查询：“今天收录了多少？”“哪些软件还缺材料？”“查找 建筑检测管理系统”“显示全部”。\n粘贴软件名称和材料要求，可以建立收纳清单。'


def parse_query(raw, today=None):
    text = raw.strip()
    if not text or '\n' in text or re.match(r'^(?:项目|批次|套餐|材料要求|\d+[.、])[:：\s]?', text):
        return None
    day = today or today_local()
    if text in ('帮助', '怎么查询', '能查什么', '查询帮助', '你能做什么', 'help', '?', '？'):
        return dict(intent='help', description='查询帮助')
    if re.match(r'^(?:删除|清空|覆盖|修改|改名|确认提交|全部提交|把.+(?:标记|设为)|(?:帮我|请)(?:删除|清空|提交|覆盖|修改))', text):
        return dict(intent='unsupported', description='操作提示')
    prefix = re.match(r'^(?:请)?(?:帮我)?(?:查询|查找|搜索|查一下|查一查|看看|列出|显示|统计|查[：:\s])\s*[:：]?', text)
    natural = (re.search(r'多少|几项|几个|有哪些|哪些|什么进度|齐了吗|提交了吗|检查了吗', text)
               and re.search(r'今天|昨天|本月|这个月|上月|最近|收齐|收纳|完成|收录|检查|提交|材料|软件|任务|进度', text))
    if not prefix and not natural:
        return None
    # A bare software name such as 查询管理系统 remains a name intake.
    if (re.fullmatch(r'(?:查询|搜索|统计|显示)[^\s：:？?]+(?:系统|软件|平台)', text)
            and not re.search(r'多少|哪些|今天|昨天|本月|这个月|全部|所有|已提交|未提交|待检查|未收齐', text)):
        return None
    value = text[prefix.end():].strip() if prefix else text
    quoted = re.search(r'[“"‘\'](.+?)[”"’\']', value)
    detail = bool(re.search(r'(?:的)?(?:完成情况|收录情况|提交情况|检查情况|当前进度|进度|详情)[？?。!！]*$', value))
    # An explicit name lookup preserves every character in names such as 统计分析系统.
    name_lookup = bool(prefix and re.match(r'^(?:请)?(?:帮我)?(?:查找|搜索|查一下|查一查|查询)', text)
                       and not re.search(r'今天|昨天|本月|这个月|上个?月|最近[七7]天|多少|哪些|几项|几个', value)
                       and not any(re.fullmatch('(?:' + pattern + r')(?:的软件|的任务|软件|任务)?[？?]?', value) for _, pattern in STATES))
    if not name_lookup and re.search(r'待检查|未检查|检查通过|需修改|需要修改|已提交|未提交|待提交',text):
        return dict(intent='unsupported',description='操作提示')
    state = '' if name_lookup else next((name for name, pattern in STATES if re.search(pattern, text)), '')
    spec = dict(intent='records', name='', state=state, day='', monthly=False, metric='', date_field='created_at')
    monthly = bool(re.search(r'本月|这个月|上个?月|\d{4}年\d{1,2}月(?!\d)|\d{4}-\d{2}(?!-\d)', text))
    if re.search(r'上个?月', text):
        selected = day.replace(day=1) - timedelta(days=1)
    elif re.search(r'昨天', text):
        selected = day - timedelta(days=1)
    else:
        selected = day
    exact = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?', text)
    month = re.search(r'(\d{4})[-年](\d{1,2})月?', text) if not exact else None
    try:
        if exact:
            selected = date(*map(int, exact.groups()))
            monthly = False
        elif month:
            selected = date(int(month[1]), int(month[2]), 1)
            monthly = True
    except ValueError:
        return dict(intent='help', description='日期格式有误', message='这个日期无效，请使用例如“2026-10-29”或“2026年10月”。')
    has_date = bool(exact or month or re.search(r'今天|昨天|本月|这个月|上个?月|最近[七7]天', text))
    if has_date:
        spec.update(day=selected.isoformat(), monthly=monthly)
        if re.search(r'最近[七7]天', text):
            spec.update(start=(day - timedelta(days=6)).isoformat(), day=day.isoformat(), monthly=False)
    if not name_lookup and re.search(r'多少|几项|几个|数量|统计', text):
        spec['intent'] = 'statistics'
    if not name_lookup and '提交' in text and state not in ('未提交', '待提交', '部分提交'):
        spec.update(metric='submitted', date_field='completed_submission_at')
    elif not name_lookup and '检查' in text and state != '待检查':
        spec.update(metric='checked', date_field='checked_at')
    elif not name_lookup and re.search(r'收齐|完成|做完', text) and state != '未收齐':
        spec.update(metric='collected', date_field='collected_at')
    elif not name_lookup and re.search(r'收录|收进|收纳了',text):
        spec.update(metric='received',date_field='last_received_at')
    elif not name_lookup and re.search(r'新增|新建|添加|建立', text):
        spec.update(metric='added', date_field='created_at')
    # Remove query grammar, not arbitrary words inside software names.
    value = re.sub(r'(?:的)?(?:完成情况|收录情况|提交情况|检查情况|当前进度|进度|详情)$', '', value.rstrip('？?。!！'))
    if not name_lookup:
        value = re.sub(r'\d{4}[-年]\d{1,2}(?:[-月]\d{1,2}日?|月)?|今天|昨天|本月|这个月|上个?月|最近[七7]天', ' ', value)
        for _, pattern in STATES:
            value = re.sub(pattern, ' ', value)
        value = re.sub(r'收齐|收录|收进|完成|做完|检查|提交|新增|新建|添加|建立|多少(?:个|项|件)?|几[个项件]|有哪些|还有哪些|哪些|数量|总共|一共|现在|目前|一下|了吗|了|吗|统计', ' ', value)
        value = re.sub(r'(?:所有|全部)?(?:软件|任务|名称|记录|材料)|还|的|有', ' ', value).strip()
    value = value.strip(' ：:？?。!！“”"\'‘’')
    if value in ('', '的', '全部', '所有', '全部软件', '所有软件', '全部任务', '所有任务', '软件', '任务', '名称', '记录', '材料', '总表', '全部记录', '所有记录'):
        value = ''
    # Quoted names and common politeness do not become part of the search term.
    value = re.sub(r'^(?:帮我|请|找一下)\s*', '', value).strip(' 的“”"\'‘’')
    if not name_lookup and '收纳' in text and re.search(r'多少|几项|几个|完成|已收纳|收纳了',text):
        spec.update(state='已收纳',metric='collected',date_field='collected_at')
        value=re.sub('收纳(?:完成|了)?','',value)
    spec['name'] = quoted[1] if quoted else value
    value = spec['name']
    spec['description'] = ('全部软件' if not value and not state and not has_date else ' · '.join(
        x for x in [(selected.isoformat()[:7] if monthly else selected.isoformat()) if has_date else '', value, state or dict(collected='已收齐', checked='检查通过', submitted='已提交', added='新增').get(spec['metric'], '')] if x))
    return spec


def matches(row,spec,local_date,*,ignore_date=False):
    import unicodedata
    from task_query import status,unfinished,receipt_time
    fold=lambda value:unicodedata.normalize('NFKC',value).casefold()
    if spec.get('name') and fold(spec['name']) not in fold(row['name']):return False
    state=spec.get('state');actual=status(row)
    if state:
        if state=='待整理':
            if not unfinished(row):return False
        elif state=='未收齐':
            if actual=='已收齐':return False
        elif state!=actual:return False
    metric=spec.get('metric','')
    if metric in ('checked','submitted'):return False
    if metric=='collected' and actual!='已收齐':return False
    if metric=='received' and not receipt_time(row):return False
    if not ignore_date and spec.get('day'):
        field=spec.get('date_field','created_at');value=receipt_time(row) if field=='last_received_at' else row.get(field,'')
        actual_day=local_date(value)
        if not actual_day:return False
        if spec.get('start'):return spec['start']<=actual_day<=spec['day']
        width=7 if spec.get('monthly') else 10
        return actual_day[:width]==spec['day'][:width]
    return True


def answer(store,spec):
    from task_query import missing,status
    if spec['intent'] in ('help','unsupported'):
        response=((spec.get('message','')+'\n'+HELP).strip() if spec['intent']=='help' else 'BOX 按文件收录和收齐情况统计，不再使用核对、修改和提交状态。\n'+HELP)
        return dict(spec=spec,response=response,matched_project_ids=[],count=0)
    if spec.get('intent')=='statistics' and spec.get('metric')=='received' and spec.get('day') and not any(spec.get(k) for k in ('monthly','start','name','state')):
        from daily_receipts import snapshot
        data=snapshot(store.service,spec['day'])
        response=f"{data['day']}：已收录 {data['materials']} 份材料，涉及 {data['software']} 个软件。"
        response+=''.join(f"\n{c['title']}：{c['software']} 个软件 · {c['materials']} 份" for c in data['categories'])
        return dict(spec=dict(intent='daily_receipts',day=data['day']),response=response,
                    matched_project_ids=list(dict.fromkeys(v['project_id'] for v in data['entries'])),count=data['software'])
    rows=store.rows();found=[r for r in rows if matches(r,spec,store.local_date)]
    response=f"{spec.get('description') or '全部软件'}：找到 {len(found)} 个软件任务。"
    if spec['intent']=='statistics':
        response+='\n'+' · '.join(f'{name} {sum(status(r)==name for r in found)}' for name in ['已收齐','原包已收录','待补材料','有异常','待确认'])
    if found:
        response+='\n'+'\n'.join(f"{i+1}. {r['name']} · 已收录 {r['collection_count']}/{r['required_count']} · {status(r)}" for i,r in enumerate(found[:6]))
        if len(found)==1:
            r=found[0];response+=f"\n{r['batch_name']} · WB-{r['id']:06d} · {r['package_name']}"
            absent=missing(r)
            if absent:response+='\n待补或异常：'+'、'.join(m['kind'] for m in absent)
        response+='\n对应任务已列在右侧。'
    else:response+='\n可以换个关键词，或输入“显示全部”。'
    return dict(spec=spec,response=response,matched_project_ids=[r['id'] for r in found],count=len(found))
