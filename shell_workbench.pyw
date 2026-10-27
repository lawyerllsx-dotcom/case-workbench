# -*- coding: utf-8 -*-
"""法律工作台 · pywebview 桌面壳 v2（2026-09-13）：+ 系统托盘 / 缩到托盘 / 服务看门狗

双击桌面「法律工作台」→ 启动器 → 本壳：
  1) 直接开 WebView2 窗口（金天平图标）——与服务启动**并行**（批 47i，不串行等待；
     服务就绪晚于引擎的极端情况由 _ensure_nav 兜底重载）；持久缓存档复用静态资源
  2) **系统托盘**：金天平常驻托盘；左键单击 / 菜单「打开工作台」→ 显示窗口
  3) **关窗 = 缩到托盘**（气泡提示），不退进程；托盘菜单：「退出壳（服务继续常驻）」/「关闭工作台（服务一并关停）」
  4) **服务看门狗**：页面「关闭服务」→ 服务退 → 壳 30 秒内自动跟着退（托盘一并消失）

依赖：venv 内 pywebview + pystray + pillow（均 pip，隔离在 venv）；Win11 自带 WebView2。
`--selftest`：不弹窗，验证依赖 import 与服务探测（供回归）。
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import webview  # noqa: E402  venv 内装（pip install pywebview）
import launch_workbench as L  # noqa: E402  复用探测/拉起逻辑

WIN_TITLE = '法律工作台'
WIN_SIZE = (1400, 920)
MIN_SIZE = (1000, 620)
ICO = os.path.join(HERE, '法律工作台.ico')   # 窗口/任务栏/托盘图标

# 批 47i：WebView2 持久缓存档——静态资源/JS 代码缓存跨冷启复用（省 0.3-0.8s）；
# API/案卷响应服务端一律 no-store → 案件数据绝不落缓存盘，全在本机
PROFILE = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                       'LegalWorkbench', 'WebView2')

MUTEX_NAME = 'LegalWorkbenchShellMutex'      # 单实例锁：重复双击/托盘只允许一个壳
SHOW_EVENT_NAME = 'LegalWorkbenchShellShow'  # 新实例通知旧壳「开窗」的命名事件


def single_instance_notify_or_exit():
    """已有壳实例 → 唤起它的窗口 → 本进程退出；首次启动 → 持锁继续。"""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        # EVENT_MODIFY_STATE=0x0002（SetEvent 必需）；原 0x00100000=SYNCHRONIZE
        # 拿它 SetEvent 会 ACCESS_DENIED 静默失败 → 托盘驻留时双击从不重开（批 42 修复）
        ev = ctypes.windll.kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)
        try:
            if ev:
                ctypes.windll.kernel32.SetEvent(ev)       # 旧壳驻留中 → 通知重开窗口
            else:                                         # 旧壳窗口开着 → 直接前置
                hwnd = ctypes.windll.user32.FindWindowW(None, WIN_TITLE)
                if hwnd:
                    ctypes.windll.user32.ShowWindowAsync(hwnd, 9)   # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
        finally:
            if ev:
                ctypes.windll.kernel32.CloseHandle(ev)
        sys.exit(0)


# ---------------------------------------------------------------- 托盘
def start_tray(port, state):
    """金天平托盘。回调只写 state['cmd'] / 强杀，绝不跨线程操作窗口（三轮实踩教训）。
    state['cmd']: None=无 / 'open'=重开窗口 / 'quit'=退壳。失败不致命。
    批 47f：pystray/PIL 的 import 挪进后台线程——原在主线程 import 阻塞
    webview.start()（开窗慢的组成段之一），托盘晚半秒出现无感。"""
    def _bg():
        try:
            import pystray
            from PIL import Image
            img = Image.open(ICO)

            def on_open(icon, item):
                # 窗口还开着 → 尝试激活；已关（缩托盘驻留中）→ 通知主循环重开
                if webview.windows:
                    try:
                        webview.windows[0].show()
                    except Exception:                    # noqa: BLE001
                        pass
                else:
                    state['cmd'] = 'open'

            def on_quit(icon, item):
                os._exit(0)                              # 只退壳；服务由计划任务常驻

            def on_stop(icon, item):
                # 批 70：关闭工作台＝优雅关服 + 退壳（替代桌面「关闭法律工作台」图标）
                # 批 70b：先主动摘托盘图标——os._exit 强杀后系统托盘会滞留图标（"关得慢"观感主源）；
                #          pystray 的 visible=False → NIM_DELETE 同步直调，即时生效
                try:
                    icon.visible = False
                except Exception:                        # noqa: BLE001
                    pass
                try:
                    p = L.find_running()
                    if p:
                        import urllib.request
                        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        op.open('http://127.0.0.1:%d/api/quit' % p, timeout=3)
                except Exception:                        # noqa: BLE001
                    pass
                os._exit(0)                              # 退壳（quit 已受理；服务未跑则本就只关壳）

            menu = pystray.Menu(
                pystray.MenuItem('打开工作台', on_open, default=True),   # default = 左键单击
                pystray.MenuItem('关闭工作台（服务一并关停）', on_stop),   # 批 70
                pystray.MenuItem('退出壳（服务继续常驻）', on_quit),
            )
            icon = pystray.Icon(WIN_TITLE, img, WIN_TITLE, menu)
            icon.run()
        except Exception:                                # noqa: BLE001
            pass
    threading.Thread(target=_bg, daemon=True).start()
    return state


def start_watchdog(port):
    """服务看门狗：页面「关闭服务」→ 服务退 → 壳 30 秒内自动退（托盘一并消失）。"""
    def _run():
        misses = 0
        while True:
            time.sleep(15)
            try:
                alive = L.find_running() is not None
            except Exception:                        # noqa: BLE001
                alive = False
            misses = 0 if alive else misses + 1
            if misses >= 2:
                os._exit(0)
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------- 主流程
def _ensure_nav(window, url):
    """批 47i 兜底线程：开窗不再串行等服务——WebView2 初始化（1.5-3s）远慢于
    服务就绪（实测 <1s），「开窗即导航成功」是常态；本线程只兜极端情况：
    服务就绪明显晚于引擎就绪（慢冷启/服务崩过）→ 就绪后强制重载一次救场；
    30 秒仍无服务 → 补拉一次。"""
    t0 = time.time()
    while time.time() - t0 < 30:
        p = L.find_running()
        if p:
            if time.time() - t0 > 1.2:               # 就绪明显晚于导航 → 重载
                for _ in range(3):
                    try:
                        window.load_url('http://127.0.0.1:%d' % p)
                        break
                    except Exception:                # noqa: BLE001  引擎未就绪稍后再试
                        time.sleep(0.5)
            return
        time.sleep(0.2)
    try:
        L.ensure_service_started()
    except Exception:                                # noqa: BLE001
        pass


def run():
    single_instance_notify_or_exit()
    import ctypes
    show_event = ctypes.windll.kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    # 批 47i：不再串行 wait_port（旧流程先等服务就绪再开引擎，白等 0.2-0.5s）——
    # 直接开窗，WebView2 初始化与服务启动并行；_ensure_nav 兜底极端情况。
    # 服务几乎总落在 8899（启动器只在端口空闲时才拉起）；异常漂移由 _ensure_nav 纠正。
    url0 = 'http://127.0.0.1:%d' % L.PORTS[0]
    state = {'cmd': None, 'icon': None}
    start_tray(L.PORTS[0], state)
    start_watchdog(L.PORTS[0])
    icon = state.get('icon')
    # 不设 icon 时 WinForms 回退取系统关联图标（实测显示成 Edge 球）→ 必须显式给
    start_kw = dict(icon=ICO) if os.path.isfile(ICO) else {}
    try:
        os.makedirs(PROFILE, exist_ok=True)
        # 持久缓存档：静态资源/JS 代码缓存跨冷启复用（API/案卷 no-store 不落盘）
        start_kw.update(private_mode=False, storage_path=PROFILE)
    except OSError:
        pass                                           # 目录建不出来 → 退回无痕模式

    while True:
        # ✕ 关窗 = 窗口销毁 = webview.start() 返回 → 转托盘驻留（不拦 FormClosing，
        # 三轮实踩证明在关闭流程里 hide/show 都不可靠）
        window = webview.create_window(
            WIN_TITLE, url0,
            width=WIN_SIZE[0], height=WIN_SIZE[1], min_size=MIN_SIZE,
            maximized=True,                              # 打开即最大化（爸爸拍板）
            # 批 57b：启动底色对齐页面 --paper（oklch(0.978 0.008 247) 换算值；
            # 原暖米色属批 57 前旧皮肤，漏改至此）
            background_color='#F4F8FD')
        threading.Thread(target=_ensure_nav, args=(window, url0), daemon=True).start()
        webview.start(**start_kw)
        # ✕ 关窗后静默转托盘驻留（爸爸拍板：不弹气泡）
        # 托盘驻留：等「打开」（托盘菜单 或 新实例 SetEvent）或「退出」
        while state['cmd'] is None:
            r = ctypes.windll.kernel32.WaitForSingleObject(show_event, 200)
            if r == 0:                                   # 新实例请求开窗
                state['cmd'] = 'open'
                break
        if state['cmd'] == 'quit':
            break
        state['cmd'] = None                              # 'open' → 回循环顶重开窗口
    return 0


def selftest():
    """不弹窗回归：依赖 import + 服务探测。"""
    ok = []
    ok.append(('pywebview import', webview.__name__ == 'webview'))
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        ok.append(('pystray + pillow import', True))
    except Exception as e:                           # noqa: BLE001
        ok.append(('pystray + pillow import: %s' % e, False))
    ok.append(('ico 可读', os.path.isfile(ICO)))
    ok.append(('venv pythonw（服务/壳解释器）', os.path.isfile(L.pick_service_py())))
    p = L.find_running()
    ok.append(('服务已就绪 port=%s' % p, p is not None))
    # 批 47j：壳跑在 pythonw 下无控制台，run() 里的 NameError 只能静默死亡
    # （47i 实踩：PORTS 漏 L. 前缀，ast/selftest 都不执行 run() 拦不住）→
    # 静态扫「全大写名字是否都有模块级定义」，专拦这类错。
    import ast as _ast
    _src = open(os.path.join(HERE, 'shell_workbench.pyw'), encoding='utf-8').read()
    _bad = set()
    for _node in _ast.walk(_ast.parse(_src)):
        if isinstance(_node, _ast.Name) and isinstance(_node.ctx, _ast.Load) \
                and _node.id.isupper() and _node.id not in globals():
            _bad.add(_node.id)
    ok.append(('run() 全大写名字静态检查%s' % ('：缺 ' + ','.join(sorted(_bad)) if _bad else ''),
               not _bad))
    for name, good in ok:
        print('[%s] %s' % ('OK' if good else 'FAIL', name))
    return 0 if all(g for _, g in ok) else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    sys.exit(run())
