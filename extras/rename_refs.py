# -*- coding: utf-8 -*-
r"""改名引用同步器：把旧案件夹名替换为新名（配合 migration_apply --batch rename）

- 只扫「活引用」范围：wiki（排除 log.md / sessions/ 历史留痕）、My-case、Env-Maintenance\docs
- docs 内**历史报告不改**（留痕真实性），仅方案活文档参与替换
- 三重保险：① `A个案\旧名` 路径限定 ② `A个案/旧名` 路径限定 ③ **长名（≥10 字）裸名替换**
  （短名如「张前恩」会作为人名出现在正文，故不裸替换，避免污染）
- 默认 dry-run；`--apply` 才写盘；逐文件统计命中数
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from migration_apply import RENAME_MAP  # noqa: E402

WIKI = r'<知识库目录>'
ROOTS = [WIKI, r'<案件项目目录>', r'<工作目录>\docs']
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'sessions', '.obsidian'}
SKIP_FILES = {
    'log.md',
    # docs 内历史报告留痕不改（方案活文档仍参与替换）
    '阶段2-数据层-自测报告-20260912.md',
    '阶段3-Web操作台-自测报告-20260912.md',
    '阶段4-归档导出与外部服务联动-报告-20260912.md',
    '阶段5-历史迁移-dryrun清单-20260912.md',
    '阶段5-批1-补件执行报告-20260912.md',
    '阶段5-批2前置-编号补全与结构问题清单-20260912.md',
    '阶段5-批3-散件归位执行报告-20260912.md',
}
EXTS = {'.md', '.py', '.json', '.txt', '.js', '.bat'}


def build_pairs():
    out = []
    for old, new, _why in RENAME_MAP:
        out.append(('A个案\\' + old, 'A个案\\' + new))
        out.append(('A个案/' + old, 'A个案/' + new))
        if len(old) >= 10:
            out.append((old, new))
    return out


def scan(apply=False):
    pairs = build_pairs()
    hits = []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if fn in SKIP_FILES or os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                p = os.path.join(dp, fn)
                try:
                    t = open(p, encoding='utf-8', errors='replace').read()
                except OSError:
                    continue
                hit = [(a, b) for a, b in pairs if a in t]
                if not hit:
                    continue
                rec = {'path': p, 'n': sum(t.count(a) for a, _ in hit),
                       'kinds': len(hit), 'ok': None}
                if apply:
                    for a, b in hit:
                        t = t.replace(a, b)
                    try:
                        open(p, 'w', encoding='utf-8', newline='').write(t)
                        rec['ok'] = True
                    except OSError as e:
                        rec['ok'] = False
                        rec['err'] = repr(e)[:80]
                hits.append(rec)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    hits = scan(args.apply)
    tag = '执行' if args.apply else 'dry-run'
    print('[%s] 命中 %d 个文件' % (tag, len(hits)))
    for r in hits:
        rel = r['path']
        for k in ROOTS:
            if rel.startswith(k):
                rel = rel.replace(k + os.sep, '')
                break
        print('  %-4d 处 / %d 类  %s%s' % (r['n'], r['kinds'], rel[:96],
                                           '' if r['ok'] is None else
                                           ('  OK' if r['ok'] else '  FAIL ' + r.get('err', ''))))


if __name__ == '__main__':
    main()
