# -*- coding: utf-8 -*-
r"""test_api_board.py — 看板泳道接口回归（批 50，2026-10-09）

覆盖：
  · 分桶顺序 = STATUS_POOL + 「其他/未填」收尾（空列也保留）
  · 默认排除「已结案」且 closed_count 单独计数；include_closed=True 并入泳道
  · 池外状态 / 空状态 → 「其他/未填」桶
  · 卡片字段齐全 + 当前进度截断 80 字 + 两个 sheet 的行都在
  · 空行（序号与案件都空）跳过；generated_at 生成

⚠️ 台账读取打桩（不碰真台账、不碰真案卷）；纯只读接口，无写操作断言。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def main():
    import app as A

    rows = {
        '海大案件': [
            {'_row': 4, '序号': 1, '案件': '甲VS乙买卖合同纠纷', '委托人': '甲公司',
             '案件类别': '诉讼', '案号': '（2026）闽0203民初1号',
             '当前进度': '已备齐材料，待立案', '最新状态': '待立案'},
            {'_row': 5, '序号': 2, '案件': '丙VS丁', '委托人': '丙',
             '案件类别': '诉讼', '案号': '', '当前进度': '', '最新状态': ''},
            {'_row': 6, '序号': 3, '案件': '庚VS辛', '委托人': '庚',
             '案件类别': '执行', '案号': '', '当前进度': '', '最新状态': '已结案'},
            {'_row': 7, '序号': None, '案件': None, '委托人': None,
             '案件类别': None, '案号': None, '当前进度': None, '最新状态': None},   # 空行
        ],
        '其他案件': [
            {'_row': 4, '序号': 4, '案件': '戊VS己', '委托人': '戊公司',
             '案件类别': '诉讼', '案号': '（2026）闽0206民初2号',
             '当前进度': '05.12：已立案，等排期', '最新状态': '已立案'},
            {'_row': 5, '序号': 5, '案件': '壬VS癸', '委托人': '壬',
             '案件类别': '仲裁', '案号': '', '当前进度': 'x' * 200, '最新状态': '待执行'},
        ],
    }
    orig = A.ledger_ops.read_rows
    try:
        A.ledger_ops.read_rows = lambda sheet, limit=None: [dict(r) for r in rows.get(sheet, [])]

        print('=== ① 默认（不含已结案）===')
        d = A.api_board()
        ck('返回 ok', d.get('ok') is True)
        sts = [l['status'] for l in d['lanes']]
        ck('泳道顺序 = STATUS_POOL + 其他/未填收尾',
           sts == A.ledger_ops.STATUS_POOL + ['其他/未填'])
        lane = {l['status']: l for l in d['lanes']}
        ck('已结案默认排除（lane 计 0）', lane['已结案']['count'] == 0)
        ck('closed_count=1 单独计数', d['closed_count'] == 1)
        ck('待立案桶=1（空行被跳过）', lane['待立案']['count'] == 1)
        ck('池外「待执行」→ 其他/未填桶', lane['其他/未填']['count'] == 2)
        ck('空状态行也在其他/未填', any(i['状态'] == '' for i in lane['其他/未填']['items']))
        it = lane['待立案']['items'][0]
        ck('卡片字段齐全',
           set(('案件', '委托人', '案件类别', '案号', '当前进度', '状态', 'sheet', '_row')) <= set(it))
        ck('卡片 sheet/_row 正确', it['sheet'] == '海大案件' and it['_row'] == 4)
        ck('两个 sheet 都读了（已立案在「其他案件」）',
           lane['已立案']['count'] == 1
           and lane['已立案']['items'][0]['sheet'] == '其他案件')
        ck('进度截断 ≤80 字', all(len(i['当前进度']) <= 80 for l in d['lanes'] for i in l['items']))
        ck('generated_at 有值', bool(d.get('generated_at')))

        print('=== ② include_closed=True ===')
        d2 = A.api_board(include_closed=True)
        lane2 = {l['status']: l for l in d2['lanes']}
        ck('已结案行并入泳道', lane2['已结案']['count'] == 1
           and lane2['已结案']['items'][0]['案件'] == '庚VS辛')
        ck('closed_count 仍单列', d2['closed_count'] == 1)
        ck('泳道数不变（11+1）', len(d2['lanes']) == len(A.ledger_ops.STATUS_POOL) + 1)

        print('=== ③ AI 分析（批 55，LLM 打桩，零网络零积分）===')
        import assistant_ops
        orig_reach = assistant_ops.llm_reachable
        orig_call = assistant_ops._llm_call
        try:
            assistant_ops.llm_reachable = lambda: (False, 'no pet')
            r = A.api_board_analyze({})
            ck('llm down → 优雅降级（不报错页面）', r.get('ok') is False and r.get('llm') == 'down')
            seen = {}
            assistant_ops.llm_reachable = lambda: (True, 'ok')
            def fake_call(messages, **kw):
                seen['p'] = messages[0]['content']
                return '分析：优先跟进待立案案件'
            assistant_ops._llm_call = fake_call
            r = A.api_board_analyze({})
            ck('分析透出 + 在办计数=4（不含已结案）',
               r.get('ok') is True and '优先跟进' in r.get('analysis', '')
               and r.get('open_count') == 4)
            ck('prompt 含状态名与案件名、排除已结案行',
               '待立案' in seen.get('p', '') and '甲VS乙' in seen['p']
               and '庚VS辛' not in seen['p'])
        finally:
            assistant_ops.llm_reachable = orig_reach
            assistant_ops._llm_call = orig_call
    finally:
        A.ledger_ops.read_rows = orig

    print('\n=== 结果：%d/%d 通过 ===' % (len(total) - len(fails), len(total)))
    if fails:
        print('FAIL 项：')
        for f in fails:
            print('  - ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
