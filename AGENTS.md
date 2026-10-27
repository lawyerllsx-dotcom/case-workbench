# AGENTS.md — 给 AI 编码助手的接手指引

> 这个文件是给 Claude Code / WorkBuddy / Cursor / Codex 之类**自动改代码的 agent** 看的。
> 人类同事请读 [README.md](README.md)；要接自己的业务系统请读 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)。

## 一、30 秒认识这个项目

本地案件工作台：**Python 标准库 `http.server` + 原生 JS 前端**，不引框架、不打包、不走 CDN。

| 事实 | 值 |
|---|---|
| 入口 | `app.py`（113 条路由，`do_GET` / `do_POST` 两个大分派） |
| 前端 | `static/app.js`（全部视图函数）+ `static/index.html`（模板与 CSS 变量） |
| 数据事实源 | **台账 xlsx**（唯一真相），页面只是视图 |
| 自建库 | `data/workbench.db`（SQLite：任务、日程、写作项目、执行款） |
| 配置 | `workbench.config.json`（本机）/ `workbench.config.example.json`（示例） |
| 端口 | 默认 8899，只监听 `127.0.0.1` |
| 启动 | `python app.py`；关页面约 2 分钟后由空闲守望自动退出 |

模块分工：

```
ledger_ops.py    台账读写与受控回写（五重闸都在这里，改它要格外小心）
case_ops.py      立卷 / 收件箱入库 / 归档 / 业务线扫描 / 目录规范
deadline_ops.py  期限与保全窗口（从台账三路抽取）
tasks_ops.py     任务与客户补充档案（SQLite）
assistant_ops.py AI 助理（可选，需 OpenAI 兼容端点）
ocr_ops.py       本地 OCR 通道（可选）
court_sms_ops.py 法院短信拉文书（可选）
static/app.js    所有页面渲染与交互
```

## 二、五条硬红线（违反即回滚）

1. **写操作必须 dry-run 优先**：新增写路径一律「先返回计划 → `confirm/--apply` 才落盘」。不要写「点了就生效」的接口。
2. **绝不删除用户数据**：只 `move` / `makedirs` / 追加 / 替换目标格。删除只允许作用于**本次自己创建的临时物**。
3. **改台账前必须备份**：走 `ledger_ops.backup()`，不要自己 `shutil.copy`。改「当前进度」必须先把旧值追加进「进度日志」（已有实现 `apply_progress_guarded`，别另写一套）。
4. **AI 不碰案件正文**：任何把文件内容送进 LLM 的新功能，先回答三个问题——含正文吗？含个人信息吗？扩大红线吗？任一命中就先停下来问人。
5. **不引依赖、不联网**：前端不许加 CDN / npm 包；后端新增依赖要写进 `requirements.txt` 并标注可选。所有请求不得离开 `127.0.0.1`。

## 三、常见任务怎么下手

| 你要做的事 | 改哪里 | 注意 |
|---|---|---|
| 加一个页面 | `static/app.js` 的 `VIEWS` + `index.html` 导航 + `app.py` 的 `do_GET` 分支 | 视图函数必须能在 Node 里裸跑（`smoke_views.py` 会执行它） |
| 加一个只读接口 | `app.py` 的 `do_GET` + 对应 `*_ops.py` | 返回 `json.dumps(..., ensure_ascii=False)` |
| 加一个写接口 | `app.py` 的 `do_POST` + `*_ops.py` 的 plan/apply 两段式 | 必须校验 `confirm` 字段，且先判路径越界再判存在性 |
| 改台账结构 | `ledger_ops.py` 常量区 + `workbench.config.example.json` | 列号从配置读，别写死；改完跑 `ledger_ops.py selftest` |
| 接新的文书模板 | `templates/` + 对应 `*_ops.py` | 必填为空要 blocked，选填为空保留 `{{占位}}`，不要渲染成空白 |
| 加一个提醒规则 | `deadline_ops.py` 抽取 + `deadline_check.py` 推送 | 相对期限（「15 天内」）不猜，交人工补录 |

## 四、改完必须跑的三件事

```bash
python -m compileall -q .          # 语法
python smoke_views.py --demo       # 前端 19 页渲染（13 PASS + 6 SKIP 为基线）
python ledger_ops.py selftest      # 台账库回归（改了 ledger_ops 才需要）
```

改动涉及接口时，跑对应 `test_api_*.py`；`run_all_tests.py` 可一次跑完（较慢）。

**不要用「我改完了看起来没问题」交差**：这个项目的回归断言 700+ 条，专治「看起来没问题」。

## 五、前端三条特殊纪律（踩过 6 次）

1. **搜索框禁止整页重绘**：用 `patchInner()` 只换表格容器；`render()` 会销毁输入框、打断中文输入法。
2. **搜索框不要加 `isComposing` 护栏**（增量过滤），**聊天框必须加**（显式提交）——写反了中文就打不了字。
3. **「点一下要出结果」的操作，结果必须渲在视线处**（居中浮层 `showModal`），不能渲到表格下方；异步按钮要进 `处理中` 态防重复提交。

## 六、这个仓库里哪些东西是「作者环境专用」

| 模块 | 依赖 | 替代做法 |
|---|---|---|
| `assistant_ops.py` 的 LLM | 作者自建的本地代理（8788） | 配 `llm.base_url` 指向任意 OpenAI 兼容端点（Ollama / vLLM / 云端） |
| `wiki_ops.py` | Obsidian vault 目录结构 | 不配 `wiki.*` 即视为关闭；要接 Notion/语雀请自己写适配层 |
| `ocr_ops.py` | 作者本机 PaddleOCR 环境 | 配 `ocr.python` + `ocr.script` 指向自己的识别脚本（约定见 `docs/INTEGRATIONS.md`） |
| `sohan_ops.py` / `commission_ops.py` | 所内文书模板 | 放自己的模板进 `templates/`，字段名对齐 `templates/委托材料/` 的占位符 |
| `deadline_check.py` toast | Windows toast + AUMID | 无替代则降级为「页面内提醒」；跨平台请走 webhook |
| `launch_workbench.pyw` / `shell_workbench.pyw` | Windows + pywebview | 直接 `python app.py` 即可，不需要壳 |

**给 agent 的额外提示**：如果你要在这套代码上写「AI 自动改文件」的功能，先装同作者的
`write-guard`：写前 `assert`、写后 `stamp`。没有它，重跑生成脚本会覆盖人手改过的文书——
本项目的 `ledger_ops` 五重闸是同一个思路在数据层的实现。

## 七、提交前自查

- [ ] 新增写路径有 dry-run 与 `confirm` 门
- [ ] 路径参数先判越界、再判存在性
- [ ] 没有把案件正文 / 电话 / 微信送进任何外部服务
- [ ] `compileall` + `smoke_views --demo` 通过
- [ ] 日志里不打印台账内容与个人信息
- [ ] 更新了 `README.md` 或本文件里受影响的说明
