# BOX 发布版本约定

继续遵守上级 AGENTS.md：只维护并交付 `dist/total/WorkBox` 和指定的唯一免安装 ZIP，保留用户 data、材料及历史。

- 程序内容改变时，增加 `portable_release.py` 中的 `RELEASE_BUILD`；同步 `assets/windows-version.txt` 中四段 Windows 文件版本的最后一段。发行版版本号在 `portable_release.py` 统一维护。
- 同一发布标识不得对应不同的程序。打包脚本会检查当前运行目录，发现同标识的不同 EXE 时拒绝发布。
- 在临时目录构建后，先运行 `python -m scripts.prepare_portable_release <构建目录>` 生成文件校验清单，再验证冻结程序、打包和更新唯一日常运行目录。
- 不手工伪造版本清单、摘要或验证报告。更新验证在隔离目录进行，不能对用户业务数据试运行更新或旧材料迁移。
- 自动更新只替换版本清单允许的程序文件。备份总表后再替换，失败恢复原程序；data、自定义材料、归档和历史版本不作为程序文件删除。
