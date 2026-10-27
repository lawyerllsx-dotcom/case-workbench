# -*- coding: utf-8 -*-
r"""sohan_ops.py — 所函生成（批 39a）

用户 2026-09-17 拍板：
  ① 落点 = 选定案卷 `01-委托手续\`
  ② 出具日期**留空**（不自动填今天）
  ③（律师函跟进另见 39b）

🔴 铁律（MEMORY.md）：**正式法律文书一律 .docx；一律复制既有模板改，禁自拟 HTML 重排**。
   故本模块只做：`复制模板 docx → 定点替换若干 <w:t> 文本节点 → 另存新文件`。
   **不做**：拼 XML / 自建版式 / 改抬头与落款。

模板真源：`D:\\案件\\张律师\\所函\\_代盖章（非经手案）\\4.所函.doc`
  ⚠️ 该文件是 **OLE2 老式 .doc**（二进制），Python 无法直接改；
  故本模块使用**预转换的 docx 模板**（`docs/所函模板.docx`，由 `convert_doc.py` 一次性生成并留档）。
  生成时**绝不改模板真源**。

替换口径（2026-09-17 实测模板文本节点，带前导/尾随空格）：
  节点①  `    福建省惠安县人民法院    `            → 致送法院
  节点②  `  周锦全、周高典、王彩丽、周科清、周科明  ` → 委托人
  节点③  ` 张律师律师   `                        → 承办律师（默认「张律师律师」）
  节点④  `  强制执行    `                        → 诉讼阶段
  节点⑤  `  骆纪川…孙庄土地经营权出租合同    `      → 对方当事人 + 案由（**同节点，需拆**）
  节点⑥  ` 年  月  日`                            → **保持留空**（用户拍板）
"""
import argparse
import config as _config
import hashlib
import io
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.abspath(__file__))

# 模板真源（可选，.doc 只读参考，从配置读；未配置则只用预转换的 docx）
TPL_DOC = _config.get('sohan.template_doc', '') or ''
DEFAULT_TPL = os.environ.get('SOHAN_TEMPLATE') or os.path.join(HERE, 'templates', '所函模板.docx')

STAGES = ['洽谈', '一审', '二审', '再审', '执行', '强制执行', '仲裁', '非诉', '其他']
DEFAULT_LAWYER = (_config.get('lawyer.name', '') or '') + '律师'

# 模板里「已填样例」的原始文本（用于识别 + 校验是否替换干净）
_TPL_SAMPLES = {
    'court': '某市某区人民法院',
    'client': '甲公司、乙公司',
    'lawyer': '张律师',
    'stage': '一审',
    'opponent_cause': '丙公司、丁公司买卖合同',
}


def template_path():
    return DEFAULT_TPL


def template_ok():
    """模板可用性：存在 + 是 docx（zip）+ 含 document.xml"""
    p = template_path()
    if not os.path.isfile(p):
        return False, '模板不存在：%s' % p
    try:
        with zipfile.ZipFile(p) as z:
            if 'word/document.xml' not in z.namelist():
                return False, '模板缺 word/document.xml'
    except (zipfile.BadZipFile, OSError) as e:
        return False, '模板不是有效 docx：%r' % (e,)
    return True, ''


def template_mode():
    """模板形态：'placeholder' = 含 {{变量}}（通用，推荐）；'node' = 固定文本节点（老模板）。
    开源版优先占位符模式；两种都支持，互不影响。"""
    p = template_path()
    if not os.path.isfile(p):
        return ''
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read('word/document.xml').decode('utf-8', 'replace')
    except (zipfile.BadZipFile, OSError, KeyError):
        return ''
    return 'placeholder' if '{{' in xml else 'node'


# ------------------------------------------------------------------ 文本节点读写

def _doc_xml(path):
    with zipfile.ZipFile(path) as z:
        return z.read('word/document.xml').decode('utf-8')


def text_nodes(xml, min_len=1):
    """取所有 <w:t> 文本（含前后空格），返回 [(完整标签, 文本)]"""
    out = []
    for m in re.finditer(r'(<w:t[^>]*>)(.*?)(</w:t>)', xml, re.S):
        t = m.group(2)
        if len(t.strip()) >= min_len:
            out.append((m.group(0), t))
    return out


def visible_lines(path):
    """给前端看的「结构预览」：按段落给文本"""
    xml = _doc_xml(path)
    lines = []
    for m in re.finditer(r'<w:p[ >].*?</w:p>', xml, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', m.group(0), re.S))
        if t.strip():
            lines.append(t)
    return lines


# ------------------------------------------------------------------ 计划

def _norm(s):
    return re.sub(r'\s+', '', str(s or ''))


def _sub_node(xml, old_text, new_text):
    """在 <w:t> 文本节点内做**子串替换**，保留节点内其余文字与全部样式。

    实情（2026-09-17 实测）：模板里法院那格是 `    福建省惠安县人民法院    法院：`
    —— 法院名与「法院：」在同一个 <w:t>；整节点精确匹配必然 miss。
    故改为：找到**包含 old_text 的第一个 <w:t>**，只替换其中该子串。

    返回 (新 xml, 是否命中, 原节点文本)
    """
    for tag, txt in text_nodes(xml):
        if old_text in txt:
            inner = re.sub(r'^<w:t[^>]*>', '', tag)
            inner = re.sub(r'</w:t>$', '', inner)
            open_tag = tag[:len(tag) - len(inner) - len('</w:t>')]
            new_inner = inner.replace(old_text, new_text, 1)
            return xml.replace(tag, '%s%s</w:t>' % (open_tag, new_inner), 1), True, txt
    return xml, False, '' 


def plan_sohan(court='', client='', lawyer='', stage='', opponent='', cause='', case_folder=''):
    """生成所函的**计划**（不落盘）。返回替换清单 + 目标路径 + 校验结果。"""
    ok, err = template_ok()
    if not ok:
        return {'blocked': err}
    # ---- 空值纪律（2026-09-17 吸收自 LawyerCaseTool 的 _prepare_context，只借思路）----
    # 原则：**绝不静默抹白**。空白会伪装成「已填但内容为空」，导致漏填出厂。
    #   · 必填为空 → blocked（拒绝生成，让人看见）
    #   · 选填为空 → 保留模板原文（不替换），同样让人看见
    miss = [k for k, v in (('致送法院', court), ('委托人', client)) if not str(v or '').strip()]
    if miss:
        return {'blocked': '缺必填：%s（不会生成空白件）' % ' / '.join(miss)}
    stage = str(stage or '').strip()
    if stage and stage not in STAGES:
        return {'blocked': '诉讼阶段需为：%s' % '/'.join(STAGES)}
    lawyer = str(lawyer or '').strip() or DEFAULT_LAWYER
    opponent = str(opponent or '').strip()
    cause = str(cause or '').strip()

    if template_mode() == 'placeholder':
        return _plan_by_placeholder(court, client, lawyer, stage, opponent, cause, case_folder)

    tpl = template_path()
    xml = _doc_xml(tpl)

    # 节点⑤ = 对方 + 案由 合在一个节点；重建为「对方 + 案由」拼接（保持原样式）
    opp_cause = ('%s%s' % (opponent, cause)).strip()
    repl = [
        ('致送法院', '    %s    ' % court, '    %s    ' % _TPL_SAMPLES['court']),
        ('委托人', '  %s  ' % client, '  %s  ' % _TPL_SAMPLES['client']),
        ('承办律师', ' %s   ' % lawyer, ' %s   ' % _TPL_SAMPLES['lawyer']),
        ('诉讼阶段', '  %s    ' % (stage or _TPL_SAMPLES['stage']),
         '  %s    ' % _TPL_SAMPLES['stage']),
        ('对方与案由', '  %s    ' % (opp_cause or _TPL_SAMPLES['opponent_cause']),
         '  %s    ' % _TPL_SAMPLES['opponent_cause']),
    ]

    steps, new_xml = [], xml
    for label, new_text, old_text in repl:
        new_xml, hit, node_txt = _sub_node(new_xml, old_text, new_text)
        kept = new_text.strip() == old_text.strip()
        steps.append({'字段': label, '改前': old_text.strip(), '改后': new_text.strip(),
                      '命中': hit, '节点原文': node_txt.strip(),
                      '保留原文': kept})          # True = 本次没动（空值保留，非抹白）

    # 校验：替换后不得再出现**未被本次覆盖**的模板样例原文。
    # ⚠️ 新旧同值不算残留（默认承办律师就是模板样例）——见批 39a 修正 2。
    covered = {k for k, _n, _o in repl}          # 本次真正处理过的字段（中文标签）
    key_of = {'致送法院': 'court', '委托人': 'client', '承办律师': 'lawyer',
              '诉讼阶段': 'stage', '对方与案由': 'opponent_cause'}
    done = {key_of[k] for k in covered if k in key_of}
    leftover = []
    for k, s in _TPL_SAMPLES.items():
        if k in done:
            continue
        if _norm(s) and _norm(s) in _norm(new_xml):
            leftover.append('%s=%s' % (k, s))
    if not all(x['命中'] for x in steps):
        return {'blocked': '模板节点未全部命中（模板可能已变）：%s'
                           % '、'.join(x['字段'] for x in steps if not x['命中']),
                'steps': steps}

    # 目标路径
    folder = str(case_folder or '').strip()
    if folder and not os.path.isdir(folder):
        return {'blocked': '案卷夹不存在：%s' % folder, 'steps': steps}
    # 不指定案卷时：落「所函」业务目录下的 _生成\<日期>\（**不落模板所在的 _代盖章 子夹**）
    if folder:
        dst_dir = os.path.join(folder, '01-委托手续')
    else:
        biz = os.environ.get('CASE_OPS_BIZ_ROOT') or _config.path('paths.biz_root')
        dst_dir = os.path.join(biz, '所函', '_生成', datetime.now().strftime('%Y%m%d'))
    who = _safe_fn(str(opponent or client).split('、')[0])[:20]
    fname = '所函-%s%s.docx' % (who, ('-'+cause) if cause else '')
    dst = os.path.join(dst_dir, _free_name(fname, [dst_dir]))

    return {'template': tpl, 'template_src_doc': TPL_DOC, 'steps': steps,
            'leftover': leftover, 'dst': dst, 'dst_dir': dst_dir,
            'case_folder': folder, 'xml': new_xml,
            'values': {'court': court, 'client': client, 'lawyer': lawyer,
                       'stage': stage, 'opponent': opponent, 'cause': cause},
            'date_blank': True}


def _safe_fn(name):
    return re.sub(r'[\\/:*?"<>|]', '_', str(name or '')).strip()


def _free_name(name, dirs):
    """同名避让：xxx.docx → xxx-2.docx"""
    for d in dirs:
        if not os.path.isdir(d):
            continue
        if not os.path.isfile(os.path.join(d, name)):
            return name
    stem, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = '%s-%d%s' % (stem, i, ext)
        if all(not os.path.isfile(os.path.join(d, cand)) for d in dirs if os.path.isdir(d)):
            return cand
        i += 1


def _dst_of(opponent, client, cause, case_folder):
    """目标路径（两种模板模式共用）。"""
    folder = str(case_folder or '').strip()
    if folder and not os.path.isdir(folder):
        return None, None, '案卷夹不存在：%s' % folder
    if folder:
        dst_dir = os.path.join(folder, '01-委托手续')
    else:
        biz = os.environ.get('CASE_OPS_BIZ_ROOT') or _config.path('paths.biz_root')
        dst_dir = os.path.join(biz, '所函', '_生成', datetime.now().strftime('%Y%m%d'))
    who = _safe_fn(str(opponent or client).split('、')[0])[:20]
    fname = '所函-%s%s.docx' % (who, ('-'+cause) if cause else '')
    return dst_dir, os.path.join(dst_dir, _free_name(fname, [dst_dir])), ''


def _plan_by_placeholder(court, client, lawyer, stage, opponent, cause, case_folder):
    """占位符模板（{{变量}}）的计划：只算值，不碰 XML。"""
    dst_dir, dst, err = _dst_of(opponent, client, cause, case_folder)
    if err:
        return {'blocked': err, 'mode': 'placeholder'}
    # 空值保留占位符：传回 '{{ key }}' 字面量，渲染后仍能一眼看见「这里没填」
    def keep(k, v):
        v = str(v or '').strip()
        return v if v else '{{ %s }}' % k

    values = {
        'court': court, 'client': client, 'lawyer': lawyer,
        'stage': keep('stage', stage), 'opponent': keep('opponent', opponent),
        'cause': keep('cause', cause),
        'firm': _config.get('lawyer.firm', '') or '{{ firm }}',
        'body': '{{ body }}', 'days': '{{ days }}',
        'date': '  年  月  日',              # 日期留空，手填
    }
    steps = [
        {'字段': '致送法院', '改前': '{{ court }}', '改后': court, '命中': True, '保留原文': False},
        {'字段': '委托人', '改前': '{{ client }}', '改后': client, '命中': True, '保留原文': False},
        {'字段': '承办律师', '改前': '{{ lawyer }}', '改后': lawyer, '命中': True, '保留原文': False},
        {'字段': '诉讼阶段', '改前': '{{ stage }}', '改后': values['stage'],
         '命中': True, '保留原文': values['stage'].startswith('{{')},
        {'字段': '对方与案由', '改前': '{{ opponent }} / {{ cause }}',
         '改后': '%s %s' % (values['opponent'], values['cause']),
         '命中': True, '保留原文': values['opponent'].startswith('{{')},
    ]
    # 预览用 xml：把日期占位换成留空样式，前端与冒烟据此判断「日期未被自动填写」
    try:
        _xml = _doc_xml(template_path())
        for _pat in ('{{ date }}', '{{date}}'):
            _xml = _xml.replace(_pat, values['date'])
    except (zipfile.BadZipFile, OSError, KeyError):
        _xml = ''
    return {'mode': 'placeholder', 'template': template_path(), 'template_src_doc': TPL_DOC,
            'dst': dst, 'dst_dir': dst_dir, 'case_folder': str(case_folder or ''),
            'values': values, 'steps': steps, 'leftover': [], 'xml': _xml,
            'date_blank': True}


def apply_sohan(plan):
    """按计划落盘（占位符模板走 docxtpl；固定节点模板走 zip 文本替换）。
    **模板与案卷既有文件不动。**"""
    if plan.get('blocked'):
        raise ValueError(plan['blocked'])
    tpl, dst = plan['template'], plan['dst']
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if plan.get('mode') == 'placeholder':
        from docxtpl import DocxTemplate
        t = DocxTemplate(tpl)
        t.render(plan['values'])
        t.save(dst)
        return {'dst': dst, 'bytes': os.path.getsize(dst), 'mode': 'placeholder'}
    new_xml = plan['xml'].encode('utf-8')
    with zipfile.ZipFile(tpl) as zin:
        items = zin.infolist()
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
            for it in items:
                data = zin.read(it.filename)
                if it.filename == 'word/document.xml':
                    data = new_xml
                zout.writestr(it, data)
    return {'dst': dst, 'bytes': os.path.getsize(dst)}


# ------------------------------------------------------------------ 自测

def selftest():
    import tempfile
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    ok, err = template_ok()
    chk('模板可用（docx + document.xml）', ok), None if ok else print('   原因:', err)
    if not ok:
        print('--- 模板缺失，跳过后半 ---')
        print('--- selftest %d 项，失败 %d ---' % (len(total), len(fails)))
        return 1 if fails else 0

    xml = _doc_xml(template_path())
    nodes = text_nodes(xml)
    texts = [t for _tag, t in nodes]
    chk('模板含 5 个可填文本节点', all(any(_norm(s) in _norm(t) for t in texts)
                                   for s in _TPL_SAMPLES.values()))
    p = plan_sohan(court='厦门市思明区人民法院', client='张三、李四',
                   stage='一审', opponent='王五', cause='买卖合同纠纷',
                   case_folder=tempfile.mkdtemp(prefix='sohan_case_'))
    chk('plan 无 blocked', not p.get('blocked'))
    chk('plan 5 步全部命中', all(x['命中'] for x in p['steps']))
    chk('plan 左无残留样例', not p['leftover'])
    chk('plan 目标在 01-委托手续', '01-委托手续' in p['dst'])
    chk('plan 日期留空（未填今天）', p.get('date_blank') is True
        and '年  月  日' in p['xml'])
    chk('plan 新值已入 xml', '厦门市思明区人民法院' in p['xml'] and '张三、李四' in p['xml'])
    chk('plan 旧值已清', '周锦全' not in p['xml'] and '骆纪川' not in p['xml'])
    chk('缺必填被拦', bool(plan_sohan(court='', client='')['blocked']))
    chk('非法阶段被拦', bool(plan_sohan(court='X法院', client='张三', stage='乱写')['blocked']))
    chk('案卷不存在被拦', bool(plan_sohan(court='X法院', client='张三',
                                        case_folder='Z:/绝对不存在')['blocked']))
    # 真落盘
    r = apply_sohan(p)
    chk('落盘成功且文件存在', os.path.isfile(r['dst']))
    with zipfile.ZipFile(r['dst']) as z:
        nx = z.read('word/document.xml').decode('utf-8')
        chk('落盘件含新值', '厦门市思明区人民法院' in nx)
        chk('落盘件无旧值', '周锦全' not in nx and '骆纪川' not in nx)
        chk('落盘件保留抬头', '某某律师事务所' in nx)
        chk('落盘件 zip 结构完整', len(z.namelist()) == 17)
    chk('模板真源未被改（.doc 仍在）', os.path.isfile(TPL_DOC))
    chk('同名避让生效', _free_name(os.path.basename(r['dst']),
                                 [os.path.dirname(r['dst'])]) != os.path.basename(r['dst']))

    print('--- selftest 全部通过（%d 项，失败 %d）---' % (len(total), len(fails))
          if not fails else '--- FAIL %d / %d：%s ---' % (len(fails), len(total), fails))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description='所函生成（复制模板 + 定点替换，日期留空）')
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('plan')
    p.add_argument('--court', default=''); p.add_argument('--client', default='')
    p.add_argument('--lawyer', default=''); p.add_argument('--stage', default='')
    p.add_argument('--opponent', default=''); p.add_argument('--cause', default='')
    p.add_argument('--case', default='')
    p = sub.add_parser('make')
    for a in ('--court', '--client', '--lawyer', '--stage', '--opponent', '--cause', '--case'):
        p.add_argument(a, default='')
    p.add_argument('--apply', action='store_true')
    sub.add_parser('template')
    sub.add_parser('selftest')
    args = ap.parse_args()

    if args.cmd == 'template':
        ok, err = template_ok()
        print('模板: %s' % template_path())
        print('状态: %s %s' % ('OK' if ok else 'FAIL', err))
        print('真源 .doc: %s（存在=%s）' % (TPL_DOC, os.path.isfile(TPL_DOC)))
        if ok:
            for i, l in enumerate(visible_lines(template_path())):
                print('  %2d| %s' % (i, l))
        return 0 if ok else 1
    if args.cmd in ('plan', 'make'):
        pl = plan_sohan(args.court, args.client, args.lawyer, args.stage,
                        args.opponent, args.cause, args.case)
        if pl.get('blocked'):
            print('不能生成：%s' % pl['blocked'])
            return 2
        for s in pl['steps']:
            print('  [%s] %s → %s' % ('命中' if s['命中'] else '未命中', s['改前'], s['改后']))
        print('日期行: 留空（按用户 09-17 拍板）')
        print('目标: %s' % pl['dst'])
        if args.cmd == 'plan' or not args.apply:
            print('（dry-run，未写入；加 --apply 落盘）')
            return 0
        r = apply_sohan(pl)
        print('已生成: %s (%d 字节)' % (r['dst'], r['bytes']))
        return 0
    if args.cmd == 'selftest':
        return selftest()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
