"""Deterministic, offline intake. All results are editable proposals, never automatic writes."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from datetime import datetime

from material_templates import PRESETS, package_for


def package_hint(value):
    compact = re.sub(r'\s+', '', str(value)).casefold().replace('word', 'Word').replace('十四', '14').replace('视屏', '视频')
    if not compact or compact in ('待确认', '未说明', '不确定'):
        return '待确认', []
    if '14' in compact and '视频' in compact:
        if not re.search(r'(?:两(?:个|份)?|2(?:个|份)?|双)(?:\d+秒)?(?:演示)?视频', compact):
            return '待确认', []
        return '14件＋双视频', list(PRESETS['14件＋双视频'])
    if compact in ('桌面14件', '14件', '十四件', '完整14件', '14件材料'):
        return '桌面14件', list(PRESETS['桌面14件'])
    if re.fullmatch(r'(?:只做|只要|做|需要|改为)?(?:五|5)件(?:材料)?', compact):
        return '五件材料', list(PRESETS['五件材料'])
    if compact in ('单手册Word', '单手册', '手册Word', '说明书Word', '说明手册Word') or (
            ('只做' in compact or '只用做' in compact or '只要' in compact) and any(s in compact for s in ('手册', '说明书')) and 'pdf' not in compact):
        return '单手册 Word', list(PRESETS['单手册 Word'])
    if str(value) in PRESETS:
        return str(value), list(PRESETS[str(value)])
    # Explicit custom filenames are requirements; unrecognised prose is never a guessed preset.
    parts = [s.strip() for s in re.split(r'[;；\n、|]+', str(value)) if s.strip()]
    if parts and all(re.fullmatch(r'[^\\/<>:]{1,95}\.(?:docx|pdf|txt|exe|msi|db|sql|mp4|wmv|zip|xlsx)', p, re.I) for p in parts):
        return '自定义', parts
    return '待确认', []


def proposal(name, *, commission='', batch='', category='', requirement='', source_files=None):
    package, kinds = package_hint(requirement)
    return dict(commission=commission.strip(), batch=batch.strip(), name=name.strip(), category=category.strip(),
                package=package, kinds=kinds, source_files=list(source_files or []),
                warning=('材料要求待确认' + (f'：{requirement}' if requirement else '')) if not kinds else '')


def parse_text(raw, *, default_batch='', default_commission='', default_requirement='待确认'):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 200000:
        raise ValueError('请粘贴需求文字，最多 200,000 字符。')
    batch = default_batch or datetime.now().strftime('%m-%d %H:%M:%S 收件')
    commission, category, requirement = default_commission, '', default_requirement
    rows, notes = [], []
    table_headers = None
    for original in raw.splitlines():
        line = original.strip()
        if not line:
            continue
        match = re.fullmatch(r'(委托项目|客户项目|项目)\s*[:：]\s*(.+)', line)
        if match:
            commission = match[2].strip()
            batch = default_batch or datetime.now().strftime('%m-%d %H:%M:%S 收件')
            category = ''
            requirement = default_requirement
            continue
        match = re.fullmatch(r'(?:批次\s*[:：]\s*)?((?:第[一二三四五六七八九十\d]+批|[^：:]{1,60}批次))\s*[:：]\s*(.*)', line)
        if match:
            batch = match[1].strip()
            requirement = match[2].strip() or default_requirement
            continue
        match = re.fullmatch(r'批次\s*[:：]\s*(.+)', line)
        if match:
            batch = match[1].strip()
            requirement = default_requirement
            continue
        match = re.fullmatch(r'(?:材料要求|交付要求|套餐|材料)\s*[:：]\s*(.+)', line)
        if match:
            requirement = match[1].strip()
            continue
        match = re.fullmatch(r'(?:软件分类|分类)\s*[:：]\s*(.+)', line)
        if match:
            category = match[1].strip()
            continue
        # Tab/pipe rows allow copying a table directly from chat or Excel.
        cells = [c.strip() for c in re.split(r'\t|\s*\|\s*', line.strip('|'))]
        if len(cells) >= 2:
            if any(h in cells for h in HEADERS['name']):
                table_headers = {key: next((cells.index(h) for h in aliases if h in cells), None) for key, aliases in HEADERS.items()}
                continue
            if all(re.fullmatch(r'[-: ]+', c or '-') for c in cells):
                continue
            if table_headers:
                item = {k: cells[i] if i is not None and i < len(cells) else '' for k, i in table_headers.items()}
                row = proposal(item['name'], commission=item['commission'] or commission, batch=item['batch'] or batch,
                               category=item['category'] or category, requirement=item['requirement'] or requirement)
            else:
                row = proposal(cells[0], commission=commission, batch=batch, category=category, requirement=cells[1])
            rows.append(row)
            continue
        numbered = re.match(r'^(?:\d{1,3}[.、．)）]|[（(]\d+[)）]|[-•])\s*(.+)', line)
        labeled = re.match(r'^(?:软件名称|软件名|名称)\s*[:：]\s*(.+)', line)
        value = (numbered or labeled)[1] if numbered or labeled else line
        # Named exceptions can refer back to an earlier row; ambiguous references remain visible notes.
        exception = re.fullmatch(r'(.+?)[，,:： ]+(?:其中)?(?:这个)?(只做.+|改为.+)', value)
        if exception and not numbered and any(r['name'] == exception[1].strip() for r in rows):
            matches = [r for r in rows if r['name'] == exception[1].strip() and r['batch'] == batch and r['commission'] == commission]
            if len(matches) == 1:
                p, kinds = package_hint(exception[2].replace('改为', ''))
                matches[0].update(package=p, kinds=kinds, warning='' if kinds else '例外要求待确认：' + exception[2])
            else:
                notes.append('例外要求无法唯一定位：' + original)
            continue
        specific = re.match(r'^(.+?)[，,]\s*(?:其中)?(?:这个)?\s*(只做.+|只要.+|\d+件.*|五件.*|单手册.*)$', value)
        suffix = re.match(r'^(.+?)[（(]([^()（）]*(?:件|手册|说明书|Word|word|视频)[^()（）]*)[)）]$', value)
        if specific or suffix:
            name, req = (specific or suffix).groups()
        else:
            name, req = value, requirement
        if not numbered and not labeled and not specific and not suffix and not re.search(r'(?:系统|软件|平台|工具|网站|应用|小程序|助手)(?:\s*V?\d+(?:\.\d+)*)?$', name, re.I):
            notes.append('未作为软件名称导入：' + original)
            continue
        rows.append(proposal(name.strip('《》 '), commission=commission, batch=batch, category=category, requirement=req))
    if not rows:
        raise ValueError('没有识别到软件名称，请使用每行编号或“软件名称：”；原文没有写入数据库。')
    return rows, notes


HEADERS = {
    'commission': ('委托项目', '客户项目', '项目'), 'batch': ('批次', '收件批次'),
    'name': ('软件名称', '软件名', '名称'), 'category': ('软件分类', '分类'),
    'requirement': ('材料要求', '交付要求', '套餐', '材料套餐'),
}


def read_sheet(path, *, default_batch='', default_commission='', default_requirement='待确认'):
    path = Path(path)
    if path.suffix.casefold() == '.csv':
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            text = path.read_text(encoding='gb18030')
        records = list(csv.reader(text.splitlines()))
    elif path.suffix.casefold() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            sheet = book.active
            records = []
            for row in sheet.iter_rows():
                if len(records) > 501:
                    raise ValueError('一次最多导入 500 个软件任务。')
                if any(c.data_type == 'f' for c in row):
                    raise ValueError('导入表格包含公式，请先粘贴为值再导入。')
                records.append([str(c.value or '').strip() for c in row])
        finally:
            book.close()
    else:
        raise ValueError('请使用 XLSX 或 CSV 表格；旧 XLS 请先另存为 XLSX。')
    records = [r for r in records if any(str(c).strip() for c in r)]
    if not records:
        raise ValueError('表格为空。')
    headers = [str(c).strip() for c in records[0]]
    mapping = {key: next((headers.index(h) for h in names if h in headers), None) for key, names in HEADERS.items()}
    if mapping['name'] is None:
        raise ValueError('表格第一行需要“软件名称”列；可先下载导入模板。')
    rows = []
    for number, values in enumerate(records[1:], 2):
        item = {k: str(values[i]).strip() if i is not None and i < len(values) else '' for k, i in mapping.items()}
        if not item['name']:
            raise ValueError(f'第 {number} 行缺少软件名称，请补齐或删除该行。')
        rows.append(proposal(item['name'], commission=item['commission'] or default_commission,
            batch=item['batch'] or default_batch or datetime.now().strftime('%m-%d %H:%M:%S 收件'),
            category=item['category'], requirement=item['requirement'] or default_requirement))
    return validate_rows(rows), []


def validate_rows(rows):
    from production import normalized, Production
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ValueError('请保留 1 至 500 个软件任务。')
    result, seen = [], set()
    for row in rows:
        item = dict(row)
        for key in ('commission', 'batch', 'name', 'category'):
            value = item.get(key, '')
            if not isinstance(value, str) or len(value.strip()) > 100:
                raise ValueError('项目、批次、名称和分类最多 100 字符。')
            item[key] = value.strip()
        if not item['name'] or not item['batch']:
            raise ValueError('软件名称和批次不能为空。')
        if len((item['commission'] + ' · ' if item['commission'] else '') + item['batch']) > 100:
            raise ValueError('委托项目与批次组合名称不能超过 100 字符。')
        key = tuple(normalized(item[k]) for k in ('commission', 'batch', 'name'))
        if key in seen:
            raise ValueError(f'同一委托批次内软件名称重复：{item["name"]}。不同批次可同名。')
        seen.add(key)
        item['kinds'] = Production._clean(item.get('kinds', []), '材料类型', 40) if item.get('kinds') else []
        package = item.get('package')
        if package is not None and (not isinstance(package, str) or len(package)>100):
            raise ValueError('分类名称无效。')
        item['package'] = (package or package_for(item['kinds'])) if item['kinds'] else '待确认'
        item['source_files'] = list(item.get('source_files', []))
        bindings=item.get('source_kinds',{})
        if not isinstance(bindings,dict) or any(p not in item['source_files'] or k not in item['kinds'] for p,k in bindings.items()):
            raise ValueError('来源文件与材料类型不对应，请重新核对。')
        item['source_kinds']=dict(bindings)
        if item.get('target_project_id') is not None and (type(item['target_project_id']) is not int or item['target_project_id']<1):
            raise ValueError('已有任务编号无效。')
        if item.get('processing_mode') is not None and item['processing_mode'] not in ('collect','review'):
            raise ValueError('处理方式无效。')
        result.append(item)
    return result


def folder_proposals(path, *, default_batch='', default_commission='', default_requirement='待确认'):
    import os
    from native_files import plain_path
    root = plain_path(path, allow_network=True)
    if not root.is_dir():
        raise ValueError('请选择普通材料文件夹。')
    children = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')]
    # A directory is a task only if it explicitly looks like a software name.
    software_dirs = [p for p in children if re.search(r'(系统|软件|平台|工具|助手)(?:V?[\d.]+)?$', p.name, re.I)]
    groups = software_dirs if software_dirs and not any(p.is_file() for p in root.iterdir()) else [root]
    rows, notes, total = [], [], 0
    for group in groups:
        files = []
        for directory, dirs, names in os.walk(group, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', '_过程资产')]
            plain_path(directory, allow_network=True)
            for name in names:
                if name.startswith('.'):
                    continue
                total += 1
                if total > 5000:
                    raise ValueError('文件夹超过 5000 个文件，请缩小范围。')
                files.append(str(plain_path(Path(directory) / name, allow_network=True)))
        rows.append(proposal(group.name, commission=default_commission, batch=default_batch or root.name,
                             requirement=default_requirement, source_files=files))
    notes.append('文件夹只识别名称和来源，不按现有文件数量推断套餐；核对后才复制材料，原件保留。')
    return rows, notes


def write_template(path):
    headers = ['委托项目', '批次', '软件名称', '软件分类', '材料要求']
    rows = [headers, ['建筑材料委托', '上午第一批', '建筑检测管理系统', '建筑检测', '单手册 Word'],
            ['设备监测委托', '下午第二批', '设备异常监测系统', '设备监测', '14件＋双视频'],
            ['', '临时单项', '库存管理系统', '仓储管理', '五件材料']]
    if Path(path).suffix.casefold() == '.csv':
        with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
            csv.writer(stream).writerows(rows)
    else:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        book = Workbook()
        sheet = book.active
        sheet.title = '需求清单'
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='117A75')
        for column, width in zip('ABCDE', [24, 24, 40, 20, 28]):
            sheet.column_dimensions[column].width = width
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(vertical='center', wrap_text=True)
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        book.save(path)
        book.close()
