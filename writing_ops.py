# -*- coding: utf-8 -*-
r"""writing_ops.py — 写作项目管理（批 51，CaseBoard 反向移植，2026-10-09）

定位：诉状/代理词等长文「一次任务一个项目」——项目化登记 + 版本入卷留痕。
- 元数据落**工作台自建库** `data\workbench.db`（照 tasks_ops 先例：台账没有的东西放这儿，
  不复制案件主数据；同一库文件，路径可用 `WORKBENCH_DB` 覆盖）
- 版本文件**复制入卷**：`<案件夹>\02-法律文书\<项目标题>\vNN-YYYYMMDD-<原文件名>`
  （02-法律文书 = 《_材料怎么放.md》规定的文书目录；目录不存在则新建＝只新增、不覆盖）
- 状态机：起草 → 修订 → 送审 → 定稿；允许 定稿 → 修订 回退；其余跨跳拒绝
- 写前自动备份 db 到 `D:\备份\08-工作台数据备份\`（保留 10 份；`WORKBENCH_BACKUP_DIR` 可覆盖）
- **台账零写入**；**只新增不覆盖不删除**

CLI：
    python writing_ops.py --list [--status 修订] [--case 案件夹名]
    python writing_ops.py --versions 3
    python writing_ops.py --selftest          # 临时 db + 临时案件根，不碰真数据
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
sys.path.insert(0, HERE)
import case_ops          # noqa: E402  只读用：案件根

DB_PATH = os.environ.get('WORKBENCH_DB') or os.path.join(DATA_DIR, 'workbench.db')
BACKUP_DIR = os.environ.get('WORKBENCH_BACKUP_DIR') or r'D:\备份\08-工作台数据备份'
KEEP_BACKUPS = 10
MAX_MB = float(os.environ.get('WRITING_MAX_MB') or '40')

#: 案卷文书目录（与 case_ops 的 v2 规范 / `_材料怎么放.md` 一致。
#: ⚠️ 批 51 实勘修正：方案原文写「03-法律文书」，实际规范是 **02-法律文书**（实抽 3 个案卷核对））
DOC_DIR = '02-法律文书'

DOC_TYPES = ('起诉状', '答辩状', '代理词', '法律意见书', '合同', '其他')
STATUSES = ('起草', '修订', '送审', '定稿')
ALLOWED_EXT = ('.docx', '.doc', '.pdf', '.md', '.txt')

#: 状态机：允许的迁移（另：同状态 = 无操作放行；其余跨跳一律拒）
TRANSITIONS = {'起草': ('修订',), '修订': ('送审',), '送审': ('定稿',), '定稿': ('修订',)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_projects (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  case_name  TEXT NOT NULL,
  doc_type   TEXT NOT NULL,
  title      TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT '起草',
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS writing_versions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  version_no INTEGER NOT NULL,
  filename   TEXT NOT NULL,
  rel_path   TEXT NOT NULL,
  note       TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wp_status ON writing_projects(status);
CREATE INDEX IF NOT EXISTS idx_wp_case   ON writing_projects(case_name);
CREATE INDEX IF NOT EXISTS idx_wv_proj   ON writing_versions(project_id);
"""


# ------------------------------------------------------------------ 基础

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def connect(path=None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)  # i2:write 写库模块（建表+写入）
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def backup_db(tag='auto', src=None):
    """写前备份（保留最近 KEEP_BACKUPS 份，与 tasks_ops 共用同一命名池）；DB 不存在则跳过"""
    src = src or DB_PATH
    if not os.path.isfile(src):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, 'workbench-%s-%s.db'
                       % (datetime.now().strftime('%Y%m%d-%H%M%S'), tag))
    shutil.copy2(src, dst)
    olds = sorted(f for f in os.listdir(BACKUP_DIR)
                  if f.startswith('workbench-') and f.endswith('.db'))
    for f in olds[:-KEEP_BACKUPS]:
        try:
            os.unlink(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return dst


# ------------------------------------------------------------------ 校验（安全闸）

def _case_dir(case_name):
    """案件夹解析：纯夹名 + 案件根第一层 + 真实存在（与 app._case_path_safe 同款口径）。"""
    case_name = str(case_name or '').strip()
    if not case_name or os.path.basename(case_name) != case_name or case_name in ('.', '..'):
        raise ValueError('案件夹名不合法（安全闸：只收纯夹名）')
    root = os.path.abspath(case_ops.case_root())
    p = os.path.abspath(os.path.join(root, case_name))
    if os.path.dirname(p) != root:
        raise ValueError('路径越界（安全闸）')
    if not os.path.isdir(p):
        raise ValueError('案件夹不存在：%s（先建案 / 核对夹名）' % case_name)
    return p


_BAD_TITLE_CHARS = set('<>:"/\\|?*')


def _norm_title(title):
    t = str(title or '').strip().rstrip('.')
    if not t:
        raise ValueError('项目标题不能为空')
    if t in ('.', '..') or any(ch in _BAD_TITLE_CHARS for ch in t):
        raise ValueError('标题不能包含 \\ / : * ? " < > | 等字符（要作为文件夹名）')
    if len(t) > 60:
        raise ValueError('标题过长（≤60 字）')
    return t


def _norm_filename(filename):
    fn = str(filename or '').strip()
    if not fn or os.path.basename(fn) != fn or '/' in fn or '\\' in fn:
        raise ValueError('文件名不合法（只收纯文件名，安全闸）')
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError('只支持 %s（收到：%s）'
                         % ('/'.join(ALLOWED_EXT), ext or '无扩展名'))
    return fn, ext


def _ver_name(version_no, filename):
    return 'v%02d-%s-%s' % (version_no, date.today().strftime('%Y%m%d'), filename)


def _unique_path(target):
    """同名避让（只新增不覆盖）：name.ext → name-2.ext → …"""
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(target)
    for i in range(2, 30):
        cand = '%s-%d%s' % (stem, i, ext)
        if not os.path.exists(cand):
            return cand
    raise ValueError('同名文件过多，请换文件名或先整理该目录')


def can_transition(frm, to):
    return to == frm or to in TRANSITIONS.get(frm, ())


# ------------------------------------------------------------------ 项目 CRUD

def get_project(pid, path=None):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    conn = connect(path)
    try:
        r = conn.execute('SELECT * FROM writing_projects WHERE id=?', (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def plan_project(case_name, doc_type, title, path=None):
    """dry-run：校验 + 返回将建行与落点（不落盘）。"""
    case_dir = _case_dir(case_name)
    doc_type = str(doc_type or '').strip()
    if doc_type not in DOC_TYPES:
        raise ValueError('类型只能是 %s' % '/'.join(DOC_TYPES))
    title = _norm_title(title)
    return {'case_name': str(case_name).strip(), 'doc_type': doc_type, 'title': title,
            'status': '起草',
            'folder_rel': os.path.join(DOC_DIR, title),
            'folder_abs': os.path.join(case_dir, DOC_DIR, title)}


def add_project(case_name, doc_type, title, path=None, do_backup=True):
    plan = plan_project(case_name, doc_type, title, path=path)
    if do_backup:
        backup_db('writing-add', src=path)
    now = _now()
    conn = connect(path)
    try:
        cur = conn.execute(
            'INSERT INTO writing_projects(case_name, doc_type, title, status,'
            ' created_at, updated_at) VALUES(?,?,?,?,?,?)',
            (plan['case_name'], plan['doc_type'], plan['title'], '起草', now, now))
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    return get_project(pid, path)


def list_projects(status=None, case=None, path=None):
    conn = connect(path)
    try:
        sql = ('SELECT p.*, '
               '(SELECT COUNT(*) FROM writing_versions v WHERE v.project_id=p.id)'
               ' AS version_count, '
               '(SELECT MAX(v.created_at) FROM writing_versions v WHERE v.project_id=p.id)'
               ' AS last_version_at '
               'FROM writing_projects p WHERE 1=1')
        args = []
        if status:
            sql += ' AND p.status=?'
            args.append(status)
        if case:
            sql += ' AND p.case_name=?'
            args.append(case)
        sql += ' ORDER BY p.updated_at DESC, p.id DESC'
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for r in rows:
            r['last_update'] = max(r.get('updated_at') or '', r.get('last_version_at') or '')
        return rows
    finally:
        conn.close()


def status_counts(path=None):
    conn = connect(path)
    try:
        out = {s: 0 for s in STATUSES}
        for r in conn.execute('SELECT status, COUNT(*) AS n FROM writing_projects'
                              ' GROUP BY status'):
            out[r['status']] = int(r['n'])
        return out
    finally:
        conn.close()


def set_status(pid, to, path=None, do_backup=True):
    """状态机校验后改状态；同状态 = 无操作放行；非法跨跳抛 ValueError。"""
    to = str(to or '').strip()
    if to not in STATUSES:
        raise ValueError('状态只能是 %s' % '/'.join(STATUSES))
    proj = get_project(pid, path)
    if not proj:
        raise ValueError('项目不存在：id=%s' % pid)
    frm = proj['status']
    if to == frm:
        return proj
    if to not in TRANSITIONS.get(frm, ()):
        raise ValueError('不允许从「%s」跳到「%s」——只允许 起草→修订→送审→定稿，'
                         '或 定稿→修订 回退' % (frm, to))
    if do_backup:
        backup_db('writing-status', src=path)
    conn = connect(path)
    try:
        conn.execute('UPDATE writing_projects SET status=?, updated_at=? WHERE id=?',
                     (to, _now(), proj['id']))
        conn.commit()
    finally:
        conn.close()
    return get_project(proj['id'], path)


# ------------------------------------------------------------------ 版本

def get_version(vid, path=None):
    try:
        vid = int(vid)
    except (TypeError, ValueError):
        return None
    conn = connect(path)
    try:
        r = conn.execute('SELECT * FROM writing_versions WHERE id=?', (vid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _next_version_no(project_id, path=None):
    conn = connect(path)
    try:
        r = conn.execute('SELECT MAX(version_no) AS m FROM writing_versions'
                         ' WHERE project_id=?', (project_id,)).fetchone()
        return int(r['m'] or 0) + 1
    finally:
        conn.close()


def plan_version(project_id, filename, path=None):
    """dry-run：返回将用的 v 号与落点（不落盘、不建目录）。"""
    proj = get_project(project_id, path)
    if not proj:
        raise ValueError('项目不存在：id=%s' % project_id)
    fn, _ext = _norm_filename(filename)
    case_dir = _case_dir(proj['case_name'])
    version_no = _next_version_no(proj['id'], path)
    rel = os.path.join(DOC_DIR, proj['title'], _ver_name(version_no, fn))
    return {'project_id': proj['id'], 'version_no': version_no, 'filename': fn,
            'rel_path': rel, 'abs_path': os.path.join(case_dir, rel),
            'case_name': proj['case_name'], 'title': proj['title']}


def add_version(project_id, filename, data, note='', path=None, do_backup=True):
    """写版本文件入卷（新建目录＝只新增）+ 登记 db。返回版本行。

    ⚠️ 顺序：先校验 → 备份 → **先写文件、后插 db 行**（文件写失败则 db 零改动；
       db 插失败会留一个孤儿文件——文件在案卷里可见可人工处理，不静默删除）。
    """
    proj = get_project(project_id, path)
    if not proj:
        raise ValueError('项目不存在：id=%s' % project_id)
    fn, _ext = _norm_filename(filename)
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        raise ValueError('版本内容为空')
    if len(data) > MAX_MB * 1048576:
        raise ValueError('文件 %.1fMB 超过上限 %gMB'
                         % (len(data) / 1048576, MAX_MB))
    case_dir = _case_dir(proj['case_name'])
    target_dir = os.path.join(case_dir, DOC_DIR, proj['title'])
    if not os.path.abspath(target_dir).startswith(case_dir + os.sep):
        raise ValueError('落点越界（安全闸）')
    version_no = _next_version_no(proj['id'], path)
    os.makedirs(target_dir, exist_ok=True)                       # 目录不存在则创建＝只新增
    target = _unique_path(os.path.join(target_dir, _ver_name(version_no, fn)))
    rel_path = os.path.relpath(target, case_dir)
    if do_backup:
        backup_db('writing-version', src=path)
    with open(target, 'wb') as f:
        f.write(bytes(data))
    now = _now()
    conn = connect(path)
    try:
        cur = conn.execute(
            'INSERT INTO writing_versions(project_id, version_no, filename, rel_path,'
            ' note, created_at) VALUES(?,?,?,?,?,?)',
            (proj['id'], version_no, os.path.basename(target), rel_path, note or '', now))
        conn.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, proj['id']))
        conn.commit()
        vid = cur.lastrowid
    finally:
        conn.close()
    return get_version(vid, path)


def list_versions(project_id, path=None):
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM writing_versions WHERE project_id=?'
            ' ORDER BY version_no, id', (int(project_id),)).fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------------------ CLI / 自测

def _main():
    ap = argparse.ArgumentParser(description='写作项目管理（批 51）')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--status')
    ap.add_argument('--case')
    ap.add_argument('--versions', type=int)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.versions:
        for v in list_versions(a.versions):
            print('v%02d  %s  %s  %s' % (v['version_no'], v['created_at'],
                                         v['rel_path'], v['note'] or ''))
        return 0
    if a.list:
        for p in list_projects(status=a.status, case=a.case):
            print('#%d [%s] %s｜%s｜%s｜版本 %d｜%s'
                  % (p['id'], p['status'], p['title'], p['case_name'], p['doc_type'],
                     p['version_count'], p['last_update']))
        return 0
    ap.print_help()
    return 0


def selftest():
    import tempfile
    checks = []

    def ck(n, c):
        checks.append((n, bool(c)))

    def bad(fn):
        try:
            fn()
            return False
        except ValueError:
            return True

    tmp = tempfile.mkdtemp(prefix='writing_selftest_')
    db = os.path.join(tmp, 'wb.db')
    cases = os.path.join(tmp, 'cases')
    os.makedirs(os.path.join(cases, '测试案夹'))
    old_root = os.environ.get('CASE_OPS_ROOT')
    old_bak = BACKUP_DIR
    old_max = MAX_MB
    try:
        os.environ['CASE_OPS_ROOT'] = cases
        globals()['BACKUP_DIR'] = os.path.join(tmp, 'bak')

        ck('初始空列表', list_projects(path=db) == [])
        p = add_project('测试案夹', '起诉状', '民事起诉状', path=db, do_backup=False)
        ck('新增项目 id/状态=起草', isinstance(p['id'], int) and p['status'] == '起草')
        ck('拒绝空标题', bad(lambda: add_project('测试案夹', '起诉状', '  ', path=db,
                                                do_backup=False)))
        ck('拒绝非法类型', bad(lambda: add_project('测试案夹', '瞎写', 'x', path=db,
                                                  do_backup=False)))
        ck('拒绝不存在的案件夹', bad(lambda: add_project('不存在夹', '起诉状', 'x', path=db,
                                                        do_backup=False)))
        ck('拒绝含路径分隔的标题', bad(lambda: add_project('测试案夹', '起诉状', 'a/b',
                                                        path=db, do_backup=False)))

        ck('非法跨跳 起草→定稿 拒', bad(lambda: set_status(p['id'], '定稿', path=db,
                                                          do_backup=False)))
        ck('非法跨跳 起草→送审 拒', bad(lambda: set_status(p['id'], '送审', path=db,
                                                          do_backup=False)))
        ck('起草→修订 放行',
           set_status(p['id'], '修订', path=db, do_backup=False)['status'] == '修订')
        ck('修订→送审 放行',
           set_status(p['id'], '送审', path=db, do_backup=False)['status'] == '送审')
        ck('送审→定稿 放行',
           set_status(p['id'], '定稿', path=db, do_backup=False)['status'] == '定稿')
        ck('定稿→修订 回退放行',
           set_status(p['id'], '修订', path=db, do_backup=False)['status'] == '修订')
        ck('非法状态值拒', bad(lambda: set_status(p['id'], '瞎写', path=db, do_backup=False)))
        ck('同状态无操作放行',
           set_status(p['id'], '修订', path=db, do_backup=False)['status'] == '修订')

        pv = plan_version(p['id'], '起诉状.docx', path=db)
        ck('预览 v 号=1 且落点含 02-法律文书',
           pv['version_no'] == 1 and DOC_DIR in pv['rel_path'])
        ck('预览拒绝不存在项目', bad(lambda: plan_version(9999, 'x.docx', path=db)))

        data1 = '起诉状正文 v1'.encode('utf-8')
        v1 = add_version(p['id'], '起诉状.docx', data1, note='初稿', path=db, do_backup=False)
        abs1 = os.path.join(cases, '测试案夹', v1['rel_path'])
        ck('v 落盘且内容一致', os.path.isfile(abs1) and open(abs1, 'rb').read() == data1)
        base1 = os.path.basename(v1['rel_path'])
        ck('文件名格式 v01-YYYYMMDD-起诉状.docx',
           base1.startswith('v01-') and base1.endswith('-起诉状.docx'))
        v2 = add_version(p['id'], '起诉状.docx', 'v2 正文'.encode('utf-8'), path=db,
                         do_backup=False)
        ck('v 号自增 → 02', v2['version_no'] == 2
           and os.path.basename(v2['rel_path']).startswith('v02-'))
        ck('两个版本都在（list_versions）', len(list_versions(p['id'], path=db)) == 2)
        ck('拒绝非法扩展名', bad(lambda: add_version(p['id'], 'x.exe', b'x', path=db,
                                                    do_backup=False)))
        ck('拒绝带路径文件名', bad(lambda: add_version(p['id'], '..\\x.docx', b'x', path=db,
                                                     do_backup=False)))
        ck('拒绝空内容', bad(lambda: add_version(p['id'], 'x.docx', b'', path=db,
                                                do_backup=False)))
        globals()['MAX_MB'] = 0.001                              # 1KB 上限 → 2KB 必拒
        ck('超大拒（上限压到 1KB 后 2KB 被拒）',
           bad(lambda: add_version(p['id'], 'x.docx', b'x' * 2048, path=db, do_backup=False)))
        globals()['MAX_MB'] = old_max

        p2 = add_project('测试案夹', '代理词', '代理词草稿', path=db, do_backup=False)
        ck('按状态筛', len(list_projects(status='修订', path=db)) == 1)
        ck('按案件筛', len(list_projects(case='测试案夹', path=db)) == 2)
        row = [x for x in list_projects(path=db) if x['id'] == p['id']][0]
        ck('列表带 version_count=2', row['version_count'] == 2)
        ck('最近更新取 max(updated_at, last_version_at)', bool(row['last_update']))
        ck('状态计数', status_counts(path=db)['修订'] == 1
           and status_counts(path=db)['起草'] == 1)
        add_project('测试案夹', '合同', '合同审阅稿', path=db, do_backup=True)
        ck('写前备份落盘', any(f.startswith('workbench-')
                             for f in os.listdir(os.path.join(tmp, 'bak'))))
        ck('p2 与 p 互不影响（版本独立）', list_versions(p2['id'], path=db) == [])
    finally:
        if old_root is None:
            os.environ.pop('CASE_OPS_ROOT', None)
        else:
            os.environ['CASE_OPS_ROOT'] = old_root
        globals()['BACKUP_DIR'] = old_bak
        globals()['MAX_MB'] = old_max
        shutil.rmtree(tmp, ignore_errors=True)

    n_fail = sum(1 for _n, c in checks if not c)
    for n, c in checks:
        print('%s %s' % ('PASS' if c else 'FAIL', n))
    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not n_fail else '有失败', len(checks), n_fail))
    return 0 if not n_fail else 1


if __name__ == '__main__':
    sys.exit(_main())
