#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""timeline_export.py — 案件时间轴导出（台账派生 → xlsx，法律工作台）

定位：把台账该案「进度日志」里的时间条目 + 「开庭时间」抽成一条事件线，导出 xlsx 到案卷。
纪律（写死在代码里）：
  - **默认 dry-run**，必须 --apply 才落盘；**只新增文件、绝不覆盖**（重名自动加 -2/-3）
  - 只读台账（不写台账、不动案件材料里已有文件）；输出落在案件夹 `08-过程文件\`
  - **不推断年份**：台账 J 列只写 MM.DD，导出的「时间」列原样照抄，年份留空/照抄原文

⚠️ 关于根目录模板 `D:\案件\案件时间轴自动生成模板.xlsx`：
   该模板含**图表（chart1）+ 2 张图片 + 打印设置**，用 openpyxl 重写会把这些非单元格对象**全部丢掉**
   → 本工具**不重写模板**，而是输出一张干净的时间轴表；要用模板出图，把本表 C12 起整块粘进模板即可
   （模板 C/H/I/J 列是公式，粘进去会自动出图）。

用法：
  <default venv python> timeline_export.py --sheet 示例案件 --row 4                # dry-run
  <default venv python> timeline_export.py --sheet 示例案件 --row 4 --apply
  <default venv python> timeline_export.py --selftest                              # 自测（临时目录）
"""
import argparse
import io
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass

import ledger_ops                                            # noqa: E402
import case_ops                                              # noqa: E402

VERSION = '1.0.0'
TEMPLATE = r'D:\案件\案件时间轴自动生成模板.xlsx'
OUT_SUBDIR = r'08-过程文件'
FALLBACK_DIR = os.environ.get('TIMELINE_OUT_DIR') or os.path.join(HERE, 'data', '导出')
# 台账 J 列既有「MM.DD：」也有「YYYY.MM.DD：/YYYY-MM-DD：」写法 → 有年份就照抄，绝不自己推年份
# 台账里的日期写法不止一种：
#   ① 「09.12：已立案」——MM.DD + 冒号（J 列主流）
#   ② 「2026-06-30裁定准许…」——带年份、无分隔符（I 列出现过）
#   ⚠️ 不带年份且不带冒号的（如「1.5万已付」）**不当日期**，避免把数字误读成日期
ENTRY_RE = re.compile(r'^(?:(\d{4})[.\-/年])?(\d{1,2})[.\-/月](\d{1,2})[日]?\s*[：:]\s*(.+)$', re.S)
ENTRY_YMD_RE = re.compile(r'^(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})[日]?\s*(.+)$', re.S)


def parse_entry(text):
    """一条台账文本 → (时间, 正文)；识别不了日期就返回 ('', 原文)。
       带年份照抄年份；MM.DD 归一为两位；月/日越界视为不是日期。"""
    t = str(text or '').strip()
    m = ENTRY_RE.match(t)
    if m:
        y, mo, d, body = m.groups()
    else:
        m = ENTRY_YMD_RE.match(t)
        if not m:
            return '', t
        y, mo, d, body = m.groups()
    mo, d = int(mo), int(d)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return '', t
    stamp = ('%s.%02d.%02d' % (y, mo, d)) if y else ('%02d.%02d' % (mo, d))
    return stamp, body.strip()
DATE_RE = re.compile(r'(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})')


def find_case_folder(sheet, row_idx, folder=None):
    """案件夹定位（**与页面同一套匹配器**，避免两处口径）：
       逐个案件夹跑 case_ops.match_case_ledger（台账只读一次），命中同一 (sheet,row) 才认。"""
    root = case_ops.case_root()
    if folder:
        p = os.path.join(root, folder)
        if os.path.isdir(p):
            return p
    if not os.path.isdir(root):
        return None
    try:
        rows = case_ops.ledger_case_rows()
    except Exception:                                        # noqa: BLE001
        return None
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        try:
            m = case_ops.match_case_ledger(d, rows=rows)
        except Exception:                                    # noqa: BLE001
            continue
        mt = m.get('matched')
        if mt and mt['sheet'] == sheet and int(mt['row']) == int(row_idx):
            return p
    return None


def build_rows(sheet, row_idx):
    """台账行 → 时间轴条目 [{序号,时间,大事件,备注}]（只读台账，不猜年份）"""
    rows = [r for r in ledger_ops.read_rows(sheet) if r['_row'] == row_idx]
    if not rows:
        raise ValueError('未找到台账数据行 row=%s（%s）' % (row_idx, sheet))
    r = rows[0]
    out = []

    def add(t, ev, note):
        ev = (ev or '').strip()
        if not ev:
            return
        out.append({'时间': (t or '').strip(), '大事件': ev, '备注': note})


    log = str(r.get('进度日志') or '').strip()
    for ln in log.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        t, body = parse_entry(ln)
        if t:
            add(t, body, '台账「进度日志」')
        elif out:
            # 无日期行 = 上一条的续行 → 并进上一条正文，不另起一行
            out[-1]['大事件'] = (out[-1]['大事件'] + ' ' + body).strip()
            _base = out[-1]['备注'].replace('·含续行', '')
            out[-1]['备注'] = _base + '·含续行'
        else:
            add('', body, '台账「进度日志」（原文无日期）')
    cur = str(r.get('当前进度') or '').strip()
    if cur:
        t, body = parse_entry(cur)
        add(t, body, '台账「当前进度」' + ('' if t else '（原文无日期）'))
    kt = str(r.get('开庭时间') or '').strip()
    if kt:
        add(kt, '开庭', '台账「开庭时间」列')
    # 时间条目在前（按原文出现顺序），无日期条目排最后
    dated = [x for x in out if x['时间']]
    undated = [x for x in out if not x['时间']]
    seq = dated + undated
    for i, x in enumerate(seq, start=1):
        x['序号'] = i
    return {'sheet': sheet, 'row': row_idx, '案件': str(r.get('案件') or ''),
            '委托人': str(r.get('委托人') or ''), '案号': str(r.get('案号') or ''),
            '法院': str(r.get('法院/仲裁') or ''), 'rows': seq}


def plan_export(sheet, row_idx, folder=None, out_dir=None, stamp=None):
    """产出导出方案（不落盘）：内容 + 落点 + 是否重名加后缀 + 风险提示"""
    data = build_rows(sheet, row_idx)
    stamp = stamp or datetime.now().strftime('%Y%m%d-%H%M')
    simple = re.sub(r'[\\/:*?"<>|]', '', str(data['案件'] or '未命名'))[:28]
    case_dir = find_case_folder(sheet, row_idx, folder)
    if out_dir:
        dst_dir = out_dir
    elif case_dir:
        dst_dir = os.path.join(case_dir, OUT_SUBDIR)
    else:
        dst_dir = FALLBACK_DIR
    name = '时间轴-%s-%s.xlsx' % (simple, stamp)
    dst = os.path.join(dst_dir, name)
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(dst_dir, name[:-5] + '-%d.xlsx' % n)
    return {'sheet': sheet, 'row': row_idx, '案件': data['案件'], '委托人': data['委托人'],
            '案号': data['案号'], '法院': data['法院'],
            'case_dir': case_dir, 'dst': dst, 'dst_dir': dst_dir,
            'rows': data['rows'], 'count': len(data['rows']),
            'overwritten': False,
            'notes': ['只新增文件、不覆盖；输出为**独立表格**（不重写根目录模板，因模板带图表，重写会丢）',
                      '时间列照抄台账原文（台账只写 MM.DD，**不推断年份**）']}


def write_xlsx(plan):
    """落盘（调用方保证已确认）。返回输出路径"""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, Side, PatternFill
    os.makedirs(plan['dst_dir'], exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '时间轴'
    thin = Side(style='thin', color='BFBFBF')
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws['A1'] = plan['案件'] or '（案件名空）'
    ws['A1'].font = Font(name='微软雅黑', size=14, bold=True)
    ws.merge_cells('A1:D1')
    ws['A2'] = ('案号：%s ｜ 委托人：%s ｜ 法院：%s ｜ 来源：台账「%s」第 %s 行 ｜ 生成：%s'
                % (plan['案号'] or '—', plan['委托人'] or '—', plan['法院'] or '—',
                   plan['sheet'], plan['row'], datetime.now().strftime('%Y-%m-%d %H:%M')))
    ws['A2'].font = Font(name='微软雅黑', size=9, color='666666')
    ws.merge_cells('A2:D2')
    ws['A3'] = '⚠️ 时间列照抄台账原文（台账只写「MM.DD」，不含年份）；正式使用前请人工补年份与未载事项。'
    ws['A3'].font = Font(name='微软雅黑', size=9, color='C0392B')
    ws.merge_cells('A3:D3')
    heads = ['序号', '时间', '大事件', '备注']
    for c, h in enumerate(heads, start=1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = Font(name='微软雅黑', size=10, bold=True)
        cell.fill = PatternFill('solid', fgColor='EEF2F7')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = bd
    for i, it in enumerate(plan['rows']):
        rr = 5 + i
        for c, v in enumerate([it['序号'], it['时间'], it['大事件'], it['备注']], start=1):
            cell = ws.cell(row=rr, column=c, value=v)
            cell.font = Font(name='微软雅黑', size=10)
            cell.alignment = Alignment(vertical='top', wrap_text=(c == 3))
            cell.border = bd
    for col, w in (('A', 7), ('B', 13), ('C', 74), ('D', 20)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A5'
    wb.save(plan['dst'])
    wb.close()
    return plan['dst']


# ---------------------------------------------------------------- 自测 / CLI

def selftest():
    import shutil
    import tempfile
    fails = []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        if not cond:
            fails.append(name)

    def parse_probe():
        return parse_entry('2026-06-30裁定准许撤回对廖永娣的起诉')

    tmp = tempfile.mkdtemp(prefix='timeline_selftest_')
    fake = os.path.join(tmp, 'fake.xlsx')
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '案件'
    for c, h in enumerate(['序号', '案号', '案件', '案件类别', '委托人', '法院/仲裁',
                           '承办法官', '开庭时间'], start=1):
        ws.cell(row=3, column=c, value=h)
    ws.cell(row=4, column=1, value=1)
    ws.cell(row=4, column=2, value='(2026)测字第1号')
    ws.cell(row=4, column=3, value='甲公司vs乙公司-买卖合同纠纷')
    ws.cell(row=4, column=5, value='甲公司')
    ws.cell(row=4, column=8, value='2026.10.20')
    # I 列＝当前进度（带日期）｜J 列＝进度日志（两条带日期 + 一条无日期）
    ws.cell(row=4, column=9, value='09.12：已立案')
    ws.cell(row=4, column=10, value='09.10：递交诉状\n08.01：签合同\n无日期的补记')
    ws.cell(row=4, column=11, value='已立案')
    wb.save(fake)
    wb.close()
    os.environ['LEDGER_PATH'] = fake
    os.environ['LEDGER_BACKUP_ROOT'] = os.path.join(tmp, 'bak')
    out = os.path.join(tmp, 'out')
    try:
        data = build_rows('案件', 4)
        times = [x['时间'] for x in data['rows']]
        notes = [x['备注'] for x in data['rows']]
        chk('条目数 = 日志 2 + 进度 1 + 开庭 1（无日期行被并入）', len(data['rows']) == 4)
        chk('MM.DD 归一为两位（09.10/08.01/09.12 都在）',
            {'09.10', '08.01', '09.12'} <= set(times))
        chk('无日期行并入上一条并标注「含续行」',
            '含续行' in ' '.join(notes) and all(times))
        chk('开庭时间并入（原样 YYYY.MM.DD）', '2026.10.20' in times)
        wsx = None
        d2 = parse_probe()
        chk('带年份且无冒号的写法也认（2026-06-30裁定…）',
            d2[0] == '2026.06.30' and d2[1].startswith('裁定'))
        chk('MM.DD 无冒号不当日期（防「1.5万」误读）', parse_entry('1.5万已付')[0] == '')
        chk('月日越界不当日期', parse_entry('13.45：瞎写')[0] == '')
        chk('MM.DD 带冒号照常识别', parse_entry('09.12：已立案') == ('09.12', '已立案'))
        chk('备注标注来源', any('开庭时间' in x for x in notes)
            and any('当前进度' in x for x in notes) and any('进度日志' in x for x in notes))
        chk('序号连续', [x['序号'] for x in data['rows']] == list(range(1, len(data['rows']) + 1)))
        chk('大事件正文抽对（去掉 MM.DD： 前缀）',
            any(x['大事件'] == '递交诉状' for x in data['rows']))

        pl = plan_export('案件', 4, out_dir=out, stamp='20260101-0000')
        chk('dry-run 不落盘', not os.path.exists(pl['dst']))
        chk('落点在指定目录', pl['dst_dir'] == out)
        chk('文件名带案件简称与时间戳',
            os.path.basename(pl['dst']).startswith('时间轴-') and '20260101-0000' in pl['dst'])
        p1 = write_xlsx(pl)
        chk('已落盘', os.path.isfile(p1))
        wb2 = openpyxl.load_workbook(p1)
        ws2 = wb2['时间轴']
        chk('表头就位', [ws2.cell(row=4, column=c).value for c in range(1, 5)]
            == ['序号', '时间', '大事件', '备注'])
        chk('数据行数一致', ws2.max_row == 4 + len(pl['rows']))
        chk('含「不推断年份」的警示', '不含年份' in str(ws2['A3'].value))
        chk('标题行是案件名', str(ws2['A1'].value).startswith('甲公司vs乙公司'))
        wb2.close()

        p2 = plan_export('案件', 4, out_dir=out, stamp='20260101-0000')
        chk('同名再导出自动改名 -2', p2['dst'].endswith('-2.xlsx'))
        write_xlsx(p2)
        chk('原文件未被覆盖（两个文件都在）', os.path.isfile(p1) and os.path.isfile(p2['dst']))
        chk('台账未被改动', ledger_ops.read_rows('案件')[0]['当前进度'] == '09.12：已立案')
        chk('未在案件夹外乱建目录', not os.path.exists(os.path.join(tmp, 'fake.xlsx.xlsx')))
    finally:
        for k in ('LEDGER_PATH', 'LEDGER_BACKUP_ROOT'):
            os.environ.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)
    print('--- timeline_export selftest %s（失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(fails)))
    return 0 if not fails else 3


def main():
    ap = argparse.ArgumentParser(description='案件时间轴导出 v%s' % VERSION)
    ap.add_argument('--sheet', choices=ledger_ops.SHEETS_CASE)
    ap.add_argument('--row', type=int)
    ap.add_argument('--case', default=None, help='案件夹名（落点用；不传则按台账案件名粗匹配）')
    ap.add_argument('--out', default=None, help='输出目录（默认 案件夹\\%s）' % OUT_SUBDIR)
    ap.add_argument('--stamp', default=None, help='文件名时间戳（测试用）')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.sheet and args.row):
        ap.print_help()
        return 2
    plan = plan_export(args.sheet, args.row, args.case, args.out, args.stamp)
    print('--- 时间轴导出方案（%s 第 %s 行）---' % (plan['sheet'], plan['row']))
    print('案件：%s ｜ 委托人：%s ｜ 案号：%s' % (plan['案件'], plan['委托人'], plan['案号'] or '—'))
    print('条目：%d 条' % plan['count'])
    for x in plan['rows'][:12]:
        print('  %-3s %-12s %s' % (x['序号'], x['时间'] or '（无日期）', x['大事件'][:46]))
    if plan['count'] > 12:
        print('  …（共 %d 条）' % plan['count'])
    print('落点：%s' % plan['dst'])
    for n in plan['notes']:
        print('  · %s' % n)
    if not args.apply:
        print('（dry-run，未落盘；加 --apply 执行）')
        return 0
    p = write_xlsx(plan)
    print('已导出：%s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
