# -*- coding: utf-8 -*-
"""test_api_tasks.py — 任务 API 端到端回归（批 12）

覆盖：写接口无 confirm 一律 dry-run、新增/改状态/改优先级/改截止、过滤、非法值 400、
      删除、**刷新后（重新起服务读同一库）数据仍在**。
只动工作台自建库 `data\\workbench.db`；测试用独立端口，跑完自动关停。
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
ROOT = os.path.dirname(HERE)                          # 项目根
PY = sys.executable
PORT = 8811
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = 'http://127.0.0.1:%d' % PORT

checks = []


def ck(n, c):
    checks.append((n, bool(c)))


def req(path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={'Content-Type': 'application/json'})
    try:
        with OP.open(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')


def alive():
    try:
        return req('/api/health')[0] == 200
    except Exception:                                        # noqa: BLE001
        return False


def start():
    env = dict(os.environ)
    env['_WB_STDOUT_UTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'          # 与 launch_workbench.pyw 一致（防 GBK 编码崩溃）
    logf = open(os.path.join(ROOT, 'app.out.log'), 'a', encoding='utf-8', buffering=1)
    subprocess.Popen([PY, '-u', os.path.join(ROOT, 'app.py'), '--port', str(PORT), '--idle-exit', '0'],
                     cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS,
                     close_fds=True)
    for _ in range(60):
        time.sleep(0.2)
        if alive():
            return True
    return False


def log_lines():
    p = os.path.join(ROOT, 'app.out.log')
    if not os.path.isfile(p):
        return []
    return io.open(p, encoding='utf-8', errors='replace').read().splitlines()


def main():
    lines_before = len(log_lines())
    created = []            # 本用例建过的任务 id → 无论成败都在 finally 清理，避免污染真库
    if not start():
        print('服务未就绪；日志尾部：')
        print('\n'.join(log_lines()[-20:]))
        return 3
    new_log = '\n'.join(log_lines()[lines_before:])          # 只看本次启动新增的行
    ck('启动过程无 UnicodeEncodeError（GBK 事故回归）', 'UnicodeEncodeError' not in new_log)
    ck('日志出现自检通过（未崩）', '自检通过' in new_log)
    try:
        # 无 confirm → dry-run，不落库
        s, d = req('/api/tasks', 'POST', {'title': '不该被写入'})
        ck('无 confirm 走 dry-run', s == 200 and d.get('dry_run') is True)
        before = req('/api/tasks')[1]['stats']['total']
        ck('dry-run 未落库', '不该被写入' not in json.dumps(
            req('/api/tasks')[1], ensure_ascii=False))

        # 新增
        s, d = req('/api/tasks', 'POST', {'title': '【API测试】阅卷', 'case_ref': 'A闽兴\\三明闽兴',
                                          'priority': '紧急', 'due_date': '2026-09-01',
                                          'note': '回归用例', 'confirm': True})
        tid = d['task']['id']
        created.append(tid)
        ck('新增成功', s == 200 and d['task']['title'] == '【API测试】阅卷')
        ck('新增后计数 +1', d['stats']['total'] == before + 1)
        ck('逾期统计生效', d['stats']['overdue'] >= 1)

        # 改状态/优先级/截止
        s, d = req('/api/tasks/update', 'POST', {'id': tid, 'confirm': True,
                                                 'patch': {'status': '进行中'}})
        ck('改状态=进行中', d['task']['status'] == '进行中')
        s, d = req('/api/tasks/update', 'POST', {'id': tid, 'confirm': True,
                                                 'patch': {'priority': '普通'}})
        ck('改优先级', d['task']['priority'] == '普通')
        s, d = req('/api/tasks/update', 'POST', {'id': tid, 'confirm': True,
                                                 'patch': {'due_date': '2026-12-31'}})
        ck('改截止时间', d['task']['due_date'] == '2026-12-31')
        s, d = req('/api/tasks/update', 'POST', {'id': tid, 'confirm': True,
                                                 'patch': {'status': '已完成'}})
        ck('完成写 done_at', bool(d['task']['done_at']))

        # 过滤
        s, d = req('/api/tasks?' + urllib.parse.urlencode({'status': '已完成'}))
        ck('按状态过滤', any(t['id'] == tid for t in d['tasks']))
        s, d = req('/api/tasks?' + urllib.parse.urlencode({'q': 'API测试'}))
        ck('关键词搜索', len(d['tasks']) >= 1)
        s, d = req('/api/tasks?' + urllib.parse.urlencode({'case': 'A闽兴\\三明闽兴'}))
        ck('按案件过滤', any(t['id'] == tid for t in d['tasks']))

        # 校验 → 400
        s, _ = req('/api/tasks', 'POST', {'title': '  ', 'confirm': True})
        ck('空标题 400', s == 400)
        s, _ = req('/api/tasks', 'POST', {'title': 'x', 'due_date': '2026/1/1', 'confirm': True})
        ck('非法日期 400', s == 400)
        s, _ = req('/api/tasks/update', 'POST', {'id': tid, 'confirm': True,
                                                 'patch': {'status': '瞎写'}})
        ck('非法状态 400', s == 400)

        # 批 14：期限 → 任务打通 + 任务「关联案件」取自台账行 → 客户归属按台账案件名精确匹配
        s, led = req('/api/ledger_rows')
        ledrows = [r for r in led.get('rows', [])
                   if str(r.get('案件') or '').strip() and str(r.get('委托人') or '').strip()]
        ck('台账行可用（供任务「关联案件」下拉取数）', s == 200 and len(ledrows) >= 1)
        sample = ledrows[0] if ledrows else {}
        case_name = str(sample.get('案件') or '').strip()
        client = str(sample.get('委托人') or '').strip()
        s, d = req('/api/tasks', 'POST', {'title': '【API测试】期限转任务',
                                          'case_ref': case_name, 'priority': '紧急',
                                          'due_date': '2026-09-13', 'note': '由期限页一键生成',
                                          'confirm': True})
        tid3 = d['task']['id']
        created.append(tid3)
        ck('期限转任务（关联案件=台账案件名）新增成功',
           s == 200 and d['task']['case_ref'] == case_name)
        cl0 = req('/api/overview')[1]['clients']
        one = [x for x in cl0['items'] if x['name'] == client]
        ck('客户聚合按台账案件名精确归到委托人',
           bool(one) and any(t['id'] == tid3 for t in one[0]['tasks']))
        req('/api/tasks/delete', 'POST', {'id': tid3, 'confirm': True})
        created.remove(tid3)

        # 批 22：律师函 → 任务（前端 letterTaskPayload 的载荷形状：不猜关联案件/到期日，备注带来源行）
        s, lt = req('/api/letters')
        litems = lt.get('items', [])
        ck('律师函表可取数（供一键建跟进任务）', s == 200 and len(litems) >= 1)
        lrows = [x for x in litems if not str(x.get('后续跟进') or '').strip()]
        if lrows:
            x0 = lrows[0]
            title = ('【律师函跟进】' + str(x0.get('收函对象') or '')[:40]
                     + '（' + str(x0.get('委托人') or '') + '）')
            note2 = ('来源：台账「律师函」表 r%s（序号 %s）｜欠款本金 %s｜台账该列不回写'
                     % (x0.get('row'), x0.get('序号'), x0.get('欠款金额（本金）')))
            s, d = req('/api/tasks', 'POST', {'title': title, 'case_ref': '', 'priority': '普通',
                                              'due_date': '', 'note': note2, 'confirm': True})
            tid4 = d['task']['id']
            created.append(tid4)
            ck('律师函转任务（不猜案件/日期）新增成功',
               s == 200 and d['task']['case_ref'] == '' and d['task']['due_date'] == ''
               and d['task']['note'] == note2 and d['task']['priority'] == '普通')
            req('/api/tasks/delete', 'POST', {'id': tid4, 'confirm': True})
            created.remove(tid4)
        else:
            ck('律师函转任务（当前无「后续跟进」为空的行 → 跳过）', True)

        # 首页统计联动
        o = req('/api/overview')[1]
        ck('overview 含任务统计', 'tasks' in o and 'today' in o['tasks'])
        ck('overview 含客户聚合', o.get('clients', {}).get('count', 0) > 0)
        ck('overview 含进行中案件', o.get('active_cases', {}).get('count', 0) > 0)

        # 删除
        s, d = req('/api/tasks/delete', 'POST', {'id': tid, 'confirm': True})
        ck('删除成功', s == 200 and d['ok'])
        ck('删除后回到原计数', d['stats']['total'] == before)

        # 持久化：重启服务后仍在
        s, d = req('/api/tasks', 'POST', {'title': '【API测试】持久化', 'confirm': True})
        tid2 = d['task']['id']
        created.append(tid2)
        req('/api/quit')
        time.sleep(1.5)
        ck('服务已关停', not alive())
        ck('重启服务', start())
        s, d = req('/api/tasks')
        ck('重启后数据仍在（真持久化）', any(t['id'] == tid2 for t in d['tasks']))
        req('/api/tasks/delete', 'POST', {'id': tid2, 'confirm': True})
        created.remove(tid2)
    finally:
        for _tid in list(created):                          # 兜底清理（断言中途失败也不留垃圾数据）
            try:
                req('/api/tasks/delete', 'POST', {'id': _tid, 'confirm': True})
            except Exception:                                # noqa: BLE001
                pass
        try:
            req('/api/quit')
        except Exception:                                    # noqa: BLE001
            pass

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print('[%s] %s' % ('OK' if ok else 'FAIL', n))
    print('任务 API 回归: %d/%d 通过' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
