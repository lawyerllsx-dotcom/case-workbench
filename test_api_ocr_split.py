# -*- coding: utf-8 -*-
r"""test_api_ocr_split.py — 大 PDF 分段识别接口回归（批 65，2026-10-14）

覆盖：
  · ocr_ops.split_plan：页数探测 / 段规划（30 页每段）/ 续跑起点 / 缓存判定
  · api_ocr_split_plan / api_ocr_split_run（API 层）
  · 分段推进：自动起点 → 逐段 → done（缓存落位 + suggest/head/path 字段 + 合并页数）
  · 段失败重试（断点续跑不重来）
  · force 重跑（清现场从段 1 重新开始）
  · api_ocr_plan 增强：pages / split 建议字段
  · 安全闸：越界文件拒绝

⚠️ 全隔离：fake OCR 脚本（按 --pages 写页标注、幂等追加、模拟 checkpoint 删除时机）；
   分段现场 OCR_SPLIT_DIR、内容缓存 OCR_CONTENT_CACHE_DIR 全指临时目录；
   案件根打桩；**不碰真 OCR 栈、真案卷**。
"""
import os
import re
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='wb_ocrsplit_test_')

# fake OCR 脚本：模拟 ocr_contract.py 的 --pages 段独立产物（`{stem}_P{a}-{b}`）+ 段级 checkpoint
FAKE = os.path.join(TMP, 'fake_ocr.py')
with open(FAKE, 'w', encoding='utf-8', newline='\n') as f:
    f.write(
        'import json, os, pathlib, re, sys\n'
        'inp, outdir = sys.argv[1], sys.argv[2]\n'
        'os.makedirs(outdir, exist_ok=True)\n'
        'stem = pathlib.Path(inp).stem\n'
        'pages = None\n'
        'for i, a in enumerate(sys.argv):\n'
        '    if a == "--pages":\n'
        '        s, e = sys.argv[i + 1].split("-"); pages = (int(s), int(e))\n'
        'if pages is None:\n'
        '    pathlib.Path(outdir, stem + ".md").write_text(\n'
        '        os.environ.get("FAKE_OCR_TEXT", "text"), encoding="utf-8")\n'
        '    sys.exit(0)\n'
        'failf = pathlib.Path(outdir, ".fake_failed_once")\n'
        'if os.environ.get("FAKE_OCR_FAIL_ONCE") and not failf.exists():\n'
        '    failf.write_text("1"); sys.exit(3)\n'
        'seg = "%s_P%d-%d" % (stem, pages[0], pages[1])\n'
        'md = pathlib.Path(outdir, seg + ".md")\n'
        'done = set()\n'
        'if md.exists():\n'
        '    done = {int(m.group(1)) for m in re.finditer(r"^## Page (\\d+)", md.read_text(encoding="utf-8"), re.M)}\n'
        'with open(md, "a", encoding="utf-8") as fh:\n'
        '    if not done:\n'
        '        fh.write("# " + stem + "\\n\\n")\n'
        '    for pg in range(pages[0], pages[1] + 1):\n'
        '        if pg not in done:\n'
        '            fh.write("## Page %d\\n\\n%s p%d\\n\\n---\\n\\n" % (pg, os.environ.get("FAKE_OCR_TEXT", "text"), pg))\n'
        'ck = pathlib.Path(outdir, seg + ".checkpoint.json")\n'
        'if os.environ.get("FAKE_OCR_LEAVE_CKPT"):\n'
        '    ck.write_text(json.dumps({"completed_pages": sorted(done | set(range(pages[0], pages[1] + 1)))}), encoding="utf-8")\n'
        'else:\n'
        '    ck.unlink(missing_ok=True)\n'
        'sys.exit(0)\n')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def main():
    os.environ['OCR_PYTHON'] = sys.executable
    os.environ['OCR_SCRIPT'] = FAKE
    os.environ['OCR_SPLIT_DIR'] = os.path.join(TMP, 'split')
    os.environ['OCR_CONTENT_CACHE_DIR'] = os.path.join(TMP, 'ccache')
    os.environ['FAKE_OCR_TEXT'] = '本院受理通知书 请于开庭传票载明日期到庭'

    import pymupdf as fitz
    import app as A

    origin_root = A.case_ops.case_root
    tmp_root = os.path.join(TMP, 'cases')
    os.makedirs(tmp_root)
    A.case_ops.case_root = lambda: tmp_root
    case = '民-A-00001_甲VS乙_买卖合同纠纷'
    cp = os.path.join(tmp_root, case)
    os.makedirs(os.path.join(cp, '00-收件箱'))

    # 合成 65 页 PDF（只用于页数探测；fake 脚本不看内容）
    big = os.path.join(cp, '00-收件箱', '大卷宗.pdf')
    d = fitz.open()
    for _ in range(65):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), 'x', fontsize=12)
    d.save(big)
    d.close()

    try:
        # [1] 分段计划（API 层）
        pl = A.api_ocr_split_plan(case, '大卷宗.pdf')
        ck('分段计划：65 页 → 3 段（30/30/5）、起点段 1',
           pl['ok'] and pl['pages'] == 65 and pl['segs_total'] == 3
           and pl['seg_pages'] == 30 and pl['starting_seg'] == 1 and pl['done_pages'] == 0)

        # [2] 段 1（自动起点）
        r1 = A.api_ocr_split_run(case, '大卷宗.pdf')
        ck('段1：自动起点 → 30/65、seg_range 1-30',
           r1['ok'] and (not r1['done']) and r1['done_pages'] == 30
           and r1['seg'] == 1 and r1['seg_range'] == [1, 30])

        # [3] 续跑起点 + 段 2、3 → done
        pl2 = A.api_ocr_split_plan(case, '大卷宗.pdf')
        ck('进度可查：起点自动到段 2', pl2['starting_seg'] == 2 and pl2['done_pages'] == 30)
        r2 = A.api_ocr_split_run(case, '大卷宗.pdf', seg=2)
        ck('段2 → 60/65', r2['ok'] and r2['done_pages'] == 60)
        r3 = A.api_ocr_split_run(case, '大卷宗.pdf', seg=3)
        ck('段3 → done + 缓存落位 + suggest/head/path 字段齐',
           r3['ok'] and r3['done'] and r3['chars'] > 0
           and (r3.get('suggest') or {}).get('dir') == '04-法院文件'
           and r3.get('head') is not None and r3.get('path'))
        txt = A.ocr_ops.read_text(cp, '大卷宗.pdf') or ''
        ck('合并文本：65 页各一次无重复',
           len(re.findall(r'^## Page \d+', txt, re.M)) == 65 and '## Page 65' in txt)

        # [4] done 后重调 → 读缓存
        r4 = A.api_ocr_split_run(case, '大卷宗.pdf')
        ck('完成后重调 → 读缓存 done（不重跑）', r4['ok'] and r4['done'] and r4.get('cached'))

        # [5] 段失败重试（断点续跑不重来）
        os.remove(A.ocr_ops.cache_path(cp, '大卷宗.pdf'))
        shutil.rmtree(A.ocr_ops._split_dir(big), ignore_errors=True)
        os.environ['FAKE_OCR_FAIL_ONCE'] = '1'
        rbad = A.api_ocr_split_run(case, '大卷宗.pdf')
        ck('段失败 → ok=False 带错误信息', (not rbad['ok']) and bool(rbad.get('error')))
        rretry = A.api_ocr_split_run(case, '大卷宗.pdf')
        ck('重试同段 → 断点续跑成功推进', rretry['ok'] and rretry['done_pages'] >= 30)
        os.environ.pop('FAKE_OCR_FAIL_ONCE', None)

        # [6] force 重跑（清现场从段 1 重新开始）
        r6 = A.api_ocr_split_run(case, '大卷宗.pdf', force=True)
        ck('force → 清现场重来（30/65、段 1）',
           r6['ok'] and (not r6['done']) and r6['done_pages'] == 30 and r6['seg'] == 1)

        # [7] 安全闸：越界
        out_side = os.path.join(TMP, '外面.pdf')
        with open(out_side, 'wb') as fh:
            fh.write(b'x')
        bad = A.api_ocr_split_plan(case, out_side)
        ck('越界文件拒绝（ok=False）', bad['ok'] is False and bool(bad.get('error')))

        # [8] 单次 plan 增强字段
        p8 = A.api_ocr_plan(case, '大卷宗.pdf')
        ck('单次 plan 带 pages=65 + split=True', p8.get('pages') == 65 and p8.get('split') is True)
        small = os.path.join(cp, '00-收件箱', '小件.pdf')
        d2 = fitz.open()
        for _ in range(5):
            d2.new_page(width=595, height=842)
        d2.save(small)
        d2.close()
        p9 = A.api_ocr_plan(case, '小件.pdf')
        ck('小文件 split=False（不走分段）', p9.get('pages') == 5 and p9.get('split') is False)

        # [9] 批 66：识别全文读取 + 修字保存（.bak + suggest 按修正文本刷新）
        A.api_ocr_split_run(case, '大卷宗.pdf', seg=2)      # [6] force 后只跑了段 1 → 先补完
        A.api_ocr_split_run(case, '大卷宗.pdf', seg=3)
        t9 = A.api_ocr_text(case, '大卷宗.pdf')
        ck('读全文：与缓存一致（chars==len(text)）',
           t9['ok'] and t9['chars'] > 0 and len(t9['text']) == t9['chars'])
        never = os.path.join(cp, '00-收件箱', '没识别过.pdf')
        with open(never, 'wb') as fh:
            fh.write(b'%PDF-1.4 fake')
        ck('读全文：未识别过 → 拒绝', A.api_ocr_text(case, '没识别过.pdf')['ok'] is False)
        FIXED = '增值税发票 诉讼费缴费凭证'
        st9 = A.api_ocr_save_text(case, '大卷宗.pdf', FIXED)
        ck('修字：保存成功 + suggest 按修正文本刷新（→05-费用票据）',
           st9['ok'] and (st9.get('suggest') or {}).get('dir') == '05-费用票据')
        ck('修字：旧版 .bak 留存', os.path.isfile(A.ocr_ops.cache_path(cp, '大卷宗.pdf') + '.bak'))
        ck('修字：读回一致', (A.ocr_ops.read_text(cp, '大卷宗.pdf') or '') == FIXED)
        ck('修字：越界拒绝', A.api_ocr_save_text(case, out_side, 'x')['ok'] is False)
        ck('修字：空文本拒绝', A.api_ocr_save_text(case, '大卷宗.pdf', '  ')['ok'] is False)
    finally:
        A.case_ops.case_root = origin_root

    for k in ('OCR_PYTHON', 'OCR_SCRIPT', 'OCR_SPLIT_DIR', 'OCR_CONTENT_CACHE_DIR',
              'FAKE_OCR_TEXT', 'FAKE_OCR_TOTAL_PAGES', 'FAKE_OCR_FAIL_ONCE'):
        os.environ.pop(k, None)
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    print('PASS %d / %d' % (len(total) - len(fails), len(total)))
    if fails:
        print('FAIL: ' + '; '.join(fails))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
