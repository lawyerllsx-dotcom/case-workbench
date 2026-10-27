# -*- coding: utf-8 -*-
"""dup_scan.py — 案件目录「真重复」只读扫描器（法律工作台 · 阶段 5 批 5 沉淀）

用途：按 md5 找出案件树内**内容完全相同**的文件，输出分组报告供人工甄别。
      **只读，零改动**——本工具不删除、不移动、不合并任何文件。

定位：case_workbench 的侦察端（与 migration_scan / migration_apply 同族）。

口径（2026-09-12 定，避免误报）：
  - 排除 .json / .progress / .pyc（OCR gate、进度文件天然同内容）
  - 排除 < --min-size（默认 1KB）的小文件
  - 分组后按「是否跨案件夹」分两类：跨案多为同源模板副本（保留）；同案内多为刻意双份（需人工判断）

用法：
    python dup_scan.py                       # 默认 A个案，输出到 docs\\_batch5_dup_groups.md
    python dup_scan.py --root <案件根> --out <报告.md> --min-size 1024
"""
import argparse
import config as _config
import hashlib
import io
import os
import re
import sys
from collections import defaultdict

VERSION = '1.1.0'
import config as _config        # 开源版：扫描根与输出去向由配置决定

DEFAULT_ROOT = _config.path('paths.case_root', env='CASE_OPS_ROOT')
DEFAULT_OUT = _config.path('paths.dup_out')

SKIP_EXT = {'.json', '.pyc', '.progress'}
SKIP_NAME = {'_png_list.txt', '_batch_index.json'}
RAWNAME = re.compile(r'^([0-9a-zA-Z\-\_\.]{6,}|微信图片_\d+.*|Screenshot_\d+.*)$')


def case_of(rel):
    """案件口径：`A闽兴\\` 下按**两层**计案（子夹即一案），避免同客户下两案被误并为同案"""
    parts = rel.split(os.sep)
    if parts[0] == 'A闽兴' and len(parts) > 2:
        return parts[0] + os.sep + parts[1]
    return parts[0]


def classify(rels):
    """把一组重复文件归入模式类别，便于人工批量判断"""
    bs = [os.path.basename(r) for r in rels]
    joined = ' | '.join(rels)
    norm = set(re.sub(r'\s*\(\d+\)', '', b) for b in bs)
    if len(norm) == 1 and any(re.search(r'\(\d+\)', b) for b in bs):
        return 'A 括号副本（(1)/(2)…）'
    if len(set(bs)) == 1 and len(set(os.path.dirname(r) for r in rels)) == len(rels):
        return 'B 同名多目录冗余'
    if re.search(r'提交给?法院的证据', joined) and re.search(r'(^|[\\/])证据([\\/]|$)', joined):
        return 'C 证据两阶段并存（证据 ↔ 提交法院的证据）'
    stripped = set(re.sub(r'^\d+\s*[-、\.]\s*', '', b) for b in bs)
    if len(stripped) == 1 and len(set(bs)) > 1:
        return 'D 编号化/语义化改名 ↔ 原件并存'
    if any(RAWNAME.match(b) for b in bs) and any(re.search(r'[\u4e00-\u9fff]', b) for b in bs):
        return 'D 编号化/语义化改名 ↔ 原件并存'
    if len(set(bs)) > 1 and all(re.search(r'(副本|定稿|草案|备份|终稿|修改|补充|草稿)', b) for b in bs):
        return 'E 版本/备份并存'
    if any(re.search(r'(_converted|_fromDocx|ocr|OCR|_test|_tmp)', r, re.I) for r in rels):
        return 'F 转换/中间产物'
    return 'G 其他（需人看）'


def scan(root, min_size):
    files = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith('~$')]
        for f in fn:
            if os.path.splitext(f)[1].lower() in SKIP_EXT or f in SKIP_NAME:
                continue
            fp = os.path.join(dp, f)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz < min_size:
                continue
            files.append((os.path.relpath(fp, root), fp, sz))

    by_size = defaultdict(list)
    for rel, fp, sz in files:
        by_size[sz].append((rel, fp))

    groups, hashed = [], 0
    for sz, items in by_size.items():
        if len(items) < 2:
            continue
        m = defaultdict(list)
        for rel, fp in items:
            h = hashlib.md5()
            with open(fp, 'rb') as fh:
                for c in iter(lambda: fh.read(1 << 20), b''):
                    h.update(c)
            hashed += 1
            m[h.hexdigest()].append(rel)
        for hx, rels in m.items():
            if len(rels) > 1:
                groups.append((sz, hx, sorted(rels)))
    return files, groups


def load_exempt(path):
    """读「已知正常」豁免清单（用户拍板结论），避免同一批重复件反复上报"""
    import json
    if not path or not os.path.isfile(path):
        return {'rules': []}
    try:
        return json.loads(io.open(path, encoding='utf-8').read())
    except Exception:                                     # noqa: BLE001
        return {'rules': []}


def exempt_hit(rels, ex):
    """返回 (类别, 依据) 或 (None, '')。规则：组内案件/文件名全部命中才算豁免。"""
    cases = set(case_of(r) for r in rels)
    n = len(cases)
    for rule in ex.get('rules', []):
        kind, basis = rule.get('kind', ''), rule.get('basis', '')
        # 规则 1：案件对 + 路径关键词
        if 'case_pairs' in rule:
            if n == 2 and cases in [set(p) for p in rule['case_pairs']]:
                kw = rule.get('path_any', [])
                if not kw or all(any(k in r for k in kw) for r in rels):
                    return kind, basis
        # 规则 2：参考案借放 —— 必须**跨案**（材料出现在别人的案夹里），同案内重复不算
        if 'path_any' in rule and 'case_pairs' not in rule:
            kw = rule['path_any']
            if kw and all(any(k in r for k in kw) for r in rels):
                return kind, basis
            if rule.get('case_any') and n >= 2 and cases <= set(rule['case_any']):
                return kind, basis
        # 规则 3：文件名白名单（同源模板套件）
        if 'basenames' in rule:
            allow = set(rule['basenames'])
            if all(os.path.basename(r) in allow for r in rels):
                return kind, basis
    return None, ''


def render(root, files, groups, min_size, triage=False, ex=None):
    ex = ex or {'rules': []}
    exempted, cross, same = [], [], []
    for g in groups:
        kind, basis = exempt_hit(g[2], ex)
        if kind:
            exempted.append((kind, basis, g))
        elif len(set(case_of(r) for r in g[2])) > 1:
            cross.append(g)
        else:
            same.append(g)

    L = ['# 案件目录真重复分组（dup_scan.py 只读产物）', '',
         '> 扫描根：`%s`（%d 文件 ≥%dB 参与比对）' % (root, len(files), min_size),
         '> 去噪：排除 .json/.progress/.pyc 与小于阈值的文件',
         '> 案件口径：`A闽兴\\` 下按两层计案（子夹即一案）',
         '> **本报告不含任何删除/合并动作**——仅供人工甄别。', '',
         '## 汇总', '', '| 口径 | 组数 |', '|---|---|',
         '| 待处理·同案内重复 | %d |' % len(same),
         '| 待处理·跨案重复 | %d |' % len(cross),
         '| **已知正常（已豁免）** | %d |' % len(exempted),
         '| 合计 | %d |' % len(groups), '']

    if triage:
        buckets = defaultdict(list)
        for g in same:
            buckets[classify(g[2])].append(g)
        L += ['## 模式聚类（待处理·同案内 %d 组 → 分类）' % len(same), '',
              '| 类别 | 组数 | 占比 |', '|---|---|---|']
        tot = len(same) or 1
        for k in sorted(buckets):
            L.append('| %s | %d | %.0f%% |' % (k, len(buckets[k]), 100.0 * len(buckets[k]) / tot))
        L.append('')
        for k in sorted(buckets):
            gs = buckets[k]
            L.append('\n### %s —— %d 组' % (k, len(gs)))
            for sz, hx, rels in sorted(gs, key=lambda g: -g[0])[:12]:
                L.append('\n**%s（%d B）**' % (hx[:8], sz))
                L += ['- `%s`' % r for r in rels]
            if len(gs) > 12:
                L.append('\n… 其余 %d 组同类（明细见下方全量清单）' % (len(gs) - 12))

    cross_pairs = defaultdict(int)
    for g in cross:
        cross_pairs[' + '.join(sorted(set(case_of(r) for r in g[2])))] += 1
    L.append('## 一、待处理·跨案重复（%d 组，按案件对）' % len(cross))
    L.append('\n| 案件对 | 组数 |')
    L.append('|---|---|')
    for k, v in sorted(cross_pairs.items(), key=lambda x: -x[1]):
        L.append('| %s | %d |' % (k, v))
    for sz, hx, rels in sorted(cross, key=lambda x: -x[0]):
        L.append('\n**%s（%d B）**' % (hx[:8], sz))
        L += ['- `%s`' % r for r in rels]

    L.append('\n## 二、待处理·同案内重复（%d 组，全量）' % len(same))
    for sz, hx, rels in sorted(same, key=lambda x: -x[0]):
        L.append('\n**%s（%d B）**' % (hx[:8], sz))
        L += ['- `%s`' % r for r in rels]

    if exempted:
        by_kind = defaultdict(list)
        for kind, basis, g in exempted:
            by_kind[(kind, basis)].append(g)
        L.append('\n## 三、已知正常（豁免 %d 组，不再上报）' % len(exempted))
        L.append('\n> 依据来自 `case_workbench\\dup_exempt.json`（用户逐项拍板），新增须注明依据与日期。')
        for (kind, basis), gs in sorted(by_kind.items()):
            L.append('\n### %s —— %d 组' % (kind, len(gs)))
            L.append('\n> 依据：%s' % basis)
            for sz, hx, rels in sorted(gs, key=lambda g: -g[0])[:6]:
                L.append('- `%s`（%d B，%d 份）' % (rels[0], sz, len(rels)))
            if len(gs) > 6:
                L.append('- … 其余 %d 组同类' % (len(gs) - 6))
    return '\n'.join(L) + '\n', len(same), len(cross)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                     # noqa: BLE001
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='案件目录真重复只读扫描 v%s' % VERSION)
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--min-size', type=int, default=1024)
    ap.add_argument('--triage', action='store_true',
                    help='追加「模式聚类」段（把重复组归类，便于批量判断）')
    ap.add_argument('--exempt', default=os.path.join(here, 'dup_exempt.json'),
                    help='「已知正常」豁免清单 JSON（默认同目录 dup_exempt.json）')
    ap.add_argument('--no-exempt', action='store_true', help='忽略豁免清单，全量上报')
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print('输入错误：案件根不存在 → %s' % args.root)
        return 2
    ex = {'rules': []} if args.no_exempt else load_exempt(args.exempt)
    files, groups = scan(args.root, args.min_size)
    md, n_same, n_cross = render(args.root, files, groups, args.min_size,
                                 triage=args.triage, ex=ex)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    io.open(args.out, 'w', encoding='utf-8').write(md)
    print('已写出 %s' % args.out)
    print('参与比对 %d 文件；重复 %d 组（待处理 同案 %d / 跨案 %d；豁免规则 %d 条）'
          % (len(files), len(groups), n_same, n_cross, len(ex.get('rules', []))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
