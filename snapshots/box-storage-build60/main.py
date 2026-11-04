"""WorkBox portable desktop entry point and local database health check."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app import VERSION, WorkBox
from portable_release import RELEASE_KEY,RELEASE_BUILD


def default_data_dir() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BOX · 软件材料工作台")
    parser.add_argument("--version", action="version", version=f"WorkBox {VERSION} (build {RELEASE_BUILD})")
    parser.add_argument("--data-dir", type=Path, default=None, help="指定本地数据目录；默认是程序目录下的 data")
    parser.add_argument("--check", action="store_true", help="初始化/检查数据库，输出计数后退出，不打开窗口")
    parser.add_argument("--panel", action="store_true", help="启动时展开管理面板；默认仅显示悬浮球")
    parser.add_argument("--no-tray", action="store_true", help="不用系统托盘，仍可从球右键退出")
    parser.add_argument("--startup", action="store_true", help="登录启动时只显示悬浮球和托盘")
    parser.add_argument("--verify-portable", type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--apply-update',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--update-token',help=argparse.SUPPRESS)
    parser.add_argument('--restore-update',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--update-no-launch',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--legacy-source',type=Path,action='append',default=[],help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.verify_portable:
        from scripts.verify_storage_workflow import verify_portable
        return verify_portable(args.verify_portable)
    data_dir = args.data_dir if args.data_dir is not None else default_data_dir()
    if args.check:
        try:
            from portable_update import data_directory_version
            if data_directory_version(data_dir)>RELEASE_KEY:raise ValueError("此数据来自较新版本 BOX，不能用旧版检查或初始化。")
            service = WorkBox(data_dir / "workbox.db")
            print(json.dumps({"version": VERSION, "database": str(service.db.path), "counts": service.db.counts()}, ensure_ascii=False))
            return 0
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from desktop_service import DesktopService
    from desktop_controller import FloatingController
    from onboarding_ui import FirstRunDialog
    from theme import install_theme
    from dialog_lifecycle import transient_dialog

    application = QApplication(sys.argv[:1])
    application.setApplicationName("WorkBox")
    application.setApplicationVersion(VERSION)
    application.setOrganizationName("WorkBox")
    application.setStyle("Fusion")
    application.setQuitOnLastWindowClosed(False)
    controller = None
    try:
        if args.apply_update:
            from portable_update_ui import run_helper
            return run_helper(args.apply_update,args.update_token,restore=args.restore_update,no_launch=args.update_no_launch)
        from portable_update import data_directory_version,write_json
        if getattr(sys,'frozen',False) and args.data_dir is None:
            from portable_update_ui import preflight
            if not preflight(Path(sys.executable).parent,startup=args.startup,import_sources=args.legacy_source):return 0
            from portable_update import independent_instance
            if independent_instance(Path(sys.executable).parent):args.legacy_source.clear()
        if data_directory_version(data_dir)>RELEASE_KEY:raise ValueError('此数据已由较新版本 BOX 使用，请打开新版。')
        service = DesktopService(data_dir / "workbox.db")
        install_theme(service)
        controller = FloatingController(service, tray_enabled=not args.no_tray)
        controller.exit_requested.connect(application.quit)
        controller.pending_legacy_sources=[str(p) for p in args.legacy_source]
        def first_run_setup():
            with transient_dialog(FirstRunDialog(service)) as dialog:
                return dialog.exec() == QDialog.Accepted
        if not controller.start(setup_callback=first_run_setup):
            return 0
        write_json(data_dir/'BOX-Data-Version.json',dict(version=VERSION,build=RELEASE_BUILD))
        if getattr(sys,'frozen',False) and args.data_dir is None:
            from portable_update import register
            register(Path(sys.executable).parent)
        for source in args.legacy_source:controller.offer_legacy_import(source)
        if args.panel or (getattr(sys, 'frozen', False) and not args.startup):
            controller.show_panel()
        return application.exec()
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        QMessageBox.critical(None, "WorkBox 无法启动", f"无法完成启动或版本核对。请确认程序文件完整、所在目录可以写入。\n\n{exc}\n\n未重置或覆盖原有数据。")
        return 1
    finally:
        if controller is not None:
            controller.shutdown()
            controller.dispose_ui()


if __name__ == "__main__":
    raise SystemExit(main())
