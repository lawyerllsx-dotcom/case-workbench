# -*- coding: utf-8 -*-
"""make_shortcuts.py — 重建桌面两个快捷方式（法律工作台 · 静默启动 / 一键关闭）

背景：工作台是**本地 Web 服务**（非独立 exe）。原先桌面图标指向 `.bat`，双击会弹 CMD 黑窗
（2026-09-12 爸爸反馈）。现改为指向 `pythonw.exe` + `.pyw` 静默启动器 → **不弹黑窗**。

用法：
    <default venv python> make_shortcuts.py            # 生成 ps1 并尝试自动执行
    <default venv python> make_shortcuts.py --no-run   # 只生成 <项目目录>\\_mk_shortcuts.ps1

说明：
  - ps1 以 **utf-8-sig** 写出（PowerShell 5.1 无 BOM 会按 GBK 误读中文路径）
  - 沙箱内 WScript.Shell COM 可用（2026-09-12 实测 COM_OK）；若自动执行失败，手动跑：
      powershell -NoProfile -ExecutionPolicy Bypass -File <项目目录>\\_mk_shortcuts.ps1
  - 回读校验结果写 `_mk_shortcuts_report.txt`
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 启动器解释器（2026-09-13 起第一位 = 系统 Python312 pythonw：**有 tkinter**，
# launch_workbench v2 的品牌启动窗需要；venv 精简版没有 tkinter，作降级候选）
PYW_CANDIDATES = [
    r'<pythonw>',
    r'<pythonw>',
]
PYW = next((p for p in PYW_CANDIDATES if os.path.isfile(p)), PYW_CANDIDATES[0])
DESK = r'<用户目录>\Desktop'
ICO = os.path.join(HERE, '法律工作台.ico')
PS1 = os.path.join(HERE, '_mk_shortcuts.ps1')
REPORT = os.path.join(HERE, '_mk_shortcuts_report.txt')

TEMPLATE = '''$ErrorActionPreference = 'Stop'
$report = @()
function Rep($m) { $script:report += $m }
try {
    $ws = New-Object -ComObject WScript.Shell
    $desk = '@@DESK@@'
    $pyw  = '@@PYW@@'
    $ico  = '@@ICO@@'
    $dir  = '@@HERE@@'

    $s1 = $ws.CreateShortcut((Join-Path $desk '法律工作台.lnk'))
    $s1.TargetPath = $pyw
    $s1.Arguments = '"' + (Join-Path $dir 'launch_workbench.pyw') + '"'
    $s1.WorkingDirectory = $dir
    $s1.IconLocation = $ico
    $s1.Description = '法律工作台（本地 Web 操作台 · 静默启动；关掉网页后自动关停）'
    $s1.Save()
    Rep ('OK 启动快捷方式 -> ' + (Join-Path $desk '法律工作台.lnk'))

    # 2026-09-13 下午：关闭功能已嵌入工作台页面（左栏底部「关闭服务」按钮调 /api/quit），
    # 桌面只留一个图标；这里幂等清理可能残留的旧关闭图标
    $old = Join-Path $desk '关闭法律工作台.lnk'
    if (Test-Path $old) { Remove-Item $old -Force; Rep ('REMOVED 关闭图标（功能已入页面）-> ' + $old) }
    else { Rep 'SKIP 关闭图标不存在（已清理）' }

    $c1 = $ws.CreateShortcut((Join-Path $desk '法律工作台.lnk'))
    Rep ('VERIFY1 target=' + $c1.TargetPath + ' args=' + $c1.Arguments + ' icon=' + $c1.IconLocation)
    Rep ('EXISTS1=' + (Test-Path (Join-Path $desk '法律工作台.lnk')))
    $c2 = $ws.CreateShortcut((Join-Path $desk '关闭法律工作台.lnk'))
    Rep ('VERIFY2 EXISTS2(应为False)=' + (Test-Path (Join-Path $desk '关闭法律工作台.lnk')))
} catch {
    Rep ('FAIL: ' + $_.Exception.Message)
}
$report | Out-File -Encoding utf8 '@@REPORT@@'
'''


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass
    no_run = '--no-run' in sys.argv

    for p, what in [(PYW, 'pythonw.exe'), (ICO, '图标')]:
        if not os.path.isfile(p):
            print('缺少 %s：%s' % (what, p))
            return 2

    ps = TEMPLATE
    for k, v in [('@@DESK@@', DESK), ('@@PYW@@', PYW), ('@@ICO@@', ICO),
                 ('@@HERE@@', HERE), ('@@REPORT@@', REPORT)]:
        ps = ps.replace(k, v)
    io.open(PS1, 'w', encoding='utf-8-sig').write(ps)
    print('已生成 %s' % PS1)
    if no_run:
        return 0

    try:
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PS1],
                       capture_output=True, timeout=90)
    except Exception as e:                                   # noqa: BLE001
        print('自动执行未成功（%r）→ 请手动运行：' % e)
        print('  powershell -NoProfile -ExecutionPolicy Bypass -File %s' % PS1)
        return 3
    if os.path.isfile(REPORT):
        print(io.open(REPORT, encoding='utf-8-sig', errors='replace').read().strip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
