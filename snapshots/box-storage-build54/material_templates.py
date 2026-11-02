"""Local delivery presets; each task saves its own requirement snapshot."""
PRESETS = {
    '待确认': [],
    '单手册 Word': ['软件说明书.docx'],
    '五件材料': ['软件说明书.docx', '软件说明书.pdf', '软件著作权代码段.docx', '软件著作权代码段.pdf', '登记申请信息.txt'],
    '桌面14件': [
        '一键运行版.exe', '安装版.exe', '测试数据库.db', '测试账号密码.txt', '登记申请信息.txt',
        '功能测试表.docx', '功能特点.docx', '软件设计文档.docx', '软件说明书.docx', '软件说明书.pdf',
        '软件著作权代码段.docx', '软件著作权代码段.pdf', '运行环境表.docx', '增删改查SQL.sql',
    ],
}
PRESETS['14件＋双视频'] = [*PRESETS['桌面14件'], '软件操作演示.mp4', '数据库演示.mp4']


def dependencies(kinds, kind):
    source = kind[:-4] + '.docx' if kind.endswith('.pdf') else None
    return [source] if source in kinds else []


def group_for(kind):
    suffix = kind.casefold().rsplit('.', 1)[-1]
    from file_formats import SHEETS,SLIDES,IMAGES,TEXT,AUDIO,ARCHIVES
    ext='.'+suffix
    if ext in SHEETS:return '表格材料'
    if ext in SLIDES:return '演示材料'
    if ext in IMAGES:return '图片材料'
    if ext in AUDIO:return '音频材料'
    if ext in ARCHIVES:return '压缩包'
    if suffix in ('mp4', 'wmv', 'avi', 'mov', 'mkv'):
        return '视频'
    if suffix in ('exe', 'msi', 'zip'):
        return '程序与运行包'
    if suffix in ('db', 'sql'):
        return '数据库与脚本'
    if suffix == 'txt':
        return '信息资料'
    return '文档材料'


def package_for(kinds):
    return next((name for name, values in PRESETS.items() if values == list(kinds)), '自定义')
