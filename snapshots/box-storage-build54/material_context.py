"""One compact task menu for the workbench and its details."""
from workspace_chrome import WorkspaceMenu


class MaterialMenu(WorkspaceMenu):
    def refresh_chrome(self):
        super().refresh_chrome()
        self.setStyleSheet(self.styleSheet().replace('font-size: 13px','font-size: 11px').replace('8px 24px 8px 12px','6px 20px 6px 10px'))


def task_menu(workspace,row,parent=None):
    pid=row['id'];menu=MaterialMenu(parent or workspace)
    menu.addAction('查看材料详情',lambda:workspace.open_task_by_id(pid))
    menu.addSeparator()
    menu.addAction('打开原始位置',lambda:workspace.open_original(pid))
    menu.addAction('打开保存位置',lambda:workspace.open_saved(pid))
    if row.get('original_zips'):
        menu.addAction('解压并整理',lambda:workspace.extract_archive(pid))
    menu.addSeparator()
    menu.addAction('修改软件名称',lambda:workspace.rename(pid))
    menu.addAction('调整收纳规则',lambda:workspace.page.edit_task(row))
    menu.addSeparator()
    menu.addAction('删除任务',lambda:workspace.page.delete_tasks([row]))
    return menu
