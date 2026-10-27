#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
assistant_ops.py — 法律工作台 · 台账助理引擎（智能提醒 + 对话）

来源：从桌宠 case_remind.py / case_chat.py 迁移（2026-09-18 爸爸拍板退役桌宠侧，
      功能并入工作台）。规划见 docs\桌宠案件功能迁移工作台-技术规划-20260918.md。

职责：
  1) 智能提醒分析：全量台账 → 本地积分池（wb-proxy 8788）LLM → data\ai_reminders.json
  2) 台账助理对话：问题 + 全量台账 + 最近 6 轮历史 → LLM → 回复（历史落 data\assistant_history.json）
  3) 忽略名单：data\ai_remind_ignore.json（首次自动从 ~/.petdex/runtime/case-ignore.json 迁移）
  4) 跨进程锁：data\assistant_state.json（网页按钮 与 潜在的计划任务 两个入口互斥）

铁律（原样继承，勿改）：
  - 数据源锁死台账单文件（ledger_ops.ledger_path()），禁止任何 glob/附件/raw/文书目录扩展
  - 台账只读：copy 到 %TEMP%（PID 命名）再读，try/finally 清理，原文件零接触
  - LLM 输出仅作建议，宁漏报不错报；案号/日期不得编造
  - 案件本体（证据/文书草稿）绝不外发；台账数据经本地池外发 = 爸爸 2026-09-02 已解禁口径
  - 密钥运行时读 wb-proxy\api-key.txt，绝不硬编码/不落日志

用法：
  python assistant_ops.py --analyze              # 跑一次智能提醒分析（真调 LLM）
  python assistant_ops.py --chat --q "问题"       # 问一句（真调 LLM）
  python assistant_ops.py --context-demo         # 只打印台账上下文（不调 LLM）
  python assistant_ops.py --selftest             # 自测（不调 LLM）
"""
import argparse
import config as _config
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ledger_ops  # noqa: E402

VERSION = '1.0.0'
DATA_DIR = os.path.join(HERE, 'data')
LOG_PATH = os.path.join(HERE, 'workbench.log')

# ── 数据文件（支持环境变量覆盖 → 测试隔离用；与 ledger_ops 同款机制）────────
#    ASSISTANT_DATA_DIR 一次改掉全部四个数据文件落点（测试专用；正式运行不设）。
DATA_DIR = os.environ.get('ASSISTANT_DATA_DIR') or DATA_DIR


def _d(name):
    return os.path.join(DATA_DIR, name)


REMINDERS_PATH = _d('ai_reminders.json')
HISTORY_PATH = _d('assistant_history.json')
IGNORE_PATH = _d('ai_remind_ignore.json')
STATE_PATH = _d('assistant_state.json')

# 迁移源（桌宠运行时；存在则首次拷入，原文件不删）。
# ASSISTANT_NO_MIGRATE=1 可关闭自动迁移（测试隔离用，防把真历史拖进临时目录）。
PETDEX_HISTORY = os.path.expanduser('~/.petdex/runtime/case-chat-history.json')
PETDEX_IGNORE = os.path.expanduser('~/.petdex/runtime/case-ignore.json')
NO_MIGRATE = os.environ.get('ASSISTANT_NO_MIGRATE') == '1'

# ── LLM 通道（可选；开源版从配置读取，未配置即关闭该功能）──────────────────
# 任何 OpenAI 兼容端点都可以：本地 vLLM / Ollama / 自建代理 / 云端 API。
LOCAL_PROXY_BASE = _config.get('llm.base_url', '') or ''
API_KEY_FILE = _config.get('llm.api_key_file', '') or ''
MODEL = _config.get('llm.model', '') or ''
LLM_MAX_REMINDERS = 6
# 对话历史窗口（2026-09-19 爸爸拍板 6 → 12 轮）：原 6 轮问长一点就滑出窗口「忘了前面」。
# 12 轮 ≈ 24 条消息；按中文约 30-60 字/条估算 ≈ 1-2K token，对上下文预算影响可接受。
MAX_HISTORY_TURNS = 12
ANALYZE_TIMEOUT = 120
CHAT_TIMEOUT = 180


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁重定向：初始 URL 锁死不等于最终请求主机正确，301/302/307/308 第二跳
    会携带台账 + token。Sol round7 高危项，原样继承。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ⚠️ 工作台进程环境与桌宠 spawn 环境不同：本机存在 HTTP_PROXY 时，urlopen 连
#    127.0.0.1 也会走代理 → 502。故 **禁重定向 + 绕代理 双保险**（规划 §四.4）。
#    注：空 proxies 的 ProxyHandler 会被 build_opener 优化为 UnknownHandler（即
#    "不做代理处理"），链上不再出现 ProxyHandler 类——这正是绕过生效的标志；
#    若系统代理真被启用，链上会有 ProxyHandler。`no_proxy_effective()` 用于断言。
OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def no_proxy_effective():
    """绕代理是否生效：链上无启用状态的 ProxyHandler（空代理被优化为 UnknownHandler）"""
    for h in OPENER.handlers:
        if isinstance(h, urllib.request.ProxyHandler) and h.proxies:
            return False
    return True


def no_redirect_effective():
    """禁重定向是否生效：链上用的是我们的 _NoRedirect"""
    return any(isinstance(h, _NoRedirect) for h in OPENER.handlers)

# 台账列号（A=1 … Z=26，按列号取数不按表头名——防加列漂移；与 ledger_ops.COLS 对齐）
COL = {'seq': 1, 'case_no': 2, 'name': 3, 'category': 4, 'client': 5, 'court': 6,
       'judge': 7, 'hearing': 8, 'progress': 9, 'log': 10, 'status': 11,
       'seal': 24, 'balance': 21, 'note': 26}
CASE_SHEETS = list(ledger_ops.SHEETS_CASE)      # ['案件', '其他案件']（单一来源）
LETTER_SHEET = ledger_ops.SHEET_LETTER          # '律师函'


def log(action, detail=''):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s | %s | %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                        action, detail))
    except OSError:
        pass


# ---------------------------------------------------------------- 通用 IO

def _atomic_write_json(path, obj):
    """临时文件 + os.replace 原子替换（防读到写一半的 JSON）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:                                        # noqa: BLE001
        return default


# ---------------------------------------------------------------- 忽略名单 / 历史

def load_ignore(apply=False):
    """忽略名单（关键词数组）。首次调用若工作台侧不存在而桌宠侧存在 → 迁移拷入。"""
    if not NO_MIGRATE and not os.path.isfile(IGNORE_PATH) and os.path.isfile(PETDEX_IGNORE):
        arr = _load_json(PETDEX_IGNORE, None)
        if isinstance(arr, list) and apply:
            _atomic_write_json(IGNORE_PATH, arr)
    arr = _load_json(IGNORE_PATH, [])
    if not isinstance(arr, list):
        return []
    return [str(k).strip() for k in arr if str(k).strip()]


def save_ignore(items, apply=False):
    items = [str(k).strip() for k in items if str(k).strip()]
    if apply:
        _atomic_write_json(IGNORE_PATH, items)
    return items


def ignore_add(word, apply=False):
    word = str(word or '').strip()
    if not word:
        return load_ignore(), False
    cur = load_ignore()
    if word in cur:
        return cur, False
    cur.append(word)
    save_ignore(cur, apply=apply)
    log('ASSISTANT_IGNORE_ADD', word)
    return cur, True


def ignore_remove(word, apply=False):
    word = str(word or '').strip()
    cur = load_ignore()
    if word not in cur:
        return cur, False
    cur = [k for k in cur if k != word]
    save_ignore(cur, apply=apply)
    log('ASSISTANT_IGNORE_REMOVE', word)
    return cur, True


def load_history(apply=False):
    """对话历史（最近 6 轮 = 12 条）。首次自动从桌宠侧迁移。"""
    if not NO_MIGRATE and not os.path.isfile(HISTORY_PATH) and os.path.isfile(PETDEX_HISTORY):
        arr = _load_json(PETDEX_HISTORY, None)
        if isinstance(arr, list) and apply:
            _atomic_write_json(HISTORY_PATH, arr)
    h = _load_json(HISTORY_PATH, [])
    if not isinstance(h, list):
        return []
    return h[-MAX_HISTORY_TURNS * 2:]


def save_history(items, apply=False):
    if apply:
        _atomic_write_json(HISTORY_PATH, items[-MAX_HISTORY_TURNS * 2:])
    return items[-MAX_HISTORY_TURNS * 2:]


def clear_history(apply=False):
    if apply:
        _atomic_write_json(HISTORY_PATH, [])
    log('ASSISTANT_HISTORY_CLEAR', '')
    return []


def _clean_history_for_llm(history):
    """只放行 role/content 两个字段，防脏数据入库/外发（Sol round7）"""
    out = []
    for h in history or []:
        if (isinstance(h, dict) and h.get('role') in ('user', 'assistant')
                and str(h.get('content') or '').strip()):
            out.append({'role': h['role'], 'content': str(h['content'])})
    return out


# ---------------------------------------------------------------- 提醒快照

def _rid(text):
    """提醒 id = 文案 md5 前 10 位。同文案跨轮同 id → read/dismissed 状态不丢；
    文案变则 id 变 = 新提醒。"""
    return hashlib.md5(str(text).encode('utf-8')).hexdigest()[:10]


def load_reminders():
    arr = _load_json(REMINDERS_PATH, [])
    return arr if isinstance(arr, list) else []


def save_reminders(items, apply=False):
    if apply:
        _atomic_write_json(REMINDERS_PATH, items)
    return items


def merge_reminders(new_items, old_items, apply=False):
    """合并新一轮 LLM 事件与旧快照：
    - 同 id（同文案）→ 保留旧 read/dismissed 状态，更新 priority/change/generated_at
    - 旧快照里 dismissed 且本轮未再出现 → 丢弃（不再提醒）
    - 旧快照 dismissed 但本轮仍出现 → 保留 dismissed=True（用户说过不再提醒，尊重）
    """
    old_by_id = {x.get('id'): x for x in (old_items or []) if isinstance(x, dict)}
    now = datetime.now().isoformat(timespec='seconds')
    out = []
    for it in new_items or []:
        text = str(it.get('text') or '').strip()
        if not text:
            continue
        i = _rid(text)
        prev = old_by_id.get(i) or {}
        out.append({
            'id': i,
            'priority': str(it.get('priority') or 'normal'),
            'text': text,
            'state': str(it.get('state') or 'none'),
            'change': str(it.get('change') or 'same'),
            'generated_at': now,
            'read': bool(prev.get('read', False)),
            'dismissed': bool(prev.get('dismissed', False)),
        })
    if apply:
        _atomic_write_json(REMINDERS_PATH, out)
    return out


def mark_read(rid, dismissed=False, apply=False):
    items = load_reminders()
    hit = False
    for it in items:
        if isinstance(it, dict) and it.get('id') == rid:
            it['read'] = True
            if dismissed:
                it['dismissed'] = True
            hit = True
    if hit and apply:
        _atomic_write_json(REMINDERS_PATH, items)
    return items, hit


# ---------------------------------------------------------------- 跨进程锁

def load_state():
    st = _load_json(STATE_PATH, {})
    return st if isinstance(st, dict) else {}


def _pid_alive(pid):
    """探活：os.kill(pid, 0) 不杀进程，Windows 下进程不存在抛 OSError"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:                                        # noqa: BLE001
        return False


def state_busy(st=None):
    """是否正在分析中；崩溃残留（busy=true 但 pid 已死）→ 自动接管重置"""
    st = st if st is not None else load_state()
    if not st.get('busy'):
        return False
    if _pid_alive(st.get('pid')):
        return True
    log('ASSISTANT_LOCK_STALE', 'pid=%s 已死，接管重置' % st.get('pid'))
    return False


def set_state(patch, apply=False):
    st = load_state()
    st.update(patch)
    if apply:
        _atomic_write_json(STATE_PATH, st)
    return st


# ---------------------------------------------------------------- 日期解析

DATE_PATTERNS = [
    (re.compile(r'(\d{4})\s*[./\-年]\s*(\d{1,2})\s*[./\-月]\s*(\d{1,2})\s*日?'), 'full'),
    (re.compile(r'(\d{1,2})\s*[./月]\s*(\d{1,2})\s*[日号]?'), 'md'),
]
ALL_DATES_RE = re.compile(
    r'(\d{4})\s*[./\-年]\s*(\d{1,2})\s*[./\-月]\s*(\d{1,2})|(\d{1,2})\s*[./月]\s*(\d{1,2})\s*[日号]?')
TODO_KEYWORDS = ['跟进', '联系', '催款', '催办', '询问', '确认', '领取', '提交',
                 '缴费', '续封', '上诉', '到期', '开庭', '判决', '执行']


def parse_date(v, ref=None):
    """容错日期解析：datetime / Excel serial / 字符串。返回 date 或 None。"""
    ref = ref or date.today()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 30000 < v < 60000:     # Excel serial（1990+）
        try:
            from openpyxl.utils.datetime import from_excel
            d = from_excel(v)
            return d.date() if hasattr(d, 'date') else d
        except Exception:                                    # noqa: BLE001
            return None
    if not isinstance(v, str):
        return None
    v = v.strip()
    for pat, kind in DATE_PATTERNS:
        m = pat.search(v)
        if not m:
            continue
        try:
            if kind == 'full':
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2000 <= y <= 2100:
                    return date(y, mo, d)
            else:
                # MM.DD：年份按最近的那个 M月D日推（跨年回退见 _md_to_date）
                return _md_to_date(int(m.group(1)), int(m.group(2)), ref)
        except (ValueError, TypeError):
            continue
    return None


def _md_to_date(mo, d, ref):
    """MM.DD → date：年份按 ref 当年推；若推算结果已在 60 天前的远过去 → 回退去年。

    ⚠️ 口径说明（2026-09-18 迁移时定，勿轻改）：**只判过去，不判未来**。
    台账 I 列待办/日志里的 MM.DD 是「要做的事」，语义偏未来——例如 9 月看到
    「12.31」应理解为今年末（未来 3 个月），而不是已过去 8 个月的去年末。
    故对「今年末落在未来」的情形**不做回退**（即使未来 300 天，也比错误回退到过
    去安全：回退错了会把未来待办变成历史事项，直接漏提醒）。
    单一实现：parse_date 与 parse_todo_from_progress 共用，防口径漂移。"""
    cand = date(ref.year, mo, d)
    if (ref - cand).days > 60:
        cand = date(ref.year - 1, mo, d)
    return cand


def parse_todo_from_progress(cases):
    """I 列待办识别：'关键词字段 + 日期' 同条命中才算候选；宁漏报不错报。"""
    today = date.today()
    todos = []
    for c in cases:
        text = c.get('progress', '')
        if not text:
            continue
        for kw in TODO_KEYWORDS:
            if kw not in text:
                continue
            dates = []
            for m in ALL_DATES_RE.finditer(text):
                try:
                    if m.group(1):
                        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        if 2000 <= y <= 2100:
                            dates.append(date(y, mo, d))
                    else:
                        dates.append(_md_to_date(int(m.group(4)), int(m.group(5)), today))
                except (ValueError, TypeError):
                    continue
            if dates:
                todos.append({'case': c.get('name', ''), 'case_no': c.get('case_no', ''),
                              'sheet': c.get('sheet', ''), 'row': c.get('row'),
                              'date': max(dates).isoformat(),
                              'dates': sorted(set(d.isoformat() for d in dates)),
                              'text': text[:60]})
            break
    return todos


# ---------------------------------------------------------------- 台账读取

def _cell_str(ws, row, col):
    v = ws.cell(row=row, column=col).value
    return '' if v is None else str(v).strip()


def parse_case_sheet(ws):
    """解析案件 sheet。返回 (cases, warnings)。数据行判定 = A 列为正整数序号。"""
    cases, warnings = [], []
    for row in range(1, ws.max_row + 1):
        seq = ws.cell(row=row, column=COL['seq']).value
        if not (isinstance(seq, (int, float)) and seq == int(seq) and seq > 0):
            continue
        c = {'sheet': ws.title, 'row': row, 'seq': int(seq),
             'case_no': _cell_str(ws, row, COL['case_no']),
             'name': _cell_str(ws, row, COL['name']),
             'status': _cell_str(ws, row, COL['status']),
             'progress': _cell_str(ws, row, COL['progress']),
             'log': _cell_str(ws, row, COL['log']),
             'seal_text': _cell_str(ws, row, COL['seal']),
             'balance': ws.cell(row=row, column=COL['balance']).value}
        c['hearing'] = parse_date(ws.cell(row=row, column=COL['hearing']).value)
        hv = ws.cell(row=row, column=COL['hearing']).value
        if hv and not c['hearing']:
            warnings.append('%s 第%d行 开庭时间无法解析: %r [待核实]' % (ws.title, row, hv))
        c['seal_deadline'] = parse_date(c['seal_text']) if c['seal_text'] else None
        if c['seal_text'] and not c['seal_deadline']:
            warnings.append('%s 第%d行 保全期限无法解析: %r [待核实]'
                            % (ws.title, row, c['seal_text']))
        cases.append(c)
    return cases, warnings


def parse_ledger(path=None):
    """只读解析台账（copy 到临时文件防 WPS 锁）。返回结构化结果 dict。"""
    path = path or ledger_ops.ledger_path()
    result = {'ok': False, 'stale': True, 'generated_at': None,
              'cases': [], 'letters': [], 'todos': [], 'warnings': [], 'errors': []}
    if not os.path.isfile(path):
        result['errors'].append('台账不存在: %s' % path)
        return result
    fd, tmp = tempfile.mkstemp(prefix='ledger-assistant-', suffix='.xlsx')
    os.close(fd)
    try:
        shutil.copy2(path, tmp)
        import openpyxl
        wb = openpyxl.load_workbook(tmp, data_only=True, read_only=True)
        try:
            for name in CASE_SHEETS:
                if name in wb.sheetnames:
                    cs, ws_warn = parse_case_sheet(wb[name])
                    result['cases'] += cs
                    result['warnings'] += ws_warn
            if LETTER_SHEET in wb.sheetnames:
                ws = wb[LETTER_SHEET]
                for row in range(1, min(ws.max_row, 60) + 1):
                    vals = [_cell_str(ws, row, col) for col in range(1, 15)]
                    if any(vals):
                        result['letters'].append({'sheet': LETTER_SHEET, 'row': row,
                                                  'cells': vals})
        finally:
            try:
                wb.close()
            except Exception:                                # noqa: BLE001
                pass
        result['todos'] = parse_todo_from_progress(result['cases'])
        result['ok'] = True
        result['stale'] = False
        result['generated_at'] = datetime.now().isoformat(timespec='seconds')
    except Exception as e:                                   # noqa: BLE001
        result['errors'].append('%s: %s' % (type(e).__name__, e))
    finally:
        try:
            os.remove(tmp)                                   # xlsx 不留盘
        except OSError:
            pass
    return result


def build_ledger_context(path=None):
    """全量台账 → 文本上下文（档位A）。数据源锁死 = 台账单文件。
    单一实现：对话与提醒分析共用（口径不许漂移）。"""
    path = path or ledger_ops.ledger_path()
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    fd, tmp = tempfile.mkstemp(prefix='ledger-assistant-', suffix='.xlsx')
    os.close(fd)
    try:
        shutil.copy2(path, tmp)
        return _build_context_impl(tmp, today)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _cell(ws, row, col):
    v = ws.cell(row=row, column=col).value
    return str(v).strip() if v is not None else ''


def _money(v):
    """金额归一：数字保留两位，空值返回 ''（喂给模型不该出现 None）"""
    if v in (None, '', 0, '0'):
        return ''
    try:
        return '%.2f' % float(v)
    except (TypeError, ValueError):
        return str(v).strip()


def _build_context_impl(tmp_path, today):
    """台账全字段 → 文本。2026-09-19 扩容（B 方案）：
    原只喂 8 列（案名/案号/状态/开庭/进度/日志/保全/尾款），现补全**台账里已有**的
    实务字段：类别/委托人/法院/法官/合同编号/委托阶段/标的额/律师费/风险费/
    已收/付款日期/开票/发票号/保全费/保费/承办律师。
    不新增外发面——全部来自同一个台账文件，只是喂得更全。"""
    import openpyxl
    wb = openpyxl.load_workbook(tmp_path, data_only=True, read_only=True)
    parts = ['[台账快照 %s]' % today]
    try:
        for name in CASE_SHEETS:
            if name not in wb.sheetnames:
                continue
            ws = wb[name]
            parts.append('== %s ==' % name)
            for row in range(1, ws.max_row + 1):
                seq = ws.cell(row=row, column=1).value
                if not (isinstance(seq, (int, float)) and seq == int(seq) and seq > 0):
                    continue
                case_no = _cell(ws, row, 2)
                nm = _cell(ws, row, 3)
                parts.append('◆ %s｜案号:%s｜状态:%s'
                             % (nm, case_no or '未立案', _cell(ws, row, 11) or '空'))
                # 基本情况
                for label, col in (('类别', 4), ('委托人', 5), ('法院', 6), ('承办法官', 7)):
                    v = _cell(ws, row, col)
                    if v:
                        parts.append('  %s: %s' % (label, v[:80]))
                hearing = ws.cell(row=row, column=8).value
                if hearing not in (None, ''):
                    parts.append('  开庭: %s' % hearing)
                prog = _cell(ws, row, 9)
                if prog:
                    parts.append('  进度: %s' % prog[:200])
                logj = _cell(ws, row, 10)
                if logj:
                    parts.append('  日志: %s' % logj[:700])
                # 委托合同
                for label, col in (('合同编号', 12), ('委托阶段', 13)):
                    v = _cell(ws, row, col)
                    if v:
                        parts.append('  %s: %s' % (label, v[:80]))
                amt = _money(ws.cell(row=row, column=14).value)
                if amt:
                    parts.append('  标的额: %s' % amt)
                fee = _money(ws.cell(row=row, column=15).value)
                if fee:
                    parts.append('  基础律师费: %s' % fee)
                risk = _cell(ws, row, 16)
                if risk:
                    parts.append('  风险费约定: %s' % risk[:100])
                recv = _money(ws.cell(row=row, column=17).value)
                if recv:
                    parts.append('  已收到: %s' % recv)
                payday = ws.cell(row=row, column=18).value
                if payday not in (None, ''):
                    parts.append('  付款日期: %s' % payday)
                inv_amt = _money(ws.cell(row=row, column=19).value)
                if inv_amt:
                    parts.append('  开票金额: %s' % inv_amt)
                inv_no = _cell(ws, row, 20)
                if inv_no:
                    parts.append('  发票号: %s' % inv_no[:60])
                bal = _money(ws.cell(row=row, column=21).value)
                if bal:
                    parts.append('  剩余尾款: %s' % bal)
                for label, col in (('保全费', 22), ('保费', 23)):
                    v = _money(ws.cell(row=row, column=col).value)
                    if v:
                        parts.append('  %s: %s' % (label, v))
                seal = _cell(ws, row, 24)
                if seal:
                    parts.append('  保全及期限: %s' % seal[:200])
                lawyer = _cell(ws, row, 25)
                if lawyer:
                    parts.append('  承办律师: %s' % lawyer[:60])
                note = _cell(ws, row, 26)
                if note:
                    parts.append('  备注: %s' % note[:200])
        if LETTER_SHEET in wb.sheetnames:
            ws = wb[LETTER_SHEET]
            parts.append('== 律师函 ==')
            for row in range(1, min(ws.max_row, 60) + 1):
                vals = [_cell(ws, row, c) for c in range(1, 15)]
                if any(vals):
                    parts.append('◆ ' + '｜'.join(v for v in vals if v)[:200])
    finally:
        try:
            wb.close()
        except Exception:                                    # noqa: BLE001
            pass
    ctx = '\n'.join(parts)
    return ctx + _build_workbench_blocks(today)


def _build_workbench_blocks(today):
    """工作台其它本地数据源 → 文本区块（B 方案，2026-09-19）。

    ⚠️ 外发边界（铁律）：只喂**元数据/索引/文本字段**，绝不喂文件正文。
    - 任务表：标题/状态/优先级/截止/备注（本机 sqlite）
    - 期限表：到期日/类型/状态（台账派生，同源）
    - 日程表：日期/类型/标题
    - 客户扩展：联系人/电话/微信/备注（**本机留存，非案卷材料**）
    - 案卷文件**只喂「案件 → 文件数 + 子目录名」**（目录结构属元数据；
      文件正文/证据内容一律不读、不喂）
    - 智能提醒快照（已有分析结果）
    任一源读取失败 → 该区块标注「读取失败」并继续，不让整体挂掉。
    """
    blocks = []

    # ── 任务 ────────────────────────────────────────────────────────────
    try:
        import tasks_ops
        items = tasks_ops.list_tasks() or []
        lines = []
        for t in items[:60]:
            due = t.get('due_date') or ''
            flag = ''
            if t.get('逾期'):
                flag = '［逾期］'
            elif t.get('今日'):
                flag = '［今日］'
            row = '%s%s｜%s' % (flag, t.get('title', ''), t.get('status', ''))
            if t.get('priority') and t['priority'] != '普通':
                row += '｜%s' % t['priority']
            if due:
                row += '｜截止 %s' % due
            if t.get('case_ref'):
                row += '｜关联 %s' % t['case_ref']
            if t.get('note'):
                row += '｜%s' % str(t['note'])[:60]
            lines.append('- ' + row)
        blocks.append('== 工作台·任务（%d 项%s）==\n%s' % (
            len(items), '，只列前 60' if len(items) > 60 else '',
            '\n'.join(lines) if lines else '（无）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·任务 ==\n（读取失败：%s）' % str(e)[:80])

    # ── 期限 ────────────────────────────────────────────────────────────
    try:
        import deadline_ops
        d = deadline_ops.load() or {}
        items = d.get('items') or []
        lines = []
        for it in items[:80]:
            st = it.get('状态') or ''
            if st == 'done':
                continue
            lines.append('- %s｜%s｜到期 %s%s' % (
                it.get('案件', ''), it.get('期限类型', ''), it.get('到期日', ''),
                '｜%s' % it.get('备注', '')[:60] if it.get('备注') else ''))
        blocks.append('== 工作台·期限（未完成 %d 项）==\n%s' % (
            len(lines), '\n'.join(lines) if lines else '（无未完成期限）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·期限 ==\n（读取失败：%s）' % str(e)[:80])

    # ── 日程 ────────────────────────────────────────────────────────────
    try:
        import schedule_ops
        d = schedule_ops.load() or {}
        items = d.get('items') or []
        lines = ['- %s｜%s｜%s' % (it.get('日期', ''), it.get('类型', ''), it.get('标题', ''))
                 for it in items[:50]]
        blocks.append('== 工作台·日程（%d 项）==\n%s' % (
            len(items), '\n'.join(lines) if lines else '（无）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·日程 ==\n（读取失败：%s）' % str(e)[:80])

    # ── 客户扩展信息 ────────────────────────────────────────────────────
    # ⚠️ 2026-09-19 爸爸拍板：**电话/微信/联系人一律不喂**（个人信息，不上行）。
    #    只喂「客户名 + 备注」——备注可能含案件背景，属台账同源信息。
    try:
        import tasks_ops
        items = tasks_ops.list_clients_ext() or []
        lines = []
        for c in items[:60]:
            seg = [str(c.get('client_key', ''))]
            if c.get('note'):
                seg.append('备注:%s' % str(c['note'])[:60])
            if len(seg) > 1:
                lines.append('- ' + '｜'.join(seg))
        blocks.append('== 工作台·客户备注（%d 条；联系方式按纪律不上行）==\n%s' % (
            len(lines), '\n'.join(lines) if lines else '（无）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·客户备注 ==\n（读取失败：%s）' % str(e)[:80])

    # ── 案卷文件索引（只目录结构，不读正文）────────────────────────────
    # ⚠️ 2026-09-19 实踩两个坑，已修：
    #   ① 原来只枚举目录名 → 模型得**自己数**，实测数错（先答 18 后改 19）。
    #      修法：**直接报总数**，别让模型做算术。
    #   ② `_归档区` 是横向材料区（批量催收/差旅/顾问单位），**不是案件夹**，
    #      混在总数里会虚增。修法：分类计数 + 显式标注。
    try:
        import case_ops
        root = case_ops.case_root()
        lines, n_case, n_other = [], 0, 0
        if os.path.isdir(root):
            for d in sorted(os.listdir(root))[:60]:
                p = os.path.join(root, d)
                if not os.path.isdir(p) or d.startswith('.'):
                    continue
                subs = [x for x in sorted(os.listdir(p))
                        if os.path.isdir(os.path.join(p, x)) and not x.startswith('.')]
                nfile = 0
                for dp, dn, fn in os.walk(p):
                    nfile += len([f for f in fn if not f.startswith('~$')])
                    if nfile > 5000:
                        break
                if d.startswith('_'):
                    tag = '（横向材料区，非案件夹）'
                    n_other += 1
                else:
                    tag = ''
                    n_case += 1
                lines.append('- %s%s｜子目录: %s｜文件约 %d 个' % (
                    d, tag, '、'.join(subs[:8]) if subs else '（无）', nfile))
        header = '== 工作台·案卷索引（仅目录结构，不含文件内容）==\n' \
                 '【统计】案件夹 %d 个；横向材料区 %d 个。' % (n_case, n_other)
        if n_case or n_other:
            header += '（问「几个案件」时答 **%d**，不含横向材料区）' % n_case
        blocks.append(header + '\n' + ('\n'.join(lines) if lines else '（无）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·案卷索引 ==\n（读取失败：%s）' % str(e)[:80])

    # ── 智能提醒快照 ────────────────────────────────────────────────────
    try:
        items = load_reminders() or []
        lines = ['- [%s] %s' % (it.get('priority', ''), it.get('text', ''))
                 for it in items[:20] if not it.get('dismissed')]
        blocks.append('== 工作台·智能提醒（%d 条未忽略）==\n%s' % (
            len(lines), '\n'.join(lines) if lines else '（无）'))
    except Exception as e:                                   # noqa: BLE001
        blocks.append('== 工作台·智能提醒 ==\n（读取失败：%s）' % str(e)[:80])

    return '\n\n' + '\n\n'.join(blocks)


# ---------------------------------------------------------------- LLM 通道

def get_local_cfg():
    """LLM 端点配置。任何 OpenAI 兼容端点均可（本地 / 自建代理 / 云端）。
    密钥运行时读文件，不硬编码、不落日志；未配置 base 即视为该功能关闭。"""
    if not LOCAL_PROXY_BASE:
        raise RuntimeError('未配置 LLM 端点：请在 workbench.config.json 的 llm.base_url 填写')
    base = LOCAL_PROXY_BASE.rstrip('/')
    token = ''
    if API_KEY_FILE:
        try:
            token = open(API_KEY_FILE, encoding='utf-8').read().strip()
        except OSError as e:
            raise RuntimeError('读不到 LLM 密钥文件 %s: %s' % (API_KEY_FILE, e))
    return base, token


def _llm_call(messages, timeout=ANALYZE_TIMEOUT, retries=1):
    """OpenAI 形态调用（wb-proxy /v1/chat/completions）。返回 content 文本。
    retries=1 → 失败重试 1 次（指数退避 5s）。"""
    base, token = get_local_cfg()
    last_err = None
    data = None
    for attempt in range(retries + 1):
        try:
            payload = {'model': MODEL, 'stream': False, 'messages': messages}
            req = urllib.request.Request(
                base + '/v1/chat/completions',
                data=json.dumps(payload).encode('utf-8'), method='POST',
                headers={'content-type': 'application/json',
                         'authorization': 'Bearer ' + token if token else 'none'})
            with OPENER.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            break
        except Exception as e:                               # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(5)
    if data is None:
        raise RuntimeError('LLM 调用重试后仍失败: %s' % (type(last_err).__name__,))
    try:
        return ((data.get('choices') or [{}])[0].get('message', {}).get('content') or '')
    except Exception:                                        # noqa: BLE001
        return ''


def llm_reachable():
    """探活 LLM 端点（只做 TCP 可达性，不花钱）。返回 (ok, msg)"""
    if not LOCAL_PROXY_BASE:
        return False, '未配置 LLM：在 workbench.config.json 的 llm.base_url 填入任意 OpenAI 兼容端点'
    try:
        import socket
        from urllib.parse import urlparse
        u = urlparse(LOCAL_PROXY_BASE if '//' in LOCAL_PROXY_BASE else 'http://' + LOCAL_PROXY_BASE)
        host, port = u.hostname or '127.0.0.1', u.port or (443 if u.scheme == 'https' else 80)
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        return True, 'ok'
    except Exception as e:                                   # noqa: BLE001
        return False, 'LLM 端点不可达（%s）：%s' % (LOCAL_PROXY_BASE, type(e).__name__)


# ---------------------------------------------------------------- 提示词

ANALYZE_RULES = (
    "1. 开庭日=今天 → 最高优先；2. 明确写了'X天后要做某事'且日期已到/临近 → 提醒；\n"
    "3. 写了跟进/联系/催款等且承诺时间已过 → 催办提醒；\n"
    "4. 开庭时间有变更/送达待确认/续封临期/尾款拖欠 → 提醒；\n"
    "5. 已办结或久远的不要报。\n"
)
ANALYZE_FORMAT = (
    "输出要求（必须遵守）：直接输出 JSON 数组，你的回复的第一个字符必须是 [；\n"
    "禁止输出任何分析过程、解释、markdown；每项 {\"priority\":\"high\"|\"normal\"|\"low\",\n"
    "\"text\":\"不超过45字的提醒文案\",\"state\":\"review\"|\"jumping\"|\"failed\"|\"waiting\"|\"none\",\n"
    "\"change\":\"new\"|\"updated\"|\"same\"}，\n"
    "有效提醒最多 %d 条，按优先级排序。宁可少报不错报，日期/案号不得编造。\n"
    "【案名硬规则】每条 text 必须以案件短名开头（如'当事人甲案：开庭改至10月8日'）。\n"
    "短名取'案件'列的对方当事人或核心主体（'福州闽兴vs当事人甲、吴群华...'→'当事人甲案'；\n"
    "'厦门中非世野野生动物园'→'中非世野案'），禁止只写案号尾号（如'1016号案'）；"
    "45 字限额含案名。\n" % LLM_MAX_REMINDERS
)
CHAT_SYSTEM = (
    "你是煤球——法律工作台助理，服务对象是实习律师（称呼'爸爸'）。"
    "数据来源是**本机法律工作台的本地数据**，分为六块："
    "①案件台账（案件名/案号/状态/开庭/进度/日志/保全/尾款/合同/费用/承办律师等）"
    "②工作台任务  ③期限表  ④日程表  ⑤案卷目录索引  ⑥客户备注。"
    "严格依据下方数据回答：案号/金额/日期必须与数据一致，"
    "数据里没有的信息明确说'工作台数据中没有记录'，绝不编造。"
    "【隐私纪律】客户电话/微信等联系方式**不在你的数据范围内**（按纪律不上行），"
    "被问到时答'联系方式未纳入助理数据，请在工作台「客户」页查看'。"
    "提到具体案件时用案名指代（对方当事人或核心主体+案，如'当事人甲案''中非世野案'），"
    "不要只报案号尾号。"
    "【边界】案卷里**文件的正文内容**（起诉状/证据/判决书等）你看不到，"
    "只有目录索引；问这类内容要明说'我看不到文件正文，只知目录结构'，"
    "并建议到煤球主会话（案件工作区）查阅。"
    "【🔴 最重要的纪律：你是只读的，没有任何写入能力】"
    "你**只能读数据、回答、给建议**。你**无法**修改台账、无法登记签收状态、"
    "无法关闭任务、无法改期限、无法写任何文件——这些功能你**根本没有**。"
    "所以**绝对不要**说「把X给我，我帮你登记/录入/更新/记录/改一下」这类话，"
    "那是**不存在的功能**，说了就是欺骗。正确做法：告知**去哪个页面自己操作**，"
    "指引话术举例——"
    "「签收状态登记 → 请到工作台『台账进展』页，律师函表那一行直接改签收状态列」；"
    "「任务标完成 → 请到『任务』页点该任务的完成」；"
    "「新增期限 → 请到『期限与日历』页新增」。"
    "可以帮他把**要填的内容整理好**（如『单号 EMS123... 签收时间 X 月 X 日』），"
    "让他复制过去，但**必须说清这不是我登记的**。"
    "涉及法律策略判断时，提示'建议在煤球主会话深入讨论'。"
    "【记忆】你只有最近若干轮对话的短期记忆，更早的记不住；"
    "若爸爸问的是本轮上下文之外的历史对话，如实说'更早的对话我记不住了'，不要猜。"
    "【摘要纪律】回答只给关键信息：先给结论/数字/去处，再给必要的一句依据；"
    "不要复述推导过程、不要长篇解释、不客套；清单类回答用短句直列（批 66）。"
    "【语气】不确定就说不确定；做不到就说做不到。**宁可显得笨，也不假装能做到**——"
    "你是助理不是演员，诚实比讨喜重要。"
    "用中文，简洁口语，像贴身助理不像百科。"
    "今天是 %s。\n\n%s"
)


def build_analyze_prompt(context, prev_items=None, prev_time=None, ignore_kws=None):
    """组装分析用的 user message（原样搬运 case_remind.py 的分段顺序）"""
    today = date.today().isoformat()
    msg = ('今天是 %s。以下是律师事务所案件台账全量数据'
           '（案件名/案号/状态/开庭/进度/日志/保全/尾款等）。'
           '请判断哪些需要在今天推送提醒给承办律师：\n' % today) + ANALYZE_RULES + \
        ANALYZE_FORMAT + '\n' + context
    if prev_items:
        prev_lines = '\n'.join('- %s' % it.get('text', '') for it in prev_items)
        msg += ('\n\n【上一轮提醒清单（生成于 %s）】\n%s\n'
                '请对比本轮台账与上一轮清单：\n'
                '- 仍有效的旧提醒：保留（原样或微调文案），标 "change":"same"；\n'
                '- 本轮新出现或内容有变化的：优先靠前，标 "change":"new" 或 "updated"；\n'
                '- 上一轮提过、但本轮台账显示已完成/已不存在的事项：输出一条销案提醒 '
                '{"priority":"low","text":"✅ 案件短名：已完成，提醒可销",'
                '"state":"none","change":"resolved"}（最多 2 条），'
                '这类不计入有效提醒名额。' % (prev_time or '', prev_lines))
    if ignore_kws:
        msg += ('\n\n【暂停处理名单】以下案件当事人/关键词相关的事项一律不要输出提醒：'
                '%s。' % '、'.join(ignore_kws))
    return msg


def _parse_events(text):
    """从 LLM 输出里抠 JSON 数组（容忍前后杂讯）"""
    text = text or ''
    m = re.search(r'\[\s*\{', text)
    if not m:
        log('ASSISTANT_LLM_NOJSON', 'content 前 200 字：%r' % text[:200])
        return []
    try:
        events, _ = json.JSONDecoder().raw_decode(text[m.start():])
    except json.JSONDecodeError as e:
        log('ASSISTANT_LLM_BADJSON', '%s；原文前 300 字：%r'
            % (e, text[m.start():m.start() + 300]))
        return []
    return events if isinstance(events, list) else []


# ---------------------------------------------------------------- 智能提醒分析

def analyze(apply=False, path=None):
    """跑一轮智能提醒分析。返回 dict 结果（供 API 与 CLI 共用）。"""
    t0 = time.time()
    parsed = parse_ledger(path)
    if not parsed['ok']:
        log('ASSISTANT_ANALYZE_FAIL', '; '.join(parsed['errors'])[:200])
        return {'ok': False, 'stage': 'ledger',
                'error': '台账读取失败：%s' % (parsed['errors'][0] if parsed['errors'] else '未知')}
    context = build_ledger_context(path)
    prev = load_reminders()
    prev_path = REMINDERS_PATH
    prev_time = None
    if os.path.isfile(prev_path):
        prev_time = datetime.fromtimestamp(os.path.getmtime(prev_path)).strftime('%m-%d %H:%M')
    ignore_kws = load_ignore()
    prompt = build_analyze_prompt(context, prev or None, prev_time, ignore_kws)
    try:
        content = _llm_call([{'role': 'user', 'content': prompt}], timeout=ANALYZE_TIMEOUT,
                            retries=1)
    except Exception as e:                                   # noqa: BLE001
        log('ASSISTANT_ANALYZE_FAIL', '%s: %s' % (type(e).__name__, e))
        ok, hint = llm_reachable()
        return {'ok': False, 'stage': 'llm',
                'error': '分析失败：%s' % e,
                'hint': None if ok else hint}
    events = _parse_events(content)
    items = [{'priority': str(ev.get('priority', 'normal')),
              'text': str(ev.get('text', '')).strip(),
              'state': str(ev.get('state', 'none')),
              'change': str(ev.get('change', 'same'))}
             for ev in events if isinstance(ev, dict) and str(ev.get('text', '')).strip()]
    active = [it for it in items if it['change'] != 'resolved']
    if ignore_kws:                                           # 第二道闸：LLM 漏拦的兜底
        dropped = [it for it in active if any(k in it['text'] for k in ignore_kws)]
        active = [it for it in active if not any(k in it['text'] for k in ignore_kws)]
        if dropped:
            log('ASSISTANT_IGNORE_FILTER',
                '%d 条：%s' % (len(dropped), '; '.join(x['text'][:30] for x in dropped)))
    active = active[:LLM_MAX_REMINDERS]
    resolved = [it['text'] for it in items if it['change'] == 'resolved'][:2]
    merged = merge_reminders(active, prev, apply=apply)
    # dismissed 的仍在报 → 尊重用户，标记但不重复推
    result = {'ok': True, 'count': len(merged), 'changed': len([x for x in merged
                                                               if x['change'] in ('new', 'updated')]),
              'resolved': resolved, 'items': merged,
              'cases': len(parsed['cases']), 'todos': len(parsed['todos']),
              'warnings': parsed['warnings'], 'elapsed': round(time.time() - t0, 1)}
    log('ASSISTANT_ANALYZE_OK', '%d 案 → %d 条提醒（变化 %d，销案 %d，%.1fs）'
        % (result['cases'], result['count'], result['changed'], len(resolved),
           result['elapsed']))
    return result


# ---------------------------------------------------------------- 对话

def chat(q, apply=False, path=None):
    """问一句台账（同步阻塞）。返回 {'ok': bool, 'reply': str}"""
    q = str(q or '').strip()
    if not q:
        return {'ok': False, 'reply': '问题为空'}
    if len(q) > 2000:
        return {'ok': False, 'reply': ('⚠️ 问题过长（>2000 字符）。台账查询不需要长文本；'
                                       '如需分析案件材料，请到煤球主会话（本地）。')}
    try:
        context = build_ledger_context(path)
    except Exception as e:                                   # noqa: BLE001
        log('ASSISTANT_CHAT_FAIL', 'context: %s' % e)
        return {'ok': False, 'reply': '⚠️ 台账读取失败：%s' % str(e)[:80]}
    history = _clean_history_for_llm(load_history())
    messages = [{'role': 'system',
                 'content': CHAT_SYSTEM % (datetime.now().strftime('%Y-%m-%d'), context)}]
    messages += history
    messages.append({'role': 'user', 'content': q})
    try:
        content = _llm_call(messages, timeout=CHAT_TIMEOUT, retries=0)
    except Exception as e:                                   # noqa: BLE001
        log('ASSISTANT_CHAT_FAIL', '%s: %s' % (type(e).__name__, e))
        ok, hint = llm_reachable()
        return {'ok': False, 'reply': '⚠️ 助理响应失败：%s%s'
                % (str(e)[:80], '' if ok else '\n' + hint)}
    reply = (content or '').strip() or '（模型返回为空，再问一次试试喵）'
    hist = load_history()
    hist.append({'role': 'user', 'content': q})
    hist.append({'role': 'assistant', 'content': reply})
    save_history(hist, apply=apply)
    return {'ok': True, 'reply': reply}


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='台账助理引擎 v%s' % VERSION)
    ap.add_argument('--analyze', action='store_true', help='跑一次智能提醒分析（真调 LLM）')
    ap.add_argument('--chat', action='store_true', help='问一句（真调 LLM）')
    ap.add_argument('--q', default='', help='--chat 的问题')
    ap.add_argument('--context-demo', action='store_true', help='只打印台账上下文')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--apply', action='store_true', help='真正写数据文件（默认 dry-run 不写）')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.context_demo:
        ctx = build_ledger_context()
        print(ctx[:3000])
        print('... 上下文共 %d 字符' % len(ctx))
        return 0
    if args.chat:
        r = chat(args.q, apply=args.apply)
        print(r['reply'])
        return 0 if r['ok'] else 3
    if args.analyze:
        r = analyze(apply=args.apply)
        if not r['ok']:
            print('FAIL:', r.get('error'))
            if r.get('hint'):
                print(r['hint'])
            return 3
        print('OK: %d 案 → %d 条提醒（变化 %d，%.1fs）'
              % (r['cases'], r['count'], r['changed'], r['elapsed']))
        for it in r['items']:
            print('  [%s] %s' % (it['priority'], it['text']))
        if not args.apply:
            print('（dry-run，未写 ai_reminders.json；加 --apply 才写）')
        return 0
    ap.print_help()
    return 1


# ---------------------------------------------------------------- 自测（不调 LLM）

def selftest():
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    # ---- 日期解析矩阵
    ref = date(2026, 9, 18)
    chk('日期 2026-09-20', parse_date('2026-09-20', ref) == date(2026, 9, 20))
    chk('日期 2026.9.20', parse_date('2026.9.20', ref) == date(2026, 9, 20))
    chk('日期 2026年9月20日', parse_date('2026年9月20日', ref) == date(2026, 9, 20))
    chk('日期 9.20（当年）', parse_date('9.20', ref) == date(2026, 9, 20))
    chk('日期 9月20日', parse_date('9月20日', ref) == date(2026, 9, 20))
    # 跨年口径（_md_to_date）：只判过去、不判未来 —— 已知取舍，勿轻改
    chk('9 月看 12.31 → 今年末（未来近 3 个月，待办语义）',
        parse_date('12.31', ref) == date(2026, 12, 31))
    chk('9 月看 3.1 → 回退去年（已过去 >60 天）',
        parse_date('3.1', ref) == date(2025, 3, 1))
    chk('1 月看 12.31 → 今年末（不回退；跨年待办应写明年份）',
        parse_date('12.31', date(2026, 1, 5)) == date(2026, 12, 31))
    chk('datetime 对象', parse_date(datetime(2026, 9, 20, 10, 0), ref) == date(2026, 9, 20))
    chk('date 对象', parse_date(date(2026, 9, 20), ref) == date(2026, 9, 20))
    chk('Excel serial 46273 → 有值', parse_date(46273, ref) is not None)
    chk('无法解析 → None', parse_date('待定', ref) is None)
    chk('空值 → None', parse_date(None, ref) is None)

    # ---- 待办识别（双向：真阳性必过 / 假阳性必拦）
    cases = [
        {'name': '当事人甲案', 'case_no': 'A1', 'sheet': '案件', 'row': 4,
         'progress': '跟进 9.20 联系法官'},
        {'name': '无日期案', 'case_no': 'B1', 'sheet': '其他案件', 'row': 5,
         'progress': '已跟进联系法官'},                     # 关键词有、日期无 → 不算待办
        {'name': '纯日期案', 'case_no': 'C1', 'sheet': '其他案件', 'row': 6,
         'progress': '9.20 收到材料'},                       # 日期有、关键词无 → 不算待办
        {'name': '多日期案', 'case_no': 'D1', 'sheet': '其他案件', 'row': 7,
         'progress': '09.12：已联系；跟进 2026-09-25 提交'},
    ]
    todos = parse_todo_from_progress(cases)
    names = [t['case'] for t in todos]
    chk('待办：真阳性（当事人甲案）命中', '当事人甲案' in names)
    chk('待办：假阳性（无日期）被拦', '无日期案' not in names)
    chk('待办：假阳性（无关键词）被拦', '纯日期案' not in names)
    chk('待办：多日期取最远（2026-09-25）',
        next((t['date'] for t in todos if t['case'] == '多日期案'), '') == '2026-09-25')

    # ---- 提醒 id 稳定性
    chk('id 同文案相同', _rid('当事人甲案：开庭') == _rid('当事人甲案：开庭'))
    chk('id 文案变则变', _rid('当事人甲案：开庭') != _rid('当事人甲案：改期'))

    # ---- merge 状态保持
    old = [{'id': _rid('X案：提醒'), 'text': 'X案：提醒', 'priority': 'high',
            'read': True, 'dismissed': True, 'change': 'same', 'state': 'none',
            'generated_at': 'x'}]
    merged = merge_reminders([{'priority': 'normal', 'text': 'X案：提醒',
                               'state': 'none', 'change': 'new'}], old)
    chk('merge：同 id 保留 read 状态', merged[0]['read'] is True)
    chk('merge：同 id 保留 dismissed 状态', merged[0]['dismissed'] is True)
    chk('merge：更新 priority', merged[0]['priority'] == 'normal')
    chk('merge：更新 change', merged[0]['change'] == 'new')
    chk('merge：空文案被过滤', merge_reminders([{'text': '  '}], []) == [])

    # ---- 忽略名单（双向）
    ig_prompt = build_analyze_prompt('ctx', None, None, ['某当事人'])
    chk('忽略名单进 prompt', '【暂停处理名单】' in ig_prompt and '某当事人' in ig_prompt)
    chk('无名单不进 prompt', '【暂停处理名单】' not in build_analyze_prompt('ctx'))
    chk('增量对比段含上一轮', '【上一轮提醒清单' in build_analyze_prompt('c', [{'text': 'A案'}], '09-18 09:00'))

    # ---- 上下文构造（合成 xlsx，不碰真台账）
    import openpyxl
    tmp = os.path.join(tempfile.gettempdir(), '_assistant_selftest.xlsx')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '案件'
    ws.cell(row=1, column=1, value='序号')
    ws.cell(row=4, column=1, value=1)
    ws.cell(row=4, column=2, value='(2026)闽01民初1号')
    ws.cell(row=4, column=3, value='福州闽兴vs当事人甲')
    ws.cell(row=4, column=8, value='2026-10-08')
    ws.cell(row=4, column=9, value='跟进 9.20 联系法官')
    ws.cell(row=4, column=11, value='待开庭')
    ws.cell(row=4, column=24, value='保全至 2027-03-01')
    ws.cell(row=4, column=21, value=138667)
    wb.save(tmp)
    wb.close()
    try:
        ctx = build_ledger_context(tmp)
        chk('上下文含案名', '当事人甲' in ctx)
        chk('上下文含案号', '(2026)闽01民初1号' in ctx)
        chk('上下文含开庭', '2026-10-08' in ctx or '2026-10-08 00:00:00' in ctx)
        chk('上下文含进度', '跟进 9.20 联系法官' in ctx)
        chk('上下文含保全', '保全至 2027-03-01' in ctx)
        chk('上下文含尾款', '138667' in ctx or '138667.0' in ctx or '138,667' in ctx)
        chk('上下文头含快照标记', ctx.startswith('[台账快照'))
        p = parse_ledger(tmp)
        chk('parse_ledger ok', p['ok'] is True)
        chk('parse_ledger 认到 1 案', len(p['cases']) == 1)
        chk('parse_ledger 开庭日期解析', p['cases'][0]['hearing'] == date(2026, 10, 8))
        chk('parse_ledger 保全期限解析', p['cases'][0]['seal_deadline'] == date(2027, 3, 1))
        chk('parse_ledger 认到待办', len(p['todos']) == 1)
        chk('临时副本已清理', not any(f.startswith('ledger-assistant-')
                                      for f in os.listdir(tempfile.gettempdir())))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    # ---- 解析容错（杂讯前后缀 / 非数组）
    chk('JSON 解析：前后杂讯', _parse_events('思考中…\n[{"text":"A案：X"}]\n以上。') == [{'text': 'A案：X'}])
    chk('JSON 解析：非数组 → 空', _parse_events('没有提醒') == [])
    chk('JSON 解析：空 → 空', _parse_events('') == [])

    # ---- 历史裁剪
    h = _clean_history_for_llm([{'role': 'user', 'content': 'a'},
                                {'role': 'system', 'content': 'x'},
                                {'role': 'assistant', 'content': ''},
                                {'role': 'assistant', 'content': 'b'},
                                'bad', None])
    chk('历史：只放行 user/assistant 且非空', len(h) == 2 and h[0]['role'] == 'user')

    # ---- 锁探活
    chk('锁：当前进程 pid 存活', _pid_alive(os.getpid()) is True)
    chk('锁：不存在的 pid 判死', _pid_alive(999999) is False)
    chk('锁：busy=false 不算忙', state_busy({'busy': False}) is False)
    chk('锁：busy=true + 死 pid → 接管', state_busy({'busy': True, 'pid': 999999}) is False)
    chk('锁：busy=true + 活 pid → 忙', state_busy({'busy': True, 'pid': os.getpid()}) is True)

    # ---- 通道锁定（防误配外发）
    try:
        _bad = LOCAL_PROXY_BASE
        chk('通道：base 为 127.0.0.1', _bad.startswith('http://127.0.0.1:'))
    except Exception:                                        # noqa: BLE001
        chk('通道：base 为 127.0.0.1', False)
    chk('opener 装了禁重定向', no_redirect_effective())
    chk('opener 绕代理生效（链上无启用态 ProxyHandler）', no_proxy_effective())

    # ---- 对话入参闸
    chk('chat 空问题', chat('')['ok'] is False)
    chk('chat 超长问题', chat('x' * 2001)['ok'] is False)

    # ---- 工作台助理扩容（2026-09-19 B 方案）
    chk('历史窗口 12 轮', MAX_HISTORY_TURNS == 12)
    # 🔴 只读纪律（批 45 爸爸拍板 A 方案）：助手绝不能声称能写数据
    #    （实踩：模型看到任务「登记签收状态」，主动说「把 EMS 单号给我我登记」= 幻觉功能）
    chk('提示词声明只读', '你是只读的' in CHAT_SYSTEM)
    chk('提示词禁「我帮你登记」类话术', '绝对不要' in CHAT_SYSTEM and '我帮你登记' in CHAT_SYSTEM)
    chk('提示词含页面引导示例', '台账进展' in CHAT_SYSTEM and '任务' in CHAT_SYSTEM
        and '期限与日历' in CHAT_SYSTEM)
    chk('提示词有诚实优先原则', '宁可显得笨' in CHAT_SYSTEM)
    # chat() 不得含任何写路径（白名单断言：模块内 chat 只许读）
    _csrc = inspect.getsource(chat)
    chk('chat() 无写入调用', all(k not in _csrc for k in
        ('save_ledger', 'write_ledger', 'ai_reminders.json', 'os.remove')))
    chk('_money 正常数', _money(138667) == '138667.00')
    chk('_money 小数', _money(1234.5) == '1234.50')
    chk('_money 空值', _money(None) == '' and _money('') == '' and _money(0) == '')
    chk('_money 非数透传', _money('待定') == '待定')
    # 六区块必须齐备（任一缺失 → 说明扩容退化）
    try:
        blocks = _build_workbench_blocks('2026-09-19')
        for kw in ('工作台·任务', '工作台·期限', '工作台·日程',
                   '工作台·客户备注', '工作台·案卷索引', '工作台·智能提醒'):
            chk('区块齐备 %s' % kw, kw in blocks)
        chk('区块以换行起（与台账段拼接）', blocks.startswith('\n'))
        # 边界：案卷索引必须自述「不含文件内容」
        chk('案卷索引声明不含正文', '不含文件内容' in blocks)
        # 案卷索引须给「统计」直报数（防模型自己数错，2026-09-19 实踩）
        chk('案卷索引直报总数', '【统计】案件夹' in blocks)
        chk('横向材料区被标注', '（横向材料区，非案件夹）' in blocks or '横向材料区 0 个' in blocks)
        # 边界：客户联系方式不出现在上下文（防将来误加）
        chk('客户电话不上行', '工作台·客户联系方式' not in blocks)
    except Exception as e:                                   # noqa: BLE001
        chk('六区块构造不抛异常', False)
        print('   区块构造异常:', repr(e)[:200])

    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(total), len(fails)))
    if fails:
        for f in fails:
            print('  FAILED:', f)
    return 0 if not fails else 3


if __name__ == '__main__':
    sys.exit(main())
