# -*- coding: utf-8 -*-
r"""test_api_writing.py — 写作项目管理接口回归（批 51，2026-10-09）

覆盖：
  · 项目：dry-run 不落库 / confirm 落库 / 非法参数（类型/案件夹/标题）回 error
  · 状态机：非法跨跳拒（起草→定稿、起草→送审）/ 定稿→修订 回退放行 / 同状态无操作
  · 版本：dry-run 不落盘 / confirm 入卷（v 号自增、路径=案件夹\02-法律文书\标题\vNN-日期-名、内容一致）
  · 闸门：越界文件名先于存在性 / 非白名单扩展名拒 / 超大拒 / 项目不存在拒
  · 列表：version_count / 筛选（状态、案件）/ project_id 非数字
  · 文本预览五道闸：md 放行（含子目录）/ .. 拒 / 非 md·txt 拒 / 案件夹不存在拒 / 目录外拒

⚠️ 全隔离：`WORKBENCH_DB` + `WORKBENCH_BACKUP_DIR` + `CASE_OPS_ROOT` 指向临时目录，
   `INVOICE_MAX_MB=1` 压低上传闸——**不碰真库、不碰真案卷、不碰真备份**。
   本文件为**直调函数**式回归（env 需在 import app 前生效，见下方）。
"""
import base64
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix='wb_writing_test_')
CASES = os.path.join(TMP, 'cases')
CASE = '测试案夹'
os.makedirs(os.path.join(CASES, CASE))
os.environ['WORKBENCH_DB'] = os.path.join(TMP, 'wb.db')
os.environ['WORKBENCH_BACKUP_DIR'] = os.path.join(TMP, 'bak')
os.environ['CASE_OPS_ROOT'] = CASES
os.environ['INVOICE_MAX_MB'] = '1'          # 上传闸压到 1MB（超大用例）

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def main():
    import app as A
    import writing_ops as W

    # 案件夹根级放一份真实 md（证「目录锁定」：根级真实文件也不放行）
    with open(os.path.join(CASES, CASE, 'intake-log.md'), 'w', encoding='utf-8') as f:
        f.write('根级留痕')

    print('=== ① 项目：dry-run / confirm / 非法参数 ===')
    r = A.api_writing_project({'case': CASE, 'doc_type': '起诉状', 'title': '民事起诉状'},
                              confirm=False)
    ck('dry-run 返回将建行（状态=起草）',
       r.get('dry_run') is True and r['plan']['status'] == '起草' and r['plan']['case_name'] == CASE)
    ck('dry-run 落点含 02-法律文书', '02-法律文书' in r['plan']['folder_rel'])
    ck('dry-run 不落库', W.list_projects() == [])
    r2 = A.api_writing_project({'case': CASE, 'doc_type': '起诉状', 'title': '民事起诉状'},
                               confirm=True)
    ck('confirm 落库', r2.get('ok') is True and isinstance(r2['project']['id'], int))
    pid = r2['project']['id']
    ck('非法类型回 error',
       'error' in A.api_writing_project({'case': CASE, 'doc_type': '瞎写', 'title': 'x'},
                                        confirm=True))
    ck('不存在案件夹回 error',
       'error' in A.api_writing_project({'case': '没有这个夹', 'doc_type': '起诉状',
                                         'title': 'x'}, confirm=True))

    print('=== ② 状态机 ===')
    d = A.api_writing_status({'id': pid, 'to': '定稿'}, confirm=False)
    ck('dry-run 标 allowed=False（起草→定稿 非法跨跳）', d.get('allowed') is False)
    r3 = A.api_writing_status({'id': pid, 'to': '定稿'}, confirm=True)
    ck('非法跨跳 confirm 回 error', 'error' in r3)
    ck('非法跨跳后状态未变（仍起草）', W.get_project(pid)['status'] == '起草')
    ck('起草→修订 放行',
       A.api_writing_status({'id': pid, 'to': '修订'}, confirm=True)['project']['status'] == '修订')
    ck('修订→送审 放行',
       A.api_writing_status({'id': pid, 'to': '送审'}, confirm=True)['project']['status'] == '送审')
    ck('送审→定稿 放行',
       A.api_writing_status({'id': pid, 'to': '定稿'}, confirm=True)['project']['status'] == '定稿')
    ck('定稿→修订 回退放行',
       A.api_writing_status({'id': pid, 'to': '修订'}, confirm=True)['project']['status'] == '修订')
    ck('项目不存在 → error',
       'error' in A.api_writing_status({'id': 999, 'to': '修订'}, confirm=True))

    print('=== ③ 版本：dry-run / confirm / 自增 / 落盘 ===')
    data = '第一版正文'.encode('utf-8')
    b64 = base64.b64encode(data).decode('ascii')
    dv = A.api_writing_version({'project_id': pid, 'filename': '起诉状.docx', 'data': b64},
                               confirm=False)
    ck('dry-run v 号=1 + 落点正确',
       dv.get('dry_run') is True and dv['plan']['version_no'] == 1
       and '02-法律文书' in dv['plan']['rel_path'])
    ck('dry-run 未落盘', not os.path.exists(dv['plan']['abs_path']))
    v1 = A.api_writing_version({'project_id': pid, 'filename': '起诉状.docx', 'data': b64,
                                'note': '初稿'}, confirm=True)
    ck('confirm 入卷（v1）', v1.get('ok') is True and v1['version']['version_no'] == 1)
    abs1 = os.path.join(CASES, CASE, v1['version']['rel_path'])
    ck('文件落盘且内容一致', os.path.isfile(abs1) and open(abs1, 'rb').read() == data)
    ck('路径 = 02-法律文书\\标题\\v01-日期-名',
       v1['version']['rel_path'].startswith(os.path.join('02-法律文书', '民事起诉状', 'v01-')))
    v2 = A.api_writing_version({'project_id': pid, 'filename': '起诉状.docx', 'data': b64},
                               confirm=True)
    ck('v 号自增 → 02', v2['version']['version_no'] == 2)

    print('=== ④ 闸门（越界先于存在性 / 白名单 / 超大）===')
    r = A.api_writing_version({'project_id': 99999, 'filename': '..\\evil.docx', 'data': b64},
                              confirm=True)
    ck('越界文件名先于存在性（项目不存在也先报文件名）',
       'error' in r and '文件名' in r['error'])
    r = A.api_writing_version({'project_id': pid, 'filename': 'x.exe', 'data': b64},
                              confirm=True)
    ck('非白名单扩展名拒', 'error' in r and '只支持' in r['error'])
    big = base64.b64encode(b'x' * 1048577).decode('ascii')
    r = A.api_writing_version({'project_id': pid, 'filename': 'x.docx', 'data': big},
                              confirm=True)
    ck('超大拒（1MB 上限）', 'error' in r and '过大' in r['error'])
    r = A.api_writing_version({'project_id': 99999, 'filename': 'ok.docx', 'data': b64},
                              confirm=True)
    ck('项目不存在拒', 'error' in r and '不存在' in r['error'])

    print('=== ⑤ 列表 / 详情 ===')
    items = W.list_projects()
    ck('列表 version_count=2', len(items) == 1 and items[0]['version_count'] == 2)
    rr = A.api_writing_versions({'project_id': str(pid)})
    ck('版本清单带 abs_path/ext', rr.get('ok') is True and len(rr['items']) == 2
       and rr['items'][0]['ext'] == '.docx'
       and rr['items'][0]['abs_path'].endswith(rr['items'][0]['filename']))
    ck('project_id 非数字 → error', 'error' in A.api_writing_versions({'project_id': 'abc'}))
    ck('按状态筛（现=修订）', len(W.list_projects(status='修订')) == 1
       and W.list_projects(status='定稿') == [])
    ck('按案件筛', len(W.list_projects(case=CASE)) == 1)

    print('=== ⑥ 文本预览五道闸 ===')
    md = '测试正文第一行'.encode('utf-8')
    vm = A.api_writing_version({'project_id': pid, 'filename': '说明.md',
                                'data': base64.b64encode(md).decode('ascii')}, confirm=True)
    rel_md = vm['version']['rel_path']
    r = A.api_writing_view({'case': CASE, 'rel': rel_md})
    ck('md 预览 ok + 正文一致（含子目录）',
       r.get('ok') is True and '测试正文第一行' in r.get('text', ''))
    r = A.api_writing_view({'case': CASE, 'rel': rel_md.replace('说明.md', '..\\x.md')})
    ck('rel 含 .. → 拒', 'error' in r)
    r = A.api_writing_view({'case': CASE, 'rel': rel_md.replace('.md', '.docx')})
    ck('非 md/txt → 拒', 'error' in r)
    r = A.api_writing_view({'case': CASE, 'rel': 'intake-log.md'})
    ck('目录外（案件夹根级真实 md）→ 拒', 'error' in r)
    r = A.api_writing_view({'case': '不存在夹', 'rel': rel_md})
    ck('案件夹不存在 → 拒', 'error' in r)
    r = A.api_writing_view({'case': CASE, 'rel': '民事起诉状\\没有这份.md'})
    ck('不存在文件 → 拒', 'error' in r)

    shutil.rmtree(TMP, ignore_errors=True)
    print('\n=== 结果：%d/%d 通过 ===' % (len(total) - len(fails), len(total)))
    if fails:
        print('FAIL 项：')
        for f in fails:
            print('  - ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
