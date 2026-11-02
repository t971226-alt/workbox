"""Package a verified onedir build and corresponding local GPL sources."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
import shutil
import subprocess
import argparse


def package(report, build_dir=None, output_name='BOX-Portable-20261029-Total-x64.zip'):
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(Path(report).read_text(encoding='utf-8'))
    if not evidence.get('passed') or not evidence.get('frozen'):
        raise ValueError('先运行冻结程序的 --verify-portable，并提供成功报告。')
    folder = Path(build_dir).resolve() if build_dir else root / 'dist' / 'total' / 'WorkBox'
    if (folder/'data').exists():raise ValueError('构建目录含运行数据，不能打包；请使用临时构建目录。')
    if not (folder / 'WorkBox.exe').is_file():
        raise ValueError('找不到已构建的程序。')
    with (folder / 'WorkBox.exe').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != evidence.get('executable_sha256'):
            raise ValueError('验证报告与当前程序不对应。')
    for filename in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'PORTABLE.md'):
        shutil.copy2(root / filename, folder / filename)
    shutil.copytree(root / 'licenses', folder / 'licenses', dirs_exist_ok=True)
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode('utf-8').split('\0')
    with ZipFile(folder / 'SOURCE.zip', 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(set(files)):
            source = root / name
            if name and source.is_file():
                archive.write(source, 'workbox/' + name)
    from portable_update import write_release,read_release
    from portable_release import RELEASE_KEY
    live=root/'dist/total/WorkBox'
    if live.resolve()!=folder and (live/'WorkBox.exe').is_file():
        existing=read_release(live)
        if existing.version>=RELEASE_KEY and existing.sha256!=evidence['executable_sha256']:
            raise ValueError('新程序内容变化，必须同步增加 portable_release 和 Windows 构建版本号，再构建发布。')
    write_release(folder)
    if Path(output_name).name != output_name:
        raise ValueError('压缩包名称不能包含路径。')
    target = root / 'dist' / output_name
    with ZipFile(target, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(folder.rglob('*')):
            if source.is_file():
                if 'data' == source.relative_to(folder).parts[0]:
                    raise ValueError('分发目录含运行数据，停止打包。')
                archive.write(source, 'WorkBox/' + source.relative_to(folder).as_posix())
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(json.dumps({'file': str(target), 'size': target.stat().st_size, 'sha256': digest}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--build-dir', type=Path)
    args = parser.parse_args()
    package(args.report, args.build_dir)
