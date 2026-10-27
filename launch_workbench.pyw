# -*- coding: utf-8 -*-
"""法律工作台 · 启动器 v2（2026-09-13）：品牌启动窗 + 应用窗口模式

双击桌面「法律工作台」（.lnk → pythonw + 本文件）：
  1) ≤0.3 秒弹出品牌启动小窗（Tk 无边框，暖色出版物风格，转圈动画持续可动）——即时反馈
  2) 探测 / 拉起服务 / 开窗 / 等窗全部跑在**后台线程**（批 47h）：Tk 主线程只画动画、
     收进度消息——网络探测、进程拉起再慢也**不会卡住弹窗**（47g 实踩教训：
     慢调用放进 after 回调仍在主线程，弹窗照样「无响应」假死）
  3) 服务已在跑 → 直接复用；否则拉起后**立即开桌面壳**（壳自等端口，启动并行）
  4) 桌面窗口出现 → 启动小窗自动消失
  5) 关掉工作台窗口约 2 分钟后服务自动关停（app.py --idle-exit，默认 120 秒）

说明：
  - 本文件只用标准库，对解释器无要求；但**启动窗需要 tkinter**（系统 Python312 有、
    venv 精简版没有）→ import 失败自动降级为 v1 静默模式（功能不损失，只没启动窗）
  - 探测本地端口必须绕过环境代理（HTTP_PROXY 存在时连 127.0.0.1 也会走代理 → 502）
  - `--selftest`：不弹窗，只验证探测/拉起逻辑（供回归）
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'app.py')
LOG = os.path.join(HERE, 'app.out.log')
# 单实例（批 42，2026-09-18 爸爸拍板「完全禁止误操作双开」）：
#   已有服务 → 零弹窗，直接唤起已有工作台窗口（壳或 Edge），本进程秒退；
#   服务未就绪但已有启动器在拉 → 后来者静默退出（先到者负责到底）。
#   事件名/窗口标题与 shell_workbench.pyw 保持一致（壳的 mutex 负责壳不双开，
#   本文件负责「启动器层」不再弹启动窗/不再重复拉壳）。
SHELL_SHOW_EVENT = 'LegalWorkbenchShellShow'
LAUNCH_MUTEX = 'LegalWorkbenchLauncherMutex'

# 批 47b：8799 → 8899（wb-proxy 临时端口池 8791~8799 撞车，爸爸拍板换段）
PORTS = list(range(8899, 8911))
BASE_URL = 'http://127.0.0.1:%d'
WIN_TITLE = '法律工作台'

# 服务解释器：优先用「当前解释器」（谁启动就用谁），其次同目录 venv，最后常见路径。
# 用 pythonw.exe 可避免 Windows 弹控制台黑窗。
_HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE_PY_CANDIDATES = [
    sys.executable,
    os.path.join(_HERE, '.venv', 'Scripts', 'pythonw.exe'),
    os.path.join(_HERE, '.venv', 'Scripts', 'python.exe'),
    r'<pythonw>',
]
EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

# 品牌色（批 57b：对齐 static/index.html 冷灰蓝令牌——oklch 原值经浏览器引擎换算）
PAPER = '#F4F8FD'; CARD = '#FCFEFF'; INK = '#0E1822'; MUTED = '#5A6673'
FAINT = '#838D98'; RULE = '#D5DEE6'; ACCENT = '#153A5D'


def _opener():
    """绕过环境代理的 opener（否则 127.0.0.1 也会走代理）"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def alive(port, timeout=0.6):
    try:
        with _opener().open(BASE_URL % port + '/api/health', timeout=timeout) as r:
            return r.status == 200
    except Exception:                                        # noqa: BLE001
        return False


def find_running():
    for p in PORTS:
        if alive(p):
            return p
    return None


def pick_service_py():
    for p in SERVICE_PY_CANDIDATES:
        if os.path.isfile(p):
            return p
    return sys.executable


def pick_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def ensure_service_started():
    """已有实例 → 返回其端口；否则静默拉起服务并返回 None（交给轮询）。"""
    port = find_running()
    if port:
        return port
    env = dict(os.environ)
    env['_WB_STDOUT_UTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    logf = open(LOG, 'a', encoding='utf-8', buffering=1)
    logf.write('\n===== %s 启动器拉起 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    subprocess.Popen(
        [pick_service_py(), '-u', APP, '--port', str(PORTS[0])],
        cwd=HERE, env=env, stdout=logf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW),
        close_fds=True)
    return None


def edge_window_up():
    """Edge --app 窗口（标题=页面 title）是否已出现；探测失败按已出现处理。"""
    try:
        return bool(ctypes.windll.user32.FindWindowW(None, WIN_TITLE))
    except Exception:                                        # noqa: BLE001
        return True


def _window_pid(u32, hwnd):
    pid = ctypes.c_ulong(0)
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _pid_image_name(pid):
    """窗口进程的映像名（小写）；读不到返回 ''（调用方保守拒绝）。"""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit('\\', 1)[-1].lower()
        return ''
    finally:
        k32.CloseHandle(h)


def _window_is_workbench(title, image_name):
    """工作台窗口判据（批 79，2026-10-17 修复「标题误认」）：
    ① 标题**精确等于** WIN_TITLE——浏览器普通标签标题为「… - Microsoft Edge」必带
       后缀（且含隐藏零宽字符），由此天然排除；壳窗口与 Edge --app 窗口标题均为精确值。
    ② 进程 ∈ {pythonw.exe（pywebview 壳）, msedge.exe（--app 回退）}。
    原实现按「标题含 WIN_TITLE」模糊匹配，浏览器标签会被误认 → 双击快捷方式被浏览器
    截胡、壳不被唤起（2026-10-16 发现；10-17 实测 Edge 标签「法律工作台 - 用户配置 1
    - Microsoft Edge」旧判据误接受、新判据拒绝）。"""
    return title == WIN_TITLE and image_name in ('pythonw.exe', 'msedge.exe')


def find_workbench_hwnd():
    """枚举顶层可见窗口，找工作台窗口本体（判据见 _window_is_workbench）。返回句柄或 None。"""
    import ctypes
    u32 = ctypes.windll.user32
    hits = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        try:
            if u32.IsWindowVisible(hwnd):
                n = u32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                    if _window_is_workbench(buf.value, _pid_image_name(_window_pid(u32, hwnd))):
                        hits.append(hwnd)
        except Exception:                                    # noqa: BLE001
            pass
        return True

    try:
        u32.EnumWindows(proc(_cb), 0)
    except Exception:                                        # noqa: BLE001
        return None
    return hits[0] if hits else None


def notify_existing_window():
    """已有壳实例 → 唤起其窗口并置前；托盘驻留（窗口已销毁）→ 发事件让旧壳重开窗。
    返回 True=已通知/已前置；False=没找到壳也没事件（壳从未开过）。"""
    import ctypes
    hwnd = find_workbench_hwnd()
    if hwnd:
        u32 = ctypes.windll.user32
        if u32.IsIconic(hwnd):                               # 仅最小化才还原
            u32.ShowWindowAsync(hwnd, 9)                     # SW_RESTORE
        # 最大化/normal 窗口不动显示状态（SW_RESTORE 会把最大化还原成小窗），只置前
        u32.SetForegroundWindow(hwnd)
        return True
    k32 = ctypes.windll.kernel32
    # EVENT_MODIFY_STATE=0x0002（SetEvent 必需）；0x00100000 是 SYNCHRONIZE，
    # 拿它 SetEvent 会 ACCESS_DENIED 静默失败——壳 notify 同款历史 bug 一并修
    ev = k32.OpenEventW(0x0002, False, SHELL_SHOW_EVENT)
    if ev:
        try:
            k32.SetEvent(ev)
        finally:
            k32.CloseHandle(ev)
        return True
    return False


def single_launcher_or_exit():
    """启动器互斥：已有启动器正在拉服务（还没就绪）→ 本进程静默退出。
    返回 True=本进程可继续；False=已有启动器在跑。"""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, LAUNCH_MUTEX)
    return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS


def open_workbench_window(port):
    """开工作台窗口：优先 pywebview 桌面壳（真窗口，2026-09-13 爸爸拍板）；
    壳/解释器缺失时回退 Edge --app，再回退默认浏览器。"""
    url = BASE_URL % port
    shell = os.path.join(HERE, 'shell_workbench.pyw')
    service_py = pick_service_py()
    try:
        if os.path.isfile(shell) and os.path.basename(service_py) == 'pythonw.exe':
            subprocess.Popen([service_py, shell], cwd=HERE, close_fds=True)
            return
    except Exception:                                    # noqa: BLE001
        pass
    edge = pick_edge()
    if edge:
        subprocess.Popen([edge, '--app=' + url], close_fds=True)
    else:
        import webbrowser
        webbrowser.open(url)


# ---------------------------------------------------------------- 启动小窗
class Splash:
    """Tk 无边框品牌启动窗。tkinter 不可用时整类跳过（降级静默模式）。"""

    def __init__(self):
        import tkinter as tk
        from tkinter import font as tkfont
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:                                    # noqa: BLE001
            pass
        self._tk = tk
        self.root = tk.Tk()
        self.root.title(WIN_TITLE + ' · 启动中')
        self.root.overrideredirect(True)                     # 无边框
        self.root.attributes('-topmost', True)
        W, H = 384, 172
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry('%dx%d+%d+%d' % (W, H, (sw - W) // 2, (sh - H) // 2 - 60))
        self.root.configure(bg=ACCENT)
        # 顶部 3px 墨蓝条 + 冷灰 1px 外框 + 白卡（品牌层叠；批 57b 冷灰蓝）
        tk.Frame(self.root, bg=ACCENT, height=3).pack(fill='x', side='top')
        outer = tk.Frame(self.root, bg=RULE, padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        card = tk.Frame(outer, bg=CARD, padx=28, pady=20)
        card.pack(fill='both', expand=True)
        f_title = tkfont.Font(family='宋体', size=17, weight='bold')
        f_sub = tkfont.Font(family='微软雅黑', size=9)
        f_status = tkfont.Font(family='微软雅黑', size=10)
        tk.Label(card, text='法律工作台', font=f_title, fg=INK, bg=CARD).pack(anchor='w')
        tk.Label(card, text='本机运行 · 案件材料不出本机',
                 font=f_sub, fg=FAINT, bg=CARD).pack(anchor='w', pady=(3, 16))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill='x')
        self.canvas = tk.Canvas(row, width=24, height=24, bg=CARD, highlightthickness=0)
        self.canvas.pack(side='left')
        self.status = tk.Label(row, text='正在启动…', font=f_status,
                               fg=MUTED, bg=CARD)   # 不设 wraplength：永不折行，窗口随内容自适应
        self.status.pack(side='left', padx=(12, 0))          # pack 默认垂直居中，与转圈对齐
        self._angle = 0
        self._spin()
        self.t0 = time.time()
        # 自适应尺寸：DPI 缩放下字体变大，写死高度会被裁切（爸爸实机已踩）
        # 宽度按「最长状态文案」预留——否则文案变长时被挤压盖住转圈（第二轮实踩）
        self.status.config(text='服务就绪，正在打开界面…')
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), 360)
        h = self.root.winfo_reqheight() + 4
        self.root.geometry('%dx%d+%d+%d' % (w, h, (sw - w) // 2, (sh - h) // 2 - 60))
        self.status.config(text='正在启动…')

    def _spin(self):
        self._angle = (self._angle + 15) % 360
        c = self.canvas
        c.delete('all')
        c.create_arc(3, 3, 21, 21, start=0, extent=359, style='arc', outline=RULE, width=3)
        c.create_arc(3, 3, 21, 21, start=self._angle, extent=110, style='arc',
                     outline=ACCENT, width=3)
        self.root.after(50, self._spin)

    def say(self, msg):
        try:
            self.status.config(text=msg)
        except Exception:                                    # noqa: BLE001
            pass

    def close(self):
        try:
            self.root.destroy()
        except Exception:                                    # noqa: BLE001
            pass

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def loop(self):
        self.root.mainloop()


# ---------------------------------------------------------------- 主流程
def _open_window(port):
    """开工作台窗口，失败回退默认浏览器（不抛出）。"""
    try:
        open_workbench_window(port)
    except Exception:                                        # noqa: BLE001
        import webbrowser
        webbrowser.open(BASE_URL % port)


def _wait_service_ready(timeout_s):
    """轮询直到服务就绪，返回端口；超时返回 None。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        p = find_running()
        if p:
            return p
        time.sleep(0.25)
    return None


def _wait_window(timeout_s):
    """等工作台桌面窗口出现（壳 / Edge --app）；超时返回 False（弹窗也照关）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if edge_window_up():
            return True
        time.sleep(0.15)
    return False


def launch_flow(say, done):
    """完整启动流程（阻塞版）。

    ⚠️ 批 47h 铁律：本函数**绝不能跑在 Tk 主线程**——端口探测（最多 12×0.6s）、
    进程拉起（Defender 实扫可拖到秒级）、Edge 冷启都是慢调用，主线程一碰就
    「弹窗无响应」假死（47g 实踩：挪进 after 回调 ≠ 后台，仍在主线程）。
    有弹窗时跑在 worker 线程，静默模式跑在主线程。
    say(text)=进度汇报；done()=流程结束（弹窗关闭信号）。done() 保证必达。
    """
    try:
        port = find_running()
        if port:
            say('服务就绪，正在打开界面…')
            _open_window(port)
        else:
            say('正在启动本地服务…')
            ensure_service_started()                         # 探测 + 拉起（阻塞）
            shell_ok = os.path.isfile(os.path.join(HERE, 'shell_workbench.pyw')) \
                and os.path.basename(pick_service_py()) == 'pythonw.exe'
            if shell_ok:
                # 批 47f 并行：壳自等端口就绪 → 立即开壳，服务就绪与壳启动并行
                say('正在打开桌面窗口…')
                _open_window(PORTS[0])
            else:
                port = _wait_service_ready(60)
                if port is None:
                    raise TimeoutError('服务 60 秒未就绪，请看 case_workbench\\app.out.log')
                say('服务就绪，正在打开界面…')
                _open_window(port)
        _wait_window(10)                                     # 弹窗跟到桌面窗口出现为止
    except Exception as e:                                   # noqa: BLE001
        say('启动失败：%s（详见 app.out.log）' % e)
        time.sleep(8)                                        # 留出看清错误的时间（worker 线程内，不卡界面）
    finally:
        done()


def run(with_ui=True):
    # ---- 批 42：已有实例 → 零弹窗秒退（禁止误操作双开） ----
    try:
        port0 = find_running()
    except Exception:                                        # noqa: BLE001
        port0 = None
    if port0:
        # 服务已在跑：唤起已有工作台窗口（壳置前 / 托盘驻留重开）；
        # 壳从未开过（如仅开机常驻）才开一个壳——这不算双开，是本来的打开。
        if not notify_existing_window():
            open_workbench_window(port0)
        return 0
    if not single_launcher_or_exit():
        # 服务还没就绪但已有启动器在拉 → 后来者静默退出，先到者负责开窗
        return 0

    # 双击 → 立刻出启动窗（先 UI 后逻辑，保证反馈最优先）
    ui = None
    if with_ui:
        try:
            ui = Splash()
        except Exception:                                    # noqa: BLE001  无 tkinter 等环境
            ui = None

    if ui is None:                                           # 降级静默模式：同一流程同步跑
        launch_flow(lambda _t: None, lambda: None)
        return 0

    # 批 47h：全部阻塞工作进 worker 线程；Tk 主线程只做「转圈动画 + 收消息 + 关窗」。
    # （47g 曾把探测/拉服务挪进 poll 的 after 回调——仍是主线程，慢调用一卡、
    #   mainloop 停摆，弹窗直接「无响应」——爸爸实测「弹窗都是卡住的」。）
    msgq = queue.Queue()

    def say(text):
        msgq.put(('say', text))

    def done():
        msgq.put(('done', None))

    threading.Thread(target=launch_flow, args=(say, done), daemon=True).start()

    state = {'closed': False}

    def tick():
        if state['closed']:
            return
        try:
            while True:
                kind, text = msgq.get_nowait()
                if kind == 'say':
                    ui.say(text)
                else:
                    state['closed'] = True
                    ui.close()
                    return
        except queue.Empty:
            pass
        ui.after(100, tick)

    ui.after(100, tick)
    ui.loop()
    return 0


def selftest():
    """不弹窗回归：探测/拉起/工具函数。"""
    ok = []
    ok.append(('pick_service_py 存在', os.path.isfile(pick_service_py())))
    ok.append(('pick_edge 存在', pick_edge() is not None))
    ok.append(('find_workbench_hwnd 判据（壳/--app 接受，浏览器标签/启动窗拒绝）',
               _window_is_workbench(WIN_TITLE, 'pythonw.exe')
               and _window_is_workbench(WIN_TITLE, 'msedge.exe')
               and not _window_is_workbench(WIN_TITLE + ' - Microsoft Edge', 'msedge.exe')
               and not _window_is_workbench(WIN_TITLE + ' · 启动中', 'pythonw.exe')))
    p = ensure_service_started()
    ok.append(('ensure_service_started 已有端口或已拉起', p is None or alive(p)))
    for name, good in ok:
        print('[%s] %s' % ('OK' if good else 'FAIL', name))
    t0 = time.time()
    while time.time() - t0 < 30:
        p = find_running()
        if p:
            print('[OK] 服务就绪 on port %d (%.1fs)' % (p, time.time() - t0))
            break
        time.sleep(0.25)
    else:
        print('[FAIL] 30s 未就绪，请看 app.out.log')
        return 1
    p2 = _wait_service_ready(5)
    ok2 = p2 is not None and alive(p2)
    print('[%s] _wait_service_ready 就绪返回端口 %s' % ('OK' if ok2 else 'FAIL', p2))
    print('[INFO] edge_window_up() =', edge_window_up())
    return 0


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    sys.exit(run(with_ui='--no-ui' not in sys.argv))
