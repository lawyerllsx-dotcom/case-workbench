#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
deadline_ops.py — 期限表操作库（法律工作台 · 单一实现）

定位：从台账派生本地期限表 `data\deadlines.json`（**只读台账，绝不回写**），
供 ① 「期限与日程页」 ② 阶段 4 的 `deadline_check.py`（外部服务推送）共用。

三路供给（方案 §5.1 期限与日程）：
  ① 台账 H 列（开庭时间）        → 期限类型：开庭
  ② 台账 J 列（进度日志日期）    → 关键词定类型：上诉期 / 举证期限 / 续封 / 缴费 / 其他
  ③ 台账 X 列（保全情况及期限）  → 期限类型：保全到期 / 续封期限
  ④ 人工补录（add）
相对表述（如「15 天自然日」）无法自动落日期 → 输出「需人工补录」提示，不猜。

纪律：只读台账；期限表独立存储；不删除历史（done 只改状态）；到期判定在读取时计算。

用法：
  python deadline_ops.py extract [--apply]        # 从台账抽取（dry-run 默认）
  python deadline_ops.py list [--days N]          # 清单（按到期排序）
  python deadline_ops.py due [--days 7]           # 临期/逾期项
  python deadline_ops.py add --案件 X --type 开庭 --date 2026-09-20 [--note ..] [--apply]
  python deadline_ops.py done --id 3 [--apply]
  python deadline_ops.py selftest

退出码：0=正常 2=输入错误 3=异常
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

if os.environ.get('_WB_STDOUT_UTF8') != '1':      # 幂等：多模块同进程重复包裹会关掉缓冲区
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger_ops  # noqa: E402  台账读（单一实现）

VERSION = '1.0.0'
DEFAULT_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', 'deadlines.json')

TYPES = ['开庭', '上诉期', '举证期限', '续封期限', '保全到期', '缴费期限', '其他']

# 批 58：续封窗口（保全/续封类提前量 + 里程碑提醒日；只影响推送提醒，不改默认查询窗口）
PRESERVATION_TYPES = ('续封期限', '保全到期')
PRESERVATION_DAYS = 60
PRESERVATION_MILESTONES = (60, 30, 14)
TYPE_KEYWORDS = [
    ('上诉期', ['上诉期', '上诉届满', '15 天', '15天']),
    ('举证期限', ['举证期限', '举证届满', '举证']),
    ('续封期限', ['续封']),
    ('保全到期', ['保全到期', '保全期限', '查封到期', '冻结到期', '保全届满']),
    ('缴费期限', ['缴费', '交费', '公告费', '诉讼费']),
]

# 短式（两位月日）尾部加「单位词负向断言」：防把「3-4天」「15-30天」这类数量范围
# 当成日期（2026-09-14 用户实机：J 列「04.25到期前3-4天需确认续封」→「3-4」被读成
# 03-04 再跨年校正成 2027-03-04，凭空多出一条假期限）。
DATE_RE = re.compile(
    r'(20\d{2})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?'
    r'|(?<!\d)(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?(?!\s*[\d天年周人项次份月])')

# ---- 完成态识别（2026-09-14 用户实机：「09.03：已缴费」被误报逾期）----
# 口径：「已」紧邻动作词 → 视为已完成；但行内仍有「待/等待/尚未/需 + 类型动作」
# 的未完成信号时保守判未完成（防混合行「已提交材料，等待缴费通知」误判）。
_DONE_RE = re.compile(
    r'已\s*(缴费|缴纳|交费|付款|付|支付|提交|立案|开庭|送达|归档|领取|发出|发函|办理|办结|'
    r'处理|履行|结清|结案|完成|撤|销|解除|放款|退)')
_PENDING_RE = re.compile(
    r'(待|等待|尚未|未|需|将于|计划|预计|拟)[^，。；,;]{0,6}'
    r'(缴费|缴纳|交费|提交|立案|开庭|送达|归档|办理|付|缴)')


def _is_done_line(line):
    """日志行是否描述「已完成」的节点（完成则不生成待办期限，标 done）。"""
    if not _DONE_RE.search(line):
        return False
    return not _PENDING_RE.search(line)


# ---- 期限语义定位（2026-09-17 用户实机：一行台账被抽成 4 条假期限）----
# 现象：J 列「09.17：期间核算修正——公告08.17刊载→09.16公告期满视为送达；被告上诉期
# 09.17起15日至10.01（国庆法定节假日顺延至节后首个工作日，待确认）」被抽成
# 09-16 / 09-17 / 10-01 / 10-08 四条独立「上诉期」——其中 09-16、09-17 是**过程节点**
# （刊载日、起算日），不是期限，却各成一条并每天推假警报。
# 口径：① 一条日志行最多产出一条期限（真期限），其余日期视为过程节点；
#      ② 「真期限」= 句中最后一个**指向未来**的日期（期间届满、顺延结果）；
#      ③ 若句中出现「顺延至/顺延到/届满/截止/至」，取其后的日期；
#      ④ 过程节点不单独成条。
_DUE_MARK_RE = re.compile(
    r'(顺延至|顺延到|届满|截至|截止|前完成|前提交|前缴纳|之前|至)')
_PROC_NODE_RE = re.compile(
    r'(刊载|刊登|起算|开始计算|送达|发出|收文|受理|立案日|起)')


def pick_due_dates(line, ds, today=None):
    """从一行的全部日期中挑出「真期限」，其余归为过程节点。

    返回 (期限日期列表, 过程节点日期列表)。规则公开可复核，不做猜测：
    1) 无日期 → 空；
    2) 只有一个日期 → 它是期限（沿用原行为）；
    3) 多个日期 → 看「顺延/届满/截止/至」后的日期优先；
       再退化为「不早于今天的最晚日期」；都早于今天则取最晚的那个（已过期仍要报）。
    """
    today = today or date.today()
    ds = sorted(set(ds or []))
    if not ds:
        return [], []
    if len(ds) == 1:
        return ds, []

    # ① 「顺延至 X」/「至 X」类锚点：取锚点之后最近的日期
    tail = []
    for m in _DUE_MARK_RE.finditer(line or ''):
        after = (line or '')[m.end():]
        for d in parse_dates(after, today):
            tail.append(d)
    if tail:
        due = [max(tail)]
        return due, [d for d in ds if d not in due]

    # ② 无锚点：优先取「不早于今天」的日期中最晚的（期间届满通常在最后）
    future = [d for d in ds if date.fromisoformat(d) >= today]
    if future:
        due = [max(future)]
        return due, [d for d in ds if d not in due]

    # ③ 全部已过期：取最晚的（保守：仍提示，但只一条）
    return [max(ds)], [d for d in ds if d != max(ds)]


# ---------------------------------------------------------------- 日期

def parse_dates(text, ref=None):
    """从文本抽绝对日期 → ['YYYY-MM-DD', ...]；两位月日按 ref 年补全（跨年校正）"""
    ref = ref or date.today()
    out = []
    for m in DATE_RE.finditer(text or ''):
        try:
            if m.group(1):
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                y, mo, d = ref.year, int(m.group(4)), int(m.group(5))
                if date(y, mo, d) < ref - timedelta(days=180):
                    y += 1
                elif date(y, mo, d) > ref + timedelta(days=365):
                    y -= 1
            out.append(date(y, mo, d).isoformat())
        except ValueError:
            continue
    return sorted(set(out))


def judge(due, today=None):
    """→ (剩余天数, 档位)；档位 ∈ 逾期 / 当日 / 3日内 / 7日内 / 正常"""
    today = today or date.today()
    d = (date.fromisoformat(due) - today).days if isinstance(due, str) else (due - today).days
    if d < 0:
        return d, '逾期'
    if d == 0:
        return d, '当日'
    if d <= 3:
        return d, '3日内'
    if d <= 7:
        return d, '7日内'
    return d, '正常'


def _id_for(item):
    key = '%s|%s|%s|%s' % (item.get('案件', ''), item.get('期限类型', ''),
                           item.get('到期日', ''), item.get('来源', ''))
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


# ---------------------------------------------------------------- 存储

def store_path():
    return os.environ.get('DEADLINE_PATH', DEFAULT_STORE)


def load():
    p = store_path()
    if not os.path.isfile(p):
        return {'version': VERSION, 'generated_at': None, 'items': [], 'manual_hints': []}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save(data, apply=False):
    p = store_path()
    if not apply:
        return p, False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p, True


# ---------------------------------------------------------------- 抽取

def _case_label(row):
    return str(row.get('案件') or '').strip()


def extract_from_ledger(keep_past_days=14):
    """从台账三路抽取 → (新增/更新项, 需人工补录提示)

    keep_past_days：H/J 两路的历史日期（已开过的庭、已过的节点）默认丢弃，
    仅保留最近 N 天内的（便于发现"刚过期但没销项"的）；X 列保全期限不过滤。
    """
    today = date.today()
    floor = today - timedelta(days=keep_past_days)
    items, hints, skipped = [], [], 0

    def _add(**kw):
        nonlocal skipped
        d = kw['到期日']
        if kw['来源'] in ('台账H', '台账J') and date.fromisoformat(d) < floor:
            skipped += 1
            return
        items.append(kw)

    for sheet in ledger_ops.SHEETS_CASE:
        for row in ledger_ops.read_rows(sheet):
            case = _case_label(row)
            if not case:
                continue
            base = {'案件': case, 'sheet': sheet, 'row': row['_row']}

            # ① H 列 开庭时间
            h = str(row.get('开庭时间') or '').strip()
            for d in parse_dates(h, today):
                _add(**dict(base, 期限类型='开庭', 到期日=d, 来源='台账H', 备注=h[:60]))

            # ② J 列 进度日志
            # 一行最多产出一条期限（2026-09-17 修：一行多日期曾被全抽成独立期限）
            j = str(row.get('进度日志') or '')
            for line in [l.strip() for l in j.splitlines() if l.strip()]:
                ds = parse_dates(line, today)
                t = None
                for tname, keys in TYPE_KEYWORDS:
                    if any(k in line for k in keys):
                        t = tname
                        break
                if ds and t:
                    due, proc = pick_due_dates(line, ds, today)
                    for d in due:
                        note = line[:80]
                        if proc:
                            note = '%s〔同行过程节点 %s 不另立期限〕' % (
                                line[:60], '、'.join(x[5:] for x in proc))
                        _add(**dict(base, 期限类型=t, 到期日=d, 来源='台账J',
                                    _done_line=_is_done_line(line), 备注=note))
                elif t and not ds and re.search(r'\d+\s*(天|个工作日|日)', line):
                    hints.append({'案件': case, 'sheet': sheet, 'row': row['_row'],
                                  'line': line[:100],
                                  '原因': '相对期限（无绝对日期）需人工确认'})

            # ③ X 列 保全情况及期限
            x = str(row.get('保全情况及期限') or '').strip()
            if x:
                t = '续封期限' if '续封' in x else '保全到期'
                for d in parse_dates(x, today):
                    _add(**dict(base, 期限类型=t, 到期日=d, 来源='台账X', 备注=x[:80]))
    return items, hints, skipped


def merge(items, hints, apply=False):
    """合并进期限表：key=(案件, 类型, 到期日) 去重；已 done 的保留状态"""
    data = load()
    old = {i['key']: i for i in data.get('items', [])}
    merged, added, kept, dup = [], 0, 0, 0
    seen = set()
    for it in items:
        it = dict(it)
        done_line = bool(it.pop('_done_line', False))    # 台账行判定的完成态（不走 prev 继承）
        it['key'] = '%s|%s|%s' % (it['案件'], it['期限类型'], it['到期日'])
        if it['key'] in seen:                    # 同一次抽取内的重复（同一期限多行日志）
            dup += 1
            continue
        seen.add(it['key'])
        prev = old.pop(it['key'], None)
        if done_line:
            it['状态'] = 'done'                  # 台账明写已完成 → 强制 done（修正历史误报）
            it['done_at'] = (prev or {}).get('done_at') or datetime.now().strftime('%Y-%m-%d %H:%M')
        elif prev:
            it['状态'] = prev.get('状态', 'open')
        else:
            it.setdefault('状态', 'open')
        if prev:
            it['id'] = prev.get('id') or _id_for(it)
            kept += 1
        else:
            it['id'] = _id_for(it)
            added += 1
        merged.append(it)
    # 台账里已消失的来源项（台账改过）→ 保留但标 orphan，不删
    for k, v in old.items():
        v = dict(v)
        v['状态'] = v.get('状态', 'open')
        v['来源态'] = 'orphan（台账已无此条目，保留待人工确认）'
        merged.append(v)
    data['items'] = sorted(merged, key=lambda i: (i.get('到期日') or '9999', i.get('案件', '')))
    data['manual_hints'] = hints
    data['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    data['version'] = VERSION
    return data, {'added': added, 'updated': kept, 'orphan': len(old), 'dup': dup}


def add_manual(case, dtype, due, note='', apply=False):
    if dtype not in TYPES:
        raise ValueError('期限类型需为 %s' % TYPES)
    date.fromisoformat(due)
    data = load()
    it = {'案件': case, '期限类型': dtype, '到期日': due, '来源': '人工', '备注': note,
          '状态': 'open', 'key': '%s|%s|%s' % (case, dtype, due)}
    it['id'] = _id_for(it)
    data.setdefault('items', []).append(it)
    data['items'] = sorted(data['items'], key=lambda i: (i.get('到期日') or '9999',
                                                         i.get('案件', '')))
    if apply:
        save(data, apply=True)
    return data, it


def mark_done(item_id, apply=False):
    data = load()
    hit = None
    for it in data.get('items', []):
        if str(it.get('id')) == str(item_id):
            it['状态'] = 'done'
            it['done_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            hit = it
    if not hit:
        raise ValueError('未找到期限项 id=%s' % item_id)
    if apply:
        save(data, apply=True)
    return data, hit


def drop(item_id, apply=False):
    """删除一条期限（用于台账抽取误报等**证伪**条目）。

    与 mark_done 的区别：done 是「办完了、留痕」；drop 是「这条根本不该存在」。
    注意：若该条仍会从台账抽取出来，下次 extract 会重新出现（属预期）。
    """
    data = load()
    hit, keep = None, []
    for it in data.get('items', []):
        if str(it.get('id')) == str(item_id):
            hit = it
            continue
        keep.append(it)
    if not hit:
        raise ValueError('未找到期限项 id=%s' % item_id)
    data['items'] = keep
    if apply:
        save(data, apply=True)
    return data, hit


def due_items(days=7, only_open=True, preservation_days=None):
    """到期项清单。preservation_days 提供时，保全/续封类改用该窗口（批 58）；
    默认 None = 全部沿用 days（对既有调用零影响）。"""
    data = load()
    out = []
    for it in data.get('items', []):
        if only_open and it.get('状态') == 'done':
            continue
        try:
            left, level = judge(it['到期日'])
        except Exception:
            continue
        win = days
        if preservation_days is not None and it.get('期限类型') in PRESERVATION_TYPES:
            win = preservation_days
        if level != '正常' or left <= win:
            out.append(dict(it, 剩余天=left, 档位=level))
    return sorted(out, key=lambda i: i['剩余天'])


def preservation_alert(days=7):
    """批 58：续封里程碑提醒项——保全/续封类在 PRESERVATION_DAYS 窗口内、
    且剩余天数恰为里程碑（60/30/14）的项（防刷屏；≤7 天由主窗口日常推送覆盖）。"""
    out = []
    try:
        wide = due_items(days, preservation_days=PRESERVATION_DAYS)
    except Exception:
        return out
    for it in wide:
        if (it.get('期限类型') in PRESERVATION_TYPES
                and it['剩余天'] > days
                and it['剩余天'] in PRESERVATION_MILESTONES):
            out.append(it)
    return out


# ---------------------------------------------------------------- CLI

def _print_items(items):
    if not items:
        print('  （无）')
        return
    for it in items:
        lv = it.get('档位', '')
        mark = {'逾期': '🔴', '当日': '🟠', '3日内': '🟡', '7日内': '🟡'}.get(lv, '  ')
        print('%s %-11s %s %-10s %-8s %s' % (mark, it.get('到期日', ''), it.get('案件', '')[:18],
                                             it.get('期限类型', ''), lv,
                                             ('剩余 %d 天' % it['剩余天']) if '剩余天' in it else ''))
        if it.get('备注'):
            print('      备注: %s（来源 %s）' % (it['备注'][:60], it.get('来源')))


def main():
    ap = argparse.ArgumentParser(description='期限表操作库 v%s' % VERSION)
    sub = ap.add_subparsers(dest='cmd')

    p_e = sub.add_parser('extract')
    p_e.add_argument('--apply', action='store_true')
    p_l = sub.add_parser('list')
    p_l.add_argument('--days', type=int, default=0)
    p_d = sub.add_parser('due')
    p_d.add_argument('--days', type=int, default=7)
    p_a = sub.add_parser('add')
    p_a.add_argument('--案件', dest='case', required=True)
    p_a.add_argument('--type', dest='dtype', required=True, choices=TYPES)
    p_a.add_argument('--date', dest='due', required=True, help='YYYY-MM-DD')
    p_a.add_argument('--note', default='')
    p_a.add_argument('--apply', action='store_true')
    p_m = sub.add_parser('done')
    p_m.add_argument('--id', required=True)
    p_m.add_argument('--apply', action='store_true')
    p_x = sub.add_parser('drop')
    p_x.add_argument('--id', required=True, help='删除一条证伪的期限（台账抽取误报）')
    p_x.add_argument('--apply', action='store_true')
    sub.add_parser('selftest')

    args = ap.parse_args()

    if args.cmd == 'extract':
        items, hints, skipped = extract_from_ledger()
        data, stat = merge(items, hints)
        print('台账抽取：候选 %d 条（历史跳过 %d / 同期限重复合并 %d）→ 新增 %d / 维持 %d / 台账已消失 %d'
              % (len(items), skipped, stat['dup'], stat['added'], stat['updated'],
                 stat['orphan']))
        for it in data['items']:
            print('  %-11s %-10s %-16s %s' % (it.get('到期日'), it.get('期限类型'),
                                              it.get('案件', '')[:16], it.get('来源')))
        if hints:
            print('需人工补录（相对期限，不猜）：')
            for h in hints:
                print('  - %s | %s' % (h['案件'][:16], h['line']))
        if not args.apply:
            print('（dry-run，未写入 %s；加 --apply 写入）' % store_path())
            return 0
        p, _ = save(data, apply=True)
        print('已写入 %s' % p)
        return 0

    if args.cmd == 'list':
        data = load()
        items = []
        for it in data.get('items', []):
            if it.get('状态') == 'done':
                continue
            left, lv = judge(it['到期日']) if it.get('到期日') else (None, '')
            if args.days and (left is None or left > args.days):
                continue
            items.append(dict(it, 剩余天=left, 档位=lv))
        print('=== 期限表（%s）=== 来源: %s' % (
            len(items), data.get('generated_at') or '尚未抽取'))
        _print_items(sorted(items, key=lambda i: i.get('剩余天', 9999)))
        return 0

    if args.cmd == 'due':
        items = due_items(args.days)
        print('=== 临期/逾期（%d 天内）=== %d 条' % (args.days, len(items)))
        _print_items(items)
        return 0

    if args.cmd == 'add':
        data, it = add_manual(args.case, args.dtype, args.due, args.note, args.apply)
        print('拟新增：%s | %s | %s' % (it['案件'], it['期限类型'], it['到期日']))
        if not args.apply:
            print('（dry-run，未写入；加 --apply）')
            return 0
        p, _ = save(data, apply=True)
        print('已写入 %s' % p)
        return 0

    if args.cmd == 'done':
        data, it = mark_done(args.id, args.apply)
        print('拟标记完成：%s | %s | %s' % (it['案件'], it['期限类型'], it['到期日']))
        if not args.apply:
            print('（dry-run，未写入；加 --apply）')
            return 0
        p, _ = save(data, apply=True)
        print('已写入 %s' % p)
        return 0

    if args.cmd == 'drop':
        data, it = drop(args.id, args.apply)
        print('拟删除：%s | %s | %s | %s' % (it['案件'], it['期限类型'], it['到期日'],
                                            it.get('备注', '')[:40]))
        if not args.apply:
            print('（dry-run，未写入；加 --apply）')
            return 0
        p, _ = save(data, apply=True)
        print('已写入 %s' % p)
        return 0

    if args.cmd == 'selftest':
        return selftest()
    ap.print_help()
    return 0


# ---------------------------------------------------------------- 自测

def selftest():
    import tempfile
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    ref = date(2026, 9, 12)
    chk('全日期格式', parse_dates('2026.09.30', ref) == ['2026-09-30'])
    chk('中文日期', parse_dates('2026年9月30日', ref) == ['2026-09-30'])
    chk('斜杠日期', parse_dates('2026/9/30', ref) == ['2026-09-30'])
    chk('短日期当年', parse_dates('10.15', ref) == ['2026-10-15'])
    chk('短日期跨年校正（已过 → 次年）', parse_dates('01.05', ref) == ['2027-01-05'])
    chk('句中多日期去重', parse_dates('查封 2027.04.03，续封至2028.04.03', ref)
        == ['2027-04-03', '2028-04-03'])
    chk('无日期返回空', parse_dates('已提交材料', ref) == [])
    # 2026-09-14 数量范围误判（用户实机：「到期前3-4天」被读成 2027-03-04 假期限）
    chk('数量范围不当日期：「3-4天」', parse_dates('到期前3-4天需确认', ref) == [])
    chk('数量范围不当日期：「15-30天」', parse_dates('15-30天内提交', ref) == [])
    chk('范围行只取真日期', parse_dates('04.25到期前3-4天需确认续封', ref) == ['2026-04-25'])
    chk('短横线日期仍识别', parse_dates('09-03 提交', ref) == ['2026-09-03'])
    # 2026-09-14 完成态识别（用户实机：「09.03：已缴费」被误报逾期）
    chk('完成态：已缴费判 done', _is_done_line('09.03：已缴费，公告待刊登状态') is True)
    chk('完成态：混合行「已提交…等待缴费」不判 done',
        _is_done_line('已提交材料，等待缴费通知，09.20 前缴费') is False)
    chk('完成态：无完成词不判 done', _is_done_line('09.20：缴费期限届满') is False)

    # 2026-09-17 一行多日期收敛（用户实机：一行被抽成 4 条「上诉期」含 2 条假警报）
    _L = ('09.17：期间核算修正——公告08.17刊载→09.16公告期满视为送达；被告上诉期'
          '09.17起15日至10.01（国庆法定节假日顺延至节后首个工作日，待确认）')
    _ds = parse_dates(_L, date(2026, 9, 17))
    _due, _proc = pick_due_dates(_L, _ds, date(2026, 9, 17))
    chk('多日期行只产出一条真期限', len(_due) == 1)
    chk('多日期行取「至10.01」为真期限', _due == ['2026-10-01'])
    chk('过程节点被归并（不再单独成条）', '2026-09-16' in _proc and '2026-09-17' in _proc)
    chk('单日期行行为不变', pick_due_dates('09.20 缴费', ['2026-09-20'], date(2026, 9, 17))
        == (['2026-09-20'], []))
    chk('无日期返回空', pick_due_dates('无日期', [], date(2026, 9, 17)) == ([], []))
    chk('无锚点多日期取最晚未来日',
        pick_due_dates('09.16送达，10.08开庭', ['2026-09-16', '2026-10-08'],
                       date(2026, 9, 17))[0] == ['2026-10-08'])
    chk('全过期多日期取最晚（仍报但只一条）',
        pick_due_dates('08.01起算，08.17刊载', ['2026-08-01', '2026-08-17'],
                       date(2026, 9, 17))[0] == ['2026-08-17'])

    chk('判定 逾期', judge('2026-09-01', ref)[1] == '逾期')
    chk('判定 当日', judge('2026-09-12', ref)[1] == '当日')
    chk('判定 3日内', judge('2026-09-14', ref)[1] == '3日内')
    chk('判定 7日内', judge('2026-09-18', ref)[1] == '7日内')
    chk('判定 正常', judge('2026-10-30', ref)[1] == '正常')
    chk('剩余天数', judge('2026-09-19', ref)[0] == 7)

    tmp = tempfile.mkdtemp(prefix='deadline_selftest_')
    os.environ['DEADLINE_PATH'] = os.path.join(tmp, 'data', 'deadlines.json')
    data, it = add_manual('测试案', '开庭', '2026-09-15', '连江法院', apply=True)
    p, ok = save(data, apply=True)
    chk('人工补录落盘', ok and os.path.isfile(p))
    chk('落盘后可读回', len(load()['items']) == 1)
    chk('人工项来源标记', load()['items'][0]['来源'] == '人工')
    chk('due 命中 3 日内', len(due_items(7, only_open=True)) == 1)

    # 合并去重 + 保留 done 状态
    items = [{'案件': '测试案', '期限类型': '开庭', '到期日': '2026-09-15',
              '来源': '台账H', '备注': '', 'sheet': '案件', 'row': 4}]
    d2, stat = merge(items, [])
    chk('重复项判为维持', stat['added'] == 0 and stat['updated'] == 1)
    _, it2 = mark_done(d2['items'][0]['id'], apply=True)
    d3, _ = merge(items, [])
    chk('done 状态在重抽后保留', d3['items'][0]['状态'] == 'done')
    chk('无效类型被拒', _raises(lambda: add_manual('x', '不存在的类型', '2026-09-15')))
    chk('非法日期被拒', _raises(lambda: add_manual('x', '开庭', '2026-13-40')))
    chk('mark_done 未知 id 报错', _raises(lambda: mark_done('nope')))
    # drop（删除证伪条目；与 done 的区别 = 不留痕地移除）
    add_manual('误报案', '续封期限', '2027-03-04', '假期限', apply=True)
    fake = [i for i in load()['items'] if i['案件'] == '误报案']
    chk('drop 前存在', len(fake) == 1)
    d4, removed = drop(fake[0]['id'], apply=True)
    chk('drop 移除了该条', removed and removed['案件'] == '误报案')
    chk('drop 后仅剩原条', len(load()['items']) == 1
        and load()['items'][0]['案件'] == '测试案')
    chk('drop 未知 id 报错', _raises(lambda: drop('nope')))
    chk('ID 稳定（同键同 id）', _id_for({'案件': 'a', '期限类型': '开庭',
                                        '到期日': '2026-01-01', '来源': '人工'})
        == _id_for({'案件': 'a', '期限类型': '开庭', '到期日': '2026-01-01',
                    '来源': '人工'}))

    # 批 58：续封窗口（默认口径不变 + 60 天可见 + 里程碑过滤）
    import datetime as _dt58
    d55 = (_dt58.date.today() + _dt58.timedelta(days=55)).isoformat()
    d30 = (_dt58.date.today() + _dt58.timedelta(days=30)).isoformat()
    add_manual('续封甲案', '续封期限', d55, '批58测试', apply=True)
    add_manual('续封乙案', '续封期限', d30, '', apply=True)
    chk('续封默认窗口不扩（55 天不进 7 天窗）',
        all(i['案件'] != '续封甲案' for i in due_items(7, only_open=True)))
    chk('续封 60 天窗口可见', any(i['案件'] in ('续封甲案', '续封乙案')
                                  for i in due_items(7, preservation_days=60)))
    chk('里程碑 30 天命中、55 天不刷屏',
        any(i['案件'] == '续封乙案' for i in preservation_alert(7))
        and all(i['案件'] != '续封甲案' for i in preservation_alert(7)))

    os.environ.pop('DEADLINE_PATH', None)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print('--- selftest %s（%d 项，失败 %d）---' % (
        '全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


if __name__ == '__main__':
    sys.exit(main())
