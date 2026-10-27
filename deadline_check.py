#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
deadline_check.py — 期限/日程提醒推送（法律工作台 · 阶段 4；批 D 起双通道）

链路（2026-09-18 批 D 起：**toast 为主 + 桌宠气泡为备用**，拍板 D1）：
  1) 先刷新期限表：读台账（只读）→ `deadline_ops.extract_from_ledger` + merge → 写 `data\deadlines.json`
  2) 取临期/逾期项 + 手工日程 → 组装一句文本
  3) **先发 Windows toast**（`toast_notify.notify`）→ 成功则**不再推气泡**（防重复打扰）
  4) toast 失败（或 force-bubble）→ **回退桌宠气泡**：POST http://127.0.0.1:7777/bubble
     （header `x-petdex-update-token`，token 文件 `~\.petdex\runtime\update-token`）
  5) 两边都失败 → 写 `workbench.log` 兜底；期限页打开即见（不依赖推送）

为什么保留气泡通道：桌宠可能在跑（它是 wb-proxy 8788 的宿主），气泡是它能力的一部分；
toast 若被系统策略/通知权限拦下，气泡是可靠兜底。两通道**不同时推**，避免双份打扰。

为什么用 `/bubble` 而不是 `/mood`：`/mood` 是**额度心情主权通道**（`push-mood.js` 是唯一写者，30 秒一推），
抢写会互相覆盖。`/bubble` 是 sidecar 的文本气泡接口（`text` ≤200，实测可显示），**不改壳、不改 push-mood、不重启进程**。

去重：`data\deadline_push_state.json` 记录当日已推次数与内容指纹 —— 同一内容当日最多推 2 次（对应 09:00 / 14:00 两个计划任务触发）。

用法：
  python deadline_check.py                # 正常跑（刷新 + 判定 + toast→气泡回退）
  python deadline_check.py --dry-run      # 只算不推（打印将推送的文本）
  python deadline_check.py --no-refresh   # 不刷新台账派生（用现有 deadlines.json）
  python deadline_check.py --days 7
  python deadline_check.py --bubble-only  # 强制走气泡（跳开 toast；调试用）
  python deadline_check.py --toast-only   # 强制只走 toast（跳开气泡回退）
  python deadline_check.py --selftest

退出码：0=正常（无论是否有期限）2=参数错误 3=异常
"""
import argparse
import config as _config
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import deadline_ops  # noqa: E402

VERSION = '1.1.0'
DEFAULT_PORT = int(_config.get('notification.bubble_port', 7777) or 7777)
STATE_PATH = os.path.join(HERE, 'data', 'deadline_push_state.json')
LOG_PATH = os.path.join(HERE, 'workbench.log')
MAX_TEXT = 200          # sidecar /bubble 上限
MAX_ITEMS_SHOWN = 3


def token_path():
    return os.environ.get('PETDEX_TOKEN_PATH') or os.path.join(
        os.path.expanduser('~'), '.petdex', 'runtime', 'update-token')


def petdex_port():
    return int(os.environ.get('PETDEX_PORT') or DEFAULT_PORT)


def log(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s | DEADLINE | %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


def build_text(items, today=None):
    """组装气泡文本（逾期优先、按剩余天数升序）；无临期项 → 返回 None"""
    if not items:
        return None
    today = today or date.today()
    parts, overdue = [], 0
    for it in items[:MAX_ITEMS_SHOWN]:
        left = it['剩余天']
        tag = '逾期%d天' % (-left) if left < 0 else ('今天' if left == 0 else '%d天' % left)
        if left < 0:
            overdue += 1
        parts.append('%s %s %s' % (str(it.get('案件') or '')[:12], it.get('期限类型') or '', tag))
    head = '⚖️ 期限%d项' % len(items)
    if overdue:
        head += '（逾期%d）' % overdue
    text = head + '：' + '；'.join(parts)
    if len(items) > MAX_ITEMS_SHOWN:
        text += '；等'
    return text[:MAX_TEXT]


def build_preservation_text(items):
    """批 58：续封窗口提示文案（保全/续封类里程碑）；无项返回 None"""
    if not items:
        return None
    parts = []
    for it in items[:3]:
        parts.append('%s %s %s天后' % (str(it.get('案件') or '')[:10],
                                       it.get('期限类型') or '', it['剩余天']))
    text = '📌 续封窗口：' + '；'.join(parts)
    if len(items) > 3:
        text += '；等%d项' % len(items)
    return text[:160]


def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def push_bubble(text):
    """POST /bubble → (ok, detail)"""
    tp = token_path()
    if not os.path.isfile(tp):
        return False, 'token 文件不存在：%s' % tp
    try:
        with open(tp, encoding='utf-8') as f:
            token = f.read().strip()
    except OSError as e:
        return False, 'token 读取失败：%r' % e
    body = json.dumps({'text': text, 'agent_source': 'legal-workbench'}).encode('utf-8')
    req = urllib.request.Request(
        'http://127.0.0.1:%d/bubble' % petdex_port(), data=body,
        headers={'Content-Type': 'application/json', 'x-petdex-update-token': token})
    # ⚠️ 必须绕过系统代理：沙箱/环境里 HTTP_PROXY 存在时，urlopen 会把 127.0.0.1 也走代理 → 502
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=4) as r:
            j = json.loads(r.read().decode('utf-8'))
            return bool(j.get('ok')), 'ok=%s counter=%s' % (j.get('ok'), j.get('counter'))
    except urllib.error.HTTPError as e:
        return False, 'HTTP %s %s' % (e.code, e.read()[:120])
    except Exception as e:
        return False, '%r' % e


def push_toast(title, text):
    """发 Windows toast（批 D）。→ (ok, detail)。失败由调用方回退气泡。"""
    try:
        import toast_notify
        return toast_notify.notify(title, text)
    except Exception as e:                                   # noqa: BLE001
        return False, 'toast_notify 不可用：%r' % e


def refresh():
    """读台账 → 刷新 deadlines.json（只读台账，不回写）"""
    items, hints, skipped = deadline_ops.extract_from_ledger()
    data, stat = deadline_ops.merge(items, hints)
    deadline_ops.save(data, apply=True)
    return stat, skipped


def main():
    ap = argparse.ArgumentParser(description='期限提醒推送 v%s' % VERSION)
    ap.add_argument('--days', type=int, default=7, help='临期窗口（默认 7 天）')
    ap.add_argument('--dry-run', action='store_true', help='只算不推')
    ap.add_argument('--no-refresh', action='store_true', help='不刷新台账派生')
    ap.add_argument('--force', action='store_true', help='忽略当日去重限制')
    ap.add_argument('--bubble-only', action='store_true', help='强制走桌宠气泡（跳开 toast）')
    ap.add_argument('--toast-only', action='store_true', help='强制只走 toast（跳开气泡回退）')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    stat = None
    if not args.no_refresh:
        try:
            stat, skipped = refresh()
        except Exception as e:
            log('refresh 失败（沿用旧表）：%r' % e)
            stat, skipped = None, 0

    items = deadline_ops.due_items(args.days)
    # 2026-09-17 批 37：桌宠气泡同时带「手工日程」（两套数据合并推送，各标前缀区分）
    sched = []
    try:
        import schedule_ops
        sched = schedule_ops.due_for_reminder()
    except Exception as e:                                    # noqa: BLE001
        log('日程读取失败（不影响期限推送）：%r' % e)
    sched_text = None
    if sched:
        try:
            sched_text = schedule_ops.build_reminder_text(sched)
        except Exception as e:                                # noqa: BLE001
            log('日程文案组装失败：%r' % e)

    text = build_text(items)
    # 批 58：续封窗口（保全/续封类提前 60 天，仅在 60/30/14 天里程碑日提示，防刷屏）
    pres_items = deadline_ops.preservation_alert(args.days)
    pres_text = build_preservation_text(pres_items)
    if text and pres_text:
        text = (text + ' ｜ ' + pres_text)[:400]
    elif pres_text:
        text = pres_text[:400]
    if text and sched_text:
        text = (text + ' ｜ ' + sched_text)[:400]             # 合并（期限在前，日程在后）
    elif sched_text:
        text = sched_text                                     # 只有日程也要推

    head = '[deadline_check] 期限 %d 项（%d 天内）· 续封 %d 项 · 日程 %d 项待提醒' % (
        len(items), args.days, len(pres_items), len(sched))
    if stat:
        head += ' 刷新：新增 %d / 维持 %d / 历史跳过 %d' % (stat['added'], stat['updated'], skipped)
    print(head)
    if not text:
        print('无临期/逾期项、无待提醒日程 → 不推送')
        return 0
    for it in items:
        print('  %-11s %-10s %-16s %s' % (it['到期日'], it['期限类型'],
                                          str(it['案件'])[:16], it['档位']))
    for it in sched:
        print('  [日程] %-11s %-10s %s' % (it['日期'], it['类型'], it['标题']))
    for it in pres_items:
        print('  [续封] %-11s %-10s %s（剩%d天）' % (it['到期日'], it.get('期限类型'),
                                                    str(it['案件'])[:16], it['剩余天']))
    print('提醒文本：%s' % text)

    if args.dry_run:
        print('（--dry-run，未推送）')
        return 0

    today = date.today().isoformat()
    st = load_state()
    same_day = st.get('date') == today and st.get('text') == text
    if same_day and not args.force and int(st.get('count', 0)) >= 2:
        print('当日已推 2 次且内容未变 → 跳过（--force 可强推）')
        return 0

    # 批 D 双通道（拍板 D1）：toast 为主 → 成功不推气泡；失败回退气泡
    channel, detail = None, ''
    if not args.bubble_only:
        ok, detail = push_toast('法律工作台 · 期限提醒', text[:400])
        if ok:
            channel = 'toast'
        elif args.toast_only:
            log('TOAST 失败且 --toast-only：%s | text=%s' % (detail, text))
            print('toast 失败（--toast-only，不推气泡）：%s' % detail)
            return 0
        else:
            print('toast 失败，回退桌宠气泡：%s' % detail)
    if channel is None:                                     # 气泡通道（回退 或 --bubble-only）
        ok, bdetail = push_bubble(text)
        if ok:
            channel, detail = 'bubble', bdetail
        else:
            log('推送失败（toast+气泡都不通）：toast=%s | bubble=%s | text=%s'
                % (detail, bdetail, text))
            print('推送失败（已写 workbench.log 兜底）：%s' % bdetail)
            print('提示：桌宠未开、且 toast 不可用属异常；期限页打开即见。')
            return 0

    save_state({'date': today, 'text': text,
                'count': (int(st.get('count', 0)) + 1) if same_day else 1,
                'last_ok': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'channel': channel})
    print('已推送（%s）：%s' % (channel, detail))
    return 0


# ---------------------------------------------------------------- 自测

def selftest():
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    today = date(2026, 9, 12)
    items = [
        {'案件': '陈丽萍vs李喜前', '期限类型': '缴费期限', '到期日': '2026-09-03', '剩余天': -9},
        {'案件': '福建委托人vs新余钦', '期限类型': '开庭', '到期日': '2026-09-15', '剩余天': 3},
    ]
    t = build_text(items, today)
    chk('文本含逾期标记', '逾期9天' in t)
    chk('文本含剩余天数', '3天' in t)
    chk('文本含案件与类型', '陈丽萍' in t and '开庭' in t)
    chk('逾期计数出现', '（逾期1）' in t)
    chk('无项返回 None', build_text([], today) is None)
    long_items = [{'案件': '甲' * 30, '期限类型': '开庭', '到期日': '2026-09-13', '剩余天': 1}
                  for _ in range(9)]
    chk('超长文本截断到 200', len(build_text(long_items, today)) <= 200)
    chk('超 3 项折叠为「等」', build_text(long_items, today).endswith('等'))
    pres_t = [{'案件': '甲案甲案甲案甲案甲案甲案', '期限类型': '续封期限',
               '到期日': '2026-11-11', '剩余天': 60}]
    pt = build_preservation_text(pres_t)
    chk('续封窗口文案成形', bool(pt) and '续封窗口' in pt and '60天后' in pt)
    chk('续封空列表返回 None', build_preservation_text([]) is None)

    tmp_state = os.path.join(os.path.dirname(STATE_PATH), '_selftest_state.json')
    chk('token 路径可推导', token_path().endswith('update-token'))
    chk('端口可覆盖', petdex_port() == 7777)
    chk('状态文件可读写', _state_roundtrip(tmp_state))
    if os.path.isfile(tmp_state):
        os.unlink(tmp_state)

    # 批 D：双通道接线断言（不真发通知）
    try:
        import toast_notify
        chk('toast_notify 可导入', True)
        chk('toast selftest 编码往返', toast_notify._utf16le_b64_roundtrip('甲') == '甲')
    except Exception as e:                                   # noqa: BLE001
        chk('toast_notify 可导入（%r）' % e, False)
    chk('push_toast 是函数', callable(push_toast))
    try:
        ok, d = push_toast('t', '')                          # 空正文 → 不该真发
        chk('push_toast 空正文被拦', ok is False)
    except Exception as e:                                   # noqa: BLE001
        chk('push_toast 空正文被拦（%r）' % e, False)

    print('--- selftest %s（%d 项，失败 %d）---' % (
        '全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def _state_roundtrip(path):
    global STATE_PATH
    old = STATE_PATH
    STATE_PATH = path
    try:
        save_state({'date': '2026-09-12', 'text': 'x', 'count': 1})
        return load_state().get('count') == 1
    finally:
        STATE_PATH = old


if __name__ == '__main__':
    sys.exit(main())
