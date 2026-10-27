# 贡献指南（很短）

## 先跑起来

```bash
pip install -r requirements.txt
python verify.py            # 自检
python demo_seed.py         # 示例数据
python app.py               # http://127.0.0.1:8899
```

## 提交前必须过

```bash
python -m compileall -q .            # 语法
python smoke_views.py --demo         # 前端 19 页（13 PASS + 6 SKIP 为基线）
python run_all_tests.py              # 20 个接口测试（较慢，约 40 秒）
python sanitize_check.py             # 不许有**新增**疑似个人信息
```

## 代码约定

- **不引框架**：前端原生 JS、后端标准库 `http.server`；新依赖必须可选且写进 `requirements.txt` 注释。
- **写操作两段式**：先 `plan`（dry-run）再 `apply`/`confirm`。
- **不删除，只移动**：用户数据没有任何删除分支。
- **路径先判越界、再判存在性**（顺序反了会泄露「文件是否存在」）。
- **中文输入框**：搜索框不要加 `isComposing` 护栏，聊天框必须加。
- **注释写「为什么」，并且带日期**：这个仓库里最有价值的东西是踩坑记录。

## 改配置结构时

同步改三处：`config.py` 的 `DEFAULTS`、`workbench.config.example.json`、`README` 的「接自己的台账」。

## 报 issue 请带

- `python verify.py` 的输出
- Python 版本、操作系统
- 复现步骤（哪一页、点了什么、期望与实际）
- 如果是数据问题：**不要贴真实案件信息**，用 `demo_seed.py` 造一份等价的假数据
