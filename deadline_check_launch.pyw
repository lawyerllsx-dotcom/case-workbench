# -*- coding: utf-8 -*-
r"""
deadline_check_launch.pyw — 期限检查的**无窗**启动器（由 pythonw.exe 运行）

用途：计划任务 `LegalWorkbench-DeadlineCheck` 的动作 = `pythonw.exe <本文件>` → **无控制台窗口、不闪窗**。
（沙箱禁止注册 `wscript.exe` 动作，故改用 pythonw 方案；比 .bat 更干净。）

输出全部重定向到 `deadline_check.out.log`（pythonw 下 sys.stdout 为 None，必须先自建）。
"""
import io
import os
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = os.path.join(HERE, 'deadline_check.out.log')

# 关键：先设环境变量，让 deadline_check 的 stdout 幂等守卫跳过 TextIOWrapper 包裹
os.environ['_WB_STDOUT_UTF8'] = '1'


class _Tee(object):
    def __init__(self, fh):
        self.fh = fh

    def write(self, s):
        if s:
            self.fh.write(s)
            self.fh.flush()

    def flush(self):
        self.fh.flush()

    def isatty(self):
        return False


def run():
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write('\n=== %s ===\n' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        sink = _Tee(fh)
        sys.stdout = sink
        sys.stderr = sink
        try:
            import deadline_check
            return deadline_check.main() or 0
        except SystemExit as e:
            return int(e.code or 0)
        except Exception:
            traceback.print_exc()
            return 3


if __name__ == '__main__':
    sys.exit(run())
