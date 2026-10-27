#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
app.py — 法律工作台 · 本地 Web 操作台（仅 127.0.0.1，零外发）

定位：日常高频动作的点击入口（看板 / 案件 / 立卷 / 入库 / 费用 / 期限）。
数据与纪律全部复用同一实现：`ledger_ops` / `case_ops` / `deadline_ops`。

写死在代码里的安全纪律：
  - 只绑定 127.0.0.1（局域网不可见）；无任何外网请求；前端无 CDN（全本地文件）
  - 所有写动作必须 POST 且带 `"confirm": true`，否则一律走 dry-run
  - 台账：Web 端只读 + 立卷追加草稿行（进展更新仍走煤球会话，守 I→J 纪律）
  - 不提供案件材料下载/读取接口（只暴露文件名与目录结构）
  - 操作留痕 → `workbench.log`

启动（2026-09-12 起）：双击桌面「法律工作台」→ `launch_workbench.pyw`（pythonw 无窗口起本文件 + 自动开浏览器）
    关掉网页约 2 分钟后由**空闲守望**自动关停（`--idle-exit`，默认 120 秒；0 = 不自动关停）
命令行：
  <default venv python> D:\dev-tools\case_workbench\app.py [--port 8899] [--idle-exit 120]
  或双击 启动-法律工作台.bat（故障排查用，会弹黑窗）
"""
import argparse
import config as _config
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('_WB_STDOUT_UTF8', '1')
# stdout/stderr 一律强制 utf-8（兼容：控制台 / 重定向到文件 / pythonw 无 stdout）
# 2026-09-12 事故：launch_workbench 传 _WB_STDOUT_UTF8=1 后本文件跳过包裹 →
#   重定向到文件时 Python 用 cp936 打开 stdout → print('✓') 抛 UnicodeEncodeError → 双击打不开
for _name in ('stdout', 'stderr'):
    _f = getattr(sys, _name, None)
    if _f is None:
        if _name == 'stdout':
            try:
                _LOG = open(os.path.join(HERE, 'app.out.log'), 'a', encoding='utf-8', buffering=1)
                sys.stdout, sys.stderr = _LOG, _LOG
            except Exception:                                # noqa: BLE001
                pass
        continue
    try:
        _f.reconfigure(encoding='utf-8', errors='replace')    # TextIOWrapper（含重定向文件）
    except Exception:                                        # noqa: BLE001
        try:
            setattr(sys, _name, io.TextIOWrapper(_f.buffer, encoding='utf-8', errors='replace'))
        except Exception:                                    # noqa: BLE001
            pass

import ledger_ops      # noqa: E402
import case_ops        # noqa: E402
import deadline_ops    # noqa: E402
import schedule_ops    # noqa: E402  批 37：手工日程（与台账派生期限分开两套数据）
import tasks_ops       # noqa: E402  批 38：散件「关注/待处理」标记（存自建库）
import trace_ops        # noqa: E402  批 39d：留痕与文件管理（分区判定/体检/归集，只移动不删除）
import sohan_ops       # noqa: E402  批 39a：所函生成（复制模板 + 定点替换）
import export_batch_data  # noqa: E402
import timeline_export   # noqa: E402
import ocr_ops           # noqa: E402
import wiki_ops         # noqa: E402
import assistant_ops    # noqa: E402  批 A：台账助理引擎（智能提醒分析 + 对话；本地池 8788）
import ems_ops          # noqa: E402  批 46：EMS 签收凭证（存放/识别/提炼，全程本机）
import writing_ops      # noqa: E402  批 51：写作项目管理（项目化登记 + 版本入卷留痕）
import binding_ops      # noqa: E402  批 59：卷宗装订包（封面/登记表/目录/小结 + 连页书签 PDF）
import court_sms_ops    # noqa: E402  批 61：法院短信送达文书拉取（一张网纯 API；只拉取不上传）
import payment_ops      # noqa: E402  批 62：执行款回款记录（自建库主存 + 台账受控同步）
import commission_ops   # noqa: E402  批 64：委托手续套件（民事六件套 docxtpl 渲染 → 01-委托手续）

# 测试桩钩子（**仅测试用**）：ASSISTANT_MOCK_FILE 指向一个 .py，加载后可 monkeypatch
# assistant_ops 的 LLM 调用 → HTTP 语义回归零网络、零积分。正式运行不设该变量，零影响。
_mock_file = os.environ.get('ASSISTANT_MOCK_FILE')
if _mock_file and os.path.isfile(_mock_file):
    try:
        _spec_ns = {'__file__': _mock_file, '__name__': '_assistant_mock'}
        with open(_mock_file, encoding='utf-8') as _f:
            exec(compile(_f.read(), _mock_file, 'exec'), _spec_ns)   # noqa: S102
    except Exception as _e:                                          # noqa: BLE001
        sys.stderr.write('assistant mock load failed: %r\n' % (_e,))


VERSION = '1.2.0'   # 批 69：对齐 README「数据层 + Web 操作台 v1.2」（兼作新旧进程探针：/api/health）
# 批 47b（2026-09-19 爸爸拍板）：默认口 8799 → 8899——wb-proxy 临时端口池（8791~8799）
# 与本台默认口撞车（实锤：converter 占 8799 → 本台被迫漂移 8809/8800 → 爸爸窗口连错
# 服务，「点了没反应」复发）。8899 避开 8787/8788/8791-8799 全段，永绝撞车。
DEFAULT_PORT = int(_config.get('app.port', 8899) or 8899)
BLOCKED_PORTS = {7777, 8788, 8791, 8792, 8793, 8794, 8795, 8796, 8797}
STATIC_DIR = os.path.join(HERE, 'static')
LOG_PATH = os.path.join(HERE, 'workbench.log')
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
        '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon'}


def log(action, detail=''):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s | %s | %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                        action, detail))
    except OSError:
        pass


def _num(v):
    """从台账单元格抠数字（容忍 元/，/空格/区间）"""
    if v is None:
        return None
    s = str(v).replace(',', '').replace('，', '').replace('元', '').replace(' ', '')
    import re
    m = re.search(r'\d+(?:\.\d+)?', s)
    return float(m.group()) if m else None


# ---------------------------------------------------------------- 业务数据

def _folder_by_ledger_row():
    """台账行 → 案件夹（用同一套匹配器；台账只读一次）。用于案卷侧材料检查（09/10）。"""
    # 批 71：委托 case_ops 统一实现（多级案件夹）——原为单层重复实现，与 case_ops 版失步
    return case_ops.folder_by_ledger_row()


def api_timeline_plan(sheet, row, folder=None):
    """时间轴导出方案（dry-run，不落盘）"""
    return timeline_export.plan_export(sheet, int(row), folder)


def api_overview():
    out = {'sheets': [], 'fees': None, 'deadlines': None, 'cases': None}
    from collections import Counter
    rows_by_sheet = {}                      # 批 69：一次读取、本请求内复用（原同 sheet 读 3 遍）
    for sheet in ledger_ops.SHEETS_CASE:
        rows = ledger_ops.read_rows(sheet)
        rows_by_sheet[sheet] = rows
        cnt = Counter(str(r.get('最新状态') or '（空）').strip() for r in rows)
        out['sheets'].append({'sheet': sheet, 'count': len(rows), 'status': cnt.most_common()})
    out['letter'] = {'count': len(ledger_ops.read_rows(ledger_ops.SHEET_LETTER))}
    out['fees'] = api_fees()['summary']
    due = deadline_ops.due_items(7)
    out['deadlines'] = {'due_count': len(due), 'due': due[:8],
                        'total': len([i for i in deadline_ops.load().get('items', [])
                                      if i.get('状态') != 'done']),
                        'generated_at': deadline_ops.load().get('generated_at')}
    cases = api_cases()
    out['cases'] = {'count': cases['count'], 'with_inbox': cases['with_inbox'],
                    'no_inbox': cases['count'] - cases['with_inbox']}
    # 批 12：任务（工作台自建库）+ 客户去重计数（台账「委托人」列派生，只读）
    try:
        out['tasks'] = tasks_ops.stats()
        out['task_lists'] = {'today': tasks_ops.list_tasks(due='today')[:20],
                             'week': tasks_ops.list_tasks(due='week')[:20]}
    except Exception as e:                                   # noqa: BLE001
        log('TASKS-ERROR', repr(e)[:120])
        out['tasks'] = {'total': 0, 'open': 0, 'doing': 0, 'done': 0,
                        'overdue': 0, 'today': 0, 'week': 0, 'none': 0, 'error': repr(e)[:120]}
        out['task_lists'] = {'today': [], 'week': []}
    try:
        act = []
        for sheet in ledger_ops.SHEETS_CASE:
            for r in rows_by_sheet[sheet]:
                st = str(r.get('最新状态') or '').strip()
                if any(k in st for k in ('结案', '归档', '完结', '已结')):
                    continue
                act.append({'案件': str(r.get('案件') or ''), '委托人': str(r.get('委托人') or ''),
                            '状态': st, '案件类别': str(r.get('案件类别') or ''),
                            '法院/仲裁': str(r.get('法院/仲裁') or ''),
                            '开庭时间': str(r.get('开庭时间') or ''), 'sheet': sheet,
                            'row': r.get('_row')})
        out['active_cases'] = {'count': len(act), 'items': act[:12]}
        # 执行案独立视图（方案 §5.1 看板页原条目）：只按台账「案件类别 = 执行」筛，不另做生意口径
        execs = [x for x in act if x['案件类别'] == '执行']
        # 执行案材料视图（批 15）：案卷侧有没有 09-财产查控 / 10-处置分配（口径=案卷目录，不猜）
        try:
            fmap = _folder_by_ledger_row()
        except Exception as e:                               # noqa: BLE001
            log('FOLDER-MAP-ERROR', repr(e)[:120])
            fmap = {}
        for x in execs:
            p = fmap.get((x['sheet'], int(x['row'])))
            dirs = []
            if p and os.path.isdir(p):
                dirs = [d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))]
            x['folder'] = os.path.basename(p) if p else ''
            x['has_09'] = '09-财产查控' in dirs
            x['has_10'] = '10-处置分配' in dirs
        out['exec_cases'] = {'count': len(execs), 'items': execs[:12],
                             'with_09': sum(1 for x in execs if x['has_09']),
                             'with_10': sum(1 for x in execs if x['has_10']),
                             'matched_folder': sum(1 for x in execs if x['folder'])}
    except Exception as e:                                   # noqa: BLE001
        log('ACTIVE-ERROR', repr(e)[:120])
        out['active_cases'] = {'count': 0, 'items': [], 'error': repr(e)[:120]}
    try:
        out['clients'] = _client_items(rows_by_sheet=rows_by_sheet)
    except Exception as e:                                   # noqa: BLE001
        log('CLIENTS-ERROR', repr(e)[:120])
        out['clients'] = {'count': 0, 'items': [], 'error': repr(e)[:120]}
    return out


def _client_items(rows_by_sheet=None):
    """客户聚合视图（派生，只读）：台账「委托人」去重 + 台账案件名 + 未完成任务数
    + 工作台自建库里的补充档案（联系人/电话/微信/备注，批 13）
    批 69：rows_by_sheet 可选——调用方已读过则复用，未传则自读（兼容原调用）"""
    cases_by_client, order = {}, []
    for sheet in ledger_ops.SHEETS_CASE:
        rows = (rows_by_sheet or {}).get(sheet)
        if rows is None:
            rows = ledger_ops.read_rows(sheet)
        for r in rows:
            c = str(r.get('委托人') or '').strip()
            if not c:
                continue
            if c not in cases_by_client:
                cases_by_client[c] = []
                order.append(c)
            nm = str(r.get('案件') or '').strip()
            if nm:
                cases_by_client[c].append({'案件': nm, '状态': str(r.get('最新状态') or '').strip(),
                                           'sheet': sheet, 'row': r.get('_row')})
    open_tasks = [t for t in tasks_ops.list_tasks() if t['status'] != '已完成']
    try:
        ext = tasks_ops.list_clients_ext()
    except Exception as e:                                    # noqa: BLE001
        log('CLIENTS-EXT-ERROR', repr(e)[:120])
        ext = {}
    # 任务 → 客户归属（批 14）：
    #   ① 优先「台账案件名精确匹配」——任务页「关联案件」取自台账行（案件名），据此反查委托人；
    #   ② 回退旧启发式「客户核心词包含于 case_ref」——兼容批 13 之前挂在「案件夹名」上的历史值。
    case_to_client = {}
    for c, rows in cases_by_client.items():
        for r in rows:
            nm = str(r.get('案件') or '').strip()
            if nm:
                case_to_client.setdefault(nm, c)
    core_of = {c: case_ops.client_core(c) for c in order}
    tasks_by_client = {c: [] for c in order}
    for t in open_tasks:
        cref = str(t.get('case_ref') or '').strip()
        cli = case_to_client.get(cref)
        if cli is None and cref:
            for c in order:
                core = core_of[c]
                if core and core in cref:
                    cli = c
                    break
        if cli:
            tasks_by_client[cli].append(t)
    items = []
    for c in order:
        core = core_of[c]
        ts = tasks_by_client[c]
        e = ext.get(c) or {}
        items.append({'name': c, 'core': core,
                      'cases': cases_by_client[c], 'case_count': len(cases_by_client[c]),
                      'open_tasks': len(ts),
                      'ext': {k: str(e.get(k) or '') for k in tasks_ops.CLIENT_FIELDS},
                      'ext_updated_at': str(e.get('updated_at') or ''),
                      'tasks': [{'id': t['id'], 'title': t['title'], 'status': t['status'],
                                 'priority': t['priority'], 'due_date': t['due_date'],
                                 '逾期': t['逾期'], 'case_ref': t['case_ref']} for t in ts]})
    items.sort(key=lambda x: (-x['open_tasks'], -x['case_count'], x['name']))
    return {'count': len(items), 'items': items,
            'with_ext': sum(1 for i in items if any(i['ext'].values())),
            'field_labels': tasks_ops.CLIENT_LABELS}



def api_tasks(q):
    """任务列表（工作台自建库，不碰台账）"""
    return {'tasks': tasks_ops.list_tasks(
        status=q.get('status') or None, q=q.get('q') or None,
        case=q.get('case') or None, due=q.get('due') or None),
        'stats': tasks_ops.stats(),
        'statuses': list(tasks_ops.STATUSES), 'priorities': list(tasks_ops.PRIORITIES)}


def api_fees():
    rows_out, agg = [], {'应收基础': 0.0, '已收到': 0.0, '已开票': 0.0, '剩余尾款': 0.0,
                         '发票缺口': 0, '案件数': 0}
    for sheet in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sheet):
            base = _num(r.get('基础律师费'))
            recv = _num(r.get('已收到'))
            invoiced = _num(r.get('开票金额'))
            tail = _num(r.get('剩余尾款'))
            inv_no = str(r.get('发票号') or '').strip()
            gap = []
            # 2026-09-12 实测：台账 Q–U（收款/付款/开票/发票号/尾款）填充率为 0
            # → 缺口口径以「有费用约定但付款信息未登记」为主，直击台账待办的补录痛点
            if base and recv is None:
                gap.append('收款未登记')
            if base and tail is None:
                gap.append('尾款未登记')
            if invoiced is not None and not inv_no:
                gap.append('发票号空缺')
            if recv is not None and invoiced is not None and recv > invoiced + 0.01:
                gap.append('已收>已开票')
            if tail is None and base is not None and recv is not None:
                tail = round(base - recv, 2)
            item = {'sheet': sheet, 'row': r['_row'], '案件': r.get('案件'),
                    '委托人': r.get('委托人'), '状态': r.get('最新状态'),
                    '标的额': r.get('标的额'), '基础律师费': base, '已收到': recv,
                    '开票金额': invoiced, '发票号': inv_no, '剩余尾款': tail,
                    '缺口': gap}
            rows_out.append(item)
            agg['案件数'] += 1
            for k, v in (('应收基础', base), ('已收到', recv), ('已开票', invoiced),
                         ('剩余尾款', tail)):
                if v:
                    agg[k] += v
            if gap:
                agg['发票缺口'] += 1
    rows_out.sort(key=lambda x: (-(x['剩余尾款'] or 0)))
    return {'summary': agg, 'rows': rows_out}


def _case_ledger_brief(name, rows, detail=False):
    """案件夹 → 台账行摘要（列表/详情共用；只读匹配，认不出就明说，绝不硬套）"""
    try:
        m = case_ops.match_case_ledger(name, rows)
    except Exception as e:                                    # noqa: BLE001
        log('CASE-LEDGER-MATCH-ERROR', '%s %s' % (name, repr(e)[:120]))
        return {'matched': False, 'error': repr(e)[:120]}
    r = m.get('matched')
    if not r:
        return {'matched': False, 'candidates': len(m.get('candidates') or [])}
    b = {'matched': True, 'sheet': r['sheet'], 'row': r['row'], '案件': r['案件'],
         '委托人': r['委托人'], '案件类别': r['案件类别'], '状态': r['状态'],
         '状态合规': r['状态合规'], '案号': r['案号'], '开庭时间': r['开庭时间'],
         '承办律师': r.get('承办律师', ''), 'match_score': r['match_score']}
    if detail:
        b['match_why'] = r.get('match_why') or []
        b['法院'] = r.get('法院', '')
        b['当前进度'] = r.get('当前进度', '')
    return b


def _case_subcases(case_dir, rows):
    """系列案总夹 → 子案件夹清单（只读；含文件数/收件箱/说明/台账行摘要）"""
    try:
        extra = [d for d in case_ops.case_dirs(case_dir) if d not in case_ops.ALLOWED_DIRS]
    except Exception:                                         # noqa: BLE001
        return []
    if not case_ops.is_series_case(case_dir, extra):
        return []
    out = []
    for c in case_ops.series_children(case_dir):
        cp = os.path.join(case_dir, c['name'])
        d = dict(c)
        d['path'] = cp
        d['files'] = sum(len(f) for _r, _dd, f in os.walk(cp))
        d['inbox_count'] = len(os.listdir(os.path.join(cp, case_ops.INBOX))) if c['inbox'] else 0
        d['ledger'] = _case_ledger_brief(c['name'], rows) if rows is not None else {'matched': False}
        out.append(d)
    return out


def api_cases():
    root = case_ops.case_root()
    try:
        rows = case_ops.ledger_case_rows()
    except Exception as e:                                    # noqa: BLE001
        log('LEDGER-ROWS-ERROR', repr(e)[:160])
        rows = None
    out = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            subs = [d for d in sorted(os.listdir(p)) if os.path.isdir(os.path.join(p, d))]
            n_files = sum(len(f) for _r, _d, f in os.walk(p))
            inbox = os.path.join(p, case_ops.INBOX)
            inbox_n = len([x for x in os.listdir(inbox)]) if os.path.isdir(inbox) else 0
            subs_cases = _case_subcases(p, rows)
            out.append({'name': name, 'path': p, 'dirs': subs, 'files': n_files,
                        'inbox_count': inbox_n,
                        'has_guide': os.path.isfile(os.path.join(p, case_ops.GUIDE_NAME)),
                        'has_inbox': case_ops.INBOX in subs,
                        'has_pipeline': case_ops.PIPELINE_DIR in subs,
                        'series': bool(subs_cases), 'subcase_count': len(subs_cases),
                        'subcases': [s['name'] for s in subs_cases],   # 批 71：供下载/入库下拉列子案件夹
                        'ledger': (_case_ledger_brief(name, rows) if rows is not None else {'matched': False}),
                        'mtime': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d')})
    return {'count': len(out), 'with_inbox': sum(1 for c in out if c['has_inbox']),
            'recognized': sum(1 for c in out if c['ledger'].get('matched')),
            'root': root, 'cases': out}


# ---- 案件研究报告（批 49 立「执行深挖报告」；批 60 泛化为通用目录，含承办法官裁判参考等）----
DEEPDIVE_SUBDIR = os.path.join('08-过程文件', '研究报告')   # 报告落卷约定位置（常量名保留兼容）


def _case_deepdive(p):
    r"""案件夹的研究报告清单（批 49 立「深挖报告」，批 60 泛化）。

    报告由煤球在 WorkBuddy 会话里跑元典生成，按约定落案件夹的
    `08-过程文件\研究报告\`（如 `被执行人深挖报告-YYYYMMDD.md`、
    `承办法官裁判参考-<法官>-YYYYMMDD.md`）。
    工作台只 listdir 列出（**不读正文**——正文预览走 /api/deepdive/view 安全接口，
    数据不出本机、不上行 LLM）。
    """
    d = os.path.join(p, DEEPDIVE_SUBDIR)
    items = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            fp = os.path.join(d, fn)
            if os.path.isfile(fp) and fn.lower().endswith('.md'):
                st = os.stat(fp)
                items.append({'name': fn, 'size_kb': round(st.st_size / 1024, 1),
                              'mtime': time.strftime('%Y-%m-%d', time.localtime(st.st_mtime))})
    return {'dir': DEEPDIVE_SUBDIR, 'exists': os.path.isdir(d),
            'count': len(items), 'items': items}


def _case_path_safe(case):
    """案件夹路径安全闸（批 49 立为 _deepdive_case_path；批 51 提为公共函数，deepdive / writing 共用）：
    case 必须是案件根下第一层的真实夹名（防 ../ 越界）。"""
    case = str(case or '').strip()
    if not case or os.path.basename(case) != case:
        return None, '夹名不合法（安全闸：只收纯夹名）'
    root = os.path.abspath(case_ops.case_root())
    p = os.path.abspath(os.path.join(root, case))
    if os.path.dirname(p) != root:
        return None, '路径越界（安全闸）'
    if not os.path.isdir(p):
        return None, '案件夹不存在'
    return p, None


def _case_path_safe_rel(case):
    """批 71：案件夹路径安全闸（**1 或 2 段**：「案件夹」或「总夹/子案件夹」）。

    供法院短信下载 / 收件箱入库等**系列案子案件夹**流程用（_case_path_safe 的
    「只收第一层」契约保持不变——deepdive/writing 等页面继续用它）。
    安全约束：每段须为纯夹名（禁 ../、禁盘符路径）、整路径必须落在案件根下、最多两段。
    """
    case = str(case or '').strip().replace('\\', '/')
    if not case or case.endswith('/') or case.count('/') > 1:
        return None, '夹名不合法（安全闸：只收纯夹名或「总夹/子案件夹」两段）'
    parts = case.split('/')
    for seg in parts:
        if not seg or seg in ('.', '..') or os.path.basename(seg) != seg:
            return None, '夹名不合法（安全闸：只收纯夹名或「总夹/子案件夹」两段）'
    root = os.path.abspath(case_ops.case_root())
    p = os.path.abspath(os.path.join(root, *parts))
    rel = os.path.relpath(p, root)
    if rel.startswith('..') or rel.count(os.sep) > 1:
        return None, '路径越界（安全闸）'
    if not os.path.isdir(p):
        return None, '案件夹不存在'
    return p, None


def _deepdive_case_path(case):
    """兼容旧名（批 49 起被测试引用）：等价 _case_path_safe。"""
    return _case_path_safe(case)


def api_deepdive_view(q):
    r"""研究报告页内文本预览（批 49，GET；批 60 泛化目录）。

    与 file_view 的分工：file_view 直出**图片/PDF 二进制**（批 47 正解），
    研究报告是 .md 文本，浏览器无原生渲染——本接口读文本返 JSON，前端在
    浮层 <pre> 展示。数据只在本机页面里给人看，不上行任何 LLM（不违外发红线）。

    ⚠️ 安全闸五道（**顺序要紧**，批 46/47 惯例：越界最先、先于存在性）：
      ① case 纯夹名 + 案件根第一层；② file 强制 basename（禁路径分隔）；
      ③ 只放行 .md；④ 最终路径必须落在研究报告目录内；⑤ 存在性 + ≤2MB。
    """
    p, err = _case_path_safe(q.get('case'))
    if err:
        return {'error': err}
    fname = str(q.get('file') or '').strip()
    if not fname:
        return {'error': '缺 file 参数'}
    if os.path.basename(fname) != fname or '/' in fname or '\\' in fname:
        return {'error': '文件名不合法（只收纯文件名，安全闸）'}
    if not fname.lower().endswith('.md'):
        return {'error': '只支持 .md 研究报告预览'}
    full = os.path.abspath(os.path.join(p, DEEPDIVE_SUBDIR, fname))
    if os.path.dirname(full) != os.path.abspath(os.path.join(p, DEEPDIVE_SUBDIR)):
        return {'error': '路径不在研究报告目录内（安全闸）'}
    if not os.path.isfile(full):
        return {'error': '报告不存在（可能已被移动或改名）'}
    size = os.path.getsize(full)
    if size > 2 * 1024 * 1024:
        return {'error': '报告过大（%.1f MB > 2 MB），请点「打开文件夹」看原文'
                         % (size / 1048576)}
    try:
        with open(full, encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        return {'error': '报告不是 UTF-8 文本，无法页内预览'}
    log('VIEW_DEEPDIVE', '%s（%d KB）' % (fname, size // 1024))
    return {'ok': True, 'case': q.get('case'), 'name': fname,
            'size_kb': round(size / 1024, 1), 'text': text}


def api_deepdive_open(body):
    r"""打开案件夹的研究报告目录（批 49，POST；批 60 泛化目录）。

    目录不存在时逐级回退：研究报告 → 08-过程文件 → 案件夹（方便把新报告
    直接放进去）。安全闸同 _case_path_safe；实际打开走 _open_in_explorer
    （批 46.4 定案 os.startfile，勿改回 explorer）。打开文件夹非不可逆、
    非写入，与律师函页 open_folder 同例：不设 confirm。
    """
    p, err = _case_path_safe(body.get('case'))
    if err:
        return {'error': err}
    target = os.path.join(p, DEEPDIVE_SUBDIR)
    if not os.path.isdir(target):
        target = os.path.join(p, '08-过程文件')
    if not os.path.isdir(target):
        target = p
    ok, detail = _open_in_explorer(target)
    return {'ok': bool(ok), 'opened': detail if ok else '', 'error': None if ok else detail}


# ---------------------------------------------------------------- 卷宗装订包（批 59）

def _binding_meta(case_name):
    r"""卷宗封面/小结元数据（台账匹配，只读；认不出就空着——不猜不编）。"""
    try:
        mt = case_ops.match_case_ledger(case_name, case_ops.ledger_case_rows()).get('matched')
    except Exception:                                        # noqa: BLE001
        mt = None
    if not mt:
        return {'matched': False, '案件名': case_name}
    return {'matched': True, '案件名': mt.get('案件') or case_name,
            '委托人': mt.get('委托人') or '', '案件类别': mt.get('案件类别') or '',
            '案号': mt.get('案号') or '', '法院': mt.get('法院') or '',
            '承办律师': mt.get('承办律师') or '', '标的额': mt.get('标的额') or '',
            '当前进度': mt.get('当前进度') or '', '状态': mt.get('状态') or '',
            'match_score': mt.get('match_score')}


def api_binding_build(body):
    r"""卷宗装订包：材料分拣清单（dry-run，零副作用）与生成（confirm 落 `卷宗装订\`）。

    合成逻辑全在 binding_ops（本地 PyMuPDF：合并 + 页码 + 书签 + 字体子集）；
    原件只读、产物只写 `卷宗装订\`——安全闸 `_case_path_safe` 同全套案卷接口。
    """
    case_name = str(body.get('case') or '').strip()
    confirm = bool(body.get('confirm'))
    p, err = _case_path_safe(case_name)
    if err:
        return {'error': err}
    meta = _binding_meta(case_name)
    r = binding_ops.build(p, case_name, meta=meta, apply=confirm)
    r['meta'] = meta
    r['case'] = case_name
    if confirm and r.get('ok'):
        log('BINDING_BUILD', '%s → %s（%s 页，并入 %s 份）'
            % (case_name, r.get('pdf'), r.get('pages'), r.get('merged')))
    return r


def api_binding_open(body):
    r"""打开案件夹的卷宗装订产物目录（非不可逆、非写入，不设 confirm）。

    目录不存在时回退案件夹根（方便第一次生成前先看案卷）。
    """
    p, err = _case_path_safe(body.get('case'))
    if err:
        return {'error': err}
    target = os.path.join(p, binding_ops.BIND_SUBDIR)
    if not os.path.isdir(target):
        target = p
    ok, detail = _open_in_explorer(target)
    return {'ok': bool(ok), 'opened': detail if ok else '', 'error': None if ok else detail}


# ---------------------------------------------------------------- 法院短信（批 61）

def _court_sms_match(case_no):
    r"""案号 → 案件夹建议（只读；台账「案号」列逐条比对——一格可能含多条案号；认不出不猜）。

    执行案号与立案案号常不同 → 未命中只提示「手动选」，绝不硬指。
    """
    if not case_no:
        return {'suggest': None, 'note': '短信里没解析到案号——请手动选案件'}
    target = court_sms_ops.normalize_case_no(case_no)
    try:
        rows = case_ops.ledger_case_rows()
    except Exception as e:                                    # noqa: BLE001
        return {'suggest': None, 'note': '台账读取失败：%s' % e}
    # 批 73：台账常一格存两条案号（民初+执保 / 带后缀）→ 逐条包含比对（整串全等时代必不命中）
    hits = [r for r in rows if court_sms_ops.match_case_no(r.get('案号'), target)]
    if not hits:
        return {'suggest': None,
                'note': '案号未命中台账（执行案号与立案案号常不同）——请手动选案件'}
    try:
        mapping = case_ops.folder_by_ledger_row(rows)
    except Exception:                                         # noqa: BLE001
        mapping = {}
    for r in hits:
        p = mapping.get((r['sheet'], int(r['row'])))
        if p:
            return {'suggest': os.path.basename(p), '案件名': r['案件'],
                    '案号': r['案号'], 'sheet': r['sheet'], 'row': r['row']}
    # 批 71：映射未覆盖时兜底——按案件名直接找夹（匹配器已收紧：案由剥离 + 并列拒认）
    for r in hits:
        try:
            p = case_ops.folder_by_case_name(str(r.get('案件') or ''))
        except Exception:                                     # noqa: BLE001
            p = ''
        if p:
            return {'suggest': os.path.basename(p), '案件名': r['案件'],
                    '案号': r['案号'], 'sheet': r['sheet'], 'row': r['row']}
    r = hits[0]
    return {'suggest': None,
            'note': '台账命中「%s」但没找到对应案件夹——先建夹或手动选' % r['案件']}


_LINK_KEY_RE = re.compile(r'^[A-Za-z0-9+/=_\-]+$')


def api_court_sms_preview(body):
    r"""法院短信解析 + 拉文书列表 + 案号建议案件（只读；向法院平台拉取）。

    零落盘、零本地数据上行——请求只带短信里的签名参数（sdbh/qdbh/sdsin）。
    """
    sms = str(body.get('sms') or '')
    p = court_sms_ops.parse_sms(sms)
    out = {'ok': True, 'parse': p, 'match': _court_sms_match(p.get('case_no')),
           'docs': [], 'fetch_note': ''}
    if not p.get('link'):
        out['fetch_note'] = ('短信里没有识别到「一张网」送达链接（须含 zxfw.court.gov.cn）'
                             '——省级自建平台短信暂不支持')
        return out
    r = court_sms_ops.fetch_docs(p['link'])
    if r.get('ok'):
        for d in r['docs']:                                   # 批 72：附「拟归入」（下载即归位预览）
            sg = case_ops.suggest_dir(d.get('name'))
            d['dir'], d['dir_reason'] = sg['dir'], sg['reason']
        out['docs'] = r['docs']
    else:
        out['fetch_note'] = r.get('error') or '拉取失败'
    return out


def api_court_sms_download(body):
    r"""下载送达文书 → 案件夹 `00-收件箱`（dry-run 默认；confirm 才落盘）。

    批 67：`stash=True` → 落「短信文书暂存」（`<biz_root>\接收到的文件\短信文书\`，
    供案件还没建卷时帮带教下载用；之后可在「散件」页入库到案件）。
    批 72：普通下载落盘后**下载即归位**——按「扫描→确认归位」同一套分类器直接归位
    （判决书→04-法院文件 等）；认不准 / 重复 / 冲突 → 留在收件箱待人工。留痕进 intake-log。
    批 74：目标为分组夹（下含 ≥2 案件子夹）时回 warn（纯提示不阻断）。
    批 75：归位完成后**自动打开目标文件夹**（归位目录去重；全留收件箱时打开收件箱）。
    """
    case = str(body.get('case') or '').strip()
    stash = bool(body.get('stash'))
    confirm = bool(body.get('confirm'))
    if stash:
        p = os.path.join(case_ops.biz_root(), '接收到的文件', '短信文书')
        target_label = '短信文书暂存'
    else:
        p, err = _case_path_safe_rel(case)
        if err:
            return {'error': err}
        target_label = case
    link = body.get('link') or {}
    if not all(isinstance(link.get(k), str) and _LINK_KEY_RE.match(link[k] or '')
               for k in ('sdbh', 'qdbh', 'sdsin')):
        return {'error': '链接参数缺失或含非法字符（安全闸：须 sdbh/qdbh/sdsin 三齐）'}
    r = court_sms_ops.download(p, {'sdbh': link['sdbh'], 'qdbh': link['qdbh'],
                                   'sdsin': link['sdsin']}, apply=confirm,
                               subdir='' if stash else '00-收件箱')
    r['stash'] = stash
    # 批 74：目标为「分组夹」（下含 ≥2 个案件子夹）时提醒——防误落（实踩：许明竹案误落 A海大）
    if not stash and not r.get('error'):
        try:
            extra = [d for d in case_ops.case_dirs(p) if d not in case_ops.ALLOWED_DIRS]
            if case_ops.is_series_case(p, extra):
                n = len([d for d in extra if case_ops.SERIES_RE.search(d)])
                r['warn'] = ('注意：目标「%s」是分组夹（下含 %d 个案件子夹）——文书将归入分组夹本身；'
                             '如需归入具体案件，请在上方下拉改选「↳ …（%s）」的子案项'
                             % (os.path.basename(p), n, os.path.basename(p)))
        except Exception:                                     # noqa: BLE001
            pass
    if not confirm and not stash and r.get('docs'):
        for d in r['docs']:                                   # 批 72：dry-run 附「拟归入」
            d['dir'] = case_ops.suggest_dir(d.get('name'))['dir']
    if confirm and r.get('ok') and r.get('downloaded'):
        log('COURT_SMS_DL', '%s ← 法院短信送达 %d 份' % (target_label, len(r['downloaded'])))
        if not stash:                                        # 批 72：下载即归位（认不准/冲突留收件箱）
            try:
                af = case_ops.auto_file_names(
                    p, [d['name'] for d in r['downloaded']],
                    allow_sub=('/' in case or '\\' in case))
                r['filed'], r['left'] = af['filed'], af['left']
                if af['filed']:
                    log('COURT_SMS_FILE', '%s ← 下载即归位 %d 份'
                        % (target_label, len(af['filed'])))
            except Exception as e:                           # noqa: BLE001
                r['filed'], r['left'] = [], [{'name': '(全部)', 'reason': '自动归位失败：%r' % e}]
            # 批 75：下载完成自动打开目标文件夹（归位目录去重；全留收件箱 → 打开收件箱）
            try:
                dirs = []
                for x in (r.get('filed') or []):
                    d = os.path.join(p, x['dir'])
                    if d not in dirs:
                        dirs.append(d)
                if not dirs:
                    dirs.append(os.path.join(p, '00-收件箱'))
                opened = []
                for d in dirs[:3]:                           # 最多 3 个，防一次弹一串
                    ok_one, _dt = _open_in_explorer(d)
                    if ok_one:
                        opened.append(os.path.basename(d))
                if opened:
                    r['opened'] = opened
            except Exception:                                # noqa: BLE001
                pass
    return r


def api_court_sms_open_stash():
    """批 67：打开「短信文书暂存」文件夹（不存在先建；打开文件夹非不可逆，不需 confirm）。"""
    p = os.path.join(case_ops.biz_root(), '接收到的文件', '短信文书')
    try:
        os.makedirs(p, exist_ok=True)
    except OSError as e:
        return {'ok': False, 'error': '创建暂存目录失败：%r' % (e,)}
    ok, detail = _open_in_explorer(p)
    return {'ok': bool(ok), 'opened': p if ok else '', 'error': None if ok else detail}


# ---------------------------------------------------------------- 执行款（批 62）

def api_payments(folder):
    r"""案件执行款清单 + 汇总（自建库只读）。"""
    folder = str(folder or '').strip()
    if not folder:
        return {'error': '缺案件夹名'}
    return {'ok': True, 'items': payment_ops.list_payments(folder),
            'summary': payment_ops.summary(folder)}


def api_payment_add(body):
    r"""记一笔回款（自建库写；confirm 门；写前自动备份 db）。"""
    if not body.get('confirm'):
        return {'dry_run': True,
                'will_add': {k: body.get(k) for k in ('folder', 'pay_date', 'amount', 'source', 'note')}}
    try:
        pid = payment_ops.add_payment(
            case_folder=body.get('folder'), pay_date=body.get('pay_date'),
            amount=body.get('amount'), source=body.get('source', ''),
            note=body.get('note', ''), case_ref=body.get('ref', ''))
    except ValueError as e:
        return {'error': str(e)}
    log('PAYMENT_ADD', '%s +%s 元 (id=%s)' % (body.get('folder'), body.get('amount'), pid))
    return {'ok': True, 'id': pid}


def api_payment_delete(body):
    r"""删一笔回款（自建库；confirm 门；不回删台账已同步行）。"""
    if not body.get('confirm'):
        return {'dry_run': True, 'will_delete': body.get('id')}
    try:
        n = payment_ops.delete_payment(body.get('id'))
    except (TypeError, ValueError) as e:
        return {'error': 'id 不合法：%r' % e}
    log('PAYMENT_DEL', 'id=%s 删除 %d 行' % (body.get('id'), n))
    return {'ok': True, 'removed': n}


def api_payment_sync(body):
    r"""同步未同步回款 → 台账「执行款」sheet（受控写入；dry-run 默认）。

    台账口径：案件 / 案号取「案件夹 → 台账行」匹配结果（认不出用夹名 / 空案号，不猜）。
    """
    folder = str(body.get('folder') or '').strip()
    confirm = bool(body.get('confirm'))
    p, err = _case_path_safe(folder)
    if err:
        return {'error': err}
    pend = [r for r in payment_ops.list_payments(folder) if not r['synced_at']]
    if not pend:
        return {'ok': True, 'action': 'none', 'note': '没有待同步记录（都已同步）'}
    try:
        mt = case_ops.match_case_ledger(folder, case_ops.ledger_case_rows()).get('matched')
    except Exception:                                         # noqa: BLE001
        mt = None
    case_name = (mt or {}).get('案件') or folder
    case_no = (mt or {}).get('案号') or ''
    rows = [{'案件': case_name, '案号': case_no, '回款日期': r['pay_date'],
             '金额': r['amount'], '来源': r['source'], '备注': r['note']} for r in pend]
    if not confirm:
        r = ledger_ops.sync_payments(rows, apply=False)
        r['case_name'] = case_name
        return r
    r = ledger_ops.sync_payments(rows, apply=True)
    if r.get('ok') and r.get('action') == 'appended':
        n = payment_ops.mark_synced([x['id'] for x in pend], r['start_row'])
        log('PAYMENT_SYNC', '%s → 台账执行款 r%s 起 %d 行（回填 %d）'
            % (folder, r['start_row'], len(pend), n))
    return r


def api_board(include_closed=False):
    r"""看板泳道（批 50，CaseBoard 反向移植）：台账在办案件按「最新状态」排成泳道。

    数据源 = 两个 SHEETS_CASE 台账行（**只读**）；分桶顺序 = `ledger_ops.STATUS_POOL`；
    池外状态（含空）归末尾「其他/未填」桶；默认排除「已结案」行（closed_count 单独报，
    include_closed=True 时并入泳道）。每卡片只给前端必需字段（当前进度截断 80 字）。
    **纯只读：不做任何台账写操作**（改状态走「台账进展」页受控回写）。
    """
    lanes = [{'status': s, 'count': 0, 'items': []} for s in ledger_ops.STATUS_POOL]
    by_status = {l['status']: l for l in lanes}
    other = {'status': '其他/未填', 'count': 0, 'items': []}
    closed_count = 0
    for sh in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sh):
            name = str(r.get('案件') or '').strip()
            if not name and not str(r.get('序号') or '').strip():
                continue                                     # 空行
            status = str(r.get('最新状态') or '').strip()
            if status == '已结案':
                closed_count += 1
                if not include_closed:
                    continue
            item = {'案件': name,
                    '委托人': str(r.get('委托人') or '').strip(),
                    '案件类别': str(r.get('案件类别') or '').strip(),
                    '案号': str(r.get('案号') or '').strip(),
                    '当前进度': str(r.get('当前进度') or '').strip()[:80],
                    '状态': status, 'sheet': sh, '_row': r.get('_row')}
            by_status.get(status, other)['items'].append(item)
    out = lanes + [other]
    for l in out:
        l['count'] = len(l['items'])
    return {'ok': True, 'lanes': out, 'closed_count': closed_count,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


def api_board_analyze(body=None):
    r"""看板 AI 分析（批 55，POST；爸爸拍板加）。

    看板数据压紧成清单 → 本地 8788 → 督办分析。台账内容**不出本机**
    （8788 = 本地积分池，桌宠壳托管）。不可达时优雅降级（页面提示，不报错）。
    日志只记件数与耗时，不记案件名。
    """
    import assistant_ops
    ok, msg = assistant_ops.llm_reachable()
    if not ok:
        return {'ok': False, 'llm': 'down',
                'msg': '桌宠/8788 不在线' + ('（%s）' % msg if msg else '')}
    b = api_board(include_closed=False)
    total_open = sum(l['count'] for l in b['lanes'])
    lines = []
    for lane in b['lanes']:
        if not lane['count']:
            continue
        lines.append('【%s】%d 件' % (lane['status'], lane['count']))
        for it in lane['items'][:20]:
            prog = str(it.get('当前进度') or '')[:40]
            lines.append('- %s（%s）%s' % (it.get('案件') or '?', it.get('案件类别') or '?',
                                           ('｜' + prog) if prog else ''))
    digest = '\n'.join(lines)[:6000]
    prompt = ('你是律师事务所的案件管理助理。以下是某律师当前在办案件按状态分组的清单'
              '（共 %d 件在办，数据来自其台账）。请用中文给出简洁督办分析（≤400字）：\n'
              '1) 哪几件最该优先跟进、为什么（结合状态与进度文字）；\n'
              '2) 有无状态堆积异常（某一档案件明显偏多）；\n'
              '3) 一句总体建议。\n'
              '【信息纪律（批 66）】只写关键信息：直接给结论与依据，不写推导过程（不要'
              '「首先…然后…」式的思考复述）、不复述清单、不客套。信息密度优先。\n'
              '只输出分析正文。\n\n%s'
              % (total_open, digest))
    t0 = time.time()
    try:
        content = assistant_ops._llm_call([{'role': 'user', 'content': prompt}], retries=1)
    except Exception as e:                                    # noqa: BLE001
        return {'ok': False, 'error': 'AI 调用失败：%s: %s' % (type(e).__name__, e)}
    log('BOARD_ANALYZE', '%d 件在办｜%.1fs' % (total_open, time.time() - t0))
    return {'ok': True, 'analysis': str(content or '').strip(),
            'open_count': total_open, 'elapsed': round(time.time() - t0, 1)}


def api_case_detail(name):
    root = case_ops.case_root()
    p = os.path.join(root, name)
    if not os.path.isdir(p) or os.path.dirname(os.path.abspath(p)) != os.path.abspath(root):
        return {'error': '案件夹不存在或越界'}
    tree = []
    for d in sorted(os.listdir(p)):
        dp = os.path.join(p, d)
        if os.path.isdir(dp):
            files = sorted(os.listdir(dp))[:40]
            tree.append({'dir': d, 'count': len(os.listdir(dp)), 'files': files})
        else:
            tree.append({'dir': '（根级文件）', 'count': 1, 'files': [d]})
    logrow = []
    il = os.path.join(p, 'intake-log.md')
    if os.path.isfile(il):
        lines = [l for l in open(il, encoding='utf-8').read().splitlines() if l.startswith('|')]
        logrow = lines[-12:]
    rows = None
    try:
        rows = case_ops.ledger_case_rows()
    except Exception as e:                                    # noqa: BLE001
        log('LEDGER-ROWS-ERROR', repr(e)[:160])
    try:
        led = case_ops.match_case_ledger(name, rows)
    except Exception as e:                                    # noqa: BLE001
        log('CASE-LEDGER-MATCH-ERROR', repr(e)[:160])
        led = {'matched': None, 'candidates': [], 'error': repr(e)[:160]}
    # 差旅发票（只读）：文件名线索挂到本案；台账案件名精确相等，或该案件名就在夹名里（系列案总夹）
    travel = {'items': [], 'total': 0}
    case_name = str((led.get('matched') or {}).get('案件') or '').strip()
    try:
        tv = case_ops.travel_invoices(rows)
        for it in tv.get('items', []):
            for x in (it.get('cases') or []):
                nm2 = str(x.get('案件') or '').strip()
                if not nm2:
                    continue
                if (case_name and nm2 == case_name) or (nm2 and nm2 in name):
                    travel['items'].append({'month': it['month'], 'name': it['name'],
                                            'size_kb': it['size_kb'],
                                            'why': x.get('为什么', '')})
                    break
        travel['total'] = len(travel['items'])
    except Exception as e:                                    # noqa: BLE001
        log('CASE-TRAVEL-ERROR', repr(e)[:160])
    return {'name': name, 'path': p, 'tree': tree, 'intake_log': logrow, 'ledger': led,
            'travel': travel, 'subcases': _case_subcases(p, rows),
            'guide_exists': os.path.isfile(os.path.join(p, case_ops.GUIDE_NAME)),
            'deepdive': _case_deepdive(p),
            'payments': {'items': payment_ops.list_payments(name),
                         'summary': payment_ops.summary(name)}}


# ---------------------------------------------------------------- 批 51：写作项目管理
# 项目化登记（自建库）+ 版本文件复制入卷（<案件夹>\02-法律文书\<项目标题>\）。
# 台账零写入；所有写动作 POST + confirm（无 confirm 只 dry-run）；文件只新增不覆盖。

def api_writing_projects(q):
    """写作项目列表（批 51，GET，只读）。"""
    return {'ok': True,
            'items': writing_ops.list_projects(status=q.get('status') or None,
                                               case=q.get('case') or None),
            'counts': writing_ops.status_counts(),
            'statuses': list(writing_ops.STATUSES),
            'doc_types': list(writing_ops.DOC_TYPES),
            'doc_dir': writing_ops.DOC_DIR}


def api_writing_project(body, confirm=False):
    """新写作项目（dry-run 给将建行；confirm 落库）。"""
    case_name = str(body.get('case') or '').strip()
    doc_type = str(body.get('doc_type') or '').strip()
    title = str(body.get('title') or '').strip()
    if not confirm:
        try:
            plan = writing_ops.plan_project(case_name, doc_type, title)
        except ValueError as e:
            return {'error': str(e)}
        return {'dry_run': True, 'plan': plan}
    try:
        proj = writing_ops.add_project(case_name, doc_type, title)
    except ValueError as e:
        return {'error': str(e)}
    log('WRITING_ADD', '#%s %s｜%s（%s）'
        % (proj['id'], proj['case_name'], proj['title'], proj['doc_type']))
    return {'ok': True, 'dry_run': False, 'project': proj}


def api_writing_status(body, confirm=False):
    """项目状态机推进（起草→修订→送审→定稿；定稿→修订 回退）。"""
    pid = body.get('id')
    to = str(body.get('to') or '').strip()
    proj = writing_ops.get_project(pid)
    if not proj:
        return {'error': '项目不存在：id=%s' % pid}
    if not confirm:
        return {'dry_run': True, 'id': proj['id'], 'from': proj['status'], 'to': to,
                'allowed': writing_ops.can_transition(proj['status'], to)}
    try:
        r = writing_ops.set_status(proj['id'], to)
    except ValueError as e:
        return {'error': str(e)}
    log('WRITING_STATUS', '#%s %s→%s' % (r['id'], proj['status'], r['status']))
    return {'ok': True, 'dry_run': False, 'project': r}


def api_writing_version(body, confirm=False):
    r"""传新版本（base64；照 /api/ems/upload 先例）。

    ⚠️ 闸门顺序（越界类判定先于存在性，防路径探测）：
      ① 文件名纯名判定 → ② 扩展名白名单 → ③ base64 解析 + 大小 → ④ 项目/案件夹/落点。
    dry-run 只算将用的 v 号与落点（不落盘）；confirm 才写文件 + 登记。
    """
    import base64
    pid = body.get('project_id')
    filename = str(body.get('filename') or '').strip()
    note = str(body.get('note') or '').strip()[:200]
    if not filename or os.path.basename(filename) != filename \
            or '/' in filename or '\\' in filename:
        return {'error': '文件名不合法（只收纯文件名，安全闸）'}
    if os.path.splitext(filename)[1].lower() not in writing_ops.ALLOWED_EXT:
        return {'error': '只支持 %s（收到：%s）'
                         % ('/'.join(writing_ops.ALLOWED_EXT),
                            os.path.splitext(filename)[1].lower() or '无扩展名')}
    data = str(body.get('data') or '')
    if not data:
        return {'error': '缺 data（base64 文件内容）'}
    if ',' in data[:80]:                                     # 容忍 dataURL 前缀
        data = data.split(',', 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception as e:                                   # noqa: BLE001
        return {'error': 'base64 解析失败：%s' % e}
    if len(raw) == 0:
        return {'error': '文件内容为空'}
    if len(raw) > UPLOAD_MAX_MB * 1048576:
        return {'error': '文件过大（%d KB > %d MB 上限）'
                         % (len(raw) // 1024, UPLOAD_MAX_MB)}
    if not confirm:
        try:
            plan = writing_ops.plan_version(pid, filename)
        except ValueError as e:
            return {'error': str(e)}
        return {'dry_run': True, 'plan': plan, 'size_kb': round(len(raw) / 1024, 1)}
    try:
        v = writing_ops.add_version(pid, filename, raw, note=note)
    except ValueError as e:
        return {'error': str(e)}
    log('WRITING_VERSION', '#%s v%d %s（%d KB）'
        % (v['project_id'], v['version_no'], v['filename'], len(raw) // 1024))
    return {'ok': True, 'dry_run': False, 'version': v, 'size_kb': round(len(raw) / 1024, 1)}


def api_writing_versions(q):
    """某项目的版本清单（批 51，GET，只读）+ 打开所需的绝对路径。"""
    try:
        pid = int(q.get('project_id') or 0)
    except (TypeError, ValueError):
        return {'error': 'project_id 必须是数字'}
    proj = writing_ops.get_project(pid)
    if not proj:
        return {'error': '项目不存在：id=%s' % pid}
    items = writing_ops.list_versions(pid)
    case_dir = os.path.join(case_ops.case_root(), proj['case_name'])
    for it in items:
        it['abs_path'] = os.path.join(case_dir, it['rel_path'])
        it['ext'] = os.path.splitext(it['filename'])[1].lower()
    return {'ok': True, 'project': proj, 'items': items, 'case': proj['case_name'],
            'doc_dir': writing_ops.DOC_DIR}


def api_writing_view(q):
    r"""写作版本页内**文本**预览（批 51，GET）：只放 .md/.txt（PDF/图片走 file_view 直出）。

    ⚠️ 安全闸五道（**顺序要紧**，越界先于存在性，照 api_deepdive_view）：
      ① case 纯夹名 + 案件根第一层；② rel 必须相对、无 .. 段、无盘符；
      ③ 只放行 .md/.txt；④ 最终路径必须落在 <案件夹>\02-法律文书 内；⑤ 存在性 + ≤2MB。
    """
    p, err = _case_path_safe(q.get('case'))
    if err:
        return {'error': err}
    rel = str(q.get('rel') or '').replace('\\', '/').strip()
    if not rel or rel.startswith('/') or ':' in rel:
        return {'error': 'rel 必须是案件夹内相对路径（安全闸）'}
    parts = [x for x in rel.split('/') if x not in ('', '.')]
    if not parts or any(x == '..' for x in parts):
        return {'error': '路径不能包含 .. （安全闸）'}
    if os.path.splitext(parts[-1])[1].lower() not in ('.md', '.txt'):
        return {'error': '只支持 .md / .txt 文本预览（PDF/图片走文件直出，docx 用系统程序打开）'}
    base = os.path.abspath(os.path.join(p, writing_ops.DOC_DIR))
    full = os.path.abspath(os.path.join(p, *parts))          # rel 相对**案件夹**（含 02-法律文书 前缀）
    if not full.startswith(base + os.sep):
        return {'error': '路径不在 %s 目录内（安全闸）' % writing_ops.DOC_DIR}
    if not os.path.isfile(full):
        return {'error': '版本文件不存在（可能已被移动或改名）'}
    size = os.path.getsize(full)
    if size > 2 * 1024 * 1024:
        return {'error': '文件过大（%.1f MB > 2 MB），请用系统程序打开'
                         % (size / 1048576)}
    try:
        with open(full, encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        return {'error': '不是 UTF-8 文本，无法页内预览'}
    log('VIEW_WRITING', '%s（%d KB）' % (parts[-1], size // 1024))
    return {'ok': True, 'name': parts[-1], 'size_kb': round(size / 1024, 1), 'text': text}


def api_intake_plan(case_name):
    p = os.path.join(case_ops.case_root(), case_name)
    plan = case_ops.plan_intake(p, allow_sub=('/' in case_name or '\\' in case_name))
    items = [{k: v for k, v in it.items() if k in ('name', 'dir', 'newname', 'status',
                                                   'reason')} for it in plan['items']]
    for it in items:
        # 批 41.3：仅 PDF/图片给「本地识别」入口（与 ocr_ops.SUPPORTED 同源口径）
        it['ocr'] = os.path.splitext(it['name'])[1].lower() in ocr_ops.SUPPORTED
    return {'case': case_name, 'inbox_exists': plan['inbox_exists'], 'items': items,
            'suggest': sum(1 for i in items if i['status'] == '建议'),
            'manual': sum(1 for i in items if i['status'] in ('需人工', '冲突(同名不同内容)')),
            'conflict': sum(1 for i in items if i['status'].startswith('冲突'))}


def api_ocr_plan(case_name, name):
    """单文件本地 OCR 可识别性 + （若已识别）建议归位"""
    p = os.path.join(case_ops.case_root(), case_name)
    r = ocr_ops.plan(p, name)
    t = ocr_ops.read_text(p, name)
    if t is not None:
        r['chars'] = len(t)
        r['suggest'] = ocr_ops.suggest(p, name, t)
    return r


def api_ocr_split_plan(case_name, name):
    """批 65：大 PDF 分段识别计划（只读）——页数 / 段数 / 续跑起点 / 进度"""
    p = os.path.join(case_ops.case_root(), case_name)
    return ocr_ops.split_plan(p, name)


def api_ocr_split_run(case_name, name, seg=None, force=False):
    """批 65：大 PDF 分段识别一个段（前端循环调用；每段分钟级，段级可中断续跑）"""
    p = os.path.join(case_ops.case_root(), case_name)
    r = ocr_ops.run_segment(p, name, seg=seg, force=force)
    if r.get('ok') and r.get('done'):
        text = ocr_ops.read_text(p, name) or ''
        r['suggest'] = ocr_ops.suggest(p, name, text)
        r['head'] = text[:600]
        r['path'] = ocr_ops.cache_path(p, name)
        try:
            r['src'] = ocr_ops.abs_path(p, name)        # 批 66：原文件绝对路径（看图补正预览用）
        except ValueError:
            pass
        if not r.get('cached'):
            log('OCR_SPLIT', '%s / %s → 分段识别完成 %s 页'
                % (case_name, name, r.get('total_pages', '')))
    elif r.get('ok'):
        log('OCR_SPLIT', '%s / %s → 段 %s/%s（已识 %s/%s 页）'
            % (case_name, name, r.get('seg'), r.get('segs_total'),
               r.get('done_pages'), r.get('total_pages')))
    return r


def api_ocr_text(case_name, name):
    """批 66：读识别全文（对照原图修字用；安全闸：案卷内 + 已有缓存）"""
    p = os.path.join(case_ops.case_root(), case_name)
    try:
        ocr_ops.abs_path(p, name)                        # 越界先在（_resolve 抛错）
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    t = ocr_ops.read_text(p, name)
    if t is None:
        return {'ok': False, 'error': '还没有识别结果（先跑一次识别）'}
    return {'ok': True, 'chars': len(t), 'text': t}


def api_ocr_save_text(case_name, name, text):
    """批 66：保存人工修正的识别文本 → 重跑归位建议（旧版留 .md.bak）"""
    p = os.path.join(case_ops.case_root(), case_name)
    r = ocr_ops.save_text(p, name, text)
    if not r.get('ok'):
        return r
    t = ocr_ops.read_text(p, name) or ''
    r['suggest'] = ocr_ops.suggest(p, name, t)
    log('OCR_EDIT', '%s / %s → %d 字（人工修正）' % (case_name, name, r.get('chars', 0)))
    return r


def api_deadlines():
    data = deadline_ops.load()
    items = []
    for it in data.get('items', []):
        if it.get('状态') == 'done':
            it = dict(it, 档位='已完成')
            items.append(it)
            continue
        try:
            left, lv = deadline_ops.judge(it['到期日'])
        except Exception:
            left, lv = None, '异常'
        items.append(dict(it, 剩余天=left, 档位=lv))
    order = {'逾期': 0, '当日': 1, '3日内': 2, '7日内': 3, '正常': 4, '已完成': 5, '异常': 6}
    items.sort(key=lambda i: (order.get(i.get('档位'), 9), i.get('到期日') or ''))
    return {'generated_at': data.get('generated_at'), 'items': items,
            'manual_hints': data.get('manual_hints', []),
            'types': deadline_ops.TYPES, 'due_count': len(deadline_ops.due_items(7))}


def api_new_case_preview(q):
    return case_ops.plan_new_case(q.get('case_no', ''), q.get('client', ''),
                                  q.get('opponent', ''), q.get('cause', ''),
                                  q.get('type', '诉讼'))


def _letter_file_kind(name):
    """按文件名判文档类（**只做展示分类，不动任何文件**）"""
    n = str(name)
    if '律师函' in n:
        return '律师函'
    if '对账单' in n or '对账' in n:
        return '对账单'
    if '合同' in n or '协议' in n:
        return '合同/协议'
    if '身份证' in n or '户籍' in n or '营业执照' in n:
        return '主体资料'
    if n.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.mp4', '.mov')):
        return '影像'
    return '其他'


def _letter_folder_files(folder):
    """债务人夹内文件清单（只读）。_ocr 子夹里的文本单独计数，不混进材料清单。"""
    out = {'files': [], 'subdirs': [], 'ocr': 0}
    if not os.path.isdir(folder):
        return out
    for r, dirs, fs in os.walk(folder):
        rel = os.path.relpath(r, folder)
        if rel == '.':
            out['subdirs'] = sorted(dirs)
        for f in sorted(fs):
            if f.startswith('~$') or f.startswith('.'):
                continue
            fp = os.path.join(r, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if f.lower().endswith('.md') and 'ocr' in rel.lower():
                out['ocr'] += 1
                continue
            out['files'].append({
                'name': f, 'sub': '' if rel == '.' else rel,
                'size_kb': round(st.st_size / 1024.0, 1),
                'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d'),
                'kind': _letter_file_kind(f)})
    out['files'].sort(key=lambda x: (x['kind'], x['name']))
    return out


def _match_letter_folder(obj, cli, folders):
    """台账行（收函对象 / 委托人）→ 债务人夹名。认不出返回 ('', 0)，**不硬套**。

    依据：收函对象里任一 token（≥2 字）出现在夹名 → +2；委托人出现在夹名 → +1；
         夹名本身出现在收函对象里 → +2。取最高分，需 ≥2 才认定。
    """
    # 分层判定（批 39b）：高优先层命中即**不再看低优先层**，避免同分靠遍历顺序取胜。
    #   第 1 层（强 +4）：收函对象里任一 token（≥2 字）命中夹名（如「霍山县…」→ 霍山）
    #   第 2 层（强 +4）：夹名整串出现在收函对象里（短夹名，如「郑思建」）
    #   第 3 层（弱 +2）：夹名出现在**委托人**列（律师函常按我方委托人立夹；
    #                    实踩：夹名「黎林培」是 r11 的委托人、收函对象却是「安徽速煦」）
    cands = []
    toks = [t.strip() for t in re.split(r'[、，,;；\s+]+', obj) if len(t.strip()) >= 2]
    for f in folders:
        nm = f['name']
        if len(nm) < 2:
            continue
        sc = 0
        if any(t in nm for t in toks):
            sc = 4                                  # 第 1 层
        elif nm in obj:
            sc = 4                                  # 第 2 层
        elif cli and nm in cli:
            sc = 2                                  # 第 3 层
        if sc:
            cands.append((sc, len(nm), nm))
    if not cands:
        return '', 0
    cands.sort(key=lambda x: (-x[0], -x[1]))
    top_sc, top_len, top_nm = cands[0]
    if top_sc < 2:
        return '', top_sc
    # 同层同长并列 → 多夹同分，交人工（不猜）
    tied = [c for c in cands if c[0] == top_sc and c[1] == top_len]
    if len(tied) > 1:
        return '', top_sc
    return top_nm, top_sc


# ============================================================ 批 39d 留痕与文件管理

def _trace_case_dir(name):
    """案件名 → 案卷绝对路径（三层守卫，防越界）"""
    root = trace_ops.case_root()
    if not name:
        raise ValueError('缺案件名')
    base = os.path.basename(str(name).strip().replace('/', os.sep))
    if base in ('', '.', '..'):
        raise ValueError('案件名非法')
    d = os.path.join(root, base)
    rr, dd = os.path.realpath(root), os.path.realpath(d)
    if not (dd == rr or dd.startswith(rr + os.sep)) or not os.path.isdir(dd):
        raise ValueError('案件夹不存在或越界：%s' % base)
    return dd


# ---------------------------------------------------------------- 批 53：LLM 抽取预填
# 新案材料 → 本机 OCR → **本地 8788** LLM 抽取 → 预填开新案表单（人工核对后才提交）。
# ⚠️ 红线（爸爸拍板口径）：OCR 文本**只发本地 8788**；抽取结果只进表单草稿——
#    不写台账、不建案、绝不自动提交（提交权在人）。

PREFILL_MAX_CHARS = 8000
PREFILL_TIMEOUT = 150
PREFILL_KEYS = ('委托人', '对方', '案由', '法院', '案号', '标的额', '立案日期')
PREFILL_PROMPT = (
    '你是法律工作台的开案信息提取器。从下面的材料文本中抽取字段，'
    '只输出一个 JSON 对象（JSON-only：不要解释、不要 markdown 代码块、不要多余文字）。\n'
    '键固定为：委托人 / 对方 / 案由 / 法院 / 案号 / 标的额 / 立案日期。\n'
    '规则：\n'
    '1. 抽不出的字段一律用空字符串 ""，禁止猜测、禁止编造；\n'
    '2. 案号必须含「（20XX）…号」字号格式才填，否则留空；\n'
    '3. 金额只填数字与单位（如 1250000 元 或 125万元）；\n'
    '4. 日期用 YYYY-MM-DD 格式；\n'
    '5. 委托人 / 对方 / 案号必须原样抄录材料原文，不得改写、不得简称。\n'
    '材料文本：\n---\n%s\n---\n'
    '现在输出 JSON：'
)
PREFILL_CNO_RE = re.compile(r'[（(]\s*20\d{2}\s*[）)]')


def _prefill_mock():
    r"""测试桩（**仅测试用**）：`PREFILL_MOCK_FILE` 指向 JSON 文件时按其内容替代
    OCR / LLM（零 OCR 零网络零积分）。支持键：`ocr_text`（替代 OCR 结果）、
    `llm_content`（替代 LLM 原始输出，**走真实解析链**）。不设该 env 时零影响。"""
    p = os.environ.get('PREFILL_MOCK_FILE')
    if p and os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception as e:                               # noqa: BLE001
            sys.stderr.write('prefill mock load failed: %r\n' % (e,))
    return {}


def _parse_prefill_fields(content):
    """LLM 输出文本 → 字段 dict（7 键固定）；解析失败返回 None。"""
    s = str(content or '').strip()
    if s.startswith('```'):                                  # 容忍代码围栏
        s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
        s = re.sub(r'\s*```$', '', s).strip()
    obj = None
    try:
        obj = json.loads(s)
    except Exception:                                        # noqa: BLE001
        m = re.search(r'\{[\s\S]*\}', s)                     # 兜底：取第一个 {…} 块
        if m:
            try:
                obj = json.loads(m.group())
            except Exception:                                # noqa: BLE001
                obj = None
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in PREFILL_KEYS:
        v = obj.get(k)
        out[k] = str(v).strip() if v not in (None, '') else ''
    # 案号格式硬闸（prompt 已要求，代码再兜一层——零容忍项宁可空，不让可疑案号进表单）
    if out['案号'] and not PREFILL_CNO_RE.search(out['案号']):
        out['案号'] = ''
    return out


def api_newcase_prefill(body):
    r"""从材料抽取开案字段（批 53，POST；只读 + 表单草稿）。

    入参二选一：
      · `{case, name}`   —— 案卷内已支持格式文件（走 ocr_ops.run，**读缓存优先**）
      · `{filename, b64}` —— base64 上传（照 EMS 先例；暂存 data\prefill_upload 后 run_file）
    流程：安全闸（纯夹名/白名单扩展名/大小上限）→ OCR → 截断 ≤8000 字 →
          llm_reachable() 探活 → 本地 8788 抽取 → 解析 JSON。
    返回：`{'ok':True,'fields':…,'ocr_chars':N,'elapsed':秒}`；
         LLM 不可达 `{'ok':False,'llm':'down','msg':'桌宠/8788 不在线'}`（不报错页面）。
    ⚠️ 日志只记「文件+字符数+耗时」，**不记材料正文**。
    """
    import base64
    t0 = time.time()
    mock = _prefill_mock()
    source = ''
    if body.get('case') or body.get('name'):
        p, err = _case_path_safe(body.get('case'))
        if err:
            return {'ok': False, 'error': err}
        name = str(body.get('name') or '').strip()
        if not name:
            return {'ok': False, 'error': '缺 name（案卷内文件名）'}
        if not ocr_ops.is_supported(name):
            return {'ok': False, 'error': '只支持 %s' % '、'.join(sorted(ocr_ops.SUPPORTED))}
        source = name
        if 'ocr_text' in mock:
            text = str(mock.get('ocr_text') or '')
        else:
            r = ocr_ops.run(p, name)
            if not r.get('ok'):
                return {'ok': False, 'error': r.get('error') or 'OCR 失败'}
            text = r['text']
    else:
        filename = os.path.basename(str(body.get('filename') or '').strip())
        data = str(body.get('b64') or body.get('data') or '')
        if not filename or not data:
            return {'ok': False, 'error': '给 {case,name} 或 {filename,b64} 二选一'}
        if not ocr_ops.is_supported(filename):
            return {'ok': False, 'error': '只支持 %s' % '、'.join(sorted(ocr_ops.SUPPORTED))}
        if ',' in data[:80]:                                 # 容忍 dataURL 前缀
            data = data.split(',', 1)[1]
        try:
            raw = base64.b64decode(data)
        except Exception as e:                               # noqa: BLE001
            return {'ok': False, 'error': 'base64 解析失败：%s' % e}
        if len(raw) == 0:
            return {'ok': False, 'error': '文件内容为空'}
        if len(raw) > UPLOAD_MAX_MB * 1048576:
            return {'ok': False, 'error': '文件过大（> %d MB），请压缩后重试' % UPLOAD_MAX_MB}
        source = filename
        if 'ocr_text' in mock:
            text = str(mock.get('ocr_text') or '')
        else:
            d = os.path.join(HERE, 'data', 'prefill_upload')
            os.makedirs(d, exist_ok=True)
            stage = os.path.join(d, '%s-%s'
                                 % (datetime.now().strftime('%Y%m%d-%H%M%S'), filename))
            try:
                with open(stage, 'wb') as f:
                    f.write(raw)
            except Exception as e:                           # noqa: BLE001
                return {'ok': False, 'error': '暂存失败：%s' % e}
            try:
                r = ocr_ops.run_file(stage)                  # 任意路径识别（不写案卷缓存）
                if not r.get('ok'):
                    return {'ok': False, 'error': r.get('error') or 'OCR 失败'}
                text = r['text']
            finally:
                try:
                    os.unlink(stage)                         # 批 54：暂存即用即删（案卷材料最小留存，不堆积）
                except OSError:
                    pass
    text = str(text or '')
    ocr_chars = len(text)
    if not text.strip():
        return {'ok': False, 'error': 'OCR 没有读出可用的文字（扫描件太糊？换一张试试）'}
    text8k = text[:PREFILL_MAX_CHARS]
    if 'llm_content' in mock:                                # 测试桩：跳过探活与调用
        content = str(mock.get('llm_content') or '')
    else:
        ok, msg = assistant_ops.llm_reachable()
        if not ok:
            return {'ok': False, 'llm': 'down',
                    'msg': '桌宠/8788 不在线' + ('（%s）' % msg if msg else '')}
        try:
            content = assistant_ops._llm_call(
                [{'role': 'user', 'content': PREFILL_PROMPT % text8k}],
                timeout=PREFILL_TIMEOUT, retries=1)
        except Exception as e:                               # noqa: BLE001
            return {'ok': False, 'error': 'AI 调用失败：%s: %s' % (type(e).__name__, e)}
    fields = _parse_prefill_fields(content)
    if fields is None:
        return {'ok': False, 'error': 'AI 返回的不是可解析的 JSON——请重试，或直接手工填写'}
    elapsed = round(time.time() - t0, 1)
    log('PREFILL', '%s｜%d字%s｜%.1fs' % (source, ocr_chars,
                                         '（截断8000）' if ocr_chars > PREFILL_MAX_CHARS else '',
                                         elapsed))
    return {'ok': True, 'fields': fields, 'ocr_chars': ocr_chars, 'elapsed': elapsed,
            'truncated': ocr_chars > PREFILL_MAX_CHARS}


def api_trace_index():
    """留痕与文件管理页（只读）：案卷清单 + 全案体检汇总

    **只读，不移动不删除任何文件。** 归集动作必须另走 /api/trace/gather。
    """
    agg = trace_ops.sweep_all()
    brief = []
    for c in agg.get('cases', []):
        mp = c.get('misplace') or {}
        brief.append({
            'case': c['case'], 'total': c['total'],
            'process_count': c['process_count'], 'final_count': c['final_count'],
            'junk_count': len(c['junk']),
            'alias_count': len(c['alias_dirs']),
            'naming_count': c['naming_issue_count'],
            'family_count': c['version_family_count'],
            'misplace_count': mp.get('total', 0),
            'court_in_proc_n': mp.get('court_in_proc_n', 0),
        })
    brief.sort(key=lambda x: -(x['misplace_count'] * 4 + x['family_count']))
    return {'cases': brief, 'case_count': agg.get('case_count', 0),
            'junk_total': agg.get('junk_total', 0),
            'alias_total': agg.get('alias_total', 0),
            'naming_total': agg.get('naming_total', 0),
            'family_total': agg.get('family_total', 0),
            'misplace_total': agg.get('misplace_total', 0),
            'court_in_proc_total': agg.get('court_in_proc_total', 0),
            'generated_at': agg.get('generated_at', ''),
            'conventions': {'process': list(trace_ops.PROCESS_DIRS),
                            'final': list(trace_ops.FINAL_DIRS),
                            'junk': list(trace_ops.JUNK_NAMES)}}


def api_trace_case(name):
    """单案详情（只读）：分区 / 口径问题 / 命名问题 / 版本族 / junk"""
    d = trace_ops.sweep(_trace_case_dir(name))
    d.pop('process_files', None)          # 大字段，另有接口
    return d


def api_trace_files(name, folder=''):
    """列某案某目录下的文件（只读），带分类判定

    folder 为空 = 案卷根；否则为相对路径（三层守卫）。
    """
    case = _trace_case_dir(name)
    sub = str(folder or '').strip().replace('/', os.sep)
    if sub in ('', '.'):
        d = case
    else:
        if os.path.isabs(sub) or '..' in sub.split(os.sep):
            raise ValueError('目录参数非法')
        d = os.path.join(case, sub)
        rc, rd = os.path.realpath(case), os.path.realpath(d)
        if not (rd == rc or rd.startswith(rc + os.sep)) or not os.path.isdir(d):
            raise ValueError('目录不存在或越界')
    items = []
    for f in sorted(os.listdir(d)):
        fp = os.path.join(d, f)
        if os.path.isdir(fp):
            items.append({'type': 'dir', 'name': f,
                          'rel': os.path.relpath(fp, case),
                          'files': len(os.listdir(fp))})
        else:
            info = trace_ops.classify_file(fp, case)
            info['type'] = 'file'
            info['size'] = os.path.getsize(fp)
            info.pop('need_move', None)
            items.append(info)
    return {'case': name, 'folder': folder, 'dir': d, 'items': items,
            'file_count': len([i for i in items if i['type'] == 'file'])}


def api_trace_gather(body, confirm=False):
    """归集到过程区。confirm=False → dry-run 计划；True → 执行（只移动）

    参数：case（案件名）、rels（相对路径列表）、dest（可选，默认 08-过程文件）
    """
    case = _trace_case_dir(body.get('case'))
    rels = body.get('rels') or []
    if isinstance(rels, str):
        rels = [x for x in rels.split('\n') if x.strip()]
    if not rels:
        raise ValueError('没有选中要归集的文件')
    plan = trace_ops.plan_gather(case, rels, body.get('dest') or None)
    if not confirm:
        return plan
    if plan['blocked_count']:
        raise ValueError('有 %d 项被拦（%s），请先剔除后再执行'
                         % (plan['blocked_count'], plan['blocked'][0]['why']))
    r = trace_ops.apply_gather(plan, confirm=True)
    log('TRACE-GATHER', 'case=%s moved=%d -> %s'
        % (body.get('case'), r['moved'], r['proc_dir']))
    return {'done': True, 'moved': r['moved'], 'failed': r['failed'],
            'proc_dir': r['proc_dir'], 'log': r['log'],
            'items': [{'name': i['name'], 'rel': i['rel']} for i in r['items']]}


def api_trace_refile(body, confirm=False):
    """错位归位（批 39e）：把文件移到**指定目标目录**（不限过程区）

    confirm=False → dry-run 计划；True → 执行（**只移动不删除**，写 `_归位留痕.md`）。
    参数：case（案件名）、moves（[{'rel': ..., 'dest': ...}]）

    ⚠️ 与 `trace/gather` 的区别：gather 是「收进过程区」（固定目标 08-过程文件），
    refile 是「归到正确目录」（目标由调用方指定，如 02-法律文书 / 03-证据材料）。
    目标目录**必须已存在**（plan_refile 守卫，不自动建目录）。
    """
    case = _trace_case_dir(body.get('case'))
    moves = body.get('moves') or []
    if not moves:
        raise ValueError('没有要归位的文件')
    norm = []
    for mv in moves:
        if not isinstance(mv, dict):
            raise ValueError('moves 每项需为 {rel, dest} 结构')
        norm.append({'rel': mv.get('rel', ''), 'dest': mv.get('dest', '')})
    plan = trace_ops.plan_refile(case, norm)
    if not confirm:
        return plan
    if plan['blocked_count']:
        raise ValueError('有 %d 项被拦（%s），请先剔除后再执行'
                         % (plan['blocked_count'], plan['blocked'][0]['why']))
    r = trace_ops.apply_refile(plan, confirm=True)
    log('TRACE-REFILE', 'case=%s moved=%d dests=%s'
        % (body.get('case'), r['moved'],
           ','.join(sorted({i['dest'] for i in plan['items']}))))
    return {'done': True, 'moved': r['moved'], 'failed': r['failed'],
            'logs': r.get('logs', []),
            'items': [{'name': i['name'], 'rel': i['rel'], 'dest': i['dest']}
                      for i in plan['items']]}


def api_letters():
    """律师函台（批 39b）：台账行 ↔ 债务人夹 ↔ 发函进展 **三段联动**。

    爸爸 2026-09-17 拍板 ③：**签收状态 / 后续跟进开受控回写**（走 ledger_ops 三重闸）。
    认不出对应夹的行 → 明说「未认到夹」，绝不硬套。
    """
    items = []
    for r in ledger_ops.read_rows(ledger_ops.SHEET_LETTER):
        if not str(r.get('序号') or '').strip():
            continue
        d = {k: ('' if r.get(k) is None else str(r.get(k)).strip()) for k in
             ('序号', '委托人', '发函日期', '承办律师', '收函对象', '欠款金额（本金）',
              '案由', '签收状态', '后续跟进', '备注')}
        d['row'] = r['_row']
        items.append(d)
    folders = case_ops.letter_folders()
    fl = folders.get('folders', [])
    for d in items:
        nm, sc = _match_letter_folder(d.get('收函对象', ''), d.get('委托人', ''), fl)
        d['matched_folder'] = nm
        d['match_score'] = sc
        if nm:
            d['folder_files'] = _letter_folder_files(os.path.join(folders['root'], nm))
    return {'count': len(items), 'items': items, 'folders': folders,
            'sheet': ledger_ops.SHEET_LETTER,
            'write_cols': list(ledger_ops.LETTER_WRITE_COLS.keys())}


def api_contracts():
    """合同审查业务线（批 52，只读，双源）——照律师函台先例。

    ① 台账「合同审查」sheet（`ledger_ops.read_rows`；**表由爸爸在 WPS 手工建**，
       sheet 缺失时优雅返回空——页面给「怎么建表」指引，代码绝不代建/绝不写入）；
    ② `D:\\案件\\安清惠\\合同审查\\` 横向素材夹扫描（夹不存在返回 exists=False，
       **不自动建**，页面给建夹指引文案）。
    **全页只读：台账零写入、素材夹零改动。**
    """
    items = []
    for r in ledger_ops.read_rows(ledger_ops.SHEET_CONTRACT):
        name = str(r.get('合同名称') or '').strip()
        if not str(r.get('序号') or '').strip() and not name:
            continue
        d = {k: ('' if r.get(k) is None else str(r.get(k)).strip()) for k in
             ('序号', '送审日期', '委托人', '合同名称', '相对方', '合同类型',
              '审查状态', '收费', '交付日期', '备注')}
        d['row'] = r['_row']
        items.append(d)
    total, n_fee = 0.0, 0
    for d in items:
        v = _num(d.get('收费'))
        if v is not None:                                    # 非数字跳过（不猜）
            total += v
            n_fee += 1
    counts = {}
    for d in items:
        st = d.get('审查状态') or '（未填）'
        counts[st] = counts.get(st, 0) + 1
    root = os.path.join(case_ops.biz_root(), '合同审查')
    folder = {'root': root, 'exists': os.path.isdir(root), 'files': [], 'subdirs': [],
              'ocr': 0}
    if folder['exists']:
        folder.update(_letter_folder_files(root))            # 与律师函同款通用夹扫描
    return {'ok': True, 'sheet': ledger_ops.SHEET_CONTRACT, 'count': len(items),
            'items': items, 'fee_total': round(total, 2), 'fee_count': n_fee,
            'status_counts': counts, 'folder': folder}


def _open_in_explorer(path, select=None):
    """在资源管理器中打开路径（批 46）。
    - select 为 None：打开该目录
    - select 有值：打开父目录并**高亮选中**该文件/子目录

    ⚠️ 安全闸（三道，**顺序要紧**）：
      ① **越界判定最先做**——只允许案件体系内路径（case_root / biz_root / 备份根
         之下），防止网页被注入任意路径变成「任意目录浏览器」。
         ⚠️ 必须先于「存在性」判定：否则越界+不存在的路径会回「路径不存在」，
         攻击者可据此**探测目录结构**（存在=有、不存在=无）。2026-09-19 实踩修正。
      ② 路径必须**真实存在**（不存在的路径 explorer 会弹错误框）；
      ③ 仅允许**目录**做 root 打开（select 目标可为文件）。
    返回 (ok, detail)。调用方负责把结果回给前端。
    """
    p = os.path.abspath(str(path or '').strip())
    if not p:
        return False, '空路径'
    # ---- 安全闸①：越界判定（**最先**，先于存在性）----
    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:                                # noqa: BLE001
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    inside = any(p == r or p.startswith(r + os.sep) for r in roots if r)
    if not inside:
        return False, '路径不在案件/备份体系内，已拒绝（安全闸）'
    # ---- 安全闸②：存在性 ----
    if not os.path.exists(p):
        return False, '路径不存在'
    try:
        if select:
            sel = os.path.abspath(str(select))
            if not os.path.exists(sel):
                return False, '要选中的文件不存在'
            # ⚠️⚠️ 2026-09-19 **实测对照结论（请勿再改回 explorer）**：
            #   `Popen(['explorer', '/select,' + path])` → **不弹窗**（爸爸实测「没反应」）
            #   `subprocess.run('explorer /select,...', shell=True)` → 同样不可靠
            #   `os.startfile(目录)` → **✅ 稳定弹窗**（三次对照实验确认）
            #   故：一律用 `os.startfile` 打开**文件所在目录**（深层子目录直接开该子目录，
            #   比在父夹里高亮更直观）；**不再调 explorer /select**。
            #   "精确高亮"改为由前端「页面内预览」承担（不依赖外部程序）。
            d = sel if os.path.isdir(sel) else os.path.dirname(sel)
            os.startfile(d)                                   # noqa: S606（实测唯一可靠的打开方式）
        else:
            if not os.path.isdir(p):
                p = os.path.dirname(p)
            os.startfile(p)                                   # noqa: S606
        log('OPEN_DIR', '%s%s' % (p, '｜select=' + str(select)[:60] if select else ''))
        return True, p
    except Exception as e:                                   # noqa: BLE001
        return False, '%s: %s' % (type(e).__name__, e)


def api_letter_preview_file(body):
    r"""页面内预览：把案卷里的**图片**读成 base64 返回，供前端直接 <img> 展示（批 46.4）。

    为什么需要：`os.startfile` 依赖外部看图程序，且用户在浏览器里"跳出去"体验割裂。
    小图直接在页面内看最顺（PDF/视频仍走「📄 直接打开」）。

    ⚠️ 安全与体积：
      · 安全闸同其他接口（越界**最先**判 + 必须存在 + 必须是文件）
      · 只放行**图片**扩展名；单文件 **≤ 8MB**（超出请下载/外部打开）
      · 返回 base64 dataURL，前端 `<img src>` 直接用
    """
    import base64
    rel = str(body.get('path') or '').strip()
    folder = str(body.get('folder') or '').strip()
    if not rel:
        return {'error': '缺 path'}
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    full = os.path.abspath(os.path.join(root, folder, rel)) if folder else os.path.abspath(rel)
    # ---- 安全闸：越界最先 ----
    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:                                # noqa: BLE001
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    if not any(full == r or full.startswith(r + os.sep) for r in roots if r):
        return {'error': '路径不在案件/备份体系内，已拒绝（安全闸）'}
    if not os.path.isfile(full):
        return {'error': '文件不存在'}
    ext = os.path.splitext(full)[1].lower()
    mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.bmp': 'image/bmp', '.webp': 'image/webp'}.get(ext)
    kind = 'image' if mime else ''
    if not mime and ext == '.pdf':
        # 批 46.6（爸爸拍板「全在页面内」）：PDF 也走页内——浏览器原生能渲染
        # data:application/pdf，用 <iframe> 内嵌，不依赖外部阅读器。
        mime, kind = 'application/pdf', 'pdf'
    if not mime:
        return {'error': '该类型不支持页面内预览', 'ext': ext,
                'hint': '可在下方「下载」保存后自行处理'}
    size = os.path.getsize(full)
    limit = 20 * 1024 * 1024 if kind == 'pdf' else 8 * 1024 * 1024
    if size > limit:
        return {'error': '文件过大（%.1f MB > %d MB），请用「下载」'
                         % (size / 1048576, limit // 1048576),
                'size_kb': round(size / 1024, 1), 'can_download': True}
    try:
        with open(full, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
    except Exception as e:                                   # noqa: BLE001
        return {'error': '读取失败：%s' % e}
    log('PREVIEW_FILE', '%s（%d KB，%s）' % (os.path.basename(full), size // 1024, kind))
    return {'ok': True, 'data_url': 'data:%s;base64,%s' % (mime, b64), 'kind': kind,
            'name': os.path.basename(full), 'size_kb': round(size / 1024, 1),
            'can_download': True}


def api_letter_download(body):
    r"""把案卷里的文件**以附件形式回给浏览器**（批 46.6，爸爸拍板「全在页面内」）。

    为什么需要：`os.startfile` 依赖外部程序 + 窗口层级不可控（本轮实踩：
    窗口确实开出来了、也没最小化，但用户看不到 → 反复「点了没反应」）。
    改成浏览器原生下载：**不依赖任何外部程序**，落点是用户自己的下载目录。

    ⚠️ 安全闸同其他接口（越界**最先**判 + 必须存在 + 必须是文件）。
    ⚠️ 与「预览」的分工：预览＝页内看（图片/PDF）；下载＝保存到本地（任何类型）。
    """
    import urllib.parse
    import base64
    rel = str(body.get('path') or '').strip()
    folder = str(body.get('folder') or '').strip()
    if not rel:
        return {'error': '缺 path'}
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    full = os.path.abspath(os.path.join(root, folder, rel)) if folder else os.path.abspath(rel)
    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:                                # noqa: BLE001
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    if not any(full == r or full.startswith(r + os.sep) for r in roots if r):
        return {'error': '路径不在案件/备份体系内，已拒绝（安全闸）'}
    if not os.path.isfile(full):
        return {'error': '文件不存在'}
    size = os.path.getsize(full)
    if size > 100 * 1024 * 1024:
        return {'error': '文件过大（%.1f MB > 100 MB）' % (size / 1048576)}
    try:
        with open(full, 'rb') as f:
            raw = f.read()
    except Exception as e:                                   # noqa: BLE001
        return {'error': '读取失败：%s' % e}
    name = os.path.basename(full)
    log('DOWNLOAD', '%s（%d KB）' % (name, size // 1024))
    return {'ok': True, 'filename': name, 'size_kb': round(size / 1024, 1),
            'b64': base64.b64encode(raw).decode('ascii'),
            'quoted': urllib.parse.quote(name)}


def api_letter_file_view(q):
    r"""文件直出（批 47，GET 二进制流）——浏览器原生渲染的「正解」。

    为什么需要（批 46.6 遗留的最后一环）：preview_file 的 base64 dataURL 路线
    对 PDF 不可靠——Chromium（Edge）对 iframe 内嵌 data:application/pdf 支持差
    （大概率白屏），且 base64 膨胀 33%（4MB PDF → ~5.6MB URL 字符串）。
    本接口让服务端**直接把文件字节流递给浏览器**（前端 <img>/<iframe src> 指向
    本 URL），走 Edge 原生图片/PDF 渲染器——零转码、无 URL 长度限制。
    ⚠️ **勿改回 dataURL 路线渲染 PDF**（批 47 定案，同「explorer /select 勿改回」）。

    ⚠️ 安全闸同其他接口（越界**最先**判 + 必须存在 + 必须是文件）；
       只放行**图片 + PDF**；≤100MB（同 download，直出无需更紧的 base64 式限制）。
    """
    rel = str(q.get('path') or '').strip()
    folder = str(q.get('folder') or '').strip()
    if not rel:
        return {'error': '缺 path'}
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    full = os.path.abspath(os.path.join(root, folder, rel)) if folder else os.path.abspath(rel)
    # ---- 安全闸：越界最先 ----
    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:                                # noqa: BLE001
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    if not any(full == r or full.startswith(r + os.sep) for r in roots if r):
        return {'error': '路径不在案件/备份体系内，已拒绝（安全闸）'}
    if not os.path.isfile(full):
        return {'error': '文件不存在'}
    ext = os.path.splitext(full)[1].lower()
    mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.bmp': 'image/bmp', '.webp': 'image/webp',
            '.pdf': 'application/pdf'}.get(ext)
    if not mime:
        return {'error': '该类型不支持页面内预览', 'ext': ext,
                'hint': '可用「⬇ 下载」保存后自行处理'}
    size = os.path.getsize(full)
    if size > 100 * 1024 * 1024:
        return {'error': '文件过大（%.1f MB > 100 MB），请用「下载」'
                         % (size / 1048576), 'can_download': True}
    try:
        with open(full, 'rb') as f:
            raw = f.read()
    except Exception as e:                                   # noqa: BLE001
        return {'error': '读取失败：%s' % e}
    log('VIEW_FILE', '%s（%d KB，%s）' % (os.path.basename(full), size // 1024, mime))
    return {'raw': raw, 'mime': mime, 'name': os.path.basename(full),
            'size_kb': round(size / 1024, 1)}


def api_letter_open_file(body):
    r"""用**默认程序打开文件本身**（批 46.3 爸爸要求）。

    与 `/api/letter/open_folder` 的区别：
      · open_folder：打开**资源管理器**定位到文件（选中高亮）——用于"我想看看这夹里有啥"
      · open_file：直接用**系统默认程序**打开文件（图片看大图 / PDF 开阅读器）——用于"我要看内容"
    两者场景不同，都保留。

    安全闸同 `_open_in_explorer`：只允许案件/备份体系内的**文件**（不能是目录）。
    """
    rel = str(body.get('path') or body.get('select') or '').strip()
    folder = str(body.get('folder') or '').strip()
    if not rel:
        return {'error': '缺 path（文件相对路径）'}
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    full = os.path.abspath(os.path.join(root, folder, rel)) if folder else os.path.abspath(rel)
    # ---- 安全闸：越界最先判（顺序要紧，见 _open_in_explorer 注释）----
    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:                                # noqa: BLE001
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    if not any(full == r or full.startswith(r + os.sep) for r in roots if r):
        return {'error': '路径不在案件/备份体系内，已拒绝（安全闸）'}
    if not os.path.isfile(full):
        return {'error': '文件不存在'}
    try:
        os.startfile(full)                                    # noqa: S606（默认程序打开）
        log('OPEN_FILE', full)
        return {'ok': True, 'opened': full}
    except Exception as e:                                   # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_letter_open_folder(body):
    """律师函材料夹：在资源管理器中打开（批 46 爸爸要求）。
    body: {row?} 或 {folder?} 或 {select?}（文件名）
    - 给 row → 由台账行反查债务人夹
    - 给 folder → 直接打开该夹
    - 给 select → 打开并选中该文件
    """
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    name = str(body.get('folder') or '').strip()
    sel = str(body.get('select') or '').strip()
    row = body.get('row')
    if not name and row is not None:
        items = api_letters().get('items', [])
        hit = next((v for v in items if v.get('row') == row), None)
        if not hit:
            return {'error': '该行不存在（页面可能已过期，刷新重试）'}
        name = hit.get('matched_folder') or ''
        if not name:
            return {'error': '这一行没认到债务人夹（夹名与「收函对象」对不上）'}
    if not name:
        # 不给 folder → 打开根目录
        ok, det = _open_in_explorer(root)
        return {'ok': ok, 'opened': det, 'root': root} if ok else {'error': det}
    target = os.path.join(root, name)
    if sel:
        ok, det = _open_in_explorer(target, select=os.path.join(target, sel))
    else:
        ok, det = _open_in_explorer(target)
    return {'ok': ok, 'opened': det} if ok else {'error': det}


def api_letter_rescan():
    """重新扫描律师函材料夹（批 46）：文件数 / 最新改动会变，进页时只扫一次，
    本接口供「重新扫描」按钮即时刷新（只读，不动文件）。"""
    folders = case_ops.letter_folders()
    return {'ok': True, 'folders': folders,
            'count': len(folders.get('folders') or [])}


def api_letter_folder_files(q):
    """单夹文件清单**实时**扫描（批 48）。q.folder = 债务人夹名（**纯名字，禁路径**）。

    为什么加（爸爸 09-21 实踩）：① 行级「看文件」原先用进页时的 LETTER_ITEMS 缓存，
    新放进夹子的文件（如盖章扫描件）不显示；②「重新扫描」按钮只报数不刷新。
    → 看文件/函件入口改**点按时现扫**（毫秒级 walk），数据永远新鲜，无需建索引。
    """
    folder = str(q.get('folder') or '').strip()
    if not folder:
        return {'error': '缺 folder'}
    if folder in ('.', '..') or os.sep in folder or '/' in folder or ':' in folder:
        return {'error': '夹名不合法（安全闸：只收纯夹名）'}
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    p = os.path.join(root, folder)
    if not os.path.isdir(p):
        return {'error': '夹不存在：%s' % folder}
    out = _letter_folder_files(p)
    out['ok'] = True
    out['folder'] = folder
    return out


def api_ems_recognize(body):
    """EMS 签收截图：识别 + 提炼字段（批 46）。**只读不写台账**。

    body: {path} —— 图片绝对路径（本机，浏览器不能直读本地，故由前端
    —— 先经 upload 接口存到临时区，或直接贴路径）。
    识别走**本机 OCR**（零外发）；提炼结果**交人确认**，绝不自动回填台账。
    """
    p = str(body.get('path') or '').strip()
    if not p:
        return {'error': '缺 path（图片路径）'}
    r = ems_ops.recognize(p)
    if r.get('ok'):
        log('EMS_RECOGNIZE', '%s｜%d字｜%s' % (os.path.basename(p), r.get('chars', 0),
                                               (r.get('summary') or '')[:60]))
    else:
        log('EMS_RECOGNIZE_FAIL', '%s｜%s' % (os.path.basename(p), r.get('error')))
    return r


def api_ems_upload(body):
    """接收上传的截图（base64）→ 存到临时区 → 返回可识别路径（批 46）。

    为什么用 base64 而非 multipart：本服务是极简 HTTPServer，没有 multipart
    解析器；引入插件会破坏「零依赖 stdlib」定位。JSON+base64 对本场景够用
    （截图通常 < 5MB；上限 12MB 防滥用）。
    """
    import base64
    name = os.path.basename(str(body.get('name') or 'ems.png'))
    data = str(body.get('data') or '')
    if not data:
        return {'error': '缺 data（base64）'}
    if ',' in data[:80]:                                     # 容忍 dataURL 前缀
        data = data.split(',', 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception as e:                                   # noqa: BLE001
        return {'error': 'base64 解析失败：%s' % e}
    if len(raw) > 12 * 1024 * 1024:
        return {'error': '图片过大（>12MB），请压缩后重试'}
    if not ems_ops.is_image(name):
        return {'error': '只支持图片/PDF：%s' % '、'.join(ems_ops.IMG_EXT)}
    d = os.path.join(HERE, 'data', 'ems_upload')
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    p = os.path.join(d, '%s-%s' % (stamp, name))
    try:
        with open(p, 'wb') as f:
            f.write(raw)
    except Exception as e:                                   # noqa: BLE001
        return {'error': '保存失败：%s' % e}
    log('EMS_UPLOAD', '%s（%d KB）' % (os.path.basename(p), len(raw) // 1024))
    # 上传后**顺手识别**（省一次点击）；识别失败不阻断（图片已存好）
    r = ems_ops.recognize(p)
    # 顺手给出**存放建议**（批 46.2：爸爸问「是自动匹配吗」→ 补上）
    sug = {}
    try:
        letters = api_letters()
        sug = ems_ops.suggest_folder(name, (r.get('text') or ''), letters.get('folders', {}).get('folders', []),
                                     letters.get('items', []))
    except Exception as e:                                   # noqa: BLE001
        log('EMS_SUGGEST_FAIL', repr(e)[:120])
    return {'ok': True, 'path': p, 'size_kb': len(raw) // 1024,
            'recognize': r, 'suggest': sug}


def api_letter_sign_records(body):
    r"""查某债务人夹下的**签收记录**（批 46.2）。

    爸爸问「我原来律师函文件夹里存的 EMS 签收图怎么不见了」——其实一直在
    （`海大\律师函-福建海大VS陈飞鹏\律师函及签收记录\` 下 5 对「函 PDF + 签收图」），
    只是页面没归类展示。本接口按**目录名含「签收」**跨层级捞出。
    """
    folder = str(body.get('folder') or '').strip()
    if not folder:
        return {'error': '缺 folder'}
    recs = ems_ops.find_sign_records(folder)
    if not recs:
        return {'ok': True, 'folder': folder, 'count': 0, 'items': [],
                'hint': '该夹下未找到「签收」相关目录或文件'}
    return {'ok': True, 'folder': folder, 'count': len(recs), 'items': recs,
            'dir': ems_ops.proof_dir(folder)}


def api_letter_add_row(body, confirm=False):
    """批 41：律师函台账**新增行** + 收函对象材料夹（预览/执行）。

    表单字段 = LETTER_ADD_COLS（委托人/发函日期/承办律师/收函对象/欠款金额（本金）/案由/备注）；
    确认后：台账行（三重闸）→ 材料夹（批量催收件/<收函对象>/，可选/默认建）。
    返回 {'ok', 'dry_run', 'plan', 'folder'}；台账占用/审计失败由 POST 段转 409。"""
    vals = {k: str(body.get(k) or '').strip() for k in ledger_ops.LETTER_ADD_COLS}
    plan = ledger_ops.plan_letter_row(vals)
    folder = None
    obj = vals.get('收函对象')
    if obj:
        try:
            folder = case_ops.letter_folder_plan(obj)
        except Exception as e:
            folder = {'error': str(e)}
    if not confirm:
        return {'ok': True, 'dry_run': True, 'plan': plan, 'folder': folder}
    if not obj:
        return {'error': '收函对象必填（台账行与材料夹都靠它命名）'}
    r = ledger_ops.apply_letter_row(vals)
    r.setdefault('normalized', plan.get('normalized') or [])
    if not r.get('ok'):
        return {'error': {'busy': '台账被占用（WPS / 云盘同步中），未写入',
                          'backup_failed': '台账备份未生成，已中止写入',
                          'audit_failed': '写后审计发现意外改动，请用备份回退',
                          'write_error': '写入异常，可从备份回退'}.get(r.get('code'), r.get('code')),
                 'code': r.get('code'), 'locks': r.get('locks'),
                 'unexpected': r.get('unexpected'), 'backup': r.get('backup'),
                 'plan': r.get('plan')}
    created = None
    try:
        fr = case_ops.letter_folder_apply(obj)
        created = fr
    except (ValueError, RuntimeError) as e:
        created = {'ok': False, 'error': str(e)}
    log('LETTER-ADD', 'row=%d obj=%s folder=%s backup=%s'
        % (plan['row'], obj[:40],
           ('new' if created and created.get('created') else
            'exists' if created and created.get('ok') else 'failed'), r.get('backup')))
    return {'ok': True, 'plan': r.get('plan'), 'backup': r.get('backup'),
            'folder': created}


def api_travel():
    """差旅发票索引（只读）：读文件名线索挂到台账行"""
    rows = []
    for sh in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sh):
            nm = str(r.get('案件') or '').strip()
            if nm:
                rows.append({'sheet': sh, 'row': r.get('_row'), '案件': nm,
                             '委托人': str(r.get('委托人') or '').strip()})
    return case_ops.travel_invoices(rows)


def api_scatter():
    """散件索引（批 36，只读）：律师函/合同/差旅发票/所函/介绍信/判决书/接收到的文件。

    爸爸 2026-09-17 定调：这些横向目录**故意这么放**（方便找），不归拢；
    本接口只把内容索引出来 + 标「疑似归属案件」「非经手」，一个文件都不动。
    """
    d = case_ops.scatter_index()
    marks = _scatter_marks()
    starred = 0
    for it in d.get('items', []):
        m = marks.get(it['rel'])
        it['starred'] = bool(m)
        it['star_tag'] = (m or {}).get('tag', '')
        it['star_note'] = (m or {}).get('note', '')
        if m:
            starred += 1
    d['marks'] = marks
    d['starred'] = starred
    return d


SCATTER_TAGS = ['关注', '待处理', '待分发', '参考']


def _scatter_marks():
    """读散件标记（自建库 meta 表，key=scatter:<rel>）；读不到返回空 dict"""
    try:
        return tasks_ops.meta_map('scatter:')
    except Exception:                                         # noqa: BLE001
        return {}


def api_scatter_marks():
    return {'marks': _scatter_marks(), 'tags': SCATTER_TAGS}


def api_scatter_mark(body):
    """标记/取消标记一件散件（**只写自建库，绝不碰散件文件**）"""
    rel = str(body.get('rel') or '').strip()
    if not rel:
        raise ValueError('缺 rel')
    if body.get('clear'):
        n = tasks_ops.meta_del('scatter:' + rel)
        return {'ok': True, 'cleared': bool(n), 'rel': rel}
    tag = str(body.get('tag') or '').strip() or '关注'
    if tag not in SCATTER_TAGS:
        raise ValueError('标记只允许：%s' % ' / '.join(SCATTER_TAGS))
    note = str(body.get('note') or '').strip()[:200]
    tasks_ops.meta_set('scatter:' + rel,
                       {'tag': tag, 'note': note,
                        'at': datetime.now().strftime('%Y-%m-%d %H:%M')})
    return {'ok': True, 'rel': rel, 'tag': tag, 'note': note}


def api_scatter_case_options():
    """散件入库的**候选案件**（供前端「人工指定」下拉）

    批 39c（2026-09-18）：实测 32 件可入库候选里 **5 件被「多案并存」拦住**
    （如「揭阳海大」能匹配 7 个案），原逻辑直接 blocked、人工无法干预。
    """
    try:
        rows = case_ops.ledger_case_rows()
    except Exception as e:                                    # noqa: BLE001
        log('SCATTER-LEDGER-ERROR', repr(e)[:120])
        rows = []
    return {'options': case_ops.scatter_case_options(rows)}


def api_scatter_intake(rel='', batch=False, confirm=False, case_name=''):
    """散件入库：预览（dry-run）/ 单件 / 批量。**只移动，不复制不删除；台账零写入。**

    case_name：**人工指定案件**（批 39c）——被「多案并存」或「认不出」拦住时，
    由人工选一个案件，跳过自动匹配。仍走全部守卫（案卷夹必须真实存在，不自动建）。
    """
    try:
        rows = case_ops.ledger_case_rows()
    except Exception as e:                                    # noqa: BLE001
        log('SCATTER-LEDGER-ERROR', repr(e)[:120])
        rows = []
    try:
        fmap = case_ops.folder_by_ledger_row(rows)
    except Exception:                                         # noqa: BLE001
        fmap = {}
    if batch:
        items = case_ops.scatter_index(rows).get('items', [])
        plans, done, failed = [], [], []
        for x in [y for y in items if y.get('matched')]:
            try:
                p = case_ops.plan_scatter_intake(x['rel'], rows, fmap)
            except Exception as e:                            # noqa: BLE001
                failed.append({'rel': x['rel'], 'name': x['name'], 'error': repr(e)[:120]})
                continue
            if p.get('blocked'):
                plans.append({'rel': p['rel'], 'name': p.get('name', ''),
                              'case': p.get('case', ''), 'blocked': p['blocked']})
                continue
            plans.append({'rel': p['rel'], 'name': p['name'], 'case': p['case'],
                          'dst': p['dst'], 'ok': True})
            if confirm:
                try:
                    r = case_ops.apply_scatter_intake(x['rel'], rows, fmap)
                    done.append({'rel': r['rel'], 'name': r['name'], 'dst': r['dst']})
                    log('SCATTER-INTAKE', '%s -> %s' % (r['rel'], r['dst']))
                except Exception as e:                        # noqa: BLE001
                    failed.append({'rel': x['rel'], 'name': x['name'], 'error': repr(e)[:160]})
        if not confirm:
            return {'dry_run': True, 'plan': plans, 'count': len(plans)}
        return {'dry_run': False, 'plan': plans, 'done': done, 'failed': failed}
    if not rel:
        raise ValueError('缺 rel（或 batch=1）')
    p = case_ops.plan_scatter_intake(rel, rows, fmap, case_name=case_name or None)
    if not confirm:
        return {'dry_run': True, 'plan': p, 'blocked': p.get('blocked', '')}
    r = case_ops.apply_scatter_intake(rel, rows, fmap, case_name=case_name or None)
    log('SCATTER-INTAKE', '%s -> %s' % (r['rel'], r['dst']))
    return {'dry_run': False, 'ok': True, 'dst': r['dst'], 'case': r.get('case', ''),
            'log': r.get('log', '')}


def api_sohan_meta():
    """所函页元数据（只读）：模板状态 + 阶段下拉 + 默认承办律师 + 案件夹清单

    批 39a 爸爸拍板：① 落点＝选定案卷 01-委托手续 ② 出具日期**留空**。
    """
    ok, err = sohan_ops.template_ok()
    return {'template': sohan_ops.template_path(), 'template_ok': ok,
            'template_error': err, 'template_src': sohan_ops.TPL_DOC,
            'src_exists': os.path.isfile(sohan_ops.TPL_DOC),
            'stages': sohan_ops.STAGES,
            'default_lawyer': sohan_ops.DEFAULT_LAWYER,
            'date_blank': True,
            'folders': _case_options()}


def api_sohan_plan(body):
    """所函生成计划（dry-run，不落盘）"""
    return sohan_ops.plan_sohan(
        body.get('court', ''), body.get('client', ''), body.get('lawyer', ''),
        body.get('stage', ''), body.get('opponent', ''), body.get('cause', ''),
        body.get('case_folder', ''))


def api_sohan_make(body, confirm):
    """按计划落盘（confirm 门；不确认只回计划）"""
    plan = sohan_ops.plan_sohan(
        body.get('court', ''), body.get('client', ''), body.get('lawyer', ''),
        body.get('stage', ''), body.get('opponent', ''), body.get('cause', ''),
        body.get('case_folder', ''))
    if plan.get('blocked'):
        return {'error': plan['blocked'], 'plan': plan}
    if not confirm:
        return {'dry_run': True, 'plan': plan}
    r = sohan_ops.apply_sohan(plan)
    log('SOHAN', '%s | %s' % (plan['values'].get('court', ''), r['dst']))
    return {'dry_run': False, 'dst': r['dst'], 'bytes': r['bytes']}


def api_commission_meta():
    """委托手续套件元数据（只读，批 64）：模板状态 + 档位选项 + 默认值 + 案件夹清单。

    六件套落 <案件夹>\01-委托手续\（同所函先例）；日期/签名一律留空。
    """
    ok, detail, err = commission_ops.template_status()
    return {'ok': ok, 'error': err, 'templates': detail,
            'stages': commission_ops.STAGES,
            'auth_types': commission_ops.AUTH_TYPES,
            'case_types': commission_ops.CASE_TYPES,
            'defaults': {'lawyer': commission_ops.DEFAULT_LAWYER,
                         'lawyer_phone': commission_ops.DEFAULT_LAWYER_PHONE,
                         'lawyer_license': commission_ops.DEFAULT_LAWYER_LICENSE,
                         'lawyer_addr': commission_ops.DEFAULT_LAWYER_ADDR,
                         'court': commission_ops.DEFAULT_COURT},
            'folders': _case_options()}


def api_commission_plan(body):
    """委托手续生成计划（dry-run，不落盘）"""
    return commission_ops.plan_commission(body.get('fields') or {},
                                           str(body.get('case') or '').strip())


def api_commission_build(body, confirm):
    """生成六件套（confirm 门；落 <案件夹>\01-委托手续\，重名避让）"""
    case = str(body.get('case') or '').strip()
    if not case:
        return {'error': '先选案卷——生成件要落到案卷 01-委托手续'}
    p, err = _case_path_safe(case)
    if not p:
        return {'error': err}
    plan = commission_ops.plan_commission(body.get('fields') or {}, case)
    if plan.get('blocked'):
        return {'error': plan['blocked'], 'plan': plan}
    if not confirm:
        return {'dry_run': True, 'plan': plan}
    r = commission_ops.apply_commission(plan, p)
    if r['items']:
        log('COMMISSION', '%s ← 委托手续 %d 件' % (case, len(r['items'])))
    return {'dry_run': False, 'ok': r['ok'], 'outdir': r['outdir'],
            'items': [os.path.basename(x) for x in r['items']],
            'errors': r['errors']}


def api_schedule():
    """手工日程（批 37）：独立于台账派生期限的另一套数据，人可直接编辑。

    与 `/api/deadlines` 分开——期限是台账抽出来的（改了会被下次抽取覆盖），
    日程是人写的（不碰台账）。日历页把两者合并显示，写入路径完全分开。
    """
    items = schedule_ops.load().get('items', [])
    open_items = [x for x in items if x.get('状态') == 'open']
    open_items.sort(key=lambda x: (x.get('日期') or '9999', x.get('时间') or ''))
    return {'count': len(open_items),
            'cancelled': len(items) - len(open_items),
            'kinds': schedule_ops.KINDS,
            'items': open_items,
            'upcoming': schedule_ops.upcoming(30),
            'due': schedule_ops.due_for_reminder()}


# ---------------------------------------------------------------- 发票归案（批 30）
#
# 爸爸 2026-09-13 定：上传发票 → 本机识别 → 建议归案 → **他点确认才动**；
# 落点 = 案卷 `05-费用票据`（正本）+ 公共发票库按年月（副本）；**不写台账**。
UPLOAD_DIR = os.environ.get('INVOICE_UPLOAD_DIR') or os.path.join(HERE, 'data', 'invoice_upload')
UPLOAD_MAX_MB = int(os.environ.get('INVOICE_MAX_MB', '40'))
UPLOAD_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp', '.ofd'}


def _cd_filename(cd):
    """从 Content-Disposition 取文件名（兼容 `filename*` 的 RFC 5987 编码与中文）"""
    cd = str(cd or '')
    m = re.search(r"filename\*\s*=\s*([^;]+)", cd, re.I)
    if m:
        v = m.group(1).strip().strip('"')
        if "''" in v:
            v = v.split("''", 1)[1]
        try:
            return unquote(v)
        except Exception:                                    # noqa: BLE001
            return v
    m = (re.search(r'filename\s*=\s*"([^"]*)"', cd, re.I)
         or re.search(r'filename\s*=\s*([^;]+)', cd, re.I))
    return m.group(1).strip() if m else ''


def _parse_multipart(raw, boundary):
    """极简 multipart/form-data 解析（标准库手写；只取字段名/文件名/内容）"""
    b = boundary if isinstance(boundary, bytes) else str(boundary).encode('utf-8')
    parts = []
    for seg in raw.split(b'--' + b)[1:]:
        if seg.startswith(b'--'):                            # 结束段
            break
        if seg.startswith(b'\r\n'):
            seg = seg[2:]
        elif seg.startswith(b'\n'):
            seg = seg[1:]
        idx, sep = seg.find(b'\r\n\r\n'), 4
        if idx < 0:
            idx, sep = seg.find(b'\n\n'), 2
            if idx < 0:
                continue
        head, body = seg[:idx], seg[idx + sep:]
        if body.endswith(b'\r\n'):
            body = body[:-2]
        elif body.endswith(b'\n'):
            body = body[:-1]
        hs = {}
        for line in head.decode('utf-8', 'replace').splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                hs[k.strip().lower()] = v.strip()
        cd = hs.get('content-disposition', '')
        nm = re.search(r'name="([^"]*)"', cd)
        parts.append({'field': nm.group(1) if nm else '', 'cd': cd,
                      'filename': _cd_filename(cd), 'ctype': hs.get('content-type', ''),
                      'data': body})
    return parts


def _case_options():
    root = case_ops.case_root()
    if not os.path.isdir(root):
        return []
    return sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))])


# ---------------------------------------------------------------- 归档区导入（批 39h）
# 爸爸 2026-09-18：`批量催收件` / `差旅报销` 要「导入入口」——
# 照批 30「上传发票 → 归案」同一模式：**暂存 → 人确认 → 落盘**，案卷不动直到确认。
ARCHIVE_UPLOAD_DIR = os.environ.get('ARCHIVE_UPLOAD_DIR') or os.path.join(
    HERE, 'data', 'archive_upload')


def api_archive_zones():
    """可用归档区列表（供前端下拉）"""
    return {'zones': [{'zone': k, 'kind': v['kind'], 'desc': v['desc'],
                       'label': v['label'], 'subs': v.get('subs') or []}
                      for k, v in case_ops.ARCHIVE_ZONES.items()]}


def api_archive_zone(zone):
    """某归档区清单（只读）"""
    return case_ops.archive_zone_scan(zone)


def api_archive_stage(parts):
    """上传 → 归档暂存区（**只写暂存区**；案卷一律不动）"""
    batch = datetime.now().strftime('%Y%m%d-%H%M%S')
    d = os.path.join(ARCHIVE_UPLOAD_DIR, batch)
    os.makedirs(d, exist_ok=True)
    items = []
    for p in parts:
        fn = p.get('filename') or ''
        it = {'orig_name': fn, 'ok': False}
        if not fn or not p.get('data'):
            it['error'] = '没有文件名或内容为空'
            items.append(it)
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in UPLOAD_EXTS:
            it['error'] = '不支持的类型：%s' % (ext or '（无扩展名）')
            items.append(it)
            continue
        staged = os.path.join(d, case_ops._free_name(
            case_ops._safe_fn(os.path.basename(fn)), [d]))
        try:
            with open(staged, 'wb') as f:
                f.write(p['data'])
        except OSError as e:                                  # noqa: BLE001
            it['error'] = '写入暂存失败：%r' % (e,)
            items.append(it)
            continue
        it.update({'ok': True, 'staged': staged,
                   'rel': os.path.relpath(staged, ARCHIVE_UPLOAD_DIR).replace(os.sep, '/')})
        items.append(it)
    return {'batch': batch, 'ok_count': sum(1 for x in items if x['ok']),
            'items': items, 'stage_root': ARCHIVE_UPLOAD_DIR}


def api_archive_staged():
    """暂存区清单（待导入的文件）"""
    out = {'root': ARCHIVE_UPLOAD_DIR, 'batches': [], 'total': 0}
    if not os.path.isdir(ARCHIVE_UPLOAD_DIR):
        return out
    for b in sorted(os.listdir(ARCHIVE_UPLOAD_DIR), reverse=True):
        d = os.path.join(ARCHIVE_UPLOAD_DIR, b)
        if not os.path.isdir(d):
            continue
        fs = []
        for r, ds, ff in os.walk(d):
            for f in sorted(ff):
                fp = os.path.join(r, f)
                fs.append({'name': f, 'rel': os.path.relpath(fp, ARCHIVE_UPLOAD_DIR).replace(os.sep, '/'),
                           'size': os.path.getsize(fp)})
        if fs:
            out['batches'].append({'batch': b, 'files': fs})
            out['total'] += len(fs)
    return out


def api_archive_import_preview(zone, rel, group='', sub='', month=''):
    """单件导入预览（**只算不改**）"""
    fp = os.path.join(ARCHIVE_UPLOAD_DIR, rel.replace('/', os.sep))
    plan = case_ops.plan_archive_import(zone, fp, group=group, sub=sub, month=month)
    return {'plan': plan, 'ok': bool(plan.get('ok')),
            'warnings': plan.get('warnings') or []}


def api_archive_import_apply(zone, rel, group='', sub='', month='', confirm=False):
    """执行导入（confirm 才动）"""
    fp = os.path.join(ARCHIVE_UPLOAD_DIR, rel.replace('/', os.sep))
    plan = case_ops.plan_archive_import(zone, fp, group=group, sub=sub, month=month)
    if not plan.get('ok'):
        raise ValueError('；'.join(plan.get('warnings') or ['方案不可执行']))
    if not confirm:
        return {'dry_run': True, 'plan': plan}
    r = case_ops.apply_archive_import(plan, confirm=True)
    return {'dry_run': False, **r}


def api_invoice_overview():
    """发票页数据（只读）：公共库清单 + 案件下拉 + 暂存区上限"""
    return {'library': case_ops.invoice_library(), 'cases': _case_options(),
            'lib_root': case_ops.invoice_lib_root(), 'upload_dir': UPLOAD_DIR,
            'max_mb': UPLOAD_MAX_MB}


def api_invoice_stage(parts):
    """上传的发票 → 暂存区 + 逐张本机识别（**只写暂存区**；案卷/台账一律不动）"""
    batch = datetime.now().strftime('%Y%m%d-%H%M%S')
    d = os.path.join(UPLOAD_DIR, batch)
    os.makedirs(d, exist_ok=True)
    rows, fmap = None, {}
    try:
        rows = case_ops.ledger_case_rows()
        fmap = _folder_by_ledger_row()
    except Exception as e:                                    # noqa: BLE001
        log('INVOICE-LEDGER-ERROR', repr(e)[:120])
    items = []
    for p in parts:
        fn = p.get('filename') or ''
        it = {'orig_name': fn, 'ok': False}
        if not fn or not p.get('data'):
            it['error'] = '没有文件名或内容为空'
            items.append(it)
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in UPLOAD_EXTS:
            it['error'] = '不支持的类型：%s（支持 PDF / 图片 / OFD）' % (ext or '（无扩展名）')
            items.append(it)
            continue
        staged = os.path.join(d, case_ops._free_name(
            case_ops._safe_fn(os.path.basename(fn)), [d]))
        try:
            with open(staged, 'wb') as f:
                f.write(p['data'])
        except OSError as e:                                  # noqa: BLE001
            it['error'] = '写入暂存失败：%r' % (e,)
            items.append(it)
            continue
        it['staged'] = staged
        it['kb'] = round(len(p['data']) / 1024.0, 1)
        r = ocr_ops.run_file(staged)
        if not r['ok']:
            it['error'] = r['error']
            items.append(it)
            continue
        fld = ocr_ops.extract_invoice(r['text'])
        it.update({'ok': True, 'chars': r['chars'], 'seconds': r['seconds'],
                   'cached': r['cached'], 'fields': fld,
                   'suggest_name': case_ops.suggest_invoice_name(fld, fn),
                   'month': fld.get('月') or datetime.now().strftime('%Y-%m')})
        # 弱建议案件：**只有文件名带案名线索时才给**（发票正文一般没有当事人 → 不猜）
        try:
            hit = (case_ops.match_invoice(fn, rows or []).get('cases') or [None])[0]
            if hit:
                it['suggest_why'] = hit.get('为什么')
                it['suggest_case_row'] = {'sheet': hit.get('sheet'), 'row': hit.get('row'),
                                          '案件': hit.get('案件')}
                fp = fmap.get((hit.get('sheet'), int(hit.get('row') or 0)))
                if fp:
                    it['suggest_case'] = os.path.basename(fp)
        except Exception:                                    # noqa: BLE001
            pass
        items.append(it)
    return {'batch': batch, 'dir': d, 'items': items,
            'ok_count': sum(1 for i in items if i['ok'])}


def api_guides_plan(name=None):
    """案件夹《材料怎么放》补件预览（只读，不写盘）"""
    p = case_ops.plan_guides(names=[name] if name else None)
    items = [{k: it[k] for k in ('folder', 'exists', 'exec', 'series', 'dirs', 'extra', 'ledger_case')}
             for it in p['items']]
    return {'root': p['root'], 'count': len(items), 'have': len(items) - len(p['todo']),
            'todo': p['todo'], 'items': items}


def api_archive_plan(case_name, year=None):
    p = os.path.join(case_ops.case_root(), case_name)
    if not os.path.isdir(p):
        return {'error': '案件夹不存在：%s' % case_name}
    out = case_ops.plan_archive(p, year)
    # 批 26：附「台账结案移行」预判（只读）—— 认得出台账行就显示将移到哪
    try:
        row = case_ops.match_case_ledger(case_name, case_ops.ledger_case_rows()).get('matched')
        if row:
            out['ledger'] = ledger_ops.plan_move_to_closed(row['sheet'], row['row'])
        else:
            out['ledger'] = None
            out['ledger_note'] = '没认出台账行 → 台账需手工移行（「台账进展」页可逐行改状态）'
    except Exception as e:                                    # noqa: BLE001
        out['ledger'] = None
        out['ledger_note'] = '台账移行预判失败：%s' % e
    # 批 28：附「知识库案件页」预判（只读）—— 认得出才显示将标哪一页
    out['wiki'] = wiki_close_preview(case_name)
    return out


# ---------------------------------------------------------------- 知识库结案标记（批 28）

def wiki_close_preview(case_name, page=None):
    """认「案件夹 → 知识库案件页」（只读）。page 给了就直接用那一页。

    双跳口径：案卷夹 →（case_ops 成熟匹配）→ 台账案件名 →（wiki_ops 主体词口径）→ 知识库页；
    台账认不到就用夹名兜底，两个线索一起给建议；**只给建议，认不出就明说**。
    """
    pages = wiki_ops.list_case_pages()
    out = {'items': pages, 'count': len(pages),
           'closed': sum(1 for p in pages if p['closed']),
           'suggest': None, 'candidates': [], 'note': '',
           'vault': wiki_ops.vault_status()}
    if not pages:
        out['note'] = '知识库里没有案件页'
        return out
    if page:
        hit = [p for p in pages if p['title'] == page]
        out['suggest'] = hit[0]['title'] if hit else None
        out['note'] = '' if hit else '知识库里没有这一页'
        return out
    if not (case_name or '').strip():
        out['note'] = '没给案件夹名（可在清单里手工选一页）'
        return out
    keys = [case_name]
    try:
        mt = case_ops.match_case_ledger(case_name, case_ops.ledger_case_rows()).get('matched')
        if mt:
            keys.insert(0, mt['案件'])                    # 台账案件名优先（口径更全）
            out['ledger'] = mt['案件']
    except Exception as e:                                    # noqa: BLE001
        out['note'] = '（台账匹配失败，只用夹名认：%r）' % (e,)
    try:
        sg = wiki_ops.suggest_case_page(keys, pages)
    except Exception as e:                                    # noqa: BLE001
        out['note'] = '匹配失败：%r' % (e,)
        return out
    out['candidates'] = sg.get('candidates') or []
    if sg.get('suggest'):
        out['suggest'] = sg['suggest']
        out['why'] = sg.get('why') or []
        out['score'] = sg.get('score')
    else:
        out['note'] = (sg.get('why') or ['没认出对应的知识库案件页'])[0] + \
            ' → 不做模糊猜，可在清单里手工选'
    return out


def api_wiki_case_pages(q=None):
    q = q or {}
    return wiki_close_preview(q.get('case') or '', q.get('page') or None)


def api_wiki_close_plan(page, folder=None, date=None, note=None):
    pl = wiki_ops.plan_mark_closed(page, date, folder or '', note or '')
    return {'found': pl['found'], 'title': pl['title'], 'path': pl['path'],
            'already': pl['already'], 'will_write': pl['will_write'],
            'changes': pl['changes'], 'warnings': pl['warnings'],
            'before': pl['before'], 'after': pl['after']}


def api_export_preview():
    rows, manual, entities, existing = export_batch_data.build()
    return {'cases': len(rows), 'entities': len(entities), 'existing_rows': len(existing),
            'matched_entity': sum(1 for r in rows if r['entity_key']),
            'rows': [{k: v for k, v in r.items() if not k.startswith('_')} for r in rows],
            'manual_cols': export_batch_data.MANUAL_COLS,
            'out_dir': export_batch_data.GEN_DIR,
            'target': export_batch_data.DATA_XLSX}


# ---------------------------------------------------------------- 台账进展（批 13）

def api_ledger_rows(q=None):
    """台账案件行清单（只读）——供「台账进展」页选行、改进度"""
    kw = str((q or {}).get('q') or '').strip()
    rows = []
    for sheet in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sheet):
            item = {'sheet': sheet, 'row': r['_row'],
                    '序号': str(r.get('序号') or ''), '案件': str(r.get('案件') or ''),
                    '委托人': str(r.get('委托人') or ''), '案号': str(r.get('案号') or ''),
                    '状态': str(r.get('最新状态') or ''),
                    '状态合规': ledger_ops.status_is_rule_ok(r.get('最新状态')),
                    '当前进度': str(r.get('当前进度') or ''),
                    '进度日志': str(r.get('进度日志') or '')}
            if kw and not any(kw in item[k] for k in ('案件', '委托人', '当前进度', '案号', '序号')):
                continue
            rows.append(item)
    # 批 14b 体检：K 列「最新状态」写了台账规定以外取值的行（只报，不改）
    off_pool = [{'sheet': x['sheet'], 'row': x['row'], '案件': x['案件'], '状态': x['状态']}
                for x in rows if x['状态'] and not x['状态合规']]
    return {'count': len(rows), 'rows': rows,
            'sheets': list(ledger_ops.SHEETS_CASE),
            'probe': ledger_ops.probe_write_env(),
            'pending': ledger_ops.queue_load().get('items', []),
            'pending_path': ledger_ops.pending_path(),
            # status_pool = **可写入**的取值池（台账维护说明页规定）；observed = 实本见过但规定外的
            'status_pool': ledger_ops.status_write_pool(),
            'status_observed': [v for v in ledger_ops.STATUS_POOL_BASE
                                if v not in ledger_ops.status_write_pool()],
            'status_off_pool': off_pool,
            'status_pool_source': '台账「维护说明」页 K 列说明'}


def api_pending():
    d = ledger_ops.queue_load()
    items = d.get('items', [])
    return {'count': len(items), 'items': items, 'path': ledger_ops.pending_path(),
            'states': ['待同步', '冲突']}


# ---------------------------------------------------------------- 台账助理（批 A/B）
# 智能提醒分析 + 对话。数据源 = 台账单文件（assistant_ops 内部锁死）；LLM = 本地池 8788。
# 纪律：分析不写台账/案卷（只写 data\ai_reminders.json）；对话不落任何案件材料。

_CHAT_LOCK = threading.Lock()      # 对话并发锁：同一时刻只跑一个（对齐桌宠 main.js chatBusy）
_ASSIST_RUNNING = {'thread': None, 'started': 0}


def api_assistant_reminders():
    """智能提醒列表 + 分析任务状态（首页提醒卡与导航铃铛共用）"""
    items = assistant_ops.load_reminders()
    st = assistant_ops.load_state()
    unread = len([x for x in items
                  if isinstance(x, dict) and not x.get('read') and not x.get('dismissed')])
    return {'count': len(items), 'unread': unread, 'items': items,
            'state': {'busy': assistant_ops.state_busy(st), 'last_run': st.get('last_run'),
                      'last_status': st.get('last_status'), 'last_error': st.get('last_error'),
                      'last_count': st.get('last_count')},
            'ignore': assistant_ops.load_ignore(),
            'llm': dict(zip(('ok', 'msg'), assistant_ops.llm_reachable()))}


def api_assistant_remind_run(apply=True):
    """起后台线程跑一轮分析。已在跑 → 拒绝（跨进程锁）。"""
    if assistant_ops.state_busy():
        return {'busy': True, 'msg': '上一轮分析还在跑，稍等喵'}
    st = assistant_ops.load_state()
    assistant_ops.set_state({'busy': True, 'pid': os.getpid(),
                             'last_run': datetime.now().isoformat(timespec='seconds'),
                             'last_status': 'running', 'last_error': None}, apply=apply)

    def _work():
        try:
            r = assistant_ops.analyze(apply=apply)
            if r.get('ok'):
                assistant_ops.set_state({'busy': False, 'pid': None, 'last_status': 'done',
                                         'last_error': None, 'last_count': r['count']},
                                        apply=apply)
                log('ASSISTANT_RUN_OK', '%d 案 → %d 条（变化 %d，%.1fs）'
                    % (r.get('cases', 0), r['count'], r.get('changed', 0),
                       r.get('elapsed', 0)))
                # 规划 §8.5：分析完成若有「high 且本次新增」→ 系统 toast 一条
                # （页面关着也收得到；失败静默不回退气泡——分析是用户主动点的，不需打扰式回执）
                try:
                    highs = [x for x in (r.get('items') or [])
                             if isinstance(x, dict) and x.get('priority') == 'high'
                             and x.get('change') == 'new']
                    if highs:
                        import toast_notify
                        toast_notify.notify(
                            '法律工作台 · 智能提醒',
                            '智能提醒 %d 条，其中紧急 %d 条——打开工作台首页查看'
                            % (r.get('count', 0), len(highs)))
                except Exception as _e:                       # noqa: BLE001
                    log('ASSISTANT_TOAST_FAIL', repr(_e)[:160])
            else:
                assistant_ops.set_state({'busy': False, 'pid': None, 'last_status': 'error',
                                         'last_error': (r.get('error') or '')[:200]},
                                        apply=apply)
                log('ASSISTANT_RUN_FAIL', str(r.get('error'))[:200])
        except Exception as e:                                # noqa: BLE001
            assistant_ops.set_state({'busy': False, 'pid': None, 'last_status': 'error',
                                     'last_error': '%s: %s' % (type(e).__name__, e)}, apply=apply)
            log('ASSISTANT_RUN_FAIL', 'thread %r' % (e,))

    th = threading.Thread(target=_work, daemon=True)
    _ASSIST_RUNNING['thread'] = th
    _ASSIST_RUNNING['started'] = time.time()
    th.start()
    log('ASSISTANT_RUN_START', 'pid=%d' % os.getpid())
    return {'started': True, 'busy': True}


def api_assistant_chat(q, apply=True):
    """同步对话（≤180s）。并发锁：占用中直接回 409 提示语。"""
    q = str(q or '').strip()
    if not q:
        return {'error': '问题为空'}
    if len(q) > 2000:
        return {'error': '问题过长（>2000 字符）。台账查询不需要长文本；'
                         '如需分析案件材料，请到煤球主会话（本地）。'}
    if not _CHAT_LOCK.acquire(blocking=False):
        return {'busy': True, 'ok': False, 'reply': '上一条还在思考，稍等一下喵'}
    try:
        r = assistant_ops.chat(q, apply=apply)
        log('ASSISTANT_CHAT', 'q_len=%d ok=%s' % (len(q), r.get('ok')))
        return r
    finally:
        _CHAT_LOCK.release()


def api_assistant_mark(body, apply=True):
    rid = str(body.get('id') or '').strip()
    if not rid:
        return {'error': '缺 id'}
    items, hit = assistant_ops.mark_read(rid, dismissed=bool(body.get('dismissed')), apply=apply)
    return {'ok': True, 'hit': hit, 'items': items,
            'unread': len([x for x in items if not x.get('read') and not x.get('dismissed')])}


def api_assistant_ignore(body, apply=True):
    """忽略名单增删（自建配置，非台账/案卷写）"""
    if body.get('remove'):
        items, changed = assistant_ops.ignore_remove(body.get('remove'), apply=apply)
        return {'ok': True, 'changed': changed, 'items': items}
    word = str(body.get('add') or '').strip()
    if not word:
        return {'error': '缺 add 词'}
    items, changed = assistant_ops.ignore_add(word, apply=apply)
    return {'ok': True, 'changed': changed, 'items': items}


def api_assistant_history():
    return {'items': assistant_ops.load_history(),
            'max_turns': assistant_ops.MAX_HISTORY_TURNS}


# ---------------------------------------------------------------- HTTP

class _QuietHTTPServer(ThreadingHTTPServer):
    # 批 69 E4：断连静音（断连异常由 server 层捕获到此——handler 层同名定义不被调用；方案 5-1 落位校正）
    def handle_error(self, request, client_address):
        # 批 69：浏览器关窗/取消导致的断连属常态（大 PDF 预览中途关页等），不刷 Traceback 淹日志
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CaseWorkbench/%s' % VERSION

    def log_message(self, fmt, *args):      # 静音默认 stderr 日志
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', cache='no-store'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=1).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)   # 批 47i：API/案卷默认 no-store；静态资源由 _static 传长缓存
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _invoice_upload(self):
        """POST multipart：上传发票 → 暂存 + 逐张识别（不确认也照做，因为只写暂存区）"""
        ctype = self.headers.get('Content-Type') or ''
        if 'multipart/form-data' not in ctype:
            return self._send(400, {'error': '这里要 multipart/form-data 上传'})
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m:
            return self._send(400, {'error': '缺少 boundary'})
        boundary = (m.group(1) or m.group(2) or '').strip()
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self._send(400, {'error': '空上传'})
        if n > UPLOAD_MAX_MB * 1048576:
            return self._send(400, {'error': '上传超过 %d MB 上限' % UPLOAD_MAX_MB})
        try:
            raw = self.rfile.read(n)
        except Exception as e:                                # noqa: BLE001
            return self._send(400, {'error': '读取上传内容失败：%r' % (e,)})
        parts = [p for p in _parse_multipart(raw, boundary) if p.get('filename')]
        if not parts:
            return self._send(400, {'error': '没解析到文件（表单字段名用 files）'})
        res = api_invoice_stage(parts)
        log('INVOICE-UPLOAD', 'batch=%s files=%d ok=%d'
            % (res['batch'], len(res['items']), res['ok_count']))
        return self._send(200, res)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return {}

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path not in ('/api/health', '/api/quit'):
            _touch()
        try:
            if u.path in ('/', '/index.html'):
                return self._static('index.html')
            if u.path == '/favicon.ico':
                # 2026-09-13：Edge --app 应用窗口/任务栏图标取网站 favicon → 不给会显示默认地球
                return self._static('favicon.ico')
            if u.path.startswith('/static/'):
                return self._static(os.path.basename(u.path))
            if u.path == '/api/ping':
                # 前端心跳：证明还有页面开着 → 保活
                return self._send(200, {'ok': True, 'idle_limit': IDLE['limit']})
            if u.path == '/api/overview':
                return self._send(200, api_overview())
            if u.path == '/api/tasks':
                return self._send(200, api_tasks(q))
            if u.path == '/api/cases':
                return self._send(200, api_cases())
            if u.path == '/api/board':
                # 批 50：看板泳道（台账只读分桶）。include_closed 取字符串——
                # 勿抄 parse_qs 多值写法（批 48 实踩：q 已是单值 dict）
                return self._send(200, api_board(
                    include_closed=str(q.get('include_closed') or '').lower()
                    in ('1', 'true', 'yes')))
            if u.path == '/api/case':
                return self._send(200, api_case_detail(q.get('name', '')))
            if u.path == '/api/fees':
                return self._send(200, api_fees())
            if u.path == '/api/deadlines':
                return self._send(200, api_deadlines())
            if u.path == '/api/intake':
                return self._send(200, api_intake_plan(q.get('case', '')))
            if u.path == '/api/new_case_preview':
                return self._send(200, api_new_case_preview(q))
            if u.path == '/api/archive':
                return self._send(200, api_archive_plan(q.get('case', ''), q.get('year')))
            if u.path == '/api/guides':
                return self._send(200, api_guides_plan(q.get('case') or None))
            if u.path == '/api/export_preview':
                return self._send(200, api_export_preview())
            if u.path == '/api/trace/index':
                return self._send(200, api_trace_index())
            if u.path == '/api/trace/case':
                try:
                    return self._send(200, api_trace_case(q.get('case', '')))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/trace/files':
                try:
                    return self._send(200, api_trace_files(q.get('case', ''),
                                                           q.get('folder', '')))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/trace/refile':
                try:
                    return self._send(200, api_trace_refile(q, confirm=False))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/trace/gather':
                try:
                    return self._send(200, api_trace_gather(q, confirm=False))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/letters':
                return self._send(200, api_letters())
            if u.path == '/api/contracts':
                # 批 52：合同审查（台账只读 + 素材夹扫描；sheet/夹缺失都优雅降级）
                return self._send(200, api_contracts())
            if u.path == '/api/letter/add_row':
                # 批 41：新增发函对象（GET=dry-run 预览；POST confirm 执行）
                vals = {k: (q.get(k, [''])[0] or '') for k in ledger_ops.LETTER_ADD_COLS}
                return self._send(200, api_letter_add_row(vals, confirm=False))
            if u.path == '/api/letter/folder_files':
                # 批 48：单夹文件清单实时扫描（看文件/函件入口共用）——
                # 看文件改点按现扫，新放文件立即可见（勿改回 LETTER_ITEMS 缓存）。
                return self._send(200, api_letter_folder_files(q))
            if u.path == '/api/letter/file_view':
                # 批 47：文件直出（GET 二进制流）——前端 <img>/<iframe src> 直接
                # 指向本 URL，浏览器原生渲染（PDF 白屏的正解，勿改回 dataURL）。
                r = api_letter_file_view(q)
                if r.get('error'):
                    return self._send(400, r)
                return self._send(200, r['raw'], r['mime'])
            if u.path == '/api/travel':
                return self._send(200, api_travel())
            if u.path == '/api/scatter':
                # 批 36：散件索引（律师函/合同/差旅发票/所函/介绍信/判决书/接收到的文件）
                return self._send(200, api_scatter())
            if u.path == '/api/scatter/cases':
                # 批 39c：候选案件（供「人工指定」下拉）
                return self._send(200, api_scatter_case_options())
            if u.path == '/api/schedule':
                # 批 37：手工日程（独立于台账派生期限）
                return self._send(200, api_schedule())
            if u.path == '/api/sohan/meta':
                return self._send(200, api_sohan_meta())
            if u.path == '/api/sohan/plan':
                return self._send(200, api_sohan_plan(q))
            if u.path == '/api/commission/meta':
                # 批 64：委托手续套件元数据（模板状态/档位/默认值/案件夹）
                return self._send(200, api_commission_meta())
            if u.path == '/api/scatter/marks':
                return self._send(200, api_scatter_marks())
            if u.path == '/api/scatter/intake':
                try:
                    return self._send(200, api_scatter_intake(
                        q.get('rel', ''), bool(q.get('batch')), confirm=False,
                        case_name=q.get('case_name', '')))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/ocr/plan':
                return self._send(200, api_ocr_plan(q.get('case', ''), q.get('name', '')))
            if u.path == '/api/ocr/text':
                # 批 66：读识别全文（对照原图修字用；只读）
                return self._send(200, api_ocr_text(q.get('case', ''), q.get('name', '')))
            if u.path == '/api/deepdive/view':
                # 批 49/60：研究报告页内文本预览（安全闸在函数内，越界最先）
                return self._send(200, api_deepdive_view(q))
            if u.path == '/api/payments':
                # 批 62：执行款清单 + 汇总（自建库只读）
                return self._send(200, api_payments(q.get('folder', '')))
            if u.path == '/api/writing/projects':
                # 批 51：写作项目列表（只读）
                return self._send(200, api_writing_projects(q))
            if u.path == '/api/writing/versions':
                # 批 51：某项目的版本清单（只读；参数错误在函数内转 error 字段）
                return self._send(200, api_writing_versions(q))
            if u.path == '/api/writing/view':
                # 批 51：版本文本预览（安全闸在函数内，越界最先）
                return self._send(200, api_writing_view(q))
            if u.path == '/api/ledger/close':
                try:
                    return self._send(200, {'plan': ledger_ops.plan_move_to_closed(
                        q.get('sheet', ''), int(q.get('row') or 0))})
                except Exception as e:                        # noqa: BLE001
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/archive/zones':
                return self._send(200, api_archive_zones())
            if u.path == '/api/archive/zone':
                try:
                    return self._send(200, api_archive_zone(q.get('zone', '')))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/archive/staged':
                return self._send(200, api_archive_staged())
            if u.path == '/api/invoice/overview':
                return self._send(200, api_invoice_overview())
            if u.path == '/api/wiki/case_pages':
                return self._send(200, api_wiki_case_pages(q))
            if u.path == '/api/wiki/close':
                if not (q.get('page') or '').strip():
                    return self._send(400, {'error': '缺 page（知识库页标题）'})
                return self._send(200, api_wiki_close_plan(
                    q.get('page', ''), q.get('folder'), q.get('date'), q.get('note')))
            if u.path == '/api/ledger_rows':
                return self._send(200, api_ledger_rows(q))
            if u.path == '/api/ledger/probe':
                return self._send(200, ledger_ops.probe_write_env())
            if u.path == '/api/ledger/pending':
                return self._send(200, api_pending())
            if u.path == '/api/ledger/row':
                # 单行快照（外部改动检测基准）
                try:
                    return self._send(200, ledger_ops.row_snapshot(q.get('sheet', ''),
                                                                   int(q.get('row') or 0)))
                except (ValueError, TypeError) as e:
                    return self._send(400, {'error': str(e)})
            # ---- 台账助理（批 B）
            if u.path == '/api/assistant/reminders':
                return self._send(200, api_assistant_reminders())
            if u.path == '/api/assistant/history':
                return self._send(200, api_assistant_history())
            if u.path == '/api/assistant/ignore':
                return self._send(200, {'items': assistant_ops.load_ignore()})
            if u.path == '/api/health':
                return self._send(200, {'ok': True, 'version': VERSION})
            if u.path == '/api/quit':
                # 供「关闭法律工作台」图标调用；也支持在浏览器直接访问本地址关停
                self._send(200, {'ok': True, 'msg': '工作台正在关闭，页面可以关掉了'})
                _quit_soon()
                return
            return self._send(404, {'error': 'not found'})
        except Exception as e:
            log('GET-ERROR', '%s %r' % (self.path, e))
            return self._send(500, {'error': repr(e), 'trace': traceback.format_exc()[-600:]})

    # ---- POST（写动作一律要求 confirm）
    def _ledger_progress(self, body, confirm):
        """台账进度受控回写：dry-run 预览 / 外部改动冲突 / 占用排队 三种结果都结构化返回"""
        sheet = body.get('sheet', '')
        if sheet not in ledger_ops.SHEETS_CASE:
            return self._send(400, {'error': 'sheet 只能是 %s'
                                             % ' / '.join(ledger_ops.SHEETS_CASE)})
        try:
            row = int(body.get('row') or 0)
        except (TypeError, ValueError):
            return self._send(400, {'error': 'row 必须是数字'})
        text = body.get('text', '')
        date_label = body.get('date') or None
        # 批 14b：可同时改 K 列「最新状态」（只接受台账「维护说明」页规定的取值）
        status = (body.get('status') or '').strip() or None
        if not confirm:
            plan = ledger_ops.plan_progress(sheet, row, text, date_label, status)
            return self._send(200, {'dry_run': True, 'plan': plan,
                                    'expect': ledger_ops.row_snapshot(sheet, row),
                                    'status_pool': ledger_ops.status_write_pool(),
                                    'probe': ledger_ops.probe_write_env()})
        r = ledger_ops.apply_progress_guarded(sheet, row, text, expect=body.get('expect'),
                                             date_label=date_label, source='web',
                                             new_status=status)
        if r.get('audit'):
            log('LEDGER_AUDIT_WARN', '%s' % r['audit'][:5])
        log('LEDGER_PROGRESS', '%s row=%d status=%s backup=%s'
            % (sheet, row, status or '-', r['backup']))
        return self._send(200, {'ok': True, 'dry_run': False, 'plan': r['plan'],
                                'backup': r['backup'],
                                'soft_changes': r['soft_changes'],
                                'audit': r.get('audit') or [],
                                'row_after': r['row_after'],
                                'pending': api_pending()['count']})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/api/archive/upload':                  # 批 39h：multipart
            try:
                ctype = self.headers.get('Content-Type') or ''
                if 'multipart/form-data' not in ctype:
                    return self._send(400, {'error': '这里要 multipart/form-data 上传'})
                m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
                if not m:
                    return self._send(400, {'error': '缺少 boundary'})
                boundary = (m.group(1) or m.group(2) or '').strip()
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 0:
                    return self._send(400, {'error': '空上传'})
                if n > UPLOAD_MAX_MB * 1048576:
                    return self._send(400, {'error': '上传超过 %d MB 上限' % UPLOAD_MAX_MB})
                raw = self.rfile.read(n)
                parts = [x for x in _parse_multipart(raw, boundary) if x.get('filename')]
                if not parts:
                    return self._send(400, {'error': '没解析到文件（表单字段名用 files）'})
                res = api_archive_stage(parts)
                log('ARCHIVE-UPLOAD', 'batch=%s files=%d ok=%d'
                    % (res['batch'], len(res['items']), res['ok_count']))
                return self._send(200, res)
            except Exception as e:                            # noqa: BLE001
                return self._send(400, {'error': '上传处理失败：%r' % (e,)})
        if u.path == '/api/invoice/upload':                  # 批 30：multipart 要先于 _body 读
            _touch()
            try:
                return self._invoice_upload()
            except Exception as e:                            # noqa: BLE001
                log('INVOICE-UPLOAD-ERROR', repr(e)[:200])
                return self._send(400, {'error': '上传处理失败：%r' % (e,)})
        body = self._body()
        confirm = bool(body.get('confirm'))
        if u.path == '/api/archive/import':                  # 批 39h：预览 / 执行
            # ⚠️ 必须在 `body = self._body()` **之后**——放前面会 UnboundLocalError
            try:
                if confirm:
                    res = api_archive_import_apply(
                        str(body.get('zone') or ''), str(body.get('rel') or ''),
                        group=str(body.get('group') or ''), sub=str(body.get('sub') or ''),
                        month=str(body.get('month') or ''), confirm=True)
                    log('ARCHIVE-IMPORT', 'zone=%s dst=%s'
                        % (body.get('zone'), res.get('dst')))
                    return self._send(200, res)
                return self._send(200, api_archive_import_preview(
                    str(body.get('zone') or ''), str(body.get('rel') or ''),
                    group=str(body.get('group') or ''), sub=str(body.get('sub') or ''),
                    month=str(body.get('month') or '')))
            except ValueError as e:
                return self._send(400, {'error': str(e)})
        if u.path != '/api/quit':
            _touch()
        try:
            if u.path == '/api/tasks':
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'will_add': {k: body.get(k) for k in
                                                         ('title', 'case_ref', 'status',
                                                          'priority', 'due_date', 'note')}})
                t = tasks_ops.add_task(body.get('title', ''), case_ref=body.get('case_ref', ''),
                                       status=body.get('status') or '待办',
                                       priority=body.get('priority') or '普通',
                                       due_date=body.get('due_date') or '',
                                       note=body.get('note') or '')
                log('TASK-ADD', '#%s %s' % (t['id'], t['title']))
                return self._send(200, {'ok': True, 'task': t, 'stats': tasks_ops.stats()})
            if u.path == '/api/tasks/update':
                patch = body.get('patch') or {}
                if not confirm:
                    return self._send(200, {'dry_run': True, 'id': body.get('id'),
                                            'patch': patch})
                t = tasks_ops.update_task(body.get('id'), patch)
                log('TASK-UPDATE', '#%s %s' % (t['id'], patch))
                return self._send(200, {'ok': True, 'task': t, 'stats': tasks_ops.stats()})
            if u.path == '/api/tasks/delete':
                if not confirm:
                    return self._send(200, {'dry_run': True, 'id': body.get('id'),
                                            'task': tasks_ops.get_task(body.get('id'))})
                r = tasks_ops.delete_task(body.get('id'))
                log('TASK-DELETE', '#%s %s' % (r['deleted']['id'], r['deleted']['title']))
                return self._send(200, {'ok': True, 'stats': tasks_ops.stats()})
            if u.path == '/api/intake':
                case = body.get('case', '')
                p = os.path.join(case_ops.case_root(), case)
                allow_sub = '/' in case or '\\' in case            # 批 71：系列案子案件夹（两段）
                plan = case_ops.plan_intake(p, allow_sub=allow_sub)
                preview = [{k: v for k, v in it.items() if k in ('name', 'dir', 'newname',
                                                                 'status', 'reason')}
                           for it in plan['items']]
                if not confirm:
                    return self._send(200, {'dry_run': True, 'items': preview})
                plan2, moved, skipped = case_ops.apply_intake(p, allow_sub=allow_sub)
                log('INTAKE', '%s moved=%d skipped=%d' % (case, len(moved), len(skipped)))
                return self._send(200, {'dry_run': False, 'moved': len(moved),
                                        'skipped': [s['name'] + '（' + s['status'] + '）'
                                                    for s in skipped]})
            if u.path == '/api/new_case':
                q = body.get('form', {})
                kw = {'court': q.get('court', ''), 'case_no_court': q.get('case_no_court', ''),
                      'lawyer': q.get('lawyer', '')}
                args = (q.get('case_no', ''), q.get('client', ''), q.get('opponent', ''),
                        q.get('cause', ''), q.get('type', '诉讼'))
                plan = case_ops.plan_new_case(*args, **kw)
                # 可选：知识库案件页骨架（批 16；只新建不覆盖）
                want_entity = bool(body.get('write_entity', True))
                ent = None
                if want_entity:
                    ent = wiki_ops.plan_case_entity(
                        args[0], args[1], args[2], args[3], args[4],
                        plan['folder'], case_ops.case_root(),
                        {'court': kw['court'], 'case_no_court': kw['case_no_court'],
                         'lawyer': kw['lawyer'], 'ledger_hint': ''})
                if not confirm:
                    return self._send(200, {'dry_run': True, 'plan': plan, 'entity': ent,
                                            'vault': wiki_ops.vault_status()})
                plan2 = case_ops.create_new_case(*args, with_pipeline=bool(body.get('with_pipeline', True)),
                                                 **kw)
                out = {'dry_run': False, 'path': plan2['path'],
                       'guide': plan2.get('guide_written') or plan2['guide_path'],
                       'pipeline': (plan2.get('pipeline_out') or '')[-300:]}
                if ent:
                    try:
                        out['entity'] = wiki_ops.write_case_entity(ent)
                    except FileExistsError as e:
                        out['entity_skipped'] = str(e)
                        log('NEW_CASE_ENTITY_SKIP', str(e)[:160])
                    except Exception as e:                    # noqa: BLE001
                        out['entity_error'] = repr(e)[:200]
                        log('NEW_CASE_ENTITY_ERR', repr(e)[:160])
                sheet = body.get('sheet')
                if body.get('append_ledger') and sheet in ledger_ops.SHEETS_CASE:
                    lp, dst = ledger_ops.append_draft_row(sheet, plan2['ledger_draft'])
                    out['ledger'] = {'sheet': sheet, 'row': lp['row'],
                                     'values': lp['values'], 'backup': dst}
                    log('NEW_CASE_LEDGER', '%s row=%d' % (sheet, lp['row']))
                log('NEW_CASE', plan2['path'])
                return self._send(200, out)
            if u.path == '/api/trace/refile':
                try:
                    return self._send(200, api_trace_refile(body, confirm=confirm))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/trace/gather':
                try:
                    return self._send(200, api_trace_gather(body, confirm=confirm))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/letter/update':
                try:
                    row = int(body.get('row') or 0)
                    if not confirm:
                        return self._send(200, {'dry_run': True, 'plan':
                            ledger_ops.plan_letter_update(
                                row, body.get('sign'), body.get('follow'))})
                    r = ledger_ops.apply_letter_update(
                        row, body.get('sign'), body.get('follow'), expect=body.get('expect'))
                    if r.get('busy'):
                        return self._send(409, {'error': '台账被占用（WPS / 云盘同步中），未写入',
                                                'busy': True, 'locks': r.get('locks'),
                                                'plan': r.get('plan')})
                    if r.get('conflict'):
                        return self._send(409, {'error': '该行已被外部改动，未覆盖',
                                                'conflict': True, 'field': r.get('field'),
                                                'now': r.get('now'), 'expect': r.get('expect'),
                                                'plan': r.get('plan')})
                    log('LETTER-UPDATE', 'row=%d sign=%s follow=%s backup=%s'
                        % (row, bool(body.get('sign')), bool(body.get('follow')),
                           r.get('backup')))
                    return self._send(200, {'ok': True, 'backup': r.get('backup'),
                                            'plan': r.get('plan'), 'after': r.get('after'),
                                            'audit': r.get('audit') or []})
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
                except RuntimeError as e:
                    return self._send(500, {'error': str(e)})
            if u.path == '/api/letter/add_row':
                # 批 41：新增发函对象 = 台账律师函 sheet 受控加行 + 材料夹
                try:
                    r = api_letter_add_row(body, confirm=bool(confirm))
                    if r.get('error'):
                        return self._send(409 if r.get('code') in ('busy', 'audit_failed')
                                          else 400, r)
                    return self._send(200, r)
                except (ValueError, RuntimeError) as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/sohan/make':
                r = api_sohan_make(body, confirm)
                if r.get('error'):
                    return self._send(400, r)
                return self._send(200, r)
            if u.path == '/api/commission/plan':
                # 批 64：委托手续生成计划（dry-run）
                return self._send(200, api_commission_plan(body))
            if u.path == '/api/commission/build':
                # 批 64：生成六件套（confirm 才落盘）
                r = api_commission_build(body, confirm)
                if r.get('error'):
                    return self._send(400, r)
                return self._send(200, r)
            if u.path == '/api/scatter/mark':
                try:
                    return self._send(200, api_scatter_mark(body))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/scatter/intake':
                try:
                    return self._send(200, api_scatter_intake(
                        body.get('rel', ''), bool(body.get('batch')), confirm=confirm,
                        case_name=body.get('case_name', '')))
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/schedule/add':
                try:
                    data, it = schedule_ops.add_entry(
                        body.get('title', ''), body.get('date', ''),
                        body.get('case', ''), body.get('time', ''),
                        body.get('kind', '其他'), body.get('lead', 0),
                        body.get('note', ''), apply=confirm)
                except (ValueError, TypeError) as e:
                    return self._send(400, {'error': str(e)})
                if confirm:
                    log('SCHEDULE_ADD', '%s|%s|%s' % (it['标题'], it['日期'], it.get('时间') or ''))
                return self._send(200, {'dry_run': not confirm, 'item': it})
            if u.path == '/api/schedule/update':
                try:
                    fields = {k: body.get(k) for k in
                              ('标题', '日期', '案件', '时间', '类型', '提前提醒', '备注')
                              if body.get(k) not in (None, '')}
                    data, it = schedule_ops.update_entry(
                        body.get('id', ''), apply=confirm, **fields)
                except (ValueError, TypeError) as e:
                    return self._send(400, {'error': str(e)})
                if confirm:
                    log('SCHEDULE_UPDATE', '%s|%s' % (it['标题'], it['日期']))
                return self._send(200, {'dry_run': not confirm, 'item': it})
            if u.path == '/api/schedule/cancel':
                try:
                    data, it = schedule_ops.cancel_entry(body.get('id', ''), apply=confirm)
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
                if confirm:
                    log('SCHEDULE_CANCEL', '%s|%s' % (it['标题'], it['日期']))
                return self._send(200, {'dry_run': not confirm, 'item': it})
            if u.path == '/api/deadlines/extract':
                items, hints, skipped = deadline_ops.extract_from_ledger()
                data, stat = deadline_ops.merge(items, hints)
                if not confirm:
                    return self._send(200, {'dry_run': True, 'stat': stat,
                                            'preview': data['items'][:20]})
                p, _ = deadline_ops.save(data, apply=True)
                log('DEADLINE_EXTRACT', 'added=%d kept=%d skipped=%d' % (
                    stat['added'], stat['updated'], skipped))
                return self._send(200, {'dry_run': False, 'stat': stat, 'path': p})
            if u.path == '/api/deadlines/add':
                try:
                    data, it = deadline_ops.add_manual(body.get('case', ''),
                                                       body.get('type', '其他'),
                                                       body.get('date', ''),
                                                       body.get('note', ''), apply=confirm)
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
                if confirm:
                    log('DEADLINE_ADD', '%s|%s|%s' % (it['案件'], it['期限类型'], it['到期日']))
                return self._send(200, {'dry_run': not confirm, 'item': it})
            if u.path == '/api/deadlines/done':
                try:
                    data, it = deadline_ops.mark_done(body.get('id', ''), apply=confirm)
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
                if confirm:
                    log('DEADLINE_DONE', '%s|%s' % (it['案件'], it['到期日']))
                return self._send(200, {'dry_run': not confirm, 'item': it})
            if u.path == '/api/archive':
                case = body.get('case', '')
                year = body.get('year') or None
                plan = api_archive_plan(case, year)
                if plan.get('error'):
                    return self._send(400, plan)
                if not confirm:
                    return self._send(200, {'dry_run': True, 'plan': plan})
                done = case_ops.apply_archive(os.path.join(case_ops.case_root(), case), year)
                log('ARCHIVE', '%s → %s' % (case, done['dst']))
                led = None                                    # 批 26：台账行移入「已结案」页
                try:
                    row = case_ops.match_case_ledger(case, case_ops.ledger_case_rows()).get('matched')
                    if row:
                        led = ledger_ops.apply_move_to_closed(row['sheet'], row['row'],
                                                              note='随案卷归档')
                        log('LEDGER-CLOSED', '%s %s r%s → %s r%s'
                            % (case, row['sheet'], row['row'], led['dst_sheet'], led['dst_row']))
                    else:
                        led = {'error': '没认出台账行，台账未动（需手工移行）'}
                except Exception as e:                        # noqa: BLE001
                    led = {'error': '台账移行失败（案卷已归档）：%s' % e}
                wk = {'done': False, 'reason': ''}             # 批 28：知识库页标「已结案」
                try:
                    pv = wiki_close_preview(case)
                    pg = pv.get('suggest')
                    if not pg:
                        wk['reason'] = pv.get('note') or '没认出对应的知识库案件页（不做模糊猜）'
                    else:
                        pl = wiki_ops.plan_mark_closed(pg, None, done['dst'], '随案卷归档')
                        if pl['already']:
                            wk = {'done': False, 'page': pg, 'reason': '该页已标过「已结案」'}
                        elif pl['will_write']:
                            r = wiki_ops.apply_mark_closed(pl)
                            wk = {'done': True, 'page': pg, 'backup': r['backup']}
                            log('WIKI-CLOSED', '%s → %s' % (case, pg))
                        else:
                            wk = {'done': False, 'page': pg,
                                  'reason': '；'.join(pl['warnings']) or '没有可改的锚点'}
                except Exception as e:                        # noqa: BLE001
                    wk = {'done': False, 'reason': '知识库标记失败（案卷/台账已处理）：%r' % (e,)}
                return self._send(200, {'dry_run': False, 'moved_to': done['dst'],
                                        'followup': done['followup'], 'ledger_moved': led,
                                        'wiki_marked': wk})
            if u.path == '/api/invoice/file':
                staged = (body.get('staged') or '').strip()
                case = (body.get('case') or '').strip()
                if not staged or not case:
                    return self._send(400, {'error': '缺 staged（暂存文件）或 case（案件夹）'})
                case_dir = os.path.join(case_ops.case_root(), case)
                try:
                    pl = case_ops.plan_invoice_filing(case_dir, staged, body.get('fields') or {},
                                                      body.get('month'), body.get('newname'))
                    if not confirm:
                        return self._send(200, {'dry_run': True, 'plan': pl})
                    if not pl['ok']:
                        return self._send(400, {'error': '；'.join(pl['warnings'])})
                    done = case_ops.apply_invoice_filing(pl)
                    log('INVOICE-FILED', '%s → %s（副本 %s）'
                        % (pl['orig_name'], done['main'], done['copy']))
                    return self._send(200, {'dry_run': False, 'done': done})
                except FileExistsError as e:
                    return self._send(409, {'error': str(e), 'reason': 'conflict'})
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:                        # noqa: BLE001
                    log('INVOICE-FILE-ERROR', repr(e)[:200])
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/wiki/close':
                page = (body.get('page') or '').strip()
                if not page:
                    return self._send(400, {'error': '缺 page（知识库页标题）'})
                folder = body.get('folder') or ''
                date = body.get('date') or None
                note = body.get('note') or ''
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'plan': api_wiki_close_plan(page, folder, date, note)})
                try:
                    pl = wiki_ops.plan_mark_closed(page, date, folder, note)
                    if not pl['found']:
                        return self._send(404, {'error': '知识库里没找到这一页：%s' % page})
                    if pl['already']:
                        return self._send(200, {'dry_run': False, 'changed': False,
                                                'note': '该页已标过「已结案」，未改动',
                                                'path': pl['path']})
                    r = wiki_ops.apply_mark_closed(pl)
                    log('WIKI-CLOSED', '%s（%s）' % (page, 'backup=%s' % r['backup']))
                    return self._send(200, {'dry_run': False, 'changed': r['changed'],
                                            'path': r['path'], 'backup': r['backup'],
                                            'verified': r.get('verified')})
                except FileNotFoundError as e:
                    return self._send(404, {'error': str(e)})
                except RuntimeError as e:                      # 内容已变 / 备份失败 / 校验不过
                    return self._send(409, {'error': str(e), 'reason': 'conflict'})
                except Exception as e:                        # noqa: BLE001
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/ledger/close':
                sh, rw = body.get('sheet', ''), int(body.get('row') or 0)
                note = body.get('note') or ''
                try:
                    if not confirm:
                        return self._send(200, {'dry_run': True,
                                                'plan': ledger_ops.plan_move_to_closed(sh, rw, note)})
                    done = ledger_ops.apply_move_to_closed(sh, rw, note)
                    log('LEDGER-CLOSED', '%s r%s → %s r%s'
                        % (sh, rw, done['dst_sheet'], done['dst_row']))
                    return self._send(200, {'dry_run': False, 'done': done})
                except ledger_ops.LedgerBusy as e:
                    return self._send(409, {'error': str(e), 'reason': 'busy'})
                except ledger_ops.LedgerConflict as e:
                    return self._send(409, {'error': str(e), 'reason': 'conflict'})
                except Exception as e:                        # noqa: BLE001
                    return self._send(400, {'error': str(e)})
            if u.path == '/api/export_batch':
                if not confirm:
                    return self._send(200, {'dry_run': True, 'preview': api_export_preview()})
                rows, manual, _ent, _ex = export_batch_data.build()
                os.makedirs(export_batch_data.GEN_DIR, exist_ok=True)
                stamp = datetime.now().strftime('%Y%m%d-%H%M')
                dst = os.path.join(export_batch_data.GEN_DIR,
                                   '台账派生-案件行-%s.xlsx' % stamp)
                export_batch_data.write_xlsx(rows, manual, dst)
                log('EXPORT_BATCH', '%s rows=%d' % (dst, len(rows)))
                return self._send(200, {'dry_run': False, 'path': dst, 'rows': len(rows),
                                        'manual_cols': export_batch_data.MANUAL_COLS,
                                        'note': '未改动正式数据表；派生行需按 case_key 手工并入「案件」sheet'})
            if u.path in ('/api/progress', '/api/ledger/progress'):
                return self._ledger_progress(body, confirm)
            if u.path == '/api/ocr/run':
                case_name, name = body.get('case') or '', body.get('name') or ''
                if not case_name or not name:
                    return self._send(400, {'error': '需要 case 与 name'})
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'plan': api_ocr_plan(case_name, name),
                                            'note': '本地 OCR（零外发）：确认后才跑，结果存案卷 08-过程文件\\_ocr'})
                p = os.path.join(case_ops.case_root(), case_name)
                r = ocr_ops.run(p, name, force=bool(body.get('force')))
                if not r.get('ok'):
                    return self._send(400, {'error': r.get('error')})
                sg = ocr_ops.suggest(p, name, r['text'])
                log('OCR', '%s / %s → %d 字（%s）'
                    % (case_name, name, r['chars'], 'cache' if r['cached'] else 'run'))
                return self._send(200, {'dry_run': False, 'cached': r['cached'], 'chars': r['chars'],
                                        'seconds': r['seconds'], 'path': r['path'],
                                        'src': ocr_ops.abs_path(p, name),   # 批 66：看图补正预览用
                                        'suggest': sg, 'head': r['text'][:600],
                                        'note': '只新增识别文本；原文件未改名、未移动'})
            if u.path == '/api/ocr/split-run':
                # 批 65：大 PDF 分段识别（每次调用跑一段；前端循环；中断可段级续跑）
                case_name, name = body.get('case') or '', body.get('name') or ''
                if not case_name or not name:
                    return self._send(400, {'error': '需要 case 与 name'})
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'plan': api_ocr_split_plan(case_name, name),
                                            'note': '大 PDF 分段识别（零外发）：每段几分钟；'
                                                    '中途关闭/失败都没关系——重跑自动跳过已完成页'})
                r = api_ocr_split_run(case_name, name, seg=body.get('seg'),
                                      force=bool(body.get('force')))
                return self._send(400 if (not r.get('ok')) else 200, r)
            if u.path == '/api/ocr/save_text':
                # 批 66：保存人工修正的识别文本（覆盖 _ocr 缓存；旧版留 .md.bak）
                case_name, name = body.get('case') or '', body.get('name') or ''
                if not case_name or not name:
                    return self._send(400, {'error': '需要 case 与 name'})
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'note': '保存人工修正的识别文本（覆盖 _ocr 缓存，旧版留 .md.bak）'})
                r = api_ocr_save_text(case_name, name, body.get('text') if 'text' in body else '')
                return self._send(400 if (not r.get('ok')) else 200, r)
            if u.path == '/api/guides':
                name = body.get('case') or None
                if not confirm:
                    return self._send(200, {'dry_run': True, 'plan': api_guides_plan(name)})
                try:
                    r = case_ops.apply_guides(names=[name] if name else None)
                except Exception as e:                        # noqa: BLE001
                    return self._send(400, {'error': '补件失败：%r' % e})
                log('GUIDES', '新增 %d 份 / 跳过 %d 份' % (len(r['written']), len(r['skipped'])))
                return self._send(200, {'dry_run': False, 'written': r['written'],
                                        'skipped': r['skipped'],
                                        'note': '只新增《材料怎么放.md》，未改动、未移动任何既有文件'})
            if u.path == '/api/timeline/export':
                sheet = body.get('sheet', '')
                if sheet not in ledger_ops.SHEETS_CASE:
                    return self._send(400, {'error': 'sheet 只能是 %s'
                                                     % ' / '.join(ledger_ops.SHEETS_CASE)})
                try:
                    row = int(body.get('row') or 0)
                except (TypeError, ValueError):
                    return self._send(400, {'error': 'row 必须是数字'})
                plan = api_timeline_plan(sheet, row, body.get('folder') or None)
                if not confirm:
                    return self._send(200, {'dry_run': True, 'plan': plan})
                if not plan['rows']:
                    return self._send(400, {'error': '这一行没有可导出的时间轴条目'})
                p = timeline_export.write_xlsx(plan)
                log('TIMELINE_EXPORT', '%s r%d → %s（%d 条）' % (sheet, row, p, plan['count']))
                return self._send(200, {'dry_run': False, 'path': p, 'count': plan['count']})
            if u.path == '/api/ledger/pending/retry':
                want = body.get('id') or None
                if not confirm:
                    return self._send(200, {'dry_run': True, 'will_retry': want or 'all',
                                            'pending': api_pending()})
                res = ledger_ops.queue_retry(want)
                log('LEDGER_RETRY', 'ok=%d conflict=%d busy=%d' % (
                    len(res['ok']), len(res['conflict']), len(res['busy'])))
                return self._send(200, {'dry_run': False, 'result': res,
                                        'pending': api_pending()})
            if u.path == '/api/ledger/pending/drop':
                if not confirm:
                    return self._send(200, {'dry_run': True, 'will_drop': body.get('id'),
                                            'item': next((i for i in api_pending()['items']
                                                          if i.get('id') == body.get('id')), None)})
                n = ledger_ops.queue_drop(body.get('id'))
                log('LEDGER_PENDING_DROP', '%s → %d' % (body.get('id'), n))
                return self._send(200, {'dry_run': False, 'removed': n,
                                        'pending': api_pending()})
            if u.path == '/api/deepdive/open':
                # 批 49/60：打开案件夹的研究报告目录（打开文件夹非不可逆，不需 confirm）
                r = api_deepdive_open(body)
                return self._send(200, r)
            if u.path == '/api/binding/build':
                # 批 59：卷宗装订包（dry-run 零副作用；confirm 生成落 卷宗装订\）
                r = api_binding_build(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/binding/open':
                # 批 59：打开卷宗装订产物目录（非不可逆，不需 confirm）
                return self._send(200, api_binding_open(body))
            if u.path == '/api/court_sms/preview':
                # 批 61：法院短信解析 + 文书列表拉取（只读；向法院平台拉取，不上传）
                return self._send(200, api_court_sms_preview(body))
            if u.path == '/api/court_sms/download':
                # 批 61/67：送达文书下载落 00-收件箱或短信文书暂存（dry-run 默认；confirm 才落盘）
                r = api_court_sms_download(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/court_sms/open_stash':
                # 批 67：打开「短信文书暂存」文件夹（非不可逆，不需 confirm）
                return self._send(200, api_court_sms_open_stash())
            if u.path == '/api/payments/add':
                # 批 62：记一笔执行款回款（自建库写；confirm 门）
                r = api_payment_add(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/payments/delete':
                r = api_payment_delete(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/payments/sync':
                # 批 62：同步未同步回款 → 台账「执行款」sheet（受控写入；dry-run 默认）
                r = api_payment_sync(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/board/analyze':
                # 批 55：看板 AI 分析（只读 + 本地 8788，无写动作，不需 confirm）
                return self._send(200, api_board_analyze(body))
            # ---- 批 51：写作项目管理（写动作带 confirm 门；dry-run 零副作用）----
            if u.path == '/api/writing/project':
                r = api_writing_project(body, confirm=confirm)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/writing/status':
                r = api_writing_status(body, confirm=confirm)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/writing/version':
                r = api_writing_version(body, confirm=confirm)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/newcase/prefill':
                # 批 53：材料 → 本机 OCR → 本地 8788 抽取 → 预填草稿（不写台账、不建案）
                # 注意：结果里的 error/down 一律 200 返回（前端按字段分支提示，不炸页面）
                return self._send(200, api_newcase_prefill(body))
            if u.path == '/api/clients_ext':
                key = body.get('client_key', '')
                patch = body.get('patch') or {}
                if not confirm:
                    return self._send(200, {'dry_run': True, 'client_key': key,
                                            'patch': patch,
                                            'before': tasks_ops.get_client_ext(key)})
                e = tasks_ops.set_client_ext(key, patch)
                log('CLIENT_EXT', '%s %s' % (key, list(patch.keys())))
                return self._send(200, {'dry_run': False, 'ext': e,
                                        'clients': _client_items()})
            # ---- 台账助理（批 B；追加在链尾，不插中间——避免 multipart 顺序依赖）
            if u.path == '/api/assistant/remind_run':
                if assistant_ops.state_busy():
                    return self._send(409, {'busy': True, 'msg': '上一轮分析还在跑，稍等喵'})
                return self._send(200, api_assistant_remind_run(apply=True))
            if u.path == '/api/assistant/mark':
                r = api_assistant_mark(body, apply=True)
                if r.get('error'):
                    return self._send(400, r)
                log('ASSISTANT_MARK', '%s dismissed=%s' % (body.get('id'),
                                                           bool(body.get('dismissed'))))
                return self._send(200, r)
            if u.path == '/api/assistant/ignore':
                r = api_assistant_ignore(body, apply=True)
                if r.get('error'):
                    return self._send(400, r)
                return self._send(200, r)
            if u.path == '/api/assistant/chat':
                r = api_assistant_chat(body.get('q'), apply=True)
                if r.get('error'):
                    return self._send(400, r)
                if r.get('busy'):
                    return self._send(409, r)
                return self._send(200, r)
            if u.path == '/api/assistant/history_clear':
                if not confirm:
                    return self._send(200, {'dry_run': True,
                                            'will_clear_turns': len(assistant_ops.load_history()) // 2})
                assistant_ops.clear_history(apply=True)
                log('ASSISTANT_HISTORY_CLEAR', 'via web')
                return self._send(200, {'dry_run': False, 'items': []})
            # ---- 批 46：律师函材料夹 打开/重扫（爸爸要求「一键直达文件夹」）----
            if u.path == '/api/letter/open_folder':
                r = api_letter_open_folder(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/letter/rescan':
                return self._send(200, api_letter_rescan())
            if u.path == '/api/letter/sign_records':
                r = api_letter_sign_records(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/letter/open_file':
                r = api_letter_open_file(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/letter/preview_file':
                r = api_letter_preview_file(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/letter/download':
                r = api_letter_download(body)
                return self._send(400 if r.get('error') else 200, r)
            # ---- 批 46：EMS 签收凭证（上传 → 本机识别 → 提炼；绝不自动写台账）----
            if u.path == '/api/ems/upload':
                r = api_ems_upload(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/ems/recognize':
                r = api_ems_recognize(body)
                return self._send(400 if r.get('error') else 200, r)
            if u.path == '/api/ems/import':
                fn = str(body.get('folder') or '').strip()
                p = str(body.get('path') or '').strip()
                if not fn or not p:
                    return self._send(400, {'error': '需要 folder 与 path'})
                if not confirm:
                    plan = ems_ops.plan_import(p, fn)
                    return self._send(200 if plan.get('ok') else 400,
                                      {'dry_run': True, 'plan': plan} if plan.get('ok') else plan)
                r = ems_ops.apply_import(p, fn)
                return self._send(200 if r.get('ok') else 400, r)
            return self._send(404, {'error': 'not found'})
        except ledger_ops.LedgerConflict as e:
            # 台账被外部改动 → 报差异，交人工判断，绝不静默覆盖
            log('LEDGER_CONFLICT', repr(e)[:160])
            return self._send(409, {'conflict': True, 'error': str(e), 'check': e.check})
        except ledger_ops.LedgerBusy as e:
            # 台账被占用 → 已入待同步队列，页面提示后可重试
            log('LEDGER_BUSY', '；'.join(e.probe.get('reasons') or [])[:160])
            return self._send(409, {'busy': True, 'error': str(e), 'probe': e.probe,
                                    'queued': e.queued})
        except ValueError as e:
            return self._send(400, {'error': str(e)})
        except SystemExit as e:
            return self._send(409, {'error': '操作被纪律拦下（退出码 %s）' % e.code})
        except Exception as e:
            log('POST-ERROR', '%s %r' % (self.path, e))
            return self._send(500, {'error': repr(e), 'trace': traceback.format_exc()[-600:]})

    def _static(self, name):
        p = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(p) or os.path.dirname(os.path.abspath(p)) != STATIC_DIR:
            return self._send(404, {'error': 'static not found'})
        ext = os.path.splitext(p)[1].lower()
        with open(p, 'rb') as f:
            data = f.read()
        # 批 47i：配合壳的持久缓存档——静态资源长缓存（app.js?v=N 版本查参变更即失效）；
        # index.html 不带版本号 → no-cache 保证发版后立刻拿到新引用；API/案卷文件维持 no-store
        cache = 'no-cache' if name == 'index.html' else 'public, max-age=604800'
        return self._send(200, data, MIME.get(ext, 'application/octet-stream'), cache=cache)


_SERVER = {'srv': None}
IDLE = {'last': time.time(), 'limit': 120}     # 空闲自停（秒）；0 = 关闭


def _touch():
    """记录一次「有人/有页面在用」，用于空闲自动关停判定"""
    IDLE['last'] = time.time()


def _watch_idle():
    """空闲守望：超过 idle-exit 秒没有任何页面/请求活动 → 自动关停（关网页≈关程序）"""
    while True:
        time.sleep(5)
        lim = IDLE['limit']
        if lim <= 0:
            continue
        if time.time() - IDLE['last'] > lim:
            log('IDLE-QUIT', '空闲 %d 秒无活动，自动关停工作台' % lim)
            _quit_soon(delay=0.1)
            return


def _quit_soon(delay=0.3):
    """优雅关停：先让响应发出去，再 shutdown 并退出进程（供 /api/quit 使用）"""
    def _do():
        time.sleep(delay)
        try:
            if _SERVER['srv'] is not None:
                _SERVER['srv'].shutdown()
        except Exception:                                    # noqa: BLE001
            pass
        log('QUIT', '收到 /api/quit，工作台已关闭')
        os._exit(0)
    threading.Thread(target=_do, daemon=True).start()


def pick_port(preferred):
    p = preferred
    for _ in range(20):
        if p in BLOCKED_PORTS:
            p += 1
            continue
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', p))
            s.close()
            return p
        except OSError:
            s.close()
            p += 1
    raise SystemExit('无可用端口')


def main():
    ap = argparse.ArgumentParser(description='法律工作台 Web 操作台 v%s' % VERSION)
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--check', action='store_true', help='只做启动自检后退出')
    ap.add_argument('--idle-exit', type=int, default=120,
                    help='超过该秒数无任何页面/请求活动即自动关停（默认 120；0 = 不自动关停）')
    args = ap.parse_args()

    problems = []
    if not os.path.isfile(ledger_ops.ledger_path()):
        problems.append('台账文件不可达：%s' % ledger_ops.ledger_path())
    if not os.path.isdir(case_ops.case_root()):
        problems.append('案卷根不可达：%s' % case_ops.case_root())
    if not os.path.isfile(os.path.join(STATIC_DIR, 'index.html')):
        problems.append('前端缺失：%s' % os.path.join(STATIC_DIR, 'index.html'))
    if problems:
        print('启动自检未通过：')
        for p in problems:
            print('  ❌ %s' % p)
        return 2
    port = pick_port(args.port)
    IDLE['limit'] = max(0, args.idle_exit)
    print('法律工作台自检通过：台账 ✓ 案卷根 ✓ 前端 ✓ 端口 %d' % port)
    if args.check:
        return 0
    srv = _QuietHTTPServer(('127.0.0.1', port), Handler)
    _SERVER['srv'] = srv
    log('START', 'port=%d' % port)
    _touch()
    if IDLE['limit'] > 0:
        threading.Thread(target=_watch_idle, daemon=True).start()
    print('工作台已启动 → http://127.0.0.1:%d   （仅本机可见；%s）'
          % (port, ('空闲 %d 秒自动关停' % IDLE['limit']) if IDLE['limit'] > 0
             else 'Ctrl+C 退出'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        srv.server_close()
        log('STOP', 'port=%d' % port)
    return 0


if __name__ == '__main__':
    sys.exit(main())
