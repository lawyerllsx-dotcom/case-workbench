#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_seed.py — 生成一份可立刻运行的演示数据（全部虚构）

产出：
  demo/案件管理台账.xlsx      5 个 sheet 的合成台账（含 3 条虚构案件）
  demo/案件/在办/...          2 个虚构案卷夹（按目录规范建好子夹 + 材料怎么放）
  demo/案件/已结案/...        1 个
  demo/案件/律师函|合同|差旅发票   业务线夹
  data/workbench.db           由首次启动自动创建（不在此生成）

幂等：已存在的文件/目录一律跳过，除非 --force（会先备份到 demo/.seed-backup-时间戳/）。

用法：
    python demo_seed.py
    python demo_seed.py --force
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

import config

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit('缺少 openpyxl：pip install openpyxl')

HERE = os.path.dirname(os.path.abspath(__file__))

# 目录规范（与 case_ops.TREE_BASE 一致；示例数据不写死业务语义）
TREE_BASE = ['00-收件箱', '需盖章文件', '01-委托手续', '02-法律文书', '03-证据材料',
             '04-法院文件', '05-费用票据', '06-当事人信息', '07-律师函', '08-过程文件']
TREE_EXEC_EXTRA = ['09-财产查控', '10-处置分配']

CASES = [
    {
        'sheet': '案件', '序号': 1, '案号': '（2026）闽0203民初9001号',
        '案件': '民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖',
        '案件类别': '诉讼', '委托人': '甲公司', '法院/仲裁': '某市某区人民法院',
        '承办法官': '王法官', '开庭时间': '2026-11-12 09:30', '当前进度': '已提交证据，等待开庭',
        '进度日志': '10.20：完成证据交换\n10.28：补充提交送货单 3 份',
        '最新状态': '待开庭', '合同编号': 'HT-2026-001', '委托阶段': '一审',
        '标的额': 286000, '基础律师费': 20000, '风险费约定': '回款 5%',
        '已收到': 10000, '付款日期': '2026-10-08', '开票金额': 10000,
        '发票号': '00000001', '剩余尾款': 10000, '保全费（法院）': 2000,
        '保费（保险公司）': 800, '保全情况及期限': '已保全，2027-04-30 到期',
        '承办律师': '张律师', '备注': '示例数据',
    },
    {
        'sheet': '案件', '序号': 2, '案号': '（2026）闽0205民初9002号',
        '案件': '民-A-26002_丙某诉丁某民间借贷纠纷',
        '案件类别': '诉讼', '委托人': '丙某', '法院/仲裁': '某市某区人民法院',
        '承办法官': '李法官', '开庭时间': '2026-11-20 14:30', '当前进度': '已立案，等待排期',
        '进度日志': '10.25：线上立案通过', '最新状态': '已立案',
        '合同编号': 'HT-2026-002', '委托阶段': '一审',
        '标的额': 150000, '基础律师费': 12000, '风险费约定': '',
        '已收到': 12000, '付款日期': '2026-10-25', '开票金额': 12000,
        '发票号': '00000002', '剩余尾款': 0, '保全费（法院）': 0,
        '保费（保险公司）': 0, '保全情况及期限': '', '承办律师': '张律师', '备注': '示例数据',
    },
    {
        'sheet': '其他案件', '序号': 3, '案号': '（2026）闽0203执9003号',
        '案件': '执-26003_戊公司执行案',
        '案件类别': '执行', '委托人': '戊公司', '法院/仲裁': '某市某区人民法院',
        '承办法官': '赵法官', '开庭时间': '', '当前进度': '已查控到银行账户，等待扣划',
        '进度日志': '10.15：执行立案\n10.22：收到查控反馈', '最新状态': '执行中',
        '合同编号': 'HT-2026-003', '委托阶段': '执行',
        '标的额': 420000, '基础律师费': 15000, '风险费约定': '回款 8%',
        '已收到': 0, '付款日期': '', '开票金额': 0,
        '发票号': '', '剩余尾款': 15000, '保全费（法院）': 0,
        '保费（保险公司）': 0, '保全情况及期限': '', '承办律师': '张律师', '备注': '示例数据',
    },
]

GUIDE = """# 这个案子的材料怎么放（示例，可随时改）

## 按阶段
| 子夹 | 放什么 |
|---|---|
| 00-收件箱 | 刚收到、还没归位的材料（先扔这里，再归类） |
| 01-委托手续 | 委托代理合同、授权委托书、风险告知、送达地址确认书 |
| 02-法律文书 | 起诉状、答辩状、代理词、申请书（自己出的文书） |
| 03-证据材料 | 证据清单与证据原件/复印件 |
| 04-法院文件 | 受理通知、传票、判决书、裁定书、送达回证 |
| 05-费用票据 | 诉讼费票据、保全费票据、差旅发票 |
| 06-当事人信息 | 身份证明、主体资格材料 |
| 07-律师函 | 本案发出的律师函及签收凭证 |
| 08-过程文件 | 研究笔记、会议记录、OCR 文本、内部草稿 |

## 三条规矩
1. 只移动不删除：拿不准就先进 00-收件箱。
2. 同一份材料只放一处，需要多份就复制。
3. 文件名带日期或版本号，别用「最终版」「最终版2」。
"""


def _write_ledger(path):
    wb = Workbook()
    wb.remove(wb.active)
    cols_case = config.get('ledger.cols_case') or {}
    cols_letter = config.get('ledger.cols_letter') or {}
    cols_contract = config.get('ledger.cols_contract') or {}
    cols_payment = config.get('ledger.cols_payment') or {}

    def build(title, cols, rows, groups=None):
        ws = wb.create_sheet(title)
        names = [k for k, _ in sorted(cols.items(), key=lambda kv: kv[1])]
        ws.cell(row=1, column=1, value=title)
        ws.cell(row=1, column=1).font = Font(bold=True, size=14)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(names))
        ws.cell(row=1, column=1).alignment = Alignment(horizontal='center')
        for i, n in enumerate(names, start=1):
            c = ws.cell(row=3, column=i, value=n)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='4B5563')
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(24, len(n) * 2 + 6))
        ws.freeze_panes = 'A4'
        for j, row in enumerate(rows or [], start=config.get('ledger.data_start_row', 4)):
            for k, v in (row or {}).items():
                if k in cols:
                    ws.cell(row=j, column=cols[k], value=v)
        if groups:
            for i, g in enumerate(groups[:len(names)], start=1):
                ws.cell(row=2, column=i, value=g).font = Font(bold=True, size=9, color='6B7280')
        return ws

    build('案件', cols_case, [c for c in CASES if c['sheet'] == '案件'],
          ['基本信息', '', '', '', '', '', '', '时间', '', '', '状态', '', '', '费用', '', '', '', '', '', '', '', '保全', '', '', ''])
    build('其他案件', cols_case, [c for c in CASES if c['sheet'] == '其他案件'])
    build('律师函', cols_letter, [])
    build('合同审查', cols_contract, [])
    build('执行款', cols_payment, [])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def _make_case(root, name, exec_case=False, done=False):
    base = os.path.join(root, name)
    for d in TREE_BASE + (TREE_EXEC_EXTRA if exec_case else []):
        os.makedirs(os.path.join(base, d), exist_ok=True)
    os.makedirs(os.path.join(base, 'pipeline'), exist_ok=True)
    guide = os.path.join(base, '_材料怎么放.md')
    if not os.path.exists(guide):
        with open(guide, 'w', encoding='utf-8') as f:
            f.write(GUIDE)
    log = os.path.join(base, 'intake-log.md')
    if not os.path.exists(log):
        with open(log, 'w', encoding='utf-8') as f:
            f.write('# 收件记录（示例）\n\n| 日期 | 文件 | 归位 | 说明 |\n|---|---|---|---|\n'
                    '| %s | 示例材料.pdf | 04-法院文件 | 演示数据 |\n' % datetime.now().strftime('%Y-%m-%d'))
    return base


def main():
    ap = argparse.ArgumentParser(description='生成演示数据（全部虚构）')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的 demo 数据（先备份）')
    args = ap.parse_args()

    demo = os.path.join(HERE, 'demo')
    if os.path.exists(demo):
        if not args.force:
            print('demo/ 已存在，跳过生成。要重建请加 --force（会先备份）。')
            return 0
        bak = os.path.join(demo, '.seed-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bak, exist_ok=True)
        for item in os.listdir(demo):
            if item.startswith('.seed-backup-'):
                continue
            shutil.move(os.path.join(demo, item), os.path.join(bak, item))
        print('已备份旧 demo 数据到', bak)

    ledger = config.abspath(config.get('paths.ledger')) or os.path.join(demo, '案件管理台账.xlsx')
    _write_ledger(ledger)
    print('台账：', ledger)

    root = config.abspath(config.get('paths.case_root')) or os.path.join(demo, '案件', '在办')
    archive = config.abspath(config.get('paths.archive_root')) or os.path.join(demo, '案件', '已结案')
    biz = config.abspath(config.get('paths.biz_root')) or os.path.join(demo, '案件')

    for name, is_exec in (('民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖', False),
                          ('民-A-26002_丙某诉丁某民间借贷纠纷', False)):
        print('案卷：', _make_case(root, name, is_exec))
    print('案卷：', _make_case(archive, '民-A-25009_已结案示例', done=True))
    print('案卷：', _make_case(root, '执-26003_戊公司执行案', exec_case=True))

    for d in ('律师函', '合同', '差旅发票'):
        os.makedirs(os.path.join(biz, d), exist_ok=True)

    print('\n完成。下一步：')
    print('  python app.py --port 8899')
    print('  浏览器打开 http://127.0.0.1:8899')
    return 0


if __name__ == '__main__':
    sys.exit(main())
