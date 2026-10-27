# -*- coding: utf-8 -*-
"""ocr_ops.py — 收件箱文件的**本地 OCR**（零外发）+ 用正文关键词辅助归位

设计纪律（2026-09-12 批 18，方案 §5.1 的 C 项）：
- **只走本地**：`paddleocr_ov` + `ocr_contract.py`（OpenVINO），案件材料绝不外发；本模块没有任何网络调用。
- **人工触发**：不做自动批量；页面上一次一个文件，点一次跑一次（识别慢，不能拖垮页面）。
- **有缓存**：识别文本落 `<案卷>\\08-过程文件\\_ocr\\<原名>.md`，第二次直接读缓存，不重复跑。
- **不改材料**：只新增 `_ocr` 里的文本；不动原件、不改名、不移动（归位仍走 `case_ops` 的入库流程）。
- 判定规则：**文件名关键词优先**（沿用 `case_ops.classify`），文件名认不出时才用正文打分；
  正文分数需 ≥3 且唯一最高才给建议，否则老实说「还是认不出」，不编。

CLI：`python ocr_ops.py [--selftest]` / `--plan <案卷> <文件名>` / `--run <案卷> <文件名> [--force]`
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

VERSION = '1.2.1'

import config as _config        # 开源版：OCR 通道由配置决定，默认关闭

DEFAULT_PY = _config.get('ocr.python', '') or ''
DEFAULT_SCRIPT = _config.get('ocr.script', '') or ''
OCR_ENABLED = bool(_config.get('ocr.enabled', False))
OCR_SUBDIR = '_ocr'                     # 08-过程文件\_ocr
SUPPORTED = {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
OCR_TIMEOUT = int(os.environ.get('OCR_TIMEOUT', '900'))
MAX_MB = int(os.environ.get('OCR_MAX_MB', '200'))

# ---- 批 65：大 PDF 分段识别（防超长文件卡死/超时/超限）----
# 原理：ocr_contract.py 原生支持 `--pages START-END` + 每页完成后写 checkpoint，
# 下次调用自动跳过已完成页、向同一 md **追加**输出 → 分段调用同一文件即可，
# 不需要真的把 PDF 切块。每段独立超时；任何一段失败/中断，重跑该段即断点续跑。
SEG_PAGES = int(os.environ.get('OCR_SEG_PAGES', '30'))          # 每段页数
SEG_PAGE_BUDGET_S = int(os.environ.get('OCR_SEG_PAGE_BUDGET', '20'))  # 每页时间预算（秒）
SEG_MIN_TIMEOUT = int(os.environ.get('OCR_SEG_MIN_TIMEOUT', '600'))   # 每段超时下限（秒）
SPLIT_MAX_MB = int(os.environ.get('OCR_SPLIT_MAX_MB', str(MAX_MB * 5)))  # 分段模式大小上限
SPLIT_LOCK_STALE_S = int(os.environ.get('OCR_SPLIT_LOCK_STALE', '1800'))  # 锁陈旧期（秒）

# 正文关键词（权重 3 = 强特征词；2 = 较强；1 = 泛指词，单独命中不足以判定）
TEXT_ROUTES = [
    ('04-法院文件', [('受理通知书', 3), ('受理案件通知书', 3), ('开庭传票', 3), ('传票', 3),
                     ('民事判决书', 3), ('判决书', 3), ('民事裁定书', 3), ('裁定书', 3),
                     ('民事调解书', 3), ('调解书', 3), ('执行通知书', 3), ('应诉通知书', 3),
                     ('送达回证', 2), ('举证通知书', 2), ('人民法院', 2),
                     # 批 80：与 case_ops.ROUTES 同步（立案送达包四类 + 交纳诉讼费用通知书）
                     ('廉政监督卡', 2), ('诉讼风险提示', 2), ('权利义务告知书', 2),
                     ('交纳诉讼费用通知书', 3)]),
    ('05-费用票据', [('发票', 3), ('诉讼费', 3), ('缴费', 2), ('收据', 2), ('律师费', 2),
                     ('增值税', 2)]),
    ('06-当事人信息', [('居民身份证', 3), ('身份证', 3), ('营业执照', 3), ('户口簿', 3),
                       ('户籍', 3), ('结婚证', 3), ('统一社会信用代码', 2)]),
    ('07-律师函', [('律师函', 3), ('催告', 2), ('EMS', 2), ('签收', 1)]),
    ('02-法律文书', [('民事起诉状', 3), ('起诉状', 3), ('上诉状', 3), ('答辩状', 3),
                     ('强制执行申请书', 3), ('财产保全申请书', 3), ('证据清单', 2),
                     ('代理意见', 2), ('申请书', 2), ('诉状', 2)]),
    ('01-委托手续', [('授权委托书', 3), ('委托代理合同', 3), ('委托书', 3), ('委托合同', 3),
                     ('律师事务所函', 3), ('所函', 2)]),
    ('08-过程文件', [('修订', 1), ('批注', 1), ('草稿', 2), ('讨论稿', 2)]),
    ('03-证据材料', [('销售合同', 2), ('买卖合同', 2), ('对账单', 2), ('对账确认', 2),
                     ('欠条', 2), ('借条', 2), ('送货单', 2), ('担保函', 2), ('承诺书', 2),
                     ('收货确认', 2), ('报价单', 2), ('报价表', 2),
                     ('转账', 1), ('聊天记录', 1), ('合同', 1)]),
]
MIN_TEXT_SCORE = 3


# ---------------------------------------------------------------- 环境
def py_exe():
    return os.environ.get('OCR_PYTHON', DEFAULT_PY)


def ocr_script():
    return os.environ.get('OCR_SCRIPT', DEFAULT_SCRIPT)


def available():
    """OCR 栈是否就位（解释器 + 脚本都在）"""
    return os.path.isfile(py_exe()) and os.path.isfile(ocr_script())


def is_supported(name):
    return os.path.splitext(name)[1].lower() in SUPPORTED


def ocr_dir(case_dir):
    return os.path.join(case_dir, '08-过程文件', OCR_SUBDIR)


def _resolve(case_dir, name):
    """把名字解析到案卷内的真实文件（禁止越界）。
    只给文件名时，自动去 `00-收件箱`（含子目录）里找；同名多个则要求给完整相对路径。"""
    import case_ops as C
    case_dir = os.path.abspath(case_dir)
    cand = os.path.abspath(name if os.path.isabs(name) else os.path.join(case_dir, name))
    if not cand.startswith(case_dir + os.sep):
        raise ValueError('越界，拒绝处理：%s' % name)
    if os.path.isfile(cand):
        return cand
    base = os.path.basename(name)
    inbox = os.path.join(case_dir, C.INBOX)
    hits = []
    if os.path.isdir(inbox):
        for root, _dirs, files in os.walk(inbox):
            hits += [os.path.join(root, f) for f in files if f == base]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError('文件不存在：%s（也没在 %s 里找到）' % (name, C.INBOX))
    raise ValueError('%s 里有 %d 个同名文件，请给完整相对路径' % (C.INBOX, len(hits)))


def cache_path(case_dir, name):
    stem = os.path.splitext(os.path.basename(name))[0]
    return os.path.join(ocr_dir(case_dir), stem + '.md')


def read_text(case_dir, name):
    """读已缓存的识别文本；没有则 None"""
    p = cache_path(case_dir, name)
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            return f.read()
    return None


def abs_path(case_dir, name):
    """把名字解析成案卷内的真实绝对路径（公开版 _resolve；越界抛 ValueError）。"""
    return _resolve(case_dir, name)


def save_text(case_dir, name, text):
    """批 66：保存人工修正后的识别文本（写回 `_ocr` 缓存；旧版留 `<原名>.md.bak`）。

    安全闸：文件必须在案卷内（_resolve）；只允许修正**已有识别缓存**的文件
    （没有缓存 → 拒绝，杜绝凭空造文本）；防超大文本（>200 万字符拒绝）。
    """
    try:
        _resolve(case_dir, name)
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    dst = cache_path(case_dir, name)
    if not os.path.isfile(dst):
        return {'ok': False, 'error': '还没有识别结果可修正（先跑一次识别）'}
    t = str(text if text is not None else '')
    if not t.strip():
        return {'ok': False, 'error': '修正文本不能为空'}
    if len(t) > 2000000:
        return {'ok': False, 'error': '文本过大（> 200 万字符），拒绝写入'}
    try:
        shutil.copyfile(dst, dst + '.bak')       # 旧版只留一份 .bak，防人工劳动丢失
        with open(dst, 'w', encoding='utf-8', newline='\n') as f:
            f.write(t)
    except OSError as e:
        return {'ok': False, 'error': '写回失败：%r' % (e,)}
    return {'ok': True, 'chars': len(t), 'path': dst,
            'note': '已保存修正（旧版留在 `<原名>.md.bak`）'}


def _fitz():
    """懒加载 PyMuPDF（只用于读页数/合并，缺了不影响普通单文件流程）。"""
    try:
        import pymupdf as fitz      # noqa: N813
        return fitz
    except ImportError:
        try:
            import fitz              # noqa: N813
            return fitz
        except ImportError:
            return None


def pdf_pages(src):
    """PDF 页数（非 PDF / 读不出 → None）。快（只读目录，不渲染）。"""
    if os.path.splitext(src)[1].lower() != '.pdf':
        return None
    fitz = _fitz()
    if fitz is None:
        return None
    try:
        d = fitz.open(src)
        try:
            return int(d.page_count)
        finally:
            d.close()
    except Exception:                # noqa: BLE001
        return None


def plan(case_dir, name):
    """单文件可识别性（只读）"""
    try:
        src = _resolve(case_dir, name)
    except ValueError as e:
        return {'ok': False, 'reason': str(e)}
    if not is_supported(name):
        return {'ok': False, 'reason': '不支持的类型（支持 PDF / 图片）：%s' % os.path.splitext(name)[1]}
    if not available():
        return {'ok': False, 'reason': '本机 OCR 栈没找到（%s）：请确认 D:\\ai-models 下的识别环境' % py_exe()}
    mb = os.path.getsize(src) / 1048576.0
    if mb > MAX_MB:
        return {'ok': False, 'reason': '文件 %.0fMB 超过上限 %dMB（可改走「分段识别」）' % (mb, MAX_MB),
                'split_hint': True, 'mb': round(mb, 1)}
    pages = pdf_pages(src)
    cached = read_text(case_dir, name) is not None
    r = {'ok': True, 'reason': ('已有识别结果（直接读缓存）' if cached else '可识别'),
         'cached': cached, 'mb': round(mb, 1)}
    if pages is not None:
        r['pages'] = pages
        r['split'] = pages > SEG_PAGES          # 批 65：超长文件建议分段
    return r


# ---------------------------------------------------------------- 正文打分
def score_text(text):
    """按正文关键词打分 → [(目录, 分数, 命中词)]，按分数降序。
    长词优先：已被长词覆盖的短词不重复计分（如「开庭传票」不再另计「传票」）。"""
    out = []
    for d, kws in TEXT_ROUTES:
        taken, hits, sc = [], [], 0
        for k, w in sorted(kws, key=lambda x: -len(x[0])):
            if k not in text:
                continue
            if any(k in t for t in taken):          # 被更长命中覆盖（含自身）
                continue
            taken.append(k)
            hits.append(k)
            sc += w
        if sc:
            out.append((d, sc, hits))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def suggest(case_dir, name, text):
    """归位建议：文件名优先；文件名认不出才用正文，分数需 ≥3 且唯一最高"""
    import case_ops as C
    try:
        src = _resolve(case_dir, name)
    except ValueError:
        src = os.path.join(case_dir, name)
    base = C.classify(src)
    if base.get('dir'):
        return {'dir': base['dir'], 'from': '文件名', 'reason': base['reason'],
                'newname': base.get('newname'), 'scores': []}
    sc = score_text(text or '')
    if not sc:
        return {'dir': None, 'from': '正文', 'reason': '正文也没有命中关键词，仍保持原位待人工判定',
                'newname': None, 'scores': []}
    top = sc[0]
    tie = [x for x in sc if x[1] == top[1]]
    if top[1] < MIN_TEXT_SCORE or len(tie) > 1:
        return {'dir': None, 'from': '正文',
                'reason': ('正文分数不足（最高 %s %d 分，需 ≥%d）' % (top[0], top[1], MIN_TEXT_SCORE))
                          if top[1] < MIN_TEXT_SCORE
                          else ('正文分数并列（%s），不猜' % '、'.join('%s %d分' % (x[0], x[1]) for x in tie[:3])),
                'newname': None, 'scores': sc[:4]}
    return {'dir': top[0], 'from': '正文',
            'reason': '正文命中「%s」等（%d 分）' % ('、'.join(top[2][:4]), top[1]),
            'newname': None, 'scores': sc[:4]}


# ---------------------------------------------------------------- 识别
def run(case_dir, name, force=False):
    """跑本地 OCR（或读缓存）。返回 {ok, cached, chars, seconds, path, text}"""
    pl = plan(case_dir, name)
    if not pl.get('ok'):
        return {'ok': False, 'error': pl.get('reason')}
    src = _resolve(case_dir, name)
    if not force:
        t = read_text(case_dir, name)
        if t is not None:
            return {'ok': True, 'cached': True, 'chars': len(t), 'seconds': 0.0,
                    'path': cache_path(case_dir, name), 'text': t}
    res = _engine_text(src, use_cache=not force)
    if not res['ok']:
        return {'ok': False, 'error': res['error']}
    text = res['text']
    d = ocr_dir(case_dir)
    os.makedirs(d, exist_ok=True)
    dst = cache_path(case_dir, name)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return {'ok': True, 'cached': res['cached'], 'chars': len(text),
            'seconds': res['seconds'], 'path': dst, 'text': text}


# ---------------------------------------------------------------- 批 65：大 PDF 分段识别
# 背景：超长卷宗 PDF 直接喂给识别脚本 = 全本渲染进内存/临时目录 + 总时长必超单次超时
# （900 秒），一失败全白跑。脚本原生支持 `--pages START-END`（只渲染本段页）+ 每页
# checkpoint（重跑自动跳过已完成页）+ 向同一 md 追加输出 → 分段调用同一文件即可：
#   · 每段独立超时，段级失败可单独重试（自动断点续跑，不重跑已完成的页）
#   · 段与段之间共用固定临时目录（checkpoint/md 累积），全部完成后把 md 拷回 _ocr 缓存
#   · 前端循环一段一段调（每段几分钟，避免单次 HTTP 请求挂几十分钟）
def split_segments(total_pages, seg_pages=None):
    """按每段 seg_pages 页切段 → [(start, end), ...]（1-based inclusive）"""
    sp = int(seg_pages or SEG_PAGES)
    out, p = [], 1
    while p <= total_pages:
        e = min(p + sp - 1, total_pages)
        out.append((p, e))
        p = e + 1
    return out


def _split_key(src):
    """分段临时目录键：路径+大小+mtime 的短哈希（不读全文，GB 级文件也秒出）。"""
    st = os.stat(src)
    raw = '%s|%d|%d' % (os.path.abspath(src), st.st_size, int(st.st_mtime))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _split_dir(src):
    root = os.environ.get('OCR_SPLIT_DIR') or os.path.join(tempfile.gettempdir(), 'wb_ocr_split')
    return os.path.join(root, _split_key(src))


def _seg_stem(stem, a, b):
    """脚本对 --pages 的输出名：`{stem}_P{a}-{b}`（实测 2026-10-14：md/checkpoint/gate 全用此名）。"""
    return '%s_P%d-%d' % (stem, a, b)


def _seg_md(sd, stem, a, b):
    return os.path.join(sd, _seg_stem(stem, a, b) + '.md')


def _seg_ck(sd, stem, a, b):
    return os.path.join(sd, _seg_stem(stem, a, b) + '.checkpoint.json')


def _seg_gate(sd, stem, a, b):
    return os.path.join(sd, _seg_stem(stem, a, b) + '.gate.json')


def _split_done_pages(sd, stem, segs):
    """已完成页数 = 各「完整完成」段的页数和（段 md 在、段 checkpoint 已清）。

    中途崩的段（checkpoint 还在）保守计 0——重跑该段时脚本会跳过已完成的页。
    """
    done = 0
    for a, b in segs:
        if os.path.isfile(_seg_md(sd, stem, a, b)) and not os.path.isfile(_seg_ck(sd, stem, a, b)):
            done += (b - a + 1)
    return done


def _split_lock(sd, release=False):
    """分段运行锁（防同文件两处并发识别/手快连点）。返回 (ok, holder_note)。"""
    lp = os.path.join(sd, '.run.lock')
    if release:
        try:
            os.remove(lp)
        except OSError:
            pass
        return True, ''
    if os.path.isfile(lp):
        try:
            age = time.time() - os.path.getmtime(lp)
        except OSError:
            age = 0
        if age < SPLIT_LOCK_STALE_S:
            note = ''
            try:
                with open(lp, encoding='utf-8', errors='replace') as f:
                    note = f.read().strip()[:80]
            except OSError:
                pass
            return False, note
        # 陈旧锁（> SPLIT_LOCK_STALE_S）→ 视为遗留，直接覆盖
    os.makedirs(sd, exist_ok=True)
    with open(lp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('pid=%d at=%s' % (os.getpid(), time.strftime('%Y-%m-%d %H:%M:%S')))
    return True, ''


def split_plan(case_dir, name, seg_pages=None):
    """分段识别计划（只读）：页数 / 段列表 / 续跑起点 / 已完成页数。"""
    try:
        src = _resolve(case_dir, name)
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    if not is_supported(name):
        return {'ok': False, 'error': '不支持的类型：%s' % os.path.splitext(name)[1]}
    if not available():
        return {'ok': False, 'error': '本机 OCR 栈没找到：%s' % py_exe()}
    mb = os.path.getsize(src) / 1048576.0
    if mb > SPLIT_MAX_MB:
        return {'ok': False, 'error': '文件 %.0fMB 超过分段上限 %dMB' % (mb, SPLIT_MAX_MB)}
    pages = pdf_pages(src)
    if pages is None:
        if os.path.splitext(src)[1].lower() == '.pdf':
            return {'ok': False, 'error': '读不到 PDF 页数（PyMuPDF 缺失或文件损坏）'}
        pages = 1                       # 图片 = 1 页 1 段（正常不会走分段）
    sp = int(seg_pages or SEG_PAGES)
    segs = split_segments(pages, sp)
    cached = read_text(case_dir, name) is not None
    sd = _split_dir(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    done_pages = pages if cached else _split_done_pages(sd, stem, segs)
    start = 1 if cached else min(done_pages // sp + 1, len(segs))
    return {'ok': True, 'pages': pages, 'seg_pages': sp, 'segs_total': len(segs),
            'done_pages': done_pages, 'starting_seg': start,
            'cached': cached, 'mb': round(mb, 1)}


def _split_finish(case_dir, name, sd, stem, segs, total, t0):
    """分段全部完成收尾：按段顺序合并各段 md（第一段保留 H1，后续段去 H1）→ 写 `_ocr` 缓存。"""
    parts = []
    for i, (a, b) in enumerate(segs):
        p = _seg_md(sd, stem, a, b)
        if not os.path.isfile(p):
            return {'ok': False, 'error': '段 %d-%d 的产物缺失，无法合并（请重跑该段）' % (a, b)}
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                txt = f.read()
        except OSError as e:
            return {'ok': False, 'error': '读段产物失败：%r' % (e,)}
        if i > 0:
            lines = txt.split('\n')
            if lines and lines[0].startswith('# '):
                txt = '\n'.join(lines[1:])
        parts.append(txt.strip('\n'))
    merged = parts[0] + '\n\n' + '\n\n'.join(parts[1:]) + '\n' if len(parts) > 1 else parts[0] + '\n'
    d = ocr_dir(case_dir)
    os.makedirs(d, exist_ok=True)
    dst = cache_path(case_dir, name)
    try:
        with open(dst, 'w', encoding='utf-8', newline='\n') as f:
            f.write(merged)
    except OSError as e:
        return {'ok': False, 'error': '分段识别已完成，但写缓存失败：%r' % (e,)}
    text = read_text(case_dir, name) or ''
    return {'ok': True, 'done': True, 'cached': False, 'chars': len(text),
            'seconds': round(time.time() - t0, 1) if t0 else 0.0,
            'seg': len(segs), 'segs_total': len(segs), 'seg_range': list(segs[-1]),
            'done_pages': total, 'total_pages': total, 'quality_rc': 0,
            'note': '分段识别全部完成（共 %d 页 / %d 段），已合并写入案卷 `_ocr` 缓存' % (total, len(segs))}


def run_segment(case_dir, name, seg=None, seg_pages=None, force=False):
    """分段识别一个段（1-based；seg=None 自动从续跑起点开始）。前端循环调用本函数。

    段完成判定（实测口径 2026-10-14）：段 md 产出（`{stem}_P{a}-{b}.md`）且段
    checkpoint 已清（脚本每页写 ckpt，全段处理完才删）；中途崩的段重跑自动续跑。

    返回 {ok, done, seg, segs_total, seg_range, done_pages, total_pages, chars,
          seconds, quality_rc, error, note}
    """
    pl = split_plan(case_dir, name, seg_pages)
    if not pl.get('ok'):
        return {'ok': False, 'error': pl.get('error')}
    src = _resolve(case_dir, name)
    sd = _split_dir(src)
    stem = os.path.splitext(os.path.basename(src))[0]

    if force:
        # 强制重跑：清分段现场（各段 md/checkpoint）+ 删旧缓存 → 从段 1 重新开始
        shutil.rmtree(sd, ignore_errors=True)
        cp = cache_path(case_dir, name)
        if os.path.isfile(cp):
            try:
                os.remove(cp)
            except OSError:
                pass
        pl = split_plan(case_dir, name, seg_pages)

    if pl.get('cached'):
        t = read_text(case_dir, name) or ''
        return {'ok': True, 'done': True, 'cached': True, 'chars': len(t), 'seconds': 0.0,
                'seg': 0, 'segs_total': pl['segs_total'], 'seg_range': None,
                'done_pages': pl['pages'], 'total_pages': pl['pages'], 'quality_rc': 0,
                'error': None, 'note': '已有完整识别（读缓存；要重跑请点「强制重新识别」）'}

    total = pl['pages']
    segs = split_segments(total, pl['seg_pages'])

    # 分段已完成但缓存未落（上次中断在收尾步骤）→ 直接收尾
    if pl['done_pages'] >= total:
        return _split_finish(case_dir, name, sd, stem, segs, total, 0)

    idx = int(seg or pl['starting_seg'])
    idx = max(1, min(idx, len(segs)))
    a, b = segs[idx - 1]

    ok_lock, holder = _split_lock(sd)
    if not ok_lock:
        return {'ok': False, 'seg': idx, 'segs_total': len(segs),
                'done_pages': pl['done_pages'], 'total_pages': total,
                'error': '该文件的分段识别正在进行中（%s）；请等它结束，或稍后再试' % (holder or '未知')}

    os.makedirs(sd, exist_ok=True)
    timeout = max(SEG_MIN_TIMEOUT, (b - a + 1) * SEG_PAGE_BUDGET_S + 300)
    cmd = [py_exe(), ocr_script(), src, sd, '-q', '--force', '--pages', '%d-%d' % (a, b)]
    t0 = time.time()
    quality_rc, err, tail = 0, None, ''
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        quality_rc = r.returncode
        tail = ((r.stdout or b'').decode('utf-8', 'replace')[-300:]
                + (r.stderr or b'').decode('utf-8', 'replace')[-300:])
    except subprocess.TimeoutExpired as e:
        quality_rc, err = -1, '本段超时（> %d 秒）——继续识别会自动跳过已完成的页' % timeout
        tail = ((e.stdout or b'').decode('utf-8', 'replace')[-300:]
                + (e.stderr or b'').decode('utf-8', 'replace')[-300:]) if (e.stdout or e.stderr) else ''
    finally:
        _split_lock(sd, release=True)

    secs = round(time.time() - t0, 1)
    seg_md = _seg_md(sd, stem, a, b)
    if not os.path.isfile(seg_md):
        if err is None:
            err = '本段没有产出文本（rc=%s）：%s' % (quality_rc, tail.strip()[:200])
        return {'ok': False, 'seg': idx, 'segs_total': len(segs), 'seg_range': [a, b],
                'done_pages': pl['done_pages'], 'total_pages': total,
                'seconds': secs, 'error': err}

    # 段未完整（checkpoint 未清）：崩/超时等 → 报告未完成（前端重试=断点续跑）
    if os.path.isfile(_seg_ck(sd, stem, a, b)):
        if err is None:
            err = '本段未完整跑完（rc=%s，脚本已记录断点）——继续识别会自动跳过已完成的页' \
                  % quality_rc
        return {'ok': False, 'seg': idx, 'segs_total': len(segs), 'seg_range': [a, b],
                'done_pages': _split_done_pages(sd, stem, segs), 'total_pages': total,
                'seconds': secs, 'error': err}

    note = ''
    # 质量注以段 gate.json 为准（实测：--pages 模式下脚本 rc 门有文件名盲区，rc 不可靠）
    gate = _seg_gate(sd, stem, a, b)
    if os.path.isfile(gate):
        try:
            with open(gate, encoding='utf-8') as f:
                g = json.load(f)
            gs = g.get('status')
            if gs == 'fail':
                n_fail = len(g.get('pages_failed') or [])
                note = ('质量门警告：%d 页强制复核未完成（识别文本已保留，建议对照原文抽查）'
                        % n_fail) if n_fail else '质量门警告：存在强制复核未完成的页，建议对照原文抽查'
            elif gs == 'review':
                note = '有少量低置信页（C 级），建议抽查'
        except (OSError, ValueError):
            pass
    if quality_rc not in (0, 1, 2):
        note = (note + '；' if note else '') + '脚本退出码 %s（尾部：%s）' % (quality_rc, tail.strip()[:160])

    done_pages = _split_done_pages(sd, stem, segs)
    if done_pages >= total:
        fin = _split_finish(case_dir, name, sd, stem, segs, total, t0)
        if note and fin.get('ok'):
            fin['note'] = (fin.get('note') or '') + '；' + note
        return fin
    return {'ok': True, 'done': False, 'seg': idx, 'segs_total': len(segs),
            'seg_range': [a, b], 'done_pages': done_pages, 'total_pages': total,
            'chars': 0, 'seconds': secs, 'quality_rc': quality_rc,
            'error': None, 'note': note}


# ---------------------------------------------------------------- 自测
# ------------------------------------------------- 发票识别（批 30）：本机 OCR 文本 → 结构化字段
#
# 口径来自**真实样本实测**（2 张增值税电子发票 + 1 张滴滴行程单，本机 OCR，零外发）：
#   增值税发票的「标签」和「值」是**分开排版**的（标签在前、值集中在中后段）→ **不能按标签取值**，
#   只能按「形状」抓：20 位数字 = 发票号码、`YYYY年MM月DD日` = 开票日期、多个 ¥金额取**最大**那个
#   = 价税合计（小写）；`*类*项目` 里的项目名 = 费用项目。
#   ⚠️ 实测结论：**发票上通常没有案件当事人**（购买方是我们律所、销售方是开票单位）
#      → **认不出案子，必须由人选案件**；行程单能给出「去向线索」（法院/政府名）作辅助。
INV_NO20_RE = re.compile(r'(?<!\d)(\d{20})(?!\d)')
INV_NO8_RE = re.compile(r'(?<!\d)(\d{8})(?!\d)')
INV_DATE_CN_RE = re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')
INV_DATE_ISO_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
INV_MONEY_RE = re.compile(r'[¥￥]\s*([\d,]+\.\d{2})')
INV_TOTAL_RE = re.compile(r'合计\s*([\d,]+\.\d{2})\s*元')
# 2026-10-19 补（实测红冲发票被抽成税额 0.23）：
#   ①「价税合计（小写）」标签与数字常被 OCR **拆成多行** → 必须跨行找，且数字前可能没有 ¥
#   ② 小写缺失时用「价税合计（大写）」的中文金额兜底（柒圆玖角整 → 7.90）
INV_TOTAL_SMALL_RE = re.compile(
    r'价税合计[\s\S]{0,60}?[（(]\s*小写\s*[）)][\s\S]{0,20}?[¥￥]?\s*([\d,]+\.\d{2})')
INV_TOTAL_CN_RE = re.compile(
    r'价税合计[\s\S]{0,160}?([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]{1,20}[圆元]'
    r'(?:[零壹贰叁肆伍陆柒捌玖]角)?(?:[零壹贰叁肆伍陆柒捌玖]分)?)[整正]?')
_CN_DIGITS = {'零': 0, '壹': 1, '贰': 2, '叁': 3, '肆': 4,
              '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9}
_CN_UNITS = {'拾': 10, '佰': 100, '仟': 1000}
_CN_AMOUNT_RE = re.compile(
    r'([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]{1,20})[圆元]'
    r'(?:([零壹贰叁肆伍陆柒捌玖])角)?'
    r'(?:([零壹贰叁肆伍陆柒捌玖])分)?')
INV_ITEM_RE = re.compile(r'\*[^*\n]+\*([^\s\n*]+)')
INV_ORG_RE = re.compile(r'([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,30}?'
                        r'(?:律师事务所|有限公司|有限责任公司|公司|事务所|经营部|服务中心|中心|'
                        r'合作社|商行|超市|工厂|厂|店|药店|医院|学校|人民政府|法院|人民检察院|税务局))')
OUR_FIRM_HINT = '飞想'
TRAVEL_HINT_RE = re.compile(r'行程单|行程起止|滴滴|高德|打车|顺风车|铁路|动车|火车票|机票|行程人')
PLACE_HINT_RE = re.compile(r'([\u4e00-\u9fa5]{2,20}?(?:人民法院|法院|人民政府|人民检察院|公安局|看守所|税务局|仲裁委员会))')


def _cn_section(s):
    """把小于「万」的一段中文数字转成整数（柒仟伍佰 → 7500）；认不出返回 0"""
    total, cur = 0, 0
    for ch in s or '':
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (cur or 1) * _CN_UNITS[ch]      # 「拾」开头＝10，不是 0
            cur = 0
        else:
            return 0
    return total + cur


def _cn_amount_to_number(s):
    """中文大写金额 → '12345.67'

    只处理**完整大写金额**（带 圆/元）；认不出返回空串，绝不猜。
    """
    s = str(s or '').strip()
    for tail in ('整', '正'):
        if s.endswith(tail):
            s = s[:-1]
    m = _CN_AMOUNT_RE.fullmatch(s)
    if not m:
        return ''
    ints = m.group(1) or ''
    total = 0
    if '亿' in ints:
        head, ints = ints.split('亿', 1)
        total += _cn_section(head) * 100000000
    if '万' in ints:
        head, ints = ints.split('万', 1)
        total += _cn_section(head) * 10000
    total += _cn_section(ints)
    jiao = _CN_DIGITS.get(m.group(2) or '', 0)
    fen = _CN_DIGITS.get(m.group(3) or '', 0)
    if not total and not jiao and not fen and '零' not in ints:
        return ''
    return '%.2f' % (total + jiao / 10.0 + fen / 100.0)


def extract_invoice(text):
    """从本机 OCR 文本抽发票要素（**只报抽到的，抽不到留空**，不猜不编）。

    返回 {类型, 发票号码, 开票日期, 月, 金额, 项目, 单位, 疑似我方, 去向线索, 提示}
    """
    raw = str(text or '')
    # ⚠️ 实测坑：OCR 文本第一行是**从文件名生成的 H1 标题**（不是发票内容），
    #    直接抽取会把「发票-飞想VS某某律所」这种文件名当成内容 → 先剔除 # 开头的行。
    t = '\n'.join(l for l in raw.splitlines() if not l.lstrip().startswith('#'))
    out = {'类型': '', '发票号码': '', '开票日期': '', '月': '', '金额': '', '项目': '',
           '单位': [], '疑似我方': '', '去向线索': [], '提示': []}
    if '专用发票' in t:
        out['类型'] = '增值税专票'
    elif '普通发票' in t:
        out['类型'] = '增值税普票'
    elif '增值税' in t:
        out['类型'] = '增值税发票'
    elif TRAVEL_HINT_RE.search(t):
        out['类型'] = '行程单/车票'
    elif t.strip():
        out['类型'] = '其他票据'
    m = INV_NO20_RE.search(t) or INV_NO8_RE.search(t)
    if m:
        out['发票号码'] = m.group(1)
    m = INV_DATE_CN_RE.search(t)
    if m:
        out['开票日期'] = '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        out['月'] = '%04d-%02d' % (int(m.group(1)), int(m.group(2)))
    else:
        m = INV_DATE_ISO_RE.search(t)
        if m:
            out['开票日期'] = '%s-%s-%s' % m.groups()
            out['月'] = '%s-%s' % (m.group(1), m.group(2))
    # 金额：①价税合计（小写）→ ②价税合计（大写）→ ③原「¥ 取最大」→ ④原「合计 X 元」
    # ⚠️ 2026-10-19 实测：红冲发票里 0.23 是**税额**、7.90 才是价税合计，且 ¥ 与数字被拆成两行
    #    → 旧口径「各 ¥ 取最大」把 7.90 抽成 0.23。新口径先认标签，认不到才退回旧口径（行为不变）。
    amount = ''
    m = INV_TOTAL_SMALL_RE.search(t)
    if m:
        amount = m.group(1).replace(',', '')
    if not amount:
        m = INV_TOTAL_CN_RE.search(t)
        if m:
            amount = _cn_amount_to_number(m.group(1))
    if not amount:
        vals = []
        for x in INV_MONEY_RE.findall(t):
            try:
                vals.append(float(x.replace(',', '')))
            except ValueError:
                pass
        if vals:
            amount = '%.2f' % max(vals)          # 价税合计 = 各 ¥ 金额里的最大值
    if not amount:
        m = INV_TOTAL_RE.search(t)
        if m:
            amount = m.group(1).replace(',', '')
    out['金额'] = amount
    items = [x.strip() for x in INV_ITEM_RE.findall(t) if x.strip()]
    if items:
        out['项目'] = items[0]
    orgs = []
    for x in INV_ORG_RE.findall(t):
        x = x.strip()
        if len(x) >= 4 and x not in orgs:
            orgs.append(x)
    out['单位'] = orgs[:5]
    ours = [x for x in orgs if OUR_FIRM_HINT in x]
    if ours:
        out['疑似我方'] = ours[0]
    places = []
    for x in PLACE_HINT_RE.findall(t):
        if x not in places:
            places.append(x)
    out['去向线索'] = places[:3]
    if not out['发票号码'] and not out['金额']:
        out['提示'].append('没抽到发票号码和金额（可能不是发票，或识别质量不足）')
    return out


def content_cache_dir():
    """本模块自己的**内容哈希缓存**目录（`OCR_CONTENT_CACHE_DIR` 可覆盖）。

    为什么要自备一份：实测 `ocr_contract.py` 命中它自己的缓存时**不会把 md 落进输出目录**
    （它的缓存名是 `cache/sha256:<hex>.md`，Windows 上那个 `:` 被当成 NTFS 备用数据流，
    于是输出目录是空的）→ 任何依赖输出目录的调用方「第二次跑同一个文件」都拿不到文本。
    这里不改那个共享脚本，改成：**一律 `--force` 跑**（数字版 PDF 基本是秒出）+ 用内容 SHA-256 自缓存。
    """
    d = os.environ.get('OCR_CONTENT_CACHE_DIR')
    if d:
        return d
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ocr_cache')


def _content_key(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _engine_text(src, use_cache=True):
    """跑本机识别拿文本（**一律 `--force`**，绕开契约缓存不落盘的问题）；带内容哈希缓存。

    返回 {ok, cached, text, seconds} 或 {ok: False, error}
    """
    cdir, cpath, text = content_cache_dir(), None, None
    try:
        cpath = os.path.join(cdir, _content_key(src) + '.md')
    except OSError as e:
        return {'ok': False, 'error': '读文件失败：%r' % (e,)}
    if use_cache and os.path.isfile(cpath):
        t = open(cpath, encoding='utf-8', errors='replace').read()
        if t.strip():
            return {'ok': True, 'cached': True, 'text': t, 'seconds': 0.0}
    tmp = tempfile.mkdtemp(prefix='wb_ocrf_')
    t0 = time.time()
    cmd = [py_exe(), ocr_script(), src, tmp, '-q', '--force']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=OCR_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        return {'ok': False, 'error': '识别超时（> %d 秒）' % OCR_TIMEOUT}
    stem = os.path.splitext(os.path.basename(src))[0]
    md = os.path.join(tmp, stem + '.md')
    if not os.path.isfile(md):
        cands = [f for f in os.listdir(tmp)
                 if f.lower().endswith('.md') and not f.endswith('_facts.md')]
        md = os.path.join(tmp, cands[0]) if cands else ''
    if not md or not os.path.isfile(md):
        tail = ((r.stdout or b'').decode('utf-8', 'replace')[-300:]
                + (r.stderr or b'').decode('utf-8', 'replace')[-300:])
        shutil.rmtree(tmp, ignore_errors=True)
        return {'ok': False, 'error': '识别没有产出文本（rc=%s）：%s' % (r.returncode, tail.strip()[:200])}
    text = open(md, encoding='utf-8', errors='replace').read()
    shutil.rmtree(tmp, ignore_errors=True)
    if text.strip() and cpath:
        try:
            os.makedirs(cdir, exist_ok=True)
            open(cpath, 'w', encoding='utf-8', newline='\n').write(text)
        except OSError:
            pass
    return {'ok': True, 'cached': False, 'text': text, 'seconds': round(time.time() - t0, 1)}


def run_file(src, cache_dir=None, force=False):
    """对**任意路径**文件跑本机 OCR（上传的发票用；不走案卷路径守卫、不动源文件）。

    cache_dir 给定时把文本写进去当缓存（按原文件名）。
    返回 {ok, cached, chars, seconds, text, path}
    """
    if not os.path.isfile(src):
        return {'ok': False, 'error': '文件不存在：%s' % src}
    if not is_supported(src):
        return {'ok': False, 'error': '不支持的类型（PDF/图片）：%s' % os.path.splitext(src)[1]}
    if not available():
        return {'ok': False, 'error': '本机 OCR 栈没找到：%s' % py_exe()}
    mb = os.path.getsize(src) / 1048576.0
    if mb > MAX_MB:
        return {'ok': False, 'error': '文件 %.0fMB 超过上限 %dMB' % (mb, MAX_MB)}
    cached_path = None
    if cache_dir:
        cached_path = os.path.join(cache_dir,
                                   os.path.splitext(os.path.basename(src))[0] + '.md')
        if not force and os.path.isfile(cached_path):
            t = open(cached_path, encoding='utf-8', errors='replace').read()
            if t.strip():
                return {'ok': True, 'cached': True, 'chars': len(t), 'seconds': 0.0,
                        'text': t, 'path': cached_path}
    res = _engine_text(src, use_cache=not force)
    if not res['ok']:
        return {'ok': False, 'error': res['error']}
    text = res['text']
    if cached_path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            open(cached_path, 'w', encoding='utf-8', newline='\n').write(text)
        except OSError:
            pass
    return {'ok': True, 'cached': res['cached'], 'chars': len(text),
            'seconds': res['seconds'], 'text': text, 'path': cached_path}


def selftest():
    ok = 0
    fail = []

    def ck(name, cond):
        nonlocal ok
        if cond:
            ok += 1
            print('PASS %s' % name)
        else:
            fail.append(name)
            print('FAIL %s' % name)

    TMP = tempfile.mkdtemp(prefix='ocr_selftest_')
    case = os.path.join(TMP, '民-A-26111_甲VS乙_买卖合同纠纷')
    os.makedirs(os.path.join(case, '00-收件箱'), exist_ok=True)
    fake = os.path.join(TMP, 'fake_ocr.py')
    logf = os.path.join(TMP, 'runs.log')
    with open(fake, 'w', encoding='utf-8', newline='\n') as f:
        f.write(          # 批 65 升级：支持 --pages 分段（段独立产物名 `{stem}_P{a}-{b}` + 段级 checkpoint）
            'import json, os, pathlib, re, sys\n'
            'inp, outdir = sys.argv[1], sys.argv[2]\n'
            'os.makedirs(outdir, exist_ok=True)\n'
            'stem = pathlib.Path(inp).stem\n'
            'c = os.environ.get("FAKE_OCR_COUNTER")\n'
            'open(c, "a").write("1\\n") if c else None\n'
            'pages = None\n'
            'for i, a in enumerate(sys.argv):\n'
            '    if a == "--pages":\n'
            '        s, e = sys.argv[i + 1].split("-"); pages = (int(s), int(e))\n'
            'if pages is None:\n'
            '    pathlib.Path(outdir, stem + ".md").write_text(\n'
            '        os.environ.get("FAKE_OCR_TEXT", "本院受理通知书"), encoding="utf-8")\n'
            '    sys.exit(0)\n'
            'failf = pathlib.Path(outdir, ".fake_failed_once")\n'
            'if os.environ.get("FAKE_OCR_FAIL_ONCE") and not failf.exists():\n'
            '    failf.write_text("1"); sys.exit(3)\n'
            'seg = "%s_P%d-%d" % (stem, pages[0], pages[1])\n'
            'md = pathlib.Path(outdir, seg + ".md")\n'
            'done = set()\n'
            'if md.exists():\n'
            '    done = {int(m.group(1)) for m in re.finditer(r"^## Page (\\d+)", md.read_text(encoding="utf-8"), re.M)}\n'
            'with open(md, "a", encoding="utf-8") as fh:\n'
            '    if not done:\n'
            '        fh.write("# " + stem + "\\n\\n")\n'
            '    for pg in range(pages[0], pages[1] + 1):\n'
            '        if pg not in done:\n'
            '            fh.write("## Page %d\\n\\n%s p%d\\n\\n---\\n\\n" % (pg, os.environ.get("FAKE_OCR_TEXT", "text"), pg))\n'
            'ck = pathlib.Path(outdir, seg + ".checkpoint.json")\n'
            'if os.environ.get("FAKE_OCR_LEAVE_CKPT"):\n'
            '    ck.write_text(json.dumps({"completed_pages": sorted(done | set(range(pages[0], pages[1] + 1)))}), encoding="utf-8")\n'
            'else:\n'
            '    ck.unlink(missing_ok=True)\n'
            'sys.exit(0)\n')
    os.environ['OCR_PYTHON'] = sys.executable
    os.environ['OCR_SCRIPT'] = fake
    os.environ['FAKE_OCR_COUNTER'] = logf
    os.environ['OCR_CONTENT_CACHE_DIR'] = os.path.join(TMP, 'ccache')   # 批 30：自缓存隔离
    os.environ['OCR_SPLIT_DIR'] = os.path.join(TMP, 'split')            # 批 65：分段现场隔离

    doc = os.path.join(case, '00-收件箱', '扫描件.pdf')
    with open(doc, 'wb') as f:
        f.write(b'%PDF-1.4 fake')
    txt = os.path.join(case, '00-收件箱', '说明.txt')
    with open(txt, 'w', encoding='utf-8') as f:
        f.write('x')
    out_side = os.path.join(TMP, '外面.pdf')
    with open(out_side, 'wb') as f:
        f.write(b'x')

    ck('OCR 栈可用', available())
    ck('支持 .pdf 不支持 .txt', is_supported('a.pdf') and not is_supported('a.txt'))
    ck('不支持类型被拒', plan(case, '说明.txt')['ok'] is False)
    ck('越界文件被拒', plan(case, out_side)['ok'] is False)
    ck('未识别时无缓存', read_text(case, '扫描件.pdf') is None)

    os.environ['FAKE_OCR_TEXT'] = '本院受理通知书 案号（2026）闽0122民初1016号 请于开庭传票载明日期到庭'
    r = run(case, '扫描件.pdf')
    ck('识别成功并落盘', r['ok'] and not r['cached'] and r['chars'] > 10)
    ck('缓存文件在 08-过程文件\\_ocr', os.path.isfile(os.path.join(case, '08-过程文件', '_ocr', '扫描件.md')))
    ck('原文件未被动过', os.path.getsize(doc) == len(b'%PDF-1.4 fake'))
    sg = suggest(case, '扫描件.pdf', r['text'])
    ck('正文建议归 04-法院文件', sg['dir'] == '04-法院文件' and sg['from'] == '正文')
    r2 = run(case, '扫描件.pdf')
    ck('二次调用走缓存（不重复跑）', r2['cached'] is True)
    ck('只跑了一次 OCR 进程', len(open(logf, encoding='utf-8').read().split()) == 1)

    os.environ['FAKE_OCR_TEXT'] = '随便什么内容都没有关键词'
    r3 = run(case, '扫描件.pdf', force=True)
    ck('force 重跑', r3['ok'] and r3['cached'] is False)
    sg3 = suggest(case, '扫描件.pdf', r3['text'])
    ck('没命中就老实说认不出', sg3['dir'] is None)

    # 文件名优先：文件名已能判定时，不走正文
    named = os.path.join(case, '00-收件箱', '授权委托书.pdf')
    with open(named, 'wb') as f:
        f.write(b'%PDF-1.4 fake')
    sg4 = suggest(case, '授权委托书.pdf', '本院受理通知书 传票')
    ck('文件名优先于正文', sg4['dir'] == '01-委托手续' and sg4['from'] == '文件名')

    ck('并列分数不给建议', suggest(case, '扫描件.pdf',
                                '受理通知书 对账单 聊天记录')['dir'] is None)
    ck('长词优先不重复计分（开庭传票 不再另计 传票）',
       [x for x in score_text('开庭传票') if x[0] == '04-法院文件'][0][1] == 3)

    # 批 30：发票字段抽取（合成样本文本，结构与实测一致；不含任何真实当事人）
    INV_TXT = (
        '# 合成发票\n## Page 1 (digital)\n电子发票（增值税专用发票）\n发票号码：\n开票日期：\n'
        '购\n买\n方\n信\n息\n统一社会信用代码/纳税人识别号：\n销\n售\n方\n信\n息\n'
        '统一社会信用代码/纳税人识别号：\n名称：\n名称：\n项目名称\n规格型号\n单 位\n数 量\n'
        '单 价\n金 额\n税率/征收率\n税 额\n合\n计\n价税合计（大写）\n（小写）\n备\n注\n开票人：\n'
        '26942000000893308906\n2026年07月29日\n某某律师事务所\n313200000000000000\n'
        '甲甲飞想律师事务所\n31350000MD0274017T\n¥37128.71\n¥371.29\n叁万柒仟伍佰圆整\n'
        '¥37500.00\n田某\n*生产生活服务*律师费\n1%\n件\n37128.71\n371.29\n')
    ei = extract_invoice(INV_TXT)
    ck('发票：认出发票号码（20 位）', ei['发票号码'] == '26942000000893308906')
    ck('发票：日期与月份归一化', ei['开票日期'] == '2026-07-29' and ei['月'] == '2026-07')
    ck('发票：金额取价税合计（各 ¥ 最大值）', ei['金额'] == '37500.00')
    ck('发票：抽出费用项目', ei['项目'] == '律师费')
    ck('发票：类型判为增值税专票', ei['类型'] == '增值税专票')
    ck('发票：认出我方律所（含飞想）',
        ei['疑似我方'].endswith('飞想律师事务所') and len(ei['单位']) >= 2)
    TRIP_TXT = ('滴滴顺风车行程单\nDIDI HITCH - TRIP TABLE\n· 申请日期：2026-08-18\n'
                '· 行程人手机号：13100000000\n· 共1笔行程， 合计55.00元\n'
                '1\n独享\n厦门市\n08-18 08:55 周二\n08-18 09:37 周二\n'
                '甲甲飞想律师事务所\n漳州市龙海区人民政府\n37.94\n55.00\n')
    et = extract_invoice(TRIP_TXT)
    ck('行程单：类型判对', et['类型'] == '行程单/车票')
    ck('行程单：金额取「合计 X 元」', et['金额'] == '55.00')
    ck('行程单：日期取 ISO 式', et['开票日期'] == '2026-08-18' and et['月'] == '2026-08')
    ck('行程单：给出「去向线索」（法院/政府）',
        any('人民政府' in x for x in et['去向线索']))
    en = extract_invoice('这是一段没有任何发票要素的话')
    ck('抽不到就不编：号码/金额留空并给提示',
        en['发票号码'] == '' and en['金额'] == '' and en['提示'])
    chk_h1 = extract_invoice(
        '# 发票-甲甲飞想VS乙乙公司\n开票人：\n26942000000000000001\n2026年01月02日\n'
        '乙乙公司\n313200000000000000\n甲甲飞想律师事务所\n31350000MD0274017T\n'
        '¥100.00\n¥1.00\n壹佰零壹圆整\n¥101.00\n*服务*律师费\n')
    ck('H1（文件名标题）不参与抽取，我方名称干净',
        'VS' not in chk_h1['疑似我方'] and chk_h1['疑似我方'].endswith('飞想律师事务所')
        and chk_h1['金额'] == '101.00')
    # 2026-10-19：红冲发票（¥ 与数字被 OCR 拆行、0.23 是税额）→ 必须认「价税合计（小写）」
    INV_RED = (
        '# 合成红冲发票\n## Page 1 (digital)\n电子发票（普通发票）\n'
        '发票号码: 26947000000131856554\n开票日期: 2026年10月13日\n'
        '名称：甲甲飞想律师事务所\n名称：某某出行科技有限公司\n'
        '*交通运输服务*客运服\n15.44\n1\n15.44\n3%\n0.46\n'
        '*交通运输服务*客运服\n-7.77\n3%\n-0.23\n'
        '合\n计\n7.67\n¥\n0.23\n¥\n'
        '价税合计（大写）\n（小写）\n7.90\n¥\n柒圆玖角整\n')
    er = extract_invoice(INV_RED)
    ck('红冲发票：金额认「价税合计（小写）」而非税额 0.23', er['金额'] == '7.90')
    ck('红冲发票：号码与日期不受影响',
       er['发票号码'] == '26947000000131856554' and er['月'] == '2026-10')

    # 小写缺失 → 中文大写兜底
    INV_CN_ONLY = ('电子发票（普通发票）\n26942000000000000002\n2026年09月01日\n'
                   '价税合计（大写）叁万柒仟伍佰圆整\n*服务*律师费\n')
    ck('大写金额兜底：叁万柒仟伍佰圆整 → 37500.00',
       extract_invoice(INV_CN_ONLY)['金额'] == '37500.00')

    # 价税合计（小写）优先于更大的 ¥ 干扰值
    INV_PRIO = ('电子发票（普通发票）\n26942000000000000003\n2026年09月02日\n'
                '¥9999.99\n价税合计（小写）¥123.45\n')
    ck('价税合计（小写）优先于 ¥ 大值干扰', extract_invoice(INV_PRIO)['金额'] == '123.45')

    # 大写转换函数单元用例
    ck('大写转换：柒圆玖角整', _cn_amount_to_number('柒圆玖角整') == '7.90')
    ck('大写转换：壹佰零壹圆整', _cn_amount_to_number('壹佰零壹圆整') == '101.00')
    ck('大写转换：拾圆整', _cn_amount_to_number('拾圆整') == '10.00')
    ck('大写转换：壹万贰仟叁佰肆拾伍圆陆角柒分',
       _cn_amount_to_number('壹万贰仟叁佰肆拾伍圆陆角柒分') == '12345.67')
    ck('大写转换：认不出就返回空（不猜）', _cn_amount_to_number('乱七八糟') == '')

    ck('空文本不炸', extract_invoice('')['类型'] == '' and extract_invoice(None)['单位'] == [])

    # ---- 批 65：大 PDF 分段识别（fake 脚本打桩；合成 100 页数字版 PDF 只测页数探测+调度）----
    fitz = _fitz()
    if fitz is None:
        ck('分段：PyMuPDF 缺失 → 跳过（本机应装有）', False)
    else:
        big = os.path.join(case, '00-收件箱', '大卷宗.pdf')
        bd = fitz.open()
        for _i in range(100):
            bp = bd.new_page(width=595, height=842)
            bp.insert_text((72, 100), 'content', fontsize=12)
        bd.save(big)
        bd.close()
        os.environ['FAKE_OCR_TEXT'] = '通用内容'
        ck('分段：页数探测 = 100', pdf_pages(big) == 100)
        pl = split_plan(case, '大卷宗.pdf')
        ck('分段：计划 4 段（30/30/30/10）、起点段 1',
           pl['ok'] and pl['segs_total'] == 4 and pl['seg_pages'] == 30
           and pl['starting_seg'] == 1 and pl['done_pages'] == 0)
        r1 = run_segment(case, '大卷宗.pdf')
        ck('分段：段1 → ok 未完成 进度 30/100',
           r1['ok'] and (not r1['done']) and r1['done_pages'] == 30 and r1['seg'] == 1)
        ck('分段：段产物按段独立命名（大卷宗_P1-30.md）',
           os.path.isfile(os.path.join(_split_dir(big), '大卷宗_P1-30.md')))
        pl2 = split_plan(case, '大卷宗.pdf')
        ck('分段：续跑起点自动到段 2', pl2['starting_seg'] == 2)
        r2 = run_segment(case, '大卷宗.pdf', seg=2, force=False)
        ck('分段：段2 → 进度 60/100', r2['ok'] and r2['done_pages'] == 60)
        r3 = run_segment(case, '大卷宗.pdf', seg=3)
        ck('分段：段3 → 进度 90/100', r3['ok'] and r3['done_pages'] == 90)
        r4 = run_segment(case, '大卷宗.pdf', seg=4)
        ck('分段：段4 → done + 缓存落位',
           r4['ok'] and r4['done'] and r4['done_pages'] == 100
           and read_text(case, '大卷宗.pdf') is not None)
        t = read_text(case, '大卷宗.pdf') or ''
        ck('分段：合并 md 无重复页（100 页各一次、H1 只一个）',
           len(re.findall(r'^## Page \d+', t, re.M)) == 100 and '## Page 100' in t
           and t.count('# 大卷宗') == 1)
        _sd = _split_dir(big)
        ck('分段：全完成后各段 checkpoint 已清',
           not os.path.isfile(os.path.join(_sd, '大卷宗_P1-30.checkpoint.json'))
           and not os.path.isfile(os.path.join(_sd, '大卷宗_P91-100.checkpoint.json')))
        rr = run_segment(case, '大卷宗.pdf')
        ck('分段：完成后重调 → 读缓存不重跑', rr['ok'] and rr['done'] and rr['cached'])
        # 收尾中断模拟：缓存删掉、分段现场还在 → 自动收尾
        os.remove(cache_path(case, '大卷宗.pdf'))
        rf = run_segment(case, '大卷宗.pdf')
        ck('分段：收尾中断 → 重调自动补拷缓存',
           rf['ok'] and rf['done'] and read_text(case, '大卷宗.pdf') is not None)
        # 失败重试：第一次 rc=3 不产出 → 重调同段成功
        os.remove(cache_path(case, '大卷宗.pdf'))
        os.environ['FAKE_OCR_FAIL_ONCE'] = '1'
        shutil.rmtree(_split_dir(big), ignore_errors=True)
        rbad = run_segment(case, '大卷宗.pdf')
        ck('分段：段失败 → ok=False 带错误', (not rbad['ok']) and bool(rbad.get('error')))
        rretry = run_segment(case, '大卷宗.pdf')
        ck('分段：重试同段 → 成功推进', rretry['ok'] and rretry['done_pages'] >= 30)
        os.environ.pop('FAKE_OCR_FAIL_ONCE', None)
        # 段未完整（checkpoint 未清，模拟崩/超时）→ 报未完成；重调自动续跑
        os.environ['FAKE_OCR_LEAVE_CKPT'] = '1'
        rinc = run_segment(case, '大卷宗.pdf', seg=2)
        ck('分段：段未完整（ckpt 未清）→ 报未完成',
           (not rinc['ok']) and '未完整' in (rinc.get('error') or ''))
        os.environ.pop('FAKE_OCR_LEAVE_CKPT', None)
        rinc2 = run_segment(case, '大卷宗.pdf', seg=2)
        ck('分段：续跑重调 → 段完成推进（60/100）',
           rinc2['ok'] and (not rinc2['done']) and rinc2['done_pages'] == 60)
        # 安全闸
        ck('分段：越界文件被拒', split_plan(case, out_side)['ok'] is False)
        ck('分段：不支持类型被拒', split_plan(case, '说明.txt')['ok'] is False)
        ck('分段：合成页段数学（7 页 / 3 页每段 → 3 段）',
           split_segments(7, 3) == [(1, 3), (4, 6), (7, 7)])

    # ---- 批 66：识别稿人工修字（save_text 安全闸 + 读回 + .bak）----
    ck('修字：越界文件被拒', save_text(case, out_side, 'x')['ok'] is False)
    ck('修字：未识别过的文件被拒（不凭空造文本）',
       save_text(case, '说明.txt', 'x')['ok'] is False)
    os.environ['FAKE_OCR_TEXT'] = '本院受理通知书 请到庭'
    run(case, '扫描件.pdf', force=True)
    FIXED = '修正后的文本：增值税发票 诉讼费缴费'
    st = save_text(case, '扫描件.pdf', FIXED)
    ck('修字：保存成功 + 旧版 .bak 留存',
       st['ok'] and st['chars'] == len(FIXED)
       and os.path.isfile(os.path.join(case, '08-过程文件', '_ocr', '扫描件.md.bak')))
    ck('修字：读回一致', read_text(case, '扫描件.pdf') == FIXED)
    ck('修字：空文本被拒', save_text(case, '扫描件.pdf', '   ')['ok'] is False)
    ck('修字：abs_path 公开解析可用', abs_path(case, '扫描件.pdf').endswith('扫描件.pdf'))

    # 清理
    for k in ('OCR_PYTHON', 'OCR_SCRIPT', 'FAKE_OCR_COUNTER', 'FAKE_OCR_TEXT',
              'OCR_CONTENT_CACHE_DIR', 'OCR_SPLIT_DIR', 'FAKE_OCR_FAIL_ONCE',
              'FAKE_OCR_TOTAL_PAGES', 'FAKE_OCR_LEAVE_CKPT', 'FAKE_OCR_LEAVE_NO_CKPT'):
        os.environ.pop(k, None)
    shutil.rmtree(TMP, ignore_errors=True)
    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not fail else '有失败：' + '; '.join(fail), ok + len(fail), len(fail)))
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description='收件箱文件本地 OCR v%s（零外发）' % VERSION)
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--plan', nargs=2, metavar=('CASE_DIR', 'NAME'))
    ap.add_argument('--run', nargs=2, metavar=('CASE_DIR', 'NAME'))
    ap.add_argument('--split-plan', nargs=2, metavar=('CASE_DIR', 'NAME'),
                    help='批 65：大 PDF 分段识别计划（只读）')
    ap.add_argument('--split-run', nargs=2, metavar=('CASE_DIR', 'NAME'),
                    help='批 65：跑一个段（默认从续跑起点；--seg 指定段号）')
    ap.add_argument('--seg', type=int, help='分段号（1-based）')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.plan:
        print(plan(a.plan[0], a.plan[1]))
        return 0
    if a.run:
        r = run(a.run[0], a.run[1], a.force)
        if not r['ok']:
            print('失败：%s' % r['error'])
            return 1
        sg = suggest(a.run[0], a.run[1], r['text'])
        print('识别 %d 字（%s，%.1f 秒）→ %s' % (r['chars'], '缓存' if r['cached'] else '新跑',
                                              r['seconds'], r['path']))
        print('建议：%s（%s）' % (sg['dir'] or '仍待人工', sg['reason']))
        print('--- 正文前 400 字 ---')
        print(r['text'][:400])
        return 0
    if a.split_plan:
        r = split_plan(a.split_plan[0], a.split_plan[1])
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get('ok') else 1
    if a.split_run:
        r = run_segment(a.split_run[0], a.split_run[1], seg=a.seg, force=a.force)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get('ok') else 1
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
