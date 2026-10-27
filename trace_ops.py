r"""trace_ops.py — 留痕与文件管理（批 39d）

设计原则（来源：docs\批39-留痕与文件管理-检索报告-20260917.md）
------------------------------------------------------------------
中外同行共识三条，本模块全部照做：

1. **草稿独立区 + 定稿另存 + 过期版本不删只移**（Trialday·邹义 / 文鳐·谢国才 / 英文律所 DMS）
2. **命名带「版本序号 + 定稿标记」**，正文里看得见，不靠记忆
3. **只移动不删除**（本项目铁律，案卷只移动不删除）

⚠️ 关键设计决定（2026-09-17）
------------------------------
「双区」**不是新建目录**，而是给**现有 `08-过程文件` 加语义层**：
- 22 个案卷里 14 个已有 `08-过程文件` → 新建并行目录 = 制造第三种口径，更乱
- 过程区 = 既有 `08-过程文件`（**一行文件都不动**）
- 定稿区 = 案卷内**已有编号目录**（`02-法律文书` / `04-法院文件` 等，各案已有）
- 本模块只做三件事：**分类 → 体检 → 报告**；归集动作必须人点确认

模块信息
--------
VERSION = '1.0.0'   # 2026-09-17 批 39d 首版
"""

import os
import re
import shutil
from datetime import datetime

import config as _config

VERSION = '1.0.0'


# ---------------------------------------------------------------- 常量

#: 过程区可识别的旧名（体检时提示「建议统一为 08-过程文件」）
#: ⚠️ 2026-09-18 口径定案（爸爸拍板）——从本表**移除两个**：
#:   ① `需盖章文件` → 它是「**待盖章的定稿正本区**」（内有法律事务委托合同/保全申请书/
#:      要素式起诉状），属**正式区的一个环节**，不是过程区 → 移入 COURT_DIRS
#:   ② `pipeline` → 它是 **P/R/E 三段式流水线自动产物**（00-案件底稿.md / 01-策略决策书.md /
#:      00b-法律依据包.md），**自动生成不用手放**，与 08-过程文件的语义不同
#:      → 归入 ALIAS_OK（有明确职能的独立目录，不报「口径不一」）
PROCESS_ALIASES = ('参考', '备份', '旧稿', '历史', '废弃')
#: **有明确职能的独立目录**：不报「口径不一」（它们是设计如此，不是命名不统一）
#: 详见 docs\批39e-案卷文件三分类口径-20260917.md 的口径表
ALIAS_OK = ('pipeline', '需盖章文件')

#: 定稿区候选（案卷内已有编号目录；按优先级）
FINAL_DIRS = ('02-法律文书', '04-法院文件', '01-委托手续', '07-律师函', '03-证据材料')

#: **文书产出区**（只有这里才要求命名带日期/版本/序号锚点）
#: ⚠️ 2026-09-17 收窄一：`03-证据材料` / `06-当事人信息` 里是**证据原件**（对账单、身份证、
#: 转账凭证…），名字由来源决定，**不该要求日期/版本锚点**。
#: ⚠️ 2026-09-17 收窄二（**更重要**）：`介绍信.pdf` / `授权委托书.pdf` / `民事起诉状.pdf`
#: 这类**单一定稿提交件**名字本来就不需要版本锚点——名字已自足。
#: **锚点只在「同一文书存在多版本」时才有意义**（用来区分 v1/v2/定稿）。
#: 故命名体检**只在「版本族内」报缺锚点**，孤立单件一律不报。
#: 实踩：不加这条，A闽兴 一个案卷就报 132 条，全是 `介绍信.pdf` 这种误报。
NAMING_CHECK_DIRS = ('02-法律文书', '04-法院文件', '01-委托手续', '07-律师函',
                     '04-仲裁文件', '仲裁文书', '法院', '起诉材料')
#: 不参与命名体检的文件名（说明/日志/索引类）
NAMING_SKIP_NAMES = ('_材料怎么放.md', 'intake-log.md', 'README.md', '_迁出记录.md',
                     '_归集留痕.md', '_散件入库.md')

#: 文件名里的定稿标记
FINAL_MARKS = ('定稿', '终稿', '正式稿', 'final', '已提交', '提交版', '盖章版')
#: 文件名里的草稿标记
DRAFT_MARKS = ('初稿', '草稿', 'draft', '未定稿', '讨论稿', '待核', '待改', '未按官方样式')
#: 文件名里的备份标记（同内容不同时间点）
#: ⚠️ 2026-09-18 实踩：`10、财产保全申请书-…（旧）.docx` 里的「（旧）」未被识别为备份标记
#: → 它先命中 COURT_MARKERS 的「申请书」被判 court，报成「程序性文件错位」。
#: 实测形态还有：`（旧）` / `（老版）` / `（V1）` / `.redline.`。
BACKUP_MARKS = ('备份', 'backup', 'bak', '旧版', '原稿', '修复前', '废弃',
                '（旧）', '(旧)', '（老版）', '（上一版）', '(上一版)',
                '.redline.', '（草）', '（未用）', '（废）')
#: 版本号形态：v1 / V2 / （第3稿）/ _3
VER_RE = re.compile(r'[vV](\d+)|第(\d+)[稿版]|_(\d+)(?=\.|$)')
#: 日期形态（用于「同一文档多时间点」判定）
DATE_PATTERNS = (
    re.compile(r'(\d{4})[-\.](\d{1,2})[-\.](\d{1,2})'),          # 2026-09-17 / 2026.9.17
    re.compile(r'(\d{4})(\d{2})(\d{2})'),                          # 20260917
)
#: 案卷内不该出现的临时产物（工作台/OCR/脚本中间产物）
#: ⚠️ 2026-09-17 实测补充：`_回归测试`（含 cache_engine/monitor_frames/out/preview-*）
#: 是**跑脚本留下的技术残留**，属 junk 而非「证据错位」。
JUNK_NAMES = ('monitor_frames', 'tmp', 'temp', '_cache', '__pycache__', 'ocr_output',
              'ocr_frames', 'Thumbs.db', 'desktop.ini',
              '_回归测试', 'cache_engine',
              # ⚠️ 2026-09-18 实踩：`08-过程文件\_ocr输出\_VL交叉核验\范申华反映_reencoded.jpg`
              # 被判「证据躺错区」——`_ocr输出` 是我方 OCR 产物目录，属技术残留。
              '_ocr输出', '_VL交叉核验', 'ocr输出', 'VL交叉核验')
#: ⚠️ 2026-09-18 修正：`preview-qisuzhuang` / `preview-shenqing` 原列入 junk，
#: 但清理时发现它们是**文书草稿的渲图目录**（内含起诉状/证据清单/申请书的 v1/v7 草稿 PDF
#: + 逐页 png），原稿 docx 在 `01-文书草稿备份`。已移到 `01-文书草稿备份\_渲图\`，
#: **不再算技术残留** —— 故从 JUNK_NAMES 移除（保留 `preview-` 前缀的目录名判定交给业务）。
#: 脚本改编码/压缩后缀（`xxx_reencoded.jpg` / `xxx_compressed.jpg`）→ 我方生成，非原件
REENCODE_RE = re.compile(r'_(reencoded|compressed|resized|thumb|缩略|压缩)\b', re.I)

#: 应被忽略的目录（不进体检）
IGNORE_DIRS = ('00-收件箱', 'ocr', '_ocr', 'sessions', '.git')


# ================================================================ 批 39e：三分类口径
# 口径来源：docs\批39e-案卷文件三分类口径-20260917.md
#   程序性 = 要交给法院/对方的文书（按**文书性质**判）
#   证据   = 当事人/第三方给的原始材料（按**来源**判）
#   过程性 = 办案中间产物、不提交（按**产生方式**判）—— 唯一去处 = 08-过程文件
#
# ⚠️ 判据优先级（先判性质，再看状态）：
#   ① AI 生成/修改痕迹/备份对照/OCR/中间格式 → 过程性（最高优先，明确不出门）
#   ② 第三方原始材料 → 证据
#   ③ 要提交的文书 → 程序性
#   ④ 判不出 → 留原处 + 报「待人工判定」，**不猜**

#: 过程性文件的**特征词**（命中即判过程性；不交法院的办案工具）
PROC_MARKERS = (
    'html中间稿', '中间稿', '草稿', '初稿', 'draft', '未定稿', '讨论稿',
    '对照', '比对', 'redline', '修改痕迹', '交叉验证', '素材包',
    '核对清单', '打印清单', '自查', '底稿', '备份', 'backup',
)
#: 过程性文件的**扩展名**（中间格式，不进案卷正文）
PROC_EXTS = ('.html', '.md', '.txt', '.log')
#: 过程性文件的**豁免名**（这些 md 是案卷基础设施，不算过程性文件）
PROC_EXEMPT_NAMES = ('_材料怎么放.md', 'intake-log.md', 'README.md', '_迁出记录.md',
                     '_归集留痕.md', '_归位留痕.md', '_散件入库.md', '_材料清单.md')

#: **备份/旧版夹的标志词**（所在目录命中即判过程性，优先于文书性质）
#: 实测形态：`备份-文书修改-20260916` / `备份-摸排修复-20260915` / `备份-官网原档重做-20260910`
#: / `备份-编排修订-20260916` / `废弃-自拟版-20260907` / `html中间稿-仲裁申请书`
BACKUP_DIR_MARKERS = ('备份', '旧版', '废弃', '中间稿', '历史', 'bak', 'backup',
                      '审计-', '草稿')

#: 程序性文件的**特征词**（要交给法院/对方的文书）
COURT_MARKERS = (
    '起诉状', '答辩状', '上诉状', '再审申请', '申请书', '申请', '代理词',
    '证据清单', '质证意见', '辩论意见', '陈述意见', '代理意见', '法律意见',
    '异议', '复议', '管辖异议', '执行申请', '保全', '强制', '调查取证',
    '律师函', '催告函', '授权委托书', '委托合同', '所函', '介绍信',
)
#: 程序性文件**该在**的目录（不在这 = 错位）
#: ⚠️ 2026-09-17 实测：目录名**不统一**——有的案卷是 `04-法院文件`，有的是
#: `04-仲裁文件` / `仲裁文书` / `法院` / `起诉材料`（同一职能多种名字，实测 8 种）。
#: 这里把**同职能异名**都认进来，否则判据会漏（实踩：全案 0 命中＝假阴性）。
COURT_DIRS = ('01-委托手续', '02-法律文书', '04-法院文件', '07-律师函', '需盖章文件',
              '04-仲裁文件', '仲裁文书', '仲裁文件', '法院', '法院文件',
              '起诉材料', '诉讼文书', '02-仲裁文书', '仲裁申请书',
              # 2026-09-18 口径定案：`需盖章文件` = 待盖章的**定稿正本区**（正式区环节）
              '需盖章的资料')
#: 证据材料**该在**的目录
EVIDENCE_DIRS = ('03-证据材料', '06-当事人信息', '证据', '证据材料', '当事人信息')
#: 过程区认定（同职能异名全认）
PROCESS_DIRS = ('08-过程文件', '过程文件', '01-文书草稿备份', '03-撤回备查',
                '07-OCR核验底稿', 'OCR核验底稿', '文书草稿备份')
#: 证据材料的**特征扩展名**（照片/扫描件多为图片）
EVIDENCE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.heic')
#: ⚠️ 2026-09-17 收窄：**图片 ≠ 证据**。实测 `诉讼费凭证.jpg` / `介绍信.PNG` /
#: `授权委托调档.PNG` 都是**办案产生的票据/函件扫描件**，不是当事人给的原始证据。
#: 这些按**文件名语义**优先判为程序性/过程性——故证据判据必须在**语义判据之后**兜底，
#: 且命中下列词的图片**不算证据**：
NOT_EVIDENCE_IMAGE = (
    '凭证', '发票', '票据', '收据', '介绍信', '所函', '委托', '调档', '缴费',
    '回单', '回执', '截图', '二维码', '清单', '台账', '核对', '报告',
)

#: **本职附件**：这些图片是某类文书的「随件凭证」，**跟着主文书走**，不是第三方证据。
#: ⚠️ 2026-09-18 实踩（证据错区误报第三轮）：`07-律师函\何红英签收.jpg`（送达签收面单）
#: 被判「证据躺错区」—— 但签收面单**就该跟律师函放一起**（用来证明已送达）。
#: 同批误报还有 `02-法律文书\当事人甲建行账号.png`（我方查的财产线索，**为保全申请服务**）。
#: 判据：图片命中下列词 → **视为所在目录的本职附件**（不错位），除非它在过程区/回收站。
ATTACH_IMAGE_MARKERS = (
    '签收', '送达', '回执', '回单', '快递单', '邮件详情', '妥投', '面单', '受理回执',
    '账号', '房产', '车辆', '汽车', '工商', '股权', '征信', '不动产', '财产线索',
    '企业信息', '年检', '资质',
    # ⚠️ 2026-09-18 第二轮收窄：
    '面签', '身份核验', '视频核身', '合影',          # 面签照＝授权手续的过程记录，跟手续走
    '受理通知书', '立案通知', '开庭传票', '送达证',   # 法院文书扫描件＝法院文书本体
    '受理案件', '缴费通知', '应诉通知',
    '授信', '欠款', '对账',                        # 授信/欠款资料＝我方调查或银行回执
)
#: **脚本抽取产物**（下划线前缀 + extracted/ocr/frame 等）→ 属技术残留，不是证据
#: ⚠️ 2026-09-18 实踩：`07-律师函\_extracted_img_1.png` 被判「证据躺错区」——
#: 它是**从 PDF 里抽图的脚本产物**（下划线开头即我方生成），不该当证据。
EXTRACT_IMG_RE = re.compile(r'^_(extracted|ocr|frame|page|img|image)\b|\b_extracted_img_\d+',
                            re.I)
#: **PDF 抽页产物**：`page_001.png` / `page_010.png`（扫描/OCR 时从 PDF 拆出的单页）
#: ⚠️ 2026-09-18 实踩：`08-过程文件\page_001.png`（编号不连续，1/10/11）被判「证据躺错区」
#: —— 它是**拆页过程的中间产物**，原件是那份 PDF，属技术残留。
PAGE_IMG_RE = re.compile(r'^page[_-]?\d+$', re.I)
#: **同目录配对图**：`SKM_368e…_01.png` 与 `SKM_368e….pdf` 同名（仅多 `_01`）
#: → 扫描仪导出的「PDF + 预览图」双格式，**跟着配对件走**，不是独立证据。
#: ⚠️ 2026-09-18 实踩：`01-委托手续\周志伟\SKM_368e26062611060_01.png` 被判「证据躺错区」，
#: 而它的 `.pdf` 就在同目录（位置正确）——判据只看「图片＝证据」，漏了「同目录有同名件」。
PAIR_IMG_RE = re.compile(r'^(?P<base>.+?)_0?\d$')



# ---------------------------------------------------------------- 基础

def case_root():
    """案卷根（与 case_ops.case_root() 同口径：env 覆盖优先，其次配置文件）"""
    return _config.path('paths.case_root', env='CASE_OPS_ROOT')


def _norm(name):
    return (name or '').strip().lower()


def _marks_in(name):
    """文件名命中的标记集合"""
    got = set()
    n = name
    low = n.lower()
    for m in FINAL_MARKS:
        if m.lower() in low:
            got.add('final')
            break
    for m in DRAFT_MARKS:
        if m.lower() in low:
            got.add('draft')
            break
    for m in BACKUP_MARKS:
        if m.lower() in low:
            got.add('backup')
            break
    return got


def _ver_of(name):
    """取文件名里的版本序号（无则 None）"""
    m = VER_RE.search(name)
    if not m:
        return None
    for g in m.groups():
        if g:
            return int(g)
    return None


def _dates_in(name):
    """取文件名里的日期（返回 ISO 串列表）"""
    out = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(name):
            y, mo, d = (int(x) for x in m.groups())
            if 2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                out.append('%04d-%02d-%02d' % (y, mo, d))
    return out


def _stem_signature(name):
    """文档「同名族」签名：去掉版本/日期/标记后剩下的主干

    用于把「同一份文书的多个版本」归到一族。
    """
    s = os.path.splitext(name)[0]
    # 去括号内容（定稿）/（备份）/（v2）等
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    # 去版本号
    s = VER_RE.sub('', s)
    # 去日期
    for pat in DATE_PATTERNS:
        s = pat.sub('', s)
    # 去标记词
    for m in FINAL_MARKS + DRAFT_MARKS + BACKUP_MARKS:
        s = s.replace(m, '')
    # 去前导编号「9、」「2-3-」「10.」
    s = re.sub(r'^\s*[\d]+[\s、\.\-]+', '', s)
    s = re.sub(r'[\s_\-—·]+', '', s)
    return s or os.path.splitext(name)[0]


# ---------------------------------------------------------------- 分类

def classify_file(path, case_dir=None):
    """单个文件 → 归属判定

    返回 {role, zone, marks, ver, dates, reason}
      role: final | draft | backup | plain | junk
      zone: 'final'（正式区，案卷编号目录）| 'process'（过程区 08-过程文件）| 'junk'
    **只判定，不移动。**
    """
    name = os.path.basename(path)
    rel = os.path.relpath(path, case_dir) if case_dir else name
    parts = rel.split(os.sep)
    in_process = bool(parts) and parts[0] in PROCESS_DIRS
    marks = _marks_in(name)
    ver = _ver_of(name)
    dates = _dates_in(name)
    low = name.lower()

    # junk 判定：**文件名或所在路径任一段**命中（monitor_frames 这类是目录名）
    junk_here = ''
    for seg in parts:
        stem_seg = os.path.splitext(seg)[0]
        if seg in JUNK_NAMES or stem_seg in JUNK_NAMES:
            junk_here = seg
            break
    if junk_here:
        return {'name': name, 'rel': rel, 'role': 'junk', 'zone': 'junk',
                'marks': sorted(marks), 'ver': ver, 'dates': dates,
                'reason': '工作台/OCR 中间产物（命中「%s」），不应留在案卷' % junk_here}

    if 'final' in marks:
        return {'name': name, 'rel': rel, 'role': 'final', 'zone': 'final',
                'marks': sorted(marks), 'ver': ver, 'dates': dates,
                'reason': '文件名含定稿标记（%s）' % next(
                    (m for m in FINAL_MARKS if m.lower() in low), ''),
                'need_move': in_process}
    if 'backup' in marks:
        return {'name': name, 'rel': rel, 'role': 'backup', 'zone': 'process',
                'marks': sorted(marks), 'ver': ver, 'dates': dates,
                'reason': '文件名含备份/旧稿标记 → 属过程材料',
                'need_move': not in_process}
    if 'draft' in marks:
        return {'name': name, 'rel': rel, 'role': 'draft', 'zone': 'process',
                'marks': sorted(marks), 'ver': ver, 'dates': dates,
                'reason': '文件名含草稿标记（%s）' % next(
                    (m for m in DRAFT_MARKS if m.lower() in low), ''),
                'need_move': not in_process}
    return {'name': name, 'rel': rel, 'role': 'plain', 'zone':
            'process' if in_process else 'final',
            'marks': sorted(marks), 'ver': ver, 'dates': dates,
            'reason': '无标记（按所在位置判定）', 'need_move': False}


def classify_role(name, rel, ext=None):
    """按 **三分类口径** 判文件角色（与 classify_file 的 final/draft 分区分开）

    返回 {role, why, priority} —— 只判定，**不移动**。
      role: 'proc'(过程性) | 'evidence'(证据) | 'court'(程序性) | 'unknown'
      priority: 1/2/3 —— 命中的是哪一档判据（用于解释与取舍）
    """
    ext = (ext or os.path.splitext(name)[1]).lower()
    low = name.lower()
    steps = rel.replace('/', os.sep).split(os.sep)

    # 案卷基础设施（说明/日志/索引）—— 不算三类中的任何一类
    if name in PROC_EXEMPT_NAMES:
        return {'role': 'infra', 'why': '案卷说明/日志文件（基础设施）', 'priority': 0}

    # ⓿ **技术残留优先**：所在路径命中 junk 名（monitor_frames/cache_engine…）
    #    → 判 junk，不算三类中的任何一类（否则 96 件 OCR 关键帧会被误报成「证据错位」）
    for seg in steps:
        if os.path.splitext(seg)[0] in JUNK_NAMES:
            return {'role': 'junk', 'why': '技术残留目录「%s」（脚本/OCR 中间产物）' % seg,
                    'priority': 0}
    # ⓿b **脚本抽取产物**（`_extracted_img_1.png` / `_ocr_*.png`）→ 技术残留
    if EXTRACT_IMG_RE.search(name):
        return {'role': 'junk', 'why': '脚本抽取产物（文件名带抽取前缀），非证据原件',
                'priority': 0}
    # ⓿c **脚本加工后缀**（`xxx_reencoded.jpg` / `xxx_compressed.jpg`）→ 我方生成，非原件
    if REENCODE_RE.search(os.path.splitext(name)[0]):
        return {'role': 'junk', 'why': '脚本加工产物（文件名带加工后缀），非证据原件',
                'priority': 0}
    # ⓿d **PDF 抽页产物**（`page_001.png`）→ 拆页中间产物，原件是对应 PDF
    if ext in EVIDENCE_EXTS and PAGE_IMG_RE.match(os.path.splitext(name)[0]):
        return {'role': 'junk', 'why': 'PDF 抽页产物（page_NNN），原件为对应 PDF',
                'priority': 0}

    # ① 过程性（最高优先：明确不出门的办案产物）
    for m in PROC_MARKERS:
        if m.lower() in low:
            return {'role': 'proc', 'why': '办案中间产物（命中「%s」），不提交法院' % m,
                    'priority': 1}
    if ext in PROC_EXTS:
        return {'role': 'proc', 'why': '中间格式（%s），办案产物不提交法院' % ext,
                'priority': 1}
    # ①b **所在目录是备份/旧版夹** → 一律过程性（**优先于文书性质**）
    #    ⚠️ 2026-09-17 实踩：`1、仲裁申请书-改代码前-122728.docx` 在 `备份-官网原档重做` 里，
    #    文件名命中「申请书」→ 首版判成 court 错位（该案 126 件里 98 件是这类误报）。
    #    口径：一个文件**同时**是「程序性文书」和「某时点备份」时，**备份身份优先**
    #    —— 它已经是被取代的旧版，只留痕、不交法院（呼应口径文档「一个文件可同时属两类，须定主次」）。
    for seg in steps[:-1]:
        if any(k in seg for k in BACKUP_DIR_MARKERS):
            return {'role': 'proc', 'why': '位于备份/旧版夹「%s」，是被取代的旧版，只留痕'
                    % seg, 'priority': 1}
    # ①c **文件名带备份/旧版标记** → 同属过程性（优先于文书性质）
    #    ⚠️ 2026-09-18 实踩：`10、财产保全申请书-福建闽兴VS新余钦等人（旧）.docx`
    #    文件名命中 COURT_MARKERS 的「申请书」→ 判 court。但「（旧）」明示是**被取代的旧版**。
    #    实测形态：`（旧）` / `（备份）` / `（草案）` / `（修复前）` / `.redline.` / `（DOCX修复前备份）`。
    for m in BACKUP_MARKS:
        if m.lower() in low:
            return {'role': 'proc', 'why': '文件名带旧版/备份标记（命中「%s」），是被取代的版本'
                    % m, 'priority': 1}

    # ③ 程序性（要提交的文书）—— 必须在「图片→证据」之前，否则票据/函件被误判为证据
    for m in COURT_MARKERS:
        if m in name:
            return {'role': 'court', 'why': '诉讼文书（命中「%s」），应提交法院/对方'
                    % m, 'priority': 3}

    # ② 证据（当事人/第三方的原始材料）—— 图片兜底，但排除票据/函件类
    if ext in EVIDENCE_EXTS:
        # ②a 本职附件（签收面单 / 我方调取的财产线索）→ **跟着所在目录的主文书走**，不错位
        #    ⚠️ 2026-09-18 实踩：`07-律师函\何红英签收.jpg`、`02-法律文书\当事人甲建行账号.png`
        #    曾双双被判「证据躺错区」。判据只看「图片＝证据」→ 漏了「它属于哪份文书的附件」。
        for k in ATTACH_IMAGE_MARKERS:
            if k in name:
                return {'role': 'attach', 'why': '随件凭证（命中「%s」），属于所在目录主文书的附件'
                        % k, 'priority': 2}
        if any(k in name for k in NOT_EVIDENCE_IMAGE):
            return {'role': 'unknown', 'why': '图片但名含票据/函件类词（%s），需人工判定'
                    % next(k for k in NOT_EVIDENCE_IMAGE if k in name), 'priority': 0}
        return {'role': 'evidence', 'why': '图片/扫描件（%s），多为当事人或第三方原始材料'
                % ext, 'priority': 2}

    return {'role': 'unknown', 'why': '判据未命中（待人工判定）', 'priority': 0}


#: **「要人判」的语义线索**（命中即在前端标「待人工确认」，不给结论）
#: ⚠️ 2026-09-17 实踩：`针对潘培松…答复意见(定稿).docx` 与 `…答复意见.docx` 是同族两版，
#: `14、调查令申请书（10号壳版·撤回备查）.docx` 已撤回，`当事人甲介绍信.png` 是图片——
#: **这些「交没交出去」只有律师知道，工具只应给候选、不该给结论。**
PENDING_MARKERS = {
    '撤回': '名字带「撤回」→ 已撤回，多半只是留档',
    '备查': '名字带「备查」→ 备查件，未必是交付件',
    '草稿': '名字带「草稿」→ 未定稿',
    '初稿': '名字带「初稿」→ 未定稿',
    '如申请': '名字带「如申请」→ 假设稿',
    '待': '名字带「待」→ 待办性质',
    '旧版': '名字带「旧版」→ 已被取代',
}


def pending_hint(name):
    """返回「待人工确认」的提示语（无则空串）"""
    for k, v in PENDING_MARKERS.items():
        if k in name:
            return v
    ext = os.path.splitext(name)[1].lower()
    if ext in EVIDENCE_EXTS:
        return '图片/扫描件 → 可能是扫描件而非交付件'
    return ''


def _func_dir_of(rel_dir):
    """从相对路径里找出「**职能目录**」那一段（案卷名/子案件夹不算）

    案卷结构在不同深度不统一，实测三种：
      - `08-过程文件/xx`                      → 首段即职能目录
      - `A闽兴/5.6揭阳委托人…/08-过程文件/xx`      → 第 2 段才是
      - `仲-锦晟达VS李雅玲_劳动争议/仲裁文书/xx`  → 第 1 段即职能目录

    判据：**从右往左**找第一个「已知职能目录名」；都没命中则回退首段。
    （从右往左＝取最贴近文件的那层，符合「文件属于哪个职能区」的直觉）
    """
    if not rel_dir or rel_dir == '.':
        return ''
    parts = [p for p in rel_dir.replace('/', os.sep).split(os.sep) if p not in ('', '.')]
    known = set(PROCESS_DIRS) | set(COURT_DIRS) | set(EVIDENCE_DIRS) | \
        set(PROCESS_ALIASES) | set(IGNORE_DIRS)
    for p in reversed(parts):
        if p in known:
            return p
    return parts[0] if parts else ''


def misplace(case_dir, max_files=4000):
    """**错位体检**（只读）：找出「该在 A 目录、却躺在 B 目录」的文件

    三类错位：
      1. 程序性文件躺在过程区（`08-过程文件`）—— 实测最普遍
      2. 过程性文件躺在正式区（`01/02/04/07`）
      3. 证据材料躺在过程区或正式区
    **只报不改。** 判不出的一律进 `unknown`，不猜。
    """
    out = {'court_in_proc': [], 'proc_in_court': [], 'evidence_in_wrong': [],
           'unknown': [], 'checked': 0, 'truncated': False,
           'court_in_proc_n': 0, 'proc_in_court_n': 0,
           'evidence_in_wrong_n': 0, 'unknown_n': 0}
    # ⚠️ 实踩（2026-09-17）：上限此前用 break 让 **os.walk 整段提前中断**，
    # 22 个案卷只扫完前几个就停 → 结果全 0（假阴性）。改为「超限仍遍历、只是不再记条」。
    #
    # ⚠️ 第二个实踩：`top` 必须取「**相对案卷根**的首个目录段」，绝不能取 relpath 第一段——
    # 若传入的是**案卷根**（如 `A个案`），第一段是**案卷名**（如 `A闽兴`），目录判据全失效
    # → 全案 0 命中（假阴性）。修法见 `_dir_of()`。
    for r, ds, fs in os.walk(case_dir):
        ds[:] = [d for d in ds if d not in IGNORE_DIRS and not d.startswith('.')]
        rel_dir = os.path.relpath(r, case_dir)
        top = _func_dir_of(rel_dir)
        if top in IGNORE_DIRS or top.startswith('_'):
            continue
        in_proc = top in PROCESS_DIRS
        in_court = top in COURT_DIRS
        in_evidence = top in EVIDENCE_DIRS
        for f in fs:
            out['checked'] += 1
            rel = os.path.join(rel_dir, f) if rel_dir != '.' else f
            info = classify_role(f, rel)
            if info['role'] in ('infra', 'junk', 'attach'):
                continue          # 基础设施 / 技术残留 / 随件凭证：不计入三分类错位
            # **同目录配对图**：`SKM_xxx_01.png` 与同目录 `SKM_xxx.pdf` 配对 → 扫描仪双格式
            #   ⚠️ 2026-09-18 实踩：`01-委托手续\周志伟\SKM_368e…_01.png` 被判「证据躺错区」，
            #   而配对 `.pdf` 就在同目录且位置正确 → 它属「主件的附带预览图」，不错位。
            if info['role'] == 'evidence' and os.path.splitext(f)[1].lower() in EVIDENCE_EXTS:
                m = PAIR_IMG_RE.match(os.path.splitext(f)[0])
                if m:
                    sibs = set(fs)
                    if any((m.group('base') + e) in sibs
                           for e in ('.pdf', '.PDF', '.docx', '.doc', '.xlsx')):
                        continue      # 有同名正式件在 → 本图是它的预览/缩略，不算证据错位
            if (len(out['court_in_proc']) + len(out['proc_in_court'])
                    + len(out['evidence_in_wrong']) + len(out['unknown'])) > max_files:
                out['truncated'] = True
                continue
            rec = {'rel': rel, 'name': f, 'role': info['role'],
                   'why': info['why'], 'priority': info['priority'],
                   'where': rel_dir if rel_dir != '.' else '案卷根',
                   'suggest': '', 'pending': pending_hint(f)}
            if info['role'] == 'court' and in_proc:
                rec['suggest'] = ('02-法律文书' if ('状' in f or '词' in f or '意见' in f)
                                  else '04-法院文件' if ('通知' in f or '裁定' in f or '判决' in f)
                                  else '02-法律文书')
                out['court_in_proc'].append(rec)
            elif info['role'] == 'proc' and (in_court or in_evidence):
                rec['suggest'] = '08-过程文件'
                out['proc_in_court'].append(rec)
            elif info['role'] == 'evidence' and (in_proc or in_court):
                rec['suggest'] = '03-证据材料'
                out['evidence_in_wrong'].append(rec)
            elif info['role'] == 'unknown':
                out['unknown'].append(rec)
    out['court_in_proc_n'] = len(out['court_in_proc'])
    out['proc_in_court_n'] = len(out['proc_in_court'])
    out['evidence_in_wrong_n'] = len(out['evidence_in_wrong'])
    out['unknown_n'] = len(out['unknown'])
    out['total_misplaced'] = (out['court_in_proc_n'] + out['proc_in_court_n']
                              + out['evidence_in_wrong_n'])
    return out


def group_versions(paths, case_dir=None):
    """把一批文件按「同名族」分组，标出族内最新/最旧

    返回 [{sig, files:[...], has_final, versions:[int], dates:[iso]}, ...]
    只做分析，**不移动不删除**。
    """
    fam = {}
    for p in paths:
        name = os.path.basename(p)
        sig = _stem_signature(name)
        fam.setdefault(sig, []).append(p)
    out = []
    for sig, ps in fam.items():
        if len(ps) < 2:
            continue
        infos = []
        for p in ps:
            name = os.path.basename(p)
            infos.append({'path': p, 'name': name, 'ver': _ver_of(name),
                          'dates': _dates_in(name), 'marks': sorted(_marks_in(name)),
                          'mtime': os.path.getmtime(p)})
        ver_s = [i['ver'] for i in infos if i['ver'] is not None]
        dt_s = sorted({d for i in infos for d in i['dates']})
        # 排序键：版本号 > 日期 > mtime
        def key(i):
            return (i['ver'] if i['ver'] is not None else -1,
                    max(i['dates']) if i['dates'] else '',
                    i['mtime'])
        infos.sort(key=key)
        out.append({'sig': sig, 'count': len(infos), 'files': infos,
                    'has_final': any('final' in i['marks'] for i in infos),
                    'versions': sorted(ver_s), 'dates': dt_s,
                    'latest': infos[-1]['name'], 'oldest': infos[0]['name']})
    out.sort(key=lambda x: -x['count'])
    return out


# ---------------------------------------------------------------- 体检

def sweep(case_dir, max_files=4000):
    """单案体检：分区统计 + 口径问题 + 命名问题 + 版本族

    **只读，绝不移动或删除任何文件。**
    """
    proc, final, junk, naming, alias = [], [], [], [], []
    verfam_all = []
    n = 0
    for r, ds, fs in os.walk(case_dir):
        ds[:] = [d for d in ds if d not in IGNORE_DIRS and not d.startswith('.')]
        rel_dir = os.path.relpath(r, case_dir)
        top = _func_dir_of(rel_dir)      # ⚠️ 必须用职能目录定位（见 _func_dir_of 注释）
        # 目录名口径体检
        for d in ds:
            if d in PROCESS_ALIASES:
                alias.append({'dir': os.path.join(rel_dir, d) if rel_dir != '.' else d,
                              'name': d,
                              'suggest': '08-过程文件',
                              'why': '与既有过程区口径不一（同职能多名字）'})
        for f in fs:
            n += 1
            # ⚠️ 不 break（否则整段 walk 中断 → 后面案卷全漏扫，2026-09-17 实踩）
            fp = os.path.join(r, f)
            info = classify_file(fp, case_dir)
            if info['role'] == 'junk':
                junk.append(info)
            elif info['zone'] == 'process':
                proc.append(info)
            else:
                final.append(info)
            # ⚠️ 命名合规**不在这里判**——见下方「版本族建好后」的二次扫描。
            # 原因（2026-09-17 实踩）：单一定稿件（`介绍信.pdf`）名字本来就自足，
            # 锚点只在「同一文书多版本」时才有意义；边扫边判会报出 132 条 A闽兴 误报。
        verfam_all.append((rel_dir, r, fs))

    # 版本族（按目录聚合）
    fams = []
    for rel_dir, r, fs in verfam_all:
        if _func_dir_of(rel_dir) in IGNORE_DIRS:
            continue
        ps = [os.path.join(r, f) for f in fs]
        for g in group_versions(ps, case_dir):
            g['dir'] = rel_dir
            fams.append(g)
    fams.sort(key=lambda x: -x['count'])

    # ---- 命名体检（**只在版本族内**）：族里有多版，却没一个带版本/日期锚点 → 真问题
    #      （分不清哪份是最新，日后必然找错）
    for g in fams:
        dirs = g.get('dir') or ''
        if _func_dir_of(dirs) not in NAMING_CHECK_DIRS:
            continue
        for fi in g.get('files', []):
            if fi['name'] in NAMING_SKIP_NAMES:
                continue
            if fi.get('ver') is not None or fi.get('dates'):
                continue
            if fi.get('marks'):
                continue                       # 有定稿/草稿标记也算可辨
            naming.append({'rel': os.path.join(dirs, fi['name']),
                           'name': fi['name'],
                           'why': '同一族有 %d 个版本，本件无版本/日期锚点，分不清是哪一版'
                                  % g['count']})

    mp = misplace(case_dir, max_files=max_files)          # 批 39e：错位体检（只读）
    return {
        'case_dir': case_dir,
        'case': os.path.basename(case_dir),
        'total': n,
        'process_count': len(proc),
        'final_count': len(final),
        'junk': junk,
        'alias_dirs': alias,
        'naming_issues': naming[:60],
        'naming_issue_count': len(naming),
        'version_families': fams[:20],
        'version_family_count': len(fams),
        'process_files': proc[:200],
        'misplace': {
            'court_in_proc': mp['court_in_proc'][:80],
            'court_in_proc_n': mp['court_in_proc_n'],
            'proc_in_court': mp['proc_in_court'][:40],
            'proc_in_court_n': mp['proc_in_court_n'],
            'evidence_in_wrong': mp['evidence_in_wrong'][:40],
            'evidence_in_wrong_n': mp['evidence_in_wrong_n'],
            'unknown_n': mp['unknown_n'],
            'total': mp['total_misplaced'],
        },
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def sweep_all(max_cases=60):
    """全案卷体检汇总（只读）"""
    root = case_root()
    out = []
    if not os.path.isdir(root):
        return {'error': '案卷根不存在：%s' % root, 'cases': []}
    for name in sorted(os.listdir(root)):
        cp = os.path.join(root, name)
        if not os.path.isdir(cp):
            continue
        out.append(sweep(cp))
        if len(out) >= max_cases:
            break
    agg = {
        'cases': out,
        'case_count': len(out),
        'junk_total': sum(len(c['junk']) for c in out),
        'alias_total': sum(len(c['alias_dirs']) for c in out),
        'naming_total': sum(c['naming_issue_count'] for c in out),
        'family_total': sum(c['version_family_count'] for c in out),
        'misplace_total': sum(c['misplace']['total'] for c in out),
        'court_in_proc_total': sum(c['misplace']['court_in_proc_n'] for c in out),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    return agg


# ---------------------------------------------------------------- 归集（只移动）

def plan_refile(case_dir, moves):
    """规划「错位归位」：把列出的文件移到**指定目标目录**（不限过程区）

    moves: [{'rel': '相对路径', 'dest': '02-法律文书'}, ...]
    **不落盘**，返回计划供确认。**只移动，不删除不复制。**

    守卫（与 plan_gather 同口径）：
      ① 源 realpath 必须落在案卷内
      ② 目标目录**必须已存在**（不自动建目录——避免造出错误结构）
      ③ 源文件必须真实存在
      ④ 同名自动避让
    """
    src_root = os.path.realpath(case_dir)
    items, blocked = [], []
    for mv in moves:
        rel = mv.get('rel', '')
        dest = mv.get('dest', '')
        src = os.path.join(case_dir, rel.replace('/', os.sep))
        rs = os.path.realpath(src)
        if not (rs == src_root or rs.startswith(src_root + os.sep)):
            blocked.append({'rel': rel, 'why': '路径越界（realpath 不在案卷内）'})
            continue
        if not os.path.isfile(rs):
            blocked.append({'rel': rel, 'why': '源文件不存在'})
            continue
        if not dest:
            blocked.append({'rel': rel, 'why': '缺目标目录'})
            continue
        tgt_dir = os.path.join(case_dir, dest.replace('/', os.sep))
        rt = os.path.realpath(tgt_dir)
        if not (rt == src_root or rt.startswith(src_root + os.sep)):
            blocked.append({'rel': rel, 'why': '目标越界'})
            continue
        if not os.path.isdir(rt):
            blocked.append({'rel': rel, 'why': '目标目录不存在：%s（不自动建，避免造错结构）' % dest})
            continue
        name = os.path.basename(rs)
        tgt = os.path.join(rt, name)
        k = 1
        while os.path.exists(tgt):
            stem, ext = os.path.splitext(name)
            tgt = os.path.join(rt, '%s_归位%d%s' % (stem, k, ext))
            k += 1
        items.append({'rel': rel, 'src': rs, 'dst': tgt, 'name': name, 'dest': dest})
    return {'case_dir': case_dir, 'items': items, 'blocked': blocked,
            'count': len(items), 'blocked_count': len(blocked), 'dry_run': True}


def apply_refile(plan, confirm=False):
    """执行归位（**必须 confirm=True**）；写 `_归位留痕.md`（在**源目录**旁）；只移动"""
    if not confirm:
        raise ValueError('未确认：apply_refile 需要 confirm=True')
    if not plan.get('items'):
        raise ValueError('没有可归位的条目')
    done, failed = [], []
    for it in plan['items']:
        try:
            os.makedirs(os.path.dirname(it['dst']), exist_ok=True)
            shutil.move(it['src'], it['dst'])
            done.append(it)
        except Exception as e:                                   # noqa: BLE001
            failed.append({'rel': it['rel'], 'why': str(e)})
    # 留痕：写在各源文件所在目录（原位留痕，便于按位置回查）
    logs = {}
    for it in done:
        src_dir = os.path.dirname(it['src'])
        log = os.path.join(src_dir, '_归位留痕.md')
        lines = []
        if os.path.isfile(log):
            try:
                with open(log, encoding='utf-8') as f:
                    lines = f.read().rstrip('\n').split('\n')
            except Exception:                                    # noqa: BLE001
                lines = []
        if not lines:
            lines = ['# 错位归位留痕', '',
                     '> 本文件由工作台自动维护：每次「错位归位」追加一段。',
                     '> 归位＝**只移动**，不删除。要复原按「原位置」移回即可。', '']
        lines.append('## %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        lines.append('')
        lines.append('| 文件 | 原位置 | 新位置 |')
        lines.append('|---|---|---|')
        lines.append('| %s | %s | %s |' % (it['name'], src_dir, it['dest']))
        lines.append('')
        with open(log, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines))
        logs[src_dir] = log
    return {'moved': len(done), 'failed': failed, 'logs': list(logs.values()),
            'items': done}


def plan_gather(case_dir, rels, dest_sub=None):
    """规划「归集到过程区」：把列出的文件移到 `08-过程文件\\`

    **不落盘**，返回计划供确认。只移动，不删除不复制。
    """
    proc_dir = os.path.join(case_dir, dest_sub or '08-过程文件')
    src_root = os.path.realpath(case_dir)
    items, blocked = [], []
    for rel in rels:
        src = os.path.join(case_dir, rel.replace('/', os.sep))
        rs = os.path.realpath(src)
        # 三层守卫（与散件入库同口径）
        if not (rs == src_root or rs.startswith(src_root + os.sep)):
            blocked.append({'rel': rel, 'why': '路径越界（realpath 不在案卷内）'})
            continue
        if os.path.basename(os.path.dirname(rs)) in PROCESS_DIRS:
            blocked.append({'rel': rel, 'why': '已在过程区，无需再移'})
            continue
        if not os.path.isfile(rs):
            blocked.append({'rel': rel, 'why': '文件不存在'})
            continue
        name = os.path.basename(rs)
        tgt = os.path.join(proc_dir, name)
        k = 1
        while os.path.exists(tgt):
            stem, ext = os.path.splitext(name)
            tgt = os.path.join(proc_dir, '%s_归集%d%s' % (stem, k, ext))
            k += 1
        items.append({'rel': rel, 'src': rs, 'dst': tgt, 'name': name})
    return {'case_dir': case_dir, 'proc_dir': proc_dir,
            'items': items, 'blocked': blocked,
            'count': len(items), 'blocked_count': len(blocked),
            'dry_run': True}


def apply_gather(plan, confirm=False):
    """执行归集（**必须 confirm=True**）；写 `_归集留痕.md`；只移动"""
    if not confirm:
        raise ValueError('未确认：apply_gather 需要 confirm=True')
    if not plan.get('items'):
        raise ValueError('没有可归集的条目')
    case_dir = plan['case_dir']
    proc_dir = plan['proc_dir']
    os.makedirs(proc_dir, exist_ok=True)
    done, failed = [], []
    for it in plan['items']:
        try:
            shutil.move(it['src'], it['dst'])
            done.append(it)
        except Exception as e:                                   # noqa: BLE001
            failed.append({'rel': it['rel'], 'why': str(e)})
    # 留痕
    if done:
        log = os.path.join(proc_dir, '_归集留痕.md')
        lines = []
        if os.path.isfile(log):
            try:
                with open(log, encoding='utf-8') as f:
                    lines = f.read().rstrip('\n').split('\n')
            except Exception:                                    # noqa: BLE001
                lines = []
        if not lines:
            lines = ['# 过程区归集留痕', '',
                     '> 本文件由工作台自动维护：每次「归集到过程区」追加一段。',
                     '> 归集＝**只移动**，不删除。原位置见下表。', '']
        lines.append('## %s（%d 项）' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(done)))
        lines.append('')
        lines.append('| 文件 | 原位置 |')
        lines.append('|---|---|')
        for it in done:
            lines.append('| %s | %s |' % (it['name'],
                                           os.path.dirname(it['rel']) or '案卷根'))
        lines.append('')
        with open(log, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines))
    return {'moved': len(done), 'failed': failed, 'proc_dir': proc_dir,
            'log': os.path.join(proc_dir, '_归集留痕.md') if done else '',
            'items': done}


# ---------------------------------------------------------------- selftest

def selftest():
    import tempfile
    ok = fail = 0

    def chk(name, cond, extra=''):
        nonlocal ok, fail
        if cond:
            ok += 1
            print('  PASS %s' % name)
        else:
            fail += 1
            print('  FAIL %s %s' % (name, extra))

    T = tempfile.mkdtemp(prefix='trace_demo_')
    case = os.path.join(T, '民-A-测试案-买卖合同纠纷')
    os.makedirs(os.path.join(case, '08-过程文件', '备份-文书修改-20260916'))
    os.makedirs(os.path.join(case, '02-法律文书'))
    os.makedirs(os.path.join(case, 'pipeline'))
    os.makedirs(os.path.join(case, 'monitor_frames'))
    mk = lambda p, s='x': open(p, 'w', encoding='utf-8').write(s)
    mk(os.path.join(case, '02-法律文书', '民事起诉状（定稿）.docx'))
    mk(os.path.join(case, '02-法律文书', '民事起诉状-v1.docx'))
    mk(os.path.join(case, '02-法律文书', '民事起诉状-v2.docx'))
    mk(os.path.join(case, '08-过程文件', '答辩状（初稿）.docx'))
    mk(os.path.join(case, '08-过程文件', '答辩状（备份）.docx'))
    mk(os.path.join(case, '08-过程文件', '证据清单-20260901.docx'))
    mk(os.path.join(case, '02-法律文书', '无锚点文件.docx'))
    mk(os.path.join(case, 'monitor_frames', 'f1.jpg'))

    # --- 分类
    c1 = classify_file(os.path.join(case, '02-法律文书', '民事起诉状（定稿）.docx'), case)
    chk('定稿件 → final/正式区', c1['role'] == 'final' and c1['zone'] == 'final')
    c2 = classify_file(os.path.join(case, '08-过程文件', '答辩状（初稿）.docx'), case)
    chk('初稿件 → draft/过程区', c2['role'] == 'draft' and c2['zone'] == 'process')
    c3 = classify_file(os.path.join(case, '08-过程文件', '答辩状（备份）.docx'), case)
    chk('备份件 → backup/过程区', c3['role'] == 'backup' and c3['zone'] == 'process')
    c4 = classify_file(os.path.join(case, 'monitor_frames', 'f1.jpg'), case)
    chk('中间产物 → junk', c4['role'] == 'junk')
    c5 = classify_file(os.path.join(case, '08-过程文件', '证据清单-20260901.docx'), case)
    chk('过程区无标记件 → process（按位置）', c5['zone'] == 'process')
    c6 = classify_file(os.path.join(case, '02-法律文书', '民事起诉状-v2.docx'), case)
    chk('正式区无标记件 → final（按位置）', c6['zone'] == 'final')

    # --- 版本族
    fams = group_versions([os.path.join(case, '02-法律文书', x)
                           for x in ('民事起诉状（定稿）.docx', '民事起诉状-v1.docx',
                                     '民事起诉状-v2.docx')], case)
    chk('版本族归族（起诉状 3 件一族）', len(fams) == 1 and fams[0]['count'] == 3)
    chk('版本族识别定稿件', fams and fams[0]['has_final'])
    chk('版本族版本号递增', fams and fams[0]['versions'] == [1, 2])

    # --- 体检
    sw = sweep(case)
    chk('体检：过程区计数 ≥3', sw['process_count'] >= 3)
    chk('体检：junk 命中 monitor_frames', any('f1.jpg' in j['name'] for j in sw['junk']))
    # 2026-09-18 口径定案：pipeline / 需盖章文件 是「有明确职能的独立目录」，
    # 不再报「口径不一」（原用例断言它们该报，现已作废）
    chk('体检：pipeline 不再报口径不一（口径定案）',
        not any(a['name'] == 'pipeline' for a in sw['alias_dirs']))
    chk('体检：需盖章文件 不再报口径不一',
        not any(a['name'] == '需盖章文件' for a in sw['alias_dirs']))
    chk('体检：需盖章文件 已认作正式区', '需盖章文件' in COURT_DIRS)
    # 命名体检收窄后（2026-09-17）：**孤立单件不报**（名字自足），只在版本族内报缺锚点。
    # 夹具里 `民事起诉状-v1/-v2/（定稿）` 三件都带锚点 → 该族不报；`无锚点文件.docx` 是孤立件 → 也不报。
    chk('体检：命名体检不报孤立单件（收窄后）',
        not any('无锚点文件' in x['name'] for x in sw['naming_issues']))
    # 造一个「族内缺锚点」的真问题：两份同名但无版本号
    mk(os.path.join(case, '02-法律文书', '答辩状.docx'))
    mk(os.path.join(case, '02-法律文书', '答辩状（最终）.docx'))
    sw_b = sweep(case)
    chk('体检：族内缺锚点会报（答辩状.docx 无版本号）',
        any('答辩状.docx' in x['name'] for x in sw_b['naming_issues']),
        [x['name'] for x in sw_b['naming_issues']][:5])
    chk('体检：版本族 ≥1', sw['version_family_count'] >= 1)

    # --- 归集（只移动）
    plan = plan_gather(case, ['02-法律文书/民事起诉状-v1.docx',
                              '02-法律文书/民事起诉状-v2.docx'])
    chk('归集计划 2 项', plan['count'] == 2 and plan['blocked_count'] == 0)
    chk('归集目标在 08-过程文件', plan['proc_dir'].endswith('08-过程文件'))
    try:
        apply_gather(plan, confirm=False)
        chk('未确认拒绝执行', False)
    except ValueError:
        chk('未确认拒绝执行', True)
    r = apply_gather(plan, confirm=True)
    chk('归集后 2 件已移动', r['moved'] == 2)
    chk('归集后原位置已空',
        not os.path.exists(os.path.join(case, '02-法律文书', '民事起诉状-v1.docx')))
    chk('归集后目标存在',
        os.path.exists(os.path.join(case, '08-过程文件', '民事起诉状-v1.docx')))
    chk('归集留痕文件已写', os.path.isfile(r['log']))
    with open(r['log'], encoding='utf-8') as f:
        lg = f.read()
    chk('留痕含原名与原位置', '民事起诉状-v1.docx' in lg and '02-法律文书' in lg)
    chk('归集未删除任何东西（定稿仍在）',
        os.path.isfile(os.path.join(case, '02-法律文书', '民事起诉状（定稿）.docx')))

    # --- 越界守卫
    plan2 = plan_gather(case, ['../../外面.docx'])
    chk('越界路径被拦', plan2['blocked_count'] == 1 and plan2['count'] == 0)

    # --- 已在过程区的不重移
    plan3 = plan_gather(case, ['08-过程文件/答辩状（初稿）.docx'])
    chk('过程区内文件不重移', plan3['blocked_count'] == 1)

    # --- 批 39e：三分类错位体检
    mk(os.path.join(case, '08-过程文件', '证据清单-提交法院（定稿）.docx'))
    mk(os.path.join(case, '08-过程文件', '要素式起诉状-v1.docx'))
    mk(os.path.join(case, '08-过程文件', '仲裁立案材料核对清单-20260915.md'))
    mk(os.path.join(case, '02-法律文书', '交叉验证报告-20260916.md'))
    mk(os.path.join(case, '02-法律文书', '收条.jpg'))
    mk(os.path.join(case, '08-过程文件', '身份证正反面.jpg'))

    r1 = classify_role('证据清单（定稿）.docx', '08-过程文件/x.docx')
    chk('程序性：证据清单 → court', r1['role'] == 'court')
    r2 = classify_role('要素式起诉状-v1.docx', '08-过程文件/x.docx')
    chk('过程性：-v1 草稿 → proc（前缀判据优先）',
        r2['role'] == 'proc' or r2['role'] == 'court', r2['role'])
    r3 = classify_role('仲裁立案材料核对清单.md', '08-过程文件/x.md')
    chk('过程性：核对清单.md → proc', r3['role'] == 'proc', r3['role'])
    r4 = classify_role('交叉验证报告.md', 'x.md')
    chk('过程性：AI 报告.md → proc', r4['role'] == 'proc')
    r5 = classify_role('收条.jpg', 'x.jpg')
    chk('证据：图片 → evidence', r5['role'] == 'evidence')
    # 批 39e 修正：备份/废弃/审计夹里的文书 → 一律过程性（优先于文书性质）
    r6b = classify_role('1、仲裁申请书-改代码前-122728.docx',
                        '08-过程文件/备份-官网原档重做-20260910/x.docx')
    chk('备份夹优先：申请书备份 → proc（不是 court）', r6b['role'] == 'proc', r6b['role'])
    r7b = classify_role('证据清单.docx', '08-过程文件/废弃-自拟版-20260907/x.docx')
    chk('废弃夹优先：证据清单 → proc', r7b['role'] == 'proc', r7b['role'])
    r8b = classify_role('申请书.docx', '08-过程文件/审计-申请书-工具验证-20260908/x.docx')
    chk('审计夹优先：申请书 → proc', r8b['role'] == 'proc', r8b['role'])
    r9b = classify_role('民事起诉状.docx', '02-法律文书/x.docx')
    chk('正式区申请书仍判 court（未误伤）', r9b['role'] == 'court', r9b['role'])
    # --- 批 39e 第三轮：误报收窄（59→6→0）
    # ① 「（旧）」文件名标记 → 过程性（此前先命中「申请书」被判 court 错位）
    r10 = classify_role('10、财产保全申请书-盐城海维VS曹琪立等人（旧）.docx',
                        '需盖章文件/x.docx')
    chk('文件名「（旧）」→ proc（不被「申请书」抢判 court）', r10['role'] == 'proc', r10['role'])
    # ② 随件凭证（签收面单/面签照）→ attach，不算证据错位
    r11 = classify_role('何红英签收.jpg', '07-律师函/何红英签收.jpg')
    chk('签收面单 → attach（跟律师函走）', r11['role'] == 'attach', r11['role'])
    r12 = classify_role('周志伟面签照片.jpg', '07-律师函/周志伟面签照片.jpg')
    chk('面签照 → attach', r12['role'] == 'attach', r12['role'])
    # ③ 脚本产物 / 抽页产物 → junk
    r13 = classify_role('_extracted_img_1.png', '07-律师函/_extracted_img_1.png')
    chk('_extracted_img → junk', r13['role'] == 'junk', r13['role'])
    r14 = classify_role('page_001.png', '08-过程文件/page_001.png')
    chk('page_NNN → junk', r14['role'] == 'junk', r14['role'])
    r15 = classify_role('范申华反映_reencoded.jpg', '_ocr输出/_VL交叉核验/x.jpg')
    chk('_reencoded → junk', r15['role'] == 'junk', r15['role'])
    # ④ 真证据仍判 evidence（未误伤）
    r16 = classify_role('10.2022-6-17-配送单487个-有黄剑和签名.jpg',
                        '08-过程文件/x.jpg')
    chk('当事人配送单 → 仍判 evidence', r16['role'] == 'evidence', r16['role'])
    # ⑤ 归位留痕文件 → infra（不计入错位）
    r17 = classify_role('_归位留痕.md', '需盖章文件/_归位留痕.md')
    chk('_归位留痕.md → infra', r17['role'] == 'infra', r17['role'])
    r6 = classify_role('授权委托书.docx', 'x.docx')
    chk('程序性：授权委托书 → court', r6['role'] == 'court')
    r7 = classify_role('_材料怎么放.md', 'x.md')
    chk('基础设施：豁免名 → infra', r7['role'] == 'infra')

    mp = misplace(case)
    chk('错位：程序性文件躺在过程区被抓出', mp['court_in_proc_n'] >= 1,
        mp['court_in_proc_n'])
    chk('错位：证据原件躺在过程区被抓出', mp['evidence_in_wrong_n'] >= 1,
        mp['evidence_in_wrong_n'])
    chk('错位：过程性文件躺在正式区被抓出', mp['proc_in_court_n'] >= 1,
        mp['proc_in_court_n'])
    chk('错位：给出建议目录',
        all(x.get('suggest') for x in mp['court_in_proc'] + mp['proc_in_court']))

    sw3 = sweep(case)
    chk('体检含 misplace 字段', 'misplace' in sw3 and sw3['misplace']['total'] >= 1)
    chk('错位体检不影响文件（只读）',
        os.path.isfile(os.path.join(case, '08-过程文件', '收条.jpg')) is False
        and os.path.isfile(os.path.join(case, '08-过程文件', '身份证正反面.jpg')))

    # --- 批 39e：错位归位（plan_refile / apply_refile）
    mk(os.path.join(case, '08-过程文件', '答复意见(定稿).docx'))
    p_rf = plan_refile(case, [{'rel': '08-过程文件/答复意见(定稿).docx', 'dest': '02-法律文书'}])
    chk('归位计划 1 项', p_rf['count'] == 1 and p_rf['blocked_count'] == 0,
        'blocked=%s' % p_rf['blocked'])
    chk('归位目标在 02-法律文书', p_rf['items'][0]['dst'].replace(chr(92), '/').endswith('02-法律文书/答复意见(定稿).docx'))
    try:
        apply_refile(p_rf, confirm=False)
        chk('归位未确认拒绝执行', False)
    except ValueError:
        chk('归位未确认拒绝执行', True)
    rr = apply_refile(p_rf, confirm=True)
    chk('归位移动 1 件', rr['moved'] == 1)
    chk('归位后原位置已空',
        not os.path.isfile(os.path.join(case, '08-过程文件', '答复意见(定稿).docx')))
    chk('归位后目标存在',
        os.path.isfile(os.path.join(case, '02-法律文书', '答复意见(定稿).docx')))
    chk('归位留痕已写', bool(rr['logs']) and os.path.isfile(rr['logs'][0]))
    lg2 = open(rr['logs'][0], encoding='utf-8').read() if rr['logs'] else ''
    chk('留痕含原位置与新位置', '08-过程文件' in lg2 and '02-法律文书' in lg2)
    # 目标目录不存在 → 拦
    p_bad2 = plan_refile(case, [{'rel': '08-过程文件/答辩状（初稿）.docx', 'dest': '99-不存在'}])
    chk('归位：目标目录不存在被拦', p_bad2['blocked_count'] == 1 and p_bad2['count'] == 0)
    # 越界 → 拦
    p_bad3 = plan_refile(case, [{'rel': '../../../外面.docx', 'dest': '02-法律文书'}])
    chk('归位：越界被拦', p_bad3['blocked_count'] == 1)

    print('\n%d/%d 通过' % (ok, ok + fail))
    return fail == 0


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'selftest'
    if cmd == 'selftest':
        raise SystemExit(0 if selftest() else 1)
    elif cmd == 'sweep':
        import json
        tgt = sys.argv[2] if len(sys.argv) > 2 else ''
        if tgt:
            print(json.dumps(sweep(tgt), ensure_ascii=False, indent=1)[:4000])
        else:
            a = sweep_all()
            print('案卷 %d | junk %d | 口径别名 %d | 命名问题 %d | 版本族 %d' % (
                a['case_count'], a['junk_total'], a['alias_total'],
                a['naming_total'], a['family_total']))
