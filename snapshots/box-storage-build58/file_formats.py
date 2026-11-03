"""File suffixes are labels, never implicit conversion between formats."""
DOCUMENTS={'.docx','.doc','.docm','.dotx','.dotm','.rtf','.wps','.odt','.ott'}
SHEETS={'.xlsx','.xls','.xlsm','.xlsb','.xltx','.xltm','.csv','.tsv','.et','.ods','.ots'}
SLIDES={'.pptx','.ppt','.pptm','.pps','.ppsx','.ppsm','.potx','.potm','.dps','.odp','.otp'}
TEXT={'.txt','.md','.log','.json','.xml','.yaml','.yml','.html','.htm','.ini','.cfg'}
IMAGES={'.png','.jpg','.jpeg','.gif','.bmp','.tif','.tiff','.webp','.svg','.heic'}
VIDEOS={'.mp4','.wmv','.avi','.mov','.mkv','.m4v','.webm'}
AUDIO={'.mp3','.wav','.m4a','.aac','.flac','.ogg','.wma'}
ARCHIVES={'.zip','.7z','.rar'}
EXTRACTABLE={'.zip','.7z'}
OFFICE=DOCUMENTS|SHEETS|SLIDES|TEXT|IMAGES|{'.pdf'}
LABELS={'.docx':'Word (.docx)','.doc':'Word 旧版 (.doc)','.pdf':'PDF',
    '.xlsx':'Excel (.xlsx)','.xls':'Excel 旧版 (.xls)','.pptx':'PPT (.pptx)','.ppt':'PPT 旧版 (.ppt)',
    '.txt':'文本 (.txt)','.csv':'CSV 表格','.png':'PNG 图片','.jpg':'JPG 图片',
    '.zip':'ZIP 压缩包','.7z':'7z 压缩包','.rar':'RAR 原包','.mp4':'MP4 视频',
    '.wps':'WPS 文档','.et':'WPS 表格','.dps':'WPS 演示','.odt':'ODF 文档','.ods':'ODF 表格','.odp':'ODF 演示'}
for suffix in sorted(OFFICE|VIDEOS|AUDIO|ARCHIVES):LABELS.setdefault(suffix,suffix[1:].upper())

def recognizable(path):return path.suffix.casefold() in OFFICE|VIDEOS|AUDIO

def naming_kinds(kinds,suffix):
    """Recognize a role with its actual extension; DOC never satisfies DOCX."""
    from pathlib import Path
    if suffix not in OFFICE:return kinds
    stems=list(dict.fromkeys(Path(k).stem for k in kinds if Path(k).suffix.casefold() in OFFICE))
    stems.extend(['表格','统计表','预算表','报价表','登记表','材料清单','演示文稿','课件','汇报材料','软件截图','界面截图','图片'])
    return list(dict.fromkeys([*kinds,*(stem+suffix for stem in stems)]))

def badge(ext):
    ext=ext.casefold()
    if ext in DOCUMENTS:return '#2c83e9','W'
    if ext in SHEETS:return '#188962','XLS'
    if ext in SLIDES:return '#db7442','PPT'
    if ext in IMAGES:return '#438c9b',ext[1:].upper()[:4]
    if ext in ARCHIVES:return '#c89835',ext[1:].upper()
    if ext in VIDEOS|AUDIO:return '#8c69cc',ext[1:].upper()[:4]
    if ext=='.pdf':return '#ef4f59','PDF'
    return '#82909e',ext[1:].upper()[:4] or 'FILE'
