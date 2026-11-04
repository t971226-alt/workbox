from pathlib import Path
import pytest
from openpyxl import Workbook
from sheet_checklist import read_tables, suggest_columns, extract_names, compare, save_checklist, load_checklist


def test_date_filter_inclusive_and_preserves_source_dates():
    from sheet_checklist import filter_dates
    items=[dict(name='甲系统',date='2026-11-02'),dict(name='乙系统',date='11-03'),dict(name='丙系统',date='2026-11-04'),dict(name='丁系统',date='')]
    assert filter_dates(items,'all')==items
    chosen=filter_dates(items,'day','2026-11-03');assert [r['name'] for r in chosen]==['乙系统'] and chosen[0]['date']=='11-03'
    assert [r['name'] for r in filter_dates(items,'range','2026-11-02','2026-11-03')]==['甲系统','乙系统']
    with pytest.raises(ValueError):filter_dates(items,'range','2026-11-04','2026-11-02')


def test_merged_date_multiline_and_duplicate(tmp_path):
    w=Workbook();s=w.active;s.append(['日期','软件名称']);s.append(['2026-11-03','1、甲软件系统\n2、乙软件系统']);s.append([None,'甲软件系统']);s.merge_cells('A2:A3');p=tmp_path/'names.xlsx';w.save(p)
    tables=read_tables(p);matrix=tables[0][1];assert suggest_columns(matrix)==(1,0)
    names=extract_names(matrix,1,0)
    assert [n['name'] for n in names]==['甲软件系统','乙软件系统','甲软件系统']
    assert {n['date'] for n in names}=={'2026-11-03'}
    assert names[0]['duplicate'] and names[2]['duplicate']


def test_formula_rejected_and_source_unchanged(tmp_path):
    w=Workbook();s=w.active;s.append(['软件名称']);s.append(['=HYPERLINK("https://example.com","系统")']);p=tmp_path/'formula.xlsx';w.save(p);before=p.read_bytes()
    with pytest.raises(ValueError,match='公式'):read_tables(p)
    assert p.read_bytes()==before


def test_comparison_never_silently_resolves_ambiguous_names():
    def row(i,name):return dict(id=i,name=name,materials=[],required_count=0,collection_count=0,original_zips=[],created_at='2026-11-03T00:00:00Z')
    records=[row(1,'甲系统'),row(2,'甲系统'),row(3,'乙系统')]
    result=compare([dict(name='甲系统',date=''),dict(name='丙系统',date='')],records)
    assert result[0]['state']=='同名待确认' and len(result[0]['candidates'])==2
    assert result[1]['state']=='尚未收录' and not result[1]['candidates']


def test_saved_checklist_keeps_names_separate_from_tasks(tmp_path):
    target=tmp_path/'data';target.mkdir();items=[dict(name='甲系统',date='2026-11-03',row=2,duplicate=False)]
    save_checklist(target,items);assert load_checklist(target)==items
    assert not list(target.glob('*.db'))
    with pytest.raises(ValueError):save_checklist(target,[dict(name='',date='')])
    assert load_checklist(target)==items
