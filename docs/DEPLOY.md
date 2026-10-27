# 部署指引（给人看，也给 AI 看）

> 目标：从零到能在浏览器里点开工作台。约 3 分钟。
> 如果你是 AI agent，直接照第二节执行，不要额外发明步骤。

## 一、给人：一句话提示词

把下面这段连同仓库链接发给你常用的 AI 助手：

```
帮我部署这个项目：https://github.com/lawyerllsx-dotcom/case-workbench
按仓库里的 docs/DEPLOY.md 执行，不要改任何代码，不要动我电脑上的其他文件。
装好后把访问地址告诉我，并汇报 verify.py 的结果。
```

## 二、给 agent：执行清单

```bash
git clone https://github.com/lawyerllsx-dotcom/case-workbench.git
cd case-workbench
python -m pip install -r requirements.txt     # 只有 openpyxl
python demo_seed.py                            # 生成虚构示例数据（幂等）
python verify.py                               # 自检，退出码 0 = 可用
python app.py --port 8899                      # 起服务（前台运行）
```

**成功判据（三条全满足才算成）**

1. `verify.py` 退出码 **0**，输出 `可以启动了： python app.py`
2. `curl -s http://127.0.0.1:8899/api/health` 返回 `{"ok": true, ...}`
3. `curl -s http://127.0.0.1:8899/api/overview` 里 `sheets` 有 `案件` 且 `count` 大于 0

**首次运行的正常现象**

- `verify.py` 在 `demo_seed.py` 之前跑，会提示「待生成」——属正常，退出码仍是 0。
- 首页「今日提醒」显示「未配置 LLM」——正常，该功能默认关闭。

## 三、agent 不许做的事

- ❌ 不要修改仓库里的任何代码（部署 ≠ 改造；要改请单独提）
- ❌ 不要动用户已有的台账、案卷目录与其他文件
- ❌ 不要把任何本地文件内容发到外部服务（包括日志、案卷名）
- ❌ 不要 `git push`、不要建新仓库
- ❌ 端口被占时不要 `kill` 陌生进程，改用 `--port` 换一个

## 四、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `python` 不是内部命令 | Windows 装 Python 时没勾 “Add to PATH” | 重装并勾选，或用 `py -3` |
| 8899 被占用 | 已有实例在跑 | `python app.py --port 8900` |
| 页面能开但数字全是 0 | 没跑 `demo_seed.py`，或台账路径不对 | 先 seed；接自己台账见 README「接自己的台账」 |
| `openpyxl` 缺失 | 只装了 Python 没装依赖 | `python -m pip install openpyxl` |
| 中文乱码（Windows 控制台） | 终端代码页 | `chcp 65001`，或在 Windows Terminal 里跑 |
| macOS/Linux 上 `start.sh` 无权限 | 未加执行位 | `chmod +x start.sh` |

## 五、接自己的台账（部署完之后）

1. 复制 `workbench.config.example.json` 为 `workbench.config.json`
2. 改 `paths.ledger` 指向你的台账；改 `ledger.cols_case` 对齐你的列号
3. `python ledger_ops.py check` 体检
4. 三个必填列：`当前进度` / `进度日志` / `最新状态`

详见 README「接自己的台账」。
