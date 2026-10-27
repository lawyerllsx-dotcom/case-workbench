# -*- coding: utf-8 -*-
r"""test_api_prefill.py — LLM 抽取预填接口回归（批 53，2026-10-09）

覆盖：
  · 字段映射（7 键固定 + 多余键丢弃 + 空值原样）
  · 案号格式硬闸（无（20XX）字号 → 置空）
  · LLM 坏 JSON → 降级 ok:false + 提示；llm 不可达 → llm:'down'
  · 路径闸（.. / 案件夹不存在 / 非白名单扩展名 / 缺 name）
  · 上传闸（非白名单 / base64 非法 / 超大）与上传正常路径
  · 超长截断（>8000 → truncated + 进 prompt 的文本 ≤8000）
  · 日志：只记文件+字符数+耗时，**材料正文与字段值不入日志**
  · 空材料 → 明确报错（不硬抽）

⚠️ 全程 `PREFILL_MOCK_FILE` 走桩（零 OCR 零网络零积分）+ `CASE_OPS_ROOT` 临时案件根；
   LLM 探活/调用在需要时 monkeypatch（本机 8788 是否在跑都不影响测试）。
"""
import base64
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
ROOT = os.path.dirname(HERE)                          # 项目根
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix='wb_prefill_test_')
CASES = os.path.join(TMP, 'cases')
CASE = '测试案夹'
os.makedirs(os.path.join(CASES, CASE, '00-收件箱'))
with open(os.path.join(CASES, CASE, '00-收件箱', '材料.pdf'), 'wb') as f:
    f.write(b'%PDF-1.4')
os.environ['CASE_OPS_ROOT'] = CASES
os.environ['INVOICE_MAX_MB'] = '1'          # 上传大小闸压到 1MB

MARK = 'PREFILL_MARKER_ABC_不可入日志'
OCR_TEXT = '民事起诉状：原告甲公司，被告乙公司，买卖合同纠纷，厦门市思明区人民法院。' + MARK
GOOD_JSON = json.dumps({'委托人': '甲公司', '对方': '乙公司', '案由': '买卖合同纠纷',
                        '法院': '厦门市思明区人民法院', '案号': '（2026）闽0203民初1234号',
                        '标的额': '1250000元', '立案日期': '2026-09-01', '多余键': 'x'},
                       ensure_ascii=False)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def _mock_file(name, obj):
    p = os.path.join(TMP, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.environ['PREFILL_MOCK_FILE'] = p
    return p


def main():
    import app as A

    logs = []
    orig_log = A.log
    orig_reach = A.assistant_ops.llm_reachable
    orig_call = A.assistant_ops._llm_call
    A.log = lambda a, d='': logs.append((a, d))
    try:
        print('=== ① 正常路径（case + mock：字段映射）===')
        _mock_file('m1.json', {'ocr_text': OCR_TEXT, 'llm_content': GOOD_JSON})
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('ok=True', r.get('ok') is True)
        f = r.get('fields') or {}
        ck('7 键固定 + 值映射正确',
           set(f) == set(A.PREFILL_KEYS) and f['委托人'] == '甲公司' and f['对方'] == '乙公司'
           and f['案由'] == '买卖合同纠纷' and f['法院'] == '厦门市思明区人民法院'
           and f['标的额'] == '1250000元' and f['立案日期'] == '2026-09-01')
        ck('多余键被丢弃', '多余键' not in f)
        ck('案号含（20XX）字号 → 保留', f['案号'] == '（2026）闽0203民初1234号')
        ck('ocr_chars=材料字符数', r['ocr_chars'] == len(OCR_TEXT))
        ck('truncated=False', r['truncated'] is False)

        print('=== ② 案号格式硬闸 ===')
        bad_cno = json.dumps({'案号': '2026闽0203民初1234号'}, ensure_ascii=False)
        _mock_file('m2.json', {'ocr_text': OCR_TEXT, 'llm_content': bad_cno})
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('无括弧年份 → 案号置空（宁可空不做可疑预填）',
           r.get('ok') is True and r['fields']['案号'] == '')

        print('=== ③ 坏 JSON / 空字段对象 ===')
        _mock_file('m3.json', {'ocr_text': OCR_TEXT, 'llm_content': '这不是 JSON'})
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('坏 JSON → ok:false + 提示重试', r.get('ok') is False and 'JSON' in r.get('error', ''))
        _mock_file('m4.json', {'ocr_text': OCR_TEXT, 'llm_content': '{}'})
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('空对象 → ok + 全空字段（不报错）',
           r.get('ok') is True and all(v == '' for v in r['fields'].values()))

        print('=== ④ llm 不可达 ===')
        _mock_file('m5.json', {'ocr_text': OCR_TEXT})
        A.assistant_ops.llm_reachable = lambda: (False, 'mocked down')
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck("不可达 → llm:'down' + 文案含 8788",
           r.get('ok') is False and r.get('llm') == 'down' and '8788' in r.get('msg', ''))
        A.assistant_ops.llm_reachable = orig_reach

        print('=== ⑤ 路径闸（越界先于存在性）===')
        _mock_file('m6.json', {'ocr_text': OCR_TEXT, 'llm_content': GOOD_JSON})
        r = A.api_newcase_prefill({'case': '..', 'name': '材料.pdf'})
        ck('.. → 拒', r.get('ok') is False and 'error' in r)
        r = A.api_newcase_prefill({'case': '不存在夹', 'name': '材料.pdf'})
        ck('案件夹不存在 → 拒', r.get('ok') is False)
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.docx'})
        ck('非白名单扩展名 → 拒且提示只支持', r.get('ok') is False and '只支持' in r.get('error', ''))
        r = A.api_newcase_prefill({'case': CASE, 'name': ''})
        ck('缺 name → 拒', r.get('ok') is False)

        print('=== ⑥ 上传闸 ===')
        b64ok = base64.b64encode(b'%PDF-1.4 tiny').decode('ascii')
        r = A.api_newcase_prefill({'filename': 'x.exe', 'b64': b64ok})
        ck('上传非白名单 → 拒', r.get('ok') is False and '只支持' in r.get('error', ''))
        r = A.api_newcase_prefill({'filename': 'x.pdf', 'b64': '!!!not-base64!!!'})
        ck('base64 非法 → 拒', r.get('ok') is False and 'base64' in r.get('error', ''))
        big = base64.b64encode(b'x' * 1048577).decode('ascii')
        r = A.api_newcase_prefill({'filename': 'x.pdf', 'b64': big})
        ck('超大 → 拒（1MB 上限）', r.get('ok') is False and '过大' in r.get('error', ''))

        print('=== ⑦ 上传正常路径（mock）===')
        _mock_file('m7.json', {'ocr_text': OCR_TEXT, 'llm_content': GOOD_JSON})
        r = A.api_newcase_prefill({'filename': '材料扫描.pdf', 'b64': b64ok})
        ck('上传 + 抽取 ok', r.get('ok') is True and r['fields']['委托人'] == '甲公司')

        print('=== ⑦b 暂存即用即删（批 54：案卷材料最小留存）===')
        _mock_file('m7b.json', {'llm_content': GOOD_JSON})   # 不含 ocr_text → 走真实暂存分支
        orig_rf = A.ocr_ops.run_file
        A.ocr_ops.run_file = lambda p: {'ok': True, 'text': OCR_TEXT}
        try:
            up_dir = os.path.join(A.HERE, 'data', 'prefill_upload')
            before = set(os.listdir(up_dir)) if os.path.isdir(up_dir) else set()
            r = A.api_newcase_prefill({'filename': '材料扫描.pdf', 'b64': b64ok})
            after = set(os.listdir(up_dir)) if os.path.isdir(up_dir) else set()
            ck('上传抽取 ok（真实暂存分支）', r.get('ok') is True)
            ck('暂存文件即用即删（目录无新增残留）', after == before)
        finally:
            A.ocr_ops.run_file = orig_rf

        print('=== ⑧ 超长截断（进 prompt 的文本 ≤8000）===')
        long_text = 'A' * 8500 + 'TAILMARKER_不该出现在prompt'
        _mock_file('m8.json', {'ocr_text': long_text})
        seen = {}
        A.assistant_ops.llm_reachable = lambda: (True, 'ok')

        def fake_call(messages, timeout=None, retries=None):
            seen['content'] = messages[0]['content']
            return GOOD_JSON
        A.assistant_ops._llm_call = fake_call
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('truncated=True + ocr_chars 完整计数',
           r.get('ok') is True and r['truncated'] is True
           and r['ocr_chars'] == len(long_text))
        content = seen.get('content', '')
        ck('prompt 内文本被截到 8000（尾部标记不出现）',
           'TAILMARKER' not in content and len(content) < 8000 + 600)
        A.assistant_ops.llm_reachable = orig_reach
        A.assistant_ops._llm_call = orig_call

        print('=== ⑨ 空材料 + 日志纪律 ===')
        _mock_file('m9.json', {'ocr_text': '   ', 'llm_content': GOOD_JSON})
        r = A.api_newcase_prefill({'case': CASE, 'name': '材料.pdf'})
        ck('空材料 → 明确报错不硬抽', r.get('ok') is False and '文字' in r.get('error', ''))
        pref = [d for a, d in logs if a == 'PREFILL']
        ck('有 PREFILL 日志（文件+字符数+耗时）', len(pref) >= 1)
        ck('日志不含材料正文标记', all(MARK not in d for _a, d in logs))
        ck('日志不含字段值', all('甲公司' not in d for _a, d in logs))
    finally:
        A.log = orig_log
        A.assistant_ops.llm_reachable = orig_reach
        A.assistant_ops._llm_call = orig_call
        os.environ.pop('PREFILL_MOCK_FILE', None)
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
