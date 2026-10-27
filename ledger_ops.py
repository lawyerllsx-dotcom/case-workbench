#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ledger_ops.py — 案件管理台账读写库（法律工作台数据层 · 单一实现）

定位：Web 操作台与煤球会话**共用**的台账读写库（Sol 原则：同一逻辑只一份实现）。
纪律（写死在代码里）：
  - 一切写操作默认 dry-run，必须显式 --apply 才落盘
  - 写前自动备份（默认落 <台账备份目录>\，可用 LEDGER_BACKUP_ROOT 覆盖）
  - I 列（当前进度）更新**必须先读旧值追加进 J 列**（进度日志）——2026-08-12 事故教训
  - 写前检测 WPS/Office 文件锁，命中即拒绝（防云端占写冲突）
  - **绝不删除任何内容**：只追加/替换目标单元格

⚠️ openpyxl 保存为整本重写：备份先行兜底；图片/图表/条件格式等非单元格对象可能不被保留，
   首次 --apply 前建议在 WPS 中确认用印版本可回退（备份文件同格式可直接替换回去）。

依赖：openpyxl → 用受管 venv 跑：
  <python>

用法：
  python ledger_ops.py check                          # 路径/锁/表结构体检
  python ledger_ops.py list                           # 各 sheet 案件数与状态分布
  python ledger_ops.py show --sheet 示例案件 [--limit 5]
  python ledger_ops.py progress --sheet 示例案件 --row 4 --text "09.12：已寄要素式起诉状"          # dry-run
  python ledger_ops.py progress --sheet 示例案件 --row 4 --text "..." --apply
  python ledger_ops.py probe                          # 写环境探测（锁/独占/目录可写）
  python ledger_ops.py pending [--retry ID|all]       # 查看/重试「待同步队列」
  python ledger_ops.py fix-j [--sheet X] [--row N] [--apply]   # 受控修复 J 列「双重日期」条目
  python ledger_ops.py restore-row --backup <备份xlsx> --sheet X --row N [--apply]  # 行级精确回退
  python ledger_ops.py backup [--apply]
  python ledger_ops.py selftest                       # 临时目录自测（不碰真本）

批次 13（2026-09-12）新增「受控回写」三件套（供 Web 操作台用）：
  1. probe_write_env()          写前环境探测：锁文件 / 独占占用 / 目录可写 → 不可写即不写
  2. row_snapshot()/check_external_change()  行级快照比对 → 台账被外部改动时**报冲突不静默覆盖**
  3. queue_add()/queue_retry()  「待同步队列」：占用导致写不进时排队留痕，页面提示后可重试

退出码：0=正常 2=输入/锁/冲突 3=执行异常
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

if os.environ.get('_WB_STDOUT_UTF8') != '1':      # 幂等：多模块同进程重复包裹会关掉缓冲区
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VERSION = '1.1.5'

import config as _config        # 开源版：路径 / 列映射 / 状态池全部外置为配置

DEFAULT_LEDGER = _config.path('paths.ledger', env='LEDGER_PATH')
DEFAULT_BACKUP_ROOT = _config.path('paths.ledger_backup_root', env='LEDGER_BACKUP_ROOT')

DATA_START_ROW = int(_config.get('ledger.data_start_row', 4) or 4)
TITLE_ROW, GROUP_ROW, FIELD_ROW = 1, 2, 3

# 列名 → 列序号（不同台账列序不同，改 workbench.config.json 的 ledger.cols_case 即可）
COLS = dict(_config.get('ledger.cols_case') or {})
if not COLS:
    raise RuntimeError(
        '缺少台账列映射：请检查 workbench.config.example.json 是否存在，'
        '或复制它为 workbench.config.json 后填写 ledger.cols_case。')
COL_I, COL_J, COL_K = COLS['当前进度'], COLS['进度日志'], COLS['最新状态']
SHEETS_CASE = list(_config.get('ledger.sheets_case') or [])   # 有 I→J 纪律的案件 sheet
SHEET_LETTER = _config.get('ledger.sheet_letter', '律师函')     # 结构不同（A–J），本库只读
LETTER_COLS = dict(_config.get('ledger.cols_letter') or {})
COLS_BY_SHEET = {SHEET_LETTER: LETTER_COLS}
# 律师函表可受控回写的两列（备注列属他人内容，绝不写）
LETTER_WRITE_COLS = {'签收状态': LETTER_COLS.get('签收状态'),
                     '后续跟进': LETTER_COLS.get('后续跟进')}
SHEET_CONTRACT = _config.get('ledger.sheet_contract', '合同审查')   # 结构同律师函，本库只读
CONTRACT_COLS = dict(_config.get('ledger.cols_contract') or {})
COLS_BY_SHEET[SHEET_CONTRACT] = CONTRACT_COLS   # 逐键注册：重赋值会丢律师函映射
SHEET_PAYMENT = _config.get('ledger.sheet_payment', '执行款')     # 执行款回款镜像（7 列）
PAYMENT_COLS = dict(_config.get('ledger.cols_payment') or {})
COLS_BY_SHEET[SHEET_PAYMENT] = PAYMENT_COLS

STATUS_POOL = list(_config.get('ledger.status_pool') or [])
# 历史遗留写法：仅为「体检不误报」保留
STATUS_POOL_OBSERVED = list(_config.get('ledger.status_pool_observed') or [])
STATUS_POOL_BASE = STATUS_POOL + STATUS_POOL_OBSERVED
# 写 K 列（最新状态）时唯一允许的取值池
STATUS_WRITE_POOL = list(STATUS_POOL)
CATEGORY_POOL = list(_config.get('ledger.category_pool') or [])

LOCK_GLOBS = ['~$%s', '%s.lck', '.~lock.%s#']


# ---------------------------------------------------------------- 路径与环境

def ledger_path():
    return os.environ.get('LEDGER_PATH', DEFAULT_LEDGER)


def backup_root():
    return os.environ.get('LEDGER_BACKUP_ROOT', DEFAULT_BACKUP_ROOT)


def _check_inputs():
    p = ledger_path()
    if not os.path.isfile(p):
        print('输入错误：台账文件不存在 → %s' % p)
        sys.exit(2)
    return p


# ---------------------------------------------------------------- 锁与备份

def detect_lock(path=None):
    """返回阻塞写操作的锁文件列表（WPS/Office 打开时会产生 ~$ 前缀临时文件）"""
    path = path or ledger_path()
    d, name = os.path.dirname(path), os.path.basename(path)
    found = []
    for g in LOCK_GLOBS:
        cand = os.path.join(d, g % name)
        if os.path.exists(cand):
            found.append(cand)
    # 同目录若存在同名 ".xlsx#" 或 WPS 云同步中转文件也一并报出
    for extra in ('%s.sync' % name, '%s.tmp' % name):
        cand = os.path.join(d, extra)
        if os.path.exists(cand):
            found.append(cand)
    return found


def backup(apply=False):
    """备份台账到 backup_root()；返回目标路径（dry-run 时只返回拟用路径）

    命名到秒；同一秒内的多次写（如队列连续重试）自动加 -2/-3 后缀 →
    **每次写入都留下各自的前置状态，互不覆盖**（批 13 修正）。
    """
    src = _check_inputs()
    root = backup_root()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(root, '案件管理台账模版表-备份-%s.xlsx' % stamp)
    if os.path.exists(dst):
        i = 2
        while os.path.exists(os.path.join(
                root, '案件管理台账模版表-备份-%s-%d.xlsx' % (stamp, i))):
            i += 1
        dst = os.path.join(root, '案件管理台账模版表-备份-%s-%d.xlsx' % (stamp, i))
    if not apply:
        return dst, False
    os.makedirs(root, exist_ok=True)
    shutil.copy2(src, dst)
    man = os.path.join(root, 'manifest.md')
    line = '| %s | %s | %s | %d |\n' % (
        datetime.now().strftime('%Y-%m-%d %H:%M'), os.path.basename(dst),
        os.path.basename(src), os.path.getsize(dst))
    if not os.path.isfile(man):
        with open(man, 'w', encoding='utf-8') as f:
            f.write('# 台账备份台账\n\n> 由 ledger_ops.py backup 自动追加。恢复方式：'
                    '把备份文件复制回台账目录并改名为 案件管理台账模版表.xlsx。\n\n'
                    '| 备份时间 | 备份文件 | 源文件 | 字节 |\n|---|---|---|---|\n')
    with open(man, 'a', encoding='utf-8') as f:
        f.write(line)
    return dst, True


# ------------------------------------------------- 批 13：写后审计（只读）

def snapshot_cells(path=None):
    """全簿「非空格值」快照 {(sheet,row,col): value}（写前/写后对比用；只读）"""
    path = path or ledger_path()
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        snap = {}
        for ws in wb.worksheets:
            for ri, row in enumerate(ws.iter_rows(values_only=True), start=1):
                for ci, v in enumerate(row or (), start=1):
                    if v is not None and v != '':
                        snap[(ws.title, ri, ci)] = v
        return snap
    finally:
        wb.close()


def audit_cells(before, after, allowed):
    """对比两份快照，返回「allowed 之外发生变化」的格子 [(sheet,row,col,旧,新)]

    用途：每次写台账后自动审计——**除目标格外应当零变化**；发现意外改动立即报警
    （防列映射写错、防 openpyxl 静默丢/改内容）。纯函数，便于回归。
    """
    out = []
    for k in sorted(set(before) | set(after)):
        if k in allowed:
            continue
        a, b = before.get(k), after.get(k)
        if (a if a is not None else '') != (b if b is not None else ''):
            out.append((k[0], k[1], k[2], str(a)[:40], str(b)[:40]))
    return out


# ------------------------------------------------- 批 13：写环境探测 / 冲突检测 / 待同步队列

# 「日期前缀叠了两层」= MM.DD：MM.DD：正文（旧值自带日期被回迁逻辑又加了一次）
DOUBLE_DATE_RE = re.compile(r'^(\d{2}\.\d{2})[：:]\s*(\d{2}\.\d{2})[：:]\s*(.+)$')


def _undouble_line(text):
    """把 `09.12：09.04：正文` 规范成 `09.04：正文`（保留旧值自带的日期，去掉回迁时叠加的）"""
    m = DOUBLE_DATE_RE.match((text or '').strip())
    if not m:
        return None
    return '%s：%s' % (m.group(2), m.group(3).strip())


def scan_double_date(sheet=None, row=None):
    """只读扫描 J 列「双重日期」条目 → [{sheet,row,序号,案件,J_old,J_new,changes:[(旧行,新行)]}]"""
    hits = []
    for s in ([sheet] if sheet else SHEETS_CASE):
        try:
            rows = read_rows(s)
        except KeyError:            # 表不存在（测试用精简台账/表被改名）→ 跳过
            continue
        for r in rows:
            if row and r['_row'] != row:
                continue
            old = str(r.get('进度日志') or '')
            if not old.strip():
                continue
            lines = old.splitlines()
            new_lines, changes = [], []
            for ln in lines:
                fx = _undouble_line(ln)
                if fx is not None and fx != ln.strip():
                    new_lines.append(fx)
                    changes.append((ln.strip(), fx))
                else:
                    new_lines.append(ln)
            if changes:
                hits.append({'sheet': s, 'row': r['_row'],
                             '序号': str(r.get('序号') or ''),
                             '案件': str(r.get('案件') or ''),
                             'J_old': old, 'J_new': '\n'.join(new_lines),
                             'changes': changes})
    return hits

def pending_path():
    """待同步队列文件（工作台自建数据，非台账）"""
    p = os.environ.get('LEDGER_PENDING_PATH')
    if p:
        return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                        'pending_sync.json')


class LedgerBusy(Exception):
    """台账当前不可写（WPS 打开 / 云盘同步中 / 目录只读）→ 可重试，不丢意图"""

    def __init__(self, probe, queued=None):
        self.probe = probe or {}
        self.queued = queued
        reasons = '；'.join(self.probe.get('reasons') or ['未知占用'])
        super().__init__('台账当前不可写：%s' % reasons)


class LedgerConflict(Exception):
    """台账内容已被外部改动（与页面看到的版本不一致）→ 人工确认，绝不静默覆盖"""

    def __init__(self, check):
        self.check = check or {}
        super().__init__('台账已被外部改动：%s'
                         % '；'.join(d.get('字段', '?') for d in self.check.get('hard', [])))


def probe_write_env(path=None):
    """写环境探测（**只读**，不改任何文件）：锁文件 / 独占占用 / 目录可写"""
    path = path or ledger_path()
    out = {'path': path, 'exists': os.path.isfile(path), 'writable': False,
           'dir_writable': False, 'locks': [], 'reasons': [], 'ok': False,
           'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    if not out['exists']:
        out['reasons'].append('台账文件不存在')
        return out
    out['locks'] = detect_lock(path)
    if out['locks']:
        out['reasons'].append('检测到 WPS/Office 锁文件：%s'
                              % '、'.join(os.path.basename(x) for x in out['locks']))
    try:
        with open(path, 'r+b'):        # 独占探测：WPS 打开时 Windows 会拒
            pass
        out['writable'] = True
    except PermissionError:
        out['reasons'].append('文件被其他程序独占（WPS/Excel 可能正打开该表）')
    except OSError as e:                                      # noqa: BLE001
        out['reasons'].append('文件无法打开：%r' % e)
    d = os.path.dirname(path)
    out['dir_writable'] = bool(d) and os.access(d, os.W_OK)
    if not out['dir_writable']:
        out['reasons'].append('台账所在目录不可写（云盘只读/权限不足）')
    out['ok'] = out['writable'] and out['dir_writable'] and not out['locks']
    if out['ok']:
        out['reasons'] = []
    return out


def row_snapshot(sheet, row_idx):
    """行级快照（外部改动检测基准）：行内关键值 + 文件指纹（只读）"""
    rows = [r for r in read_rows(sheet) if r['_row'] == row_idx]
    if not rows:
        raise ValueError('未找到数据行 row=%s（%s）' % (row_idx, sheet))
    r = rows[0]
    st = os.stat(ledger_path())
    return {
        'sheet': sheet, 'row': row_idx,
        '序号': str(r.get('序号') or ''), '案件': str(r.get('案件') or ''),
        'I': str(r.get('当前进度') or ''), 'J': str(r.get('进度日志') or ''),
        'K': str(r.get('最新状态') or ''),
        'file_mtime': int(st.st_mtime), 'file_size': st.st_size,
    }


SNAP_FIELDS = (('I', '当前进度'), ('J', '进度日志'), ('K', '最新状态'),
               ('序号', '序号'), ('案件', '案件'))


def check_external_change(expect, sheet=None, row=None):
    """页面快照 vs 台账现状。hard=行内容被外部改动（阻断）；soft=文件时间变了但内容没变（仅提示）"""
    sheet = sheet or expect.get('sheet')
    row = row or expect.get('row')
    now = row_snapshot(sheet, row)
    hard, soft = [], []
    for k, label in SNAP_FIELDS:
        a, b = str(expect.get(k) or ''), str(now.get(k) or '')
        if a != b:
            hard.append({'字段': label, '页面看到': a, '台账现有': b})
    if expect.get('file_mtime') and int(now['file_mtime']) != int(expect['file_mtime']):
        soft.append({'字段': '文件修改时间',
                     '页面看到': datetime.fromtimestamp(expect['file_mtime']).strftime('%Y-%m-%d %H:%M:%S'),
                     '台账现有': datetime.fromtimestamp(now['file_mtime']).strftime('%Y-%m-%d %H:%M:%S')})
    return {'hard': hard, 'soft': soft, 'now': now, 'same': not hard}


# ---- 待同步队列（占用导致写不进时排队；工作台自建数据，写前不备份台账）

def queue_load():
    p = pending_path()
    if not os.path.isfile(p):
        return {'items': []}
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get('items'), list):
            return {'items': []}
        return d
    except (OSError, ValueError):
        return {'items': []}


def _queue_save(d):
    p = pending_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def queue_add(op, error=''):
    """入队；返回该条目（含 id）"""
    d = queue_load()
    now = datetime.now()
    item = dict(op)
    item['id'] = now.strftime('%Y%m%d-%H%M%S') + '-%02d' % (len(d['items']) + 1)
    item['created_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
    item['tries'] = 0
    item['state'] = '待同步'
    item['last_error'] = error
    d['items'].append(item)
    _queue_save(d)
    return item


def queue_drop(item_id):
    """移除队列条目（只动工作台自建队列文件，不动台账）→ 返回移除数"""
    d = queue_load()
    keep = [i for i in d['items'] if i.get('id') != item_id]
    n = len(d['items']) - len(keep)
    d['items'] = keep
    _queue_save(d)
    return n


def queue_retry(item_id=None):
    """重试队列（逐条，不改队列以外任何东西）：
    成功 → 出队；行内容冲突 → 标「冲突」保留；仍被占用 → tries+1 保留"""
    d = queue_load()
    res = {'ok': [], 'conflict': [], 'busy': []}
    for it in list(d['items']):
        if item_id and it.get('id') != item_id:
            continue
        iid = it.get('id')
        try:
            r = apply_progress_guarded(it.get('sheet'), it.get('row'), it.get('text') or '',
                                       expect=it.get('expect'),
                                       date_label=it.get('date_label'),
                                       queue_on_busy=False, source='retry',
                                       new_status=it.get('status') or None)
            res['ok'].append({'id': iid, 'backup': r.get('backup')})
            d['items'] = [x for x in d['items'] if x.get('id') != iid]
        except LedgerConflict as e:
            it['state'] = '冲突'
            it['last_error'] = '；'.join(x.get('字段', '?') for x in e.check.get('hard', []))
            res['conflict'].append({'id': iid, 'check': e.check})
        except LedgerBusy as e:
            it['tries'] = int(it.get('tries') or 0) + 1
            it['last_error'] = '；'.join(e.probe.get('reasons') or ['仍不可写'])
            res['busy'].append({'id': iid, 'reasons': e.probe.get('reasons', [])})
        except Exception as e:                                # noqa: BLE001
            it['tries'] = int(it.get('tries') or 0) + 1
            it['last_error'] = repr(e)[:200]
            res['busy'].append({'id': iid, 'reasons': [repr(e)[:200]]})
    _queue_save(d)
    return res


# ---------------------------------------------------------------- 读

def _open(read_only=True):
    import openpyxl
    return openpyxl.load_workbook(ledger_path(), read_only=read_only, data_only=read_only)


def sheet_names():
    wb = _open()
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def status_pool():
    """在册状态全集 = 记录池 + 实本已出现的值（避免对台账真实用法误报）"""
    pool = list(STATUS_POOL_BASE)
    try:
        for s in SHEETS_CASE:
            for r in read_rows(s):
                v = str(r.get('最新状态') or '').strip()
                if v and v not in pool:
                    pool.append(v)
    except Exception:
        pass
    return pool


def read_rows(sheet, limit=None):
    """读 sheet 数据行 → [{'_row': n, 列名: 值, ...}]

    批 52：sheet 不存在时**优雅返回 []**（如「合同审查」表由爸爸在 WPS 手工建，
    还没建时页面不得被异常冲垮）。"""
    cols = COLS_BY_SHEET.get(sheet, COLS)
    wb = _open()
    try:
        if sheet not in wb.sheetnames:
            return []
        ws = wb[sheet]
        out = []
        # 批 69：单扫取代双扫（原两次 iter_rows 全表，第二次只为算真实行号；行为等价）
        for i, r in enumerate(ws.iter_rows(min_row=DATA_START_ROW, values_only=True),
                              start=DATA_START_ROW):
            if not any(c not in (None, '') for c in r):
                continue
            row = {'_row': i}
            for k, idx in cols.items():
                row[k] = r[idx - 1] if idx - 1 < len(r) else None
            out.append(row)
        return out[:limit] if limit else out
    finally:
        wb.close()


def find_rows(sheet, key):
    """按 序号（整数/字符串）或 案件 关键字定位 → [(row, 序号, 案件)]"""
    hits = []
    key_s = str(key).strip()
    for row in read_rows(sheet):
        no = str(row.get('序号') or '').strip()
        name = str(row.get('案件') or '')
        if key_s.isdigit():
            if no == key_s:
                hits.append((row['_row'], no, name))
        elif key_s and key_s in name:
            hits.append((row['_row'], no, name))
    return hits


# ------------------------------------------------- 批 54：受控建「合同审查」sheet

CONTRACT_HEADER = [k for k, _ in sorted(CONTRACT_COLS.items(), key=lambda kv: kv[1])]


def ensure_contract_sheet(apply=False):
    """受控建「合同审查」sheet（批 54，默认 dry-run，幂等）。

    爸爸拍板（2026-10-09）：合同审查业务线的账记台账新 sheet。本函数把「建表」
    从手工动作升级为**受控通道**——与 I→J 回写同一套三重闸：锁检测 → 备份 →
    写后全簿审计（allowed 之外零变化）。幂等语义：
      · sheet 不存在 + 未 apply → 返回 will_create 方案（不落盘）
      · sheet 不存在 + apply → 建 sheet + 写标题行 + 第 3 行表头（11 格，其余零改动）
      · sheet 已存在且表头逐格一致 → no-op（action='none'）
      · sheet 已存在但表头不一致 → 报错**不动作**（绝不改已有内容，交人工）

    ⚠️ 批 62 修正：表头写**第 3 行**（全簿统一结构：1 标题 / 2 分组 / 3 字段 / 4+ 数据），
    与 `read_rows` 的 DATA_START_ROW=4 口径对齐（批 54 初版误写第 1 行 → 数据会被
    read_rows 跳过；零数据期修正，真表已同步受控搬移）。
    """
    wb = _open()
    try:
        names = list(wb.sheetnames)
        exists = SHEET_CONTRACT in names
        header = []
        if exists:
            ws = wb[SHEET_CONTRACT]
            header = [ws.cell(row=3, column=c).value
                      for c in range(1, len(CONTRACT_HEADER) + 1)]
    finally:
        wb.close()
    if exists and [str(v or '').strip() for v in header] != CONTRACT_HEADER:
        return {'ok': False, 'action': 'abort',
                'error': '「%s」sheet 已存在但表头不一致（读到 %s）——不改动，请人工核对'
                         % (SHEET_CONTRACT, header)}
    if exists:
        return {'ok': True, 'action': 'none', 'sheet': SHEET_CONTRACT,
                'note': 'sheet 已存在且表头一致，无需动作'}
    plan = {'sheet': SHEET_CONTRACT, 'header': CONTRACT_HEADER}
    if not apply:
        return {'ok': True, 'dry_run': True, 'plan': plan,
                'note': '将新建 sheet 并写入标题 + A3:J3 表头（--apply 才落盘；落盘前锁检测+备份）'}
    locks = detect_lock()
    if locks:
        return {'ok': False, 'action': 'abort',
                'error': '检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks}
    before = snapshot_cells()
    before_names = list(names)
    dst, ok = backup(apply=True)
    if not ok:
        return {'ok': False, 'action': 'abort', 'error': '备份未生成，已中止（台账未改动）'}
    import openpyxl
    allowed = {(SHEET_CONTRACT, 3, c) for c in range(1, len(CONTRACT_HEADER) + 1)}
    allowed.add((SHEET_CONTRACT, 1, 1))
    wb2 = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb2.create_sheet(SHEET_CONTRACT)
        ws.cell(row=1, column=1).value = '合同审查登记表'
        for ci, name in enumerate(CONTRACT_HEADER, start=1):
            ws.cell(row=3, column=ci).value = name
        wb2.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        return {'ok': False, 'action': 'abort',
                'error': '写入失败（备份已在 %s，可回滚）：%r' % (dst, e)}
    finally:
        wb2.close()
    # 复查三道：表头逐格一致 + 名单只多这一个 sheet + allowed 之外零变化
    after = snapshot_cells()
    wb3 = _open()
    try:
        names_after = list(wb3.sheetnames)
    finally:
        wb3.close()
    extra = audit_cells(before, after, allowed)
    names_ok = sorted(names_after) == sorted(before_names + [SHEET_CONTRACT])
    got = [after.get((SHEET_CONTRACT, 3, c)) for c in range(1, len(CONTRACT_HEADER) + 1)]
    header_written = [str(v or '').strip() for v in got] == CONTRACT_HEADER
    ok_all = bool(header_written and names_ok and not extra)
    return {'ok': ok_all, 'action': 'created', 'backup': dst,
            'header_written': header_written, 'sheet_names_ok': names_ok,
            'extra_changes': extra[:5],
            'note': '已建 sheet + 标题/表头（写后审计：%s）'
                    % ('零意外改动' if not extra else '%d 处意外改动，请核对' % len(extra))}


# ------------------------------------------------- 批 62：执行款 sheet（受控建 + 追加）

PAYMENT_HEADER = [k for k, _ in sorted(PAYMENT_COLS.items(), key=lambda kv: kv[1])]


def ensure_payment_sheet(apply=False):
    """受控建「执行款」sheet（批 62，默认 dry-run，幂等）。

    与 ensure_contract_sheet 同套三重闸（锁检测 → 备份 → 写后审计零意外）。
    幂等语义：不存在 → will_create / 已存在表头一致 → no-op / 表头不一致 → abort 不改动。
    """
    wb = _open()
    try:
        names = list(wb.sheetnames)
        exists = SHEET_PAYMENT in names
        header = []
        if exists:
            ws = wb[SHEET_PAYMENT]
            header = [ws.cell(row=3, column=c).value
                      for c in range(1, len(PAYMENT_HEADER) + 1)]
    finally:
        wb.close()
    if exists and [str(v or '').strip() for v in header] != PAYMENT_HEADER:
        return {'ok': False, 'action': 'abort',
                'error': '「%s」sheet 已存在但表头不一致（读到 %s）——不改动，请人工核对'
                         % (SHEET_PAYMENT, header)}
    if exists:
        return {'ok': True, 'action': 'none', 'sheet': SHEET_PAYMENT,
                'note': 'sheet 已存在且表头一致，无需动作'}
    plan = {'sheet': SHEET_PAYMENT, 'header': PAYMENT_HEADER}
    if not apply:
        return {'ok': True, 'dry_run': True, 'plan': plan,
                'note': '将新建 sheet 并写入标题 + A3:G3 表头（apply 才落盘；落盘前锁检测+备份）'}
    locks = detect_lock()
    if locks:
        return {'ok': False, 'action': 'abort',
                'error': '检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks}
    before = snapshot_cells()
    before_names = list(names)
    dst, ok = backup(apply=True)
    if not ok:
        return {'ok': False, 'action': 'abort', 'error': '备份未生成，已中止（台账未改动）'}
    import openpyxl
    allowed = {(SHEET_PAYMENT, 3, c) for c in range(1, len(PAYMENT_HEADER) + 1)}
    allowed.add((SHEET_PAYMENT, 1, 1))
    wb2 = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb2.create_sheet(SHEET_PAYMENT)
        ws.cell(row=1, column=1).value = '执行款回款登记表'
        for ci, name in enumerate(PAYMENT_HEADER, start=1):
            ws.cell(row=3, column=ci).value = name
        wb2.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        return {'ok': False, 'action': 'abort',
                'error': '写入失败（备份已在 %s，可回滚）：%r' % (dst, e)}
    finally:
        wb2.close()
    after = snapshot_cells()
    wb3 = _open()
    try:
        names_after = list(wb3.sheetnames)
    finally:
        wb3.close()
    extra = audit_cells(before, after, allowed)
    names_ok = sorted(names_after) == sorted(before_names + [SHEET_PAYMENT])
    got = [after.get((SHEET_PAYMENT, 3, c)) for c in range(1, len(PAYMENT_HEADER) + 1)]
    header_written = [str(v or '').strip() for v in got] == PAYMENT_HEADER
    ok_all = bool(header_written and names_ok and not extra)
    return {'ok': ok_all, 'action': 'created', 'backup': dst,
            'header_written': header_written, 'sheet_names_ok': names_ok,
            'extra_changes': extra[:5],
            'note': '已建 sheet + 标题/表头（写后审计：%s）'
                    % ('零意外改动' if not extra else '%d 处意外改动，请核对' % len(extra))}


def _payment_sheet_state():
    """执行款 sheet 现状（只读）→ (exists, header, last_row, max_no)"""
    wb = _open()
    try:
        exists = SHEET_PAYMENT in list(wb.sheetnames)
        header, last_row, max_no = [], DATA_START_ROW - 1, 0
        if exists:
            ws = wb[SHEET_PAYMENT]
            header = [ws.cell(row=3, column=c).value
                      for c in range(1, len(PAYMENT_HEADER) + 1)]
            for r in range(DATA_START_ROW, ws.max_row + 1):
                v = ws.cell(row=r, column=1).value
                if v is None or not str(v).strip():
                    continue
                last_row = r
                try:
                    max_no = max(max_no, int(float(v)))
                except (TypeError, ValueError):
                    pass
        return exists, header, last_row, max_no
    finally:
        wb.close()


def append_payment_rows(rows, apply=False):
    """追加执行款记录到「执行款」sheet（受控：锁检测 → 备份 → 写后审计）。

    rows = [{'案件','案号','回款日期','金额','来源','备注'}]（按序追加）；
    「序号」自动续编（现有最大序号 + 1 起）。
    """
    exists, header, last_row, max_no = _payment_sheet_state()
    if not exists:
        return {'ok': False, 'action': 'no_sheet',
                'error': '台账里没有「执行款」sheet——先初始化或由同步流程自动创建'}
    if [str(v or '').strip() for v in header] != PAYMENT_HEADER:
        return {'ok': False, 'action': 'abort',
                'error': '「执行款」sheet 表头不一致（读到 %s）——不改动，请人工核对' % header}
    start_row = last_row + 1
    if not apply:
        return {'ok': True, 'dry_run': True, 'plan': {
            'sheet': SHEET_PAYMENT, 'start_row': start_row, 'count': len(rows)}}
    if not rows:
        return {'ok': True, 'action': 'none', 'note': '没有待同步记录'}
    locks = detect_lock()
    if locks:
        return {'ok': False, 'action': 'abort',
                'error': '检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks}
    before = snapshot_cells()
    dst, ok = backup(apply=True)
    if not ok:
        return {'ok': False, 'action': 'abort', 'error': '备份未生成，已中止（台账未改动）'}
    import openpyxl
    allowed = set()
    for i in range(len(rows)):
        for c in range(1, len(PAYMENT_HEADER) + 1):
            allowed.add((SHEET_PAYMENT, start_row + i, c))
    wb2 = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb2[SHEET_PAYMENT]
        for i, row in enumerate(rows):
            vals = [max_no + 1 + i, row.get('案件', ''), row.get('案号', ''),
                    row.get('回款日期', ''), row.get('金额', ''),
                    row.get('来源', ''), row.get('备注', '')]
            for ci, v in enumerate(vals, start=1):
                ws.cell(row=start_row + i, column=ci).value = v
        wb2.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        return {'ok': False, 'action': 'abort',
                'error': '写入失败（备份已在 %s，可回滚）：%r' % (dst, e)}
    finally:
        wb2.close()
    after = snapshot_cells()
    extra = audit_cells(before, after, allowed)
    return {'ok': not extra, 'action': 'appended', 'start_row': start_row,
            'count': len(rows), 'backup': dst, 'extra_changes': extra[:5],
            'note': '已追加 %d 行（写后审计：%s）'
                    % (len(rows), '零意外改动' if not extra else '%d 处意外改动，请核对' % len(extra))}


def sync_payments(rows, apply=False):
    """编排：确保「执行款」sheet（按需受控建）→ 追加 rows。dry-run 全链零副作用。

    → dry-run: {'ok', 'dry_run', 'plan': {'will_create', 'start_row', 'count'}}
    → apply:   {'ok', 'action': 'appended'/'none', 'start_row', 'count', ...}
    """
    ens = ensure_payment_sheet(apply=False)
    if not ens.get('ok'):
        return ens
    will_create = 'plan' in ens
    if not apply:
        start = DATA_START_ROW if will_create else (_payment_sheet_state()[2] + 1)
        return {'ok': True, 'dry_run': True,
                'plan': {'will_create': will_create, 'sheet': SHEET_PAYMENT,
                         'start_row': start, 'count': len(rows)}}
    if will_create:
        r1 = ensure_payment_sheet(apply=True)
        if not r1.get('ok'):
            return r1
    return append_payment_rows(rows, apply=True)


# ---------------------------------------------------------------- 写（I→J）

def status_write_pool():
    """写 K 列（最新状态）允许的取值池 = 台账「维护说明」页 r23 明文规定的取值（批 24 起 11 个）。
    单一真源是台账自己写的规范，本库只照抄、不另造词（实本漂移写法仅用于体检告警）。"""
    return list(STATUS_WRITE_POOL)


def status_is_rule_ok(v):
    """K 列某值是否在台账规定取值内（供体检显示用，不阻断读取）"""
    return str(v or '').strip() in STATUS_WRITE_POOL


def plan_letter_update(row_idx, sign=None, follow=None, rows=None):
    """律师函表回写**计划**（不落盘）：签收状态 / 后续跟进。

    爸爸 2026-09-17 拍板 ③：开受控回写。纪律：
      ① 只允许这两列（J 列备注绝不碰）
      ② 两列都为空 → 无改动，直接拒；与现状相同 → 跳过（不白写）
      ③ 该表无「进度日志」列 → 回退唯一手段 = **写前备份**（执行时强制留）
    """
    row_idx = int(row_idx or 0)
    if row_idx < 4:
        raise ValueError('律师函表数据自第 4 行起（表头 3 行），收到 row=%s' % row_idx)
    if rows is None:
        rows = read_rows(SHEET_LETTER)
    cur = None
    for r in rows:
        if int(r.get('_row') or 0) == row_idx:
            cur = r
            break
    if cur is None:
        raise ValueError('律师函表第 %d 行不存在或为空' % row_idx)

    changes = []
    for label, val in (('签收状态', sign), ('后续跟进', follow)):
        v = str(val if val is not None else '').strip()
        if not v:
            continue                                    # 空 = 本次不改该列
        old = str(cur.get(label) or '').strip()
        if v == old:
            continue                                    # 同值不写
        changes.append({'字段': label, '列': LETTER_WRITE_COLS[label],
                        '改前': old, '改后': v})
    if not changes:
        raise ValueError('没有需要变更的内容（两列都为空或与现状相同）')
    return {'sheet': SHEET_LETTER, 'row': row_idx, 'changes': changes,
            '现状': {k: str(cur.get(k) or '') for k in ('序号', '委托人', '收函对象', '发函日期')},
            'note': '律师函表无「进度日志」列；本次改动靠**写前备份**回退，写前会另存一份'}


def _write_letter(row_idx, plan):
    """备份 → 写 H(/I) → 复查 + **写后审计**。返回 (备份路径, 复查行, 意外改动)。"""
    before = snapshot_cells()
    dst, ok = backup(apply=True)
    if not ok:
        raise RuntimeError('备份未生成，已中止写入（台账未改动）')
    import openpyxl
    allowed = set()
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[SHEET_LETTER]
        for ch in plan['changes']:
            ws.cell(row=row_idx, column=ch['列']).value = ch['改后']
            allowed.add((SHEET_LETTER, row_idx, ch['列']))
        wb.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        raise RuntimeError('写入失败（备份已在 %s，可回滚）：%r' % (dst, e))
    finally:
        wb.close()
    after = [r for r in read_rows(SHEET_LETTER) if r['_row'] == row_idx]
    after = after[0] if after else {}
    extra = audit_cells(before, snapshot_cells(), allowed)
    if extra:
        print('⚠️ 写后审计：除目标格外还有 %d 处变化，请核对 → %s' % (len(extra), extra[:5]))
    return dst, after, extra


def apply_letter_update(row_idx, sign=None, follow=None, expect=None, force_lock=False):
    """律师函回写（Web 用；返回结构化结果，不退出进程）。

    expect = 页面上读到的「签收状态 / 后续跟进」快照 → 不一致即 409（防覆盖别人的改动）。
    """
    plan = plan_letter_update(row_idx, sign, follow)
    if not force_lock:
        locks = detect_lock()
        if locks:
            return {'busy': True, 'locks': locks, 'plan': plan}
    if expect:
        cur = [r for r in read_rows(SHEET_LETTER) if r['_row'] == int(row_idx)]
        cur = cur[0] if cur else {}
        for k in ('签收状态', '后续跟进'):
            if k in expect and str(cur.get(k) or '').strip() != str(expect.get(k) or '').strip():
                return {'conflict': True, 'field': k,
                        'now': str(cur.get(k) or ''), 'expect': str(expect.get(k) or ''),
                        'plan': plan}
    dst, after, extra = _write_letter(int(row_idx), plan)
    return {'ok': True, 'backup': dst, 'plan': plan, 'after': after, 'audit': extra}


def plan_progress(sheet, row_idx, new_text, date_label=None, new_status=None):
    """纯函数：产出 I/J/K 变更方案（不落盘），供 dry-run 与 apply 共用

    - `new_text` 非空 → 改 I 列（当前进度），旧值按纪律回迁 J 列
    - `new_status` 非空 → 改 K 列（最新状态），**只接受 status_write_pool() 内取值**
      （= 台账「维护说明」页规定），同时在 J 列留一行 `MM.DD：【状态】旧→新`
    - 两者可同时；new_status 与现状相同 → 视为无改动；两者都无内容 → 报错
    """
    if sheet not in SHEETS_CASE:
        raise ValueError('sheet %r 无 I→J 纪律，仅支持 %s' % (sheet, SHEETS_CASE))
    rows = read_rows(sheet)
    target = [r for r in rows if r['_row'] == row_idx]
    if not target:
        raise ValueError('未找到数据行 row=%d（%s）' % (row_idx, sheet))
    r = target[0]
    old_i = str(r.get('当前进度') or '').strip()
    old_j = str(r.get('进度日志') or '').strip()
    old_k = str(r.get('最新状态') or '').strip()
    label = date_label or datetime.now().strftime('%m.%d')
    if not re.match(r'^\d{2}\.\d{2}$', label):
        raise ValueError('date_label 需为 MM.DD，收到 %r' % date_label)
    new_text = '' if new_text is None else str(new_text).strip()
    want_status = '' if new_status is None else str(new_status).strip()
    if want_status:
        if want_status not in STATUS_WRITE_POOL:
            raise ValueError(
                '最新状态「%s」不在台账规定取值内：%s（规则见台账「维护说明」页 K 列说明）'
                % (want_status, ' / '.join(STATUS_WRITE_POOL)))
        if want_status == old_k:
            want_status = ''                      # 与现状相同 → 不算改动，避免空写
    if not new_text and not want_status:
        raise ValueError('没有需要变更的内容（进度为空且状态未变）')
    # 若 new_text 已自带 "MM.DD：" 前缀，则原样写入 I，回迁用它的日期
    if new_text:
        m = re.match(r'^(\d{2}\.\d{2})[：:]\s*(.+)$', new_text, re.S)
        if m:
            label, new_text = m.group(1), m.group(2).strip()
    # 回迁条目：旧值**自带 MM.DD： 前缀**时原样入日志 —— 否则会叠成双重日期
    # （2026-09-12 爸爸实测；旧值自带日期比「写入新值那天」更准确，且是台账既有写法）
    if not new_text or not old_i:
        entry = None
    elif re.match(r'^\d{2}\.\d{2}[：:]', old_i):
        entry = old_i
    else:
        entry = '%s：%s' % (label, old_i)
    st_entry = ('%s：【状态】%s→%s' % (label, old_k or '（空）', want_status)) if want_status else None
    head = [x for x in (entry, st_entry) if x]
    new_j = ('%s\n%s' % ('\n'.join(head), old_j)) if (head and old_j) else ('\n'.join(head) or old_j)
    return {
        'sheet': sheet, 'row': row_idx, '序号': r.get('序号'), '案件': r.get('案件'),
        'I_old': old_i, 'I_new': ('%s：%s' % (label, new_text)) if new_text else None,
        'J_old': old_j, 'J_new': new_j, 'J_entry_added': entry, 'J_status_added': st_entry,
        'K_old': old_k, 'K_new': want_status or None,
        'warnings': validate_row(sheet, r, new_status=want_status or None),
    }


def validate_row(sheet, row, new_status=None):
    """字段校验（只告警不阻断）"""
    w = []
    cat = str(row.get('案件类别') or '').strip()
    if cat and cat not in CATEGORY_POOL:
        w.append('案件类别「%s」不在下拉池 %s' % (cat, CATEGORY_POOL))
    st = str(new_status or row.get('最新状态') or '').strip()
    if st and st not in STATUS_WRITE_POOL:
        w.append('最新状态「%s」不在台账规定取值内（「维护说明」页 K 列说明）：%s'
                 % (st, ' / '.join(STATUS_WRITE_POOL)))
    if sheet == SHEET_LETTER:
        return w
    if not str(row.get('案件') or '').strip():
        w.append('C 列（案件）为空')
    if not str(row.get('委托人') or '').strip():
        w.append('E 列（委托人）为空')
    return w


def plan_draft_row(sheet, values):
    """立卷草稿行方案（不落盘）：目标行号 = 末条数据行 + 1；序号 = 现有最大 + 1"""
    if sheet not in SHEETS_CASE:
        raise ValueError('仅支持 %s（律师函 sheet 结构不同，暂不支持追加）' % SHEETS_CASE)
    rows = read_rows(sheet)
    last = max((r['_row'] for r in rows), default=DATA_START_ROW - 1)
    nums = [str(r.get('序号') or '').strip() for r in rows]
    nxt = 1 + max([int(n) for n in nums if n.isdigit()] or [0])
    filled = {k: v for k, v in values.items() if k in COLS and v not in (None, '')}
    filled.setdefault('序号', nxt)
    if str(filled.get('序号') or '').isdigit():
        filled['序号'] = int(filled['序号'])
    prog = str(filled.get('当前进度') or '').strip()
    if prog and not prog.startswith('[待确认]'):
        filled['当前进度'] = '[待确认] %s' % prog
    return {'sheet': sheet, 'row': last + 1, 'values': filled,
            'warnings': validate_row(sheet, filled)}


def append_draft_row(sheet, values, force_lock=False):
    """立卷追加草稿行（Web v1 唯一允许的台账写动作）：备份 → 锁 → 写 → 复查"""
    plan = plan_draft_row(sheet, values)
    if not force_lock:
        locks = detect_lock()
        if locks:
            print('拒绝写入：检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks)
            sys.exit(2)
    dst, ok = backup(apply=True)
    assert ok
    import openpyxl
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[sheet]
        for k, v in plan['values'].items():
            c = ws.cell(row=plan['row'], column=COLS[k], value=v)
            c.font = openpyxl.styles.Font(name='微软雅黑', size=10)
        wb.save(ledger_path())
    except Exception as e:
        print('执行异常（备份已在 %s，可回滚）：%r' % (dst, e))
        sys.exit(3)
    finally:
        wb.close()
    after = [r for r in read_rows(sheet) if r['_row'] == plan['row']]
    if not after or str(after[0].get('序号')) != str(plan['values']['序号']):
        print('❗ 复查失败 → 用备份回滚：%s' % dst)
        sys.exit(3)
    return plan, dst


# ------------------------------------------------- 批 41：律师函表新增行（爸爸 2026-09-18 拍板）
#: 新增行允许填的列（序号自动；签收状态/后续跟进**不收**——留空由受控回写 LETTER_WRITE_COLS 维护）
LETTER_ADD_COLS = ['委托人', '发函日期', '承办律师', '收函对象',
                   '欠款金额（本金）', '案由', '备注']


def _normalize_letter_values(filled):
    """写法规范化（批 41.1，对齐台账既有口径 r4-r11）：
    ① 发函日期 `2026-09-18`/`2026/9/18`/`20260918` → `2026.09.18`（点分隔，全表惯例）
    ② 欠款金额 纯数字无「元」→ 补千分位 + 「元」（`57085`→`57,085元`；已带元/带文字的不动）
    只做纯展示变换，不改数值语义；返回 (values, normalized 变换说明列表)。"""
    import re
    norm = []
    d = str(filled.get('发函日期') or '').strip()
    m = re.fullmatch(r'(20\d{2})\s*[-/.年]?\s*(\d{1,2})\s*[-/.月]?\s*(\d{1,2})\s*日?', d)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        nd = '%s.%02d.%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))
        if nd != d:
            filled['发函日期'] = nd
            norm.append('发函日期 %s → %s（对齐台账点分隔）' % (d, nd))
    a = str(filled.get('欠款金额（本金）') or '').strip()
    if a and re.fullmatch(r'\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?', a):
        num = a.replace(',', '')
        if '.' in num:
            ip, _, fp = num.partition('.')
            fp = fp.rstrip('0')
            body = '{:,}'.format(int(ip)) + ('.' + fp if fp else '')
        else:
            body = '{:,}'.format(int(num))
        na = body + '元'
        if na != a:
            filled['欠款金额（本金）'] = na
            norm.append('欠款金额 %s → %s（补单位，对齐台账惯例）' % (a, na))
    return filled, norm


def plan_letter_row(values):
    """律师函表新增行**方案**（不落盘）：目标行 = 末条数据行 + 1；序号 = 现有最大 + 1。
    与案件表 plan_draft_row 同骨架，按 LETTER_COLS 列映射（批 41）。
    律师函表数据自第 4 行起（表头 3 行），与 DATA_START_ROW 一致。"""
    rows = read_rows(SHEET_LETTER)
    last = max((r['_row'] for r in rows), default=DATA_START_ROW - 1)
    nums = [str(r.get('序号') or '').strip() for r in rows]
    nxt = 1 + max([int(n) for n in nums if n.isdigit()] or [0])
    filled = {}
    for k in LETTER_ADD_COLS:
        v = str(values.get(k) or '').strip()
        if v:
            filled[k] = v
    filled.setdefault('序号', nxt)
    filled, norm = _normalize_letter_values(filled)
    warnings = []
    if not str(filled.get('收函对象') or '').strip():
        warnings.append('收函对象为空——台账行与材料夹都靠它命名，建议必填')
    return {'sheet': SHEET_LETTER, 'row': last + 1, 'values': filled,
            'warnings': warnings, 'normalized': norm}


def apply_letter_row(values):
    """律师函表新增行（Web 用；三重闸同套：占用探测 → 备份到秒 → 写 → 复查 + 写后审计）。
    新格子**逐格克隆上一数据行同列样式**（font/alignment/border/fill/number_format，批 41.2：
    真实使用反馈——只设 font 不带对齐 → 新行文字顶格、与全表 vertical=center 观感不一致）。
    返回结构化结果，不退出进程；占用/审计失败由调用方转 409。"""
    plan = plan_letter_row(values)
    locks = detect_lock()
    if locks:
        return {'ok': False, 'code': 'busy', 'plan': plan, 'locks': locks}
    before = snapshot_cells()
    dst, ok = backup(apply=True)
    if not ok:
        return {'ok': False, 'code': 'backup_failed', 'plan': plan}
    import openpyxl
    from copy import copy as _copy
    src_row = plan['row'] - 1   # 上一数据行（plan 保证 ≥ DATA_START_ROW）
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[SHEET_LETTER]
        for k, v in plan['values'].items():
            ci = LETTER_COLS[k]
            cell = ws.cell(row=plan['row'], column=ci, value=v)
            src = ws.cell(row=src_row, column=ci)
            cell.font = _copy(src.font)
            cell.alignment = _copy(src.alignment)
            cell.border = _copy(src.border)
            cell.fill = _copy(src.fill)
            cell.number_format = src.number_format
        wb.save(ledger_path())
    except Exception as e:
        return {'ok': False, 'code': 'write_error', 'err': repr(e)[:200],
                'backup': dst, 'plan': plan}
    finally:
        wb.close()
    after = snapshot_cells()
    allowed = {(SHEET_LETTER, plan['row'], ci) for ci in range(1, len(LETTER_COLS) + 1)}
    unexpected = audit_cells(before, after, allowed)
    if unexpected:
        return {'ok': False, 'code': 'audit_failed', 'unexpected': unexpected[:10],
                'backup': dst, 'plan': plan}
    return {'ok': True, 'plan': plan, 'backup': dst}


def _write_progress(sheet, row_idx, plan):
    """备份 → 写 I(/J)(/K) → 复查 + **写后审计**（除目标格外应零变化）。
    返回 (备份路径, ok_i, ok_j, ok_k, 复查后的行, 意外改动列表)；失败抛 RuntimeError。
    plan['I_new'] 为 None 表示本次不改 I 列（纯状态变更）。"""
    before = snapshot_cells()
    dst, ok = backup(apply=True)
    if not ok:
        raise RuntimeError('备份未生成，已中止写入（台账未改动）')
    import openpyxl
    allowed = set()
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[sheet]
        if plan.get('I_new') is not None:
            ws.cell(row=row_idx, column=COL_I).value = plan['I_new']
            allowed.add((sheet, row_idx, COL_I))
        cj = ws.cell(row=row_idx, column=COL_J)
        src_j = cj.value
        cj.value = plan['J_new']
        allowed.add((sheet, row_idx, COL_J))
        if src_j is not None:
            cj.alignment = openpyxl.styles.Alignment(
                wrap_text=True, vertical=cj.alignment.vertical or 'center',
                horizontal=cj.alignment.horizontal)
            cj.font = openpyxl.styles.Font(name=cj.font.name or '微软雅黑',
                                           size=cj.font.size or 10)
        if plan.get('K_new'):
            ws.cell(row=row_idx, column=COL_K).value = plan['K_new']
            allowed.add((sheet, row_idx, COL_K))
        wb.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        raise RuntimeError('写入失败（备份已在 %s，可回滚）：%r' % (dst, e))
    finally:
        wb.close()
    after = [r for r in read_rows(sheet) if r['_row'] == row_idx]
    after = after[0] if after else {}
    ok_i = (plan.get('I_new') is None) or (str(after.get('当前进度') or '').strip() == plan['I_new'])
    ok_j = bool(plan.get('J_entry_added')) and plan['J_entry_added'] in str(after.get('进度日志') or '')
    if not plan.get('J_entry_added'):
        ok_j = bool(plan.get('J_status_added')) and plan['J_status_added'] in str(after.get('进度日志') or '')
    ok_k = (not plan.get('K_new')) or (str(after.get('最新状态') or '').strip() == plan['K_new'])
    # 写后审计：allowed 之外发生变化的格子（正常应为空）
    extra = audit_cells(before, snapshot_cells(), allowed)
    if extra:
        print('⚠️ 写后审计：除目标格外还有 %d 处变化，请核对 → %s' % (len(extra), extra[:5]))
    return dst, ok_i, ok_j, ok_k, after, extra


def apply_progress(sheet, row_idx, new_text, date_label=None, force_lock=False, new_status=None):
    """落盘（CLI 版）：备份 → 锁检测 → 写 I/J(/K) → 复查。返回 (plan, backup_path)

    锁命中/复查不一致 → sys.exit(2|3)（保持 CLI 既有行为，供脚本与旧接口调用）。
    Web 操作台请改调 apply_progress_guarded()（返回结构化结果，不退出进程）。
    """
    plan = plan_progress(sheet, row_idx, new_text, date_label, new_status)
    if not force_lock:
        locks = detect_lock()
        if locks:
            print('拒绝写入：检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks)
            sys.exit(2)
    try:
        dst, ok_i, ok_j, ok_k, _after, _extra = _write_progress(sheet, row_idx, plan)
    except RuntimeError as e:
        print(str(e))
        sys.exit(3)
    print('复查：I 列 %s | J 列留痕 %s | K 列 %s' % ('OK' if ok_i else 'FAIL',
                                            'OK' if ok_j else 'FAIL', 'OK' if ok_k else 'FAIL'))
    if not (ok_i and ok_j and ok_k):
        print('❗ 复查不一致 → 用备份回滚：%s' % dst)
        sys.exit(3)
    return plan, dst


def apply_progress_guarded(sheet, row_idx, new_text, expect=None, date_label=None,
                           queue_on_busy=True, source='web', new_status=None):
    """受控回写（Web/队列用，**不退出进程**）：

      1) 写环境探测 → 不可写：入「待同步队列」+ 抛 LedgerBusy（台账零改动）
      2) expect 快照比对 → 行内容被外部改动：抛 LedgerConflict（台账零改动，交人工判断）
      3) 备份 → 写 I/J（+ K 列状态）→ 复查 + 写后审计

    `new_status` 非空即同时改「最新状态」列（只接受台账规定取值）。返回结构化结果。
    """
    probe = probe_write_env()
    op = {'sheet': sheet, 'row': row_idx, 'text': new_text or '', 'status': new_status or '',
          'date_label': date_label, 'expect': expect, 'source': source,
          '序号': (expect or {}).get('序号', ''), '案件': (expect or {}).get('案件', '')}
    if not probe.get('ok'):
        entry = queue_add(op, error='；'.join(probe.get('reasons') or ['不可写'])) \
            if queue_on_busy else None
        raise LedgerBusy(probe, queued=entry)
    soft = []
    if expect:
        chk = check_external_change(expect, sheet, row_idx)
        if chk['hard']:
            raise LedgerConflict(chk)
        soft = chk['soft']
    plan = plan_progress(sheet, row_idx, new_text, date_label, new_status)
    try:
        dst, ok_i, ok_j, ok_k, after, extra = _write_progress(sheet, row_idx, plan)
    except RuntimeError as e:
        entry = queue_add(op, error=str(e)[:200]) if queue_on_busy else None
        raise LedgerBusy({'ok': False, 'path': ledger_path(), 'locks': [],
                          'reasons': [str(e)]}, queued=entry)
    if not (ok_i and ok_j and ok_k):
        raise RuntimeError('复查不一致，已停止（备份可回滚：%s）' % dst)
    return {'ok': True, 'plan': plan, 'backup': dst, 'soft_changes': soft,
            'audit': extra,
            'row_after': {k: str(after.get(k) or '') for k in ('当前进度', '进度日志', '最新状态')}}


# ---------------------------------------------------------------- 受控修复 J 列

def apply_fix_double_date(hits, force_lock=False):
    """受控修复：把 J 列「双重日期」条目规范成单层（**只改写目标单元格，绝不删除内容**）

    纪律同回写：锁检测 → 备份 → 写 → 复查（逐行比对预期文本）。返回 (修复条数, 备份路径)。
    """
    if not hits:
        return 0, None
    if not force_lock:
        locks = detect_lock()
        if locks:
            print('拒绝写入：检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks)
            sys.exit(2)
    dst, ok = backup(apply=True)
    if not ok:
        print('备份未生成，已中止（台账未改动）')
        sys.exit(3)
    import openpyxl
    wb = openpyxl.load_workbook(ledger_path())
    try:
        for h in hits:
            ws = wb[h['sheet']]
            c = ws.cell(row=h['row'], column=COL_J)
            c.value = h['J_new']
            c.alignment = openpyxl.styles.Alignment(
                wrap_text=True, vertical=c.alignment.vertical or 'center',
                horizontal=c.alignment.horizontal)
            c.font = openpyxl.styles.Font(name=c.font.name or '微软雅黑',
                                          size=c.font.size or 10)
        wb.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        print('执行异常（备份已在 %s，可回滚）：%r' % (dst, e))
        sys.exit(3)
    finally:
        wb.close()
    # 复查：逐行比对写后文本
    bad = []
    for h in hits:
        cur = [r for r in read_rows(h['sheet']) if r['_row'] == h['row']]
        if not cur or str(cur[0].get('进度日志') or '') != h['J_new']:
            bad.append((h['sheet'], h['row']))
    left = []
    for s in sorted({h['sheet'] for h in hits}):
        left += scan_double_date(s)
    if bad or left:
        print('❗ 复查不一致（%s）｜仍有双重日期 %d 处 → 用备份回滚：%s'
              % (bad, len(left), dst))
        sys.exit(3)
    return len(hits), dst


def plan_restore_row(backup_file, sheet, row_idx, cols=('当前进度', '进度日志')):
    """从**指定备份文件**读该行原值 → 行级回退方案（只读，不落盘）

    用途：写错了想精确退回某一行（比整本回滚更细，且不动其他行）。
    """
    import openpyxl
    if not os.path.isfile(backup_file):
        raise ValueError('备份文件不存在：%s' % backup_file)
    cur_rows = [r for r in read_rows(sheet) if r['_row'] == row_idx]
    if not cur_rows:
        raise ValueError('当前台账无此数据行：%s r%s' % (sheet, row_idx))
    cur_row = cur_rows[0]
    wb = openpyxl.load_workbook(backup_file, read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError('备份里没有 sheet：%s' % sheet)
        ws = wb[sheet]
        plan = {'backup': backup_file, 'sheet': sheet, 'row': row_idx,
                '序号': str(cur_row.get('序号') or ''), '案件': str(cur_row.get('案件') or ''),
                'cols': [], 'before': {}, 'after': {}}
        for name in cols:
            if name not in COLS:
                raise ValueError('不支持恢复该列：%s' % name)
            v = ws.cell(row=row_idx, column=COLS[name]).value
            plan['cols'].append(name)
            plan['after'][name] = '' if v is None else str(v)
            plan['before'][name] = str(cur_row.get(name) or '')
        plan['changed'] = [c for c in plan['cols']
                           if plan['after'][c] != plan['before'][c]]
        return plan
    finally:
        wb.close()


def apply_restore_row(plan, force_lock=False):
    """执行行级回退（**只改 plan['cols'] 列**）：锁 → 备份 → 写 → 复查 + 写后审计"""
    if not plan.get('changed'):
        return 0, None, []
    if not force_lock:
        locks = detect_lock()
        if locks:
            print('拒绝写入：检测到文件锁（请先在 WPS 中关闭台账）→ %s' % locks)
            sys.exit(2)
    before = snapshot_cells()
    dst, ok = backup(apply=True)
    if not ok:
        print('备份未生成，已中止（台账未改动）')
        sys.exit(3)
    import openpyxl
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[plan['sheet']]
        for name in plan['changed']:
            c = ws.cell(row=plan['row'], column=COLS[name])
            c.value = plan['after'][name] or None
            if c.value is not None:
                c.alignment = openpyxl.styles.Alignment(
                    wrap_text=True, vertical=c.alignment.vertical or 'center',
                    horizontal=c.alignment.horizontal)
                c.font = openpyxl.styles.Font(name=c.font.name or '微软雅黑',
                                              size=c.font.size or 10)
        wb.save(ledger_path())
    except Exception as e:                                    # noqa: BLE001
        print('执行异常（备份已在 %s，可回滚）：%r' % (dst, e))
        sys.exit(3)
    finally:
        wb.close()
    after_row = [r for r in read_rows(plan['sheet']) if r['_row'] == plan['row']]
    after_row = after_row[0] if after_row else {}
    bad = [c for c in plan['changed']
           if str(after_row.get(c) or '') != plan['after'][c]]
    allowed = {(plan['sheet'], plan['row'], COLS[c]) for c in plan['cols']}
    extra = audit_cells(before, snapshot_cells(), allowed)
    if bad:
        print('❗ 复查不一致（%s）→ 用备份回滚：%s' % (bad, dst))
        sys.exit(3)
    return len(plan['changed']), dst, extra


def cmd_restore_row(args):
    plan = plan_restore_row(args.backup, args.sheet, args.row,
                            tuple(args.col) if args.col else ('当前进度', '进度日志'))
    print('--- 行级回退方案（%s r%s 序号=%s ｜ %s）' % (plan['sheet'], plan['row'],
                                                plan['序号'], plan['案件']))
    print('来源备份：%s' % plan['backup'])
    for c in plan['cols']:
        mark = '（改为）' if c in plan['changed'] else '（已是目标值，跳过）'
        print('  %s %s' % (c, mark))
        print('    现在：%s' % (plan['before'][c].replace('\n', ' ⏎ ') or '（空）'))
        print('    回退为：%s' % (plan['after'][c].replace('\n', ' ⏎ ') or '（空）'))
    if not plan['changed']:
        print('（无需回退）')
        return 0
    if not args.apply:
        print('（dry-run，未落盘；加 --apply 执行 → 先备份台账再改，只动上面这几格）')
        return 0
    n, dst, extra = apply_restore_row(plan, force_lock=args.force_lock)
    print('已回退 %d 列；备份：%s' % (n, dst))
    print('写后审计：除目标格外%s' % ('无变化 ✓' if not extra else '发现 %d 处变化 → %s' % (len(extra), extra[:3])))
    print('提示：请在 WPS 中打开核对。')
    return 0


# ---------------------------------------------------------------- 展示

def cmd_fix_j(args):
    """受控修复 J 列双重日期：默认只列清单（dry-run）"""
    hits = scan_double_date(args.sheet, args.row)
    print('J 列「双重日期」条目：%d 处' % len(hits))
    for h in hits:
        print('--- %s row=%s 序号=%s ｜ %s' % (h['sheet'], h['row'], h['序号'], h['案件']))
        for a, b in h['changes']:
            print('  改前：%s' % a)
            print('  改后：%s' % b)
    if not hits:
        print('（无需修复）')
        return 0
    if not args.apply:
        print('（dry-run，未落盘；加 --apply 执行 → 会先备份台账再改，只动这些单元格）')
        return 0
    n, dst = apply_fix_double_date(hits, force_lock=args.force_lock)
    print('已修复 %d 处；备份：%s' % (n, dst))
    print('提示：请在 WPS 中打开核对「进度日志」列。')
    return 0


def cmd_check():
    p = ledger_path()
    print('台账路径: %s' % p)
    print('存在: %s' % os.path.isfile(p))
    if not os.path.isfile(p):
        return 2
    locks = detect_lock()
    print('文件锁: %s' % (locks or '无（可写）'))
    print('备份目录: %s（存在: %s）' % (backup_root(), os.path.isdir(backup_root())))
    print('sheet: %s' % sheet_names())
    for s in SHEETS_CASE + [SHEET_LETTER]:
        ws_rows = read_rows(s)
        print('  %s：数据行 %d 条（第 %d 行起）' % (
            s, len(ws_rows), DATA_START_ROW))
    return 0


def cmd_list():
    for s in SHEETS_CASE + [SHEET_LETTER]:
        rows = read_rows(s)
        print('=== %s（%d 条）===' % (s, len(rows)))
        if s == SHEET_LETTER:
            for r in rows:
                print('  %s | %s | %s' % (r.get('序号'), r.get('委托人'), r.get('签收状态')))
            continue
        from collections import Counter
        cnt = Counter(str(r.get('最新状态') or '（空）').strip() for r in rows)
        for k, v in cnt.most_common():
            print('  %-8s %d' % (k, v))
    return 0


def cmd_show(args):
    rows = read_rows(args.sheet, limit=args.limit)
    for r in rows:
        print('r%-3d %-4s %-22s %-6s %-10s %s' % (
            r['_row'], str(r.get('序号') or ''), str(r.get('案件') or '')[:22],
            str(r.get('案件类别') or ''), str(r.get('最新状态') or ''),
            str(r.get('案号') or '')))
    return 0


def cmd_progress(args):
    plan = plan_progress(args.sheet, args.row, args.text, args.date, new_status=args.status)
    print('--- 变更方案（sheet=%s row=%d 序号=%s 案件=%s）---' % (
        plan['sheet'], plan['row'], plan['序号'], plan['案件']))
    print('I 列：%r' % plan['I_old'])
    print('   →  %r' % plan['I_new'])
    print('J 列新增行：%r' % plan['J_entry_added'])
    if plan.get('J_status_added'):
        print('J 列状态行：%r' % plan['J_status_added'])
    if plan.get('K_new'):
        print('K 列（最新状态）：%r → %r' % (plan['K_old'], plan['K_new']))
    print('J 列首行（旧）：%r' % (plan['J_old'].splitlines()[:1] or [''])[0])
    for w in plan['warnings']:
        print('⚠️  校验：%s' % w)
    if not args.apply:
        print('（dry-run，未落盘；加 --apply 执行）')
        return 0
    _, dst = apply_progress(args.sheet, args.row, args.text, args.date,
                            force_lock=args.force_lock, new_status=args.status)
    print('已写入；备份：%s' % dst)
    print('提示：请在 WPS 中打开核对（云同步会自动拉取）')
    return 0


def cmd_move_to_closed(args):
    plan = plan_move_to_closed(args.sheet, args.row, args.note, args.date)
    print('--- 结案移行方案 ---')
    print('源：%s r%s 序号=%s 案件=%s' % (plan['src_sheet'], plan['src_row'],
                                     plan['src_序号'], plan['src_案件']))
    print('目标：%s r%s（该行「最新状态」写「已结案」）' % (plan['dst_sheet'], plan['dst_row']))
    print('搬入 %d 个字段；源行内容清空、行保留' % len(plan['cells']))
    for w in plan['warnings']:
        print('⚠️  %s' % w)
    if not args.apply:
        print('（dry-run，未落盘；加 --apply 执行）')
        return 0
    done = apply_move_to_closed(args.sheet, args.row, args.note, args.date,
                                force_lock=args.force_lock)
    print('已移入 %s r%s；清空源行 %d 列；备份：%s'
          % (done['dst_sheet'], done['dst_row'], len(done['cleared']), done['backup']))
    if done['audit']:
        print('⚠️  写后审计发现 %d 处意外改动：%s' % (len(done['audit']), done['audit'][:5]))
        return 3
    print('审计：除目标格与源行外零变化 ✓')
    return 0


def cmd_backup(args):
    dst, ok = backup(apply=args.apply)
    print('%s：%s' % ('已备份' if ok else '（dry-run）拟备份至', dst))
    return 0


def cmd_probe(args):
    p = probe_write_env()
    print('台账：%s（存在 %s）' % (p['path'], p['exists']))
    print('可写：%s | 目录可写：%s | 锁：%s' % (p['writable'], p['dir_writable'],
                                          p['locks'] or '无'))
    print('结论：%s' % ('✅ 可写' if p['ok'] else '⛔ 不可写'))
    for r in p['reasons']:
        print('  ⚠️  %s' % r)
    return 0 if p['ok'] else 2


def cmd_pending(args):
    d = queue_load()
    items = d.get('items', [])
    print('待同步队列（%s）：%d 条' % (pending_path(), len(items)))
    for i in items:
        print('  %s | %s r%s | %s | %s | tries=%s | %s'
              % (i.get('id'), i.get('sheet'), i.get('row'), i.get('案件'),
                 (i.get('text') or '')[:30], i.get('tries'), i.get('last_error', '')[:50]))
    if args.retry:
        want = None if args.retry in ('all', '*') else args.retry
        if want and not any(i.get('id') == want for i in items):
            print('无此条目：%s' % want)
            return 2
        res = queue_retry(want)
        print('重试结果：成功 %d｜冲突 %d｜仍占用 %d'
              % (len(res['ok']), len(res['conflict']), len(res['busy'])))
        for x in res['ok']:
            print('  ✅ %s → 备份 %s' % (x['id'], x['backup']))
        for x in res['conflict']:
            print('  ⚠️  %s → 台账已被外部改动，请人工核对' % x['id'])
        for x in res['busy']:
            print('  ⛔ %s → %s' % (x['id'], '；'.join(x['reasons'])))
        return 0 if not res['busy'] else 2
    return 0


# ---------------------------------------------------------------- 自测

# ---------------------------------------------------------------- 批 26：结案移行


def closed_sheet_name():
    """「已结案」页名（可用 LEDGER_CLOSED_SHEET 覆盖，供测试隔离）"""
    return os.environ.get('LEDGER_CLOSED_SHEET') or '已结案'


def _closed_target_row(ws):
    """「已结案」页第一个可写数据行（序号为空的第一行，至少第 4 行）"""
    r = 4
    while ws.cell(row=r, column=1).value not in (None, ''):
        r += 1
    return r


def plan_move_to_closed(sheet, row_idx, note='', today=None):
    """**结案移行预览（只读）**：把在办页 `sheet!row` 整行搬到「已结案」页。

    - 目标列**按字段名对齐**（两页第 3 行字段同序，故可按列号搬；表头空列不搬）
    - 目标行 = 「已结案」页第一个空行
    - 目标页的「最新状态」写 `已结案`；「备注」列追加一行移入说明
    - 源行**只清空内容、不删行**（保格式，且不影响其他行的合并/序号）
    """
    if sheet not in SHEETS_CASE:
        raise ValueError('只有案件页可结案移行：%s' % sheet)
    import openpyxl
    hit = [r for r in read_rows(sheet) if r['_row'] == row_idx]
    if not hit:
        raise ValueError('源行不存在：%s r%s' % (sheet, row_idx))
    rec = hit[0]
    wb = openpyxl.load_workbook(ledger_path())
    try:
        tgt = closed_sheet_name()
        if tgt not in wb.sheetnames:
            raise ValueError('台账里没有「%s」页' % tgt)
        ws = wb[tgt]
        hdr = [ws.cell(row=3, column=c).value for c in range(1, ws.max_column + 1)]
        names = ['' if h is None else str(h).strip() for h in hdr]
        if not any(names):                                   # 合成台账（无 r3 表头）→ 用内置列映射
            names = [''] * ws.max_column
            for _k, _ci in COLS.items():
                if _ci <= ws.max_column:
                    names[_ci - 1] = _k
        cells, skipped = {}, []
        for ci, nm in enumerate(names, start=1):
            if not nm:
                continue
            if nm in rec:
                cells[ci] = '已结案' if nm == '最新状态' else rec.get(nm)
            else:
                skipped.append(nm)
        tag = '%s 自「%s」第 %s 行移入' % ((today or datetime.now().strftime('%Y-%m-%d')),
                                       sheet, row_idx)
        if note:
            tag += '；' + note
        note_col = (names.index('备注') + 1) if '备注' in names else None
        if note_col:
            old = str(rec.get('备注') or '').strip()
            cells[note_col] = (old + '\n' + tag) if old else tag
        return {'src_sheet': sheet, 'src_row': row_idx, 'src_序号': rec.get('序号'),
                'src_案件': rec.get('案件'), 'dst_sheet': tgt,
                'dst_row': _closed_target_row(ws), 'cells': cells, 'note_col': note_col,
                'skipped_fields': skipped,
                'warnings': ['源行内容将被清空（保留行与格式）——信息全量落在已结案页那一行',
                             '在办页序号会出现空档：按维护说明可重编号（本工具**不自动改其他行**）']}
    finally:
        wb.close()


def apply_move_to_closed(sheet, row_idx, note='', today=None, force_lock=False):
    """**执行结案移行**：占用探测 → 备份 → 写目标行 → 清空源行 → 写后审计（同三重闸）"""
    import openpyxl
    env = probe_write_env()
    if not env['ok'] and not force_lock:
        raise LedgerBusy('台账当前不可写：%s' % ('；'.join(env['reasons']) or '未知原因'))
    plan = plan_move_to_closed(sheet, row_idx, note, today)
    dst, ok = backup(apply=True)
    if not ok:
        raise RuntimeError('备份失败，已中止（台账未改）')
    before = snapshot_cells()
    wb = openpyxl.load_workbook(ledger_path())
    try:
        ws = wb[plan['dst_sheet']]
        for c, v in plan['cells'].items():
            ws.cell(row=plan['dst_row'], column=c, value=v)
        src_ws = wb[sheet]
        cleared = []
        for c in range(1, src_ws.max_column + 1):
            cell = src_ws.cell(row=row_idx, column=c)
            if cell.value not in (None, ''):
                cleared.append(c)
                cell.value = None
        wb.save(ledger_path())
    finally:
        wb.close()
    allowed = {(plan['dst_sheet'], plan['dst_row'], c) for c in plan['cells']}
    allowed |= {(sheet, row_idx, c) for c in cleared}
    plan['cleared'] = cleared
    plan['backup'] = dst
    plan['audit'] = audit_cells(before, snapshot_cells(), allowed)
    return plan


def selftest():
    import tempfile
    import openpyxl
    fails = []
    total = []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='ledger_selftest_')
    fake = os.path.join(tmp, 'fake.xlsx')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '案件'
    ws.cell(row=1, column=1, value='测试表')
    for i, h in enumerate(['序号', '案号', '案件', '案件类别', '委托人'], start=1):
        ws.cell(row=3, column=i, value=h)
    ws.cell(row=4, column=1, value=1)
    ws.cell(row=4, column=3, value='测试案')
    ws.cell(row=4, column=4, value='诉讼')
    ws.cell(row=4, column=5, value='测试委托人')
    ws.cell(row=4, column=COL_I, value='旧进度A')
    ws.cell(row=4, column=COL_J, value='08.01：更早的事')
    ws.cell(row=4, column=COL_K, value='待立案')
    wb.save(fake)
    wb.close()

    bak = os.path.join(tmp, 'bak')
    os.environ['LEDGER_PATH'] = fake
    os.environ['LEDGER_BACKUP_ROOT'] = bak

    chk('read_rows 读到 1 条', len(read_rows('案件')) == 1)
    chk('缺失 sheet 优雅返回 []（批 52 guard：合同审查表由人在 WPS 建）',
        read_rows('合同审查') == [])
    chk('find_rows 命中 row=4', find_rows('案件', 1) == [(4, '1', '测试案')])
    plan = plan_progress('案件', 4, '09.12：寄出起诉状')
    chk('dry-run 方案 I_new', plan['I_new'] == '09.12：寄出起诉状')
    chk('回迁条目正确', plan['J_entry_added'] == '09.12：旧进度A')
    chk('J_new 组装（新条在前，旧内容保留）',
        plan['J_new'] == '09.12：旧进度A\n08.01：更早的事')
    wb2 = openpyxl.load_workbook(fake, read_only=True)
    chk('dry-run 未改文件', wb2['案件'].cell(row=4, column=COL_I).value == '旧进度A')
    wb2.close()
    chk('dry-run 未产生备份', not os.path.isdir(bak))
    # 批 54：受控建「合同审查」sheet（dry-run → apply → 幂等 → 表头篡改拒动）
    # ⚠️ 本段会产生真实备份（apply=True 三重闸）→ 必须排在「dry-run 未产生备份」断言之后
    r = ensure_contract_sheet(apply=False)
    chk('建表 dry-run 给出 will_create 方案',
        r.get('ok') and r.get('dry_run') and r['plan']['header'] == CONTRACT_HEADER)
    r = ensure_contract_sheet(apply=True)
    chk('建表 apply 成功且审计零意外改动',
        r.get('ok') and r.get('action') == 'created' and r.get('header_written')
        and r.get('sheet_names_ok') and not r.get('extra_changes'))
    chk('建表后 read_rows 可读（空表 → 空行列表）', read_rows('合同审查') == [])
    r = ensure_contract_sheet(apply=True)
    chk('建表幂等：复跑 action=none', r.get('ok') and r.get('action') == 'none')
    import openpyxl as _opx54
    _wb54 = _opx54.load_workbook(fake)
    _wb54['合同审查'].cell(row=3, column=3, value='被改的表头')
    _wb54.save(fake)
    _wb54.close()
    r = ensure_contract_sheet(apply=True)
    chk('表头被篡改 → abort 不动作', (not r.get('ok')) and r.get('action') == 'abort')
    _wb54 = _opx54.load_workbook(fake)
    _wb54['合同审查'].cell(row=3, column=3, value='委托人')
    _wb54.save(fake)
    _wb54.close()
    chk('表头还原 → 恢复 no-op', ensure_contract_sheet(apply=True).get('action') == 'none')
    # 批 62：执行款 sheet（先测缺表拦截 → 受控建 → 同步追加 → 序号续编）
    r = append_payment_rows([{'案件': 'x', '案号': '', '回款日期': '2026-10-01',
                              '金额': 1, '来源': '', '备注': ''}], apply=True)
    chk('缺「执行款」sheet → no_sheet 拦截（零副作用）',
        (not r.get('ok')) and r.get('action') == 'no_sheet')
    r = ensure_payment_sheet(apply=True)
    chk('执行款表 apply 成功且审计零意外改动',
        r.get('ok') and r.get('action') == 'created' and r.get('header_written')
        and r.get('sheet_names_ok') and not r.get('extra_changes'))
    chk('执行款表幂等：复跑 action=none',
        ensure_payment_sheet(apply=True).get('action') == 'none')
    r = sync_payments([{'案件': '测试案', '案号': '(2026)闽0000民初1号', '回款日期': '2026-10-01',
                        '金额': 50000.0, '来源': '法院划付', '备注': '首笔'}], apply=False)
    chk('同步 dry-run：不建表（已存在）+ 起始行 4 + 1 行',
        r.get('ok') and r.get('dry_run') and (not r['plan']['will_create'])
        and r['plan']['start_row'] == 4 and r['plan']['count'] == 1)
    r = sync_payments([{'案件': '测试案', '案号': '(2026)闽0000民初1号', '回款日期': '2026-10-01',
                        '金额': 50000.0, '来源': '法院划付', '备注': '首笔'}], apply=True)
    chk('同步 apply：追加 1 行（起始第 4 行）',
        r.get('ok') and r.get('action') == 'appended' and r.get('start_row') == 4)
    rows_p = read_rows(SHEET_PAYMENT)
    chk('追加后 read_rows 读到 1 行且序号=1、字段对位',
        len(rows_p) == 1 and str(rows_p[0].get('序号')) == '1'
        and rows_p[0].get('案件') == '测试案' and rows_p[0].get('金额(元)') == 50000.0)
    append_payment_rows([{'案件': '测试案2', '案号': '', '回款日期': '2026-10-05',
                          '金额': 1000.5, '来源': '对方支付', '备注': ''}], apply=True)
    rows_p = read_rows(SHEET_PAYMENT)
    chk('续追加：行数 2 且序号续编为 2',
        len(rows_p) == 2 and str(rows_p[1].get('序号')) == '2')
    # 批 23 回归：CLI 的 --status 必须真的传给 plan（批 14b 漏接 → 纯状态变更在 CLI 上被误判「无变更」）
    import subprocess
    _self = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger_ops.py')
    cp = subprocess.run([sys.executable, _self, 'progress', '--sheet', '案件',
                         '--row', '4', '--status', '已立案'],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    chk('CLI --status（纯状态变更）dry-run 给出 K 变更、不报「无变更」',
        cp.returncode == 0 and 'K 列（最新状态）' in cp.stdout
        and '没有需要变更的内容' not in (cp.stdout + cp.stderr))
    cp2 = subprocess.run([sys.executable, _self, 'progress', '--sheet', '案件',
                          '--row', '4', '--status', '随便乱写'],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    chk('CLI 池外状态被拒（非 0 退出）', cp2.returncode != 0)
    chk('CLI 回归未写盘（合成台账 K 仍为原值）',
        openpyxl.load_workbook(fake, read_only=True)['案件'].cell(row=4, column=COL_K).value == '待立案')
    chk('状态池含实本观测值（不在册不误报）', '待审核' in status_pool()
        and '待执行' in status_pool())
    chk('律师函 sheet 用独立列映射', LETTER_COLS['签收状态'] == 8
        and COLS_BY_SHEET[SHEET_LETTER] is LETTER_COLS)

    # 锁检测 → apply 必须拒绝
    lock = os.path.join(os.path.dirname(fake), '~$fake.xlsx')
    open(lock, 'w').close()
    chk('锁检测命中', len(detect_lock()) == 1)
    code = None
    try:
        apply_progress('案件', 4, '09.12：不该写入')
    except SystemExit as e:
        code = e.code
    chk('有锁时 apply 退出码 2', code == 2)
    os.remove(lock)

    # 正常 apply
    plan2, dst = apply_progress('案件', 4, '09.12：寄出起诉状')
    chk('备份已生成', os.path.isfile(dst))
    chk('备份落点在 backup_root', os.path.dirname(dst) == bak)
    chk('manifest 已追加', os.path.isfile(os.path.join(bak, 'manifest.md')))
    after = read_rows('案件')[0]
    chk('I 列已更新', after['当前进度'] == '09.12：寄出起诉状')
    chk('J 列新条在前且旧内容保留',
        '09.12：旧进度A' in after['进度日志'] and '08.01：更早的事' in after['进度日志'])
    chk('I 列旧值未丢失（在 J 中）', '旧进度A' in after['进度日志'])
    chk('带前缀文本自动识别日期',
        plan_progress('案件', 4, '01.02：补记')['I_new'] == '01.02：补记')
    chk('非法日期被拒', _raises(lambda: plan_progress('案件', 4, 'x', '2026-01-02')))
    chk('律师函 sheet 拒绝 I→J', _raises(lambda: plan_progress('律师函', 4, 'x')))

    # 立卷草稿行
    dp = plan_draft_row('案件', {'案件': '甲公司VS乙买卖合同纠纷', '委托人': '甲公司',
                                     '案件类别': '诉讼', '当前进度': '立卷（民-A-26099）'})
    chk('草稿行目标行号 = 末行+1', dp['row'] == 5)
    chk('草稿行序号 = 最大+1', dp['values']['序号'] == 2)
    chk('草稿行进度自动加 [待确认]',
        dp['values']['当前进度'] == '[待确认] 立卷（民-A-26099）')
    chk('非法列名被忽略', '不存在的列' not in dp['values'])
    dp2, dst2 = append_draft_row('案件', {'案件': '甲公司VS乙买卖合同纠纷', '委托人': '甲公司'})
    after2 = read_rows('案件')
    chk('草稿行已落盘并可读回', len(after2) == 2
        and str(after2[-1].get('案件')) == '甲公司VS乙买卖合同纠纷')
    chk('草稿行写入前已备份', os.path.isfile(dst2))
    chk('律师函拒绝追加行', _raises(lambda: plan_draft_row('律师函', {'委托人': 'x'})))

    # ---- 批 13：写环境探测 / 外部改动冲突 / 待同步队列 ----
    os.environ['LEDGER_PENDING_PATH'] = os.path.join(tmp, 'pending.json')
    pr = probe_write_env()
    chk('probe 无锁时可写', bool(pr['ok'] and pr['writable'] and pr['dir_writable']))
    lock2 = os.path.join(os.path.dirname(fake), '~$fake.xlsx')
    open(lock2, 'w').close()
    pr2 = probe_write_env()
    chk('probe 命中锁 → 不可写且给出原因', (not pr2['ok']) and bool(pr2['reasons']))
    busy = None
    try:
        apply_progress_guarded('案件', 4, '09.12：占用时不该写')
    except LedgerBusy as e:
        busy = e
    chk('占用时抛 LedgerBusy 并自动入队 1 条', busy is not None and bool(busy.queued)
        and len(queue_load()['items']) == 1)
    wb3 = openpyxl.load_workbook(fake, read_only=True)
    chk('占用时台账零改动（不半写）',
        wb3['案件'].cell(row=4, column=COL_I).value == '09.12：寄出起诉状')
    wb3.close()
    os.remove(lock2)
    res = queue_retry()
    chk('排队项重试成功并出队', len(res['ok']) == 1 and not res['busy']
        and queue_load()['items'] == [])
    after3 = read_rows('案件')[0]
    chk('重试真正写入 I', after3['当前进度'] == '09.12：占用时不该写')
    chk('重试把前值回迁 J', '09.12：寄出起诉状' in str(after3['进度日志']))

    exp = row_snapshot('案件', 4)
    wb4 = openpyxl.load_workbook(fake)
    wb4['案件'].cell(row=4, column=COL_I, value='[外部]别人改过了')
    wb4.save(fake)
    wb4.close()
    chk('外部改动被识别为 hard 冲突',
        len(check_external_change(exp, '案件', 4)['hard']) >= 1)
    conf = None
    try:
        apply_progress_guarded('案件', 4, '09.12：不该覆盖', expect=exp)
    except LedgerConflict as e:
        conf = e
    chk('冲突时抛 LedgerConflict 且台账未被覆盖', conf is not None
        and read_rows('案件')[0]['当前进度'] == '[外部]别人改过了')
    chk('冲突不入队（必须人工判断）', queue_load()['items'] == [])
    queue_add({'sheet': '案件', 'row': 4, 'text': 'x'})
    chk('queue_drop 移除条目', queue_drop(queue_load()['items'][0]['id']) == 1
        and queue_load()['items'] == [])
    # ---- 批 13 修正：回迁不叠加日期（真台账事故回归）----
    wb5 = openpyxl.load_workbook(fake)
    ws5 = wb5['案件']
    ws5.cell(row=4, column=COL_I, value='09.04：已交代公司盖要素式诉状')
    ws5.cell(row=4, column=COL_J, value='07.02：更早\n05.18：早前')
    wb5.save(fake)
    wb5.close()
    p3 = plan_progress('案件', 4, '09.12：电话沟通等排期')
    chk('旧值自带 MM.DD： → 回迁原样入日志（不叠成双重日期）',
        p3['J_entry_added'] == '09.04：已交代公司盖要素式诉状')
    chk('组装后的 J 不出现双重日期', '09.12：09.04' not in p3['J_new'])
    wb6 = openpyxl.load_workbook(fake)
    wb6['案件'].cell(row=4, column=COL_I, value='已立案')
    wb6.save(fake)
    wb6.close()
    chk('旧值无日期前缀 → 补当前日期（原行为保留）',
        plan_progress('案件', 4, '09.12：补记')['J_entry_added'] == '09.12：已立案')

    wb7 = openpyxl.load_workbook(fake)
    wb7['案件'].cell(row=4, column=COL_J, value='09.12：09.04：双日期条目\n07.02：更早')
    wb7.save(fake)
    wb7.close()
    hits = scan_double_date()
    chk('scan 命中双重日期 1 处', len(hits) == 1 and hits[0]['row'] == 4)
    chk('修复方案保留旧值日期、去掉叠加日期',
        hits[0]['J_new'] == '09.04：双日期条目\n07.02：更早')
    n_fix, dst_fix = apply_fix_double_date(hits)
    chk('修复已落盘且写前备份', n_fix == 1 and os.path.isfile(dst_fix))
    chk('修复后不再有双重日期', scan_double_date() == [])
    chk('修复未丢失其他日志行', '07.02：更早' in str(read_rows('案件')[0]['进度日志']))
    chk('无命中时 fix 直接返回 0', apply_fix_double_date([]) == (0, None))
    chk('写后审计：除目标格外零变化（正常写）',
        audit_cells({('案件', 4, 9): 'a', ('案件', 5, 3): 'x'},
                    {('案件', 4, 9): 'b', ('案件', 5, 3): 'x'},
                    {('案件', 4, 9)}) == [])
    chk('写后审计：能抓出目标格之外的意外改动',
        audit_cells({('案件', 5, 3): 'x'}, {('案件', 5, 3): 'y'},
                    {('案件', 4, 9)}) == [('案件', 5, 3, 'x', 'y')])
    chk('写后审计：新增/清空的格子也算变化',
        len(audit_cells({}, {('其他案件', 7, 1): '新'}, set())) == 1
        and len(audit_cells({('其他案件', 7, 1): '旧'}, {}, set())) == 1)
    snap = snapshot_cells()
    chk('全簿单元格快照可用', len(snap) >= 5)

    # ---- 行级回退（restore-row，2026-09-12 新增）----
    old = os.path.join(tmp, 'old.xlsx')
    wb8 = openpyxl.load_workbook(fake)
    wb8['案件'].cell(row=4, column=COL_I, value='原始进度')
    wb8['案件'].cell(row=4, column=COL_J, value='06.01：最老的一行')
    wb8.save(old)
    wb8.close()
    wb9 = openpyxl.load_workbook(fake)
    wb9['案件'].cell(row=4, column=COL_I, value='09.12：测试写坏的新进度')
    wb9['案件'].cell(row=4, column=COL_J, value='09.12：06.01：最老的一行')
    wb9.save(fake)
    wb9.close()
    rp = plan_restore_row(old, '案件', 4)
    chk('回退方案读到备份原值', rp['after']['当前进度'] == '原始进度'
        and rp['after']['进度日志'] == '06.01：最老的一行')
    chk('回退方案标出需改的列', set(rp['changed']) == {'当前进度', '进度日志'})
    n_r, dst_r, extra_r = apply_restore_row(rp)
    cur4 = read_rows('案件')[0]
    chk('行级回退已落盘且与原值一致', n_r == 2 and str(cur4['当前进度']) == '原始进度'
        and str(cur4['进度日志']) == '06.01：最老的一行')
    chk('回退写后审计：除目标格外零变化', extra_r == [])
    chk('回退无变化时直接跳过（不写盘）',
        apply_restore_row(plan_restore_row(old, '案件', 4)) == (0, None, []))
    chk('回退拒绝不存在的备份', _raises(lambda: plan_restore_row(
        os.path.join(tmp, '没有这个文件.xlsx'), '案件', 4)))

    # ---- 批 14b：K 列（最新状态）受控回写 ----
    # 批 24：规定值 10 → 11（+已判决，台账 r23 与两页 K 列下拉已同步）；池外样本改用「待执行」
    chk('写池 = 台账「维护说明」页规定的取值（批 24 起 11 个）',
        status_write_pool() == STATUS_POOL and len(status_write_pool()) == 11
        and '已判决' in status_write_pool())
    chk('体检：池外写法判不合规 / 池内判合规',
        (not status_is_rule_ok('待执行')) and status_is_rule_ok('已立案')
        and status_is_rule_ok('已判决'))
    chk('池外状态被拒（不写台账）',
        _raises(lambda: plan_progress('案件', 4, '', None, '待执行')))
    chk('进度与状态都为空被拒',
        _raises(lambda: plan_progress('案件', 4, '', None, '')))
    chk('状态与现状相同 → 视为无改动并报错',
        _raises(lambda: plan_progress('案件', 4, '', None, '待立案')))
    pk = plan_progress('案件', 4, '09.12：判决已下', None, '待判决')
    chk('状态变更进方案 K_old/K_new', pk['K_old'] == '待立案' and pk['K_new'] == '待判决')
    chk('状态行（旧→新）进 J 方案', pk['J_status_added'] == '09.12：【状态】待立案→待判决')
    chk('进度行与状态行同时进 J',
        pk['J_new'].startswith('09.12：原始进度\n09.12：【状态】待立案→待判决'))
    pt = plan_progress('案件', 4, '', None, '已立案')
    chk('纯状态变更：I_new 为 None（不动 I 列）', pt['I_new'] is None and pt['K_new'] == '已立案')
    sg = apply_progress_guarded('案件', 4, '', None, new_status='待判决')
    a4 = read_rows('案件')[0]
    chk('纯状态回写：K 已改、I 未动',
        str(a4['最新状态']) == '待判决' and str(a4['当前进度']) == '原始进度')
    chk('纯状态回写：J 留下【状态】行', '【状态】待立案→待判决' in str(a4['进度日志']))
    chk('纯状态回写：写后审计零意外（允许格含 K 列）', sg['audit'] == [])
    chk('纯状态回写：已备份', os.path.isfile(sg['backup']))
    sg2 = apply_progress_guarded('案件', 4, '09.12：判决已送达', None, new_status='执行中')
    a5 = read_rows('案件')[0]
    chk('进度+状态同时写：两列都落盘',
        str(a5['当前进度']) == '09.12：判决已送达' and str(a5['最新状态']) == '执行中')
    chk('进度+状态同时写：J 含进度回迁 + 状态行',
        '09.12：原始进度' in str(a5['进度日志'])
        and '【状态】待判决→执行中' in str(a5['进度日志']))
    chk('进度+状态同时写：审计零意外', sg2['audit'] == [])

    os.environ.pop('LEDGER_PENDING_PATH', None)

    # 批 26：结案移行（在办页 → 「已结案」页；只清空源行、不删行）
    wb3 = openpyxl.load_workbook(fake)
    _wcn = wb3.create_sheet(closed_sheet_name())
    for _k, _ci in COLS.items():                             # 造出 r3 字段名，使目标页列数与主表一致
        _wcn.cell(row=3, column=_ci, value=_k)
    wb3.save(fake)
    wb3.close()
    pc = plan_move_to_closed('案件', 4)
    chk('结案移行预览：目标=已结案页第 4 行', pc['dst_sheet'] == closed_sheet_name() and pc['dst_row'] == 4)
    chk('结案移行预览：目标行「最新状态」写「已结案」', pc['cells'].get(COL_K) == '已结案')
    chk('结案移行预览：备注定位于最后一列', pc['note_col'] == COLS['备注'])
    mv = apply_move_to_closed('案件', 4)
    wb4 = openpyxl.load_workbook(fake, read_only=True)
    _wc, _wsx = wb4[closed_sheet_name()], wb4['案件']
    chk('结案移行：目标行已写入且状态=已结案', _wc.cell(row=4, column=COL_K).value == '已结案'
        and str(_wc.cell(row=4, column=COLS['备注']).value or '').find('移入') >= 0)
    chk('结案移行：源行已清空（行仍在）',
        all(_wsx.cell(row=4, column=c).value in (None, '') for c in range(1, 27)))
    wb4.close()
    chk('结案移行：写后审计零意外', (mv.get('audit') or []) == [])
    chk('结案移行被拒：非案件页不能结案', _raises(lambda: plan_move_to_closed('律师函', 4)))

    for k in ('LEDGER_PATH', 'LEDGER_BACKUP_ROOT'):
        os.environ.pop(k, None)
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


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='案件管理台账读写库 v%s' % VERSION)
    sub = ap.add_subparsers(dest='cmd')

    sub.add_parser('check')
    sub.add_parser('list')
    p_show = sub.add_parser('show')
    p_show.add_argument('--sheet', default='案件')
    p_show.add_argument('--limit', type=int, default=0)

    p_pr = sub.add_parser('progress')
    p_pr.add_argument('--sheet', required=True, choices=SHEETS_CASE)
    p_pr.add_argument('--row', type=int, required=True, help='数据行号（见 show）')
    p_pr.add_argument('--text', default='', help='新进度；可带 MM.DD： 前缀（改状态时可留空）')
    p_pr.add_argument('--status', default=None,
                      help='同时改「最新状态」列（只接受台账规定取值，见 status-pool）')
    p_pr.add_argument('--date', default=None, help='回迁条目日期 MM.DD（默认今天）')
    p_pr.add_argument('--apply', action='store_true')
    p_pr.add_argument('--force-lock', action='store_true', help='忽略锁检测（不推荐）')

    p_cl = sub.add_parser('move-to-closed',
                          help='结案移行：把在办页某行搬到「已结案」页（默认 dry-run）')
    p_cl.add_argument('--sheet', required=True, choices=SHEETS_CASE)
    p_cl.add_argument('--row', type=int, required=True)
    p_cl.add_argument('--note', default='', help='附加到目标行「备注」的说明')
    p_cl.add_argument('--date', default=None, help='移入日期（默认今天）')
    p_cl.add_argument('--apply', action='store_true')
    p_cl.add_argument('--force-lock', action='store_true')

    p_b = sub.add_parser('backup')
    p_b.add_argument('--apply', action='store_true')

    sub.add_parser('probe', help='写环境探测（锁/独占/目录可写）')
    p_pd = sub.add_parser('pending', help='待同步队列：查看 / --retry 重试')
    p_pd.add_argument('--retry', default=None, metavar='ID|all')
    p_fj = sub.add_parser('fix-j', help='受控修复 J 列「双重日期」条目（默认 dry-run）')
    p_fj.add_argument('--sheet', default=None, choices=SHEETS_CASE)
    p_fj.add_argument('--row', type=int, default=None)
    p_fj.add_argument('--apply', action='store_true')
    p_fj.add_argument('--force-lock', action='store_true')
    p_cs = sub.add_parser('init-contract-sheet',
                          help='受控建「合同审查」sheet（批 54；默认 dry-run，幂等，三重闸）')
    p_cs.add_argument('--apply', action='store_true')
    p_rr = sub.add_parser('restore-row', help='从指定备份按行精确回退（默认 dry-run）')
    p_rr.add_argument('--backup', required=True, help='备份 xlsx 路径')
    p_rr.add_argument('--sheet', required=True, choices=SHEETS_CASE)
    p_rr.add_argument('--row', type=int, required=True)
    p_rr.add_argument('--col', action='append', default=[],
                      help='要回退的列名，可多次（默认 当前进度 + 进度日志）')
    p_rr.add_argument('--apply', action='store_true')
    p_rr.add_argument('--force-lock', action='store_true')

    p_ar = sub.add_parser('append-row', help='立卷追加草稿行（Web v1 唯一允许的台账写动作）')
    p_ar.add_argument('--sheet', required=True, choices=SHEETS_CASE)
    p_ar.add_argument('--set', action='append', default=[], metavar='K=V',
                      help='列名=值，可多次；如 --set 案件=A与B纠纷 --set 委托人=A')
    p_ar.add_argument('--apply', action='store_true')
    p_ar.add_argument('--force-lock', action='store_true')
    sub.add_parser('status-pool', help='列出「最新状态」列允许写入的取值（来源：台账维护说明页）')
    sub.add_parser('selftest')

    args = ap.parse_args()
    if args.cmd == 'append-row':
        vals = {}
        for kv in args.set:
            if '=' not in kv:
                print('参数需 K=V 形式：%s' % kv)
                return 2
            k, v = kv.split('=', 1)
            vals[k.strip()] = v.strip()
        plan = plan_draft_row(args.sheet, vals)
        print('--- 草稿行方案（%s 第 %d 行）---' % (plan['sheet'], plan['row']))
        for k, v in plan['values'].items():
            print('  %s = %r' % (k, v))
        for w in plan['warnings']:
            print('⚠️  校验：%s' % w)
        if not args.apply:
            print('（dry-run，未落盘；加 --apply 执行）')
            return 0
        _, dst = append_draft_row(args.sheet, vals, force_lock=args.force_lock)
        print('已追加；备份：%s' % dst)
        return 0
    if args.cmd == 'check':
        return cmd_check()
    if args.cmd == 'list':
        return cmd_list()
    if args.cmd == 'show':
        return cmd_show(args)
    if args.cmd == 'progress':
        return cmd_progress(args)
    if args.cmd == 'move-to-closed':
        return cmd_move_to_closed(args)
    if args.cmd == 'backup':
        return cmd_backup(args)
    if args.cmd == 'probe':
        return cmd_probe(args)
    if args.cmd == 'pending':
        return cmd_pending(args)
    if args.cmd == 'fix-j':
        return cmd_fix_j(args)
    if args.cmd == 'init-contract-sheet':
        r = ensure_contract_sheet(apply=args.apply)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r.get('ok') else 3
    if args.cmd == 'restore-row':
        return cmd_restore_row(args)
    if args.cmd == 'status-pool':
        print('可写入「最新状态」的取值（台账「维护说明」页规定）：')
        for v in status_write_pool():
            print('  - %s' % v)
        return 0
    if args.cmd == 'selftest':
        return selftest()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
