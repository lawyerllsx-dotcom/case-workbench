# extras — 作者环境专用工具

这里的脚本是作者在自己律所里用的**一次性/环境专用**工具，保留作参考，不保证通用：

| 文件 | 用途 | 为什么放这里 |
|---|---|---|
| `migration_scan.py` / `migration_apply.py` | 历史案卷批量规范化（散件归位、改名、补目录） | 目录命名规则与作者的习惯绑定，别人用会误移文件 |
| `rename_refs.py` | 案卷改名后同步引用（wiki / 项目模板） | 依赖 Obsidian vault 与特定项目结构 |
| `make_shortcuts.py` / `make_icon.py` / `make_aumid_shortcut.py` | 桌面快捷方式、图标、Windows 通知来源名 | 依赖 Windows 与个人桌面布局 |
| `mark_commission_templates.py` | 把所内委托材料模板标记化为 `{{变量}}` | 模板本身是所内材料，不便随仓库分发 |

**使用前请先读代码**：这些脚本大多默认「只移动不删除」并用 dry-run，但仍建议先在自己的副本上跑一遍。
