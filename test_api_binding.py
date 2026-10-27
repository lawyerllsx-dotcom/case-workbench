# -*- coding: utf-8 -*-
r"""test_api_binding.py — 卷宗装订包接口回归（批 59，2026-10-13）

覆盖：
  · 夹名安全闸（空 / ../含分隔符 / 不存在 / 合法放行）
  · dry-run 零副作用（不产生 卷宗装订\ 目录）+ 分拣口径（入卷序 / 排除目录 / 统计 / 警告）
  · apply 生成：产物落盘、页数 = 前置 4 + 正文、书签（含材料书签指对物理页）、
    正文连续页码、前置页不打码、目录页含材料名与页码、md 未并入清单
  · 防重名：同秒连跑两份产物并存（不覆盖）
  · 坏 PDF 跳过（bad 计数）与空材料拒
  · open 逐级回退（卷宗装订 → 案件夹根）
  · meta 台账打桩（未匹配 matched=False；匹配时字段带入）

⚠️ 全程在临时目录替身案件根上跑，**不碰真实 D:\案件**；不真打开资源管理器
   （_open_in_explorer 打桩记录调用）。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pymupdf                                            # noqa: E402

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def _mkpdf(path, n, tag):
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), '%s p%d' % (tag, i + 1), fontsize=12)
    d.save(path)
    d.close()


def main():
    import app as A

    tmp = tempfile.mkdtemp(prefix='wb_bind_test_')
    opened = []
    orig_root = A.case_ops.case_root
    orig_rows = A.case_ops.ledger_case_rows
    orig_match = A.case_ops.match_case_ledger
    orig_open = A._open_in_explorer
    try:
        A.case_ops.case_root = lambda: tmp
        A.case_ops.ledger_case_rows = lambda: []
        A.case_ops.match_case_ledger = lambda name, rows: {'matched': None, 'candidates': []}
        A._open_in_explorer = lambda p: (opened.append(p) or (True, p))

        case = '测试-卷宗装订'
        cp = os.path.join(tmp, case)
        for d in ('01-委托手续', '04-法院文件', '00-收件箱', 'pipeline'):
            os.makedirs(os.path.join(cp, d))
        _mkpdf(os.path.join(cp, '01-委托手续', '所函.pdf'), 1, 'W')
        _mkpdf(os.path.join(cp, '04-法院文件', '判决书.pdf'), 3, 'P')
        open(os.path.join(cp, '04-法院文件', '地址确认书.docx'), 'wb').write(b'PK fake')
        _mkpdf(os.path.join(cp, '00-收件箱', '未分拣.pdf'), 1, 'X')
        bind_dir = os.path.join(cp, A.binding_ops.BIND_SUBDIR)

        print('=== ① 夹名安全闸 ===')
        ck('空夹名 → 拒', 'error' in A.api_binding_build({'case': ''}))
        ck('.. → 拒', 'error' in A.api_binding_build({'case': '..'}))
        ck('含 / → 拒', 'error' in A.api_binding_build({'case': 'a/b'}))
        ck('不存在 → 拒', 'error' in A.api_binding_build({'case': '不存在夹'}))

        print('=== ② dry-run 零副作用 + 分拣口径 ===')
        r = A.api_binding_build({'case': case, 'confirm': False})
        ck('dry_run=True 且未产生产物目录', r.get('dry_run') is True and not os.path.isdir(bind_dir))
        sc = r['plan']['scan']
        ck('入卷序 = 01 → 04', [s['dir'] for s in sc['sections']] == ['01-委托手续', '04-法院文件'])
        ck('排除目录含 00-收件箱 + pipeline',
           {e['dir'] for e in sc['excluded']} >= {'00-收件箱', 'pipeline'})
        ck('统计 3 份 / 可并入 2 / 未并入 1',
           sc['stats']['total'] == 3 and sc['stats']['mergeable'] == 2 and sc['stats']['skipped'] == 1)
        ck('警告含未分拣提示', any('未分拣' in w for w in r['plan']['warnings']))
        ck('meta 未匹配 → matched=False 且案件名回退夹名',
           r['meta']['matched'] is False and r['meta']['案件名'] == case)

        print('=== ③ apply 生成（页数/书签/页码/目录）===')
        r = A.api_binding_build({'case': case, 'confirm': True})
        ck('生成 ok', r.get('ok') is True and r.get('dry_run') is False)
        pdf_p = os.path.join(bind_dir, r['pdf'])
        md_p = os.path.join(bind_dir, r['md'])
        ck('两件产物落盘', os.path.isfile(pdf_p) and os.path.isfile(md_p))
        ck('页数 = 前置 4 + 正文 4 = 8', r['pages'] == 8 and r['body_pages'] == 4)
        doc = pymupdf.open(pdf_p)
        ck('总页数与开卷一致', doc.page_count == 8)
        ck('书签 6 条（4 前置 + 2 材料）', len(doc.get_toc()) == 6)
        ck('材料书签指对物理页（1. 所函.pdf → 5）', doc.get_toc()[4] == [1, '1. 所函.pdf', 5])
        ck('正文第 1 页打 - 1 -', '- 1 -' in doc[4].get_text())
        ck('正文末页打 - 4 -', '- 4 -' in doc[-1].get_text())
        ck('前置页不打页码', '- 1 -' not in doc[0].get_text() and '- 1 -' not in doc[2].get_text())
        ck('目录页含材料名 + 起止页（2-4）',
           '所函.pdf' in doc[2].get_text() and '2-4' in doc[2].get_text())
        ck('封面含案件名', case in doc[0].get_text())
        doc.close()
        md = open(md_p, encoding='utf-8').read()
        ck('md 含未并入清单', '地址确认书.docx' in md)
        ck('md 含未入卷目录', '00-收件箱' in md)

        print('=== ④ 防重名（同秒连跑不覆盖）===')
        r2 = A.api_binding_build({'case': case, 'confirm': True})
        ck('第二份文件名不同且两份并存',
           r2['pdf'] != r['pdf'] and os.path.isfile(pdf_p)
           and os.path.isfile(os.path.join(bind_dir, r2['pdf'])))

        print('=== ⑤ 坏 PDF / 空材料 ===')
        case2 = '测试-空案'
        cp2 = os.path.join(tmp, case2)
        os.makedirs(os.path.join(cp2, '01-委托手续'))
        r = A.api_binding_build({'case': case2, 'confirm': True})
        ck('空材料 → 拒且说明原因', 'error' in r and '没有可并入' in r['error'])
        _mkpdf(os.path.join(cp2, '01-委托手续', 'ok.pdf'), 1, 'OK')
        open(os.path.join(cp2, '01-委托手续', 'bad.pdf'), 'wb').write(b'%PDF-1.4 broken')
        r = A.api_binding_build({'case': case2, 'confirm': True})
        ck('坏 PDF 跳过后仍生成（merged=1 bad=1）',
           r.get('ok') and r['merged'] == 1 and r['bad'] == 1)

        print('=== ⑥ open 逐级回退 ===')
        opened.clear()
        r = A.api_binding_open({'case': case})
        ck('产物目录在 → 开产物目录',
           r.get('ok') is True and opened and opened[-1].endswith(A.binding_ops.BIND_SUBDIR))
        shutil.rmtree(bind_dir)
        opened.clear()
        r = A.api_binding_open({'case': case})
        ck('产物目录不在 → 回退案件夹根',
           r.get('ok') is True and opened and os.path.abspath(opened[-1]) == os.path.abspath(cp))
        ck('open 不存在夹 → 拒', 'error' in A.api_binding_open({'case': '不存在夹'}))

        print('=== ⑦ meta 台账打桩（匹配时应带入）===')
        A.case_ops.match_case_ledger = lambda name, rows: {'matched': {
            '案件': '张三诉李四', '委托人': '张三', '案件类别': '民事',
            '案号': '（2026）闽0000民初1号', '法院': '思明法院', '承办律师': '王律师',
            '标的额': '100000', '当前进度': '已立案', '状态': '已立案',
            'match_score': 5, 'timeline': []}}
        r = A.api_binding_build({'case': case, 'confirm': False})
        ck('meta 带台账字段（案号/法院）',
           r['meta']['matched'] is True and r['meta']['案号'] == '（2026）闽0000民初1号'
           and r['meta']['法院'] == '思明法院')
    finally:
        A.case_ops.case_root = orig_root
        A.case_ops.ledger_case_rows = orig_rows
        A.case_ops.match_case_ledger = orig_match
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
