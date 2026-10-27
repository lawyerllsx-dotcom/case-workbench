#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
case_ops.py — 案卷操作库（法律工作台数据层 · 单一实现）

定位：Web 操作台与煤球会话**共用**的案卷操作库。三件事：立卷 / 收件箱入库 / 结案归档。
纪律（写死在代码里）：
  - **一切批量动作默认 dry-run**，显式 --apply 才执行
  - **纯移动不删除**：本库不含任何删除分支；冲突（目标同名）→ 跳过并报告，绝不覆盖
  - 只操作白名单目录（案件夹内 00-10 / 需盖章文件 / pipeline），拒绝越界路径
  - 入库改名登记该案 `intake-log.md`（原名 ↔ 新名 ↔ 移动路径，可回溯）
  - 立卷复用 `new_case_pipeline.py`（不重复实现 pipeline 脚手架）

依赖：标准库；同名目标冲突用哈希比对可判定"同文件重复"。
用法（Windows 路径）：
  python case_ops.py classify --name "起诉状（草稿）(1).docx"
  python case_ops.py new --case-no 民-A-26016 --client 盐城海维 --opponent 曹琪立等 --cause 买卖合同纠纷
  python case_ops.py intake --case "D:\案件\你的在办案卷\民-A-26016_..."          # dry-run
  python case_ops.py intake --case "<案件夹>" --apply
  python case_ops.py archive --case "<案件夹>" --year 2026 [--apply]
  python case_ops.py tree                                                          # 目标态目录树
  python case_ops.py selftest

退出码：0=正常 2=输入/冲突/锁 3=执行异常
"""
import argparse
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_ops        # noqa: E402  （台账只读：案件夹↔台账行匹配用）
import re
import shutil
import subprocess
import sys
from datetime import datetime

if os.environ.get('_WB_STDOUT_UTF8') != '1':      # 幂等：多模块同进程重复包裹会关掉缓冲区
    os.environ['_WB_STDOUT_UTF8'] = '1'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VERSION = '1.0.0'

import config as _config        # 开源版：案卷根目录全部外置为配置

DEFAULT_CASE_ROOT = _config.path('paths.case_root', env='CASE_OPS_ROOT')
DEFAULT_ARCHIVE_ROOT = _config.path('paths.archive_root', env='CASE_OPS_ARCHIVE')
PIPELINE_SCRIPT = os.environ.get('CASE_PIPELINE_SCRIPT', '')   # 可选：外部立卷脚本，未配置即跳过

TREE_BASE = ['00-收件箱', '需盖章文件', '01-委托手续', '02-法律文书', '03-证据材料',
             '04-法院文件', '05-费用票据', '06-当事人信息', '07-律师函', '08-过程文件']
TREE_EXEC = ['09-财产查控', '10-处置分配']      # 执行案追加
INBOX = '00-收件箱'
PIPELINE_DIR = 'pipeline'
ALLOWED_DIRS = set(TREE_BASE + TREE_EXEC + [INBOX, PIPELINE_DIR, '需盖章文件'])

CASE_NO_RE = re.compile(r'^(民|执|仲)-[A-Z]-(\d{4,6})$')
ILLEGAL = set('\\/:*?"<>|')

# 路由表：按序匹配（先具体后泛化）；类别名 = 目录去掉编号前缀
ROUTES = [
    ('08-过程文件', ['草稿', '旧版', '初稿', 'redline', '修订版', '副本', 'copy', '备份', 'bak']),
    ('09-财产查控', ['查控', '限高', '失信', '终本', '财产报告', '冻结', '续封', '查封回执']),
    ('10-处置分配', ['拍卖', '变卖', '评估报告', '分配方案', '以物抵债', '处置']),
    ('07-律师函', ['律师函', '催告函', '签收记录']),
    ('01-委托手续', ['授权委托书', '授权书', '所函', '委托合同', '委托手续', '法律援助',
                     '法定代表人身份证明', '法代身份', '证据清单']),
    # 批 80 补词（2026-10-21 一张网立案送达包 6 件实踩 4 件不认）：受理案件通知（「受理通知」
    # 被「案件」二字截断整串不命中）/ 廉政监督卡 / 诉讼风险提示 / 权利义务告知；
    # 交纳诉讼费用通知书属法院文书 → 归 04（05-费用票据只收发票/收据/缴费凭证等「票据」正本）
    ('04-法院文件', ['判决书', '裁定书', '调解书', '受理通知', '受理案件通知', '传票', '应诉通知',
                     '举证通知', '送达回证', '公告', '立案通知', '执行通知', '结案通知', '缴纳通知',
                     '廉政监督卡', '诉讼风险提示', '权利义务告知',
                     '交纳诉讼费', '预交诉讼费', '诉讼费交纳']),
    ('05-费用票据', ['发票', '诉讼费', '保全费', '缴费', '收据', '专票', '普票']),
    ('06-当事人信息', ['身份证', '户籍', '结婚证', '离婚证', '营业执照', '户口', '居住证明']),
    ('02-法律文书', ['起诉状', '答辩状', '上诉状', '代理词', '代理意见', '申请书', '强执',
                     '强制执行', '保全申请', '法律服务方案', '法律意见', '管辖异议', '反诉']),
    ('03-证据材料', ['合同', '协议', '对账单', '对帐单', '担保', '送货单', '欠条', '借条',
                     '催款函', '回执', '物流', '磅单', '结算单', '微信记录', '录音']),
]
DOC_EXT = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.wps'}
KEEP_NAME_PAT = re.compile(
    r'^(微信图片|微信截图|mmexport|Screenshot|屏幕截图|IMG_|SKM_|DSC|PXL_|photo|图片\d|'
    r'\d{8}_\d{6}|VID_|video)', re.I)
IMAGE_VIDEO_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic', '.mp4', '.mov', '.avi'}
ARCHIVE_SUFFIX_RE = re.compile(r'(\s*[\(（]\d+[\)）]|\s*-?\s*(副本|copy|副本\(\d+\)))+$', re.I)
DATE_IN_NAME_RE = re.compile(r'(20\d{2})[.\-年]?(\d{1,2})[.\-月]?(\d{1,2})|(20\d{2})(\d{2})(\d{2})')


# ---------------------------------------------------------------- 环境

def case_root():
    return os.environ.get('CASE_OPS_ROOT', DEFAULT_CASE_ROOT)


def archive_root():
    return os.environ.get('CASE_OPS_ARCHIVE', DEFAULT_ARCHIVE_ROOT)


def _guard_in_case(case_dir, allow_sub=False):
    """校验 case_dir 位于 case_root 下（防越界操作）。
    默认＝第一层案件夹；allow_sub=True 额外允许二级「总夹/子案件夹」
    （批 71：系列案子案件夹的收件箱入库——只收两级，再深仍拒）。"""
    case_dir = os.path.abspath(case_dir)
    root = os.path.abspath(case_root())
    if not case_dir.startswith(root + os.sep):
        print('输入错误：案件夹必须位于 %s 之下 → %s' % (root, case_dir))
        sys.exit(2)
    rel = os.path.relpath(case_dir, root)
    ok = rel not in ('.', '..') and not rel.startswith('..') and (
        os.sep not in rel or (allow_sub and rel.count(os.sep) == 1))
    if not ok:
        print('输入错误：%s（收到 %s）' % (
            '--case 须指向第一层案件夹或「总夹/子案件夹」两段' if allow_sub
            else '--case 必须指向第一层案件夹', rel))
        sys.exit(2)
    if not os.path.isdir(case_dir):
        print('输入错误：案件夹不存在 → %s' % case_dir)
        sys.exit(2)
    return case_dir


# ---------------------------------------------------------------- 命名规范 v2

def case_folder_name(case_no, client, opponent, cause):
    """民-A-26016_闽兴VS对方_案由"""
    case_no = (case_no or '').strip()
    if not CASE_NO_RE.match(case_no):
        raise ValueError('案件编号格式应为 民-A-26NNN / 执-… / 仲-…（收到 %r）' % case_no)
    parts = [(client or '').strip(), (opponent or '').strip(), (cause or '').strip()]
    if not all(parts):
        raise ValueError('委托人 / 对方 / 案由 三项均不能为空')
    for p in [case_no] + parts:
        if set(p) & ILLEGAL or '..' in p:
            raise ValueError('含非法字符（\\ / : * ? " < > | ..）：%r' % p)
    return '%s_%sVS%s_%s' % (case_no, parts[0], parts[1], parts[2])


def _file_date(path, stem):
    m = DATE_IN_NAME_RE.search(stem)
    if m:
        if m.group(1):
            return '%04d%02d%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return '%s%s%s' % (m.group(4), m.group(5), m.group(6))
    if not os.path.isfile(path):
        return datetime.now().strftime('%Y%m%d')   # 仅按文件名试算（文件尚未落盘）
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y%m%d')


def classify(path):
    """单文件归类建议 → dict(dir/newname/keep/status/reason)"""
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    ext_l = ext.lower()
    target, reason = None, ''
    for d, keys in ROUTES:
        hit = next((k for k in keys if k in stem), None)
        if hit:
            target, reason = d, '关键词「%s」' % hit
            break
    keep = bool(KEEP_NAME_PAT.match(stem))
    if target is None and ext_l in IMAGE_VIDEO_EXT:
        keep = True
        target, reason = '03-证据材料', '图片/视频杂件（无关键词，默认归证据，dry-run 清单内可人工改判）'
    if target is None:
        return {'dir': None, 'newname': None, 'keep': True, 'status': '需人工',
                'reason': '无关键词命中，保持原位待人工判定'}
    if keep:
        return {'dir': target, 'newname': name, 'keep': True, 'status': '建议',
                'reason': reason + '；杂件保持原名（Q3）'}
    desc = ARCHIVE_SUFFIX_RE.sub('', stem).strip() or stem
    category = target.split('-', 1)[1]
    if ext_l not in DOC_EXT and ext_l not in IMAGE_VIDEO_EXT:
        return {'dir': target, 'newname': name, 'keep': True, 'status': '建议',
                'reason': reason + '；非常规文档扩展名，保持原名'}
    newname = '%s_%s_%s%s' % (_file_date(path, stem), category, desc, ext)
    return {'dir': target, 'newname': newname, 'keep': False, 'status': '建议',
            'reason': reason}


def suggest_dir(filename):
    """批 72：**仅按文件名**给归类目录建议（供「下载即归位」预览；不做改名计算）。

    与 classify() 同源（同一张 ROUTES 表）；认不准返回 dir=''（落收件箱待核对）。
    """
    stem = os.path.splitext(os.path.basename(str(filename or '')))[0]
    for d, keys in ROUTES:
        hit = next((k for k in keys if k in stem), None)
        if hit:
            return {'dir': d, 'reason': '关键词「%s」' % hit}
    return {'dir': '', 'reason': '无关键词命中，落收件箱待核对'}


def _sha16(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 入库

def plan_intake(case_dir, allow_sub=False):
    """扫 00-收件箱 → 归位建议清单（不改盘）。allow_sub：允许「总夹/子案件夹」两段（批 71）。"""
    case_dir = _guard_in_case(case_dir, allow_sub=allow_sub)
    inbox = os.path.join(case_dir, INBOX)
    plan = {'case_dir': case_dir, 'inbox_exists': os.path.isdir(inbox),
            'items': [], 'conflicts': [], 'manual': []}
    if not plan['inbox_exists']:
        return plan
    for root, _dirs, files in os.walk(inbox):
        for fn in sorted(files):
            if fn.startswith('~$') or fn == 'intake-log.md':
                continue
            if fn.startswith('_'):
                # 批 41.3：_ 开头的说明/留痕文件（_散件入库.md/_材料怎么放.md/_归集留痕.md 等）
                # 本来就该留在收件箱，不是入库对象——不进扫描清单（真实使用反馈：混在清单里是噪音）
                continue
            src = os.path.join(root, fn)
            c = classify(src)
            item = {'src': src, 'name': fn, 'dir': c['dir'], 'newname': c['newname'],
                    'status': c['status'], 'reason': c['reason'], 'dst': None}
            if c['dir']:
                d = os.path.join(case_dir, c['dir'])
                item['dst'] = os.path.join(d, c['newname'])
                if not os.path.isdir(d):
                    # 批 41.4：目标子目录不存在 → 确认归位时**自动创建**（apply 的 makedirs 已就绪），
                    # 不再拦为「缺目录」——真实使用反馈：黎林培案 3 件律师函全因缺 07-律师函 被跳过，
                    # 而建目录本就是入库的本职（v2 目录树规范内）。
                    item['reason'] += '；目标目录不存在，确认归位时将自动创建'
                elif os.path.exists(item['dst']):
                    same = (os.path.isfile(item['dst'])
                            and os.path.getsize(item['dst']) == os.path.getsize(src)
                            and _sha16(item['dst']) == _sha16(src))
                    item['status'] = '重复(同内容)' if same else '冲突(同名不同内容)'
                    item['reason'] += '；目标已存在'
                    plan['conflicts'].append(item)
                else:
                    # 批 76：-2 避让后缀剥除后命中同内容正本 → 判重复（同 auto_file_names 修复）
                    m76 = re.match(r'^(.*?)-\d+(\.[^.]+)$', item['newname'] or '')
                    if m76:
                        _fold = os.path.join(d, m76.group(1) + m76.group(2))
                        if (os.path.isfile(_fold)
                                and os.path.getsize(_fold) == os.path.getsize(src)
                                and _sha16(_fold) == _sha16(src)):
                            item['status'] = '重复(同内容)'
                            item['reason'] += '；案卷已有同内容文书，无需重复归位'
                            plan['conflicts'].append(item)
            if item['status'] in ('需人工', '冲突(同名不同内容)'):
                plan['manual'].append(item)
            plan['items'].append(item)
    return plan


def apply_intake(case_dir, allow_sub=False):
    """执行归位（仅移动建议项；冲突/需人工一律跳过）+ 追加 intake-log。allow_sub 同 plan_intake。"""
    plan = plan_intake(case_dir, allow_sub=allow_sub)
    moved, skipped = [], []
    for it in plan['items']:
        if not it['dst'] or it['status'] not in ('建议',):
            skipped.append(it)
            continue
        try:
            os.makedirs(os.path.dirname(it['dst']), exist_ok=True)
            shutil.move(it['src'], it['dst'])
            moved.append(it)
        except Exception as e:
            it['status'] = '执行失败'
            it['reason'] = repr(e)
            skipped.append(it)
    if moved:
        _append_intake_log(plan['case_dir'], moved)
    return plan, moved, skipped


def _append_intake_log(case_dir, moved):
    p = os.path.join(case_dir, 'intake-log.md')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if not os.path.isfile(p):
        with open(p, 'w', encoding='utf-8') as f:
            f.write('# intake-log — %s\n\n> 入库留痕：原名 ↔ 新名 ↔ 移动路径（Q3 折中制）。'
                    '由 case_ops.py 追加，**只追加不删除**。\n\n'
                    '| 时间 | 原名 | 新名 | 归入 | 方式 |\n|---|---|---|---|---|\n'
                    % os.path.basename(case_dir))
    with open(p, 'a', encoding='utf-8') as f:
        for it in moved:
            src_rel = os.path.relpath(it['src'], case_dir)
            dst_rel = os.path.relpath(it['dst'], case_dir)
            way = '保持原名' if it['name'] == it['newname'] else '语义化改名'
            f.write('| %s | %s | %s | %s | %s |\n' % (now, it['name'], it['newname'],
                                                     dst_rel, way))
    return p


def auto_file_names(case_dir, names, allow_sub=False):
    """批 72：**下载即归位**——把指定的几件（刚下载的文件）按「扫描→确认归位」同一套
    分类器直接归位；收件箱里**其余文件一律不动**。

    与扫描流程同约定：目标目录缺失自动创建；同名目标已存在时——同内容（大小+sha16）
    判「重复」、异内容判「冲突」，均**留在收件箱待人工**（不硬改名不覆盖）。
    返回 {'filed': [{'name','dir','newname'}], 'left': [{'name','reason'}]}；留痕进 intake-log。
    """
    case_dir = _guard_in_case(case_dir, allow_sub=allow_sub)
    inbox = os.path.join(case_dir, INBOX)
    filed, left, moved_for_log = [], [], []
    for name in names:
        src = os.path.join(inbox, name)
        if not os.path.isfile(src):
            left.append({'name': name, 'reason': '不在收件箱（可能已被移动）'})
            continue
        c = classify(src)
        if c['status'] != '建议' or not c['dir']:
            left.append({'name': name, 'reason': c['reason'] or '认不准，留在收件箱待核对'})
            continue
        tdir = os.path.join(case_dir, c['dir'])
        newname = c['newname'] or name
        dst = os.path.join(tdir, newname)
        if os.path.isfile(dst):
            try:
                same = (os.path.getsize(dst) == os.path.getsize(src)
                        and _sha16(dst) == _sha16(src))
            except OSError:
                same = False
            left.append({'name': name,
                         'reason': '案卷已有同内容文书，无需重复归位' if same
                                   else '目标同名不同内容（冲突）'})
            continue
        # 批 76：新名带「-2/-3」避让后缀（重复下载产物）时——剥后缀再试：
        # 案卷已有同内容正本 → 仍判重复（修复实踩：再点一次反而归位出 -2 重复副本）
        m76 = re.match(r'^(.*?)-\d+(\.[^.]+)$', newname)
        if m76:
            dst_fold = os.path.join(tdir, m76.group(1) + m76.group(2))
            if os.path.isfile(dst_fold):
                try:
                    same_fold = (os.path.getsize(dst_fold) == os.path.getsize(src)
                                 and _sha16(dst_fold) == _sha16(src))
                except OSError:
                    same_fold = False
                if same_fold:
                    left.append({'name': name,
                                 'reason': '案卷已有同内容文书，无需重复归位'})
                    continue
        try:
            os.makedirs(tdir, exist_ok=True)
            shutil.move(src, dst)
            nb = os.path.basename(dst)
            filed.append({'name': name, 'dir': c['dir'], 'newname': nb})
            moved_for_log.append({'src': src, 'name': name, 'newname': nb, 'dst': dst})
        except Exception as e:                                # noqa: BLE001
            left.append({'name': name, 'reason': '移动失败：%r' % e})
    if moved_for_log:
        _append_intake_log(case_dir, moved_for_log)
    return {'filed': filed, 'left': left}


def print_plan(plan):
    print('=== 入库建议清单（%s）===' % plan['case_dir'])
    if not plan['inbox_exists']:
        print('  （无 00-收件箱 目录 → 先建立卷目录树）')
        return
    if not plan['items']:
        print('  （收件箱为空）')
        return
    for it in plan['items']:
        flag = {'建议': '  ', '需人工': '❗'}.get(it['status'], '❌')
        print('%s %-40s → %-12s %s' % (flag, it['name'][:40], it['dir'] or '（原位）',
                                       it['newname'] or ''))
        print('      理由: %s [%s]' % (it['reason'], it['status']))
    print('  小计：建议 %d / 需人工 %d / 冲突 %d' % (
        sum(1 for i in plan['items'] if i['status'] == '建议'),
        sum(1 for i in plan['items'] if i['status'] in ('需人工', '冲突(同名不同内容)')),
        len(plan['conflicts'])))


# ---------------------------------------------------------------- 立卷

GUIDE_NAME = '_材料怎么放.md'      # 案卷根级那份「材料存放规则」说明（不放进 00-收件箱，免得被入库扫描当待归类文件）

GUIDE_DESIGN = (
    '| 文件夹 | 放什么 | 例子 |\n|---|---|---|\n'
    '| 00-收件箱 | **所有新材料的入口**（还没分类的都丢这儿） | 微信收到的合同照片、扫描件、对方发来的文件 |\n'
    '| 需盖章文件 | 要送客户/公司盖章的定稿（Word/Excel） | 要素式起诉状送签稿、授权委托书送签稿 |\n'
    '| 01-委托手续 | 授权委托书、所函、委托合同（PDF） | 已盖章的授权委托书扫描件 |\n'
    '| 02-法律文书 | 起诉状、保全申请、强执申请（PDF） | 盖章版起诉状、财产保全申请书 |\n'
    '| 03-证据材料 | 合同、对账单、担保函（PDF） | 销售合同、对账确认单、担保函 |\n'
    '| 04-法院文件 | 受理通知、判决、裁定、传票 | 立案通知书、开庭传票 |\n'
    '| 05-费用票据 | 律师费/诉讼费/保全费发票 | 诉讼费缴费凭证 |\n'
    '| 06-当事人信息 | 身份证、户籍、结婚证 | 被告身份证正反面 |\n'
    '| 07-律师函 | 律师函及签收记录 | 催告函 + EMS 签收单 |\n'
    '| 08-过程文件 | 旧版草稿、redline、压缩包 | 起诉状 v1、带修改痕迹的稿 |\n'
)
GUIDE_DESIGN_EXEC = '| 09-财产查控 | 查控回执、限高、失信、终本裁定 | 冻结回执、终本裁定 |\n| 10-处置分配 | 评估、拍卖、分配方案 | 评估报告、分配方案 |\n'

# 旧目录 → 建议归入的标准目录（**只给建议，从不自动移动**）
OLD_DIR_MAP = [
    (('提交给法院的证据', '提交法院的证据', '证据材料', '证据类', '证据', '聊天记录', '保险材料'), '03-证据材料'),
    (('OCR识别底稿', 'OCR核验底稿', 'OCR底稿', 'OCR'), '08-过程文件'),
    (('仲裁文书', '起诉材料', '上诉材料', '答辩材料', '法律文书'), '02-法律文书'),
    (('委托材料', '委托手续'), '01-委托手续'),
    (('保全',), '02-法律文书（保全申请）＋ 04-法院文件（保全裁定）'),
    (('法院文件', '仲裁文件'), '04-法院文件'),
    (('票据', '发票', '费用'), '05-费用票据'),
    (('当事人', '身份'), '06-当事人信息'),
    (('律师函',), '07-律师函'),
    (('执行',), '02-法律文书（执行申请）＋ 04-法院文件（执行裁定·通知）'),
    (('参考',), '**保持原位**（参考资料不并进主卷，避免污染）'),
]
SERIES_RE = re.compile(r'(?:VS|vs|Vs|诉)|^(?:民|执|仲|行|刑|非|破)\s*[-_\s]')
NO_PARSE_RE = re.compile(r'^\s*((?:民|执|仲|行|刑|非|破)\s*[-_]\s*[A-Za-z]?\s*[-_]?\s*\d{4,6})\s*[_-]\s*(.+)$')
NO_PARSE_RE2 = re.compile(r'^\s*((?:民|执|仲|行|刑|非|破)\s*[-_]\s*[A-Za-z]?\s*[-_])\s*(.+)$')


def parse_folder_name(folder):
    """把案件夹名拆成 编号/当事人/案由（拆不出就留空，不猜）"""
    s, no = folder.strip(), ''
    m = NO_PARSE_RE.match(s) or NO_PARSE_RE2.match(s)
    if m:
        if NO_PARSE_RE.match(s):
            no, s = re.sub(r'\s+', '', m.group(1)), m.group(2)
        else:
            s = m.group(2)
    parts = [x for x in re.split(r'\s*[_-]\s*', s) if x]
    who = parts[0] if parts else ''
    cause = parts[-1] if len(parts) > 1 else ''
    mm = re.split(r'\s*(?:VS|vs|Vs|诉)\s*', who)
    return {'no': no, 'who': who, 'client': (mm[0] or '').strip(),
            'opponent': (mm[1] if len(mm) > 1 else '').strip(),
            'cause': cause if len(parts) > 1 else ''}


def suggest_dir_map(extra_dirs):
    """旧目录 → 建议归入（按序取第一个命中的规则）"""
    out = []
    for d in extra_dirs:
        for keys, target in OLD_DIR_MAP:
            if any(k in d for k in keys):
                out.append((d, target))
                break
        else:
            out.append((d, '**待人工判断**（含人名/日期，多半是子案件夹或历史归档，不要合并）'))
    return out


def case_dirs(case_dir):
    """本夹顶层目录（只读）"""
    try:
        return sorted([d for d in os.listdir(case_dir) if os.path.isdir(os.path.join(case_dir, d))])
    except OSError:
        return []


def is_series_case(case_dir, extra_dirs):
    """系列案总夹判定：夹名含「系列」或本夹下有 ≥2 个看起来就是案件名的目录"""
    if '系列' in os.path.basename(os.path.normpath(case_dir)):
        return True
    hits = [d for d in extra_dirs if SERIES_RE.search(d)]
    return len(hits) >= 2


def series_children(case_dir):
    """系列案总夹下的子夹体检（只读，不递归）"""
    out = []
    for d in case_dirs(case_dir):
        if d in ALLOWED_DIRS:
            continue
        p = os.path.join(case_dir, d)
        out.append({'name': d,
                    'inbox': os.path.isdir(os.path.join(p, INBOX)),
                    'log': os.path.isfile(os.path.join(p, 'intake-log.md')),
                    'guide': os.path.isfile(os.path.join(p, GUIDE_NAME))})
    return out


def inbox_guide_text(folder, case_no, client, opponent, cause, case_type='诉讼', case_root_path='',
                     real_dirs=None, extra_dirs=None, series=False, row=None, today=None, parent=None):
    """每案一份《材料怎么放》（案卷根级）。只写规则 + 本夹实况，不编案件事实。"""
    today = today or datetime.now().strftime('%Y-%m-%d %H:%M')
    is_exec = bool(case_type and '执行' in case_type)
    design = GUIDE_DESIGN + (GUIDE_DESIGN_EXEC if is_exec else '')
    full = tree_for(case_type)
    row = row or {}
    real_dirs = [d for d in (real_dirs if real_dirs is not None else case_dirs(case_root_path or '.'))]
    extra_dirs = extra_dirs if extra_dirs is not None else [d for d in real_dirs if d not in ALLOWED_DIRS]
    have = [d for d in full if d in real_dirs]
    lack = [d for d in full if d not in real_dirs]
    has_inbox = (INBOX in real_dirs) or os.path.isdir(os.path.join(case_root_path or '.', INBOX))
    has_log = os.path.isfile(os.path.join(case_root_path or '.', 'intake-log.md'))

    buf = []
    buf.append('# 这个案子的材料怎么放（自动生成，可随时改）\n')
    buf.append('> 案卷根：`%s`' % (case_root_path or ('案件根\\' + folder)))
    if parent:
        buf.append('> 所属系列案总夹：`%s`（本页只管**本子案**；新材料进本夹 `00-收件箱`）' % parent)
    _nm = row.get('案件') or ''
    if not _nm:
        # 拆不出「谁 VS 谁」时（如 '三明闽兴'）直接用文件夹名，不硬拼成「X与Y」
        _nm = ('%s与%s%s' % (client, opponent, cause)).strip('与-') if opponent else ''
    buf.append('> 案件：%s' % (_nm or folder))
    buf.append('> 所内编号：%s ｜ 类别：%s ｜ 案号：%s'
               % (case_no or row.get('序号') or '[待补]', case_type or '[待补]',
                  str(row.get('案号') or '').splitlines()[0] if row.get('案号') else '[待补]'))
    _cl = row.get('委托人') or (client if opponent else '')
    if _cl:
        buf.append('> 委托人：%s' % _cl)
    buf.append('> 生成：%s 由法律工作台生成\n' % today)

    buf.append('## 一句话规则\n')
    buf.append('**新材料一律先丢进 `00-收件箱`** → 回工作台点「收件箱入库」→ 扫描 → 确认归位。')
    buf.append('不要自己新建文件夹、不要往案卷根目录丢、**不要删原件**（只移动）。')
    if not has_inbox:
        buf.append('\n> ⚠️ 本夹**还没有 `00-收件箱`** —— 请让工作台补建，或在案卷根手工建一个再放材料。\n')
    else:
        buf.append('')

    if series:
        buf.append('## ⚠️ 这是「系列案」总夹\n')
        buf.append('本夹下面每个子文件夹 = 一个独立案件（如 `5.6福建闽兴VS林国伟`）。')
        buf.append('新材料请丢进**对应案件子夹**的 `00-收件箱`；还没定是哪个案子的，先丢本夹根级 `00-收件箱`，')
        buf.append('在工作台「收件箱入库」时注明属于哪个案子，别直接塞进某个子夹的正文目录。\n')

    buf.append('## 本夹现状（自动盘点，只读）\n')
    if series:
        kids = series_children(case_root_path or '.')
        no_inbox = [k for k in kids if not k['inbox']]
        buf.append('- 本夹是**系列案总夹**，下面有 **%d 个**子文件夹（每个 = 一个案件）。' % len(kids))
        buf.append('- 其中已建 `00-收件箱` 的：**%d 个**；子夹里已有《材料怎么放》的：**%d 个**。'
                   % (len(kids) - len(no_inbox), sum(1 for k in kids if k['guide'])))
        if no_inbox:
            buf.append('- 还没建 `00-收件箱` 的子夹（材料要放进去之前得先建这个口）：')
            for k in no_inbox[:14]:
                buf.append('  - `%s`' % k['name'])
            if len(no_inbox) > 14:
                buf.append('  - …等共 %d 个' % len(no_inbox))
        buf.append('- 本夹**根级**的标准目录建了 **%d / %d**：%s（总夹不放单案正文材料，其余按需再建）'
                   % (len(have), len(full), '、'.join(have) if have else '（无）'))
        buf.append('- 本夹根级 intake-log：%s；根级 00-收件箱：%s'
                   % ('已有' if has_log else '**缺失**', '已有' if has_inbox else '**缺失**'))
        kidset = [k['name'] for k in kids]
        other = [d for d in extra_dirs if d not in kidset]
        if other:
            buf.append('- 本夹另有这些目录（**不是**子案件夹）：\n')
            for d, target in suggest_dir_map(other):
                buf.append('  - `%s` → %s' % (d, target))
        buf.append('\n> 建议：给还没建 00-收件箱的子夹补建（工作台「开新案」的补件入口可批量做），')
        buf.append('> 但**不会自动搬动任何文件**，要归位请走「收件箱入库」一物一确认。\n')
    else:
        buf.append('- 标准目录已建 **%d / %d**：%s'
                   % (len(have), len(full), '、'.join(have) if have else '（一个都没建）'))
        if lack:
            buf.append('- 还没建：%s（材料真到了再建也行，不必现在补空目录）' % '、'.join(lack))
        buf.append('- intake-log：%s；00-收件箱：%s'
                   % ('已有' if has_log else '**缺失**', '已有' if has_inbox else '**缺失**'))
        if extra_dirs:
            buf.append('- 本夹另有这些目录（不在标准结构里）：\n')
            for d, target in suggest_dir_map(extra_dirs):
                buf.append('  - `%s` → %s' % (d, target))
            buf.append('\n> 以上只是**建议**，工作台不会自动搬动任何文件；要归位请走「收件箱入库」一物一确认。\n')
        else:
            buf.append('')

    buf.append('## 每个文件夹放什么\n')
    buf.append(design.rstrip('\n'))
    buf.append('| pipeline | P/R/E 三段式流水线产物（自动生成，不用手放） | — |\n')

    buf.append('## 命名怎么起（折中制）\n')
    buf.append('- **正式文书 / 证据扫描**：`YYYYMMDD_类别_描述.ext`，例 `20260902_文书_要素式起诉状v1.docx`')
    buf.append('- **聊天截图 / 手机照片等杂件**：保持原名（靠文件夹归类 + Everything 搜索）')
    buf.append('- 归位时工作台会给建议名，**你确认它才改名**；原名 ↔ 新名 自动记进 `intake-log.md`\n')

    buf.append('## 三条铁律\n')
    buf.append('1. **只移动、不删除**（确实要清理就移到 `D:\\备份\\01-清理前打包\\`）')
    buf.append('2. 同名但内容不同 → 不覆盖，标「冲突」留在原处等你判断')
    buf.append('3. 认不出放哪的 → 留在 `00-收件箱`，工作台会列成「需人工」\n')

    buf.append('## 台账在哪\n')
    buf.append('案件进展与状态写 WPS 台账（**唯一事实源**）。工作台「台账进展」页可直接改「当前进度」')
    buf.append('（旧值自动挪「进度日志」）与「最新状态」（只能选台账规定的取值）；改前自动备份。\n')

    buf.append('## 案卷自足\n')
    buf.append('P/R/E（读材料 → 定策略 → 写文书）的产物放本夹 `pipeline\\`，案件材料不离开本机。')
    return '\n'.join(buf) + '\n'


def build_guide_text(case_dir, exec_case=False, row=None, parent=None):
    """按本夹实况生成《材料怎么放》文本（页面/CLI 共用同一实现）

    parent 非空 = 本夹是「系列案总夹」下的子案件夹（level 2）：文本里标明归属，
    且**不再判定为系列案总夹**（它下面就是标准目录，不是子案容器）。
    """
    folder = os.path.basename(os.path.normpath(case_dir))
    if row is None:
        try:
            m = match_case_ledger(folder, ledger_case_rows())
            row = m.get('matched')
        except Exception:                                     # noqa: BLE001
            row = None
    row = row or {}
    info = parse_folder_name(folder)
    case_type = str(row.get('案件类别') or '') or ('执行' if exec_case else '诉讼')
    real = case_dirs(case_dir)
    extra = [d for d in real if d not in ALLOWED_DIRS]
    return inbox_guide_text(folder, info['no'], info['client'] or row.get('委托人') or '',
                            info['opponent'], info['cause'], case_type, case_dir,
                            real_dirs=real, extra_dirs=extra,
                            series=(not parent) and is_series_case(case_dir, extra),
                            row=row, parent=parent)


def plan_guides(root=None, names=None, subs=None):
    """批量补件方案（只读，不写盘）：每个顶层案件夹一份《材料怎么放》。

    subs：需额外展开到「子案件夹」的**系列案总夹名**列表（如 `['A闽兴']`）。
      None / 空 = 只扫第一层案件夹（原行为）。
      展开出的子夹：`level=2`、`key='总夹/子夹'`、`parent=总夹名`（批 21 加）。
    """
    root = root or case_root()
    names = names or sorted([d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))])
    try:
        rows = ledger_case_rows()
    except Exception:                                         # noqa: BLE001
        rows = None
    items = []

    def _mk(p, name, parent=''):
        row = None
        if rows:
            try:
                row = match_case_ledger(name, rows).get('matched')
            except Exception:                                 # noqa: BLE001
                row = None
        cat = str((row or {}).get('案件类别') or '').strip()
        # 「执行异议」不是执行实施案（不需要 09/10），2026-09-12 批 17 修
        exec_case = ((cat == '执行' or (cat.startswith('执行') and '异议' not in cat))
                     or os.path.isdir(os.path.join(p, '09-财产查控')))
        gp = os.path.join(p, GUIDE_NAME)
        real = case_dirs(p)
        extra = [d for d in real if d not in ALLOWED_DIRS]
        items.append({'folder': name, 'key': (parent + '/' + name) if parent else name,
                      'parent': parent, 'level': 2 if parent else 1,
                      'guide_path': gp, 'exists': os.path.isfile(gp),
                      'exec': exec_case, 'ledger_case': (row or {}).get('案件') or '',
                      'series': (not parent) and is_series_case(p, extra),
                      'dirs': len(real), 'extra': len(extra),
                      'text': build_guide_text(p, exec_case, row, parent=parent or None)})

    for name in names:
        rel = name.replace('\\', '/').strip('/')
        if '/' in rel:                                        # 允许直接点名子夹：'A闽兴/某子夹'
            parent, leaf = rel.rsplit('/', 1)
            p = os.path.join(root, parent, leaf)
            if os.path.isdir(p):
                _mk(p, leaf, parent=parent)
            continue
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        _mk(p, name)
        if subs and name in subs:                             # 系列案总夹 → 逐子案件夹
            for sub in [d for d in case_dirs(p) if d not in ALLOWED_DIRS]:
                _mk(os.path.join(p, sub), sub, parent=name)
    return {'root': root, 'items': items,
            'todo': [i['key'] for i in items if not i['exists']]}


def apply_guides(root=None, names=None, only=None, subs=None):
    """批量写入《材料怎么放》——**只新增，已存在的跳过，不覆盖不移动**

    only：只处理这些 key（`'夹名'` 或 `'总夹/子夹'`）；不传 = 全部待补项。
    """
    plan = plan_guides(root, names, subs)
    written, skipped = [], []
    for it in plan['items']:
        if it['exists'] or (only and it['key'] not in only and it['folder'] not in only):
            skipped.append(it['key'])
            continue
        if os.path.isfile(it['guide_path']):                   # 写前再确认（防竞态）
            skipped.append(it['key'])
            continue
        # 非退出式守卫：level1 落「case_root/案件夹/」；level2 落「case_root/总夹/子夹/」
        # （Web 服务进程里不能 sys.exit）
        root_abs = os.path.abspath(root or case_root())
        gp_abs = os.path.abspath(it['guide_path'])
        if not (gp_abs.startswith(root_abs + os.sep)
                and gp_abs.count(os.sep) == root_abs.count(os.sep) + it['level'] + 1):
            raise ValueError('落点越界，拒绝写入：%s' % gp_abs)
        with open(it['guide_path'], 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(it['text'])
        written.append(it['guide_path'])
    plan['written'], plan['skipped'] = written, skipped
    return plan


def tree_for(case_type):
    t = list(TREE_BASE)
    if case_type and '执行' in case_type:
        t += TREE_EXEC
    return t


def plan_new_case(case_no, client, opponent, cause, case_type='诉讼',
                  court='', case_no_court='', lawyer=''):
    folder = case_folder_name(case_no, client, opponent, cause)
    dst = os.path.join(case_root(), folder)
    draft = {'案件类别': case_type,
             # C 列格式按台账「维护说明」页：原告vs被告-案由
             '案件': '%svs%s-%s' % (client, opponent, cause),
             '委托人': client, '最新状态': '待立案',
             '当前进度': '[待确认] 立卷（%s）' % case_no}
    if court:
        draft['法院/仲裁'] = court
    if case_no_court:
        draft['案号'] = case_no_court
    if lawyer:
        draft['承办律师'] = lawyer
    return {'folder': folder, 'path': dst, 'exists': os.path.exists(dst),
            'dirs': tree_for(case_type),
            'pipeline_cmd': [sys.executable, PIPELINE_SCRIPT, '--case-id', folder,
                             '--case-root', case_root()],
            'ledger_draft': draft,
            # 案卷根级那份《材料怎么放》（唯一一份说明，随案走）
            'guide_path': os.path.join(dst, GUIDE_NAME),
            'guide_text': inbox_guide_text(folder, case_no, client, opponent, cause,
                                           case_type, dst)}


def create_new_case(case_no, client, opponent, cause, case_type='诉讼', with_pipeline=True,
                     court='', case_no_court='', lawyer=''):
    plan = plan_new_case(case_no, client, opponent, cause, case_type,
                         court, case_no_court, lawyer)
    if plan['exists']:
        print('拒绝：案件夹已存在 → %s' % plan['path'])
        sys.exit(2)
    created = []
    try:
        os.makedirs(plan['path'])
        created.append(plan['path'])
        for d in plan['dirs']:
            sub = os.path.join(plan['path'], d)
            os.makedirs(sub, exist_ok=True)
            created.append(sub)
        log = os.path.join(plan['path'], 'intake-log.md')
        with open(log, 'w', encoding='utf-8') as f:
            f.write('# intake-log — %s\n\n> 入库留痕：原名 ↔ 新名 ↔ 移动路径（Q3 折中制）。'
                    '由 case_ops.py 追加，**只追加不删除**。\n\n'
                    '| 时间 | 原名 | 新名 | 归入 | 方式 |\n|---|---|---|---|---|\n'
                    % plan['folder'])
        created.append(log)
        # 案卷根级《材料怎么放》（每案一份；只新增，不覆盖）
        gp = plan['guide_path']
        if not os.path.exists(gp):
            with open(gp, 'w', encoding='utf-8') as f:
                f.write(plan['guide_text'])
            created.append(gp)
            plan['guide_written'] = gp
        if with_pipeline:
            r = subprocess.run(plan['pipeline_cmd'], capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode != 0:
                raise RuntimeError('new_case_pipeline 失败（退出 %d）：%s'
                                   % (r.returncode, (r.stdout or '') + (r.stderr or '')))
            plan['pipeline_out'] = (r.stdout or '').strip()
    except Exception as e:
        for p in reversed(created):
            try:
                if os.path.isdir(p) and not os.listdir(p):
                    os.rmdir(p)
                elif os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        print('执行异常（已尽力回滚）：%r' % e)
        sys.exit(3)
    return plan


# ---------------------------------------------------------------- 归档

def ensure_inbox(case_dir, exec_case=False, apply=False):
    """给既有案件夹补 v2 组件（00-收件箱 / 执行案 09+10 / intake-log）——**纯新增，不移动不改名**"""
    case_dir = _guard_in_case(case_dir)
    planned = []
    for d in [INBOX] + (TREE_EXEC if exec_case else []):
        p = os.path.join(case_dir, d)
        if not os.path.isdir(p):
            planned.append(p)
    log = os.path.join(case_dir, 'intake-log.md')
    if not os.path.isfile(log):
        planned.append(log)
    guide = os.path.join(case_dir, GUIDE_NAME)
    if not os.path.isfile(guide):
        planned.append(guide)
    if apply:
        for p in planned:
            if os.path.basename(p) == GUIDE_NAME:
                # 按本夹实况生成（旧写法传空信息 → 说明里全是 [待补]，2026-09-12 批 17 修）
                with open(p, 'w', encoding='utf-8', newline='\r\n') as f:
                    f.write(build_guide_text(case_dir, exec_case))
                continue
            if p.endswith('.md'):
                with open(p, 'w', encoding='utf-8') as f:
                    f.write('# intake-log — %s\n\n> 入库留痕：原名 ↔ 新名 ↔ 移动路径（Q3 折中制）。'
                            '由 case_ops.py 追加，**只追加不删除**。\n\n'
                            '| 时间 | 原名 | 新名 | 归入 | 方式 |\n|---|---|---|---|---|\n'
                            % os.path.basename(case_dir))
            else:
                os.makedirs(p, exist_ok=True)
    return {'case_dir': case_dir, 'planned': planned, 'applied': apply,
            'added': len(planned) if apply else 0}


# ---------------------------------------------------------------- 业务线（律师函 / 差旅发票）

DEFAULT_BIZ_ROOT = _config.path('paths.biz_root', env='CASE_OPS_BIZ_ROOT')   # 业务线目录根（律师函 / 合同 / 差旅发票 的上级）
LETTER_DIR = '律师函'
TRAVEL_DIR = '差旅发票'
# 文件名里这些词不是案件线索（平台名 / 票种 / 动作词 / 公司后缀 / 地名）
# 2026-09-12 批 19：扩表——「北京滴滴出行科技有限公司」曾因「有限/限公/公司/科技」误配到案名，
# 所以公司后缀与常见地名一律不做线索（发票平台名里这些词最多）。
INVOICE_STOPWORDS = ('顺风车', '电子发票', '打车', '滴滴', '高德', '出行', '行程', '发票', '动车',
                     '高铁', '出租车', '车费', '法院', '立案', '执行', '材料', '费用', '案件', '律师',
                     '有限', '限公', '公司', '科技', '实业', '贸易', '发展', '集团', '企业', '控股',
                     '管理', '服务', '咨询', '中心', '经营', '销售', '饲料', '生物', '水产', '养殖',
                     '北京', '上海', '厦门', '福建', '福州', '揭阳', '三明', '南通', '漳州', '泉州',
                     '盐城', '淮安', '潮州', '深圳', '广州', '漳浦', '龙海', '平和', '云霄')
# 通用字集合（由停用词拆字 + 常见公文/财务用字）——只由这些字拼成的 2-gram 不做线索
STOP_CHARS = set(''.join(INVOICE_STOPWORDS)) | set('的与等人员年月日一二三四五六七八九金额元案件')


def biz_root():
    return os.environ.get('CASE_OPS_BIZ_ROOT', DEFAULT_BIZ_ROOT)


# ---------------------------------------------------------------- 散件管理（批 36，2026-09-17）
#
# 爸爸 2026-09-17 定调：`D:\案件\你的案卷根` 下「律师函 / 合同 / 差旅发票 / 所函 / 介绍信 /
# 判决书 / 接收到的文件」这些**按业务类型横铺的目录是故意这么放的**（方便找），
# **不归拢、不搬家**；诉求是「工作台出功能管理它们」。且其中一部分**在个案里没有
# 对应**（不是爸爸直接经手的案子，如 `所函\_代盖章（非经手案）`）。
#
# 因此本模块**只做索引视图**：扫这些横向目录 → 出可搜索清单 → 标出
#   ① 疑似归属的案件（能对上台账行/案件夹就提示，认不出就说认不出，不硬套）
#   ② 「非经手」标记（依据 `_迁出记录.md` / `_代盖章` 目录名）
# 绝不移动、绝不改名、绝不删除任何文件。
SCATTER_DIRS = ['律师函', '合同', '差旅发票', '所函', '介绍信', '判决书', '接收到的文件']
#: **公司字号 / 通用公司名**：不能单独作为「归属线索」
#: ⚠️ 2026-09-18 实踩：拿「福建闽兴」当线索，把 `徐俊帆`/`陈飞鹏`/`林海明` 等
#: **批量催收对象**误挂到「当事人丙案」（15 件假阳性）——
#: 公司字号是**委托人/服务方名称**，**不是区分当事人的特征**。
SCATTER_CORP_TOKENS = (
    '委托人', '闽兴', '某公司', '云通', '中北', '新视', '向上', '恒投', '世野', '佳联',
    '优卓', '源卓', '艺千', '速煦', '京邦达', '超越', '饲料', '生物', '科技',
    '有限', '公司', '股份', '集团', '实业', '供应链', '律师事务所',
    # 批 39g 补：**地名 + 组织形态**也算公司要素（实踩『福州闽兴饲料有限公司』穿透）
    #   判定 ④ 去字号后若只剩地名（「福州」「揭阳」），仍无当事人区分度。
    '福州', '厦门', '泉州', '漳州', '揭阳', '南通', '三明', '盐城', '淮安',
    '龙海', '海沧', '翔安', '福建', '广东', '江苏', '安徽', '湖北',
    '有限公司', '有限责任公司', '农业开发', '农业', '开发',
)
SCATTER_SKIP_DIRS = {'pipeline', '__pycache__'}
SCATTER_KIND = {                     # 目录 → (业务类型, 说明)
    '律师函': ('律师函', '对外发函：每对象一夹，含函件成品、对账单、担保/授信材料'),
    '合同': ('合同审查', '法律顾问/合同审查件：按客户一夹，含初审稿、修订稿、审查说明'),
    '差旅发票': ('差旅发票', '办案差旅票据：按年月存放（报销归集用）'),
    '所函': ('所函', '律所出具函件（多为代盖章、非经手案）'),
    '介绍信': ('介绍信', '律所介绍信（查档、会见用）'),
    '判决书': ('判决书', '生效裁判文书'),
    '接收到的文件': ('外来文件', '收到的外来材料，待分发到个案'),
}
# 散件文件名里的通用词——散件页归属判定必须剔除它们，否则「合同」会把所有合同
# 挂到所有含「合同」的案子上（2026-09-17 首版实踩：三丰云通的顾问合同被挂到
# 「福州闽兴」「淮安海龙」）。剔除后只留当事人/标的类片段。
SCATTER_GENERIC = set(INVOICE_STOPWORDS) | {
    '合同', '协议', '材料', '文件', '文档', '清单', '目录', '一览表', '确认书', '委托书',
    '授权', '委托', '代理', '律师函', '律师', '函件', '介绍信', '判决书', '裁定书',
    '证据', '诉状', '起诉', '申请', '申请书', '说明', '报告', '记录', '截图', '照片',
    '身份', '身份证', '户口', '结婚证', '营业', '执照', '章程', '报价', '对账', '账单',
    '发票', '票据', '收据', '凭证', '转账', '流水', '征信', '查询', '结果', '资料',
    '初审', '审阅', '修订', '修改', '定稿', '终稿', '草稿', '版本', '补充', '增补',
    '规定', '制度', '条款', '模板', '格式', '新版', '旧版', '旧稿', '废弃', '勿用',
    '出具', '送达', '地址', '电话', '联系', '备注', '首', '第', '副本', '正本', '扫描件',
    # 2026-09-17 二次收紧：法律业务通用词（首版实踩——「服务合同」把某公司的合同
    # 全挂到「向上沟通VS沧珩服务合同纠纷」，「有限公司」把 5 份某公司合同挂到委托人案）
    '服务合同', '有限公司', '有限责任公司', '股份有限公司', '买卖合同', '委托合同',
    '顾问合同', '运输合同', '租赁合同', '借款合同', '保证合同', '担保合同', '购销合同',
    '法律事务', '法律顾问', '商务服务', '咨询服务', '货物运输', '国际货物',
    '服务', '顾问', '商务', '咨询', '运输', '租赁', '借款', '保证', '担保', '购销',
    '纠纷', '争议', '诉讼', '仲裁', '执行', '异议', '上诉', '管辖', '标的',
    # 2026-09-17 批 38 三次收紧：**行业/组织恒通用词**
    # 实踩：律师函「黎林培致安徽速煦供应链科技有限公司」被「供应链科」(4字) 挂到
    # 「张友谅vs福建京邦达供应链科技有限公司-噪声污染责任纠纷」——两家公司字号完全不同，
    # 只因都含「供应链科技」。这类词换成任何当事人都可能出现 → 不是身份线索。
    '供应链', '科技', '网络', '信息', '电子', '智能', '实业', '集团', '控股', '投资',
    '管理', '发展', '企业', '商业', '贸易', '进出口', '物流', '建设工程', '工程',
    '装饰', '设计', '文化', '传媒', '广告', '教育', '医疗', '健康', '食品', '农业',
    '生物', '环保', '新材料', '机械', '设备', '制造', '工业', '能源', '汽车', '地产',
    '置业', '物业', '酒店', '餐饮', '旅游', '保安', '劳务', '人力', '资源', '人才',
}
# 通用词的 2-gram 集合（跨词碎片判定用；批 38 三次收紧，见 _is_generic_piece ③）
_GENERIC_BIGRAMS = set()
for _g in SCATTER_GENERIC:
    if len(_g) >= 2:
        for _i in range(len(_g) - 1):
            _GENERIC_BIGRAMS.add(_g[_i:_i + 2])
del _g, _i


def _scatter_read_notes(p):
    """读目录内 `_迁出记录.md` / `README.md`，取前几行摘要（说明该目录的现状）。"""
    for fn in ('_迁出记录.md', 'README.md'):
        f = os.path.join(p, fn)
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                txt = fh.read().strip()
        except OSError:
            continue
        first = [l.strip() for l in txt.splitlines()
                 if l.strip() and not l.strip().startswith('#')]
        if first:
            return {'file': fn, 'note': first[0][:120]}
        return {'file': fn, 'note': '（空）'}
    return None


def _scatter_offhand(p, name):
    """是否判为「非经手」：目录名含「代盖章/非经手」→ 是；目录只余 _迁出记录.md → 是（已迁空）。"""
    if '代盖章' in name or '非经手' in name:
        return '目录名标注非经手'
    try:
        ents = [e for e in os.listdir(p) if not e.startswith('.')]
    except OSError:
        return ''
    real = [e for e in ents if e not in ('_迁出记录.md', 'README.md')]
    if not real:
        return '已迁空（仅余迁出记录）'
    return ''


def scatter_index(rows=None, limit_per_dir=400):
    """散件索引（**只读**）：横向业务目录 → 可搜索清单。

    每项：目录 / 业务类型 / 相对路径 / 文件名 / 大小 / 修改时间 / 疑似归属案件 / 判定依据。
    「疑似归属」复用 `match_invoice` 的中文 n-gram 线索法（含停用词与通用字门槛），
    认不出即 `matched=False`，不硬套。
    """
    root = biz_root()
    out = {'root': root, 'dirs': [], 'items': [], 'total': 0,
           'recognized': 0, 'offhand': 0, 'truncated': 0}
    _cf_pool = None          # 惰性建（批 39c：案卷夹名池，供「台账没有但案卷有」的案子）
    if rows is None:
        try:
            rows = ledger_case_rows()
        except Exception:                                     # noqa: BLE001
            rows = []
    case_names = []
    for r in rows:
        nm = str(r.get('案件') or '').strip()
        if nm:
            case_names.append(nm)

    for d in SCATTER_DIRS:
        p = os.path.join(root, d)
        info = {'name': d, 'kind': SCATTER_KIND.get(d, ('其他', ''))[0],
                'desc': SCATTER_KIND.get(d, ('其他', ''))[1],
                'path': p, 'exists': os.path.isdir(p),
                'files': 0, 'subdirs': 0, 'size_mb': 0.0,
                'offhand': '', 'notes': None, 'children': []}
        if not info['exists']:
            out['dirs'].append(info)
            continue
        info['offhand'] = _scatter_offhand(p, d)
        info['notes'] = _scatter_read_notes(p)
        # 批 39c：案卷夹名池（一次建好，全循环共用——避免每文件 walk 案卷）
        _fp = _cf_pool if _cf_pool is not None else _case_folders_for_match()
        nf = nb = 0
        kept = 0
        for r, dirs, fs in os.walk(p):
            dirs[:] = [x for x in dirs if x not in SCATTER_SKIP_DIRS]
            for f in sorted(fs):
                if f.startswith('~$') or f.startswith('.'):
                    continue
                fp = os.path.join(r, f)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                nf += 1
                nb += st.st_size
                if kept >= limit_per_dir:
                    out['truncated'] += 1
                    continue
                kept += 1
                rel = os.path.relpath(fp, root)
                sub = os.path.relpath(r, p) if r != p else ''
                # 判定线索：文件名 + 直接所属子夹名（如 律师函\11蔡和兴\…）——子夹名常含当事人
                folder_hint = os.path.basename(r) if r != p else ''
                # 批 39c：匹配源 = 台账 + **案卷夹名**（台账没有但案卷有的案子也能认到）
                m = scatter_match(f, rows, folder_hint, folder_pool=_fp) if rows else {
                    'cases': [], 'why': '台账不可用'}
                rec = bool(m['cases'])
                out['items'].append({
                    'dir': d, 'kind': info['kind'],
                    'rel': rel, 'name': f,
                    'sub': sub,
                    'size_kb': round(st.st_size / 1024.0, 1),
                    'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d'),
                    'matched': rec, 'cases': m['cases'][:2], 'why': m.get('why', ''),
                    'ambiguous': bool(m.get('ambiguous')),
                    'offhand': bool(info['offhand'])})
                if rec:
                    out['recognized'] += 1
        info['files'] = nf
        info['size_mb'] = round(nb / 1024.0 / 1024.0, 1)
        try:
            info['subdirs'] = len([x for x in os.listdir(p)
                                   if os.path.isdir(os.path.join(p, x))])
            info['children'] = sorted([x for x in os.listdir(p)
                                       if os.path.isdir(os.path.join(p, x))])[:30]
        except OSError:
            pass
        if info['offhand']:
            out['offhand'] += info['files']
        out['dirs'].append(info)

    out['items'].sort(key=lambda x: (x['dir'], x['rel']))
    out['total'] = len(out['items'])
    out['case_names'] = case_names
    return out


def letter_folders():
    r"""律师函 / 各债务人夹 概览（只读；不移动、不改名）

    ⚠️ 2026-09-18 批 39i：律师函件已**归置**（31 件入库 + 190 件进 `批量催收件`），
    原 `张律师\律师函` 已清空。数据源改为**优先新位置**、旧位置回退：
      ① `A个案\_归档区\批量催收件`（当前实际所在）
      ② `张律师\律师函`（旧位置，防历史数据/迁移中途）
    """
    cand = [os.path.join(case_root(), ARCHIVE_BASE, '批量催收件'),
            os.path.join(biz_root(), LETTER_DIR)]
    root = ''
    for c in cand:
        if os.path.isdir(c):
            root = c
            break
    if not root:
        root = cand[0]
    out = {'root': root, 'exists': os.path.isdir(root), 'folders': []}
    if not out['exists']:
        return out
    _IMG = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        files = []
        for r, _d, fs in os.walk(p):
            files += [os.path.join(r, f) for f in fs]
        latest = max(files, key=os.path.getmtime) if files else None
        # 批 46.6（爸爸拍板「全在页面内」）：给出**文件清单**供页内展示/预览/下载。
        # 只列元数据（名/大小/时间/子目录），**不读文件内容**——与助理只读同源纪律。
        flist = []
        for f in sorted(files):
            try:
                sz = os.path.getsize(f)
                mt = datetime.fromtimestamp(os.path.getmtime(f)).strftime('%m-%d %H:%M')
            except OSError:
                continue
            ext = os.path.splitext(f)[1].lower()
            kind = ('图片' if ext in _IMG else
                    'PDF' if ext == '.pdf' else
                    '表格' if ext in ('.xlsx', '.xls', '.csv') else
                    '文档' if ext in ('.doc', '.docx') else
                    '视频' if ext in ('.mp4', '.mov', '.avi') else '其它')
            sub = os.path.relpath(os.path.dirname(f), p)
            flist.append({'name': os.path.basename(f),
                          'sub': '' if sub == '.' else sub,
                          'kind': kind, 'size_kb': round(sz / 1024, 1), 'mtime': mt})
        out['folders'].append({
            'name': name, 'files': len(files),
            'subdirs': [d for d in sorted(os.listdir(p))
                        if os.path.isdir(os.path.join(p, d))],
            'latest': os.path.basename(latest) if latest else '',
            'latest_time': (datetime.fromtimestamp(os.path.getmtime(latest)).strftime('%m-%d')
                            if latest else ''),
            'samples': [os.path.basename(f) for f in sorted(files)[:5]],
            'files_list': flist})
    return out


# ---------------------------------------------------------------- 批 41：新增发函对象材料夹
def letter_folder_core(name):
    """收函对象 → 材料夹名主体（批 41.1）：取首个顿号/分号/逗号前的段（≥2 字）。
    「杨记水产、杨建忠、杨小艺…」→「杨记水产」——夹名用短主体，全串留给台账字段。"""
    s = str(name or '').strip()
    if not s:
        return ''
    core = re.split(r'[、，;；,]', s)[0].strip() or s
    return _safe_fn(core)


def letter_folder_plan(name):
    """新增发函对象材料夹**方案**（不落盘）。
    root 固定规范位 `case_root()/_归档区/批量催收件`——**读概览可回退旧位置，新建夹只去规范位**
    （letter_folders() 的 root 是「哪个存在用哪个」，对读取对、对写入不对）。
    夹名 = letter_folder_core(收函对象)（批 41.1：主体词，不再用整串）。"""
    nm = letter_folder_core(name)
    root = os.path.join(case_root(), ARCHIVE_BASE, '批量催收件')
    return {'root': root, 'name': nm, 'path': os.path.join(root, nm) if nm else '',
            'exists': (bool(nm) and os.path.isdir(os.path.join(root, nm)))}


def letter_folder_apply(name):
    """建收函对象材料夹（批 41）：**只新增目录**，已存在跳过；名字过 _safe_fn 守卫。
    返回 {'ok', 'path', 'created'(True=新建/False=已存在), 'name'}；非法名抛 ValueError。"""
    plan = letter_folder_plan(name)
    if not plan['name']:
        raise ValueError('收函对象名为空或全为非法字符，无法建材料夹')
    p = plan['path']
    if plan['exists']:
        return {'ok': True, 'created': False, 'path': p, 'name': plan['name']}
    os.makedirs(p, exist_ok=True)
    if not os.path.isdir(p):
        raise RuntimeError('目录创建失败（未报错但不存在）：%s' % p)
    return {'ok': True, 'created': True, 'path': p, 'name': plan['name']}


def _cn_grams(text, n=2):
    """中文 n-gram 候选（剔除平台名/票种/公司后缀/地名等停用词）。

    2026-09-12 批 19：加「非通用字 ≥2」门槛 —— 「北京滴滴出行科技有限公司」会滑出「技有」这类
    跨词碎片，曾把发票误挂到「盐城海维生物科技有限公司」；只由通用字拼成的碎片一律不算线索。
    """
    grams = set()
    for seg in re.findall(r'[\u4e00-\u9fa5]+', text):
        for i in range(len(seg) - n + 1):
            g = seg[i:i + n]
            if g in INVOICE_STOPWORDS:
                continue
            if sum(1 for ch in g if ch not in STOP_CHARS) < 1:
                continue
            grams.add(g)
    return grams


def match_invoice(fname, rows):
    """按文件名线索在台账「案件 / 委托人」里找对应案；认不出就明说（不编）"""
    grams = _cn_grams(os.path.splitext(fname)[0])
    if not grams:
        return {'cases': [], 'why': '文件名里没有中文线索（纯发票号/平台名，认不出属正常）'}
    hits = []
    for row in rows or []:
        hay = '%s%s' % (row.get('案件') or '', row.get('委托人') or '')
        g = sorted([x for x in grams if x in hay], reverse=True)
        if g:
            hits.append({'案件': row.get('案件'), 'sheet': row.get('sheet'), 'row': row.get('row'),
                         '为什么': '文件名含「%s」' % '、'.join(g[:3]), '命中数': len(g)})
    hits.sort(key=lambda x: -x['命中数'])
    return {'cases': hits[:3], 'why': ''}


#: **职能目录**（案卷内部的规范子夹）—— **不是案子**，不得进匹配源
#: ⚠️ 2026-09-18 实踩：首版把 `需盖章文件` / `02-法律文书` 等也收进池（175 条大半是垃圾），
#: 弱命中稀释强命中 → 认到数 **31 → 24（退步）**。
_FUNC_DIRS = frozenset((
    '00-收件箱', '01-委托手续', '02-法律文书', '03-证据材料', '04-法院文件',
    '05-费用票据', '06-当事人信息', '07-律师函', '08-过程文件', '需盖章文件',
    'pipeline', '参考', 'ocr', 'OCR识别底稿', '证据', '法院', '仲裁文书',
    '01-文书草稿备份', '03-撤回备查', '04-仲裁文件', '06-当事人信息',
))


def _case_folders_for_match():
    """案卷夹名池（供散件匹配的**第二匹配源**）—— **只收「案子夹」**

    ⚠️ 2026-09-18 批 39c：实测台账只 27 行，而案卷有 **9 个案子只在案卷不在台账**
    （`福州闽兴vs霍山`、`民-A-黎林培VS安徽速煦供应链`…）→ 只看台账会**漏认**。

    只收「像案子的」：含「VS/vs/诉/纠纷/执行/异议」等案件特征，
    或位于客户层下且不是职能目录。**职能目录一律排除**（它们不是案子）。
    """
    root = case_root()
    out = []
    if not os.path.isdir(root):
        return out
    import re as _re
    CASE_LIKE = _re.compile(r'VS|vs|诉|纠纷|执行|异议|劳动|保全|诉讼')
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p) or d.startswith('.'):
            continue
        if d in _FUNC_DIRS:
            continue
        # 一级：找案子的那几个「客户总夹」（如 `A闽兴`）里的**案子夹**
        #   ⚠️ 2026-09-18 实测再收窄：**二级里几乎全是职能目录/材料夹**
        #   （`09-财产查控`/`10-处置分配`/`提交法院的证据`/`21、2025年当事人丁授信资料`/
        #   `要准备的材料`）→ 全收会把池子污染成 175 条、弱命中稀释强命中（31→24 退步）。
        #   → **只在「客户总夹」下取二级案子夹**（含 VS/诉/纠纷等案件特征），其余不收。
        subs = []
        try:
            subs = [x for x in sorted(os.listdir(p))
                    if os.path.isdir(os.path.join(p, x)) and not x.startswith('.')]
        except Exception:                                     # noqa: BLE001
            subs = []
        has_case_subs = any(CASE_LIKE.search(x) for x in subs)
        if has_case_subs:
            # 「客户总夹」型（A闽兴 / 盐城海维生物…）→ 收它下面的案子夹
            out.append((d, d))          # 总夹本身也收（供粗匹配）
            for d2 in subs:
                if d2 in _FUNC_DIRS or not CASE_LIKE.search(d2):
                    continue
                out.append((d2, d + '/' + d2))
        else:
            # 本身就是案子夹（如 `民-A-黎林培VS安徽速煦供应链`）
            if CASE_LIKE.search(d):
                out.append((d, d))
    return out


def scatter_match(fname, rows, folder_name='', folder_pool=None):
    """散件专用归属判定（比发票严：散件目录里「合同/协议/简历」这类通用词满地都是）。

    判定链（由强到弱，任一命中即返回）：
      ① **当事人级命中**：文件名去掉通用词后，剩下的中文片段里有 ≥3 字连续出现在台账
         案件名/委托人里（长片段几乎不可能是巧合）；
      ①b **案卷夹名命中**（2026-09-18 新增）：同上，但匹配源是**案卷夹名**
         （台账没有但案卷有的案子，如 `福州闽兴vs霍山`）；
      ② **当事人名全等**：文件名或所在子夹名里的 2-4 字中文人名（非通用词）全等于台账
         案件名中的当事人片段；
      ③ 都未中 → 认不出（明确说「无足够线索」，不硬套）。

    ⚠️ **公司字号不作为线索**（见 SCATTER_CORP_TOKENS）——否则「福建闽兴」会把
    批量催收对象全挂到某一个案子上。

    与 `match_invoice` 的区别：不采纳「文件名含『合同』」这类 1 个通用 2-gram 的弱命中。
    """
    stem = os.path.splitext(fname)[0]
    text = stem + ' ' + (folder_name or '')
    hay_pool = []
    for row in rows or []:
        nm = str(row.get('案件') or '')
        cl = str(row.get('委托人') or '')
        hay_pool.append({'案件': nm, '委托人': cl, 'sheet': row.get('sheet'),
                         'row': row.get('row'), 'hay': nm + cl,
                         'src': 'ledger'})
    # ①b 案卷夹名也进池（仅当调用方给了 pool，避免每次 walk 目录）
    for nm, path in (folder_pool or []):
        hay_pool.append({'案件': nm, '委托人': '', 'sheet': '', 'row': None,
                         'hay': nm, 'src': 'folder', 'path': path})

    def _segments(t):
        out = []
        for seg in re.findall(r'[\u4e00-\u9fa5]{2,}', t):
            # 剔除通用词，只留「像当事人/标的」的片段
            if seg in SCATTER_GENERIC or seg in INVOICE_STOPWORDS:
                continue
            out.append(seg)
        return out

    segs = _segments(text)
    if not segs:
        return {'cases': [], 'why': '文件名只有通用词（合同/协议/材料…），无当事人线索——认不出属正常'}

    def _is_generic_piece(piece, from_folder=False):
        """片段是否「无身份意义」（通用词）—— **双向判定**：

        ① 片段含通用词（防「服务合」「有限公」这类被切碎的通用词）
        ② **片段是通用词的子串**（2026-09-17 批 38 补；实踩：『应链科』是『供应链科技』
           的滑动子串，不含完整通用词却同样是行业词 → 曾把安徽速煦的律师函误挂到
           福建京邦达供应链科技的噪声污染案）

        只要与任一通用词有 ≥2 字重叠，即视为通用。
        """
        if piece in SCATTER_GENERIC:
            return True
        for g in SCATTER_GENERIC:
            if len(g) < 2:
                continue
            if g in piece:              # ① 通用词在片段里
                return True
            if piece in g:              # ② 片段是通用词的子串
                return True
        # ③ 跨词碎片：片段内任一 2-gram 命中通用词 2-gram 集合
        #    （实踩：『应链科』跨『供应链』尾 +『科技』头，①② 均漏）
        for i in range(len(piece) - 1):
            if piece[i:i + 2] in _GENERIC_BIGRAMS:
                return True
        # ④ **公司字号不算线索**（2026-09-18 批 39c 补）
        #    实踩：用「福建闽兴」当线索，把「徐俊帆」「陈飞鹏」「林海明」等
        #    **批量催收对象**误挂到「当事人丙案」（15 件假阳性）。
        #    公司字号是**委托人/服务方名称**，**不是区分当事人的特征**。
        #    判定：片段「全是公司字号拼成的」（去掉公司字号后不剩 ≥2 字）→ 无身份意义。
        # ⚠️ **夹名来源豁免**（批 39g 实踩）：`律师函\霍山\` 的「霍山」是子夹名
        #    （人为归类的当事人），却被④档当公司字号剔除 → 真当事人被打掉。
        #    🔑 夹名不是公司名，**不做公司字号判定**。
        if from_folder:
            return False
        _p = piece
        for _c in SCATTER_CORP_TOKENS:
            _p = _p.replace(_c, '')
        # ⚠️ 批 39g 实踩：阈值 2 太松 ——『福州闽兴饲料有限公司』去字号后剩「福州」
        #    正好 2 字，判定通过 → 把「海王鳗业」的对账单挂到「朱泽洪案」。
        #    公司全名去掉要素后**剩不到 3 字**即无当事人区分度（真实当事人名多为 2-3 字）。
        if len(_p) < 3:
            return True
        return False

    hits = []
    for row in hay_pool:
        hay = row['hay']
        best = 0
        best_piece = ''
        for seg in segs:
            # ① 找 seg 与 hay 的最长公共连续子串（且该子串不得是通用词/含通用词）
            for L in range(len(seg), 1, -1):
                if L < 3:
                    break
                found = ''
                for i in range(len(seg) - L + 1):
                    piece = seg[i:i + L]
                    if piece in hay and not _is_generic_piece(piece):
                        found = piece
                        break
                if found:
                    if L > best:
                        best, best_piece = L, found
                    break
        # ② 当事人名全等（子夹名如「徐富强」「霍山」）——同样不得是通用词
        if best < 3:
            for seg in segs:
                # ⚠️ 批 39g 三修：豁免条件必须是「**片段 == 夹名**」（夹名本身即当事人），
                #    不能放宽到「片段 ⊂ 夹名」——后者会把 `律师函-福建闽兴VS陈飞鹏`
                #    整名放行，其中「福建闽兴」要素穿透 → 假阳性复现。
                if 2 <= len(seg) <= 4 and seg in hay and not _is_generic_piece(
                        seg, from_folder=(folder_name == seg)):
                    if best < len(seg):
                        best, best_piece = len(seg), seg
        # ③ **夹名来源加权**（批 39c 补二，2026-09-18）
        #    实踩：`律师函\霍山\*`（28 件）——「霍山」是**子夹名**（人为归类的当事人），
        #    但 2 字强度只有 2 < strong 门槛 3 → 全被降级为「弱线索」。
        #    🔑 关键区别：**夹名是人为归类的（「每对象一夹」），比文件名可靠得多**。
        #    → 若命中片段**出现在 folder_name（子夹名）里**，**+1 加权**。
        if best_piece and folder_name and best_piece in folder_name:
            best = best + 1
            for h_row in [row]:
                pass
        if best >= 2:
            hits.append({'案件': row['案件'], 'sheet': row['sheet'], 'row': row['row'],
                         '为什么': '命中「%s」(%d字)%s'
                                 % (best_piece, min(best, 4),
                                    '·夹名' if (best_piece and folder_name
                                                and best_piece in folder_name) else ''),
                         '强度': best})
    hits.sort(key=lambda x: -x['强度'])
    strong = [h for h in hits if h['强度'] >= 3]
    if strong:
        top = strong[0]['强度']
        top_hits = [h for h in strong if h['强度'] == top]
        # ⚠️ 批 39c 补三（2026-09-18）：**跨源同名去重**。
        # 规则 ①b 把「案卷夹名」也当匹配源后，同一个案子会同时以「台账行」与
        # 「案卷夹」两个身份进 hits（row 不同但案件名相同）→ top_hits 长度 2
        # → 被误判 ambiguous（实踩：test_scatter_intake「多案并存」拦截，
        # 两个候选名完全相同）。同一案件名 = 同一案，不是歧义。
        seen, uniq = set(), []
        for h in top_hits:
            key = str(h.get('案件') or '').strip()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(h)
        return {'cases': uniq[:2], 'why': '',
                'ambiguous': len(uniq) > 1}
    if hits:
        return {'cases': [], 'why': '仅弱线索（%s），不足以认定——建议人工看' %
                '、'.join(h['为什么'] for h in hits[:2])}
    return {'cases': [], 'why': '台账里找不到对应当事人——可能不是经手案'}


def _scatter_src_rel(rel):
    """散件相对路径 → 绝对路径（三层守卫：必须在散件目录内 / 真实存在 / 不是目录）"""
    rel = str(rel or '').strip().replace('/', os.sep)
    if not rel or os.path.isabs(rel) or '..' in rel.split(os.sep):
        raise ValueError('非法相对路径：%s' % rel)
    root = os.path.realpath(biz_root())
    top = rel.split(os.sep)[0]
    if top not in SCATTER_DIRS:
        raise ValueError('不在散件目录内（只认 %s）：%s' % ('/'.join(SCATTER_DIRS), rel))
    fp = os.path.realpath(os.path.join(root, rel))
    if not (fp == root or fp.startswith(root + os.sep)):
        raise ValueError('路径越界：%s' % rel)
    if not os.path.isfile(fp):
        raise ValueError('文件不存在：%s' % rel)
    return fp


def iter_case_folders(max_depth=2):
    """遍历案卷夹（**支持多级**：`案卷根/案夹` 与 `案卷根/父夹/案夹` 两种结构）

    ⚠️ 2026-09-18 批 39c 实测：真实案卷有**两级**结构——
      根下既有 `仲-厦门向上沟通VS厦门沧珩_…`（一级），
      也有 `A闽兴/民-26003-福州闽兴饲料…`（二级）。
    只扫一层会导致台账案件名认不到夹（人工指定也失败）。

    判定「是案夹」＝ 该目录含编号子夹（00-收件箱 / 01-委托手续…）或 `_材料怎么放.md`。
    """
    root = case_root()
    if not os.path.isdir(root):
        return []
    out = []
    NAMED = ('00-收件箱', '01-委托手续', '02-法律文书', '03-证据材料',
             '04-法院文件', '05-费用票据', '06-当事人信息', '07-律师函',
             '08-过程文件', '需盖章文件')
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p) or d.startswith('.'):
            continue
        subs = set(os.listdir(p)) if os.access(p, os.R_OK) else set()
        # 一级：自己是案夹
        if ('_材料怎么放.md' in subs) or any(x in subs for x in NAMED):
            out.append(p)
        if max_depth < 2:
            continue
        # 二级：子目录是案夹（父夹本身不是）
        for d2 in sorted(subs):
            p2 = os.path.join(p, d2)
            if not os.path.isdir(p2) or d2.startswith('.'):
                continue
            subs2 = set(os.listdir(p2)) if os.access(p2, os.R_OK) else set()
            if ('_材料怎么放.md' in subs2) or any(x in subs2 for x in NAMED):
                out.append(p2)
    return out


def _strip_common_cause(case_name, n):
    """比对辅助（批 71）：两侧末段完全一致（如都收「买卖合同纠纷」）→ 公共案由，剥掉再比；
    拿不准（末段不一致/缺段）→ 原样返回，行为与旧版一致。案由多案共享，不是身份线索。"""
    cs = re.split(r'[-—_]', str(case_name or ''))
    ns = re.split(r'[-—_]', str(n or ''))
    if len(cs) >= 2 and len(ns) >= 2 and cs[-1].strip() and cs[-1].strip() == ns[-1].strip():
        return '-'.join(cs[:-1]), '_'.join(ns[:-1])
    return str(case_name or ''), str(n or '')


def folder_by_case_name(case_name):
    """案件名 → 案件夹（**支持多级**；精确 > 包含 > 2-gram 片段命中；认不到返回 ''）"""
    if not case_name:
        return ''
    folders = iter_case_folders()
    if not folders:
        return ''
    base = {os.path.basename(f): f for f in folders}
    # ① 精确相等
    if case_name in base:
        return base[case_name]
    # ② 夹名含案件名，或案件名含夹名（去掉编号前缀再比）
    for n, f in base.items():
        if case_name in n:
            return f
    stripped = {re.sub(r'^[\w\-]*\d+[_\-]?', '', n): f for n, f in base.items()}
    for n, f in stripped.items():
        if n and (case_name in n or n in case_name):
            return f
    # ③ 2-gram 片段命中（最弱；要求 ≥4 字连续命中，防误认）
    # 批 71：案由（「买卖合同纠纷」等）多案共享，不是身份线索——两侧案由尾一致时先剥再比；
    #        并列最高分再按「3 字共同片段数」细化，仍并列 → **拒认**（实踩：新余钦丰祥案曾被
    #        公共案由带到先到的 26001 揭阳委托人夹；同批修复后全库 29 案名零错认）。
    runs = []
    for n, f in stripped.items():
        cn2, nn2 = _strip_common_cause(case_name, n)
        cn = re.sub(r'[\s\-_（）()、,，]', '', cn2)
        nn = re.sub(r'[\s\-_（）()、,，]', '', nn2)
        run = 0
        for i in range(len(cn) - 3):
            if cn[i:i + 4] in nn:
                run = max(run, 4)
                j = 4
                while i + j <= len(cn) and cn[i:i + j] in nn:
                    run = max(run, j)
                    j += 1
        if run >= 4:
            runs.append((run, cn, nn, f))
    if not runs:
        return ''
    top = max(r[0] for r in runs)
    cands = [r for r in runs if r[0] == top]
    if len(cands) == 1:
        return cands[0][3]
    sc = [(len({cn[i:i + 3] for i in range(len(cn) - 2) if cn[i:i + 3] in nn}), f)
          for _, cn, nn, f in cands]
    m = max(x[0] for x in sc)
    win = [f for c, f in sc if c == m]
    return win[0] if len(win) == 1 else ''


def folder_by_ledger_row(rows=None):
    """台账行 → 案件夹（台账只读一次，批量共用）。键 = (sheet, row)。"""
    out = {}
    if rows is None:
        try:
            rows = ledger_case_rows()
        except Exception:                                     # noqa: BLE001
            rows = []
    # 批 71：改扫**多级**案件夹（iter_case_folders，批 39c 语义）——原只扫一级，
    # 分组夹（如 A闽兴）下的嵌套案件夹全部漏映射，法院短信夹查找因此报「没找到对应案件夹」。
    for p in iter_case_folders():
        try:
            mt = match_case_ledger(os.path.basename(p), rows=rows).get('matched')
        except Exception:                                     # noqa: BLE001
            continue
        if mt:
            out[(mt['sheet'], int(mt['row']))] = p
    return out


def scatter_case_options(rows=None):
    """散件入库的**候选案件**（供「人工指定」下拉）：台账行 + 案卷夹，合并去重。

    ⚠️ 2026-09-18 批 39c：实测 32 件可入库候选里**5 件被「多案并存」拦住**
    （如「揭阳委托人」能匹配 7 个案），原逻辑直接 blocked、**人工无法干预 → 实际不可用**。
    本函数给出「台账里的案件 + 案卷里真实存在的案件夹」并集，供人工选。
    返回 [{'case': 展示名, 'folder': 案卷夹名, 'src': 'ledger'|'folder'}]
    """
    out, seen = [], set()
    if rows is None:
        try:
            rows = ledger_case_rows()
        except Exception:                                     # noqa: BLE001
            rows = []
    for r in rows or []:
        nm = str(r.get('案件') or '').strip()
        if not nm or nm in seen:
            continue
        seen.add(nm)
        folder = ''
        try:
            folder = folder_by_case_name(nm) or ''
        except Exception:                                     # noqa: BLE001
            folder = ''
        out.append({'case': nm, 'folder': os.path.basename(folder) if folder else '',
                    'src': 'ledger'})
    # 案卷里真实存在的夹（含台账未收录的）
    try:
        root = case_root()
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d)
            if not os.path.isdir(p) or d.startswith('.'):
                continue
            if d in seen:
                continue
            seen.add(d)
            out.append({'case': d, 'folder': d, 'src': 'folder'})
    except Exception:                                         # noqa: BLE001
        pass
    return out


def plan_scatter_intake(rel, rows=None, fmap=None, case_name=None):
    """单件入库计划（**不落盘**）：散件 → 该案卷 `00-收件箱`。只移动，不复制不删除。

    case_name：**人工指定案件**（批 39c）。传了就**跳过自动匹配**，直接用这个案名去找夹。
      适用「多案并存，需人工指定」或「没认出对应案件」两种情况。
      认不到夹（案卷里没有该案名的夹）→ 仍然 blocked，**不自动建目录**。
    """
    try:
        fp = _scatter_src_rel(rel)
    except ValueError as e:
        return {'rel': str(rel), 'blocked': str(e)}
    name = os.path.basename(fp)
    if rows is None:
        try:
            rows = ledger_case_rows()
        except Exception:                                     # noqa: BLE001
            rows = []

    # ---- 人工指定通道（跳过匹配）----
    if case_name:
        folder = ''
        try:
            folder = folder_by_case_name(str(case_name)) or ''
        except Exception:                                     # noqa: BLE001
            folder = ''
        if not folder:
            opts = [o['case'] for o in scatter_case_options(rows)][:8]
            return {'rel': str(rel), 'name': name, 'src': fp,
                    'blocked': '没找到案件夹「%s」；可选：%s'
                               % (case_name, '、'.join(opts) or '（无）'),
                    'why': '人工指定'}
        if not os.path.isdir(os.path.join(folder, '00-收件箱')):
            os.makedirs(os.path.join(folder, '00-收件箱'), exist_ok=True)
        dst_dir = os.path.join(folder, '00-收件箱')
        dst = os.path.join(dst_dir, _free_name(_safe_fn(name), [dst_dir]))
        return {'rel': str(rel), 'name': name, 'case': str(case_name),
                'why': '人工指定（跳过自动匹配）',
                'src': fp, 'dst': dst, 'case_folder': folder,
                'manual': True,
                'same': (os.path.normcase(os.path.realpath(fp))
                         == os.path.normcase(os.path.realpath(dst)))}

    top = str(rel).replace('/', os.sep).split(os.sep)[0]
    sub = os.path.dirname(str(rel).replace('/', os.sep))
    hint = os.path.basename(sub) if sub and sub != top else ''
    # ⚠️ 批 39h 实踩：**单件入库路径漏传 folder_pool** → 索引里能认到的件
    #    （`律师函\霍山\*` 靠案卷夹名匹配），一进「入库」就全被拦「没认出对应案件」。
    #    🔑 教训：同一匹配函数有多个调用点时，**新增参数必须逐个调用点补齐**。
    try:
        _fp_plan = _case_folders_for_match()
    except Exception:                                         # noqa: BLE001
        _fp_plan = None
    m = scatter_match(name, rows, hint, folder_pool=_fp_plan)
    cases = m.get('cases') or []
    # 候选案件（供前端「人工指定」下拉）——无论认不认得出都给
    cands = [str(c.get('案件')) for c in cases if c.get('案件')]
    if not cases:
        return {'rel': str(rel), 'name': name, 'src': fp,
                'blocked': '没认出对应案件（%s）' % (m.get('why') or '无线索'),
                'why': m.get('why', ''), 'candidates': cands,
                'options': [o['case'] for o in scatter_case_options(rows)]}
    if len(cases) > 1 and m.get('ambiguous'):
        return {'rel': str(rel), 'name': name, 'src': fp,
                'blocked': '多案并存，需人工指定（%s）'
                           % '、'.join(cands),
                'why': m.get('why', ''), 'candidates': cands,
                'options': [o['case'] for o in scatter_case_options(rows)]}
    hit = cases[0]
    case_name2 = str(hit.get('案件') or '')
    if fmap is None:
        try:
            fmap = folder_by_ledger_row(rows)
        except Exception:                                     # noqa: BLE001
            fmap = {}
    folder = ''
    try:
        folder = fmap.get((hit.get('sheet'), int(hit.get('row') or 0))) or ''
    except (TypeError, ValueError):
        folder = ''
    if not folder:
        folder = folder_by_case_name(case_name2)
    if not folder:
        return {'rel': str(rel), 'name': name, 'case': case_name2, 'src': fp,
                'blocked': '台账认到「%s」，但案卷里没有对应案件夹' % case_name2,
                'why': hit.get('为什么', ''), 'candidates': cands,
                'options': [o['case'] for o in scatter_case_options(rows)]}
    dst_dir = os.path.join(folder, '00-收件箱')
    dst = os.path.join(dst_dir, _free_name(_safe_fn(name), [dst_dir]))
    return {'rel': str(rel), 'name': name, 'case': case_name2,
            'why': hit.get('为什么', ''),
            'src': fp, 'dst': dst, 'case_folder': folder,
            'candidates': cands,
            'same': (os.path.normcase(os.path.realpath(fp))
                     == os.path.normcase(os.path.realpath(dst)))}


def apply_scatter_intake(rel, rows=None, fmap=None, case_name=None):
    """执行入库：把散件**移动**到案卷 `00-收件箱`。

    纪律：只移动不删除；目标同名自动避让；写 `_散件入库.md` 留痕；台账零写入。
    case_name：**人工指定案件**（批 39c），透传给 plan_scatter_intake。
    """
    plan = plan_scatter_intake(rel, rows, fmap, case_name=case_name)
    if plan.get('blocked'):
        raise ValueError(plan['blocked'])
    if plan.get('same'):
        raise ValueError('来源与目标同一个文件，无需入库')
    src, dst = plan['src'], plan['dst']
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    logline = '%s | %s | %s -> %s\n' % (
        stamp, plan.get('case') or '', plan['rel'],
        os.path.relpath(dst, plan['case_folder']))
    lg = os.path.join(plan['case_folder'], '00-收件箱', '_散件入库.md')
    head = ''
    if not os.path.isfile(lg):
        head = '# 散件入库留痕\n\n> 从「按业务类型横铺」的散件目录**移动**进来的文件。\n\n'
    with open(lg, 'a', encoding='utf-8', newline='') as f:
        f.write(head + logline)
    plan['moved'] = True
    plan['log'] = lg
    return plan


def travel_invoices(rows=None):
    """差旅发票按月目录索引（**只读**；方案 §5.1：发票维持按月存放不动，仅按文件名线索挂案）"""
    root = os.path.join(biz_root(), TRAVEL_DIR)
    out = {'root': root, 'exists': os.path.isdir(root), 'items': [], 'recognized': 0}
    if not out['exists']:
        return out
    if rows is None:
        try:
            rows = ledger_case_rows()
        except Exception:                                     # noqa: BLE001
            rows = []
    for r, _dirs, fs in os.walk(root):
        for f in sorted(fs):
            if f.startswith('~$'):
                continue
            fp = os.path.join(r, f)
            m = match_invoice(f, rows)
            rec = bool(m['cases'])
            out['items'].append({'month': os.path.relpath(r, root), 'name': f,
                                 'size_kb': round(os.path.getsize(fp) / 1024.0, 1),
                                 'matched': rec, 'cases': m['cases'][:2], 'why': m['why']})
            if rec:
                out['recognized'] += 1
    out['items'].sort(key=lambda x: (x['month'], x['name']))
    return out


def series_inbox_plan(series_dir):
    """系列案总夹 → 各子案件夹的 00-收件箱 体检（只读）"""
    series_dir = _guard_in_case(series_dir)
    subs = [d for d in case_dirs(series_dir) if d not in ALLOWED_DIRS]
    items = []
    for d in subs:
        p = os.path.join(series_dir, d)
        items.append({'name': d, 'inbox': os.path.isdir(os.path.join(p, INBOX)),
                      'log': os.path.isfile(os.path.join(p, 'intake-log.md')),
                      'path': os.path.join(p, INBOX)})
    return {'series': series_dir, 'series_name': os.path.basename(series_dir),
            'subs': items, 'todo': [x['name'] for x in items if not x['inbox']]}


def apply_series_inbox(series_dir, only=None):
    """给系列案总夹下「缺 00-收件箱」的子案件夹各补一个空目录。

    **只新增空目录**：不动、不改名、不移动任何既有文件；已有 00-收件箱 的子夹跳过。
    """
    plan = series_inbox_plan(series_dir)
    root_abs = os.path.abspath(case_root())
    made, skipped = [], []
    for it in plan['subs']:
        if it['inbox'] or (only and it['name'] not in only):
            skipped.append(it['name'])
            continue
        tgt = os.path.abspath(it['path'])
        parts = os.path.relpath(tgt, root_abs).split(os.sep)
        if len(parts) != 3 or parts[2] != INBOX:              # case_root / 总夹 / 子夹 / 00-收件箱
            raise ValueError('落点越界，拒绝创建：%s' % tgt)
        os.makedirs(tgt, exist_ok=True)
        made.append(tgt)
    plan['made'], plan['skipped'] = made, skipped
    return plan


def plan_archive(case_dir, year=None):
    case_dir = _guard_in_case(case_dir)
    year = year or datetime.now().strftime('%Y')
    dst = os.path.join(archive_root(), year, os.path.basename(case_dir))
    return {'src': case_dir, 'dst': dst, 'exists': os.path.exists(dst),
            'followup': ['在办页序号重排（工具不擅自改其他行）',
                         '过程性文件打印状态核对',
                         '知识库页标「已结案」归档时已自动做；未认到页时用「归档与导出」页卡片手工标']}


def apply_archive(case_dir, year=None):
    plan = plan_archive(case_dir, year)
    if plan['exists']:
        print('拒绝：归档目标已存在 → %s' % plan['dst'])
        sys.exit(2)
    try:
        os.makedirs(os.path.dirname(plan['dst']), exist_ok=True)
        shutil.move(plan['src'], plan['dst'])
    except Exception as e:
        print('执行异常：%r' % e)
        sys.exit(3)
    if os.path.exists(plan['src']):
        print('❗ 复查失败：源目录仍在 → 人工确认')
        sys.exit(3)
    return plan


# ---------------------------------------------------------------- 自测


# ================================================= 案件夹 ↔ 台账行匹配（批 15，单一实现）
# 定位：把「案卷夹名」与「台账案件行」对上号，供 app.py（案件详情）与 timeline_export.py（落点）共用。
# 纪律：**可解释、宁缺勿滥** —— 分数 ≥3 且唯一最高分才认定；并列/不够就返回未匹配，绝不硬套。

CLIENT_SUFFIX = ('有限责任公司', '有限公司', '股份有限公司', '公司', '个体工商户',
                 '水产养殖专业合作社', '农民专业合作社', '专业合作社', '合作社',
                 '律师事务所', '厂', '店', '中心')


def client_core(name):
    """客户名 → 匹配核心词（去企业后缀），供案件夹名粗匹配用"""
    s = str(name or '').strip()
    for suf in CLIENT_SUFFIX:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[:-len(suf)]
            break
    return s.strip() or str(name or '').strip()


# ------------------------------------------------- 案件夹 ↔ 台账行匹配（批 15）

FOLDER_PREFIX_RE = re.compile(r'^\s*(?:民|执|仲|行|刑|非|破)\s*-\s*[A-Za-z]?\s*-?\s*\d+\s*[_-]?')
PAREN_RE = re.compile(r'[（(][^）)]*[）)]')


def _strip_folder(name):
    """去所内编号前缀 + 括号补充 → 只留当事人/案由部分"""
    s = FOLDER_PREFIX_RE.sub('', str(name or ''))
    s = PAREN_RE.sub('', s)
    return s.strip()


def _party_frags(case_name):
    """台账「案件」名 → 当事人片段（vs / 诉 两侧；案由尾巴去掉）"""
    s = PAREN_RE.sub('', str(case_name or ''))
    s = re.split(r'\s*[-—]\s*', s)[0]                 # 去 '-案由'
    parts = re.split(r'(?:vs|VS|Vs|v\s*s|诉)', s)
    return [x.strip(' 、，,') for x in parts if x.strip(' 、，,')]


_TAIL_RE = re.compile(r'(?:强制执行申请|强制执行|执行申请|诉讼|执行|纠纷案|纠纷|案)$')


def _clean_tail(s):
    """去掉程序性尾巴（如「杨扬强制执行」→「杨扬」），只用于比较"""
    s = str(s or '').strip()
    prev = None
    while prev != s:
        prev = s
        s = _TAIL_RE.sub('', s).strip()
    return s


def _sub_names(case_name, frags):
    """片段里的逐个主体（按顿号/逗号切）→ 用于人名级命中；同时给出「去尾巴」变体"""
    out = []
    for f in frags:
        for x in re.split(r'[、，,／/]', f):
            x = x.strip()
            if 2 <= len(x) <= 12:
                out.append(x)
            c = _clean_tail(x)
            if c != x and 2 <= len(c) <= 12:
                out.append(c)
    return out


def ledger_case_rows():
    """台账案件行（只读）"""
    rows = []
    for sheet in ledger_ops.SHEETS_CASE:
        for r in ledger_ops.read_rows(sheet):
            nm = str(r.get('案件') or '').strip()
            if not nm:
                continue
            rows.append({'sheet': sheet, 'row': r.get('_row'), '案件': nm,
                         '委托人': str(r.get('委托人') or '').strip(),
                         '案件类别': str(r.get('案件类别') or '').strip(),
                         '状态': str(r.get('最新状态') or '').strip(),
                         '案号': str(r.get('案号') or '').strip(),
                         '法院': str(r.get('法院/仲裁') or '').strip(),
                         '开庭时间': str(r.get('开庭时间') or '').strip(),
                         '当前进度': str(r.get('当前进度') or '').strip(),
                         '进度日志': str(r.get('进度日志') or '').strip(),
                         '承办律师': str(r.get('承办律师') or '').strip(),
                         '标的额': r.get('标的额'), '基础律师费': r.get('基础律师费')})
    return rows


def _variants(s):
    """比较用变体：原文 + 去掉行政后缀（省/市/县/区/镇）。
    ⚠️ 只用于**比较**，不改台账、不改夹名。"""
    s = str(s or '').strip()
    out = {s}
    if s:
        out.add(re.sub(r'[省市县区镇]', '', s))
    return {x for x in out if x}


def _hit(needle, hay_variants):
    """needle（含变体）是否命中 hay 的任一变体（双向包含）。返回命中的那对，便于写依据。"""
    for nv in _variants(needle):
        if len(nv) < 2:
            continue
        for hv in hay_variants:
            if nv in hv or hv in nv:
                return nv if nv in hv else hv
    return None


# 批 35：企业字号尾缀（匹配专用变体，只剥尾、不改动任何数据）。
# 背景：夹名常用简称（「仲-厦门向上沟通VS厦门沧珩」），台账行是全名
# （「厦门向上沟通营销策划vs厦门沧珩实业-…」）——整段双向包含失败 → 拒认。
# 办法：当事人片段循环剥行业尾缀得「核心词」，核心词命中给 +1（弱于全名 +2）。
# 门槛：核心词 ≥4 字才参与；行业尾缀白名单循环剥，剥到 <4 字自然失效。
# 批 71：增补公司类后缀（有限/责任公司等）——夹名简称常剥到「新余钦丰祥」一档（实踩回归）。
_BIZ_TAIL_RE = re.compile(
    r'(?:有限责任公司|有限公司|公司|营销策划|供应链|合作社|事务所|集团|实业|科技|工贸|饲料|生物|农业|化工|物流|'
    r'建设|开发|投资|咨询|设计|装饰|环境|材料|制品|机械|电器|电子|网络|信息|商贸|'
    r'商务|经贸|进出口|贸易|服务)+$')


def _biz_core(s):
    """当事人片段 → 去企业字号尾缀的核心词（只用于比较，不改任何数据）"""
    s = _clean_tail(str(s or '').strip())
    prev = None
    while prev != s:
        prev = s
        s = _BIZ_TAIL_RE.sub('', s).strip()
    return s


def _score_match(folder_name, row):
    """夹名 vs 台账行 → (分数, 依据列表)。规则公开可复核，不做模糊猜测。"""
    fn = _strip_folder(folder_name)
    if not fn:
        return 0, []
    fn_var = _variants(fn)
    score, why = 0, []
    frags = [_clean_tail(x) or x for x in _party_frags(row['案件'])]
    for f in frags:
        h = _hit(f, fn_var)
        if h:
            score += 2
            why.append('当事人片段「%s」' % h[:12])
            continue
        core = _biz_core(f)
        if core != f and len(core) >= 4:
            h = _hit(core, fn_var)
            if h:
                score += 2
                why.append('当事人核心词「%s」' % h[:12])
                continue
        # 批 71：复合片段（顿号/逗号拼接的多主体）逐个主体再试——夹名常只留「首主体等」，
        # 整段匹配必对不上（实踩：「新余钦丰祥农业开发公司、孔祥斌…」→ 夹名只写「新余钦丰祥等」）。
        if re.search(r'[、，,／/]', f):
            for sub in re.split(r'[、，,／/]', f):
                sub = sub.strip()
                if not sub:
                    continue
                hh = _hit(sub, fn_var)
                if not hh:
                    sc = _biz_core(sub)
                    if sc != sub and len(sc) >= 4:
                        hh = _hit(sc, fn_var)
                if hh and all(('「%s」' % hh[:10]) not in w for w in why):
                    score += 2
                    why.append('当事人核心词「%s」' % hh[:12])
                    break
    used = 0
    for nm in _sub_names(row['案件'], frags):
        if used >= 2:
            break
        h = _hit(nm, fn_var)
        if h and all(('「%s」' % h[:10]) not in w for w in why):
            score += 1
            why.append('主体名「%s」' % h[:10])
            used += 1
    h = _hit(client_core(row['委托人']), fn_var)
    if h:
        score += 1
        why.append('委托人「%s」' % h[:12])
    if row['案件类别'] and _hit(row['案件类别'], fn_var):
        score += 1
        why.append('类别「%s」' % row['案件类别'])
    return score, why


def _timeline_from_row(row):
    """台账 J 列（进度日志）+ I 列 → 时间轴条目。
    ⚠️ J 列条目只写 MM.DD（无年份）→ 年份以台账原文为准，不猜。"""
    items = []
    for ln in str(row.get('进度日志') or '').splitlines():
        ln = ln.strip()
        m = re.match(r'^(\d{1,2})\.(\d{1,2})[：:]\s*(.+)$', ln)
        if m:
            items.append({'月日': '%02d.%02d' % (int(m.group(1)), int(m.group(2))),
                          '事项': m.group(3).strip(), '来源': '进度日志'})
        elif ln:
            items.append({'月日': '', '事项': ln, '来源': '进度日志'})
    cur = str(row.get('当前进度') or '').strip()
    if cur:
        m = re.match(r'^(\d{1,2})\.(\d{1,2})[：:]\s*(.+)$', cur)
        items.insert(0, {'月日': ('%02d.%02d' % (int(m.group(1)), int(m.group(2)))) if m else '',
                         '事项': (m.group(3).strip() if m else cur), '来源': '当前进度'})
    return items[:40]


def match_case_ledger(folder_name, rows=None):
    """案件夹 → 台账行（可解释匹配）：分数 ≥3 且唯一最高分才自动认定，否则交人工。
    rows 可传入 `ledger_case_rows()` 的缓存结果（批量比对时只读一次台账）。"""
    cands = []
    for row in (rows if rows is not None else ledger_case_rows()):
        sc, why = _score_match(folder_name, row)
        if sc:
            cands.append({'score': sc, 'why': why, 'row': row})
    cands.sort(key=lambda x: -x['score'])
    top = cands[0] if cands else None
    second = cands[1]['score'] if len(cands) > 1 else 0
    confident = bool(top and top['score'] >= 3 and top['score'] > second)
    out = {'matched': None, 'candidates': [], 'rule': '当事人片段全名命中 +2；简称时剥企业字号尾缀的核心词命中 +2；'
           '主体名/委托人/类别各 +1；≥3 分且唯一最高才算认定'}
    if top:
        out['top_score'] = top['score']
        out['secondary_score'] = second
    if confident:
        row = top['row']
        out['matched'] = {'sheet': row['sheet'], 'row': row['row'], '案件': row['案件'],
                          '委托人': row['委托人'], '案件类别': row['案件类别'],
                          '状态': row['状态'], '状态合规': ledger_ops.status_is_rule_ok(row['状态']),
                          '案号': row['案号'], '法院': row['法院'],
                          '开庭时间': row['开庭时间'], '当前进度': row['当前进度'],
                          '承办律师': row.get('承办律师', ''),
                          '标的额': str(row['标的额'] or ''), '基础律师费': str(row['基础律师费'] or ''),
                          'match_score': top['score'], 'match_why': top['why'],
                          'timeline': _timeline_from_row(row)}
    out['candidates'] = [{'score': c['score'], 'why': c['why'], '案件': c['row']['案件'],
                          'sheet': c['row']['sheet'], 'row': c['row']['row']}
                         for c in cands[:5] if c['score'] >= 2]
    return out


# ---------------------------------------------------------------- 发票归案（批 30）
#
# 爸爸 2026-09-13 定：**案卷一份 + 公共区按年月一份**。
#   正本 → `<案卷>\05-费用票据\<新名>`（跟案走，归档时随夹迁移）
#   副本 → `<公共发票库>\<YYYY-MM>\<新名>`（便于按月盘点）
# 纪律：只新增不覆盖（重名自动 -2/-3）、原件不改内容、移动而非复制到案卷（不留残件）、
#       intake-log 留痕（原名 ↔ 新名）、公共库维护只追加的 `_发票索引.md`。
INVOICE_LIB_DIR = '发票库'
INVOICE_FEE_DIR = '05-费用票据'
INVOICE_INDEX_NAME = '_发票索引.md'
UNSAFE_FN = re.compile(r'[\\/:*?"<>|\n\r\t]')


def _safe_fn(s, maxlen=110):
    return (UNSAFE_FN.sub('', str(s or '')).strip() or 'unnamed')[:maxlen]


def invoice_lib_root():
    """公共发票库根（按年月分）——`CASE_OPS_INVOICE_ROOT` 可覆盖（测试隔离用）"""
    return os.environ.get('CASE_OPS_INVOICE_ROOT') or os.path.join(biz_root(), INVOICE_LIB_DIR)


def suggest_invoice_name(fields, orig_name):
    """建议文件名：`YYYYMMDD_发票_<金额>元_<项目或对方>.<ext>`

    只拼**真抽到的**字段；一个都没抽到就保留原名（改名不是目的，认得出才有用）。
    """
    ext = (os.path.splitext(str(orig_name or ''))[1] or '.pdf').lower()
    d = str((fields or {}).get('开票日期') or '').replace('-', '')
    amt = str((fields or {}).get('金额') or '')
    tail = str((fields or {}).get('项目') or '')
    if not tail:
        ours = (fields or {}).get('疑似我方') or ''
        others = [x for x in ((fields or {}).get('单位') or []) if x and x != ours]
        tail = others[0] if others else ''
    parts = ([d] if d else []) + ['发票'] + (['%s元' % amt] if amt else []) + ([tail] if tail else [])
    if not d and not amt and not tail:
        return _safe_fn(orig_name)
    return _safe_fn('_'.join(parts) + ext)


def _free_name(name, dirs):
    """在若干目录里都不冲突的首个可用名（重名加 -2/-3…）"""
    stem, ext = os.path.splitext(name)
    cand, i = name, 1
    while any(os.path.exists(os.path.join(d, cand)) for d in dirs):
        i += 1
        cand = '%s-%d%s' % (stem, i, ext)
    return cand


def plan_invoice_filing(case_dir, staged, fields, month=None, newname=None):
    """发票归案方案（**只算不改**）：正本 + 副本落点、最终文件名、警告"""
    case_dir = os.path.abspath(case_dir)
    staged = os.path.abspath(staged)
    root = os.path.abspath(case_root())
    out = {'ok': False, 'warnings': [], 'case_dir': case_dir, 'staged': staged}
    rel = os.path.relpath(case_dir, root) if case_dir.startswith(root + os.sep) else '..'
    if not (case_dir.startswith(root + os.sep) and os.sep not in rel and rel not in ('.', '..')):
        out['warnings'].append('案件夹不在案卷根的第一层（拒绝）')
        return out
    if not os.path.isdir(case_dir):
        out['warnings'].append('案件夹不存在')
        return out
    if not os.path.isfile(staged):
        out['warnings'].append('待归位文件不存在：%s' % staged)
        return out
    fee_dir = os.path.join(case_dir, INVOICE_FEE_DIR)
    if not os.path.isdir(fee_dir):
        out['warnings'].append('该案没有 %s 目录（先用「开新案」建卷或补件）' % INVOICE_FEE_DIR)
        return out
    month = _safe_fn(month or fields.get('月') or datetime.now().strftime('%Y-%m'), 10)
    lib_dir = os.path.join(invoice_lib_root(), month)
    final = _free_name(newname or suggest_invoice_name(fields, os.path.basename(staged)),
                       [fee_dir, lib_dir])
    out.update({'ok': True, 'newname': final, 'month': month, 'lib_dir': lib_dir,
                'fee_dir': fee_dir,
                'dst_main': os.path.join(fee_dir, final),
                'dst_copy': os.path.join(lib_dir, final),
                'orig_name': os.path.basename(staged)})
    return out


def _lib_index_add(case_dir, month, name, dst_main):
    """公共库索引只追加（同路径已登记就跳过）"""
    root = invoice_lib_root()
    p = os.path.join(root, INVOICE_INDEX_NAME)
    rel = os.path.relpath(dst_main, root) if dst_main.startswith(root + os.sep) else dst_main
    if not os.path.isfile(p):
        try:
            os.makedirs(root, exist_ok=True)
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write('# 发票索引（法律工作台自动维护 · **只追加不删除**）\n\n'
                        '> 公共区副本与案卷正本的对照表；案卷 `05-费用票据` 里的才是正本。\n\n'
                        '| 归位日期 | 月份 | 文件 | 案卷 | 案卷内相对路径 |\n|---|---|---|---|---|\n')
        except OSError:
            return None
    if os.path.isfile(p):
        if rel in open(p, encoding='utf-8', errors='replace').read():
            return p
        try:
            with open(p, 'a', encoding='utf-8', newline='\n') as f:
                f.write('| %s | %s | %s | %s | %s |\n'
                        % (datetime.now().strftime('%Y-%m-%d'), month, name,
                           os.path.basename(case_dir), rel))
        except OSError:
            return None
    return p


def apply_invoice_filing(plan):
    """执行归案：移动 staged → 案卷正本；复制一份 → 公共库；追加 intake-log + 索引"""
    if not plan.get('ok'):
        raise ValueError('方案不可执行：%s' % '；'.join(plan.get('warnings') or []))
    for d in (os.path.dirname(plan['dst_main']), plan['lib_dir']):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(plan['dst_main']) or os.path.exists(plan['dst_copy']):
        raise FileExistsError('目标已存在，拒绝覆盖：%s' % plan['newname'])
    shutil.move(plan['staged'], plan['dst_main'])            # 原件进案卷（正本）
    if not os.path.isfile(plan['dst_main']):
        raise RuntimeError('移动失败（源文件仍在？）：%s' % plan['staged'])
    shutil.copy2(plan['dst_main'], plan['dst_copy'])          # 公共库留副本
    logp = _append_intake_log(plan['case_dir'], [{
        'src': plan['dst_main'], 'dst': plan['dst_main'],
        'name': plan['orig_name'], 'newname': plan['newname']}])
    idx = _lib_index_add(plan['case_dir'], plan['month'], plan['newname'], plan['dst_main'])
    return {'main': plan['dst_main'], 'copy': plan['dst_copy'], 'name': plan['newname'],
            'month': plan['month'], 'intake_log': logp, 'index': idx}


# ---------------------------------------------------------------- 归档区导入（批 39h）
#
# 爸爸 2026-09-18：`批量催收件` / `差旅报销` 两个归档区要做**导入入口**。
# 照批 30「上传发票 → 归案」同一套模式（暂存 → 本机识别 → 人确认 → 落盘）。
#
# 两区结构与去向（**人工选，正文认不出归属**——同批 30「案件由人下拉选」的结论）：
#   批量催收件 → `<案卷根>\批量催收件\<对象名>\<文件名>`
#   差旅报销   → `<案卷根>\差旅报销\<YYYY-MM>\<交通方式>\<文件名>`
# **不写台账** · 只新增不覆盖（重名 -2） · intake-log 追加留痕 · 原件移动（不留残件）。

#: 归档夹统一容器（`_` 前缀让它排在案卷层最前，与案子一眼分开）
ARCHIVE_BASE = '_归档区'
ARCHIVE_ZONES = {
    '顾问单位': {
        'kind': 'client',
        'desc': '法律顾问/合同审查客户件（非诉讼案；按客户一夹）',
        'label': '客户名',
    },
    '批量催收件': {
        'kind': 'collection',
        'desc': '未立案催收件（台账与案卷均无对应案；按对象一夹）',
        'label': '对象名',
    },
    '差旅报销': {
        'kind': 'travel',
        'desc': '差旅票据（按 YYYY-MM 归档，报销用；对应案件放索引）',
        'label': '月份',
        'subs': ['打车', '动车', '机票', '住宿', '过路', '其他'],
    },
}


def archive_zone_root(zone):
    r"""归档区根目录：`<案卷根>\_归档区\<zone>`（白名单校验）

    ⚠️ 2026-09-18 批 39i：归档夹已从案卷层**集中到 `_归档区\`**；
    保留旧位置（`<案卷根>\<zone>`）回退——防迁移中途或历史数据找不到。
    """
    if zone not in ARCHIVE_ZONES:
        raise ValueError('未知归档区：%s（只认 %s）' % (zone, '/'.join(ARCHIVE_ZONES)))
    p = os.path.join(case_root(), ARCHIVE_BASE, zone)
    if os.path.isdir(p):
        return p
    legacy = os.path.join(case_root(), zone)          # 旧位置回退
    if os.path.isdir(legacy):
        return legacy
    return p                                          # 首次导入时按新位置建


def archive_zone_scan(zone):
    """归档区清单（**只读**）：按子夹分组 + 留痕尾部"""
    root = archive_zone_root(zone)
    z = ARCHIVE_ZONES[zone]
    out = {'zone': zone, 'root': root, 'exists': os.path.isdir(root),
           'kind': z['kind'], 'desc': z['desc'], 'label': z['label'],
           'subs': z.get('subs') or [], 'groups': [], 'total': 0, 'trace': None}
    if not out['exists']:
        return out
    for m in sorted(os.listdir(root)):
        d = os.path.join(root, m)
        if not os.path.isdir(d) or m.startswith('.'):
            continue
        files, size = [], 0
        for r, ds, fs in os.walk(d):
            for f in sorted(fs):
                if f.startswith('_'):
                    continue
                fp = os.path.join(r, f)
                try:
                    size += os.path.getsize(fp)
                except OSError:
                    pass
                files.append(os.path.relpath(fp, d).replace(os.sep, '/'))
        if files:
            out['groups'].append({'name': m, 'count': len(files),
                                  'size': size, 'sample': files[:6]})
            out['total'] += len(files)
    for tn in ('_批量催收件留痕.md', '_差旅报销留痕.md', '_顾问件归入留痕.md'):
        tp = os.path.join(root, tn)
        if os.path.isfile(tp):
            try:
                txt = io.open(tp, encoding='utf-8', errors='replace').read()
                out['trace'] = {'file': tn, 'tail': txt.strip().splitlines()[-14:]}
            except OSError:
                pass
            break
    return out


def plan_archive_import(zone, staged, group='', sub='', month=None, newname=None):
    """归档导入方案（**只算不改**）：目标路径 + 冲突改名 + 警告

    group：批量催收件＝对象名；差旅报销＝忽略（用 month）
    sub  ：差旅报销的交通方式（打车/动车/…）
    """
    staged = os.path.abspath(staged)
    out = {'ok': False, 'warnings': [], 'zone': zone, 'staged': staged}
    if zone not in ARCHIVE_ZONES:
        out['warnings'].append('未知归档区：%s' % zone)
        return out
    if not os.path.isfile(staged):
        out['warnings'].append('待导入文件不存在：%s' % staged)
        return out
    root = archive_zone_root(zone)
    z = ARCHIVE_ZONES[zone]
    if z['kind'] == 'travel':
        mm = _safe_fn(month or datetime.now().strftime('%Y-%m'), 10)
        if not re.match(r'^\d{4}-\d{2}$', mm):
            out['warnings'].append('月份格式应为 YYYY-MM（收到 %s）' % mm)
            return out
        # ⚠️ 批 39h 实踩：原先写 `sub or '其他'` → **空值被静默兜底**，
        #    「必填为空拒绝生成」的纪律被绕过（空值也能导入成功）。
        #    → 空值直接拒绝，不猜不填。
        if not str(sub or '').strip():
            out['warnings'].append('必须选「交通方式」（%s）' % '/'.join(z.get('subs') or []))
            return out
        subn = _safe_fn(sub, 12)
        if z.get('subs') and subn not in z['subs']:
            out['warnings'].append('交通方式「%s」不在 %s 里（请用 %s）'
                                   % (subn, z['subs'], '/'.join(z['subs'])))
            return out
        dst_dir = os.path.join(root, mm, subn)
    else:
        # ⚠️ 校验必须在 `_safe_fn` **之前**——`_safe_fn('')` 会返回 'unnamed' 兜底，
        #    放后面 `if not grp` 永远为假 → 空对象名落成 `unnamed\` 文件夹（批 39h 实踩）。
        if not str(group or '').strip():
            out['warnings'].append('必须指定对象名（归档区按对象一夹）')
            return out
        grp = _safe_fn(str(group).strip(), 40)
        dst_dir = os.path.join(root, grp)
    final = _free_name(newname or _safe_fn(os.path.basename(staged)), [dst_dir])
    out.update({'ok': True, 'dst_dir': dst_dir, 'newname': final,
                'dst': os.path.join(dst_dir, final),
                'orig_name': os.path.basename(staged),
                'group': group, 'sub': sub, 'month': month})
    return out


def _archive_trace_add(zone, moved):
    """留痕只追加（同目标路径已登记就跳过）"""
    root = archive_zone_root(zone)
    tn = {'批量催收件': '_批量催收件留痕.md',
          '差旅报销': '_差旅报销留痕.md',
          '顾问单位': '_顾问件归入留痕.md'}.get(zone, '_归档留痕.md')
    p = os.path.join(root, tn)
    try:
        os.makedirs(root, exist_ok=True)
        if not os.path.isfile(p):
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write('# %s 导入留痕（法律工作台自动维护 · **只追加**）\n\n'
                        '> 记录「原名 ↔ 现位置」；原件已移动，可据此回溯。\n\n'
                        '| 时间 | 原名 | 现位置 |\n|---|---|---|\n' % zone)
        txt = open(p, encoding='utf-8', errors='replace').read()
        with open(p, 'a', encoding='utf-8', newline='\n') as f:
            for orig, dst in moved:
                rel = os.path.relpath(dst, root).replace(os.sep, '/')
                if rel in txt:
                    continue
                f.write('| %s | `%s` | `%s` |\n'
                        % (datetime.now().strftime('%Y-%m-%d %H:%M'), orig, rel))
    except OSError:
        return None
    return p


def apply_archive_import(plan, confirm=False):
    """执行归档导入：移动 staged → 归档区；追加留痕。**未 confirm 拒绝执行**"""
    if not confirm:
        raise ValueError('未确认，拒绝执行（需 confirm=True）')
    if not plan.get('ok'):
        raise ValueError('方案不可执行：%s' % '；'.join(plan.get('warnings') or []))
    staged, dst = plan['staged'], plan['dst']
    if not os.path.isfile(staged):
        raise ValueError('源文件不存在：%s' % staged)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(staged, dst)
    logp = _archive_trace_add(plan['zone'], [(plan['orig_name'], dst)])
    return {'zone': plan['zone'], 'dst': dst, 'trace': logp,
            'orig': plan['orig_name'], 'newname': plan['newname']}


def invoice_library():
    """公共发票库清单（**只读**）：按月分组 + 索引尾部"""
    root = invoice_lib_root()
    out = {'root': root, 'exists': os.path.isdir(root), 'months': [], 'total': 0,
           'index': None, 'index_tail': []}
    if not out['exists']:
        return out
    for m in sorted(os.listdir(root)):
        d = os.path.join(root, m)
        if not os.path.isdir(d):
            continue
        files = []
        for f in sorted(os.listdir(d)):
            fp = os.path.join(d, f)
            if os.path.isfile(fp):
                files.append({'name': f, 'kb': round(os.path.getsize(fp) / 1024.0, 1),
                              'mtime': datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%m-%d')})
        out['months'].append({'month': m, 'count': len(files), 'files': files})
        out['total'] += len(files)
    idx = os.path.join(root, INVOICE_INDEX_NAME)
    if os.path.isfile(idx):
        out['index'] = idx
        lines = open(idx, encoding='utf-8', errors='replace').read().splitlines()
        out['index_tail'] = [l for l in lines if l.startswith('|')][-11:]
    return out


def selftest():
    import tempfile
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='case_selftest_')
    os.environ['CASE_OPS_ROOT'] = tmp
    os.environ['CASE_OPS_ARCHIVE'] = os.path.join(tmp, '_已结案')
    folder = case_folder_name('民-A-26016', '盐城海维', '曹琪立等', '买卖合同纠纷')
    chk('案件夹命名', folder == '民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷')
    chk('非法编号被拒', _raises(lambda: case_folder_name('26016', 'a', 'b', 'c')))
    chk('空字段被拒', _raises(lambda: case_folder_name('民-A-26016', '', 'b', 'c')))

    # 建案（跳过 pipeline，自测不依赖 My-case 模板）
    plan = create_new_case('民-A-26016', '盐城海维', '曹琪立等', '买卖合同纠纷',
                           '诉讼', with_pipeline=False)
    case = plan['path']
    chk('目录树建齐（含 00-收件箱）',
        all(os.path.isdir(os.path.join(case, d)) for d in plan['dirs']))
    chk('intake-log 已建', os.path.isfile(os.path.join(case, 'intake-log.md')))
    chk('重复立卷被拒', _raises_exit(lambda: create_new_case(
        '民-A-26016', '盐城海维', '曹琪立等', '买卖合同纠纷', with_pipeline=False)))
    exec_case = create_new_case('执-A-26030', '甲', '乙', '借款', '执行', with_pipeline=False)
    _np = plan_new_case('民-A-26999', '甲公司', '乙公司', '买卖合同纠纷', '诉讼',
                        court='思明区法院', case_no_court='(2026)闽0203民初1号', lawyer='张律师')
    chk('台账草稿行按台账规范拼 C 列', _np['ledger_draft']['案件'] == '甲公司vs乙公司-买卖合同纠纷')
    chk('草稿行带法院/案号/承办律师', _np['ledger_draft']['法院/仲裁'] == '思明区法院'
        and _np['ledger_draft']['案号'] == '(2026)闽0203民初1号'
        and _np['ledger_draft']['承办律师'] == '张律师')
    chk('草稿行进度带 [待确认]', _np['ledger_draft']['当前进度'].startswith('[待确认]'))
    chk('立卷方案含《材料怎么放》路径与正文',
        _np['guide_path'].endswith(GUIDE_NAME) and '00-收件箱' in _np['guide_text'])
    chk('说明含三条铁律与命名政策',
        '只移动、不删除' in _np['guide_text'] and 'YYYYMMDD_类别_描述' in _np['guide_text'])
    chk('执行案说明多 09/10 两行', '09-财产查控' in inbox_guide_text(
        'f', '执-1', 'a', 'b', 'c', '执行'))
    chk('执行案加 09/10',
        os.path.isdir(os.path.join(exec_case['path'], '09-财产查控'))
        and os.path.isdir(os.path.join(exec_case['path'], '10-处置分配')))

    # 分类
    samples = {
        '起诉状（草稿）(1).docx': ('08-过程文件', False),   # 带「草稿」标记 → 过程文件
        '起诉状.pdf': ('02-法律文书', False),
        '授权委托书.pdf': ('01-委托手续', False),
        '(2026)粤5203民初2150号民事判决书.pdf': ('04-法院文件', False),
        '增值税电子普通发票.pdf': ('05-费用票据', False),
        '身份证正反面.jpg': ('06-当事人信息', False),
        '微信图片_20250912120000.jpg': ('03-证据材料', True),
        '曹琪立担保函.pdf': ('03-证据材料', False),
        '不明文件.docx': (None, True),
        '执行查控回执.pdf': ('09-财产查控', False),
        # 批 80：一张网立案送达包补词（非凡世家 6 件实踩 4 件不认 + 交纳诉讼费通知归 04）
        '受理案件通知书（BLZT_ERROR_POINT）.pdf': ('04-法院文件', False),
        '廉政监督卡（厦门非凡世家工贸有限公司）.pdf': ('04-法院文件', False),
        '民事诉讼风险提示书厦门市翔安区人民法院.pdf': ('04-法院文件', False),
        '诉讼权利义务告知书（告知当事人用）.pdf': ('04-法院文件', False),
        '厦门非凡世家工贸有限公司_交纳诉讼费用通知书.pdf': ('04-法院文件', False),
        '诉讼费电子发票.pdf': ('05-费用票据', False),   # 双向验：票据正本仍归 05
        '解除合同告知书.docx': ('03-证据材料', False),   # 双向验：裸「告知书」不进 04
    }
    for fn, (want_dir, want_keep) in samples.items():
        c = classify(os.path.join(case, INBOX, fn))
        chk('分类 %s → %s' % (fn[:18], want_dir),
            c['dir'] == want_dir and c['keep'] == want_keep)

    # 入库 dry-run → 不动盘
    inbox = os.path.join(case, INBOX)
    for fn in ['起诉状（草稿）(1).docx', '授权委托书.pdf', '微信图片_20250912120000.jpg',
               '不明文件.docx']:
        open(os.path.join(inbox, fn), 'w', encoding='utf-8').write('x')
    before = len(os.listdir(inbox))
    p1 = plan_intake(case)
    chk('dry-run 未移动文件', len(os.listdir(inbox)) == before)
    chk('dry-run 清单含建议 3 条', sum(1 for i in p1['items'] if i['status'] == '建议') == 3)
    chk('dry-run 需人工 1 条', sum(1 for i in p1['items'] if i['status'] == '需人工') == 1)

    p2, moved, skipped = apply_intake(case)
    chk('apply 移动 3 个', len(moved) == 3)
    chk('需人工项原地保留', os.path.isfile(os.path.join(inbox, '不明文件.docx')))
    d01 = os.path.join(case, '01-委托手续')
    chk('授权委托书已入 01（并按规范改名）',
        any(f.endswith('_委托手续_授权委托书.pdf') for f in os.listdir(d01)))
    newname = [m['newname'] for m in moved if m['name'].startswith('起诉状')][0]
    chk('起诉状（草稿）归 08 且改名合规',
        re.match(r'^\d{8}_过程文件_起诉状', newname) is not None)
    logtxt = open(os.path.join(case, 'intake-log.md'), encoding='utf-8').read()
    chk('intake-log 登记原名与新名',
        '起诉状（草稿）(1).docx' in logtxt and newname in logtxt
        and '微信图片_20250912120000.jpg' in logtxt)
    wxline = [l for l in logtxt.splitlines() if '微信图片' in l][0]
    chk('微信图片保持原名（登记为 保持原名）',
        wxline.count('微信图片_20250912120000.jpg') >= 3 and '保持原名' in wxline)

    # 冲突：同名不同内容 → 跳过
    open(os.path.join(inbox, '授权委托书.pdf'), 'w', encoding='utf-8').write('different')
    p3, moved3, skipped3 = apply_intake(case)
    chk('同名不同内容 → 冲突跳过',
        len(moved3) == 0 and any(i['status'].startswith('冲突') for i in p3['items']))
    open(os.path.join(inbox, '授权委托书.pdf'), 'w', encoding='utf-8').write('x')
    p4, moved4, _ = apply_intake(case)
    chk('同内容重复 → 判为重复不覆盖',
        len(moved4) == 0 and any(i['status'] == '重复(同内容)' for i in p4['items']))

    # 越界与归档
    chk('越界路径被拒', _raises_exit(lambda: plan_intake(os.path.dirname(tmp))))
    # v2 组件补齐（纯新增）
    legacy = create_new_case('民-A-26077', '甲', '乙', '测试', '诉讼', with_pipeline=False)
    lp = os.path.join(legacy['path'], INBOX)
    os.rmdir(lp)
    r0 = ensure_inbox(legacy['path'], exec_case=True)
    chk('已存在案件夹补件（dry-run 列出收件箱+09/10）',
        len(r0['planned']) >= 3 and not os.path.isdir(lp))
    r1 = ensure_inbox(legacy['path'], exec_case=True, apply=True)
    chk('补件 apply 后目录就位',
        os.path.isdir(lp) and os.path.isdir(os.path.join(legacy['path'], '09-财产查控'))
        and os.path.isdir(os.path.join(legacy['path'], '10-处置分配')))
    chk('已有 intake-log 不重复列入计划',
        not any(p.endswith('intake-log.md') for p in r0['planned'])
        and os.path.isfile(os.path.join(legacy['path'], 'intake-log.md')))
    ap = plan_archive(case, '2026')
    chk('归档目标路径正确',
        ap['dst'] == os.path.join(tmp, '_已结案', '2026',
                                  '民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷'))
    ap2 = apply_archive(case, '2026')
    chk('归档后源目录消失且目标存在',
        (not os.path.exists(case)) and os.path.isdir(ap2['dst']))
    # 批 19：业务线（律师函夹 / 差旅发票按文件名挂案）
    _old_biz = os.environ.get('CASE_OPS_BIZ_ROOT')
    biz = os.path.join(tmp, 'biz')
    os.makedirs(os.path.join(biz, LETTER_DIR, '某债务人'), exist_ok=True)
    with open(os.path.join(biz, LETTER_DIR, '某债务人', '律师函.pdf'), 'wb') as f:
        f.write(b'x')
    os.makedirs(os.path.join(biz, TRAVEL_DIR, '8月'), exist_ok=True)
    for _fn in ('2026-08-14甲公司立案顺风车.pdf', '260818_56.00_北京滴滴出行科技有限公司.pdf',
                '26359134012000024473.pdf'):
        with open(os.path.join(biz, TRAVEL_DIR, '8月', _fn), 'wb') as f:
            f.write(b'x')
    os.environ['CASE_OPS_BIZ_ROOT'] = biz
    _lf = letter_folders()
    chk('律师函夹概览（只读）', _lf['exists'] and len(_lf['folders']) == 1
        and _lf['folders'][0]['files'] == 1)
    _rows_t = [{'案件': '甲公司诉乙公司-买卖合同纠纷', '委托人': '甲公司',
                'sheet': '案件', 'row': 4}]
    _m1 = match_invoice('2026-08-14甲公司立案顺风车.pdf', _rows_t)
    chk('发票按文件名挂到对应案', bool(_m1['cases'])
        and _m1['cases'][0]['案件'] == '甲公司诉乙公司-买卖合同纠纷')
    chk('平台名发票不硬套（通用字不成线索）',
        not match_invoice('260818_56.00_北京滴滴出行科技有限公司.pdf', _rows_t)['cases'])
    _m3 = match_invoice('26359134012000024473.pdf', _rows_t)
    chk('纯发票号认不出且明说', not _m3['cases'] and '中文线索' in _m3['why'])
    _tv = travel_invoices(_rows_t)
    chk('差旅发票索引：3 张认出 1 张', len(_tv['items']) == 3 and _tv['recognized'] == 1)

    # ---- 批 38：散件归属匹配器回归（此前零覆盖，故『应链科』跨词碎片漏了两轮）
    _rows_s = _rows_t + [{'案件': '张友谅vs福建京邦达供应链科技有限公司-噪声污染责任纠纷',
                          '委托人': '张友谅', 'sheet': '其他案件', 'row': 7}]
    _s1 = scatter_match('甲公司诉乙公司-买卖合同纠纷起诉状.docx', _rows_s, '')
    chk('散件：当事人长片段命中（文件名带完整案件名）',
        bool(_s1['cases']) and _s1['cases'][0]['案件'] == '甲公司诉乙公司-买卖合同纠纷')
    _s2 = scatter_match('律师函-黎林培致安徽速煦供应链科技有限公司.docx', _rows_s, '黎林培')
    chk('散件：跨词碎片「应链科」不得认成京邦达案（批38实踩回归）',
        not _s2['cases'] or _s2['cases'][0]['案件'] != '张友谅vs福建京邦达供应链科技有限公司-噪声污染责任纠纷')
    _s3 = scatter_match('供应链科技有限公司对账单.xlsx', _rows_s, '')
    chk('散件：行业恒通用词（供应链/科技）不成线索', not _s3['cases'])
    _rows_s2 = _rows_t + [{'案件': '非凡世家系列案', '委托人': '吴小明',
                           'sheet': '其他案件', 'row': 13}]
    _s4 = scatter_match('2026-08-14非凡世家翔安法院立案顺风车.pdf', _rows_s2, '')
    chk('散件：真字号「非凡世家」仍能认（收紧未误杀）',
        bool(_s4['cases']) and _s4['cases'][0]['案件'] == '非凡世家系列案')
    _s5 = scatter_match('还款协议.pdf', _rows_s, '')
    chk('散件：无线索时明说认不出，不硬套',
        not _s5['cases'] and ('找不到' in _s5.get('why', '') or '不足以' in _s5.get('why', '')
                              or '无当事人线索' in _s5.get('why', '')))
    if _old_biz is None:
        os.environ.pop('CASE_OPS_BIZ_ROOT', None)
    else:
        os.environ['CASE_OPS_BIZ_ROOT'] = _old_biz

    # 批 20：系列案总夹 → 子案件夹补 00-收件箱（只新增空目录）
    series = os.path.join(tmp, 'A系列总夹')
    os.makedirs(os.path.join(series, '子甲'), exist_ok=True)
    os.makedirs(os.path.join(series, '子乙', INBOX), exist_ok=True)
    os.makedirs(os.path.join(series, PIPELINE_DIR), exist_ok=True)
    _sp = series_inbox_plan(series)
    chk('系列案体检：只算子案件夹（排除 pipeline/收件箱）',
        sorted(x['name'] for x in _sp['subs']) == sorted(['子甲', '子乙']))
    chk('系列案体检：列出缺收件箱的子夹', _sp['todo'] == ['子甲'])
    _sa = apply_series_inbox(series)
    chk('补建后子夹收件箱就位', os.path.isdir(os.path.join(series, '子甲', INBOX))
        and len(_sa['made']) == 1)
    chk('已有收件箱的子夹不被重复处理', _sa['skipped'] == ['子乙'])
    chk('系列案补建：总夹本身零变化',
        sorted(os.listdir(series)) == sorted(['子甲', '子乙', PIPELINE_DIR]))
    chk('非第一层案件夹被拒', _raises_exit(
        lambda: series_inbox_plan(os.path.join(series, '子甲'))))

    # 批 21：系列案总夹的子案件夹各补一份《材料怎么放》（只新增，不覆盖不移动）
    _pg = plan_guides(root=tmp, names=['A系列总夹'], subs=['A系列总夹'])
    _lv2 = [i for i in _pg['items'] if i['level'] == 2]
    chk('子夹展开：总夹 1 + 子夹 2', len(_pg['items']) == 3 and len(_lv2) == 2)
    chk('子夹 key 带父夹前缀且指向子夹路径',
        {i['key'] for i in _lv2} == {'A系列总夹/子甲', 'A系列总夹/子乙'}
        and all(os.path.dirname(i['guide_path']) == os.path.join(series, i['folder'])
                for i in _lv2))
    chk('子夹计划全部待补', set(_pg['todo']) == {'A系列总夹', 'A系列总夹/子甲', 'A系列总夹/子乙'})
    chk('子夹文本标明所属总夹',
        all('所属系列案总夹' in i['text'] and '`A系列总夹`' in i['text'] for i in _lv2))
    chk('子夹不再被当成系列案总夹', all(not i['series'] for i in _lv2))
    chk('不传 subs 时不展开子夹',
        len(plan_guides(root=tmp, names=['A系列总夹'])['items']) == 1)
    chk('可直接点名子夹（names 用「总夹/子夹」）',
        [(i['key'], i['level'], i['parent'])
         for i in plan_guides(root=tmp, names=['A系列总夹/子甲'])['items']]
        == [('A系列总夹/子甲', 2, 'A系列总夹')])
    _ag = apply_guides(root=tmp, names=['A系列总夹'], subs=['A系列总夹'])
    chk('写入 3 份（总夹 1 + 子夹 2）', len(_ag['written']) == 3
        and os.path.isfile(os.path.join(series, '子甲', GUIDE_NAME))
        and os.path.isfile(os.path.join(series, '子乙', GUIDE_NAME)))
    chk('子夹除新增说明外零变化',
        set(os.listdir(series)) == {'子甲', '子乙', PIPELINE_DIR, GUIDE_NAME}
        and set(os.listdir(os.path.join(series, '子甲'))) == {INBOX, GUIDE_NAME})
    _ag2 = apply_guides(root=tmp, names=['A系列总夹'], subs=['A系列总夹'])
    chk('已有《材料怎么放》的夹不重复写', not _ag2['written'] and len(_ag2['skipped']) == 3)

    chk('归档后 intake-log 随夹迁移',
        os.path.isfile(os.path.join(ap2['dst'], 'intake-log.md')))

    # ---- 批 30：发票归案（案卷正本 + 公共库按年月副本）----
    lib = os.path.join(tmp, '发票库')
    os.environ['CASE_OPS_INVOICE_ROOT'] = lib
    inv_case = create_new_case('民-A-26031', '甲公司', '乙公司', '买卖合同纠纷',
                               '诉讼', with_pipeline=False)['path']
    fields = {'开票日期': '2026-09-11', '月': '2026-09', '金额': '37500.00',
              '项目': '律师费', '疑似我方': '甲公司', '单位': ['甲公司', '某某律师事务所']}
    chk('建议名按抽到的字段拼', suggest_invoice_name(fields, 'scanned_001.pdf')
        == '20260911_发票_37500.00元_律师费.pdf')
    chk('字段全空 → 保留原名', suggest_invoice_name({}, '微信图片_2025.pdf') == '微信图片_2025.pdf')
    staged = os.path.join(tmp, '上传暂存.pdf')
    io.open(staged, 'wb').write(b'%PDF-1.4 invoice fake')
    pl = plan_invoice_filing(inv_case, staged, fields)
    chk('归案方案：正本进 05-费用票据、副本进发票库当月',
        pl['ok'] and pl['dst_main'].endswith(os.path.join(INVOICE_FEE_DIR,
                                                          '20260911_发票_37500.00元_律师费.pdf'))
        and pl['dst_copy'].startswith(lib) and pl['month'] == '2026-09')
    chk('方案阶段不落盘', not os.path.exists(pl['dst_main']) and not os.path.exists(pl['dst_copy']))
    r = apply_invoice_filing(pl)
    chk('执行后正本+副本都在，且暂存件已移走',
        os.path.isfile(r['main']) and os.path.isfile(r['copy']) and not os.path.exists(staged))
    chk('两份内容一致（副本是完整拷贝）',
        io.open(r['main'], 'rb').read() == io.open(r['copy'], 'rb').read() == b'%PDF-1.4 invoice fake')
    chk('intake-log 登记原名与新名',
        '上传暂存.pdf' in io.open(r['intake_log'], encoding='utf-8').read()
        and '20260911_发票_37500.00元_律师费.pdf' in io.open(r['intake_log'], encoding='utf-8').read())
    chk('公共库索引已写且含该行',
        r['index'] and os.path.isfile(r['index'])
        and '20260911_发票_37500.00元_律师费.pdf' in io.open(r['index'], encoding='utf-8').read())
    staged2 = os.path.join(tmp, '同名再来.pdf')
    io.open(staged2, 'wb').write(b'%PDF-1.4 dup')
    pl2 = plan_invoice_filing(inv_case, staged2, fields)
    chk('重名自动加 -2（两处都不冲突）', pl2['newname'].endswith('-2.pdf')
        and os.path.basename(pl2['dst_main']) == os.path.basename(pl2['dst_copy']))
    r2 = apply_invoice_filing(pl2)
    chk('第二张也落两处', os.path.isfile(r2['main']) and os.path.isfile(r2['copy']))
    chk('索引按行登记（两张各一行）',
        sum(1 for l in invoice_library()['index_tail'] if l.startswith('| 2026-')) == 2)
    libv = invoice_library()
    chk('公共库清单按月分组且计数正确',
        libv['exists'] and libv['total'] == 2
        and libv['months'][0]['month'] == '2026-09' and libv['months'][0]['count'] == 2)
    staged3 = os.path.join(tmp, '越界试.pdf')
    io.open(staged3, 'wb').write(b'x')
    bad = plan_invoice_filing(tmp, staged3, fields)          # tmp 不是第一层案件夹
    chk('非第一层案件夹被拒（不落盘）', (not bad['ok']) and bad['warnings'])
    no_fee = os.path.join(tmp, '无费用夹案')
    os.makedirs(no_fee, exist_ok=True)
    chk('缺 05-费用票据 时给警告（不硬建）',
        (not plan_invoice_filing(no_fee, staged3, fields)['ok'])
        and not os.path.isdir(os.path.join(no_fee, INVOICE_FEE_DIR)))
    try:
        apply_invoice_filing(bad)
        chk('不可执行方案 apply 抛错', False)
    except ValueError:
        chk('不可执行方案 apply 抛错', True)
    chk('发票归案未惊动台账/案卷其他内容',
        os.path.isfile(os.path.join(inv_case, INVOICE_FEE_DIR,
                                    '20260911_发票_37500.00元_律师费.pdf'))
        and not os.path.exists(os.path.join(inv_case, INBOX, '上传暂存.pdf')))
    os.environ.pop('CASE_OPS_INVOICE_ROOT', None)

    # ---- 批 71：嵌套案件夹映射 + 复合当事人核心词 + 案件名找夹细化
    # （2026-10-15 实踩：法院短信解析「台账命中但没找到对应案件夹」——夹其实存在，查找三处短板）
    _grp71 = os.path.join(tmp, 'A闽兴')
    _nested71 = os.path.join(_grp71, '民-A-26012_福建闽兴VS新余钦丰祥等_买卖合同纠纷')
    os.makedirs(os.path.join(_nested71, INBOX), exist_ok=True)
    _rows71 = [{'sheet': '案件', 'row': 12, '案号': '（2026）闽 0625 民初 2225 号',
                '案件': '福建闽兴vs新余钦丰祥农业开发公司、孔祥斌、何红英、孔令钦-买卖合同纠纷',
                '委托人': '福建闽兴饲料有限公司', '案件类别': '诉讼', '状态': '诉讼中',
                '法院': '', '开庭时间': '', '当前进度': '', '进度日志': '',
                '承办律师': '', '标的额': None, '基础律师费': None}]
    _f71 = folder_by_ledger_row(_rows71)
    chk('批71：嵌套案件夹进映射（一级分组夹不挡）', _f71.get(('案件', 12)) == _nested71)
    _m71 = match_case_ledger('民-A-26012_福建闽兴VS新余钦丰祥等_买卖合同纠纷', _rows71)
    chk('批71：复合当事人核心词命中且唯一（新余钦丰祥）',
        bool(_m71.get('matched')) and _m71['matched']['row'] == 12 and _m71['top_score'] >= 3)
    chk('批71：案由剥离——案件名找夹命中正确夹（不被公共案由带偏）',
        folder_by_case_name(_rows71[0]['案件']) == _nested71)
    _n2a = os.path.join(_grp71, '民-A-26090_福建闽兴VS某某甲等_买卖合同纠纷')
    _n2b = os.path.join(_grp71, '民-A-26091_福建闽兴VS某某乙等_买卖合同纠纷')
    os.makedirs(os.path.join(_n2a, INBOX), exist_ok=True)
    os.makedirs(os.path.join(_n2b, INBOX), exist_ok=True)
    chk('批71：并列无区分度 → 拒认不猜',
        folder_by_case_name('福建闽兴vs某某甲、某某乙-买卖合同纠纷') == '')
    # 嵌套「总夹/子案件夹」的收件箱入库（下载→归位链，批 71）
    _pi71 = plan_intake(_nested71, allow_sub=True)
    chk('批71：嵌套子案件夹可扫描（allow_sub）', _pi71['inbox_exists'] is True)
    chk('批71：默认仍拒嵌套（第一层契约不变）', _raises_exit(lambda: plan_intake(_nested71)))

    for k in ('CASE_OPS_ROOT', 'CASE_OPS_ARCHIVE'):
        os.environ.pop(k, None)
    shutil.rmtree(tmp, ignore_errors=True)
    print('--- selftest %s（%d 项，失败 %d）---' % (
        '全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def _raises_exit(fn):
    """捕获 sys.exit(2/3) 型拒绝"""
    try:
        fn()
    except SystemExit as e:
        return e.code in (2, 3)
    except Exception:
        return True
    return False


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='案卷操作库 v%s（立卷/入库/归档，默认 dry-run）' % VERSION)
    sub = ap.add_subparsers(dest='cmd')

    p_c = sub.add_parser('classify')
    p_c.add_argument('--name', required=True, help='文件名（可含路径）')

    sub.add_parser('tree')

    p_n = sub.add_parser('new')
    p_n.add_argument('--case-no', required=True)
    p_n.add_argument('--client', required=True)
    p_n.add_argument('--opponent', required=True)
    p_n.add_argument('--cause', required=True)
    p_n.add_argument('--type', default='诉讼')
    p_n.add_argument('--no-pipeline', action='store_true')
    p_n.add_argument('--apply', action='store_true')

    p_i = sub.add_parser('intake')
    p_i.add_argument('--case', required=True)
    p_i.add_argument('--apply', action='store_true')

    p_e = sub.add_parser('ensure-inbox', help='给既有案件夹补 00-收件箱 / intake-log（纯新增）')
    p_e.add_argument('--case', required=True)
    p_e.add_argument('--exec', dest='exec_case', action='store_true', help='同时补执行案 09/10')
    p_e.add_argument('--apply', action='store_true')

    p_g = sub.add_parser('guides', help='给既有案件夹补《材料怎么放》（纯新增，不覆盖）')
    p_g.add_argument('--case', default=None, help='只处理指定夹（默认全部）')
    p_g.add_argument('--subs', default=None,
                     help='展开到子案件夹的系列案总夹名（逗号分隔，如 A闽兴）')
    p_g.add_argument('--show', action='store_true', help='打印生成的文本（配合 --case 用）')
    p_g.add_argument('--apply', action='store_true')

    p_si = sub.add_parser('series-inbox',
                          help='系列案总夹：给子案件夹补 00-收件箱（只新增空目录）')
    p_si.add_argument('--case', required=True, help='系列案总夹名（如 A闽兴）')
    p_si.add_argument('--apply', action='store_true')

    p_a = sub.add_parser('archive')
    p_a.add_argument('--case', required=True)
    p_a.add_argument('--year', default=None)
    p_a.add_argument('--apply', action='store_true')

    sub.add_parser('selftest')

    args = ap.parse_args()

    if args.cmd == 'classify':
        p = args.name
        c = classify(p if os.path.isfile(p) else os.path.join(os.getcwd(), p))
        print('目录建议: %s' % c['dir'])
        print('建议文件名: %s' % c['newname'])
        print('保持原名: %s' % c['keep'])
        print('理由: %s [%s]' % (c['reason'], c['status']))
        return 0

    if args.cmd == 'tree':
        print('案件夹（%s）：' % case_root())
        print('  <编号_闽兴VS对方_案由>/')
        for d in TREE_BASE + TREE_EXEC + [PIPELINE_DIR]:
            mark = '（执行案才建）' if d in TREE_EXEC else ''
            print('    ├── %s%s' % (d, mark))
        return 0

    if args.cmd == 'new':
        plan = plan_new_case(args.case_no, args.client, args.opponent, args.cause, args.type)
        print('=== 立卷方案 ===')
        print('案件夹: %s' % plan['folder'])
        print('路径: %s（已存在: %s）' % (plan['path'], plan['exists']))
        print('目录: %s' % ' / '.join(plan['dirs']))
        print('pipeline: %s' % (' '.join(plan['pipeline_cmd'])
                                if not args.no_pipeline else '（--no-pipeline 跳过）'))
        print('台账草稿行（由会话/Web 确认后写入）: %s' % plan['ledger_draft'])
        if not args.apply:
            print('（dry-run，未建目录；加 --apply 执行）')
            return 0
        plan = create_new_case(args.case_no, args.client, args.opponent, args.cause,
                               args.type, with_pipeline=not args.no_pipeline)
        print('已立卷：%s' % plan['path'])
        if plan.get('pipeline_out'):
            print(plan['pipeline_out'])
        print('待办：台账追加草稿行（I 列旧值先回迁 J 列）+ wiki entity 页骨架')
        return 0

    if args.cmd == 'intake':
        plan = plan_intake(args.case)
        print_plan(plan)
        if not args.apply:
            print('（dry-run，未移动；加 --apply 执行）')
            return 0
        plan, moved, skipped = apply_intake(args.case)
        print('已归位 %d 个（跳过 %d 个）' % (len(moved), len(skipped)))
        for it in skipped:
            print('  跳过：%s（%s）' % (it['name'], it['status']))
        print('留痕：%s' % os.path.join(args.case, 'intake-log.md'))
        print('复检建议：重跑 intake（dry-run）应显示收件箱已空或仅剩人工项')
        return 0

    if args.cmd == 'ensure-inbox':
        r = ensure_inbox(args.case, args.exec_case, args.apply)
        print('=== v2 组件补齐（%s）===' % r['case_dir'])
        if not r['planned']:
            print('  （已齐备，无需新增）')
        for p in r['planned']:
            print('  + %s' % p)
        if not args.apply:
            print('（dry-run，未创建；加 --apply 执行）')
            return 0
        print('已新增 %d 项' % r['added'])
        return 0

    if args.cmd == 'guides':
        names = [args.case] if args.case else None
        subs = [s for s in re.split(r'[,\s、]+', args.subs) if s] if args.subs else None
        plan = plan_guides(names=names, subs=subs)
        print('=== 案件夹《材料怎么放》补件（%s）===' % plan['root'])
        for it in plan['items']:
            print('  %s[%s] %s%s（子目录 %d，规则外 %d）'
                  % ('    - ' if it['level'] == 2 else '',
                     '已有·跳过' if it['exists'] else '待补', it['key'],
                     '（执行案）' if it['exec'] else ('（系列案总夹）' if it['series'] else ''),
                     it['dirs'], it['extra']))
        print('合计 %d 夹：待补 %d，已有 %d'
              % (len(plan['items']), len(plan['todo']), len(plan['items']) - len(plan['todo'])))
        if args.show:
            for it in plan['items']:
                if not args.case or args.case in (it['key'], it['folder'], it['parent']):
                    print('\n' + '=' * 60 + '\n' + '【%s】\n' % it['key'] + it['text'])
        if not args.apply:
            print('（dry-run，未写入；加 --apply 执行）')
            return 0
        plan = apply_guides(names=names, subs=subs)
        print('已写入 %d 份；跳过 %d 份' % (len(plan['written']), len(plan['skipped'])))
        for p in plan['written']:
            print('  + %s' % p)
        return 0

    if args.cmd == 'series-inbox':
        plan = series_inbox_plan(args.case)
        print('=== 系列案总夹体检（%s）===' % plan['series'])
        for it in plan['subs']:
            print('  [%s] %-44s intake-log=%s'
                  % ('有' if it['inbox'] else '缺 00-收件箱', it['name'],
                     '有' if it['log'] else '—'))
        print('合计 %d 个子夹：缺收件箱 %d' % (len(plan['subs']), len(plan['todo'])))
        if not args.apply:
            print('（dry-run，未创建；加 --apply 执行）')
            return 0
        plan = apply_series_inbox(args.case)
        print('已新建 %d 个 00-收件箱；跳过 %d 个' % (len(plan['made']), len(plan['skipped'])))
        for p in plan['made']:
            print('  + %s' % p)
        return 0

    if args.cmd == 'archive':
        plan = plan_archive(args.case, args.year)
        print('=== 归档方案 ===')
        print('源: %s' % plan['src'])
        print('目标: %s（已存在: %s）' % (plan['dst'], plan['exists']))
        for f in plan['followup']:
            print('  后续: %s' % f)
        if not args.apply:
            print('（dry-run，未移动；加 --apply 执行）')
            return 0
        plan = apply_archive(args.case, args.year)
        print('已归档：%s' % plan['dst'])
        return 0

    if args.cmd == 'selftest':
        return selftest()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
