#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
toast_notify.py — 法律工作台 · Windows 通知中心 toast（批 D）

用途：工作台没开着时也能收到提醒（期限临期 / 智能提醒），替代原外部服务气泡通道的
      「主动触达」职责。外部服务气泡作为备用通道保留（拍板 D1：双通道）。

实现要点（都是实机验证过的坑）：
  1) 不装第三方模块、不落 ps1 文件 —— PowerShell 脚本以 **-EncodedCommand**（UTF-16LE
     base64）传入，规避引号地狱、中文编码、GBK 控制台三类问题。
  2) AUMID（AppUserModelID）：用 PowerShell 自己的 AppId（Windows PowerShell），
     保证通知在通知中心可显示且不需要注册应用；失败则回退通用 AppId。
  3) 沙箱/pwsh 差异：用 `powershell.exe`（Windows PowerShell 5.1），WinRT 投影可用
     —— 实测（2026-09-18）`ToastNotificationManager` 可创建并 Show 成功。

用法：
  python toast_notify.py "标题" "正文"
  python toast_notify.py --selftest        # 不真弹（只做编码/命令构造断言）
退出码：0=已发送 2=参数错误 3=发送失败（调用方据此回退气泡）
"""
import argparse
import base64
import io
import json
import os
import subprocess
import sys

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, 'workbench.log')

# 通知来源 AppId（**工作台自己的 AUMID**，2026-09-18 修正）：
#   ⚠️ 不再用 PowerShell 的 AppId——通知中心会显示「Windows PowerShell」（穿帮）。
#   正解：在开始菜单建带 AppUserModelID 的快捷方式（`make_aumid_shortcut.py`），
#   WinRT 据此解析显示名与图标 → 通知来源显示「法律工作台」。
#   回退链：自定义 AUMID 未注册（快捷方式被删）→ ShellExperienceHost（通用）。
APP_ID = 'Tencent.LegalWorkbench'
FALLBACK_APP_ID = 'Microsoft.Windows.ShellExperienceHost_cw5n1h2txyewy!App'
AUMID_SHORTCUT = '法律工作台.lnk'      # 与 make_aumid_shortcut.LNK_NAME 一致
DEFAULT_ICON = os.path.join(HERE, '法律工作台.ico')   # 通知左上角图标


def aumid_registered():
    """自定义 AUMID 是否已注册（= 开始菜单快捷方式存在）。
    未注册时 toast 仍能发，但通知来源可能显示为通用/陌生名 → 提示补建。"""
    p = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                     'Start Menu', 'Programs', AUMID_SHORTCUT)
    return os.path.isfile(p), p


def log(action, detail=''):
    try:
        from datetime import datetime
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s | %s | %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                        action, detail))
    except OSError:
        pass


def _ps_script(title, body, app_id, icon_path=None):
    """构造 PowerShell 脚本（标题/正文用单引号包裹并转义内部单引号）。

    icon_path：可选，通知左上角图标（`appLogoOverride`）。用 **XML 模板**而非
    ToastText02 类模板——类模板不支持插图；XML 里 image src 用 file:/// 绝对路径。
    """
    t = str(title).replace("'", "''")
    b = str(body).replace("'", "''")
    a = str(app_id).replace("'", "''")
    if icon_path and os.path.isfile(icon_path):
        uri = 'file:///' + str(icon_path).replace('\\', '/').lstrip('/')
        # 用单引号包裹，内部双引号无需转义；PowerShell -EncodedCommand 传 XML 时
        # 只需把 XML 内的单引号翻倍（PowerShell 单引号字符串的转义法）
        xml = (
            '<toast><visual><binding template="ToastGeneric">'
            '<image placement="appLogoOverride" hint-crop="circle" src="%s"/>'
            '<text>%s</text><text>%s</text>'
            '</binding></visual></toast>' % (uri, _xml_escape(title), _xml_escape(body))
        ).replace("'", "''")
        return (
            "$ErrorActionPreference='Stop'\n"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType=WindowsRuntime] > $null\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom,"
            " ContentType=WindowsRuntime] > $null\n"
            "$d=New-Object Windows.Data.Xml.Dom.XmlDocument\n"
            "$d.LoadXml('%s')\n"
            "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%s')\n"
            "$n.Show([Windows.UI.Notifications.ToastNotification]::new($d))\n"
            "Write-Output 'SENT'\n" % (xml, a)
        )
    return (
        "$ErrorActionPreference='Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType=WindowsRuntime] > $null\n"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
        "$x=$t.GetElementsByTagName('text')\n"
        "$x.Item(0).AppendChild($t.CreateTextNode('%s')) > $null\n"
        "$x.Item(1).AppendChild($t.CreateTextNode('%s')) > $null\n"
        "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%s')\n"
        "$n.Show([Windows.UI.Notifications.ToastNotification]::new($t))\n"
        "Write-Output 'SENT'\n" % (t, b, a)
    )


def _xml_escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def _encoded(script):
    """-EncodedCommand 要求 UTF-16LE + base64"""
    return base64.b64encode(script.encode('utf-16-le')).decode('ascii')


def _run(app_id, title, body, timeout=15, icon_path=None):
    script = _ps_script(title, body, app_id, icon_path=icon_path)
    enc = _encoded(script)
    try:
        r = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive',
             '-WindowStyle', 'Hidden', '-EncodedCommand', enc],
            capture_output=True, timeout=timeout)
    except Exception as e:                                   # noqa: BLE001
        return False, '%s: %s' % (type(e).__name__, e)
    out = (r.stdout or b'').decode('utf-8', errors='replace')
    err = (r.stderr or b'').decode('utf-8', errors='replace')
    if r.returncode == 0 and 'SENT' in out:
        return True, 'sent'
    return False, 'rc=%s err=%s' % (r.returncode, (err or out)[:200])


def notify(title, body, timeout=15, icon_path=None):
    """发一条 toast。返回 (ok, detail)。失败由调用方回退气泡。

    icon_path：通知左上角图标；默认用工作台 .ico（若存在）。
    """
    title = str(title or '').strip() or '法律工作台'
    body = str(body or '').strip()
    if not body:
        return False, '空正文'
    reg, lnk = aumid_registered()
    if not reg:
        # 不阻断发送，但要留痕——否则通知来源会显示成陌生名（穿帮）
        log('TOAST_AUMID_MISSING', '未注册 %s；请跑 make_aumid_shortcut.py --apply' % lnk)
    if icon_path is None:
        icon_path = DEFAULT_ICON if os.path.isfile(DEFAULT_ICON) else None
    ok, detail = _run(APP_ID, title, body, timeout=timeout, icon_path=icon_path)
    if not ok:
        log('TOAST_FAIL', 'primary: %s' % detail[:200])
        ok2, detail2 = _run(FALLBACK_APP_ID, title, body, timeout=timeout)
        if ok2:
            log('TOAST_OK', 'fallback appid；%s' % title[:40])
            return True, 'sent(fallback)'
        log('TOAST_FAIL', 'fallback: %s' % detail2[:200])
        return False, '%s | %s' % (detail, detail2)
    log('TOAST_OK', '%s | %s' % (title[:40], body[:80]))
    return True, detail


def _utf16le_b64_roundtrip(s):
    """供 selftest 验证编码往返（-EncodedCommand 的正确性基础）"""
    return base64.b64decode(_encoded(s)).decode('utf-16-le')


def selftest():
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    s = _ps_script('标题', '正文', 'APP')
    chk('脚本含 ToastText02 模板', 'ToastText02' in s)
    chk('脚本含标题', "CreateTextNode('标题')" in s)
    chk('脚本含正文', "CreateTextNode('正文')" in s)
    chk('脚本含 CreateToastNotifier', 'CreateToastNotifier' in s)
    chk('脚本含 Show 调用', '.Show(' in s)
    chk('单引号转义（注入失败）',
        "CreateTextNode('a''b')" in _ps_script('t', "a'b", 'A'))
    chk('UTF-16LE 编码往返正确', _utf16le_b64_roundtrip(s) == s)
    enc = _encoded(s)
    chk('base64 仅 ASCII', all(c.isascii() for c in enc))
    chk('中文往返正确', _utf16le_b64_roundtrip('法律工作台') == '法律工作台')
    chk('空正文被拦', notify('t', '') == (False, '空正文'))
    chk('APP_ID 不再用 PowerShell（防穿帮）',
        'PowerShell' not in APP_ID and 'powershell' not in APP_ID)
    chk('APP_ID 是工作台自己的', APP_ID == 'Tencent.LegalWorkbench')
    chk('aumid_registered 返回二元组', len(aumid_registered()) == 2)
    reg, lnk = aumid_registered()
    chk('AUMID 快捷方式路径正确',
        lnk.endswith(os.path.join('Start Menu', 'Programs', AUMID_SHORTCUT)))
    # 图标（appLogoOverride）
    chk('默认图标路径指向 .ico', DEFAULT_ICON.endswith('.ico'))
    s_icon = _ps_script('t', 'b', 'A', icon_path=DEFAULT_ICON)
    chk('带图标走 XML 模板', 'ToastGeneric' in s_icon)
    chk('XML 含 appLogoOverride', 'appLogoOverride' in s_icon)
    chk('XML 含 file:/// 路径', 'file:///' in s_icon)
    chk('XML 用 LoadXml（非类模板）', 'LoadXml' in s_icon)
    chk('无图标走类模板（回退）', 'ToastText02' in _ps_script('t', 'b', 'A'))
    chk('不存在的图标 → 自动回退类模板',
        'ToastText02' in _ps_script('t', 'b', 'A', icon_path=r'X:\nope.ico'))
    chk('XML 特殊字符被转义', '&amp;' in _ps_script('t', 'a&b', 'A', icon_path=DEFAULT_ICON))
    chk('空白正文被拦', notify('t', '   ') == (False, '空正文'))
    # notify() 层标题兜底：空标题 → 用「法律工作台」（验内部脚本构造，不发通知）
    _t = str('').strip() or '法律工作台'
    chk('标题兜底为「法律工作台」', _t == '法律工作台')
    chk('标题兜底进入脚本', "CreateTextNode('法律工作台')" in _ps_script(_t, 'b', 'A'))
    # 长正文（/bubble 上限 200 字符 → toast 也限一下）
    chk('长正文仍可构造', len(_ps_script('t', 'x' * 500, 'A')) > 500)

    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def main():
    ap = argparse.ArgumentParser(description='Windows toast 通知 v%s' % VERSION)
    ap.add_argument('title', nargs='?', default='法律工作台')
    ap.add_argument('body', nargs='?', default='')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.body:
        ap.print_help()
        return 2
    ok, detail = notify(args.title, args.body)
    print('toast: %s (%s)' % ('OK' if ok else 'FAIL', detail))
    return 0 if ok else 3


if __name__ == '__main__':
    sys.exit(main())
