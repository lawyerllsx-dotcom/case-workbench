#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
migration_scan.py — 历史迁移扫描（法律工作台 · 阶段 5，**只读，绝不移动**）

产出：`docs\阶段5-历史迁移-dryrun清单-YYYYMMDD.md` + `data\migration_plan.json`

扫三类：
  1) 顶层散件盒（介绍信 / 所函 / 判决书 / 接收到的文件）→ 归位建议（并入哪个案件夹的哪个子目录）
  2) 案件夹命名合规性（v2 唯一格式 `编号_闽兴VS对方_案由`）→ 待归位清单
  3) 案件夹 v2 组件齐备度（00-收件箱 / pipeline / 执行案 09+10）

纪律：
  - **只读扫描**，不移动、不改名、不删除；一切建议须爸爸确认后才执行
  - 匹配不靠猜：只在「台账案件名 / 现有案件夹名」里命中当事人姓名/字号才给建议，否则标「需人工」
  - 编号（民-A-26NNN）不臆造 —— 现有案件夹无编号者标「待补编号（查台账 D/L 列）」

用法：
  python migration_scan.py [--out-dir <dir>] [--json-only]
"""
import argparse
import io
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ledger_ops  # noqa: E402

BASE = r'D:\案件\你的案卷根'
SCATTER_BOXES = {
    '介绍信': '01-委托手续',
    '所函': '01-委托手续',
    '判决书': '04-法院文件',
    '接收到的文件': '04-法院文件',
}
V2_NAME_RE = re.compile(r'^(民|执|仲)-[A-Z]-\d{4,6}_')
STOPWORDS = ['有限公司', '有限责任公司', '公司', '等', '与', 'vs', 'VS', '诉', '文件',
             '文件夹', '案件', '纠纷', '-', '（', '）', '(', ')']


def case_dirs():
    root = os.path.join(BASE, 'A个案')
    if not os.path.isdir(root):
        return []
    return [d for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]


def tokens(text):
    t = str(text)
    for w in STOPWORDS:
        t = t.replace(w, ' ')
    return [x for x in re.split(r'[^\w\u4e00-\u9fa5]+', t) if len(x) >= 2]


def ledger_names():
    names = []
    for sheet in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sheet):
            n = str(r.get('案件') or '').strip()
            if n:
                names.append({'sheet': sheet, 'row': r['_row'], 'name': n,
                              'client': str(r.get('委托人') or '')})
    return names


def build_index(dirs, lnames):
    """建「特征词 → 目标」索引：剔除泛用词（命中 >2 个目标的词一律丢弃，防乱指）"""
    raw = {}
    for n in lnames:
        for t in set(tokens(n['name'])):
            key = ('台账', '%s r%d %s' % (n['sheet'], n['row'], n['name']))
            raw.setdefault(t, set()).add(key)
    for d in dirs:
        for t in set(tokens(d)):
            raw.setdefault(t, set()).add(('案件夹', d))
    return {t: v for t, v in raw.items() if len(v) <= 2}


def match_case(label, index):
    """按特征词打分匹配；阈值 = 命中合计字符数 ≥3（≈一个 3 字姓名）或 ≥2 个词"""
    toks = tokens(label)
    score, hits = 0, []
    for t in toks:
        v = index.get(t)
        if not v:
            continue
        score += len(t)
        hits.extend(sorted(v))
    hits = list(dict.fromkeys(hits))
    if score < 3 and len(hits) < 2:
        return [], score
    return hits[:3], score


def scan_scatter(dirs, lnames, index):
    rows = []
    for box, target_sub in SCATTER_BOXES.items():
        p = os.path.join(BASE, box)
        if not os.path.isdir(p):
            continue
        for name in sorted(os.listdir(p)):
            full = os.path.join(p, name)
            is_dir = os.path.isdir(full)
            inner = sorted(os.listdir(full))[:5] if is_dir else []
            label = name if not is_dir else '%s（%d 项：%s）' % (name, len(os.listdir(full)),
                                                              '、'.join(inner))
            m, score = match_case(name, index)
            rows.append({
                'box': box, 'name': name, 'is_dir': is_dir,
                'count': len(os.listdir(full)) if is_dir else 1,
                'suggest_sub': target_sub,
                'match': m, 'score': score,
                'status': '建议' if m else '需人工',
                'label': label,
            })
    return rows


def scan_folders(dirs):
    out = []
    for d in dirs:
        p = os.path.join(BASE, 'A个案', d)
        subs = [x for x in sorted(os.listdir(p)) if os.path.isdir(os.path.join(p, x))]
        out.append({
            'name': d,
            'v2_named': bool(V2_NAME_RE.match(d)),
            'no_number': not bool(re.match(r'^(民|执|仲)-[A-Z]-', d)),
            'has_inbox': '00-收件箱' in subs,
            'has_pipeline': 'pipeline' in subs,
            'has_0905': ('09-财产查控' in subs, '10-处置分配' in subs),
            'exec_like': ('执行' in d),
            'n_files': sum(len(f) for _r, _d, f in os.walk(p)),
            'dirs': subs,
        })
    return out


def build():
    dirs = case_dirs()
    lnames = ledger_names()
    index = build_index(dirs, lnames)
    return {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'scatter': scan_scatter(dirs, lnames, index),
            'folders': scan_folders(dirs),
            'case_count': len(dirs), 'ledger_cases': len(lnames),
            'index_terms': len(index)}


def render_md(data):
    sc, fo = data['scatter'], data['folders']
    L = []
    L.append('# 阶段 5 历史迁移 dry-run 清单（%s）' % data['generated'])
    L.append('')
    L.append('> **本清单只读产出，未移动/未改名/未删除任何文件。** 动任何历史案件前须爸爸单独确认。')
    L.append('> 依据：案卷编排规范 v2（`wiki/synthesis/案件管理台账系统.md` §三）+ 方案 §4.4 / Q4 增量策略。')
    L.append('')
    L.append('## 一、顶层散件盒归位建议（%d 项）' % len(sc))
    L.append('')
    L.append('> 匹配法：特征词打分（泛用词已剔除——命中 >2 个目标的词一律丢弃），需合计字符 ≥3 才给建议；'
             '否则标「需人工」由爸爸指认。**建议仅指归属，不含改名。**')
    L.append('')
    L.append('| 散件盒 | 项 | 建议归入 | 匹配线索（得分） | 判定 |')
    L.append('|---|---|---|---|---|')
    for r in sc:
        m = '；'.join('%s：%s' % (a, b) for a, b in r['match']) if r['match'] else '—'
        L.append('| %s | %s | %s | %s（%d） | %s |' % (r['box'], r['label'], r['suggest_sub'],
                                                    m, r['score'], r['status']))
    L.append('')
    L.append('## 二、案件夹命名合规（%d 个案件夹）' % len(fo))
    L.append('')
    L.append('| 案件夹 | v2 命名 | 有编号 | 00-收件箱 | pipeline | 09/10 | 文件数 |')
    L.append('|---|---|---|---|---|---|---|')
    for f in fo:
        L.append('| %s | %s | %s | %s | %s | %s | %d |' % (
            f['name'][:38], '✅' if f['v2_named'] else '❌',
            '✅' if not f['no_number'] else '❌',
            '✅' if f['has_inbox'] else '❌',
            '✅' if f['has_pipeline'] else '❌',
            '✅' if all(f['has_0905']) else ('—' if not f['exec_like'] else '❌'),
            f['n_files']))
    L.append('')
    nok = [f for f in fo if not f['v2_named']]
    L.append('**待归位命名 %d 个**（按 Q4：动案时顺手改，改名前后路径登记迁移日志）：' % len(nok))
    L.append('')
    for f in nok:
        kind = ''
        m = re.match(r'^(民|执|仲)-(\d{3,6})', f['name'])
        if m:
            kind = '（旧式编号 `%s` → 需核对所内编号后统一为 `%s-A-…_闽兴VS对方_案由`）' % (
                m.group(0), m.group(1))
        elif f['no_number']:
            kind = '（缺编号 → 查台账 D 列案件类别 / L 列合同编号补）'
        L.append('- `%s`%s' % (f['name'], kind))
    L.append('')
    L.append('## 三、v2 组件缺口（可批量补，纯新增）')
    L.append('')
    L.append('- 缺 `00-收件箱`：%d 个' % sum(1 for f in fo if not f['has_inbox']))
    L.append('- 缺 `pipeline/`：%d 个' % sum(1 for f in fo if not f['has_pipeline']))
    L.append('- 缺 `09/10`（执行类案件）：%d 个' % sum(
        1 for f in fo if f['exec_like'] and not all(f['has_0905'])))
    L.append('')
    L.append('## 四、执行建议（分批，逐批确认）')
    L.append('')
    L.append('1. **批 1（零风险，纯新增）**：对缺件案件夹跑 `case_ops.py ensure-inbox`（不移动任何既有文件）')
    L.append('2. **批 2（改名，需逐案确认）**：按本清单 §二 逐案改名 `编号_闽兴VS对方_案由`，每次登记迁移日志')
    L.append('3. **批 3（散件归位，需逐项确认）**：按 §一 仅处理「建议」项，「需人工」项待爸爸指认归属')
    L.append('')
    L.append('> 命名/迁移纪律：**禁止删除**；改名与移动全部登记该案 `intake-log.md`；台账 B 列案号不动。')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='阶段 5 历史迁移只读扫描')
    ap.add_argument('--out-dir', default=r'<工作目录>\docs')
    ap.add_argument('--json-only', action='store_true')
    args = ap.parse_args()
    data = build()
    jp = os.path.join(HERE, 'data', 'migration_plan.json')
    os.makedirs(os.path.dirname(jp), exist_ok=True)
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('扫描完成：案件夹 %d 个｜顶层散件 %d 项｜台账案件 %d 条'
          % (data['case_count'], len(data['scatter']), data['ledger_cases']))
    print('JSON：%s' % jp)
    if args.json_only:
        return 0
    md = render_md(data)
    mp = os.path.join(args.out_dir, '阶段5-历史迁移-dryrun清单-%s.md'
                      % datetime.now().strftime('%Y%m%d'))
    os.makedirs(args.out_dir, exist_ok=True)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(md + '\n')
    print('清单：%s' % mp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
