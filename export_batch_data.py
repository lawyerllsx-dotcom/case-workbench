#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
export_batch_data.py — 台账 → 批量文书系统「数据表」派生导出（法律工作台 · 阶段 4）

背景（2026-09-12 实测）：`<批量文书目录>\数据\示例案件数据.xlsx` 共 5 sheet
  `原告主体`(3 条) `案件`(0 条·仅表头) `被告`(0) `证据清单`(0) `财产线索`(0)
→ 台账是**单一事实源**，批量文书链路需要的「案件行」可由台账派生，但**违约金参数 / 事实理由正文 /
  被告 / 证据 / 财产线索**无法从台账得到，必须人工补——本脚本只派生能派生的，其余列**留空并列清单**。

纪律：
  - **默认只写到 `数据\生成\`**，绝不直接改 `示例案件数据.xlsx`（生成链上游，误改会连带批量出文书出错）
  - `--overwrite` 才写正式文件，且**先备份**到 `<数据备份目录>\`
  - 只读台账，不回写；金额解析失败 → 留空 + 标注，不猜

用法：
  python export_batch_data.py [--out-dir ...] [--dry-run]
  python export_batch_data.py --overwrite        # 写正式数据表（先备份）
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ledger_ops  # noqa: E402
import config as _config  # noqa: E402

VERSION = '1.0.0'
BATCH_DIR = _config.path('paths.batch_dir') or os.path.join(HERE, 'demo', '批量文书')
DATA_XLSX = os.path.join(BATCH_DIR, '数据', '案件数据.xlsx')
GEN_DIR = os.path.join(BATCH_DIR, '数据', '生成')
BACKUP_ROOT = _config.path('paths.batch_backup') or os.path.join(BATCH_DIR, '备份')

# 案由 关键词（从 C 列尾部解析）
CAUSES = ['买卖合同纠纷', '买卖合同', '服务合同纠纷', '劳动争议', '仲裁', '执行异议',
          '物权确认纠纷', '民间借贷', '噪声污染', '物业服务合同纠纷']

COLS_CASE = ['case_key', 'entity_key', '案由', '担保类型', '货款本金', '违约金计算起始日',
             '年利率', '暂计截止日', '逾期违约金', '律师费', '差旅费', '诉请总额',
             '销售合同编号', '合同签订日期', '事实理由正文_起诉状', '事实理由正文_保全',
             '输出文件夹']
AUTO_COLS = ['case_key', 'entity_key', '案由', '货款本金', '律师费', '诉请总额',
             '输出文件夹']
MANUAL_COLS = [c for c in COLS_CASE if c not in AUTO_COLS]


def _n(v):
    if v is None:
        return None
    s = str(v).replace(',', '').replace('，', '').replace('元', '').replace(' ', '')
    m = re.search(r'\d+(?:\.\d+)?', s)
    return float(m.group()) if m else None


def read_entities():
    """读批量文书数据表的 原告主体 → [(entity_key, 原告名称)]"""
    import openpyxl
    if not os.path.isfile(DATA_XLSX):
        return []
    wb = openpyxl.load_workbook(DATA_XLSX, read_only=True, data_only=True)
    try:
        if '原告主体' not in wb.sheetnames:
            return []
        ws = wb['原告主体']
        rows = list(ws.iter_rows(values_only=True))
        out = []
        for r in rows[1:]:
            if r and r[0]:
                out.append((str(r[0]), str(r[1] or '')))
        return out
    finally:
        wb.close()


def read_existing_cases():
    import openpyxl
    if not os.path.isfile(DATA_XLSX):
        return []
    wb = openpyxl.load_workbook(DATA_XLSX, read_only=True, data_only=True)
    try:
        if '案件' not in wb.sheetnames:
            return []
        ws = wb['案件']
        rows = list(ws.iter_rows(values_only=True))
        keys = []
        for r in rows[1:]:
            if r and r[0]:
                keys.append(str(r[0]))
        return keys
    finally:
        wb.close()


def match_entity(case_row, entities):
    """按委托人名匹配 entity_key（含简称兜底：'福建委托人' ⊂ '福建委托人饲料有限公司'）"""
    client = str(case_row.get('委托人') or '')
    for key, name in entities:
        if client and (client == name or client in name or name in client):
            return key
    for key, name in entities:
        short = re.sub(r'(有限公司|有限责任公司|公司)$', '', name)
        if short and short in client:
            return key
    return ''


def parse_cause(case_row):
    text = str(case_row.get('案件') or '')
    for c in CAUSES:
        if c in text:
            return c
    return ''


def parse_principal(case_row):
    """N 列：'296,532元（本金）+违约金+律师费，暂合计330,015.83元' → (本金, 诉请总额)"""
    text = str(case_row.get('标的额') or '')
    principal = None
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*元', text)
    if m:
        principal = _n(m.group(1))
    total = None
    m2 = re.search(r'暂?合计[约为]*\s*([\d,]+(?:\.\d+)?)', text)
    if m2:
        total = _n(m2.group(1))
    return principal, total


def build():
    rows = ledger_ops.read_rows('案件')
    entities = read_entities()
    existing = set(read_existing_cases())
    out, manual = [], []
    for r in rows:
        case = str(r.get('案件') or '').strip()
        if not case:
            continue
        principal, total = parse_principal(r)
        key = 'taizhang_r%d' % r['_row']
        item = {
            'case_key': key,
            'entity_key': match_entity(r, entities),
            '案由': parse_cause(r),
            '担保类型': '',
            '货款本金': principal,
            '违约金计算起始日': '',
            '年利率': '',
            '暂计截止日': '',
            '逾期违约金': '',
            '律师费': _n(r.get('基础律师费')),
            '差旅费': '',
            '诉请总额': total,
            '销售合同编号': '',
            '合同签订日期': '',
            '事实理由正文_起诉状': '',
            '事实理由正文_保全': '',
            '输出文件夹': '',
            '_序号': r.get('序号'), '_台账行': r['_row'], '_案件': case,
            '_委托人': r.get('委托人'), '_法院': r.get('法院/仲裁'),
            '_状态': r.get('最新状态'), '_已有case_key': key in existing,
        }
        out.append(item)
        miss = [c for c in AUTO_COLS if item.get(c) in ('', None)]
        manual.append({'case_key': key, '案件': case,
                       '自取列缺失': '、'.join(miss) or '—',
                       '必须人工补的列': '、'.join(MANUAL_COLS)})
    return out, manual, entities, existing


def write_xlsx(rows, manual, path):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '案件_台账派生'
    ws.append(COLS_CASE)
    for c in ws[1]:
        c.font = Font(bold=True)
    for it in rows:
        ws.append([it.get(c) for c in COLS_CASE])
    ws2 = wb.create_sheet('待人工补')
    ws2.append(['case_key', '案件', '自取列缺失', '必须人工补的列'])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for m in manual:
        ws2.append([m['case_key'], m['案件'], m['自取列缺失'], m['必须人工补的列']])
    ws3 = wb.create_sheet('来源定位')
    ws3.append(['case_key', '台账行', '台账序号', '案件', '委托人', '法院/仲裁', '状态', '是否已在数据表中'])
    for c in ws3[1]:
        c.font = Font(bold=True)
    for it in rows:
        ws3.append([it['case_key'], it['_台账行'], it['_序号'], it['_案件'], it['_委托人'],
                    it['_法院'], it['_状态'], '是' if it['_已有case_key'] else '否'])
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description='台账→批量文书数据表派生导出 v%s' % VERSION)
    ap.add_argument('--out-dir', default=GEN_DIR)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--overwrite', action='store_true',
                    help='写正式 示例案件数据.xlsx（先备份到 D:\\备份\\05-批量文书数据备份）')
    args = ap.parse_args()

    rows, manual, entities, existing = build()
    print('台账示例案件 %d 条；原告主体库 %d 条；数据表现有案件行 %d 条'
          % (len(rows), len(entities), len(existing)))
    matched = sum(1 for r in rows if r['entity_key'])
    print('可匹配原告主体：%d/%d；可在数据表中找到同名 case_key：%d'
          % (matched, len(rows), len(existing & {r['case_key'] for r in rows})))
    if args.dry_run:
        for it in rows:
            print('  %-14s %-10s 本金=%-12s 律师费=%-10s 总额=%-12s 案由=%s' % (
                it['case_key'], it['entity_key'] or '（未匹配）',
                it['货款本金'], it['律师费'], it['诉请总额'], it['案由'] or '（未识别）'))
        print('（--dry-run，未写文件）')
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    dst = os.path.join(args.out_dir, '台账派生-案件行-%s.xlsx' % stamp)
    write_xlsx(rows, manual, dst)
    print('已生成：%s' % dst)
    print('说明：违约金参数 / 事实理由 / 被告 / 证据 / 财产线索 无法从台账派生 → 见 sheet「待人工补」')

    if args.overwrite:
        if not os.path.isfile(DATA_XLSX):
            print('目标数据表不存在，跳过覆盖：%s' % DATA_XLSX)
            return 2
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        bak = os.path.join(BACKUP_ROOT, '示例案件数据-备份-%s.xlsx' % stamp)
        shutil.copy2(DATA_XLSX, bak)
        with open(os.path.join(BACKUP_ROOT, 'manifest.md'), 'a', encoding='utf-8') as f:
            f.write('| %s | %s | %s |\n' % (datetime.now().strftime('%Y-%m-%d %H:%M'),
                                            os.path.basename(bak), os.path.basename(DATA_XLSX)))
        print('已备份原数据表：%s' % bak)
        print('⚠️ 覆盖正式数据表需人工确认（本脚本不自动合并列）：请把派生行按 case_key 手工粘贴进「案件」sheet，保留原表其他 sheet 不动')
    return 0


if __name__ == '__main__':
    sys.exit(main())
