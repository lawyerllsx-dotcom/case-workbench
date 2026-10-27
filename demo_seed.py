#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_seed.py — 生成一份可立刻运行的演示数据（全部虚构）

产出：
  demo/案件管理台账.xlsx      5 个 sheet 的合成台账（含 3 条虚构案件）
  demo/案件/在办/...          2 个虚构案卷夹（按目录规范建好子夹 + 材料怎么放）
  demo/案件/已结案/...        1 个
  demo/案件/律师函|合同|差旅发票   业务线夹
  data/workbench.db           由首次启动自动创建（不在此生成）

幂等：已存在的文件/目录一律跳过，除非 --force（会先备份到 demo/.seed-backup-时间戳/）。

用法：
    python demo_seed.py
    python demo_seed.py --force
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

import config

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit('缺少 openpyxl：pip install openpyxl')

HERE = os.path.dirname(os.path.abspath(__file__))
_MISSING = set()          # 缺哪些可选依赖，结尾统一提示

# 目录规范（与 case_ops.TREE_BASE 一致；示例数据不写死业务语义）
TREE_BASE = ['00-收件箱', '需盖章文件', '01-委托手续', '02-法律文书', '03-证据材料',
             '04-法院文件', '05-费用票据', '06-当事人信息', '07-律师函', '08-过程文件']
TREE_EXEC_EXTRA = ['09-财产查控', '10-处置分配']

CASES = [
    {
        'sheet': '案件', '序号': 1, '案号': '（2026）闽0203民初9001号',
        '案件': '民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖',
        '案件类别': '诉讼', '委托人': '甲公司', '法院/仲裁': '某市某区人民法院',
        '承办法官': '王法官', '开庭时间': '2026-11-12 09:30', '当前进度': '已提交证据，等待开庭',
        '进度日志': '10.20：完成证据交换\n10.28：补充提交送货单 3 份',
        '最新状态': '待开庭', '合同编号': 'HT-2026-001', '委托阶段': '一审',
        '标的额': 286000, '基础律师费': 20000, '风险费约定': '回款 5%',
        '已收到': 10000, '付款日期': '2026-10-08', '开票金额': 10000,
        '发票号': '00000001', '剩余尾款': 10000, '保全费（法院）': 2000,
        '保费（保险公司）': 800, '保全情况及期限': '已保全，2027-04-30 到期',
        '承办律师': '张律师', '备注': '示例数据',
    },
    {
        'sheet': '案件', '序号': 2, '案号': '（2026）闽0205民初9002号',
        '案件': '民-A-26002_丙某诉丁某民间借贷纠纷',
        '案件类别': '诉讼', '委托人': '丙某', '法院/仲裁': '某市某区人民法院',
        '承办法官': '李法官', '开庭时间': '2026-11-20 14:30', '当前进度': '已立案，等待排期',
        '进度日志': '10.25：线上立案通过', '最新状态': '已立案',
        '合同编号': 'HT-2026-002', '委托阶段': '一审',
        '标的额': 150000, '基础律师费': 12000, '风险费约定': '',
        '已收到': 12000, '付款日期': '2026-10-25', '开票金额': 12000,
        '发票号': '00000002', '剩余尾款': 0, '保全费（法院）': 0,
        '保费（保险公司）': 0, '保全情况及期限': '', '承办律师': '张律师', '备注': '示例数据',
    },
    {
        'sheet': '其他案件', '序号': 3, '案号': '（2026）闽0203执9003号',
        '案件': '执-26003_戊公司执行案',
        '案件类别': '执行', '委托人': '戊公司', '法院/仲裁': '某市某区人民法院',
        '承办法官': '赵法官', '开庭时间': '', '当前进度': '已查控到银行账户，等待扣划',
        '进度日志': '10.15：执行立案\n10.22：收到查控反馈', '最新状态': '执行中',
        '合同编号': 'HT-2026-003', '委托阶段': '执行',
        '标的额': 420000, '基础律师费': 15000, '风险费约定': '回款 8%',
        '已收到': 0, '付款日期': '', '开票金额': 0,
        '发票号': '', '剩余尾款': 15000, '保全费（法院）': 0,
        '保费（保险公司）': 0, '保全情况及期限': '', '承办律师': '张律师', '备注': '示例数据',
    },
]

def _make_pdf(path, text):
    """生成一张极简 PDF（示例素材用）。"""
    if os.path.exists(path):
        return path
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            _MISSING.add('pymupdf')
            return ''
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), text, fontsize=11, fontname='china-s')
    doc.save(path)
    doc.close()
    return path


def _make_jpg(path):
    """生成一张示意图片（示例签收凭证用）。"""
    if os.path.exists(path):
        return path
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _MISSING.add('pillow')
        return ''
    img = Image.new('RGB', (600, 800), '#F5F5F5')
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 760], outline='#999999', width=2)
    d.text((70, 80), 'Delivery Receipt (sample)', fill='#333333')
    d.text((70, 130), 'Tracking No.: 0000000000', fill='#333333')
    d.line([70, 700, 400, 700], fill='#666666', width=1)
    d.text((70, 710), 'Signature (sample)', fill='#666666')
    img.save(path, quality=85)
    return path


def _make_sohan_template():
    """生成一个匿名的所函 docx 模板（含占位符），让所函功能开箱可用。"""
    p = os.path.join(HERE, 'templates', '所函模板.docx')
    if os.path.exists(p):
        return p
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        _MISSING.add('python-docx')
        return ''
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = Document()
    st = d.styles['Normal']
    st.font.name = '宋体'
    st.font.size = Pt(11)
    d.add_paragraph('律 师 函').alignment = 1
    d.add_paragraph('')
    d.add_paragraph('致：{{ opponent }}')
    d.add_paragraph('')
    d.add_paragraph('本所接受 {{ client }} 的委托，指派 {{ lawyer }} 律师就 {{ cause }} '
                    '一事，向贵方函告如下：')
    d.add_paragraph('')
    d.add_paragraph('一、{{ body }}')
    d.add_paragraph('')
    d.add_paragraph('二、请贵方于收到本函之日起 {{ days }} 日内予以答复并履行义务。')
    d.add_paragraph('')
    d.add_paragraph('特此函告。')
    d.add_paragraph('')
    d.add_paragraph('{{ firm }}')
    d.add_paragraph('{{ date }}')
    d.save(p)
    return p


COMMISSION_DOCS = {
    '1、委托代理合同（简易版）.docx': [
        ('委托代理合同', 'title'),
        ('甲方（委托方）：{{ client }}', ''),
        ('乙方（受托方）：{{ lawyer }}', ''),
        ('乙方指派 {{ lawyer }} 为甲方与 {{ opponent }} {{ cause_d }} 纠纷一案的{{ stage }} 审阶段代理人。', ''),
        ('代理权限：{{ auth_type }}授权。', ''),
        ('律师代理费 {{ fee }} 元，于本合同签订之日起七日内支付。', ''),
        ('本合同一式两份，双方各执一份，自签字盖章之日起生效。', ''),
        ('甲方（签字）：____________　　乙方（盖章）：____________', ''),
        ('　　　　　　　　年　　月　　日', ''),
    ],
    '2、授权委托书-民事.docx': [
        ('授权委托书', 'title'),
        ('委托人：{{ client }}', ''),
        ('身份证号／统一社会信用代码：{{ client_id }}', ''),
        ('住址：{{ address }}　　电话：{{ phone }}', ''),
        ('受托人律师：{{ lawyer }}　执业证号：{{ lawyer_license }}', ''),
        ('工作单位：{{ lawyer_addr }}　电话：{{ lawyer_phone }}', ''),
        ('现委托上述受托人作为本人 {{ stage }} 审诉讼代理人，参与委托人与 {{ opponent }} {{ cause }}一案。', ''),
        ('代理权限：{{ auth_type }} 授权。', ''),
        ('委托人（签字）：____________', ''),
        ('　　　　　　　　年　　月　　日', ''),
    ],
    '3、办案反馈表.docx': ('table', [
        ['委托人', '{{ client }}'], ['案件类型', '{{ case_type }}'], ['案由', '{{ cause }}'],
        ['承办律师', '{{ lawyer }}'], ['委托日期', '{{ commission_date }}'],
        ['反馈内容', '（由承办律师填写）'],
    ]),
    '4、聘请律师提示.docx': [
        ('聘请律师提示', 'title'),
        ('一、律师依法独立执业，其意见基于事实与法律作出，不对案件结果作承诺。', ''),
        ('二、委托人应如实陈述案情、提供真实完整的材料。', ''),
        ('三、律师费、差旅费等按合同约定收取，另有约定的从其约定。', ''),
        ('四、委托人有权解除委托；律师在委托人隐瞒重要事实等情形下有权辞去委托。', ''),
        ('五、本案承办律师及联系方式以委托合同记载为准。', ''),
        ('委托人（签字）：____________', ''),
        ('　　　　　　　　年　　月　　日', ''),
    ],
    '5、送达地址确认书-某法院格式.docx': ('table', [
        ['法院', '{{ court }}'], ['案由', '{{ cause }}'], ['当事人', '{{ client }}'],
        ['证件号码', '{{ client_id }}'], ['送达地址', '{{ address }}'],
        ['联系电话', '{{ phone }}'], ['代理人', '{{ lawyer }}'],
        ['执业证号', '{{ lawyer_license }}'], ['代理人地址', '{{ lawyer_addr }}'],
        ['代理人电话', '{{ lawyer_phone }}'],
        ['确认意见', '上述地址为有效送达地址，因地址不准确导致文书未被接收的，由本人承担后果。'],
    ]),
    '6、账户确认表-某法院格式.docx': ('table', [
        ['案号', '{{ case_no }}'], ['案由', '{{ cause }}'], ['收款人', '{{ payee }}'],
        ['当事人', '{{ client }}'], ['联系电话', '{{ phone }}'],
        ['开户银行', '{{ bank }}'], ['银行账号', '{{ account }}'],
        ['确认意见', '上述账户为本案接收款项账户。'],
    ]),
}


def _make_commission_templates():
    """生成六件套骨架模板（含 {{变量}}），让委托手续功能开箱可用。
    版式遵循国内民事委托材料的常见结构；正式使用请换成所内审定版本。"""
    base = os.path.join(HERE, 'templates', '委托材料')
    os.makedirs(base, exist_ok=True)
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        _MISSING.add('python-docx')
        return 0
    made = 0
    for fname, spec in COMMISSION_DOCS.items():
        p = os.path.join(base, fname)
        if os.path.exists(p):
            continue
        d = Document()
        if isinstance(spec, tuple) and spec[0] == 'table':
            for row in spec[1]:
                cells = d.add_table(rows=1, cols=2).rows[0].cells
                cells[0].text, cells[1].text = row[0], row[1]
        else:
            for text, style in spec:
                para = d.add_paragraph(text)
                if style == 'title':
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    if para.runs:
                        para.runs[0].bold = True
        d.save(p)
        made += 1
    return made


LETTERS = [
    {'序号': 1, '委托人': '甲公司', '发函日期': '2026-09-20', '承办律师': '张律师',
     '收函对象': '乙公司', '欠款金额（本金）': 86000, '案由': '买卖合同',
     '签收状态': '已签收', '后续跟进': '10.10 已电话催告', '备注': '示例数据'},
    {'序号': 2, '委托人': '丙某', '发函日期': '2026-10-08', '承办律师': '张律师',
     '收函对象': '丁某', '欠款金额（本金）': 150000, '案由': '民间借贷',
     '签收状态': '未签收', '后续跟进': '', '备注': '示例数据'},
]
CONTRACTS = [
    {'序号': 1, '送审日期': '2026-10-12', '委托人': '甲公司', '合同名称': '年度采购框架协议',
     '相对方': '乙公司', '合同类型': '买卖', '审查状态': '已交付', '收费': 3000,
     '交付日期': '2026-10-16', '备注': '示例数据'},
]
PAYMENTS = [
    {'序号': 1, '案件': '执-26003_戊公司执行案', '案号': '（2026）闽0203执9003号',
     '回款日期': '2026-10-20', '金额(元)': 50000, '来源': '银行扣划', '备注': '示例数据'},
]

GUIDE = """# 这个案子的材料怎么放（示例，可随时改）
## 按阶段
| 子夹 | 放什么 |
|---|---|
| 00-收件箱 | 刚收到、还没归位的材料（先扔这里，再归类） |
| 01-委托手续 | 委托代理合同、授权委托书、风险告知、送达地址确认书 |
| 02-法律文书 | 起诉状、答辩状、代理词、申请书（自己出的文书） |
| 03-证据材料 | 证据清单与证据原件/复印件 |
| 04-法院文件 | 受理通知、传票、判决书、裁定书、送达回证 |
| 05-费用票据 | 诉讼费票据、保全费票据、差旅发票 |
| 06-当事人信息 | 身份证明、主体资格材料 |
| 07-律师函 | 本案发出的律师函及签收凭证 |
| 08-过程文件 | 研究笔记、会议记录、OCR 文本、内部草稿 |

## 三条规矩
1. 只移动不删除：拿不准就先进 00-收件箱。
2. 同一份材料只放一处，需要多份就复制。
3. 文件名带日期或版本号，别用「最终版」「最终版2」。
"""


def _write_ledger(path):
    wb = Workbook()
    wb.remove(wb.active)
    cols_case = config.get('ledger.cols_case') or {}
    cols_letter = config.get('ledger.cols_letter') or {}
    cols_contract = config.get('ledger.cols_contract') or {}
    cols_payment = config.get('ledger.cols_payment') or {}

    def build(title, cols, rows, groups=None):
        ws = wb.create_sheet(title)
        names = [k for k, _ in sorted(cols.items(), key=lambda kv: kv[1])]
        ws.cell(row=1, column=1, value=title)
        ws.cell(row=1, column=1).font = Font(bold=True, size=14)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(names))
        ws.cell(row=1, column=1).alignment = Alignment(horizontal='center')
        for i, n in enumerate(names, start=1):
            c = ws.cell(row=3, column=i, value=n)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='4B5563')
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(24, len(n) * 2 + 6))
        ws.freeze_panes = 'A4'
        for j, row in enumerate(rows or [], start=config.get('ledger.data_start_row', 4)):
            for k, v in (row or {}).items():
                if k in cols:
                    ws.cell(row=j, column=cols[k], value=v)
        if groups:
            for i, g in enumerate(groups[:len(names)], start=1):
                ws.cell(row=2, column=i, value=g).font = Font(bold=True, size=9, color='6B7280')
        return ws

    build('案件', cols_case, [c for c in CASES if c['sheet'] == '案件'],
          ['基本信息', '', '', '', '', '', '', '时间', '', '', '状态', '', '', '费用', '', '', '', '', '', '', '', '保全', '', '', ''])
    build('其他案件', cols_case, [c for c in CASES if c['sheet'] == '其他案件'])
    build('律师函', cols_letter, LETTERS)
    build('合同审查', cols_contract, CONTRACTS)
    build('执行款', cols_payment, PAYMENTS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def _make_case(root, name, exec_case=False, done=False):
    base = os.path.join(root, name)
    for d in TREE_BASE + (TREE_EXEC_EXTRA if exec_case else []):
        os.makedirs(os.path.join(base, d), exist_ok=True)
    os.makedirs(os.path.join(base, 'pipeline'), exist_ok=True)
    guide = os.path.join(base, '_材料怎么放.md')
    if not os.path.exists(guide):
        with open(guide, 'w', encoding='utf-8') as f:
            f.write(GUIDE)
    log = os.path.join(base, 'intake-log.md')
    if not os.path.exists(log):
        with open(log, 'w', encoding='utf-8') as f:
            f.write('# 收件记录（示例）\n\n| 日期 | 文件 | 归位 | 说明 |\n|---|---|---|---|\n'
                    '| %s | 示例材料.pdf | 04-法院文件 | 演示数据 |\n' % datetime.now().strftime('%Y-%m-%d'))
    return base


def main():
    ap = argparse.ArgumentParser(description='生成演示数据（全部虚构）')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的 demo 数据（先备份）')
    args = ap.parse_args()

    demo = os.path.join(HERE, 'demo')
    if os.path.exists(demo):
        if not args.force:
            print('demo/ 已存在，跳过生成。要重建请加 --force（会先备份）。')
            return 0
        bak = os.path.join(demo, '.seed-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bak, exist_ok=True)
        for item in os.listdir(demo):
            if item.startswith('.seed-backup-'):
                continue
            shutil.move(os.path.join(demo, item), os.path.join(bak, item))
        print('已备份旧 demo 数据到', bak)

    ledger = config.abspath(config.get('paths.ledger')) or os.path.join(demo, '案件管理台账.xlsx')
    _write_ledger(ledger)
    print('台账：', ledger)

    root = config.abspath(config.get('paths.case_root')) or os.path.join(demo, '案件', '在办')
    archive = config.abspath(config.get('paths.archive_root')) or os.path.join(demo, '案件', '已结案')
    biz = config.abspath(config.get('paths.biz_root')) or os.path.join(demo, '案件')

    for name, is_exec in (('民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖', False),
                          ('民-A-26002_丙某诉丁某民间借贷纠纷', False)):
        print('案卷：', _make_case(root, name, is_exec))
    print('案卷：', _make_case(archive, '民-A-25009_已结案示例', done=True))
    print('案卷：', _make_case(root, '执-26003_戊公司执行案', exec_case=True))

    # 系列案总夹（总夹不对应单条台账行，页面上显示「系列案总夹」徽章）
    series = os.path.join(root, '民-A-26004_甲公司系列案（2 件）')
    for sub in ('1、甲公司与乙公司买卖合同纠纷', '2、甲公司与丙公司服务合同纠纷'):
        _make_case(series, sub)

    # 过程文件（留痕页的「归位」入口需要一个「错位」样本才会渲染）
    _make_pdf(os.path.join(proc if 'proc' in dir() else os.path.join(
        root, '民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖', '08-过程文件'),
        '财产保全申请书-错位示例.pdf'),
        '财产保全申请书（示例，故意放错区：应放 02-法律文书）\n')
    proc = os.path.join(root, '民-A-26001_甲公司诉乙公司买卖合同纠纷_买卖', '08-过程文件')
    if os.path.isdir(proc):
        with open(os.path.join(proc, '起草思路-v1.md'), 'w', encoding='utf-8') as f:
            f.write('# 起草思路 v1（示例草稿）\n\n'
                    '1. 争点：货款是否已到期、质量异议是否在合理期限提出。\n'
                    '2. 证据：送货单 3 份、对账单 1 份、微信催款记录。\n'
                    '3. 待补：对方签收凭证。\n')

    # 业务线：律师函（含签收凭证 PDF/JPG）+ 散件待归类
    letter_dir = os.path.join(biz, '律师函', '律师函-乙公司', '律师函及签收记录')
    os.makedirs(letter_dir, exist_ok=True)
    _make_pdf(os.path.join(letter_dir, '律师函-乙公司（签收）.pdf'),
              '律师函（示例）\n\n致：乙公司\n\n本所接受甲公司委托，就贵司拖欠货款一事函告如下……\n')
    _make_jpg(os.path.join(letter_dir, '签收凭证-快递单.jpg'))
    scatter_dir = os.path.join(biz, '律师函', '待归类散件')
    os.makedirs(scatter_dir, exist_ok=True)
    _make_pdf(os.path.join(scatter_dir, '对账单-乙公司-2026-09.pdf'),
              '对账单（示例）\n\n截至 2026-09-30，贵司欠款 86,000 元。\n')

    for d in ('律师函', '合同', '差旅发票'):
        os.makedirs(os.path.join(biz, d), exist_ok=True)

    # 所函 + 委托六件套模板（匿名骨架版，让两个文书功能开箱可用）
    _make_sohan_template()
    print('委托模板：新增 %d 件' % _make_commission_templates())

    if _MISSING:
        print()
        print('⚠️  以下可选依赖未安装，示例素材不完整（不影响核心功能）：')
        for _m in sorted(_MISSING):
            print('   - %s' % _m)
        print('   补全：pip install %s' % ' '.join(
            {'pymupdf': 'pymupdf', 'pillow': 'pillow',
             'python-docx': 'python-docx'}[m] for m in sorted(_MISSING)))
        print('   然后：python demo_seed.py --force')

    print('\n完成。下一步：')
    print('  python app.py --port 8899')
    print('  浏览器打开 http://127.0.0.1:8899')
    return 0


if __name__ == '__main__':
    sys.exit(main())
