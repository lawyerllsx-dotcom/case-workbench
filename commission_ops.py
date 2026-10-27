# -*- coding: utf-8 -*-
r"""commission_ops.py — 委托手续套件生成（批 64）

民事委托手续六件套，从模板（templates\委托材料\，已标记化 `{{ 变量 }}`）
用 docxtpl 渲染 → 落选定案件夹 `01-委托手续\`。

六件（模板真源 <模板目录>，人稿只读；副本经
mark_commission_templates.py 一次性标记化）：
  1 委托代理合同       2 授权委托书-民事     3 办案反馈表
  4 聘请律师提示（纯固定）5 送达地址确认书（集美格式）  6 账户确认表（集美格式）

口径（sohan 先例）：
  · 日期/签名一律**留空**（生成后在 Word 里手填）；「委托时间」例外（表单可给默认今天）。
  · 必填缺失 → blocked（不生成空白件）；选填空 → 渲染为空（plan 里明示）。
  · 只做模板替换，不改版式；重名自动避让 `-2`；生成后扫描 `{{` 残留。
"""
import datetime
import io
import os
import re
import shutil
import zipfile

import config as _config

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, 'templates', '委托材料')

# 承办律师默认信息（表单预填，可改）——开源版从配置读取，默认占位、不含任何真实个人信息
DEFAULT_LAWYER = _config.get('lawyer.name', '')
DEFAULT_LAWYER_PHONE = _config.get('lawyer.phone', '')
DEFAULT_LAWYER_LICENSE = _config.get('lawyer.license', '')
DEFAULT_LAWYER_ADDR = _config.get('lawyer.address', '')
DEFAULT_COURT = _config.get('lawyer.default_court', '')

# 六件定义（顺序 = 生成顺序；file = 模板文件名，也用作输出文件名）
DOCS = [
    {'file': '1、委托代理合同（简易版）.docx'},
    {'file': '2、授权委托书-民事.docx'},
    {'file': '3、办案反馈表.docx'},
    {'file': '4、聘请律师提示.docx'},
    {'file': '5、送达地址确认书-某法院格式.docx'},
    {'file': '6、账户确认表-某法院格式.docx'},
]

STAGES = ['一', '二', '再']          # 渲染进「{{ stage }} 审阶段」
AUTH_TYPES = ['特别', '一般']
CASE_TYPES = ['民事', '商事', '执行', '非诉', '其它']

_RE_T = re.compile(r'<w:t(?: [^>]*)?>(.*?)</w:t>', re.S)


def template_status():
    """逐件检查模板存在且可开。返回 (ok, 明细, 错误)。"""
    detail = []
    ok = True
    for d in DOCS:
        p = os.path.join(TPL_DIR, d['file'])
        exists = os.path.isfile(p)
        readable = False
        err = ''
        if exists:
            try:
                with zipfile.ZipFile(p) as z:
                    readable = 'word/document.xml' in z.namelist()
            except Exception as e:                       # noqa: BLE001
                err = str(e)
        good = exists and readable
        ok = ok and good
        detail.append({'file': d['file'], 'exists': exists, 'readable': readable,
                       'path': p, 'error': err})
    return ok, detail, ('' if ok else '部分模板缺失或不可读')


def _esc(v):
    """文本节点值转义（& < >）——防特殊字符破坏 XML。"""
    return (str(v if v is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def build_context(fields):
    """表单字段 → 渲染上下文（含派生值）。fields 全为字符串/空串。"""
    f = {k: str(v or '').strip() for k, v in (fields or {}).items()}

    def g(k, default=''):
        return f.get(k) or default

    cause = g('cause')
    cause_d = cause[:-2] if cause.endswith('纠纷') else cause    # 合同句式「{{ cause_d }} 纠纷一案」
    client = g('client')
    ctx = {
        'client': _esc(client),
        'client_id': _esc(g('client_id')),
        'address': _esc(g('address')),
        'phone': _esc(g('phone')),
        'opponent': _esc(g('opponent')),
        'cause': _esc(cause),
        'cause_d': _esc(cause_d),
        'stage': _esc(g('stage')),
        'auth_type': _esc(g('auth_type', '特别')),
        'fee': _esc(g('fee')),
        'case_type': _esc(g('case_type')),
        'commission_date': _esc(g('commission_date')),
        'court': _esc(g('court', DEFAULT_COURT)),
        'case_no': _esc(g('case_no')),
        'payee': _esc(g('payee') or client),
        'bank': _esc(g('bank')),
        'account': _esc(g('account')),
        'lawyer': _esc(g('lawyer', DEFAULT_LAWYER)),
        'lawyer_phone': _esc(g('lawyer_phone', DEFAULT_LAWYER_PHONE)),
        'lawyer_license': _esc(g('lawyer_license', DEFAULT_LAWYER_LICENSE)),
        'lawyer_addr': _esc(g('lawyer_addr', DEFAULT_LAWYER_ADDR)),
    }
    return ctx


def plan_commission(fields, case_folder=''):
    """生成计划（dry-run）。返回 blocked / 每件预览 / 目标目录 / warnings。"""
    ok, detail, err = template_status()
    if not ok:
        miss = '、'.join(x['file'] for x in detail if not (x['exists'] and x['readable']))
        return {'blocked': '模板缺失或不可读：%s' % miss}

    f = {k: str(v or '').strip() for k, v in (fields or {}).items()}
    if not f.get('client'):
        return {'blocked': '缺必填：委托人（合同甲方 / 授权书委托人），不会生成空白件'}
    if f.get('stage') and f['stage'] not in STAGES:
        return {'blocked': '审级需为：%s' % '/'.join(STAGES)}
    auth = f.get('auth_type', '特别')
    if auth and auth not in AUTH_TYPES:
        return {'blocked': '授权方式需为：%s' % '/'.join(AUTH_TYPES)}

    ctx = build_context(f)

    warnings = []
    if not f.get('opponent'):
        warnings.append('对方当事人为空 → 合同/授权书对应位置留白（生成后手补）')
    if not f.get('cause'):
        warnings.append('案由为空 → 对应位置留白')
    if not f.get('fee'):
        warnings.append('律师费为空 → 合同第六条留白')
    if not case_folder:
        warnings.append('未选案卷 → 只预览不落盘（生成需先选案卷）')

    # 每件预览：{file, render(是否需渲染), fields_hit(该件会用到的键)}
    docs = []
    per_doc = {
        '1、委托代理合同（简易版）.docx': ['client', 'lawyer', 'opponent', 'cause_d', 'stage', 'fee'],
        '2、授权委托书-民事.docx': ['client', 'address', 'phone', 'lawyer', 'lawyer_phone',
                                    'opponent', 'cause', 'stage', 'auth_type'],
        '3、办案反馈表.docx': ['client', 'case_type', 'cause', 'lawyer', 'commission_date'],
        '4、聘请律师提示.docx': [],
        '5、送达地址确认书-某法院格式.docx': ['court', 'client', 'lawyer', 'client_id', 'lawyer_license',
                                                 'address', 'lawyer_addr', 'phone', 'lawyer_phone'],
        '6、账户确认表-某法院格式.docx': ['case_no', 'cause', 'client', 'phone', 'payee', 'bank', 'account'],
    }
    for d in DOCS:
        keys = per_doc.get(d['file'], [])
        docs.append({'file': d['file'], 'keys': keys,
                     'values': {k: ctx.get(k, '') for k in keys},
                     'render': bool(keys)})
    return {'dry_run': True, 'case_folder': case_folder, 'context': ctx,
            'docs': docs, 'warnings': warnings}


def _out_path(case_path, name):
    """落盘路径（重名避让 -2、-3…）"""
    dst = os.path.join(case_path, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        cand = os.path.join(case_path, '%s-%d%s' % (stem, n, ext))
        if not os.path.exists(cand):
            return cand
        n += 1


def _docx_has_marker(path):
    """读回校验：docx 里是否残留 {{ 未渲染标记。"""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        return '{{' in ''.join(m.group(1) for m in _RE_T.finditer(xml))
    except Exception:                                    # noqa: BLE001
        return True


def apply_commission(plan, case_path):
    """按计划渲染落盘到 <案件夹>\\01-委托手续\\。返回 {ok, items, errors}。"""
    from docxtpl import DocxTemplate
    outdir = os.path.join(case_path, '01-委托手续')
    os.makedirs(outdir, exist_ok=True)
    ctx = {k: v for k, v in plan['context'].items()}
    items = []
    errors = []
    for d in plan['docs']:
        tpl = os.path.join(TPL_DIR, d['file'])
        dst = _out_path(outdir, d['file'])
        try:
            t = DocxTemplate(tpl)
            t.render(ctx)
            t.save(dst)
            if _docx_has_marker(dst):
                errors.append('%s：渲染后仍有 {{ 残留（模板标记异常）' % d['file'])
            else:
                items.append(dst)
        except Exception as e:                           # noqa: BLE001
            errors.append('%s：%r' % (d['file'], e))
    return {'ok': not errors, 'outdir': outdir, 'items': items, 'errors': errors}
