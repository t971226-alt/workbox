"""Optional per-user Windows sign-in launch; no installer or elevated access."""
from pathlib import Path
import sys

RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME='WorkBoxPortable'


def command(executable):
    path=Path(executable).resolve()
    if path.name.casefold()!='workbox.exe' or not path.is_file():
        raise ValueError('请选择完整的免安装版 WorkBox.exe。')
    return '"'+str(path)+'" --startup'


def enabled(registry=None, executable=None):
    if sys.platform!='win32' and registry is None:return False
    if registry is None:import winreg as registry
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,RUN_KEY) as key:
            value,_=registry.QueryValueEx(key,VALUE_NAME)
        return value==command(executable or sys.executable)
    except (OSError,ValueError):return False


def set_enabled(value,registry=None,executable=None):
    if type(value) is not bool:raise ValueError('开机自启设置无效。')
    if registry is None:
        if sys.platform!='win32' or not getattr(sys,'frozen',False):raise ValueError('请在免安装版中设置开机自启。')
        import winreg as registry
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,RUN_KEY,0,registry.KEY_SET_VALUE) as key:
        if value:registry.SetValueEx(key,VALUE_NAME,0,registry.REG_SZ,command(executable or sys.executable))
        else:
            try:registry.DeleteValue(key,VALUE_NAME)
            except FileNotFoundError:pass
