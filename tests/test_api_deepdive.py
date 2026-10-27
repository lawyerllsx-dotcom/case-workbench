# -*- coding: utf-8 -*-
r"""test_api_deepdive.py — 案件研究报告接口回归（批 49 立，批 60 泛化目录）

覆盖：
  · 夹名安全闸（纯夹名 / 案件根第一层 / 越界与不存在分流）
  · _case_deepdive 清单（无目录 / 只收 .md / 排序与字段齐全 / 两类报告并列）
  · /api/deepdive/view 五道闸（缺参 / 纯文件名 / 只 .md / 目录锁定 / 存在性 + 正文一致）
  · /api/deepdive/open 逐级回退（研究报告 → 08-过程文件 → 案件夹）
  · api_case_detail 挂 deepdive 字段（台账依赖打桩）

⚠️ 全程在临时目录替身案件根上跑，**不碰真实 D:\案件**；不真打开资源管理器
   （_open_in_explorer 打桩记录调用，真实 explorer 行为由人工验收）。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
ROOT = os.path.dirname(HERE)                          # 项目根
sys.path.insert(0, ROOT)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def main():
    import app as A

    tmp = tempfile.mkdtemp(prefix='wb_dd_test_')
    opened = []
    orig_root = A.case_ops.case_root
    orig_rows = A.case_ops.ledger_case_rows
    orig_match = A.case_ops.match_case_ledger
    orig_tv = A.case_ops.travel_invoices
    orig_open = A._open_in_explorer
    try:
        A.case_ops.case_root = lambda: tmp
        A.case_ops.ledger_case_rows = lambda: []
        A.case_ops.match_case_ledger = lambda name, rows: {'matched': None, 'candidates': []}
        A.case_ops.travel_invoices = lambda rows: {'items': []}
        A._open_in_explorer = lambda p: (opened.append(p) or (True, p))

        case = '测试-被执行人深挖'
        cp = os.path.join(tmp, case)
        os.makedirs(cp)

        print('=== ① 夹名安全闸 ===')
        p, err = A._deepdive_case_path('')
        ck('空夹名 → 拒', p is None and bool(err))
        p, err = A._deepdive_case_path('..')
        ck('.. → 拒', p is None and bool(err))
        p, err = A._deepdive_case_path('a/b')
        ck('含 / → 拒', p is None and bool(err))
        p, err = A._deepdive_case_path('a\\b')
        ck('含 \\ → 拒', p is None and bool(err))
        p, err = A._deepdive_case_path('不存在夹')
        ck('不存在 → 拒且理由是不存在', p is None and '不存在' in (err or ''))
        p, err = A._deepdive_case_path(case)
        ck('合法夹名 → 过闸', p is not None and err is None)

        print('=== ② _case_deepdive 清单 ===')
        d0 = A._case_deepdive(cp)
        ck('无研究报告目录 → count=0 / exists=False', d0['count'] == 0 and d0['exists'] is False)
        dd = os.path.join(cp, A.DEEPDIVE_SUBDIR)
        os.makedirs(dd)
        with open(os.path.join(dd, '被执行人深挖报告-20261009.md'), 'w', encoding='utf-8') as f:
            f.write('# 测试正文\n甲公司股东乙')
        with open(os.path.join(dd, 'b报告.md'), 'w', encoding='utf-8') as f:
            f.write('第二份')
        with open(os.path.join(dd, '承办法官裁判参考-王法官-20261013.md'), 'w', encoding='utf-8') as f:
            f.write('裁判参考测试')
        with open(os.path.join(dd, '干扰.txt'), 'w', encoding='utf-8') as f:
            f.write('不算')
        d1 = A._case_deepdive(cp)
        ck('只收 .md → count=3（批 60：两类报告并列）', d1['count'] == 3 and d1['exists'] is True)
        ck('裁判参考与深挖报告同目录并列列出',
           any('承办法官裁判参考' in i['name'] for i in d1['items']))
        ck('items 字段齐全（name/size_kb/mtime）',
           all(set(('name', 'size_kb', 'mtime')) <= set(i) for i in d1['items']))
        ck('按文件名排序', [i['name'] for i in d1['items']] == sorted(i['name'] for i in d1['items']))

        print('=== ③ view 五道闸 ===')
        r = A.api_deepdive_view({'case': case, 'file': ''})
        ck('缺 file → 拒', 'error' in r)
        r = A.api_deepdive_view({'case': case, 'file': '../x.md'})
        ck('file 含 .. → 拒', 'error' in r)
        r = A.api_deepdive_view({'case': case, 'file': 'a/b.md'})
        ck('file 含分隔符 → 拒', 'error' in r)
        r = A.api_deepdive_view({'case': case, 'file': 'x.txt'})
        ck('非 .md → 拒且理由点 .md', 'error' in r and '.md' in r['error'])
        r = A.api_deepdive_view({'case': case, 'file': '没有这份.md'})
        ck('不存在 → 拒', 'error' in r and '不存在' in r['error'])
        # 目录锁定：案件夹根级真实存在的 .md 也不放行（只认深挖报告目录内）
        with open(os.path.join(cp, 'intake-log.md'), 'w', encoding='utf-8') as f:
            f.write('根级文件')
        r = A.api_deepdive_view({'case': case, 'file': 'intake-log.md'})
        ck('目录外 .md（根级真实存在）→ 拒', 'error' in r)
        r = A.api_deepdive_view({'case': case, 'file': '被执行人深挖报告-20261009.md'})
        ck('正常预览 ok + 正文一致', r.get('ok') is True and '甲公司股东乙' in r.get('text', ''))

        print('=== ④ open 逐级回退 ===')
        opened.clear()
        r = A.api_deepdive_open({'case': case})
        ck('目录在 → 开深挖报告目录',
           r.get('ok') is True and opened and opened[-1].endswith(A.DEEPDIVE_SUBDIR))
        shutil.rmtree(dd)
        os.makedirs(os.path.join(cp, '08-过程文件'), exist_ok=True)  # 父目录随深挖目录已建过
        opened.clear()
        r = A.api_deepdive_open({'case': case})
        ck('无深挖目录 → 回退 08-过程文件',
           r.get('ok') is True and opened and opened[-1].endswith('08-过程文件'))
        os.rmdir(os.path.join(cp, '08-过程文件'))
        opened.clear()
        r = A.api_deepdive_open({'case': case})
        ck('都不在 → 回退案件夹根',
           r.get('ok') is True and opened and os.path.abspath(opened[-1]) == os.path.abspath(cp))
        r = A.api_deepdive_open({'case': '不存在夹'})
        ck('open 不存在夹 → 拒', 'error' in r)

        print('=== ⑤ case_detail 挂 deepdive 字段 ===')
        os.makedirs(dd)
        with open(os.path.join(dd, '被执行人深挖报告-20261009.md'), 'w', encoding='utf-8') as f:
            f.write('# 回来了一份')
        det = A.api_case_detail(case)
        ck('detail 含 deepdive 且 count=1',
           isinstance(det.get('deepdive'), dict) and det['deepdive']['count'] == 1)
    finally:
        A.case_ops.case_root = orig_root
        A.case_ops.ledger_case_rows = orig_rows
        A.case_ops.match_case_ledger = orig_match
        A.case_ops.travel_invoices = orig_tv
        A._open_in_explorer = orig_open
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n=== 结果：%d/%d 通过 ===' % (len(total) - len(fails), len(total)))
    if fails:
        print('FAIL 项：')
        for f in fails:
            print('  - ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
