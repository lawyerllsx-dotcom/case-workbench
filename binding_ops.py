# -*- coding: utf-8 -*-
r"""binding_ops.py — 卷宗装订包（批 59，2026-10-13；CaseBoard 0.5.26「一键归档」的本地原生实现）

输入＝一个案件夹；输出＝其 `卷宗装订\` 子夹里两件：
  ① 完整卷宗 PDF = 封面 + 登记表 + 卷内目录 + 办案小结 + 正文材料
     · 正文材料 = 案卷 7 个规范子夹内的 PDF 逐份合并（每份一章书签）；
       图片转 A4 页居中插入；正文每页右下角打连续页码，前置页不打
  ② 卷内目录 .md 文字版（便于核对；含未并入清单）

纪律（照案卷红线）：
  · 原件一律只读（pymupdf 只读打开，零写入零移动零改名）
  · 产物只写 `卷宗装订\` 本身；该目录永远排除在扫描之外（防产物被当新材料）
  · 入卷范围 = 7 个规范子夹（01→07）；其余目录/根文件只列出提示、不并入
  · 非 PDF/图片（docx 等）只列清单提示「需转 PDF 后重新生成」，绝不自动转换
  · dry-run 默认（apply=False 零副作用）；apply=True 才落盘，写临时名再原子改名

调用：`scan(case_dir)` 出分拣清单；`build(case_dir, case_name, meta, apply)` 出装订包。
"""
import datetime
import os

try:                       # PyMuPDF 是可选依赖：没装只让「卷宗装订」不可用，不影响启动
    import pymupdf
    HAS_PYMUPDF = True
    _PYMUPDF_ERR = ''
except ImportError:        # noqa: BLE001
    pymupdf = None
    HAS_PYMUPDF = False
    _PYMUPDF_ERR = '未安装 PyMuPDF（pip install pymupdf），卷宗装订功能不可用'

BIND_SUBDIR = '卷宗装订'
# 入卷顺序 = 案卷规范目录序（case_ops.TREE_BASE 的正文七夹）
ORDER = ['01-委托手续', '02-法律文书', '03-证据材料', '04-法院文件',
         '05-费用票据', '06-当事人信息', '07-律师函']
# 明确排除的顶层目录（列出 + 原因；不入卷）
EXCLUDE_TOP = {
    '00-收件箱': '未分拣材料（建议先走「收件箱入库」分拣后再装订）',
    '08-过程文件': '过程性材料（草稿/旧版），不入卷',
    'pipeline': '工作台流水线底稿，不入卷',
    '需盖章文件': '待盖章稿，不入卷（盖章版应放进正文子夹）',
    '_归档区': '横向材料区，不入卷',
}
PDF_EXT = {'.pdf'}
IMG_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}

A4W, A4H = 595.28, 841.89          # A4（pt）
_TOC_ROWS = 18                     # 卷内目录每页行数


# ---------------------------------------------------------------- 扫描（只读）

def _fentry(full, name):
    st = os.stat(full)
    ext = os.path.splitext(name)[1].lower()
    kind = 'pdf' if ext in PDF_EXT else ('image' if ext in IMG_EXT else 'other')
    return {'name': name, 'full': full, 'ext': ext, 'kind': kind,
            'size_kb': round(st.st_size / 1024.0, 1),
            'mtime': datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d')}


def scan(case_dir):
    r"""案件夹分拣清单（纯只读）。

    返回 {'sections': [{'dir', 'files': [...]}],   # 仅 7 个规范子夹，按 ORDER 排
          'excluded': [{'dir', 'count', 'note'}],  # 其余目录（不含 卷宗装订 自身）
          'root_files': [...],                     # 案件夹根级散落文件
          'stats': {'total', 'mergeable', 'skipped'}}   # 统计只算规范子夹内
    """
    sections, excluded, root_files = [], [], []
    known = set(ORDER)
    for name in sorted(os.listdir(case_dir)):
        if name.startswith('.'):
            continue
        full = os.path.join(case_dir, name)
        if os.path.isfile(full):
            root_files.append(_fentry(full, name))
            continue
        if not os.path.isdir(full):
            continue
        if name in known:
            files = []
            for f in sorted(os.listdir(full)):
                fp = os.path.join(full, f)
                if os.path.isfile(fp) and not f.startswith('.') and not f.startswith('~$'):
                    files.append(_fentry(fp, f))
            sections.append({'dir': name, 'files': files})
        elif name == BIND_SUBDIR:
            continue                                        # 自身产物，静默跳过
        else:
            note = EXCLUDE_TOP.get(name)
            if note is None:
                note = ('下划线区（备份/横向），不入卷' if name.startswith('_')
                        else '未在案卷规范内（v1 不并入，需人工确认）')
            cnt = sum(1 for f in os.listdir(full)
                      if os.path.isfile(os.path.join(full, f)) and not f.startswith('.'))
            excluded.append({'dir': name, 'count': cnt, 'note': note})
    idx = {d: i for i, d in enumerate(ORDER)}
    sections.sort(key=lambda s: idx[s['dir']])
    allf = [f for s in sections for f in s['files']]
    stats = {'total': len(allf),
             'mergeable': sum(1 for f in allf if f['kind'] in ('pdf', 'image')),
             'skipped': sum(1 for f in allf if f['kind'] == 'other')}
    return {'sections': sections, 'excluded': excluded, 'root_files': root_files, 'stats': stats}


# ---------------------------------------------------------------- PDF 小工具

def _prescan(path, kind):
    r"""预检能否并入 → (ok, pages, err)。PDF 数真实页数；图片按 1 页。"""
    try:
        d = pymupdf.open(path)
        try:
            if getattr(d, 'needs_pass', False):
                return False, 0, '加密 PDF（需密码）'
            n = d.page_count
        finally:
            d.close()
        if n <= 0:
            return False, 0, '空文件（0 页）'
        return True, (n if kind == 'pdf' else 1), ''
    except Exception as e:                                   # noqa: BLE001
        return False, 0, '无法读取：%s' % e


def _tw(s, size):
    r"""文本宽度估算（pt）——优先 pymupdf 度量，取不到就按全角/半角近似。"""
    try:
        return pymupdf.get_text_length(s, fontname='china-s', fontsize=size)
    except Exception:                                        # noqa: BLE001
        return sum(size if ord(c) > 0x2E80 else size * 0.55 for c in s)


def _text(page, x, y, s, size=12, color=(0, 0, 0)):
    page.insert_text((x, y), str(s), fontname='china-s', fontsize=size, color=color)


def _center(page, y, s, size=12):
    _text(page, (A4W - _tw(s, size)) / 2.0, y, s, size)


def _wrap(s, n):
    s = str(s or '')
    return [s[i:i + n] for i in range(0, len(s), n)] or ['']


def _mg(meta, key, default=''):
    return str((meta or {}).get(key) or default)


# ---------------------------------------------------------------- 四个前置页

def _page_cover(doc, case_name, meta):
    page = doc.new_page(width=A4W, height=A4H)
    _center(page, 205, '案  件  卷  宗', 30)
    page.draw_line(pymupdf.Point(175, 240), pymupdf.Point(A4W - 175, 240),
                   color=(0, 0, 0), width=1)
    y = 300
    for line in _wrap(_mg(meta, '案件名', case_name), 20):
        _center(page, y, line, 17)
        y += 27
    y += 34
    for label, key in (('委托方：', '委托人'), ('案　　号：', '案号'),
                       ('受理机构：', '法院'), ('案件类别：', '案件类别')):
        v = _mg(meta, key, '')
        if v:
            _center(page, y, label + v, 13)
            y += 27
    y2 = A4H - 145
    v = _mg(meta, '承办律师', '')
    if v:
        _center(page, y2, '承办律师：' + v, 12)
        y2 += 25
    _center(page, y2, '立卷日期：' + datetime.datetime.now().strftime('%Y年%m月%d日'), 12)
    return page


def _page_register(doc, case_name, meta, stats, body_pages):
    page = doc.new_page(width=A4W, height=A4H)
    _center(page, 120, '卷  宗  登  记  表', 22)
    rows = [
        ('案件名称', _mg(meta, '案件名', case_name)),
        ('案件类别', _mg(meta, '案件类别', '')),
        ('委托方', _mg(meta, '委托人', '')),
        ('案号', _mg(meta, '案号', '')),
        ('受理机构', _mg(meta, '法院', '')),
        ('承办律师', _mg(meta, '承办律师', '')),
        ('材料件数', '共 %d 份（电子并入 %d 份，未并入 %d 份）'
         % (stats['total'], stats['merged'], stats['skipped'] + stats['bad'])),
        ('卷宗页数', '%d 页（正文 %d 页）' % (stats['total_pages'], body_pages)),
        ('立卷人', '（手填）'),
        ('立卷日期', datetime.datetime.now().strftime('%Y年%m月%d日')),
    ]
    x0, x1, c1 = 90.0, A4W - 90.0, 110.0
    y = 190.0
    rh = 44.0
    page.draw_rect(pymupdf.Rect(x0, y, x1, y + rh * len(rows)), color=(0, 0, 0), width=1)
    for i, (k, v) in enumerate(rows):
        yy = y + rh * i
        if i:
            page.draw_line(pymupdf.Point(x0, yy), pymupdf.Point(x1, yy), color=(0, 0, 0), width=1)
        page.draw_line(pymupdf.Point(x0 + c1, yy), pymupdf.Point(x0 + c1, yy + rh),
                       color=(0, 0, 0), width=1)
        _text(page, x0 + 14, yy + 28, k, 12)
        _text(page, x0 + c1 + 14, yy + 28, v, 12)
    return page


def _pages_toc(doc, merged):
    r"""卷内目录（可能多页）。merged 每项须已含 'no'/'name'/'dir'/'mtime'/'bp'（正文起页）/'pc'。"""
    cols = [('序号', 40.0), ('材料名称', 250.0), ('来源', 90.0), ('日期', 60.0), ('页码', 35.0)]
    x0, x1 = 60.0, A4W - 60.0
    y = 118.0
    rh = 26.0
    total = max(1, (len(merged) + _TOC_ROWS - 1) // _TOC_ROWS)
    for pi in range(total):
        page = doc.new_page(width=A4W, height=A4H)
        _center(page, 92, '卷  内  目  录', 22)
        chunk = merged[pi * _TOC_ROWS:(pi + 1) * _TOC_ROWS]
        rows = 1 + len(chunk)
        page.draw_rect(pymupdf.Rect(x0, y, x1, y + rh * rows), color=(0, 0, 0), width=1)
        cx = x0
        for j, (label, w) in enumerate(cols):
            if j:
                page.draw_line(pymupdf.Point(cx, y), pymupdf.Point(cx, y + rh * rows),
                               color=(0, 0, 0), width=1)
            _text(page, cx + 6, y + 18, label, 11)
            cx += w
        page.draw_line(pymupdf.Point(x0, y + rh), pymupdf.Point(x1, y + rh), color=(0, 0, 0), width=1)
        for i, m in enumerate(chunk):
            yy = y + rh * (i + 1)
            if i:
                page.draw_line(pymupdf.Point(x0, yy), pymupdf.Point(x1, yy), color=(0, 0, 0), width=1)
            span = ('%d' % m['bp']) if m['pc'] <= 1 else ('%d-%d' % (m['bp'], m['bp'] + m['pc'] - 1))
            vals = [str(m['no']), m['name'], m['dir'], m['mtime'], span]
            cx = x0
            for j, v in enumerate(vals):
                _text(page, cx + 6, yy + 18, v[:26] if j == 1 else v, 11)
                cx += cols[j][1]
    return total


def _page_summary(doc, case_name, meta, stats, body_pages):
    page = doc.new_page(width=A4W, height=A4H)
    _center(page, 100, '办  案  小  结', 22)
    lines = []
    lines.append(('案件：', _mg(meta, '案件名', case_name)))
    if _mg(meta, '委托人'):
        lines.append(('委托方：', _mg(meta, '委托人') + ('　｜　类别：' + _mg(meta, '案件类别') if _mg(meta, '案件类别') else '')))
    if _mg(meta, '案号'):
        lines.append(('案号：', _mg(meta, '案号')))
    if _mg(meta, '法院'):
        lines.append(('受理机构：', _mg(meta, '法院')))
    if _mg(meta, '标的额'):
        lines.append(('标的额：', _mg(meta, '标的额')))
    if _mg(meta, '当前进度'):
        lines.append(('当前进度：', _mg(meta, '当前进度')))
    if _mg(meta, '状态'):
        lines.append(('最新状态：', _mg(meta, '状态')))
    x0 = 80.0
    y = 170.0
    for label, v in lines:
        _text(page, x0, y, label, 12)
        vl = _wrap(v, 32)
        for i, seg in enumerate(vl):
            _text(page, x0 + 78, y + i * 22, seg, 12)
        y += 22 * max(1, len(vl)) + 8
    y += 14
    page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(A4W - 80, y), color=(0.6, 0.6, 0.6), width=1)
    y += 30
    for seg in ('材料统计：本卷共 %d 份材料——电子并入正文 %d 份（%d 页）；'
                '未并入 %d 份（见卷内目录 md 版）。'
                % (stats['total'], stats['merged'], body_pages,
                   stats['skipped'] + stats['bad']),
                '装订日期：' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M')):
        for seg2 in _wrap(seg, 34):
            _text(page, x0, y, seg2, 12)
            y += 22
        y += 6
    _text(page, x0, A4H - 90, '注：本小结由工作台按台账与案卷元数据自动拼装（v1），正式归档小结请人工审阅补充。', 10)
    return page


# ---------------------------------------------------------------- 主流程

def build(case_dir, case_name, meta=None, apply=False):
    if not HAS_PYMUPDF:
        return {'error': _PYMUPDF_ERR}
    r"""生成卷宗装订包。

    apply=False → {'dry_run': True, 'plan': {...}}（零副作用，只扫描 + 预检）
    apply=True  → 生成两件产物，返回 {'ok': True, 'dry_run': False, 'pdf', 'md',
                  'pages', 'body_pages', 'merged', 'skipped', 'bad', 'warnings'}
    """
    sc = scan(case_dir)
    merged, skipped, bad = [], [], []
    for s in sc['sections']:
        for f in s['files']:
            item = {'dir': s['dir'], 'name': f['name'], 'full': f['full'],
                    'kind': f['kind'], 'mtime': f['mtime'], 'size_kb': f['size_kb']}
            if f['kind'] == 'other':
                skipped.append(item)
                continue
            ok, n, err = _prescan(f['full'], f['kind'])
            if not ok:
                bad.append(dict(item, error=err))
                continue
            item['pc'] = n
            merged.append(item)
    warnings = []
    if skipped:
        warnings.append('%d 份非 PDF/图片材料未并入正文（md 清单中列出，需转 PDF 后重新生成）' % len(skipped))
    if bad:
        warnings.append('%d 份材料无法读取（已跳过）：%s' % (len(bad), '、'.join(b['name'] for b in bad[:3])))
    for e in sc['excluded']:
        if e['dir'] == '00-收件箱' and e['count']:
            warnings.append('收件箱还有 %d 个未分拣文件（装订不含，建议先分拣）' % e['count'])
    if sc['root_files']:
        warnings.append('案件夹根目录有 %d 个散落文件（未入卷）' % len(sc['root_files']))
    stats = {'total': sc['stats']['total'], 'merged': len(merged),
             'skipped': len(skipped), 'bad': len(bad)}
    out_dir = os.path.join(case_dir, BIND_SUBDIR)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    # 防重名（同秒复跑 / 已有同名产物）：基线被占用则加序号，不覆盖旧产物
    def _taken(c):
        return (os.path.exists(os.path.join(out_dir, '卷宗-%s.pdf' % c))
                or os.path.exists(os.path.join(out_dir, '卷内目录-%s.md' % c)))
    cand, _i = ts, 2
    while _taken(cand):
        cand = '%s-%d' % (ts, _i)
        _i += 1
    ts = cand
    base_pdf = '卷宗-%s.pdf' % ts
    base_md = '卷内目录-%s.md' % ts
    plan = {'out_dir': BIND_SUBDIR, 'out_pdf': base_pdf, 'out_md': base_md,
            'scan': sc, 'merged_names': [m['name'] for m in merged],
            'skipped': [{'dir': s['dir'], 'name': s['name']} for s in skipped],
            'bad': [{'dir': b['dir'], 'name': b['name'], 'error': b['error']} for b in bad],
            'warnings': warnings}
    if not apply:
        return {'dry_run': True, 'plan': plan}
    if not merged:
        return {'ok': False, 'dry_run': False,
                'error': '没有可并入的 PDF/图片材料（7 个规范子夹内）——请先把材料归位再装订',
                'plan': plan}

    # —— 前置页数：封面1 + 登记1 + 目录N + 小结1；正文页码从 1 起 ——
    toc_pages = max(1, (len(merged) + _TOC_ROWS - 1) // _TOC_ROWS)
    front = 1 + 1 + toc_pages + 1
    bp = 1
    for i, m in enumerate(merged):
        m['no'] = i + 1
        m['bp'] = bp
        bp += m['pc']
    body_pages = bp - 1
    stats['total_pages'] = front + body_pages
    out_dir = os.path.join(case_dir, BIND_SUBDIR)
    os.makedirs(out_dir, exist_ok=True)
    out_pdf = os.path.join(out_dir, base_pdf)
    out_md = os.path.join(out_dir, base_md)

    doc = pymupdf.open()
    try:
        _page_cover(doc, case_name, meta)
        _page_register(doc, case_name, meta, stats, body_pages)
        _pages_toc(doc, merged)
        _page_summary(doc, case_name, meta, stats, body_pages)
        # 正文合并（物理页 = front + 1 起；记录每份材料物理起始页供书签）
        for m in merged:
            start = doc.page_count
            src = pymupdf.open(m['full'])
            try:
                if m['kind'] == 'pdf':
                    doc.insert_pdf(src)
                else:
                    _image_page(doc, m['full'])
            finally:
                src.close()
            m['phys'] = start            # 插入前页数 = 0-based 首页编号
        # 正文页码（前置页不打）
        for i in range(front, doc.page_count):
            pg = doc[i]
            r = pg.rect
            pg.insert_text((r.width - 58, r.height - 26), '- %d -' % (i - front + 1),
                           fontname='china-s', fontsize=9, color=(0, 0, 0))
        # 书签
        toc = [[1, '封面', 1], [1, '登记表', 2], [1, '卷内目录', 3], [1, '办案小结', front]]
        for m in merged:
            toc.append([1, '%d. %s' % (m['no'], m['name']), m['phys'] + 1])
        doc.set_toc(toc)
        doc.subset_fonts()
        tmp_pdf = out_pdf + '.tmp'
        doc.save(tmp_pdf, garbage=3, deflate=True)
        os.replace(tmp_pdf, out_pdf)
    finally:
        doc.close()
    # md 版卷内目录（写临时名再原子改名）
    md_lines = ['# 卷内目录 — %s' % _mg(meta, '案件名', case_name), '',
                '- 装订时间：' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                '- 并入材料：%d 份 ｜ 正文 %d 页 ｜ 卷宗总页数 %d 页 ｜ 电子卷宗：`%s`'
                % (len(merged), body_pages, stats['total_pages'], base_pdf), '',
                '| 序号 | 材料名称 | 来源 | 材料日期 | 正文页码 |', '|---|---|---|---|---|']
    for m in merged:
        span = str(m['bp']) if m['pc'] <= 1 else '%d-%d' % (m['bp'], m['bp'] + m['pc'] - 1)
        md_lines.append('| %d | %s | %s | %s | %s |' % (m['no'], m['name'], m['dir'], m['mtime'], span))
    if skipped:
        md_lines += ['', '## 未并入材料（如需装订请先转为 PDF 后重新生成）', '']
        md_lines += ['- %s/%s' % (s['dir'], s['name']) for s in skipped]
    if bad:
        md_lines += ['', '## 无法读取的材料（已跳过）', '']
        md_lines += ['- %s/%s — %s' % (b['dir'], b['name'], b['error']) for b in bad]
    if sc['excluded']:
        _ex = [e for e in sc['excluded'] if e['count']]
        if _ex:
            md_lines += ['', '## 未入卷目录（不计入卷宗）', '']
            md_lines += ['- %s：%d 个文件 — %s' % (e['dir'], e['count'], e['note']) for e in _ex]
    if sc['root_files']:
        md_lines += ['', '## 案件夹根目录散落文件（未入卷）', '']
        md_lines += ['- %s' % f['name'] for f in sc['root_files']]
    tmp_md = out_md + '.tmp'
    with open(tmp_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md_lines) + '\n')
    os.replace(tmp_md, out_md)
    return {'ok': True, 'dry_run': False, 'out_dir': BIND_SUBDIR,
            'pdf': base_pdf, 'md': base_md,
            'pages': stats['total_pages'], 'body_pages': body_pages,
            'merged': len(merged), 'skipped': len(skipped), 'bad': len(bad),
            'warnings': warnings}


def _image_page(doc, img_path):
    r"""图片 → A4 整页居中插入（放大受限：不超原尺寸）。"""
    page = doc.new_page(width=A4W, height=A4H)
    try:
        src = pymupdf.open(img_path)
        try:
            ir = src[0].rect
            iw, ih = (ir.width or 0), (ir.height or 0)
        finally:
            src.close()
        if iw <= 0 or ih <= 0:
            raise ValueError('图片尺寸为 0')
        avail_w, avail_h = A4W - 100.0, A4H - 100.0
        scale = min(avail_w / iw, avail_h / ih, 1.0)
        w, h = iw * scale, ih * scale
        x, y = (A4W - w) / 2.0, (A4H - h) / 2.0
        page.insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=img_path)
    except Exception:                                        # noqa: BLE001
        _text(page, 72, A4H / 2, '（图片无法插入：%s）' % os.path.basename(img_path), 11)
    return page
