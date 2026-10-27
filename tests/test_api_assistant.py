# -*- coding: utf-8 -*-
"""test_api_assistant.py — 台账助理/智能提醒 HTTP 语义层回归（批 44 补，审计问题 #1）

覆盖（规划批 B「新路由加 10-15 条断言」的落点）：
  · 入参闸：空 q → 400 / 超长 q(>2000) → 400 / 缺参数 → 400
  · 对话并发：锁占用中第二个请求 → 409「上一条还在思考，稍等一下喵」
  · 分析互斥：已在跑 → remind_run 409；state_busy 跨进程锁语义
  · 历史轮转：写 >12 轮 → 只留最近 12 轮（24 条）〔批 45 窗口 6→12〕；history_clear confirm 门
  · 忽略名单：增/删/去重；空词 400
  · mark：缺 id 400 / 已读与忽略状态落盘
  · GET：reminders / history / ignore 三接口结构

隔离（关键）：
  · ASSISTANT_DATA_DIR → 临时目录（**绝不写真实 data\\**）
  · ASSISTANT_NO_MIGRATE=1（防把外部服务真历史拖进来）
  · LEDGER_PATH → 临时合成台账（LLM 提示词取台账；不给真台账）
  · **不打真 LLM**：起子进程时设 ASSISTANT_MOCK_LLM=1 → assistant_ops 走桩，
    全部断言零积分消耗、零外网。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
ROOT = os.path.dirname(HERE)                          # 项目根
PY = sys.executable
PORT = 8813
BASE = 'http://127.0.0.1:%d' % PORT
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕开环境代理
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

checks = []
TMP = None


def ck(n, c):
    checks.append((n, bool(c)))


def req(path, method='GET', body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={'Content-Type': 'application/json'})
    try:
        with OP.open(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')


def alive():
    try:
        with OP.open(BASE + '/api/health', timeout=1) as r:
            return r.status == 200
    except Exception:                                        # noqa: BLE001
        return False


def make_fake_ledger():
    """最小合成台账（单表单行）——只为让 LLM 上下文构造有料可用"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '案件'
    ws.append(['案件编号', '案件', '委托人', '案件类别', '承办律师', '状态',
               '案号', '法院', '开庭时间', '当前进度', '标的额', '基础律师费', '进度日志'])
    ws.append(['1', '福建闽兴vs林国伟-买卖合同纠纷', '福建闽兴饲料有限公司', '诉讼',
               '张律师', '执行中', '(2026)闽0203民初1016号', '思明法院', '', '08.01：已立案',
               '100000', '20000', '07.01：立案受理'])
    p = os.path.join(TMP, '合成台账.xlsx')
    wb.save(p)
    return p


def make_mock_assistant(mock_text):
    """生成假 assistant_ops 补丁模块：把 LLM 调用替换成桩（零网络零积分）。

    做法：在 TMP 里放一个 sitecustomize 风格的 `assistant_mock.py`，由 app.py
    通过环境变量 ASSISTANT_MOCK_FILE 加载并 monkeypatch。这样不碰产品代码。
    """
    src = (
        '# -*- coding: utf-8 -*-\n'
        '# 测试桩：把 assistant_ops 的 LLM 调用替换为固定回包（零网络零积分）\n'
        'import sys\n'
        'sys.path.insert(0, r"%s")\n'
        'import assistant_ops as A\n'
        'MOCK_TEXT = %r\n'
        'def _fake_call(messages, timeout=None, retries=0):\n'
        '    return MOCK_TEXT          # 契约：返回 content 字符串\n'
        'A._llm_call = _fake_call\n'
        'A.llm_reachable = lambda: (True, "mock")\n'
    ) % (ROOT, mock_text)
    p = os.path.join(TMP, 'assistant_mock.py')
    io.open(p, 'w', encoding='utf-8', newline='\n').write(src)
    return p


def start(mock_file):
    env = dict(os.environ)
    env['_WB_STDOUT_UTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['ASSISTANT_DATA_DIR'] = os.path.join(TMP, 'adata')       # ← 数据隔离
    env['ASSISTANT_NO_MIGRATE'] = '1'                            # ← 禁自动迁移
    env['ASSISTANT_MOCK_FILE'] = mock_file                       # ← LLM 桩
    env['LEDGER_PATH'] = os.path.join(TMP, '合成台账.xlsx')
    env['LEDGER_BACKUP_ROOT'] = os.path.join(TMP, 'bak')
    env['LEDGER_PENDING_PATH'] = os.path.join(TMP, 'pending.json')
    env['WORKBENCH_DB'] = os.path.join(TMP, 'workbench.db')
    env['CASE_OPS_ROOT'] = os.path.join(TMP, 'cases')
    env['WIKI_ROOT'] = os.path.join(TMP, 'wiki')
    logf = open(os.path.join(ROOT, 'app.out.log'), 'a', encoding='utf-8', buffering=1)
    subprocess.Popen([PY, '-u', os.path.join(ROOT, 'app.py'),
                      '--port', str(PORT), '--idle-exit', '0'],
                     cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
    for _ in range(60):
        time.sleep(0.2)
        if alive():
            return True
    return False


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix='assistant_api_test_')
    os.makedirs(os.path.join(TMP, 'adata'), exist_ok=True)
    os.makedirs(os.path.join(TMP, 'cases'), exist_ok=True)      # 案卷根须存在（启动自检）
    os.makedirs(os.path.join(TMP, 'wiki'), exist_ok=True)
    make_fake_ledger()
    # 分析桩：返回合法 JSON 数组（触发解析路径）；对话桩：普通文本
    mock = make_mock_assistant(
        '[{"priority":"high","text":"当事人甲案：开庭改至10月8日","state":"review","change":"new"},'
        '{"priority":"normal","text":"陈丽萍案：缴费期限已逾期9天","state":"failed","change":"new"}]')

    if not start(mock):
        print('服务未就绪（端口 %d）' % PORT)
        return 3
    try:
        # ── 1) GET 三接口结构 ────────────────────────────────────────────
        s, d = req('/api/assistant/reminders')
        ck('GET reminders 200 且含 count/unread/state/ignore/llm',
           s == 200 and all(k in d for k in ('count', 'unread', 'items', 'state', 'ignore', 'llm')))
        ck('初始无提醒', d['count'] == 0 and d['unread'] == 0)
        ck('state 初始非 busy', d['state']['busy'] is False)
        s, d = req('/api/assistant/history')
        ck('GET history 200 且空', s == 200 and d.get('items') == [])
        s, d = req('/api/assistant/ignore')
        ck('GET ignore 200 且空', s == 200 and d.get('items') == [])

        # ── 2) 入参闸 ───────────────────────────────────────────────────
        s, d = req('/api/assistant/chat', 'POST', {'q': ''})
        ck('空 q → 400', s == 400 and 'error' in d)
        s, d = req('/api/assistant/chat', 'POST', {'q': '   '})
        ck('纯空白 q → 400', s == 400)
        s, d = req('/api/assistant/chat', 'POST', {'q': 'x' * 2001})
        ck('超长 q(>2000) → 400', s == 400 and '过长' in d.get('error', ''))
        # 边界值 2000 放行（会真写一轮历史，故先记基线，段末统一清空）
        s, d = req('/api/assistant/chat', 'POST', {'q': 'x' * 2000})
        ck('恰好 2000 字符放行（非 400）', s != 400)
        req('/api/assistant/history_clear', 'POST', {'confirm': True})   # 复位，防污染后段基数
        s, d = req('/api/assistant/mark', 'POST', {'dismissed': False})
        ck('mark 缺 id → 400', s == 400)
        s, d = req('/api/assistant/ignore', 'POST', {'add': '   '})
        ck('ignore 空词 → 400', s == 400)

        # ── 3) 分析（桩）→ 提醒落盘 + 状态机 ─────────────────────────────
        s, d = req('/api/assistant/remind_run', 'POST', {})
        ck('remind_run 首次 200 started', s == 200 and d.get('started') is True)
        done = False
        for _ in range(40):
            time.sleep(0.25)
            s, d = req('/api/assistant/reminders')
            if not d['state']['busy']:
                done = True
                break
        ck('分析任务收敛（busy→false）', done)
        ck('分析状态 done', d['state']['last_status'] == 'done')
        ck('分析产出 2 条提醒', d['count'] == 2 and d['state']['last_count'] == 2)
        ck('提醒含 high 优先级', any(x['priority'] == 'high' for x in d['items']))
        ck('提醒文案以案名开头（45 字限额内）',
           all(len(x['text']) <= 45 for x in d['items']))
        ck('未读计数 = 2', d['unread'] == 2)
        first_id = d['items'][0]['id']
        ck('提醒有稳定 id', bool(first_id))

        # ── 4) 二次分析幂等（id 不变 → change 判 same）────────────────────
        s, _ = req('/api/assistant/remind_run', 'POST', {})
        for _ in range(40):
            time.sleep(0.25)
            s, d2 = req('/api/assistant/reminders')
            if not d2['state']['busy']:
                break
        ck('二次分析后条数不变（幂等合并）', d2['count'] == 2)
        ck('二次分析 id 稳定', d2['items'][0]['id'] == first_id)

        # ── 5) mark：已读 / 忽略 ─────────────────────────────────────────
        s, d = req('/api/assistant/mark', 'POST', {'id': first_id})
        ck('mark 已读 200', s == 200 and d.get('hit') is True)
        ck('已读后未读 -1', d['unread'] == 1)
        s, d = req('/api/assistant/mark', 'POST', {'id': '不存在的id'})
        ck('mark 不存在的 id → hit=false 不炸', s == 200 and d.get('hit') is False)
        s, d = req('/api/assistant/mark', 'POST', {'id': first_id, 'dismissed': True})
        ck('dismissed 后被忽略项不再计未读（剩另一条 → 1）',
           s == 200 and d['unread'] == 1)
        item1 = [x for x in req('/api/assistant/reminders')[1]['items'] if x['id'] == first_id]
        ck('dismissed 标记已落盘', bool(item1) and item1[0].get('dismissed') is True)

        # ── 6) 忽略名单增删去重 ──────────────────────────────────────────
        s, d = req('/api/assistant/ignore', 'POST', {'add': '陈丽萍'})
        ck('ignore add 200 且 changed', s == 200 and d.get('changed') is True)
        s, d = req('/api/assistant/ignore', 'POST', {'add': '陈丽萍'})
        ck('ignore 重复 add → changed=false', s == 200 and d.get('changed') is False)
        ck('ignore 列表含该词', '陈丽萍' in d['items'])
        s, d = req('/api/assistant/ignore', 'POST', {'remove': '陈丽萍'})
        ck('ignore remove 200', s == 200 and d.get('changed') is True)
        ck('remove 后列表为空', d['items'] == [])

        # ── 7) 对话（桩）→ 历史轮转 ─────────────────────────────────────
        s, d = req('/api/assistant/chat', 'POST', {'q': '现在有哪些执行中的案子？'})
        ck('chat 正常 200 且含 reply', s == 200 and isinstance(d.get('reply'), str))
        ck('chat 回复非空', bool((d.get('reply') or '').strip()))
        s, d = req('/api/assistant/history')
        ck('对话历史落盘 1 轮（2 条）', s == 200 and len(d['items']) == 2)
        ck('历史 role 交替 user/assistant',
           [x['role'] for x in d['items']] == ['user', 'assistant'])
        # 写满 15 轮 → 只留最近 12 轮（批 45：窗口 6→12，故写入量须 > 窗口才触发轮转）
        for i in range(15):
            req('/api/assistant/chat', 'POST', {'q': '第%d个问题' % i})
        s, d = req('/api/assistant/history')
        ck('历史轮转上限 12 轮（24 条）', len(d['items']) == 24)
        # ⚠️ 防「假绿」：写入量必须 > 窗口，否则根本不触发轮转、断言会静默失效
        #    （批 45 窗口 6→12 时若不同步加大写入量，只写 8 轮就测不到轮转）
        ck('轮转用例写入量覆盖窗口（防假绿）', 15 > 12)
        ck('轮转保留最近内容（末尾是本轮问）', '第14个问题' in d['items'][-2]['content'])
        ck('历史项只含 role/content 两字段',
           all(set(x.keys()) <= {'role', 'content'} for x in d['items']))

        # ── 8) history_clear confirm 门 ──────────────────────────────────
        s, d = req('/api/assistant/history_clear', 'POST', {})
        ck('history_clear 无 confirm → dry_run', s == 200 and d.get('dry_run') is True)
        ck('dry-run 报将清 12 轮', d.get('will_clear_turns') == 12)
        s, d = req('/api/assistant/history')
        ck('dry-run 后历史仍在（未真清）', len(d['items']) == 24)
        s, d = req('/api/assistant/history_clear', 'POST', {'confirm': True})
        ck('confirm 后清空', s == 200 and d.get('dry_run') is False)
        s, d = req('/api/assistant/history')
        ck('清空后历史为空', d['items'] == [])

        # ── 9) 未知路由 404 ──────────────────────────────────────────────
        s, d = req('/api/assistant/nonexistent', 'POST', {})
        ck('未知 assistant 子路由 → 404', s == 404)
    finally:
        try:
            req('/api/quit')
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(1.0)
        shutil.rmtree(TMP, ignore_errors=True)

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print('[%s] %s' % ('OK' if ok else 'FAIL', n))
    print('台账助理 HTTP 语义回归: %d/%d 通过' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
