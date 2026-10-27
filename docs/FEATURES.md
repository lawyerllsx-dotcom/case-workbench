# 功能矩阵：装了能用什么，没装会怎样

> 唯一必需依赖是 **openpyxl**。下面这些不装也能启动，只是对应功能降级。
> 实测环境：Python 3.13 + 只装 openpyxl 的干净 venv（2026-10-28）。

## 一、开箱即用（只装 openpyxl）

| 功能 | 说明 |
|---|---|
| 台账看板 / 案件列表 / 案件详情 | 读台账、按状态分桶、费用汇总 |
| 收件箱入库 | 扫描 + 归位建议（dry-run → 确认） |
| 归档 / 散件归类 | 只移动不删除，写留痕 |
| 期限与保全提醒 | 从台账三路抽取，页面内提醒 |
| 律师函台账 / 合同审查 | 读写受控列 |
| 任务 / 日程 / 写作项目 / 执行款 | 自建 SQLite 库 |
| 留痕体检（分区 / 版本族 / 错位） | 只报不改；归位需人工勾选 |
| 计算器（诉讼费 / 利息 / 劳动） | 纯本地计算 |
| 隐私自检 / 自检 / 回归测试 | `sanitize_check.py` / `verify.py` / `run_all_tests.py` |

## 二、装依赖才能用（不装不影响启动）

| 功能 | 依赖 | 没装的表现 | 补 |
|---|---|---|---|
| **卷宗装订**（合并 PDF + 书签） | PyMuPDF | 该接口返回「未安装 PyMuPDF」，页面提示不可用 | `pip install pymupdf` |
| **所函生成**（占位符模板） | docxtpl | 点生成时报错并提示装 docxtpl | `pip install docxtpl` |
| **委托六件套** | docxtpl | 同上 | 同上 |
| **示例图片素材**（签收凭证等） | pillow | `demo_seed.py` 结尾提示「素材不完整」，散件/归位页演示项较少 | `pip install pillow` |
| **生成骨架模板** | python-docx | 模板已随仓库分发，通常不需要再生成 | `pip install python-docx` |
| **桌面窗口** | pywebview | 用浏览器打开，功能一样 | `pip install pywebview` |

> 一次性装齐：`pip install pymupdf docxtpl python-docx pillow`

## 三、需要配置才能用（不配则自动关闭）

| 功能 | 配置项 | 不配的表现 |
|---|---|---|
| AI 助理 / 智能提醒 | `llm.base_url`、`llm.model`、`llm.api_key_file` | 助理页显示「未配置」，其余页面正常 |
| 本地 OCR | `ocr.enabled`、`ocr.python`、`ocr.script` | 「本地识别」按钮不可用 |
| 知识库同步 | `wiki.root` | 功能自动关闭 |
| 提醒推送（webhook） | `notification.webhook_url` | **当前未实现发送**，需自行扩展约 20 行 |
| 法院短信拉文书 | 无需配置，但依赖法院接口稳定 | 接口变更时按报错排查 |

## 四、需要自备数据

| 东西 | 说明 |
|---|---|
| 台账 xlsx | 三个必填列：`当前进度` / `进度日志` / `最新状态`；列号写进 `ledger.cols_case` |
| 案卷目录 | 按 `00-收件箱`…`10-处置分配` 规范建；`demo_seed.py` 会给示例 |
| 文书模板 | 仓库自带**匿名骨架版**（六件套 + 所函），正式出文书前换成所内审定版本 |

## 五、明确不做

- 不做多人协作与权限（单人本地是「数据不外发」的前提）
- 不做云端同步（同上）
- 不接管案卷正文内容（AI 只读目录名与台账字段）

详见 [ROADMAP.md](ROADMAP.md) 与 [INTEGRATIONS.md](INTEGRATIONS.md)。
