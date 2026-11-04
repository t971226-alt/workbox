"""Name-list import and comparison. Never creates tasks or changes materials."""
import csv
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

LIMIT=2000
FILENAME='软件名称核对清单.json'


def normalized(value):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value))).casefold()


def date_text(value):
    if isinstance(value,(datetime,date)):return value.strftime('%Y-%m-%d')
    value=str(value or '').strip()
    m=re.fullmatch(r'(?:(20\d\d)[年/.-])?(\d{1,2})[月/.-](\d{1,2})日?',value)
    if not m:return ''
    year,month,day=m.groups()
    try:date(int(year or 2000),int(month),int(day))
    except ValueError:return ''
    return (year+'-' if year else '')+f'{int(month):02d}-{int(day):02d}'


def read_tables(path):
    path=Path(path)
    if path.stat().st_size>10*1024*1024:raise ValueError('请选择小于 10 MB 的清单表格。')
    suffix=path.suffix.casefold()
    if suffix in ('.csv','.tsv'):
        data=path.read_bytes()
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('gb18030')
        delimiter='\t' if suffix=='.tsv' else ','
        rows=list(csv.reader(io.StringIO(text),delimiter=delimiter))
        tables=[(path.stem,rows)]
    elif suffix=='.xlsx':
        from zipfile import ZipFile,BadZipFile
        try:
            with ZipFile(path) as z:
                if sum(i.file_size for i in z.infolist())>80*1024*1024:raise ValueError('表格内容过大，请仅保留需要核对的清单。')
        except BadZipFile as error:raise ValueError('无法读取 Excel 文件，请重新另存为 XLSX。') from error
        from openpyxl import load_workbook
        try:book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
        except Exception as error:raise ValueError('无法读取 Excel 表格，请重新另存为 XLSX。') from error
        try:
            tables=[]
            if len(book.worksheets)>30:raise ValueError('工作表过多，请仅保留需要核对的工作表。')
            for sheet in book.worksheets:
                matrix=[]
                if sheet.max_column>100 or sheet.max_row>5001:raise ValueError('请将清单限制在 5000 行、100 列以内。')
                for row in sheet.iter_rows():
                    if any(c.data_type=='f' for c in row):raise ValueError('表格含公式，请先粘贴为值后再导入。')
                    matrix.append([c.value if c.value is not None else '' for c in row])
                tables.append((sheet.title,matrix))
        except (KeyError,SyntaxError,EOFError) as error:raise ValueError('表格内容损坏，请重新另存为 XLSX。') from error
        finally:book.close()
    else:raise ValueError('请选择 XLSX、CSV 或 TSV；旧 XLS 请先另存为 XLSX。')
    if not tables or not any(any(any(str(v).strip() for v in row) for row in m) for _,m in tables):raise ValueError('表格为空。')
    if any(len(m)>5001 or any(len(r)>100 for r in m) for _,m in tables):raise ValueError('请将清单限制在 5000 行、100 列以内。')
    return tables


def suggest_columns(matrix):
    width=max((len(r) for r in matrix),default=1);scores=[];dates=[]
    for col in range(width):
        values=[r[col] for r in matrix[:100] if len(r)>col]
        score=sum(100 if str(v).strip() in ('软件名称','软件名','系统名称','项目名称','名称') else int(len(str(v))>=5 and bool(re.search(r'系统|软件|平台|APP',str(v),re.I))) for v in values)
        scores.append(score);dates.append(sum(bool(date_text(v)) for v in values))
    name=max(range(width),key=lambda i:scores[i]);day=max(range(width),key=lambda i:dates[i])
    return name,day if dates[day] and day!=name else -1


def extract_names(matrix,name_column,date_column=-1):
    if type(name_column) is not int or name_column<0 or type(date_column) is not int:raise ValueError('请选择软件名称列。')
    result=[];current=''
    for number,row in enumerate(matrix,1):
        if 0<=date_column<len(row):current=date_text(row[date_column]) or current
        if name_column>=len(row):continue
        for part in str(row[name_column] or '').splitlines():
            name=re.sub(r'^\s*\d{1,3}\s*[、.．)）]\s*','',part).strip()
            if not name or name in ('软件名称','软件名','系统名称','项目名称','名称','合计','总计','重做') or date_text(name) or name.isdigit():continue
            if not 2<=len(name)<=200:raise ValueError(f'第 {number} 行的软件名称过长，请检查名称列。')
            result.append(dict(name=name,date=current,row=number,duplicate=False))
            if len(result)>LIMIT:raise ValueError(f'一次最多核对 {LIMIT} 个软件名称。')
    if not result:raise ValueError('所选列未识别到软件名称，请调整名称列。')
    counts=Counter(normalized(r['name']) for r in result)
    for r in result:r['duplicate']=counts[normalized(r['name'])]>1
    return result


def date_keys(items):
    years={r['date'][:4] for r in items if re.fullmatch(r'20\d\d-\d\d-\d\d',r.get('date',''))}
    year=next(iter(years)) if len(years)==1 else str(date.today().year)
    return [year+'-'+r['date'] if re.fullmatch(r'\d\d-\d\d',r.get('date','')) else r.get('date','') for r in items]


def filter_dates(items,mode='all',first=None,last=None):
    if mode=='all':return list(items)
    if mode not in ('day','range'):raise ValueError('请选择导入日期范围。')
    if not first or (mode=='range' and not last):raise ValueError('所选日期列没有可筛选的日期。')
    last=first if mode=='day' else last
    if first>last:raise ValueError('开始日期不能晚于结束日期。')
    return [r for r,key in zip(items,date_keys(items)) if key and first<=key<=last]


def compare(items,records):
    from task_query import status
    byname=defaultdict(list)
    for r in records:byname[normalized(r['name'])].append(r)
    result=[]
    for item in items:
        candidates=byname[normalized(item['name'])]
        if len(candidates)>1:state='同名待确认'
        elif not candidates:state='尚未收录'
        else:
            row=candidates[0];s=status(row)
            received=bool(row.get('collection_count') or row.get('original_zips'))
            state='有异常' if s=='有异常' else '已收齐' if s in ('已收齐','原包已收录') else '材料不齐' if received else '尚未收录'
        result.append(dict(item,state=state,candidates=candidates))
    return result


def validate(items):
    if not isinstance(items,list) or not 1<=len(items)<=LIMIT:raise ValueError('核对清单为空或过大。')
    result=[]
    for r in items:
        if not isinstance(r,dict) or not isinstance(r.get('name'),str) or not 2<=len(r['name'].strip())<=200:raise ValueError('清单包含无效的软件名称。')
        day=r.get('date','')
        if not isinstance(day,str) or len(day)>20:raise ValueError('清单日期无效。')
        result.append(dict(name=r['name'].strip(),date=day,row=r.get('row',0) if type(r.get('row',0)) is int else 0,duplicate=bool(r.get('duplicate'))))
    return result


def save_checklist(data_dir,items):
    from portable_update import write_json
    items=validate(items);path=Path(data_dir)/FILENAME
    write_json(path,dict(version=1,items=items))


def load_checklist(data_dir):
    path=Path(data_dir)/FILENAME
    if not path.exists():return []
    if path.stat().st_size>2*1024*1024:raise ValueError('保存的核对清单过大。')
    try:payload=json.loads(path.read_text('utf-8-sig'))
    except (ValueError,OSError) as error:raise ValueError('保存的核对清单无法读取，原文件已保留。') from error
    if not isinstance(payload,dict) or payload.get('version')!=1:raise ValueError('保存的核对清单格式无法识别，原文件已保留。')
    return validate(payload.get('items'))
