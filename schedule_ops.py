# -*- coding: utf-8 -*-
"""日程（手工条目）—— 爸爸 2026-09-17 诉求「日程记录及提醒」。

与 `deadline_ops` 的分工（**两套数据，不混**）：
  · `deadlines.json`  = **台账派生**（只读台账 J/H/X 列抽出，人不能直接编辑，改了会被下次抽取覆盖）
  · `schedule.json`   = **手工录入**（会谈 / 阅卷 / 出差 / 内部提醒 / 与当事人沟通…），人写人改，
                        与台账无关、不会被抽取覆盖
日历页把两者**合并显示**，但写入路径完全分开——否则手工条目会被抽取任务冲掉。

纪律：
  · 只增删改本文件；不碰台账、不碰案卷。
  · 删除是软删（标 cancelled + 留痕），保留可追溯性。
  · 桌宠提醒复用 `deadline_check.py` 的 /bubble 通道（不另开通道，避免抢写）。
"""
import argparse
import hashlib
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.join(HERE, 'data', 'schedule.json')

KINDS = ['会谈', '阅卷', '开庭准备', '出差', '内部提醒', '当事人沟通', '其他']
# 提醒提前量（天）：到期前这些天数会出现在「待提醒」里
DEFAULT_LEAD = 1


def store_path():
    return os.environ.get('SCHEDULE_PATH', DEFAULT_STORE)


def load():
    p = store_path()
    if not os.path.isfile(p):
        return {'version': VERSION, 'generated_at': None, 'items': []}
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {'version': VERSION, 'generated_at': None, 'items': []}
    d.setdefault('items', [])
    return d


def save(data, apply=False):
    p = store_path()
    if not apply:
        return p, False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data['version'] = VERSION
    data['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p, True


def _id_for(it):
    key = '%s|%s|%s|%s' % (it.get('日期', ''), it.get('时间', ''), it.get('标题', ''),
                           it.get('案件', ''))
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


def add_entry(title, day, case='', time='', kind='其他', lead=0, note='', apply=False):
    """新增日程条目。title 必填；day 为 YYYY-MM-DD；lead=提前几天开始提醒。"""
    title = str(title or '').strip()
    if not title:
        raise ValueError('标题不能为空')
    day = str(day or '').strip()
    date.fromisoformat(day)                        # 非法日期直接抛
    if kind not in KINDS:
        raise ValueError('类型需为 %s' % KINDS)
    it = {'标题': title, '日期': day, '案件': str(case or '').strip(),
          '时间': str(time or '').strip(), '类型': kind,
          '提前提醒': int(lead or 0), '备注': str(note or '').strip(),
          '状态': 'open', '创建于': datetime.now().strftime('%Y-%m-%d %H:%M')}
    it['id'] = _id_for(it)
    data = load()
    # 同键去重（同日同时同标题同案）
    for old in data['items']:
        if old.get('id') == it['id'] and old.get('状态') == 'open':
            raise ValueError('已存在相同日程（同日/同时/同标题）：%s' % it['id'])
    data['items'].append(it)
    data['items'].sort(key=lambda x: (x.get('日期') or '9999', x.get('时间') or ''))
    if apply:
        save(data, apply=True)
    return data, it


def cancel_entry(item_id, apply=False):
    """软删（留痕）：状态 → cancelled，不从文件移除。"""
    data = load()
    hit = None
    for it in data['items']:
        if str(it.get('id')) == str(item_id):
            it['状态'] = 'cancelled'
            it['取消于'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            hit = it
    if not hit:
        raise ValueError('未找到日程 id=%s' % item_id)
    if apply:
        save(data, apply=True)
    return data, hit


def update_entry(item_id, apply=False, **fields):
    """改条目（只允许改允许的字段；日期变更后重算 id）。"""
    data = load()
    hit = None
    for it in data['items']:
        if str(it.get('id')) == str(item_id):
            hit = it
            break
    if not hit:
        raise ValueError('未找到日程 id=%s' % item_id)
    for k in ('标题', '日期', '案件', '时间', '类型', '提前提醒', '备注'):
        if k in fields and fields[k] is not None and fields[k] != '':
            hit[k] = fields[k]
    if '日期' in fields and fields['日期']:
        date.fromisoformat(str(fields['日期']))
    if '类型' in fields and fields['类型'] and fields['类型'] not in KINDS:
        raise ValueError('类型需为 %s' % KINDS)
    hit['id'] = _id_for(hit)
    hit['更新于'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    data['items'].sort(key=lambda x: (x.get('日期') or '9999', x.get('时间') or ''))
    if apply:
        save(data, apply=True)
    return data, hit


def upcoming(days=30, only_open=True, today=None):
    """未来 N 天内的日程（含当天）。"""
    today = today or date.today()
    end = today + timedelta(days=days)
    out = []
    for it in load()['items']:
        if only_open and it.get('状态') != 'open':
            continue
        try:
            d = date.fromisoformat(str(it.get('日期') or ''))
        except ValueError:
            continue
        if today <= d <= end:
            out.append(dict(it, 剩余天=(d - today).days))
    out.sort(key=lambda x: (x['日期'], x.get('时间') or ''))
    return out


def due_for_reminder(today=None):
    """今天需要提醒的日程：日期已到、或进入「提前提醒」窗口。"""
    today = today or date.today()
    out = []
    for it in load()['items']:
        if it.get('状态') != 'open':
            continue
        try:
            d = date.fromisoformat(str(it.get('日期') or ''))
        except ValueError:
            continue
        left = (d - today).days
        lead = int(it.get('提前提醒') or 0)
        if 0 <= left <= lead:
            out.append(dict(it, 剩余天=left))
    out.sort(key=lambda x: (x['日期'], x.get('时间') or ''))
    return out


def build_reminder_text(items, today=None):
    """组装桌宠气泡文本；无待提醒 → None。"""
    if not items:
        return None
    today = today or date.today()
    parts = []
    for it in items[:4]:
        left = it.get('剩余天', 0)
        tag = '今天' if left == 0 else '%d天后' % left
        t = (' ' + it['时间']) if it.get('时间') else ''
        parts.append('%s %s%s' % (str(it.get('标题') or '')[:14], tag, t))
    text = '📅 日程%d项：' % len(items) + '；'.join(parts)
    if len(items) > 4:
        text += '；等'
    return text[:200]


# ---------------------------------------------------------------- 自测

def selftest():
    import tempfile
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    def _raises(fn):
        try:
            fn()
            return False
        except Exception:                                     # noqa: BLE001
            return True

    tmp = tempfile.mkdtemp(prefix='schedule_selftest_')
    os.environ['SCHEDULE_PATH'] = os.path.join(tmp, 'data', 'schedule.json')

    ref = date(2026, 9, 17)
    d1, it1 = add_entry('与带教会谈', '2026-09-18', case='委托人案', time='10:00',
                        kind='会谈', lead=1, apply=True)
    chk('新增日程落盘', os.path.isfile(store_path()))
    chk('读回 1 条', len(load()['items']) == 1)
    chk('id 已生成', bool(it1.get('id')))
    chk('类型留档', load()['items'][0]['类型'] == '会谈')
    chk('空标题被拒', _raises(lambda: add_entry('', '2026-09-18')))
    chk('非法日期被拒', _raises(lambda: add_entry('x', '2026-13-40')))
    chk('非法类型被拒', _raises(lambda: add_entry('x', '2026-09-18', kind='不存在的')))
    chk('同键重复被拒', _raises(lambda: add_entry('与带教会谈', '2026-09-18',
                                                  case='委托人案', time='10:00')))

    # upcoming 窗口
    add_entry('阅卷', '2026-09-20', kind='阅卷', apply=True)
    add_entry('出差-龙岩', '2026-11-01', kind='出差', apply=True)
    up = upcoming(7, today=ref)
    chk('upcoming 7 天命中 2 条', len(up) == 2)
    chk('upcoming 不含远期', all(x['日期'] != '2026-11-01' for x in up))
    chk('upcoming 剩余天正确', up[0]['剩余天'] == 1)

    # 提前提醒窗口
    rem = due_for_reminder(today=ref)
    chk('提前 1 天且明日到期 → 进提醒', any(x['标题'] == '与带教会谈' for x in rem))
    chk('无提前量且后日到期 → 不进提醒', all(x['标题'] != '阅卷' for x in rem))
    add_entry('当天提醒', '2026-09-17', kind='内部提醒', apply=True)
    rem2 = due_for_reminder(today=ref)
    chk('当天日程进提醒', any(x['标题'] == '当天提醒' for x in rem2))
    txt = build_reminder_text(rem2, today=ref)
    chk('气泡文本含日程数', txt.startswith('📅 日程'))
    chk('气泡文本含「今天」', '今天' in txt)
    chk('无待提醒返回 None', build_reminder_text([], today=ref) is None)

    # 取消（软删）
    data, hit = cancel_entry(it1['id'], apply=True)
    chk('取消后状态为 cancelled', hit['状态'] == 'cancelled')
    chk('取消留痕', bool(hit.get('取消于')))
    chk('取消项不进 upcoming', all(x['id'] != it1['id'] for x in upcoming(7, today=ref)))
    chk('取消项不进提醒', all(x['id'] != it1['id'] for x in due_for_reminder(today=ref)))
    chk('取消未知 id 报错', _raises(lambda: cancel_entry('nope')))
    it0 = [x for x in load()['items'] if x['标题'] == '阅卷'][0]
    chk('已取消仍留在文件（软删）', len(load()['items']) == 4)

    # 改条目
    _, up1 = update_entry(it0['id'], apply=True, 日期='2026-09-18', 备注='改成 9-18')
    chk('改日期生效', up1['日期'] == '2026-09-18')
    chk('改后 id 重算', up1['id'] != it0['id'])
    chk('改后有更新留痕', bool(up1.get('更新于')))
    chk('改非法日期被拒', _raises(lambda: update_entry(up1['id'], 日期='2026-99-99')))
    chk('改未知 id 报错', _raises(lambda: update_entry('nope', 标题='x')))

    # 与 deadlines 数据隔离
    from datetime import datetime as _dt
    chk('数据文件独立于 deadlines.json',
        'schedule' in os.path.basename(store_path()))

    print('--- selftest 全部通过（%d 项，失败 %d）---' % (len(total), len(fails))
          if not fails else '--- FAIL %d / %d：%s ---' % (len(fails), len(total), fails))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description='日程（手工条目，独立于台账派生期限）')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('list'); p.add_argument('--days', type=int, default=30)
    p = sub.add_parser('due')
    p = sub.add_parser('add')
    p.add_argument('--title', required=True); p.add_argument('--date', required=True)
    p.add_argument('--case', default=''); p.add_argument('--time', default='')
    p.add_argument('--kind', default='其他'); p.add_argument('--lead', type=int, default=0)
    p.add_argument('--note', default=''); p.add_argument('--apply', action='store_true')
    p = sub.add_parser('cancel'); p.add_argument('--id', required=True)
    p.add_argument('--apply', action='store_true')
    p = sub.add_parser('update'); p.add_argument('--id', required=True)
    p.add_argument('--title', default=''); p.add_argument('--date', default='')
    p.add_argument('--case', default=''); p.add_argument('--time', default='')
    p.add_argument('--kind', default=''); p.add_argument('--lead', type=int, default=-1)
    p.add_argument('--note', default=''); p.add_argument('--apply', action='store_true')
    sub.add_parser('selftest')
    args = ap.parse_args()

    if args.cmd == 'list':
        items = upcoming(args.days)
        print('未来 %d 天日程 %d 条：' % (args.days, len(items)))
        for it in items:
            print('  %s %s | %s | %s | %s | 剩 %s 天' % (
                it['日期'], it.get('时间') or '--:--', it['类型'], it['标题'],
                it.get('案件') or '（无关联）', it['剩余天']))
        return 0
    if args.cmd == 'due':
        items = due_for_reminder()
        print('今日待提醒 %d 条' % len(items))
        for it in items:
            print('  %s | %s' % (it['标题'], '今天' if it['剩余天'] == 0
                                 else '%d 天后' % it['剩余天']))
        return 0
    if args.cmd == 'add':
        data, it = add_entry(args.title, args.date, args.case, args.time, args.kind,
                             args.lead, args.note, apply=args.apply)
        print('拟新增：%s | %s %s | %s' % (it['标题'], it['日期'], it.get('时间') or '',
                                          it['类型']))
        print('（dry-run，未写入；加 --apply）' if not args.apply else '已写入 %s' % store_path())
        return 0
    if args.cmd == 'cancel':
        data, it = cancel_entry(args.id, apply=args.apply)
        print('拟取消：%s | %s' % (it['标题'], it['日期']))
        print('（dry-run，未写入；加 --apply）' if not args.apply else '已写入（软删留痕）')
        return 0
    if args.cmd == 'update':
        fields = {k: v for k, v in
                  [('标题', args.title), ('日期', args.date), ('案件', args.case),
                   ('时间', args.time), ('类型', args.kind), ('备注', args.note)]
                  if v}
        if args.lead >= 0:
            fields['提前提醒'] = args.lead
        data, it = update_entry(args.id, apply=args.apply, **fields)
        print('拟修改：%s | %s %s' % (it['标题'], it['日期'], it.get('时间') or ''))
        print('（dry-run，未写入；加 --apply）' if not args.apply else '已写入 %s' % store_path())
        return 0
    if args.cmd == 'selftest':
        return selftest()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
