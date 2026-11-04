"""Fetch a pinned subset of official Lucide SVGs and compile native Qt resources."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ICONS = ['folder', 'layers-2', 'minus', 'arrow-left', 'arrow-right', 'arrow-up', 'calendar', 'check', 'chevron-down',
         'copy', 'ellipsis', 'eye', 'eye-off', 'package-open', 'power', 'plus', 'list-filter', 'rotate-ccw', 'refresh-cw', 'search',
         'sliders-horizontal', 'square-pen', 'upload', 'x']


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'WorkBox-icon-vendor', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    commit = json.loads(fetch('https://api.github.com/repos/lucide-icons/lucide/commits/main'))['sha']
    base = f'https://raw.githubusercontent.com/lucide-icons/lucide/{commit}/'
    folder = ROOT / 'assets' / 'icons' / 'lucide'
    folder.mkdir(parents=True, exist_ok=True)
    def download(name):
        data = fetch(base + 'icons/' + name + '.svg')
        svg = ET.fromstring(data)
        if svg.tag != '{http://www.w3.org/2000/svg}svg' or svg.attrib.get('viewBox') != '0 0 24 24':
            raise ValueError('Invalid icon: ' + name)
        if any(node.tag.rsplit('}', 1)[-1] not in ('svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'ellipse') for node in svg.iter()):
            raise ValueError('Unexpected SVG content: ' + name)
        return name, data
    # Validate all downloads before replacing the local subset.
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(download, ICONS))
    license_text = fetch(base + 'LICENSE')
    for name, data in files:
        (folder / (name + '.svg')).write_bytes(data)
    (ROOT / 'licenses' / 'Lucide-LICENSE.txt').write_bytes(license_text)
    manifest = dict(upstream='https://github.com/lucide-icons/lucide', commit=commit,
                    license='ISC; includes Feather-derived MIT notices in the original license',
                    files={name+'.svg': hashlib.sha256(data).hexdigest() for name, data in sorted(files)})
    (folder / 'SOURCE.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    compile_resources()
    print(json.dumps(dict(commit=commit, icons=len(files))))


def compile_resources():
    names = sorted((ROOT / 'assets' / 'icons' / 'lucide').glob('*.svg'))
    qrc = ROOT / 'assets' / 'icons' / 'icons.qrc'
    qrc.write_text('<RCC><qresource prefix="/workbox/icons">\n' + ''.join(
        f'  <file alias="{p.stem}.svg">lucide/{p.name}</file>\n' for p in names) + '</qresource></RCC>\n', encoding='utf-8')
    compiler = Path(sys.executable).parent / ('pyside6-rcc.exe' if sys.platform == 'win32' else 'pyside6-rcc')
    subprocess.run([str(compiler), str(qrc), '-o', str(ROOT / 'lucide_icons_rc.py')], check=True)


if __name__ == '__main__':
    main()
