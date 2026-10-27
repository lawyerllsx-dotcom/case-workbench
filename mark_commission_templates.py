# -*- coding: utf-8 -*-
r"""mark_commission_templates.py — 委托手续模板标记化（批 64，一次性工具，可重跑）

作用：把「<模板目录>」的 6 件模板（已转换为 docx 并落
templates\委托材料\）作**一次性标记化**——把样例值 / 填空位替换为 docxtpl 的
`{{ 变量 }}` 标记，供 commission_ops.py 渲染。

铁律遵从（sohan 先例）：
  · 只做文本节点级替换 / 空单元格插入，**不改版式、不动抬头落款**；
  · 原库（<模板目录>）只读不动；本脚本只改 templates\委托材料\ 下的副本；
  · 替换带「预期原文断言」——不符即报错退出（防文件变化后错位误伤）。

用法：
  python mark_commission_templates.py          # dry-run（只打印将做的改动）
  python mark_commission_templates.py --apply  # 落盘（先自动备份到 _原始未标记\）
"""
import io
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, 'templates', '委托材料')
BACKUP_DIR = os.path.join(TPL_DIR, '_原始未标记')

RE_T = re.compile(r'<w:t(?: [^>]*)?>(.*?)</w:t>', re.S)

# ---------------------------------------------------------------- 替换表（替换型：按文本节点序号+预期原文）
# 每项：文件名 -> [(节点序号(0基), 预期原文子串, 新文本, 说明)]
REPLACES = {
    '1、委托代理合同（简易版）.docx': [
        (3,  ' ' * 20, '{{ client }}',      '甲方（委托方）值'),
        (14, ' ' * 10, ' {{ lawyer }} ',    '指派律师'),
        (16, ' ' * 11, ' {{ client }} ',    '为甲方（委托人）'),
        (18, ' ' * 10, ' {{ opponent }} {{ cause_d }} ', '与（对方+案由去「纠纷」尾）'),
        (21, ' ' * 7,  ' {{ stage }} ',     '审级'),
        (38, ' ' * 6,  ' {{ fee }} ',       '律师代理费'),
    ],
    '2、授权委托书-民事.docx': [
        (2, '委托人：张友谅', '委托人：{{ client }}', '委托人（含消毒）'),
        (3, '住  址：', '住  址：{{ address }}      电话：{{ phone }}', '住址+电话（含消毒）'),
        (5, '律师：', '律师：{{ lawyer }}', '律师'),
        (6, '联系电话：', '联系电话：{{ lawyer_phone }}', '律师电话'),
        (8, ' ', ' {{ opponent }}', '对方当事人'),
        (9, ' ', '{{ cause }}', '案由'),
        (11, ' ', ' {{ stage }} ', '审级'),
        (14, ' ', ' {{ auth_type }} ', '授权方式（特别/一般）'),
    ],
    '5、送达地址确认书-某法院格式.docx': [
        (0,  '某市某区人民法院', ' {{ court }}', '法院名'),
        (21, '张友谅', '{{ client }}', '当事人（含消毒）'),
        (24, '张律师', '{{ lawyer }}', '代理人'),
        (33, '350200199001010000', '{{ client_id }}', '当事人证件号（消毒）'),
        (35, '13500000000000000', '{{ lawyer_license }}', '律师证号'),
        (37, '福建省厦门市集美区叶厝村田墘社2号', '{{ address }}', '当事人送达地址（消毒）'),
        (39, '福建省厦门市集美区软件园三期诚毅北大街50号901单元某某律师事务所', '{{ lawyer_addr }}', '律师所地址'),
        (41, '13900000002', '{{ phone }}', '当事人电话（消毒）'),
        (43, '13900000000', '{{ lawyer_phone }}', '律师电话'),
    ],
}

# ---------------------------------------------------------------- 插入表（插入型：表格空值格 → 插入标记）
# 每项：文件名 -> [(行标签, 第几次出现(1基), 值格序号(0基, 该行内), 新文本, 说明)]
# 值格序号 = 该行内非合并单元格的排列序号（取「标签格的后一格」约定由脚本内部实现）
INSERTS = {
    '3、办案反馈表.docx': [
        ('委托人', 1, '{{ client }}', '存根·委托人'),
        ('案件类型', 1, '{{ case_type }}', '存根·案件类型'),
        ('案 由', 1, '{{ cause }}', '存根·案由'),
        ('承办律师', 1, '{{ lawyer }}', '存根·承办律师'),
        ('委托时间', 1, '{{ commission_date }}', '存根·委托时间'),
        ('委托人', 2, '{{ client }}', '正表·委托人'),
        ('承办律师', 2, '{{ lawyer }}', '正表·承办律师'),
        ('案件类型', 2, '{{ case_type }}', '正表·案件类型'),
        ('案 由', 2, '{{ cause }}', '正表·案由'),
    ],
    '5、送达地址确认书-某法院格式.docx': [
        ('案号', 1, '{{ case_no }}', '案号'),
        ('案由', 1, '{{ cause }}', '案由'),
    ],
    '6、账户确认表-某法院格式.docx': [
        ('案号', 1, '{{ case_no }}', '案号'),
        ('案由', 1, '{{ cause }}', '案由'),
        ('预交当事人', 1, '{{ client }}', '预交当事人'),
        ('联系方式', 1, '{{ phone }}', '联系方式'),
        ('收款人姓名或收款单位（全称）', 1, '{{ payee }}', '收款人'),
        ('开户行', 1, '{{ bank }}', '开户行'),
        ('账号', 1, '{{ account }}', '账号'),
    ],
}


def read_doc_xml(path):
    with zipfile.ZipFile(path) as z:
        return z.read('word/document.xml').decode('utf-8'), {i.filename: z.read(i.filename) for i in z.infolist()}


def write_doc_xml(path, members, new_xml):
    members = dict(members)
    members['word/document.xml'] = new_xml.encode('utf-8')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)


def apply_replaces(fname, dry=True):
    """替换型：按节点序号替换。返回改动条数或抛错。"""
    path = os.path.join(TPL_DIR, fname)
    xml, members = read_doc_xml(path)
    spans = [(m.start(1), m.end(1), m.group(1)) for m in RE_T.finditer(xml)]
    edits = REPLACES.get(fname) or []
    changes = []
    for idx, expect, new, desc in edits:
        if idx >= len(spans):
            raise SystemExit('!! %s 节点 %d 越界（共 %d）' % (fname, idx, len(spans)))
        s, e, old = spans[idx]
        if expect not in old:
            raise SystemExit('!! %s 节点 %d 预期不符：期望含 %r，实际 %r（%s）' % (fname, idx, expect, old, desc))
        changes.append((idx, old, new, desc))
    print('--- %s（%d 处替换）---' % (fname, len(changes)))
    for idx, old, new, desc in changes:
        print('  [%02d] %r → %r  （%s）' % (idx, old[:40], new[:40], desc))
    if dry:
        return len(changes)
    # 逆序替换（防位移）
    for idx, old, new, desc in sorted(changes, key=lambda x: -x[0]):
        s, e, _ = spans[idx]
        xml = xml[:s] + new + xml[e:]
    write_doc_xml(path, members, xml)
    return len(changes)


def apply_inserts(fname, dry=True):
    """插入型：定位「行标签格」→ 找同行下一格 → 空则插入标记文本（复制段落 rPr 样式）。"""
    from lxml import etree
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    path = os.path.join(TPL_DIR, fname)
    xml, members = read_doc_xml(path)
    tree = etree.fromstring(xml.encode('utf-8'))
    rows = tree.findall('.//{%s}tr' % W)
    # 建立行索引：行 → [(格序, 格元素, 文本)]
    def cell_text(tc):
        return ''.join((t.text or '') for t in tc.findall('.//{%s}t' % W))
    want = INSERTS.get(fname) or []
    hits = {}
    for ri, tr in enumerate(rows):
        tcs = tr.findall('{%s}tc' % W)
        for ci, tc in enumerate(tcs):
            txt = cell_text(tc).strip()
            hits.setdefault(txt, []).append((ri, ci, tcs))
    changes = []
    for label, nth, marker, desc in want:
        occ = hits.get(label) or []
        if len(occ) < nth:
            raise SystemExit('!! %s 找不到第 %d 次行标签 %r（共 %d 次）' % (fname, nth, label, len(occ)))
        ri, ci, tcs = occ[nth - 1]
        if ci + 1 >= len(tcs):
            raise SystemExit('!! %s 行 %r 无下一格（%s）' % (fname, label, desc))
        target = tcs[ci + 1]
        exist = cell_text(target).strip()
        changes.append((ri, ci, label, exist, marker, desc, target))
    print('--- %s（%d 处插入）---' % (fname, len(changes)))
    for ri, ci, label, exist, marker, desc, target in changes:
        print('  行%d 格%d [%s] 现值=%r → 插入 %r（%s）' % (ri, ci, label, exist, marker, desc))
    if dry:
        return len(changes)
    for ri, ci, label, exist, marker, desc, target in changes:
        if exist:
            raise SystemExit('!! %s [%s] 值格非空（%r），中止（%s）' % (fname, label, exist, desc))
        # 找/建段落
        p = target.find('{%s}p' % W)
        if p is None:
            p = etree.SubElement(target, '{%s}p' % W)
        # 复制段落 pPr 里的 rPr 作为 run 样式（与「手填后自动继承」一致）
        rpr_src = p.find('{%s}pPr/{%s}rPr' % (W, W))
        r = etree.SubElement(p, '{%s}r' % W)
        if rpr_src is not None:
            import copy as _copy
            r.append(_copy.deepcopy(rpr_src))
        t = etree.SubElement(r, '{%s}t' % W)
        t.text = marker
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    new_xml = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True).decode('utf-8')
    write_doc_xml(path, members, new_xml)
    return len(changes)


def main():
    dry = '--apply' not in sys.argv
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else ''
    if not dry:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        for fname in set(list(REPLACES) + list(INSERTS)):
            if only and only not in fname:
                continue
            src = os.path.join(TPL_DIR, fname)
            dst = os.path.join(BACKUP_DIR, fname)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
                print('[备份] %s → _原始未标记\\' % fname)
    total = 0
    for fname in list(REPLACES):
        if only and only not in fname:
            continue
        total += apply_replaces(fname, dry)
    for fname in list(INSERTS):
        if only and only not in fname:
            continue
        total += apply_inserts(fname, dry)
    print('共 %d 处%s。' % (total, '（dry-run；--apply 落盘）' if dry else ' 已落盘'))


if __name__ == '__main__':
    main()
