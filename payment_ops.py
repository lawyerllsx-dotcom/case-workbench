# -*- coding: utf-8 -*-
r"""payment_ops.py — 执行款回款记录（批 62，2026-10-13）

执行案「回款记录」**主存自建库**（`workbench.db` 表 `execution_payments`），
按需**同步到台账「执行款」sheet**（受控写入：锁检测 → 备份 → 写后审计，
走 `ledger_ops.sync_payments`）。

用户拍板（2026-10-13）：「工作台自建库 + 台账加页同步」——工作台是记录入口
（随时可记，不怕 WPS 占着台账），台账页是给带教看的镜像；**台账只增不回头**
（同步为追加镜像；在工作台删除记录不回删台账行——需要删行请在 WPS 手工删）。

数据流：
  记录（本模块，零台账触碰）→ 「同步到台账」→ ledger_ops.sync_payments
  （缺 sheet 自动受控建 → 追加未同步行 → 回填 ledger_row/synced_at）
"""
import os
from datetime import datetime

import tasks_ops

SHEET = '执行款'
SOURCES = ('法院划付', '对方支付', '其他')

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_payments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  case_folder TEXT NOT NULL,
  case_ref    TEXT DEFAULT '',
  pay_date    TEXT NOT NULL,
  amount      REAL NOT NULL,
  source      TEXT DEFAULT '',
  note        TEXT DEFAULT '',
  ledger_row  INTEGER DEFAULT NULL,
  synced_at   TEXT DEFAULT '',
  created_at  TEXT NOT NULL
);
"""


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def connect(path=None):
    r"""复用 tasks_ops 的连接与建库（同一 workbench.db）＋本表 schema。"""
    conn = tasks_ops.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _norm_date(v):
    try:
        return datetime.strptime(str(v or '').strip(), '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        raise ValueError('日期格式应为 YYYY-MM-DD：%s' % v)


def _norm_amount(v):
    try:
        a = round(float(str(v).replace(',', '').replace('，', '').strip()), 2)
    except (TypeError, ValueError):
        raise ValueError('金额必须是数字：%s' % v)
    if a <= 0 or a > 1e10:
        raise ValueError('金额须为正数且不超过百亿（明显异常请核对）：%s' % v)
    return a


def list_payments(case_folder=None, path=None):
    conn = connect(path)
    try:
        sql, args = 'SELECT * FROM execution_payments', []
        if case_folder:
            sql += ' WHERE case_folder=?'
            args.append(case_folder)
        sql += ' ORDER BY pay_date, id'
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def summary(case_folder, path=None):
    rows = list_payments(case_folder, path)
    return {'count': len(rows),
            'total': round(sum(r['amount'] for r in rows), 2),
            'pending': sum(1 for r in rows if not r['synced_at'])}


def add_payment(case_folder, pay_date, amount, source='', note='', case_ref='', path=None):
    r"""记一笔回款（写前备份 db）。返回新记录 id。"""
    case_folder = str(case_folder or '').strip()
    if not case_folder:
        raise ValueError('缺少案件夹名')
    pay_date = _norm_date(pay_date)
    amount = _norm_amount(amount)
    source = str(source or '').strip()[:20]
    note = str(note or '').strip()[:200]
    tasks_ops.backup_db('pay-add')
    conn = connect(path)
    try:
        cur = conn.execute(
            'INSERT INTO execution_payments (case_folder, case_ref, pay_date, amount,'
            ' source, note, created_at) VALUES (?,?,?,?,?,?,?)',
            (case_folder, str(case_ref or '').strip()[:120], pay_date, amount,
             source, note, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_payment(pid, path=None):
    r"""删一笔回款（写前备份 db；不回删台账已同步行）。"""
    tasks_ops.backup_db('pay-del')
    conn = connect(path)
    try:
        cur = conn.execute('DELETE FROM execution_payments WHERE id=?', (int(pid),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_synced(ids, start_row, path=None):
    r"""同步成功后回填台账行号（同批连续行，从 start_row 起顺排）。"""
    if not ids:
        return 0
    tasks_ops.backup_db('pay-sync')
    now = _now()
    conn = connect(path)
    try:
        for i, pid in enumerate(ids):
            conn.execute('UPDATE execution_payments SET ledger_row=?, synced_at=? WHERE id=?',
                         (int(start_row) + i, now, int(pid)))
        conn.commit()
        return len(ids)
    finally:
        conn.close()
