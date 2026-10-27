# -*- coding: utf-8 -*-
r"""tasks_ops.py — 法律工作台「自有数据层」：任务/待办（2026-09-12 批 12）

定位：台账（xlsx，与带教共享）是**案件/费用事实源**，本模块只存**台账没有的东西**——
      任务待办、以及后续的客户补充档案（联系方式/备注）。**不复制案件主数据**，避免双事实源。

存储：`case_workbench\data\workbench.db`（Python stdlib sqlite3，单文件、零依赖、本地）
备份：每次写前 copy 到 `D:\\备份\\08-工作台数据备份\\`，保留最近 10 份

CLI：
    python tasks_ops.py --list [--status 待办] [--q 关键词]
    python tasks_ops.py --add "标题" [--case 案件夹名] [--priority 重要] [--due 2026-09-20] [--note 说明]
    python tasks_ops.py --update 3 --status 已完成
    python tasks_ops.py --delete 3
    python tasks_ops.py --stats
    python tasks_ops.py --clients                            # 客户补充档案清单
    python tasks_ops.py --client-save "客户名" --contact 张经理 --phone 138… --wechat … --client-note …
    python tasks_ops.py --selftest          # 临时库，不碰真实数据
"""
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
# 路径可用环境变量覆盖（供测试注入临时库/临时备份目录，避免污染真实数据）
DB_PATH = os.environ.get('WORKBENCH_DB') or os.path.join(DATA_DIR, 'workbench.db')
BACKUP_DIR = os.environ.get('WORKBENCH_BACKUP_DIR') or r'D:\备份\08-工作台数据备份'
KEEP_BACKUPS = 10

STATUSES = ('待办', '进行中', '已完成')
PRIORITIES = ('普通', '重要', '紧急')
PATCHABLE = ('title', 'case_ref', 'status', 'priority', 'due_date', 'note')

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  case_ref   TEXT DEFAULT '',
  status     TEXT NOT NULL DEFAULT '待办',
  priority   TEXT NOT NULL DEFAULT '普通',
  due_date   TEXT DEFAULT '',
  note       TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  done_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_case   ON tasks(case_ref);

CREATE TABLE IF NOT EXISTS clients_ext (
  client_key TEXT PRIMARY KEY,
  contact    TEXT DEFAULT '',
  phone      TEXT DEFAULT '',
  wechat     TEXT DEFAULT '',
  note       TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


# ------------------------------------------------------------------ 基础

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def connect(path=None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)  # i2:write —— connect() 兼 executescript(SCHEMA) 建表，本就是写工具
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def backup_db(tag='auto'):
    """写前备份（保留最近 KEEP_BACKUPS 份）；DB 不存在则跳过"""
    if not os.path.isfile(DB_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, 'workbench-%s-%s.db'
                       % (datetime.now().strftime('%Y%m%d-%H%M%S'), tag))
    shutil.copy2(DB_PATH, dst)
    olds = sorted(f for f in os.listdir(BACKUP_DIR)
                  if f.startswith('workbench-') and f.endswith('.db'))
    for f in olds[:-KEEP_BACKUPS]:
        try:
            os.unlink(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return dst


def _norm_due(v):
    v = (v or '').strip()
    if not v:
        return ''
    try:
        return datetime.strptime(v, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        raise ValueError('截止时间格式应为 YYYY-MM-DD：%s' % v)


def _check(status=None, priority=None):
    if status is not None and status not in STATUSES:
        raise ValueError('状态只能是 %s' % '/'.join(STATUSES))
    if priority is not None and priority not in PRIORITIES:
        raise ValueError('优先级只能是 %s' % '/'.join(PRIORITIES))


# ------------------------------------------------------------------ CRUD

def list_tasks(status=None, q=None, case=None, due=None, path=None):
    """due: all|today|week|overdue|none；status: 全部/待办/进行中/已完成"""
    conn = connect(path)
    try:
        sql, args = 'SELECT * FROM tasks WHERE 1=1', []
        if status and status != '全部':
            sql += ' AND status=?'
            args.append(status)
        if q:
            sql += ' AND (title LIKE ? OR note LIKE ? OR case_ref LIKE ?)'
            args += ['%%%s%%' % q] * 3
        if case:
            sql += ' AND case_ref=?'
            args.append(case)
        today = date.today()
        if due == 'today':
            sql += " AND status!='已完成' AND due_date!='' AND due_date<=?"
            args.append(today.strftime('%Y-%m-%d'))
        elif due == 'overdue':
            sql += " AND status!='已完成' AND due_date!='' AND due_date<?"
            args.append(today.strftime('%Y-%m-%d'))
        elif due == 'week':
            sql += " AND status!='已完成' AND due_date>? AND due_date<=?"
            args += [today.strftime('%Y-%m-%d'),
                     (today + timedelta(days=7)).strftime('%Y-%m-%d')]
        elif due == 'none':
            sql += " AND status!='已完成' AND due_date=''"

        sql += (" ORDER BY CASE status WHEN '进行中' THEN 0 WHEN '待办' THEN 1 ELSE 2 END,"
                " CASE priority WHEN '紧急' THEN 0 WHEN '重要' THEN 1 ELSE 2 END,"
                " CASE WHEN due_date='' THEN 1 ELSE 0 END, due_date, id DESC")
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        today_s = today.strftime('%Y-%m-%d')
        for r in rows:
            d = r.get('due_date') or ''
            r['剩余天'] = None
            if d and r['status'] != '已完成':
                r['剩余天'] = (datetime.strptime(d, '%Y-%m-%d').date() - today).days
            r['逾期'] = bool(d and r['status'] != '已完成' and d < today_s)
            r['今日'] = bool(d == today_s)
        return rows
    finally:
        conn.close()


def get_task(tid, path=None):
    conn = connect(path)
    try:
        r = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def add_task(title, case_ref='', status='待办', priority='普通', due_date='', note='',
             path=None, do_backup=True):
    title = (title or '').strip()
    if not title:
        raise ValueError('任务标题不能为空')
    _check(status, priority)
    due_date = _norm_due(due_date)
    if do_backup:
        backup_db('add')
    now = _now()
    conn = connect(path)
    try:
        cur = conn.execute(
            'INSERT INTO tasks(title, case_ref, status, priority, due_date, note,'
            ' created_at, updated_at, done_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (title, case_ref or '', status, priority, due_date, note or '', now, now,
             now if status == '已完成' else ''))
        conn.commit()
        return get_task(cur.lastrowid, path)
    finally:
        conn.close()


def update_task(tid, patch, path=None, do_backup=True):
    task = get_task(tid, path)
    if not task:
        raise ValueError('任务不存在：id=%s' % tid)
    fields, vals = [], []
    for k, v in (patch or {}).items():
        if k not in PATCHABLE:
            raise ValueError('不可修改字段：%s（可改：%s）' % (k, '/'.join(PATCHABLE)))
        if k == 'due_date':
            v = _norm_due(v)
        if k == 'title':
            v = (v or '').strip()
            if not v:
                raise ValueError('任务标题不能为空')
        if k == 'status':
            _check(status=v)
            if v == '已完成':
                fields.append('done_at=?')
                vals.append(task.get('done_at') or _now())
            elif task.get('status') == '已完成':
                fields.append('done_at=?')
                vals.append('')
        if k == 'priority':
            _check(priority=v)
        fields.append('%s=?' % k)
        vals.append(v)
    if not fields:
        return task
    if do_backup:
        backup_db('update')
    fields.append('updated_at=?')
    vals.append(_now())
    vals.append(tid)
    conn = connect(path)
    try:
        conn.execute('UPDATE tasks SET %s WHERE id=?' % ','.join(fields), vals)
        conn.commit()
        return get_task(tid, path)
    finally:
        conn.close()


def delete_task(tid, path=None, do_backup=True):
    task = get_task(tid, path)
    if not task:
        raise ValueError('任务不存在：id=%s' % tid)
    if do_backup:
        backup_db('delete')
    conn = connect(path)
    try:
        conn.execute('DELETE FROM tasks WHERE id=?', (tid,))
        conn.commit()
        return {'ok': True, 'deleted': task}
    finally:
        conn.close()


# ------------------------------------------------------------------ 客户补充档案（clients_ext）
# 口径：客户「是谁、有几件案、有无未完成任务」全部从台账派生（聚合视图，不落库）；
#       这里**只存台账没有的东西**——联系人/电话/微信/备注，避免双事实源。
# 纪律：只 upsert，**不删除**（字段清空 = 记录留空，列表自动隐藏）；写前自动备份 DB。

CLIENT_FIELDS = ('contact', 'phone', 'wechat', 'note')
CLIENT_LABELS = {'contact': '联系人', 'phone': '电话', 'wechat': '微信', 'note': '备注'}
CLIENT_FIELD_MAX = 200


def get_client_ext(key, path=None):
    conn = connect(path)
    try:
        r = conn.execute('SELECT * FROM clients_ext WHERE client_key=?',
                         ((key or '').strip(),)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def list_clients_ext(path=None):
    """→ {client_key: {contact,phone,wechat,note,updated_at}}；全空的记录不返回"""
    conn = connect(path)
    try:
        out = {}
        for r in conn.execute('SELECT * FROM clients_ext ORDER BY client_key'):
            d = dict(r)
            k = d.pop('client_key', '')
            if k and any(str(d.get(f) or '').strip() for f in CLIENT_FIELDS):
                out[k] = d
        return out
    finally:
        conn.close()


def set_client_ext(key, patch, path=None, do_backup=True):
    """新增/更新客户补充档案（按 client_key upsert）"""
    key = (key or '').strip()
    if not key:
        raise ValueError('客户名不能为空')
    patch = dict(patch or {})
    bad = [k for k in patch if k not in CLIENT_FIELDS]
    if bad:
        raise ValueError('不可写字段：%s（可写：%s）' % ('/'.join(bad), '/'.join(CLIENT_FIELDS)))
    for k, v in patch.items():
        if len(str(v or '')) > CLIENT_FIELD_MAX:
            raise ValueError('%s 过长（上限 %d 字）' % (CLIENT_LABELS.get(k, k), CLIENT_FIELD_MAX))
    cur = get_client_ext(key, path) or {}
    vals = {f: str(patch.get(f, cur.get(f) or '') or '').strip() for f in CLIENT_FIELDS}
    if do_backup:
        backup_db('client')
    now = _now()
    conn = connect(path)
    try:
        conn.execute(
            'INSERT INTO clients_ext(client_key, contact, phone, wechat, note, updated_at)'
            ' VALUES(?,?,?,?,?,?)'
            ' ON CONFLICT(client_key) DO UPDATE SET contact=excluded.contact,'
            ' phone=excluded.phone, wechat=excluded.wechat, note=excluded.note,'
            ' updated_at=excluded.updated_at',
            (key, vals['contact'], vals['phone'], vals['wechat'], vals['note'], now))
        conn.commit()
        return get_client_ext(key, path)
    finally:
        conn.close()


# ---------------------------------------------------- 通用 meta 键值（批 38）
#
# 工作台「自建库」放的是台账没有的东西。散件「关注/待处理」标记属于这一类：
# **只写 workbench.db，绝不碰散件文件**。key 形如 `scatter:<相对路径>`。

def meta_set(key, value, path=None, do_backup=True):
    """写一条 meta（value 存 JSON 文本）；写前自动备份 db。"""
    key = str(key or '').strip()
    if not key:
        raise ValueError('key 不能为空')
    if do_backup:
        backup_db('meta')
    conn = connect(path)
    try:
        conn.execute('INSERT INTO meta(k, v) VALUES(?,?) '
                     'ON CONFLICT(k) DO UPDATE SET v=excluded.v',
                     (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
        return get_meta(key, path)
    finally:
        conn.close()


def get_meta(key, path=None):
    conn = connect(path)
    try:
        r = conn.execute('SELECT v FROM meta WHERE k=?', (str(key),)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    try:
        return json.loads(r['v'])
    except (TypeError, ValueError):
        return None


def meta_del(key, path=None, do_backup=True):
    """删一条 meta；返回受影响行数（0 = 本来就没有）。"""
    if do_backup:
        backup_db('meta')
    conn = connect(path)
    try:
        cur = conn.execute('DELETE FROM meta WHERE k=?', (str(key),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def meta_map(prefix='', path=None):
    """读前缀匹配的 meta（key 去掉前缀 → 值 dict）。"""
    conn = connect(path)
    try:
        rows = conn.execute('SELECT k, v FROM meta WHERE k LIKE ?',
                            (str(prefix) + '%',)).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        k = str(r['k'])[len(prefix):]
        try:
            out[k] = json.loads(r['v'])
        except (TypeError, ValueError):
            out[k] = {}
    return out


# ------------------------------------------------------------------ 统计

def stats(path=None):
    today = date.today()
    t = today.strftime('%Y-%m-%d')
    w = (today + timedelta(days=7)).strftime('%Y-%m-%d')
    conn = connect(path)
    try:
        def one(sql, args=()):
            return conn.execute(sql, args).fetchone()[0]
        return {
            'total': one('SELECT COUNT(*) FROM tasks'),
            'open': one("SELECT COUNT(*) FROM tasks WHERE status!='已完成'"),
            'doing': one("SELECT COUNT(*) FROM tasks WHERE status='进行中'"),
            'done': one("SELECT COUNT(*) FROM tasks WHERE status='已完成'"),
            'overdue': one("SELECT COUNT(*) FROM tasks WHERE status!='已完成'"
                           " AND due_date!='' AND due_date<?", (t,)),
            'today': one("SELECT COUNT(*) FROM tasks WHERE status!='已完成'"
                         " AND due_date!='' AND due_date<=?", (t,)),
            'week': one("SELECT COUNT(*) FROM tasks WHERE status!='已完成'"
                        " AND due_date>? AND due_date<=?", (t, w)),
            'none': one("SELECT COUNT(*) FROM tasks WHERE status!='已完成' AND due_date=''"),
        }
    finally:
        conn.close()


# ------------------------------------------------------------------ 自测

def selftest():
    import tempfile
    checks = []

    def ck(n, c):
        checks.append((n, bool(c)))

    tmp = os.path.join(tempfile.mkdtemp(prefix='tasks_selftest_'), 'wb.db')
    try:
        ck('初始为空', stats(tmp)['total'] == 0)
        a = add_task('阅卷：三明海大案材料', case_ref='A海大\\三明海大', priority='重要',
                     due_date=(date.today() - timedelta(days=1)).strftime('%Y-%m-%d'),
                     path=tmp, do_backup=False)
        b = add_task('写起诉状', case_ref='A海大\\三明海大', priority='紧急',
                     due_date=(date.today() + timedelta(days=3)).strftime('%Y-%m-%d'),
                     path=tmp, do_backup=False)
        add_task('交差旅票', path=tmp, do_backup=False)               # 无期限
        ck('新增返回 id', isinstance(a['id'], int) and b['id'] != a['id'])
        ck('总数=3', stats(tmp)['total'] == 3)
        ck('逾期=1', stats(tmp)['overdue'] == 1)
        ck('今日待办含逾期', stats(tmp)['today'] == 1)
        ck('7 日内=1', stats(tmp)['week'] == 1)
        ck('未排期=1', stats(tmp)['none'] == 1)

        # 状态流转
        u = update_task(b['id'], {'status': '进行中'}, path=tmp, do_backup=False)
        ck('改状态=进行中', u['status'] == '进行中')
        ck('未完成=3', stats(tmp)['open'] == 3)
        u2 = update_task(b['id'], {'status': '已完成'}, path=tmp, do_backup=False)
        ck('完成写 done_at', bool(u2['done_at']))
        ck('完成后不计逾期', stats(tmp)['overdue'] == 1 and stats(tmp)['week'] == 0)
        u3 = update_task(b['id'], {'status': '进行中'}, path=tmp, do_backup=False)
        ck('撤回完成清空 done_at', u3['done_at'] == '')

        # 过滤
        ck('按状态筛', len(list_tasks(status='进行中', path=tmp)) == 1)
        ck('按关键词筛', len(list_tasks(q='起诉', path=tmp)) == 1)
        ck('按案件筛', len(list_tasks(case='A海大\\三明海大', path=tmp)) == 2)
        ck('due=overdue 筛', len(list_tasks(due='overdue', path=tmp)) == 1)
        ck('排序：紧急优先', list_tasks(path=tmp)[0]['id'] == b['id'])

        # 校验
        def bad(fn):
            try:
                fn()
                return False
            except ValueError:
                return True
        ck('拒绝非法状态', bad(lambda: update_task(a['id'], {'status': '瞎写'}, path=tmp,
                                                  do_backup=False)))
        ck('拒绝非法优先级', bad(lambda: update_task(a['id'], {'priority': '超级'}, path=tmp,
                                                   do_backup=False)))
        ck('拒绝非法日期', bad(lambda: add_task('x', due_date='2026/1/1', path=tmp,
                                              do_backup=False)))
        ck('拒绝空标题', bad(lambda: add_task('   ', path=tmp, do_backup=False)))
        ck('拒绝改非法字段', bad(lambda: update_task(a['id'], {'id': 9}, path=tmp,
                                                  do_backup=False)))

        # 删除
        before = stats(tmp)['total']
        d = delete_task(a['id'], path=tmp, do_backup=False)
        ck('删除成功', d['ok'] and stats(tmp)['total'] == before - 1)
        ck('删不存在的报错', bad(lambda: delete_task(99999, path=tmp, do_backup=False)))

        # ---- 客户补充档案（批 13） ----
        ck('初始无客户档案', list_clients_ext(tmp) == {})
        e = set_client_ext('厦门启航优卓', {'contact': '张经理', 'phone': '13800000000'},
                           path=tmp, do_backup=False)
        ck('写入客户档案', e['contact'] == '张经理' and e['phone'] == '13800000000')
        ck('列表可见', '厦门启航优卓' in list_clients_ext(tmp))
        e2 = set_client_ext('厦门启航优卓', {'wechat': 'qihang88'}, path=tmp, do_backup=False)
        ck('局部更新不丢原字段', e2['contact'] == '张经理' and e2['wechat'] == 'qihang88')
        ck('未知客户返回 None', get_client_ext('没这个客户', tmp) is None)
        ck('拒绝非法字段', bad(lambda: set_client_ext('厦门启航优卓', {'id': 1}, path=tmp,
                                                   do_backup=False)))
        ck('拒绝超长', bad(lambda: set_client_ext('厦门启航优卓', {'note': 'x' * 999},
                                                path=tmp, do_backup=False)))
        ck('拒绝空客户名', bad(lambda: set_client_ext('  ', {'note': 'x'}, path=tmp,
                                                   do_backup=False)))
        set_client_ext('厦门启航优卓', {f: '' for f in CLIENT_FIELDS}, path=tmp, do_backup=False)
        ck('字段全清空后列表自动隐藏（记录不删除）',
            '厦门启航优卓' not in list_clients_ext(tmp)
            and get_client_ext('厦门启航优卓', tmp) is not None)
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)

    bad_list = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print('[%s] %s' % ('OK' if ok else 'FAIL', n))
    print('tasks_ops selftest: %d/%d 通过' % (len(checks) - len(bad_list), len(checks)))
    return 1 if bad_list else 0


# ------------------------------------------------------------------ CLI

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass
    if '--selftest' in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description='工作台任务数据层')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--status', default=None)
    ap.add_argument('--q', default=None)
    ap.add_argument('--case', default=None)
    ap.add_argument('--due', default=None, choices=['today', 'week', 'overdue', 'none'])
    ap.add_argument('--add', default=None, metavar='标题')
    ap.add_argument('--update', type=int, default=None, metavar='ID')
    ap.add_argument('--delete', type=int, default=None, metavar='ID')
    ap.add_argument('--title', default=None)
    ap.add_argument('--priority', default=None)
    ap.add_argument('--note', default=None)
    ap.add_argument('--due-date', dest='due_date', default=None)
    ap.add_argument('--clients', action='store_true', help='列出客户补充档案')
    ap.add_argument('--client-save', dest='client_save', default=None, metavar='客户名')
    ap.add_argument('--contact', default=None)
    ap.add_argument('--phone', default=None)
    ap.add_argument('--wechat', default=None)
    ap.add_argument('--client-note', dest='client_note', default=None)
    args = ap.parse_args()

    if args.selftest:
        return 0
    if args.clients:
        ext = list_clients_ext()
        print('客户补充档案：%d 家' % len(ext))
        for k, v in ext.items():
            print('  %-22s %s %s %s %s' % (k, v.get('contact', ''), v.get('phone', ''),
                                           v.get('wechat', ''), (v.get('note') or '')[:30]))
        return 0
    if args.client_save:
        patch = {k: v for k, v in (('contact', args.contact), ('phone', args.phone),
                                   ('wechat', args.wechat), ('note', args.client_note))
                 if v is not None}
        if not patch:
            print('未给任何字段（--contact/--phone/--wechat/--client-note）')
            return 2
        r = set_client_ext(args.client_save, patch)
        print('已保存', r['client_key'], r['contact'], r['phone'], r['wechat'])
        return 0
    if args.add:
        r = add_task(args.add, case_ref=args.case or '', priority=args.priority or '普通',
                     due_date=args.due_date or '', note=args.note or '')
        print('已新增', r['id'], r['title'])
        return 0
    if args.update:
        patch = {k: v for k, v in (('status', args.status), ('priority', args.priority),
                                   ('due_date', args.due_date), ('title', args.title),
                                   ('case_ref', args.case), ('note', args.note)) if v is not None}
        r = update_task(args.update, patch)
        print('已更新', r['id'], r['title'], r['status'], r['priority'], r['due_date'])
        return 0
    if args.delete:
        r = delete_task(args.delete)
        print('已删除', r['deleted']['id'], r['deleted']['title'])
        return 0
    if args.stats:
        for k, v in stats().items():
            print('  %-8s %s' % (k, v))
        return 0
    rows = list_tasks(status=args.status, q=args.q, case=args.case, due=args.due)
    print('共 %d 条' % len(rows))
    for r in rows:
        print('  #%-3s [%s/%s] %-28s %s %s  %s'
              % (r['id'], r['status'], r['priority'], r['title'][:28],
                 r['due_date'] or '        ', '逾期' if r['逾期'] else '    ',
                 r['case_ref'][:40]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
