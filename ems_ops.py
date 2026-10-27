# -*- coding: utf-8 -*-
r"""ems_ops.py — EMS 签收凭证：存放 / 识别 / 提炼（批 46，2026-09-19）

爸爸需求：律师函页要有「导入 EMS 签收截图的入口」，并能识别出来。

## 设计原则（三条，都很要紧）

1. **存放位置约定，不散落**：截图存到该债务人材料夹的固定子目录
   `<批量催收件>\<债务人夹>\签收凭证\`——与其他材料分开、一眼能找到。
   文件名保留原名（加时间前缀防重名）。

2. **识别结果绝不自动写台账**（遵守「工作台助理只读」同源纪律）：
   OCR 只负责**把图上的字读出来 + 提炼成结构化字段**，然后**交人确认**。
   台账受控回写仍走原有 `/api/letter/update`（三重闸），本模块**不碰台账**。

3. **零外发**：一律调本机 `ocr_ops._engine_text`（OpenVINO 本地引擎）。
   案卷材料 + 快递面单含当事人姓名地址 → **绝不线上**。

## 提炼哪些字段

从 OCR 文本里正则捞（都**可能识别不全**，所以返回 raw + 置信提示）：
- `ems_no`：EMS 单号（13 位数字，常见前缀 1/9/5，或「EMS」后跟数字串）
- `sign_time`：签收时间（2026-09-18 12:34 / 2026年9月18日 等）
- `signer`：签收人（本人 / 代收 / 家人代收 / 村邮站代收…）
- `status`：投递状态关键词（已签收 / 投递中 / 待取件 / 退回…）

用法：
  python ems_ops.py --selftest
  python ems_ops.py --extract-file <图片路径>          # 只识别+提炼（不落盘）
  python ems_ops.py --import-file <图片> --folder <债务人夹>   # 复制进材料夹
"""
import argparse
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ocr_ops            # noqa: E402
import case_ops           # noqa: E402


VERSION = '1.0.0'
# 存放子目录名 —— **跟随爸爸既有习惯**（批 46.2 修正）。
# ⚠️ 教训：初版我自造了 `签收凭证`，但爸爸实际早就在用 `律师函及签收记录`
#    （证据：`海大\律师函-福建海大VS陈飞鹏\律师函及签收记录\` 内有
#     「X律师函（签收）.pdf」+「X签收.jpg」成对文件）。
#    **定目录约定前必须先扫既有结构，别另造名词。**
EMS_SUBDIR = '律师函及签收记录'
EMS_SUBDIR_LEGACY = ('签收凭证',)   # 兼容：若某夹已建过旧目录，读取时也认

IMG_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff', '.pdf')


# ---------------------------------------------------------------- 路径
def proof_dir(folder_name):
    """债务人夹 → 签收记录目录（**优先既有**，不在就返回标准名待建）。

    查找顺序：① `<夹>\律师函及签收记录`（爸爸既有习惯）② `<夹>\签收凭证`（旧版）
              ③ 都没有 → 返回标准名（导入时创建）
    """
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    base = os.path.join(root, str(folder_name))
    for name in (EMS_SUBDIR,) + tuple(EMS_SUBDIR_LEGACY):
        d = os.path.join(base, name)
        if os.path.isdir(d):
            return d
    return os.path.join(base, EMS_SUBDIR)


def find_sign_records(folder_name):
    r"""列出某债务人夹下**所有**签收记录目录里的文件（跨层级找）。

    为什么不在固定深度找：实际结构层级不统一——
      `海大\律师函-福建海大VS陈飞鹏\律师函及签收记录\X签收.jpg`（两级）
    故用**目录名匹配**（含「签收」二字）而非固定路径。
    返回 [{'dir','name','rel','size_kb','mtime','kind'}]，kind ∈ {图片, PDF, 其他}
    """
    folders = case_ops.letter_folders()
    root = folders.get('root') or ''
    base = os.path.join(root, str(folder_name))
    out = []
    if not os.path.isdir(base):
        return out
    for dp, dn, fn in os.walk(base):
        dname = os.path.basename(dp)
        if ('签收' not in dname) and (dname not in EMS_SUBDIR_LEGACY):
            # 也认顶层直接放的「X签收.jpg」（有些夹平铺）
            if dp != base:
                continue
        for f in sorted(fn):
            fp = os.path.join(dp, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.webp')):
                kind = '图片'
            elif f.lower().endswith('.pdf'):
                kind = 'PDF'
            else:
                kind = '其他'
            out.append({'dir': os.path.relpath(dp, root), 'name': f,
                        'rel': os.path.relpath(fp, base).replace(os.sep, '\\'),
                        'size_kb': round(st.st_size / 1024, 1),
                        'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d'),
                        'kind': kind})
    return out


def is_image(name):
    return str(name or '').lower().endswith(IMG_EXT)


# ---------------------------------------------------------------- 提炼
def extract_fields(text):
    """OCR 文本 → 结构化字段（可能部分为空，调用方须交人确认）。

    返回 {ems_no, sign_time, signer, status, hits:{字段:原文片段}}
    「宁缺勿错」：抓不到就是空字符串，绝不猜。
    """
    t = str(text or '')
    flat = re.sub(r'\s+', ' ', t)
    out = {'ems_no': '', 'sign_time': '', 'signer': '', 'status': '', 'hits': {}}

    # ① EMS 单号：13 位数字（EMS 标准）；也容忍 OCR 把空格/连字符插进数字串
    if not out['ems_no']:
        m = re.search(r'(?<!\d)(\d{13})(?!\d)', flat)
        if m:
            out['ems_no'] = m.group(1)
            out['hits']['ems_no'] = m.group(0)
    if not out['ems_no']:
        # 被分隔（如 113 8294 7561 2）→ 去分隔符后再找 13 位
        squashed = re.sub(r'[ \-\u2010-\u2015]', '', flat)
        m = re.search(r'(?<!\d)(\d{13})(?!\d)', squashed)
        if m:
            out['ems_no'] = m.group(1)
            out['hits']['ems_no'] = m.group(0) + '（去分隔后）'

    # ② 签收时间：多种写法
    pats = [
        r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(\d{1,2}[:：]\d{2})?',
        r'(\d{1,2})[-/.月](\d{1,2})日?\s*(\d{1,2}[:：]\d{2})',
    ]
    for p in pats:
        m = re.search(p, flat)
        if m:
            out['sign_time'] = m.group(0).strip()
            out['hits']['sign_time'] = m.group(0)
            break

    # ③ 签收人 / 代收情况（关键词命中，保留原句片段）
    for kw in ('本人签收', '本人', '代收', '家人代收', '村邮站代收', '门卫代收',
               '快递柜', '已签收', '未签收', '待取件', '投递中', '退回'):
        if kw in flat:
            i = flat.find(kw)
            seg = flat[max(0, i - 12):i + 14]
            if not out['signer'] or '代收' in kw or '本人' in kw:
                out['signer'] = kw
                out['hits']['signer'] = seg
            break
    # 状态：优先「已签收/未签收/待取件/投递中/退回」
    for kw in ('已签收', '未签收', '待取件', '投递中', '退回', '派送中'):
        if kw in flat:
            out['status'] = kw
            break
    # 签收人姓名：住手！姓名极易误抓（一段文字里全是词）→ 只从
    # 「签收人：X」「代收人：X」这类**带标签**的位置取，宁缺勿错。
    m = re.search(r'(?:签收人|代收人|收件人)[:：]\s*([\u4e00-\u9fa5]{2,4})', flat)
    if m:
        out['signer_name'] = m.group(1)
        out['hits']['signer_name'] = m.group(0)
    return out


def suggest_folder(img_name, ocr_text, folders, letter_items=None):
    r"""按「文件名 + OCR 文本」猜测该存哪个债务人夹（**只给建议，不自动落盘**）。

    实测依据（批 46.2，用真实签收图 `林海明签收.jpg` 跑 OCR）：
      · 截图里**没有**当事人姓名（只有快递轨迹 + 地名）→ 姓名得从**文件名**取
      · OCR 能认出 **单号 / 签收时间 / 签收方式**（置信度 0.93，Grade A）
      · ⚠️ **但夹名 ≠ 当事人名**！实踩：`林海明签收.jpg` 匹配不到「海大」夹，
        因为「林海明」是**保证人**、夹名是委托人侧简称。正解 = **借台账桥接**：
        文件名里的名字 → 在哪一行的「收函对象」里出现 → 取那行的 matched_folder。

    匹配优先级：
      ① 文件名中的当事人 → 台账「收函对象」命中行 → 该行 matched_folder（最可靠）
      ② 文件名直接含夹名（如「杨记水产签收.jpg」→「杨记水产」夹）
      ③ OCR 文本含夹名（≥3 字，降低误报）
    单号/时间**不做匹配依据**（只用于辅助核对）。

    返回 {'folder': 建议夹名或 ''，'why': 理由，'via': 'ledger'|'filename'|'ocr'}
    """
    name = str(img_name or '')
    text = str(ocr_text or '')
    fnames = [str(f.get('name') or '').strip() for f in (folders or [])]
    fnames = [x for x in fnames if len(x) >= 2]

    # ---- ① 借台账桥接：文件名里的名字 → 收函对象命中行 → 该行夹 ----
    if letter_items:
        # 人名候选：从文件名剥离常见后缀前的部分（「林海明签收.jpg」→「林海明」）
        stem = name
        for suf in ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.pdf'):
            if stem.lower().endswith(suf):
                stem = stem[:-len(suf)]
        for kw in ('签收', 'ems', 'EMS', '快递', '妥收', '回单', '凭证'):
            stem = stem.replace(kw, '')
        stem = stem.strip(' _-（）()')
        # 用「后缀碎片」逐个试（名字可能是 2-4 字，取长度 ≥2 的连续片段）
        cands = [stem] if stem else []
        if len(stem) >= 3:
            cands += [stem[:3], stem[:2]]
        for c in cands:
            if len(c) < 2:
                continue
            for it in letter_items:
                obj = str(it.get('收函对象') or '')
                if c and c in obj:
                    mf = str(it.get('matched_folder') or '').strip()
                    if mf:
                        return {'folder': mf, 'via': 'ledger',
                                'why': '文件名「%s」↔ 台账第 %s 行收函对象含该名 → 夹「%s」'
                                       % (c, it.get('序号') or it.get('row'), mf)}
    # ---- ② 文件名直接含夹名 ----
    best, best_len = '', 0
    for f in fnames:
        if f in name and len(f) > best_len:
            best, best_len = f, len(f)
    if best:
        return {'folder': best, 'via': 'filename', 'why': '文件名含夹名「%s」' % best}
    # ---- ③ OCR 文本含夹名（≥3 字）----
    for f in fnames:
        if len(f) >= 3 and f in text:
            return {'folder': f, 'via': 'ocr', 'why': 'OCR 文本含夹名「%s」' % f}
    return {'folder': '', 'via': '', 'why': '未能自动匹配（请手动选择）'}


def summarize(fields):
    """提炼结果 → 一句大白话（给人看的，可直接参考填台账）"""
    bits = []
    if fields.get('ems_no'):
        bits.append('EMS单号 %s' % fields['ems_no'])
    if fields.get('sign_time'):
        bits.append('时间 %s' % fields['sign_time'])
    who = fields.get('signer_name') or ''
    st = fields.get('status') or fields.get('signer') or ''
    if who or st:
        bits.append('%s%s' % (who, st))
    return '，'.join(bits) if bits else ''


# ---------------------------------------------------------------- 识别
def recognize(img_path):
    """对一张图跑本地 OCR 并提炼。返回 {ok, text, fields, summary, error}"""
    p = os.path.abspath(str(img_path or ''))
    if not os.path.isfile(p):
        return {'ok': False, 'error': '图片不存在'}
    if not is_image(p):
        return {'ok': False, 'error': '只支持图片/PDF：%s' % ', '.join(IMG_EXT)}
    if not ocr_ops.available():
        return {'ok': False, 'error': '本地 OCR 引擎不可用（检查 D:/ai-models/paddleocr_ov）'}
    try:
        res = ocr_ops._engine_text(p, use_cache=True)
    except Exception as e:                                   # noqa: BLE001
        return {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
    if not res.get('ok'):
        return {'ok': False, 'error': res.get('error') or '识别失败'}
    text = res.get('text') or ''
    fields = extract_fields(text)
    return {'ok': True, 'text': text, 'chars': len(text),
            'seconds': res.get('seconds', 0), 'cached': res.get('cached', False),
            'fields': fields, 'summary': summarize(fields)}


# ---------------------------------------------------------------- 存放
def plan_import(img_path, folder_name):
    """导入预览：目标路径 + 是否重名（不落盘）"""
    p = os.path.abspath(str(img_path or ''))
    if not os.path.isfile(p):
        return {'ok': False, 'error': '图片不存在'}
    if not str(folder_name or '').strip():
        return {'ok': False, 'error': '缺债务人夹名'}
    d = proof_dir(folder_name)
    name = os.path.basename(p)
    dst = os.path.join(d, name)
    return {'ok': True, 'src': p, 'dst_dir': d, 'dst': dst,
            'exists': os.path.isfile(dst),
            'new_name': (datetime.now().strftime('%Y%m%d-%H%M-') + name) if os.path.isfile(dst) else name}


def apply_import(img_path, folder_name):
    """复制图片进 签收凭证 目录（**移动不可逆，故一律用复制**，原图不动）"""
    pl = plan_import(img_path, folder_name)
    if not pl.get('ok'):
        return pl
    os.makedirs(pl['dst_dir'], exist_ok=True)
    dst = pl['dst'] if not pl['exists'] else os.path.join(pl['dst_dir'], pl['new_name'])
    try:
        shutil.copy2(pl['src'], dst)
    except Exception as e:                                   # noqa: BLE001
        return {'ok': False, 'error': '复制失败：%s' % e}
    return {'ok': True, 'dst': dst, 'renamed': pl['exists']}


# ---------------------------------------------------------------- CLI
def selftest():
    total, fails = [], []

    def chk(name, cond):
        total.append(name)
        print(('  PASS ' if cond else '  FAIL ') + name)
        if not cond:
            fails.append(name)

    # -- extract_fields：EMS 单号（EMS 标准 13 位）
    f = extract_fields('您的快递已签收，EMS单号 1138294756123，签收人：张三')
    chk('单号 13 位', f['ems_no'] == '1138294756123')
    f2 = extract_fields('运单号 113 8294 7561 23 已签收')
    chk('带空格分隔的单号也能抓', f2['ems_no'] == '1138294756123')
    f2b = extract_fields('单号 11382947561')          # 11 位 = 残缺，不该当单号
    chk('位数不足不误抓', f2b['ems_no'] == '')
    f3 = extract_fields('今天天气不错')
    chk('无单号 → 空（不猜）', f3['ems_no'] == '')

    # -- 时间
    f4 = extract_fields('2026-09-18 12:34 已签收')
    chk('时间 ISO 格式', f4['sign_time'].startswith('2026-09-18'))
    f5 = extract_fields('2026年9月18日 已签收')
    chk('时间中文格式', '2026年9月18日' in f5['sign_time'])

    # -- 状态/签收方式
    chk('已签收', extract_fields('已签收').get('status') == '已签收')
    chk('代收', extract_fields('村邮站代收').get('status') or '代收' in str(extract_fields('村邮站代收')))
    chk('未签收', extract_fields('未签收').get('status') == '未签收')

    # -- 姓名只从带标签处取（防误抓）
    f6 = extract_fields('签收人：张三  已签收')
    chk('带标签姓名可抓', f6.get('signer_name') == '张三')
    f7 = extract_fields('张三李四王五都说这个快递不错，已签收')
    chk('无标签不乱抓姓名', not f7.get('signer_name'))

    # -- summarize
    s = summarize({'ems_no': '1138294756123', 'sign_time': '2026-09-18', 'status': '已签收'})
    chk('summary 拼接', '1138294756123' in s and '已签收' in s)

    # -- 路径与文件类型
    # -- 存放目录跟随既有习惯（批 46.2 修正：原自造「签收凭证」→ 改「律师函及签收记录」）
    chk('EMS_SUBDIR 为既有习惯名', EMS_SUBDIR == '律师函及签收记录')
    chk('兼容旧目录名', '签收凭证' in EMS_SUBDIR_LEGACY)
    chk('proof_dir 以既有习惯名结尾', proof_dir('__不存在的夹__').endswith(EMS_SUBDIR))

    # -- suggest_folder：三级匹配（台账桥接 / 文件名 / OCR），且不猜
    _fl = [{'name': '海大'}, {'name': '杨记水产'}]
    _items = [{'序号': 2, '收函对象': '陈飞鹏、林海明 + 保证人徐俊帆', 'matched_folder': '海大'},
              {'序号': 9, '收函对象': '杨记水产、杨建忠', 'matched_folder': '杨记水产'}]
    r = suggest_folder('林海明签收.jpg', '', _fl, _items)
    chk('台账桥接：保证人名也能找到夹', r['folder'] == '海大' and r['via'] == 'ledger')
    r = suggest_folder('杨记水产签收.jpg', '', _fl, _items)
    chk('台账桥接：杨记水产', r['folder'] == '杨记水产')
    r = suggest_folder('海大-某图.jpg', '', _fl, None)
    chk('文件名直含夹名（无台账时）', r['folder'] == '海大' and r['via'] == 'filename')
    r = suggest_folder('x.jpg', '本件属于杨记水产的函', _fl, None)
    chk('OCR 文本含夹名', r['folder'] == '杨记水产' and r['via'] == 'ocr')
    r = suggest_folder('完全无关.jpg', '', _fl, _items)
    chk('匹配不到 → 空（不猜）', r['folder'] == '')
    r = suggest_folder('海大签收.jpg', '', [{'name': '海'}, {'name': '海大'}], _items)
    chk('夹名 <2 字不参与匹配', r['folder'] == '海大')
    chk('find_sign_records 不存在夹 → 空', find_sign_records('__不存在的夹__') == [])
    chk('is_image 认 png', is_image('a.png') and not is_image('a.docx'))

    # -- plan_import 缺参数守门
    chk('缺文件夹名被拦', plan_import(__file__, '')['ok'] is False)
    chk('不存在的图片被拦', plan_import('D:/__nope__.png', '杨记水产')['ok'] is False)

    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def main():
    ap = argparse.ArgumentParser(description='EMS 签收凭证：存放/识别/提炼')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--extract-file', metavar='IMG', help='识别图片并提炼字段（不落盘）')
    ap.add_argument('--import-file', metavar='IMG', help='复制图片进材料夹')
    ap.add_argument('--folder', help='债务人夹名（--import-file 用）')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.extract_file:
        r = recognize(args.extract_file)
        if not r.get('ok'):
            print('识别失败：%s' % r.get('error'))
            return 3
        print('字符数：%d（缓存 %s，%.1fs）' % (r['chars'], r['cached'], r['seconds']))
        print('提炼：%s' % (r['summary'] or '（没提到可用字段，需人工看图）'))
        for k, v in (r.get('fields') or {}).items():
            if k != 'hits' and v:
                print('  %-12s %s' % (k, v))
        return 0
    if args.import_file:
        if not args.folder:
            print('缺 --folder（债务人夹名）')
            return 2
        r = apply_import(args.import_file, args.folder)
        print(('已存入：%s' % r['dst']) if r.get('ok') else ('失败：%s' % r.get('error')))
        return 0 if r.get('ok') else 3
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
