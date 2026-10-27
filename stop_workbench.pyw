# -*- coding: utf-8 -*-
"""法律工作台 · 一键关闭器（2026-09-12）

双击桌面「关闭法律工作台」图标即运行本文件：
  1) 先向工作台发 `/api/quit`（优雅关停，服务自身退出）
  2) 失败则回退：按端口找 PID（netstat -ano）→ taskkill /F
  3) 本来就没在运行 → 提示一下即可
"""
import ctypes
import os
import re
import subprocess
import sys
import time
import urllib.request

# 批 47b：8799 → 8899（与 launch_workbench.pyw 同步换段）
PORTS = list(range(8899, 8911))


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def quit_one(port, timeout=2.0):
    try:
        with _opener().open('http://127.0.0.1:%d/api/quit' % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:                                        # noqa: BLE001
        return False


def alive(port, timeout=0.8):
    try:
        with _opener().open('http://127.0.0.1:%d/api/health' % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:                                        # noqa: BLE001
        return False


def pids_on(port):
    """netstat -ano 找监听该端口的 PID（GBK 解码，Windows 中文控制台）"""
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True,
                             creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        txt = (out.stdout or b'').decode('gbk', errors='replace')
    except Exception:                                        # noqa: BLE001
        return []
    pids = set()
    for ln in txt.splitlines():
        if ':%d ' % port in ln and 'LISTENING' in ln.upper():
            m = re.search(r'(\d+)\s*$', ln.strip())
            if m:
                pids.add(m.group(1))
    return sorted(pids)


def alert(msg, title='法律工作台'):
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x40)
    except Exception:                                        # noqa: BLE001
        pass


def main():
    live = [p for p in PORTS if alive(p)]
    if not live:
        alert('法律工作台本来就没有在运行。')
        return 0

    for p in live:
        quit_one(p)
    time.sleep(1.2)

    still = [p for p in live if alive(p)]
    killed = []
    for p in still:
        for pid in pids_on(p):
            try:
                subprocess.run(['taskkill', '/F', '/PID', pid],
                               capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                killed.append('%s(pid %s)' % (p, pid))
            except Exception:                                # noqa: BLE001
                pass
    time.sleep(0.8)
    left = [p for p in live if alive(p)]

    if left:
        alert('部分实例未能关闭：端口 %s\n请打开任务管理器结束 python.exe。'
              % ', '.join(str(x) for x in left))
        return 2
    if killed:
        alert('法律工作台已关闭（端口 %s）。' % ', '.join(str(x) for x in live))
    else:
        alert('法律工作台已关闭（端口 %s）。' % ', '.join(str(x) for x in live))
    return 0


if __name__ == '__main__':
    sys.exit(main())
