#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""wiki_ops.py — 共享知识库（Obsidian vault）写入小工具（法律工作台 · 批 16 / 批 28）

定位：工作台里唯一允许**写知识库**的入口，做两件事——
  ① **新建案件实体页骨架**（`plan_case_entity`/`write_case_entity`）
  ② **结案标记**（`plan_mark_closed`/`apply_mark_closed`，批 28）：只做**锚点增量改**（frontmatter
     status/tags/updated + 标题下标记行 + 文末「结案归档」小节），**不重写整页**、幂等、写前备份
     （落 `<知识库备份目录>`，可用 `backup_dir` 覆盖）、写后复读校验、校验不过自动回滚
纪律（写死在代码里）：
  - **新建只新建、绝不覆盖**：目标页已存在（含同名文件）→ 直接拒绝并回原路径
  - **结案标记只认精确页名**：找不到页 → 不猜、不新建，回报警告；页面内容与预览不一致 → 拒绝覆盖
  - **不编造**：骨架只写表单已知信息（编号/委托人/对方/案由/类别/案卷路径）；未知项一律 `[待补]`
  - 写前检查 vault 的 `git status`：有未提交改动**只提示**（不阻断，也不代提交）
  - 只写 `wiki/entities/` 下的单个 `.md`；**不动 raw/**、不做删除；**新建页不覆盖既有页**，
    结案标记只对**指名的那一页**做锚点增量改（写前备份 + 写后校验）
  - `WIKI_ROOT` 环境变量可覆盖知识库根（测试隔离用）

用法：
  from wiki_ops import plan_case_entity, write_case_entity, plan_mark_closed, vault_status
  python wiki_ops.py --selftest                 # 自测（临时目录，不碰真知识库）
  python wiki_ops.py --title "甲诉乙买卖合同纠纷案"   # 查同页是否已存在（只读）
  python wiki_ops.py --list-pages                       # 列案件页（只读）
  python wiki_ops.py --close "厦门锦晟达诉李雅玲劳动争议仲裁案" [--apply]
"""
import argparse
import io
import os
import re
import subprocess
import sys
from datetime import datetime

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VERSION = '1.1.0'
DEFAULT_WIKI = r'<知识库目录>'
ENTITY_SUBDIR = 'entities'
BAD_FN = re.compile(r'[\\/:*?"<>|\n\r\t]')


def wiki_root():
    return os.environ.get('WIKI_ROOT') or DEFAULT_WIKI


def entities_dir():
    return os.path.join(wiki_root(), ENTITY_SUBDIR)


def safe_filename(title):
    """标题 → 安全文件名（去非法字符、压空格、截断 80 字）"""
    s = BAD_FN.sub('', str(title or '').strip())
    s = re.sub(r'\s+', ' ', s)
    return s[:80] or '未命名'


def find_case_page(title):
    """按文件名/标题找已存在的案件页（只读）→ 路径或 None"""
    d = entities_dir()
    if not os.path.isdir(d):
        return None
    want = safe_filename(title)
    for fn in os.listdir(d):
        if not fn.endswith('.md'):
            continue
        if fn[:-3] == want:
            return os.path.join(d, fn)
        # 次一级：文件里的 title 一致
        try:
            head = io.open(os.path.join(d, fn), encoding='utf-8', errors='replace').read(600)
        except OSError:
            continue
        m = re.search(r'^title:\s*(.+)$', head, re.M)
        if m and m.group(1).strip().strip('"\'') == want:
            return os.path.join(d, fn)
    return None


def vault_status():
    """知识库 git 状态（只读）：未提交条数 / 是否可用不判断。失败不抛。"""
    root = wiki_root()
    out = {'root': root, 'exists': os.path.isdir(root), 'dirty': None, 'detail': ''}
    if not out['exists']:
        out['detail'] = '知识库根不存在（是不是路径变了？）'
        return out
    env = dict(os.environ)
    env.setdefault('GIT_DIR', '')
    # 用 vault 根上一级（git 仓库通常在 vault 目录自身；两条路都试）
    for repo in (root, os.path.dirname(root)):
        try:
            r = subprocess.run(['git', '-C', repo, 'status', '--porcelain'],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                lines = [x for x in (r.stdout or b'').decode('utf-8', 'replace').splitlines() if x.strip()]
                out['dirty'] = len(lines)
                out['detail'] = ('有 %d 处未提交改动（写完请自行提交/回滚）' % len(lines)) if lines else '工作区干净'
                break
            out['detail'] = 'git 不可用或该目录不是仓库（%s）' % repo
        except Exception as e:                                # noqa: BLE001
            out['detail'] = 'git 调用失败：%r' % (e,)
    return out


def case_page_title(client, opponent, cause):
    """页面标题（只由表单字段拼，不推断）：'X诉Y案由案'"""
    c = str(client or '').strip()
    o = str(opponent or '').strip()
    cause = str(cause or '').strip()
    if cause.endswith('案'):
        cause = cause[:-1]
    return '%s诉%s%s案' % (c, o, cause)


def plan_case_entity(case_no, client, opponent, cause, case_type='诉讼',
                     folder='', case_root='', extra=None):
    """产出案件实体页骨架方案（不落盘）：目标路径 + 全文 + 是否已存在"""
    title = case_page_title(client, opponent, cause)
    fn = safe_filename(title) + '.md'
    dst = os.path.join(entities_dir(), fn)
    exists = find_case_page(title)
    extra = extra or {}
    today = datetime.now().strftime('%Y-%m-%d')
    tags = ['案件', '在办']
    if case_type:
        tags.append(case_type)
    path_hint = os.path.join(case_root, folder) if (case_root and folder) else (folder or '[待补]')
    lines = [
        '---',
        'title: %s' % (title or '[待补]'),
        'type: entity',
        'created: %s' % today,
        'updated: %s' % today,
        'tags: [%s]' % ', '.join(tags),
        'status: 在办',
        '---',
        '',
        '# %s' % (title or '[待补]'),
        '',
        '> 案卷目录：`%s`' % path_hint,
        '> 所内编号：%s ｜ 案件类别：%s' % (case_no or '[待补]', case_type or '[待补]'),
        '> ⚠️ 本页由法律工作台按「开案表单」生成骨架，**只写了表单已知信息**；其余标 `[待补]`，'
        '请勿把未核实的案号/金额/当事人写进来。',
        '',
        '## 基本档案',
        '',
        '| 项目 | 内容 |',
        '|------|------|',
        '| 我方委托人 | %s |' % (client or '[待补]'),
        '| 对方 | %s |' % (opponent or '[待补]'),
        '| 案由 | %s |' % (cause or '[待补]'),
        '| 案件类别 | %s |' % (case_type or '[待补]'),
        '| 管辖法院/仲裁 | %s |' % (extra.get('court') or '[待补]'),
        '| 案号 | %s |' % (extra.get('case_no_court') or '[待补]'),
        '| 代理人 | %s |' % (extra.get('lawyer') or '[待补]'),
        '| 台账行 | %s |' % (extra.get('ledger_hint') or '待台账登记后回填'),
        '',
        '## 当事人',
        '',
        '| 角色 | 名称 | 备注 |',
        '|------|------|------|',
        '| 委托人（我方） | %s | [待补] 统一社会信用代码/身份证号 |' % (client or '[待补]'),
        '| 对方 | %s | [待补] 主体信息 |' % (opponent or '[待补]'),
        '',
        '## 案件事实',
        '',
        '- [待补]（建议按时间顺序写，每条注明证据来源；金额/案号零容忍，未核实一律标 `[待核实]`）',
        '',
        '## 诉讼/仲裁请求',
        '',
        '- [待补]',
        '',
        '## 程序进展',
        '',
        '| 时间 | 事项 | 来源 |',
        '|------|------|------|',
        '| %s | 立卷（所内编号 %s） | 法律工作台「立卷」 |' % (today, case_no or '[待补]'),
        '',
        '## 过程性文件台账',
        '',
        '| 文件 | 性质 | 位置 | 打印状态 |',
        '|------|------|------|---------|',
        '|（凡对话/办案中产生的过程性材料都登记在这里）| — | — | 待打印 |',
        '',
        '## 待办',
        '',
        '- [ ] 补全案号与管辖法院',
        '- [ ] 新材料先放 `00-收件箱`，再用工作台「收件箱入库」归类',
        '- [ ] 台账行登记（进展更新走「台账进展」页，守 I→J 纪律）',
        '',
        '## 相关',
        '',
        '- 案卷：`%s`' % path_hint,
    ]
    return {'title': title, 'dst': dst, 'content': '\n'.join(lines) + '\n',
            'exists': exists, 'will_write': exists is None,
            'wiki_root': wiki_root(), 'entities_dir': entities_dir()}


def write_case_entity(plan):
    """落盘（只新建）。返回写入路径；已存在同名页 → 抛 FileExistsError"""
    if plan.get('exists'):
        raise FileExistsError('同名案件页已存在，未覆盖：%s' % plan['exists'])
    if os.path.exists(plan['dst']):
        raise FileExistsError('目标文件已存在，未覆盖：%s' % plan['dst'])
    os.makedirs(plan['entities_dir'], exist_ok=True)
    io.open(plan['dst'], 'w', encoding='utf-8', newline='\n').write(plan['content'])
    return plan['dst']


# ------------------------------------------------- 结案标记（批 28，受控增量改）

CLOSE_MARK = '> ✅ **已结案'
CLOSE_SECTION = '## 结案归档'
CASE_PAGE_HINT = re.compile(r'诉|案')

# —— 「案件 → 知识库页」匹配（批 28）：知识库页标题口径是「X诉Y案由案」，与案卷夹名/台账案件名
# 都不同，复用 case_ops 的台账匹配器只能拿 2 分（不够门槛）→ 这里用**主体词命中**的可解释口径：
# 两边各切出主体词，命中（相等 / 一方含另一方且较短者 ≥3 字）即算一个，**≥2 个且唯一最高**才认定为同一案。
SUGGEST_SPLIT = re.compile(r'(?:VS|vs|Vs|诉|、|，|,|（|）|\(|\)|《|》|\s|—|-|_|/)+')
GENERIC_TOKENS = ('公司', '有限公司', '有限责任公司', '股份', '集团', '等', '案', '系列',
                  '纠纷', '合同', '买卖合同纠纷', '责任', '材料', '执行文件夹')


def _subject_tokens(s):
    """切成「主体词」（≥3 字、去掉通用词与尾缀）——只用于辅助认页，不做法律判断"""
    out = []
    for t in SUGGEST_SPLIT.split(str(s or '')):
        t = str(t).strip().strip('等案 ')
        while t and t[-1] in '等案':
            t = t[:-1]
        if len(t) >= 3 and t not in GENERIC_TOKENS and not t.isdigit():
            out.append(t)
    return out


def suggest_case_page(keys, pages=None):
    """按主体词命中给出「最像同一案」的知识库页（**只给建议，不替人拍板**）。

    keys：案件线索串（案卷夹名 / 台账案件名，可多个，取最高分）
    返回 {'suggest': 页标题或 None, 'score': int, 'why': [...], 'candidates': [...]}
    """
    pages = pages if pages is not None else list_case_pages()
    titles = [p['title'] for p in pages]
    key_toks = []
    for k in (keys if isinstance(keys, (list, tuple)) else [keys]):
        for t in _subject_tokens(k):
            if t not in key_toks:
                key_toks.append(t)
    cands = []
    for title in titles:
        toks = _subject_tokens(title)
        hits = []
        for a in key_toks:
            for b in toks:
                if a == b or (a in b or b in a) and min(len(a), len(b)) >= 3:
                    if (a, b) not in hits:
                        hits.append((a, b))
        if hits:
            why = ['主体词命中「%s」' % a for a, _b in hits[:4]]
            cands.append({'title': title, 'score': len(hits), 'why': why})
    cands.sort(key=lambda x: -x['score'])
    out = {'suggest': None, 'score': 0, 'why': [], 'candidates': cands[:5], 'keys': key_toks}
    if cands and cands[0]['score'] >= 2:
        nxt = cands[1]['score'] if len(cands) > 1 else 0
        if cands[0]['score'] > nxt:
            out['suggest'] = cands[0]['title']
            out['score'] = cands[0]['score']
            out['why'] = cands[0]['why']
        else:
            out['why'] = ['并列最高分（%d）→ 交人工选，不自动认定' % cands[0]['score']]
    return out


def list_case_pages():
    """知识库 entities 里的「案件页」清单（只读）：文件名含「诉/案」的才算。"""
    d = entities_dir()
    out = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.md') or not CASE_PAGE_HINT.search(fn[:-3]):
            continue
        p = os.path.join(d, fn)
        try:
            src = io.open(p, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        out.append({'title': fn[:-3], 'path': p,
                    'closed': (CLOSE_MARK in src or CLOSE_SECTION in src),
                    'status': (re.search(r'^status:\s*(.*)$', src, re.M) or [None, ''])[1]
                              if re.search(r'^status:\s*(.*)$', src, re.M) else '',
                    'mtime': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d')})
    return out


def plan_mark_closed(title, closed_date=None, folder='', note=''):
    """把案件页标为「已结案」（**只做增量改，不重写整页**；dry-run 优先）。

    改动点（逐个只在该锚点存在时执行）：
      ① frontmatter `updated:` → 今天
      ② frontmatter `status: 在办` → `已结案`（没有 status 行则在 updated 后补一行）
      ③ frontmatter tags 里 `在办案件`/`在办` → `已结案`；都没有则追加一个
      ④ H1 标题下插一行 `> ✅ **已结案（日期）** …`
      ⑤ 文末追加 `## 结案归档` 小节
    幂等：已有标记 → `already=True`，不产生任何改动。
    """
    p = find_case_page(title)
    out = {'found': bool(p), 'title': title, 'path': p, 'already': False,
           'changes': [], 'warnings': [], 'before': '', 'after': '', 'will_write': False}
    if not p:
        out['warnings'].append('知识库里没找到这一页（按文件名/标题精确找，不做模糊猜）')
        return out
    src = io.open(p, encoding='utf-8', errors='replace').read()
    out['before'] = src
    if CLOSE_MARK in src or CLOSE_SECTION in src:
        out['already'] = True
        out['after'] = src
        out['changes'].append('本页已标过「已结案」→ 本次不改（幂等）')
        return out
    m = re.match(r'^(---\r?\n)(.*?)(\r?\n---\r?\n)', src, re.S)
    if not m:
        out['warnings'].append('本页没有标准 frontmatter，为安全起见不改（请手工处理）')
        return out
    d = closed_date or datetime.now().strftime('%Y-%m-%d')
    nl = '\r\n' if '\r\n' in src.split('\n', 1)[0] + '\n' else '\n'
    fm, body = m.group(2), src[m.end():]
    # ① updated
    fm2, n = re.subn(r'^updated:\s*.*$', 'updated: %s' % d, fm, count=1, flags=re.M)
    if n:
        fm = fm2
        out['changes'].append('frontmatter updated → %s' % d)
    # ② status
    fm2, n = re.subn(r'^status:\s*在办\s*$', 'status: 已结案', fm, count=1, flags=re.M)
    if n:
        fm = fm2
        out['changes'].append('frontmatter status：在办 → 已结案')
    elif re.search(r'^updated:', fm, re.M):
        fm = re.sub(r'^(updated:.*)$', lambda mm: mm.group(1) + nl + 'status: 已结案',
                    fm, count=1, flags=re.M)
        out['changes'].append('frontmatter 补 status: 已结案')
    # ③ tags
    if '在办案件' in fm:
        fm = fm.replace('在办案件', '已结案')
        out['changes'].append('frontmatter tags：在办案件 → 已结案')
    elif re.search(r'在办', fm):
        fm = re.sub(r'在办', '已结案', fm)
        out['changes'].append('frontmatter tags：在办 → 已结案')
    elif '已结案' not in fm:
        def _add_tag(mm):
            line = mm.group(0).rstrip()
            if line.endswith(']'):
                return line[:-1].rstrip().rstrip(',') + ', 已结案]'
            return line + ' #已结案'
        fm2, n = re.subn(r'^tags:.*$', _add_tag, fm, count=1, flags=re.M)
        if n:
            fm = fm2
            out['changes'].append('frontmatter tags 追加「已结案」')
    # ④ H1 下插标记行
    callout = '%s（%s）** — 案卷已归档%s；本页转知识沉淀，进展不再更新。' % (
        CLOSE_MARK, d, ('（`%s`）' % folder) if folder else '')
    body2, n = re.subn(r'^(#[^\n]*\r?\n)', lambda mm: mm.group(1) + nl + callout + nl,
                       body, count=1, flags=re.M)
    if n:
        body = body2
        out['changes'].append('标题下加「已结案」标记行')
    else:
        out['warnings'].append('没找到 H1 标题行 → 未能插入标记行')
    # ⑤ 文末追加小节
    sec = [nl, CLOSE_SECTION, '',
           '- 结案日期：%s' % d,
           '- 案卷位置：`%s`' % (folder or '[待补]'),
           '- 台账处置：整行已移入台账「已结案」页（法律工作台「归档与导出」）',
           '- 备注：%s' % (note or '—'), '']
    body = body.rstrip('\r\n') + nl + nl.join(sec)
    out['after'] = m.group(1) + fm + m.group(3) + body
    out['will_write'] = out['after'] != src and not out['warnings']
    if out['warnings']:
        out['after'] = src
    return out


def apply_mark_closed(plan, backup_dir=None):
    """落盘（受控）：先备份原文件 → 写 → 复读校验。已标过则不写。"""
    if not plan.get('found'):
        raise FileNotFoundError('知识库里没找到这一页：%s' % plan.get('title'))
    if plan.get('already'):
        return {'path': plan['path'], 'changed': False, 'backup': None,
                'note': '已标过「已结案」，本次未改'}
    if not plan.get('will_write'):
        raise ValueError('方案不可写（有警告或无改动）：%s' % '；'.join(plan.get('warnings') or []))
    src = io.open(plan['path'], encoding='utf-8', errors='replace').read()
    if src != plan['before']:
        raise RuntimeError('页面内容已变（与预览时不一致），拒绝覆盖：%s' % plan['path'])
    bak = None
    bd = backup_dir or os.environ.get('WIKI_BACKUP_DIR') or r'<知识库备份目录>'
    try:
        os.makedirs(bd, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        bak = os.path.join(bd, '%s-before-结案标记-%s.md' % (safe_filename(plan['title']), stamp))
        io.open(bak, 'w', encoding='utf-8', newline='').write(src)
    except OSError as e:                                      # noqa: BLE001
        raise RuntimeError('备份失败，已中止（未改页面）：%r' % (e,))
    io.open(plan['path'], 'w', encoding='utf-8', newline='').write(plan['after'])
    after = io.open(plan['path'], encoding='utf-8', errors='replace').read()
    verified = (CLOSE_MARK in after) and (CLOSE_SECTION in after) and (after == plan['after'])
    if not verified:                                          # 留档已写，尽力回滚
        try:
            io.open(plan['path'], 'w', encoding='utf-8', newline='').write(src)
        except OSError:
            pass
        raise RuntimeError('写后校验不通过，已回滚（原文件备份：%s）' % bak)
    return {'path': plan['path'], 'changed': True, 'backup': bak, 'verified': True}


# ---------------------------------------------------------------- CLI / 自测

def selftest():
    import shutil
    import tempfile
    fails = []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='wiki_selftest_')
    os.environ['WIKI_ROOT'] = tmp
    try:
        pl = plan_case_entity('民-A-26999', '甲公司', '乙公司', '买卖合同纠纷', '诉讼',
                              '民-A-26999_甲公司VS乙公司_买卖合同纠纷', r'D:\案件\x',
                              {'court': '思明区法院'})
        chk('标题按「X诉Y案由案」拼', pl['title'] == '甲公司诉乙公司买卖合同纠纷案')
        chk('落点在 entities/', pl['dst'].replace('\\', '/').endswith('/entities/甲公司诉乙公司买卖合同纠纷案.md'))
        chk('dry-run 不落盘', not os.path.exists(pl['dst']))
        chk('骨架含 frontmatter 与 status', 'type: entity' in pl['content']
            and 'status: 在办' in pl['content'])
        chk('只写表单已知信息，其余 [待补]', '思明区法院' in pl['content']
            and pl['content'].count('[待补]') >= 5)
        chk('零容忍提示在页内', '零容忍' in pl['content'])
        chk('含材料存放指引（00-收件箱）', '00-收件箱' in pl['content'])
        p = write_case_entity(pl)
        chk('已写入', os.path.isfile(p))
        pl2 = plan_case_entity('民-A-26999', '甲公司', '乙公司', '买卖合同纠纷', '诉讼')
        chk('同名页再建被识别为已存在', pl2['exists'] == p and pl2['will_write'] is False)
        try:
            write_case_entity(pl2)
            chk('已存在时拒绝覆盖', False)
        except FileExistsError:
            chk('已存在时拒绝覆盖', True)
        chk('原文件内容未被改动', io.open(p, encoding='utf-8').read() == pl['content'])
        # 非法字符清洗
        bad = plan_case_entity('x', '甲:公司', '乙/公司', '纠纷*')
        chk('标题里的非法字符被清掉', not BAD_FN.search(os.path.basename(bad['dst'])))
        chk('safe_filename 截断', len(safe_filename('长' * 200)) == 80)
        st = vault_status()
        chk('vault_status 可返回（临时目录不算仓库）', 'exists' in st and 'dirty' in st)

        # ---- 批 28：结案标记（受控增量改）----
        chk('案件页清单只列含「诉/案」的页', [x['title'] for x in list_case_pages()]
            == ['甲公司诉乙公司买卖合同纠纷案'])
        ck = plan_mark_closed('甲公司诉乙公司买卖合同纠纷案', '2026-09-13',
                              '民-A-26999_甲公司VS乙公司_买卖合同纠纷')
        chk('结案标记：找到页且可写', ck['found'] and ck['will_write'] and not ck['warnings'])
        chk('结案标记：frontmatter status 已改',
            'status: 已结案' in ck['after'] and 'status: 在办' not in ck['after'])
        chk('结案标记：tags 里 在办 → 已结案',
            ', 已结案,' in ck['after'] and '在办' not in ck['after'])
        chk('结案标记：标题下插了标记行 + 文末加小节',
            CLOSE_MARK in ck['after'] and CLOSE_SECTION in ck['after']
            and '民-A-26999_甲公司VS乙公司_买卖合同纠纷' in ck['after'])
        chk('结案标记：updated 改成给定日期', 'updated: 2026-09-13' in ck['after'])
        chk('dry-run 未落盘', io.open(p, encoding='utf-8').read() == pl['content'])
        bk = os.path.join(tmp, '_bak')
        r = apply_mark_closed(ck, backup_dir=bk)
        chk('结案标记已落盘且校验通过', r['changed'] and r['verified']
            and os.path.isfile(r['backup']) and os.path.basename(r['backup']).endswith('.md'))
        chk('备份内容 = 改前原文', io.open(r['backup'], encoding='utf-8').read() == ck['before'])
        ck2 = plan_mark_closed('甲公司诉乙公司买卖合同纠纷案')
        chk('幂等：再跑一次判为已标记', ck2['already'] and not ck2['will_write'])
        r2 = apply_mark_closed(ck2, backup_dir=bk)
        chk('幂等：apply 不改文件', r2['changed'] is False)
        chk('正文其余内容未被破坏（原骨架仍在）',
            '## 过程性文件台账' in io.open(r['path'], encoding='utf-8').read())
        nf = plan_mark_closed('不存在的案子诉某人案')
        chk('找不到页时不写、给警告', (not nf['found']) and (not nf['will_write']) and nf['warnings'])
        try:
            apply_mark_closed(nf, backup_dir=bk)
            chk('找不到页时 apply 抛错', False)
        except FileNotFoundError:
            chk('找不到页时 apply 抛错', True)

        # ---- 批 28：案件 → 知识库页 建议匹配（只给建议、认不出明说）----
        pgs = [{'title': t} for t in (
            '福州委托人诉当事人甲等买卖合同纠纷案', '福州委托人诉当事人乙等买卖合同纠纷案',
            '福建委托人诉当事人丙等买卖合同纠纷案', '漳州佳联化工诉联胜华阳盛月乔买卖合同纠纷案',
            '陈丽萍执行异议案（民-A-26010）')]
        s1 = suggest_case_page(['福州委托人vs当事人甲、吴群华、许明华、吴伟腾-买卖合同纠纷'], pgs)
        chk('建议匹配：台账名 ↔ 知识库页', s1['suggest'] == '福州委托人诉当事人甲等买卖合同纠纷案'
            and s1['score'] >= 2 and s1['why'])
        chk('建议匹配：简短主体名也能认（含被包含）',
            suggest_case_page(['福建委托人饲料诉当事人丙、王惠华等-买卖合同纠纷'], pgs)['suggest']
            == '福建委托人诉当事人丙等买卖合同纠纷案')
        chk('建议匹配：案卷夹名里的主体词能认',
            suggest_case_page(['执-24585_漳州佳联化工VS漳州联胜超纤、盛月乔_买卖合同纠纷'], pgs)['suggest']
            == '漳州佳联化工诉联胜华阳盛月乔买卖合同纠纷案')
        chk('建议匹配：执行异议页能认',
            suggest_case_page(['陈丽萍vs李喜前、王署心-执行异议'], pgs)['suggest']
            == '陈丽萍执行异议案（民-A-26010）')
        chk('建议匹配：唯一最高才认定（同类不同案不误配）',
            suggest_case_page(['福州委托人vs当事人乙、黄铸祥等'], pgs)['suggest']
            == '福州委托人诉当事人乙等买卖合同纠纷案')
        s5 = suggest_case_page(['广州某某公司VS某某某_买卖合同纠纷'], pgs)
        chk('建议匹配：认不出就明说（不猜）', s5['suggest'] is None and not s5['candidates'])
    finally:
        os.environ.pop('WIKI_ROOT', None)
        shutil.rmtree(tmp, ignore_errors=True)
    print('--- wiki_ops selftest %s（失败 %d）---' % ('全部通过' if not fails else '有失败', len(fails)))
    return 0 if not fails else 3


def main():
    ap = argparse.ArgumentParser(description='知识库写入小工具 v%s' % VERSION)
    ap.add_argument('--title', default=None, help='查这个标题的案件页是否已存在（只读）')
    ap.add_argument('--list-pages', action='store_true', help='列知识库里的案件页（只读）')
    ap.add_argument('--close', default=None, metavar='页标题', help='给案件页标「已结案」（默认 dry-run）')
    ap.add_argument('--folder', default='', help='配合 --close：案卷目录（写进标记与归档小节）')
    ap.add_argument('--date', default=None, help='配合 --close：结案日期 YYYY-MM-DD（默认今天）')
    ap.add_argument('--note', default='', help='配合 --close：备注')
    ap.add_argument('--backup-dir', default=None, help='配合 --close --apply：备份落点（默认 D:\\备份\\03-vault备份）')
    ap.add_argument('--apply', action='store_true', help='配合 --close：真写（不带则只预览）')
    ap.add_argument('--status', action='store_true', help='看知识库 git 状态（只读）')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.status:
        st = vault_status()
        print('知识库：%s（存在 %s）' % (st['root'], st['exists']))
        print('git：%s' % st['detail'])
        return 0
    if args.list_pages:
        rows = list_case_pages()
        print('知识库案件页 %d 个（根：%s）' % (len(rows), entities_dir()))
        for r in rows:
            print('  [%s] %s（%s）' % ('已结案' if r['closed'] else '在办',
                                      r['title'], r['mtime']))
        return 0
    if args.close:
        pl = plan_mark_closed(args.close, args.date, args.folder, args.note)
        if not pl['found']:
            print('没找到这一页：%s' % pl['title'])
            print('提示：先 `--list-pages` 看准确页名（按文件名/标题精确找，不模糊猜）')
            return 3
        if pl['already']:
            print('已标过「已结案」，无需再改：%s' % pl['path'])
            return 0
        print('页：%s' % pl['path'])
        for c in pl['changes']:
            print('  改动：%s' % c)
        for w in pl['warnings']:
            print('  ⚠️ %s' % w)
        if not args.apply:
            print('（dry-run，未写入；加 --apply 执行）')
            return 0
        r = apply_mark_closed(pl, backup_dir=args.backup_dir)
        print('已写入：%s' % r['path'])
        print('备份：%s' % r['backup'])
        print('写后校验：%s' % ('通过' if r.get('verified') else '未通过'))
        return 0
    if args.title:
        p = find_case_page(args.title)
        print('已存在：%s' % p if p else '不存在（可新建）')
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
