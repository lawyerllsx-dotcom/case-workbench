# -*- coding: utf-8 -*-
"""阶段 5 批量修复器（migration_scan 的配套执行端）

批 1  inbox    : 补 `00-收件箱`（+ 执行类案件补 `09-财产查控`/`10-处置分配`）+ `intake-log.md`
批 1b pipeline : 补 `pipeline/` 三件套（P/R/E 脚手架，调 new_case_pipeline.py）
批 2  rename   : 按清单改名（**默认不出手**，需 --only 指定案件名）
批 3  scatter  : 顶层散件盒归位（**默认不出手**，需 --only 指定文件名）

纪律：默认 dry-run；`--apply` 才落盘；只新增、不移动、不删除、不改名（批 2/3 未启用）。
用法：
    python migration_apply.py --batch inbox            # 预览
    python migration_apply.py --batch inbox --apply    # 执行
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import case_ops  # noqa: E402

BASE = case_ops.case_root()
PIPE_SCRIPT = r'D:\dev-tools\new_case_pipeline.py'


def case_dirs():
    if not os.path.isdir(BASE):
        return []
    return sorted(d for d in os.listdir(BASE)
                  if os.path.isdir(os.path.join(BASE, d))
                  and not d.startswith(('_', '.', '~')))


def is_exec(name):
    return '执行' in name or '执行文件夹' in name


def _guard_trace(label, trace_fn, moves):
    """留痕守卫：留痕失败即**回滚本次全部移动**，不留半成品。

    moves = [(源绝对路径, 目标绝对路径), ...]（按实际移动顺序）
    返回 (ok, note)。2026-09-12 批 5 实踩：「先移动、后写留痕」时留痕抛异常
    → 文件已移动但无记录；此后一律走本守卫。
    """
    try:
        trace_fn()
        return True, ''
    except Exception as e:                                  # noqa: BLE001
        rolled, failed = 0, 0
        for sp, dp in reversed(moves):
            try:
                if os.path.exists(dp) and not os.path.exists(sp):
                    os.makedirs(os.path.dirname(sp), exist_ok=True)
                    shutil.move(dp, sp)
                    rolled += 1
                else:
                    failed += 1
            except Exception:                               # noqa: BLE001
                failed += 1
        return False, ('%s 留痕失败(%s) → 已回滚 %d/%d 项%s；**本次动作视为未生效**'
                       % (label, repr(e)[:80], rolled, len(moves),
                          '' if failed == 0 else '，另有 %d 项需人工核对' % failed))


# ── 批 3：顶层散件盒归位（人工核定映射；源=散件盒，目标=A个案 内案件夹子目录）──
SCATTER_BOXES = ['介绍信', '所函', '判决书', '接收到的文件']
SCATTER_MAP = [
    # (散件盒, 源名, 是否目录, 目标案件夹[相对 A个案], 目标子目录, 判定依据)
    ('介绍信', '介绍信-海大饲料诉周志伟等.docx', False, r'A海大\福州海大饲料VS周志伟等人',
     '01-委托手续', '同案已存「周志伟所函.pdf/法律事务委托合同.pdf」，台账 海大r13'),
    ('介绍信', '周志伟', True, r'A海大\福州海大饲料VS周志伟等人',
     '01-委托手续', '子夹名=当事人名，扫描件与介绍信同批；台账 海大r13'),
    ('所函', '廖永灿所函.pdf', False, r'A海大\福州海大vs廖永娣',
     '01-委托手续', '台账 海大r14「福州海大vs廖永灿、陈萍、廖永娣」'),
    ('接收到的文件', '福州海大vs廖永灿、陈萍、廖永娣民事裁定书.pdf', False, r'A海大\福州海大vs廖永娣',
     '04-法院文件', '同上案，裁定书属法院文件'),
    # ── 2026-09-12 批 4 追加：交叉命中定位到的归属 ──
    ('介绍信', r'吴小明\吴小明介绍信.pdf', False,
     r'民-A-厦门非凡世家系列案\民-26XXX-非凡世家-买卖合同系列案件', '吴小明',
     '系列案内已有「吴小明」子夹（含委托合同/律师函/欠条），该当事人属非凡世家系列'),
    ('介绍信', r'吴小明\吴小明等人户籍资料查询结果.pdf', False,
     r'民-A-厦门非凡世家系列案\民-26XXX-非凡世家-买卖合同系列案件', '吴小明',
     '同上；系列案夹内仅存一份同名（不同层级），归入当事人子夹'),
    ('所函', '淮安海龙所函.pdf', False, r'民-A-26013_淮安海龙饲料VS羿昌静等_买卖合同纠纷',
     '01-委托手续', '淮安海龙已有独立正式夹（缺 01-委托手续，本项为首次归位）'),
    ('判决书', '26-747-福州海大VS许锦虾等人一审民事判决书.pdf', False,
     r'A海大\福州海大vs许锦虾等人', '04-法院文件',
     '台账 海大r17 该案在办（已判决待强执）；A海大 内原无该案子夹，本项为首次建夹归位'),
]

# ── 批 5：散件盒收尾（第二批，2026-09-12 实读文件内容后核定）──
#    批 4 曾把下列 3 项误判为「通用模板」；本批**实读文件正文**后纠正为个案文书：
#      · `介绍信.docx` → 正文是「致钟山派出所 调取 张丽萍/黄树波/康鑫/张培阳/吴小明 户籍」
#        = 非凡世家系列案专用 → 系列案根（与「吴小明等人户籍资料查询结果.pdf」并列）
#      · `4.所函 - 副本.doc` → 正文「淮安海龙饲料有限公司 vs 羿昌静等 买卖合同纠纷（金湖县法院）」
#        → 台账 海大 r8 = 民-A-26013
#      · `4.所函.doc` → 正文「周锦全等 5 人 vs 骆纪川等 8 人 土地经营权出租合同（惠安县法院，强制执行）」
#        → **台账无此案、A个案 无此夹**，暂不移动（待爸爸定是否立卷）
SCATTER_MAP2 = [
    ('介绍信', '介绍信.docx', False, r'民-A-厦门非凡世家系列案', '',
     '正文=致钟山派出所调取张丽萍/黄树波/康鑫/张培阳/吴小明户籍（非凡世家系列）'),
    ('所函', '4.所函 - 副本.doc', False, r'民-A-26013_淮安海龙饲料VS羿昌静等_买卖合同纠纷',
     '01-委托手续', '正文=淮安海龙饲料VS羿昌静等（金湖县法院）；同批 淮安海龙所函.pdf 已归此处'),
]

# 批 5 自造备份残留：文件名模式（编辑文书时留下的 .bak-* 回滚点，非案件材料）
BAK_SUFFIX_PAT = ('.bak-',)

# 批 5「非经手件」归拢（2026-09-12 爸爸拍板：只代盖章、不经手的案件不必立卷，单独一个板块）
#   源=散件盒；目标=同盘散件盒内子夹（相对 `D:\案件\安清惠`）
HOLD_MAP = [
    # (散件盒, 源名, 目标子夹, 依据)
    ('所函', '4.所函.doc', '_代盖章（非经手案）',
     '正文=周锦全等5人 vs 骆纪川等8人 土地经营权出租合同（惠安县法院·强制执行）；非经手案，只代盖章'),
    ('所函', '周锦全等土地经营权出租合同所函.pdf', '_代盖章（非经手案）',
     '同上案的扫描盖章件'),
]


# ── 清理批：把「确认无疑的自造残留」移出案件目录（**移动，不是删除**；可一条命令回退）──
#    落点：D:\备份\01-清理前打包\案件目录清理-YYYYMMDD\<原相对路径>（保结构 + manifest）
CLEAN_ROOT = r'D:\备份\01-清理前打包'
CLEAN_LIST = [
    # (相对 A个案 的路径, 类别, 依据)
    (r'民-A-黎林培VS安徽速煦供应链\证据材料\_test_last.png', '测试残留', 'OCR 批量测试输出图'),
    (r'民-A-黎林培VS安徽速煦供应链\证据材料\_test_p1.png', '测试残留', 'OCR 批量测试输出图'),
    (r'民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷\需盖章文件\_TEST_TABLE_INSERT.docx',
     '测试残留', '表格插入功能验证文档'),
    (r'A海大\5.6「参考」民-26005-福建海大VS林健荣-买卖合同\林健荣案件需盖章的资料\需盖章文件\8、民事起诉状-福建海大饲料VS林健荣等人_converted.docx',
     '转换残留', 'html-to-docx 中间产物，原件 .doc 仍在'),
    (r'A海大\5.6揭阳海大vs林癸涵\需盖章文件\4、所函_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6揭阳海大vs林癸涵\需盖章文件\8、民事起诉状（林癸涵）_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6福州海大vs陈嘉豪\需盖章文件\1、法律事务委托合同及补充协议_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6福州海大vs陈嘉豪\需盖章文件\2、授权委托书-审判_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6福州海大vs陈嘉豪\需盖章文件\3、法定代表人身份证明书_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6福州海大vs陈嘉豪\需盖章文件\4、所函_converted.docx', '转换残留', '同上'),
    (r'A海大\5.6福建海大VS林国伟\林国伟案件需盖章的资料\需盖章文件\8、民事起诉状-福建海大饲料VS林国伟等人_converted.docx',
     '转换残留', '同上'),
    (r'A海大\三明海大\需盖章文件\4、所函_converted.docx', '转换残留', '同上'),
    (r'民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷\参考——民-A-26013-江苏当代国安律师事务所委托-淮安海龙饲料有限公司与羿昌静、高梦、梁建军、梁伟-买卖合同\1、民事起诉状-淮安海龙饲料有限公司VS羿昌静等人买卖合同纠纷_converted.docx',
     '转换残留', '同上'),
    (r'民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷\参考——民-A-26013-江苏当代国安律师事务所委托-淮安海龙饲料有限公司与羿昌静、高梦、梁建军、梁伟-买卖合同\2、证据材料清单-淮安海龙饲料有限公司VS羿昌静等人买卖合同纠纷_converted.docx',
     '转换残留', '同上'),
    (r'民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷\参考——民-A-26013-江苏当代国安律师事务所委托-淮安海龙饲料有限公司与羿昌静、高梦、梁建军、梁伟-买卖合同\3、财产保全申请书-淮安海龙饲料有限公司VS羿昌静等人买卖合同纠纷_converted.docx',
     '转换残留', '同上'),
    (r'民-A-郑金平VS郑志典等\所函-第二次-郑金平等人_converted.docx', '转换残留', '同上'),
    (r'A海大\民-26003-福州海大饲料有限公司诉朱泽洪、黄敏胜-买卖合同纠纷\需盖章文件\4.所函_converted.docx',
     '转换残留', '同上'),
    (r'民-A-厦门市懋宇工贸VS卢治斌_买卖合同纠纷\起诉材料\1、民事起诉状-厦门懋宇工贸有限公司VS卢治斌-买卖合同纠纷_converted.docxx_converted.docx',
     '转换残留', '双重转换产物（2.5MB）'),
    (r'民-A-厦门市懋宇工贸VS卢治斌_买卖合同纠纷\起诉材料\2、证据清单-厦门懋宇工贸有限公司VS卢治斌-买卖合同纠纷_converted.docx',
     '转换残留', '同上'),
    (r'民-A-厦门市懋宇工贸VS卢治斌_买卖合同纠纷\起诉材料\3、财产保全申请书-厦门懋宇工贸有限公司VS卢治斌-买卖合同纠纷_converted.docx',
     '转换残留', '同上'),
    (r'执-顾永珍VS杨扬、陈礼斌_民间借贷纠纷\需盖章文件\修改-强制执行申请书（传统格式）_20260609_173208_fromDocx.md',
     '转换残留', 'docx→md 中间产物'),
    (r'民-A-26010_陈丽萍VS李喜前、王署心_执行异议\需盖章文件\陈丽萍执行异议证据目录_converted.docx',
     '转换残留', '同上'),
    (r'国际\ocr_temp', '临时目录', 'OCR 中间图+文本（正文 pdf 仍在）'),
]

# ── 批 2：案件夹改名（人工核定；2026-09-12 爸爸拍板：编号「有多少用多少，没有就省编号段」，
#    执行 `执-`、仲裁 `仲-`、诉讼 `民-A-`，A海大 容器保留原名）──
#    格式：`[前缀[-编号]_]委托人VS对方[_案由]`
RENAME_MAP = [
    # (旧夹名, 新夹名, 依据)
    ('7.14郑金平', '民-A-郑金平VS郑志典等', '材料含「被告郑志典答辩状」；无编号、案由未确认'),
    ('7.3黎林培VS安徽速煦案件', '民-A-黎林培VS安徽速煦供应链', '律师函文件名「黎林培致安徽速煦供应链科技有限公司」；无编号'),
    ('保利云禧2026.9.1', '仲-厦门向上沟通VS厦门沧珩_服务合同纠纷', '台账 其他r15（类别=仲裁）；夹名与案件内容不符'),
    ('厦门启航优卓公司VS河北艾恩格燃气有限公司、郑福和-买卖合同纠纷案件-执行文件夹',
     '执-厦门启航优卓VS河北艾恩格、郑福和_买卖合同纠纷', '台账 其他r4（类别=执行）'),
    ('张前恩', '执-张前恩VS陈美娥_赠与合同纠纷', '台账 其他r12'),
    ('张文滨执行', '执-张文滨VS巫朝阳', '判决书文件名；案由未确认'),
    ('李雅玲', '仲-锦晟达VS李雅玲_劳动争议', '庭审材料「劳动仲裁申请书-锦晟达诉李雅玲」'),
    ('民-24585-漳州佳联化工有限公司VS漳州联胜超纤有限公司、盛月乔-买卖合同纠纷',
     '执-24585_漳州佳联化工VS漳州联胜超纤、盛月乔_买卖合同纠纷', '台账 其他r14（类别=执行）+ 实物编号 24585'),
    ('民-25XXX-厦门市懋宇工贸有限公司VS卢治斌-买卖合同纠纷',
     '民-A-厦门市懋宇工贸VS卢治斌_买卖合同纠纷', '实物编号是占位符 25XXX，按「没有就省编号段」处理'),
    ('民-A-26010-陈丽萍（罗洪昕）VS李喜前先生、王署心',
     '民-A-26010_陈丽萍VS李喜前、王署心_执行异议', '台账 其他r10（类别=执行异议）'),
    ('民-A-26016-江苏当代国安律师事务所委托-盐城海维VS曹琪立等人-买卖合同纠纷',
     '民-A-26016_盐城海维VS曹琪立等_买卖合同纠纷', '实物编号；去掉「江苏当代国安委托」冗余段'),
    ('民-A-26019盐城海维史奇元', '民-A-26019_盐城海维VS史奇元等_买卖合同纠纷', '实物编号；台账 海大r16'),
    ('淮安海龙饲料', '民-A-26013_淮安海龙饲料VS羿昌静等_买卖合同纠纷',
     '编号来自实物子夹名「参考——民-A-26013-…」；台账 海大r8'),
    ('陈龙珠', '民-A-陈龙珠、刘志华VS潮州艺千瓷业_买卖合同纠纷',
     '台账 其他r11（委托人=陈龙珠、刘志华）'),
    ('非凡世家', '民-A-厦门非凡世家系列案', '台账 其他r13（系列案，3 案）'),
    ('顾总珍-执行', '执-顾永珍VS杨扬、陈礼斌_民间借贷纠纷',
     '调解书文件名「原告顾永珍与被告杨扬、陈礼斌民间借贷纠纷」+ 台账 其他r9；⚠️ 夹名「顾总珍」疑笔误，待核'),
]


def batch_inbox(apply=False):
    plan, done = [], 0
    for d in case_dirs():
        full = os.path.join(BASE, d)
        has_inbox = os.path.isdir(os.path.join(full, '00-收件箱'))
        has_log = os.path.isfile(os.path.join(full, 'intake-log.md'))
        ex = is_exec(d)
        missing = []
        if not has_inbox:
            missing.append('00-收件箱')
        if not has_log:
            missing.append('intake-log.md')
        if ex:
            if not os.path.isdir(os.path.join(full, '09-财产查控')):
                missing.append('09-财产查控')
            if not os.path.isdir(os.path.join(full, '10-处置分配')):
                missing.append('10-处置分配')
        if not missing:
            continue
        rec = {'case': d, 'missing': missing, 'exec': ex, 'ok': None, 'note': ''}
        if apply:
            try:
                res = case_ops.ensure_inbox(full, exec_case=ex, apply=True)
                rec['ok'] = True
                rec['note'] = '新增 %d 项' % res.get('added', 0)
            except Exception as e:                      # noqa: BLE001
                rec['ok'] = False
                rec['note'] = repr(e)[:120]
        plan.append(rec)
        done += 1
    return plan


def batch_pipeline(apply=False):
    plan = []
    for d in case_dirs():
        full = os.path.join(BASE, d)
        if os.path.isdir(os.path.join(full, 'pipeline')):
            continue
        rec = {'case': d, 'target': os.path.join(d, 'pipeline'), 'ok': None, 'note': ''}
        if apply:
            try:
                o = subprocess.run(
                    [sys.executable, PIPE_SCRIPT, '--case-id', d, '--case-root', BASE,
                     '--skill', 'legal-workbench'],
                    capture_output=True, encoding='utf-8', errors='replace', timeout=60)
                rec['ok'] = o.returncode == 0 and os.path.isdir(os.path.join(full, 'pipeline'))
                rec['note'] = (o.stdout or o.stderr or '').strip().splitlines()[-1][:100] \
                    if (o.stdout or o.stderr) else ''
            except Exception as e:                      # noqa: BLE001
                rec['ok'] = False
                rec['note'] = repr(e)[:120]
        plan.append(rec)
    return plan


def batch_scatter(apply=False):
    """批 3：顶层散件盒 → 案件夹子目录。只移动，不删除、不改名（重名跳过）"""
    root = os.path.dirname(BASE)
    plan = []
    _moves = []
    for box, src, is_dir, dst_case, dst_sub, why in SCATTER_MAP:
        sp = os.path.join(root, box, src)
        dp_dir = os.path.join(BASE, dst_case, dst_sub)
        dp = os.path.join(dp_dir, os.path.basename(src))   # src 可能含子路径（如 吴小明\x.pdf）
        rec = {'box': box, 'src': src, 'dst': dp, 'why': why, 'ok': None, 'note': ''}
        if not os.path.exists(sp):
            rec['ok'] = False
            rec['note'] = '源不存在（可能已归位）'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '目标已存在同名 → 跳过不覆盖'
        elif not os.path.isdir(os.path.dirname(os.path.dirname(dp_dir))):
            rec['ok'] = False
            rec['note'] = '目标上级目录不存在'
        else:
            rec['ok'] = True
            rec['note'] = '待移动' if os.path.isdir(os.path.dirname(dp_dir)) \
                else '待移动（新建案件夹）'
            if apply:
                try:
                    os.makedirs(dp_dir, exist_ok=True)
                    shutil.move(sp, dp)
                    rec['note'] = '已移动'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)

    if apply:
        good, note = _guard_trace('散件归位', lambda: _scatter_trace(root, plan), _moves)
        if not good:
            for r in plan:
                if r['note'] == '已移动':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'box': '—', 'src': '留痕守卫', 'dst': '', 'why': note,
                         'ok': False, 'note': note})
    return plan


def _scatter_trace(root, plan):
    """留痕：① 目标案件夹 intake-log 追加行 ② 散件盒写 _迁出记录.md"""
    ok = [r for r in plan if r['ok'] and r['note'] == '已移动']
    if not ok:
        return
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    for r in ok:
        # 目标案件夹 = 相对 BASE 的第一段（兼容 dst_sub='' 直接落在案件夹根的情形）
        rel = os.path.relpath(r['dst'], BASE)
        case_dir = os.path.join(BASE, rel.split(os.sep)[0])
        log = os.path.join(case_dir, 'intake-log.md')
        head = not os.path.isfile(log)
        with open(log, 'a', encoding='utf-8') as f:
            if head:
                f.write('# intake-log — %s\n\n' % os.path.basename(case_dir))
                f.write('> 入库留痕：原名 ↔ 新名 ↔ 移动路径（Q3 折中制）。\n\n')
                f.write('| 时间 | 原名 | 新名 | 归入 | 方式 |\n|---|---|---|---|---|\n')
            f.write('| %s | %s | （未改名） | 散件盒归位 | %s → %s |\n'
                    % (stamp, r['src'], r['box'], os.path.relpath(r['dst'], BASE)))
    bybox = {}
    for r in ok:
        bybox.setdefault(r['box'], []).append(r)
    for box, rs in bybox.items():
        p = os.path.join(root, box, '_迁出记录.md')
        stamp_now = stamp
        new_rows = ['| %s | %s | `%s` | %s |' % (stamp_now, r['src'], os.path.relpath(r['dst'], BASE), r['why'])
                    for r in rs]
        old_rows = []
        if os.path.isfile(p):
            # **保留既有行**（追加式）：避免覆盖前一批（如 批 3/批 4）的迁出留痕
            for ln in open(p, encoding='utf-8').read().splitlines():
                if ln.startswith('|') and not ln.startswith('|---') and ' 时间 ' not in ln:
                    old_rows.append(ln)
        lines = ['# 散件盒迁出记录 — %s\n' % box,
                 '> 归位到案件夹，原件已移走；本表仅作索引，不重复存件。\n',
                 '| 时间 | 项 | 去向 | 依据 |', '|---|---|---|---|']
        lines += new_rows + old_rows
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')


def batch_hold(apply=False):
    """批 5：非经手案（只代盖章）文书归拢到散件盒子夹，不立卷、不进案件目录"""
    root = os.path.dirname(BASE)                      # D:\案件\安清惠
    plan = []
    _moves = []
    for box, src, dst_sub, why in HOLD_MAP:
        sp = os.path.join(root, box, src)
        dp_dir = os.path.join(root, box, dst_sub)
        dp = os.path.join(dp_dir, src)
        rec = {'box': box, 'src': src, 'dst': dp, 'why': why, 'ok': None, 'note': ''}
        if not os.path.exists(sp):
            rec['ok'] = False
            rec['note'] = '源不存在（可能已归拢）'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '目标已存在同名 → 跳过不覆盖'
        else:
            rec['ok'] = True
            rec['note'] = '待移动'
            if apply:
                try:
                    os.makedirs(dp_dir, exist_ok=True)
                    shutil.move(sp, dp)
                    rec['note'] = '已移动'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)

    def _hold_trace():
        ok = [r for r in plan if r['note'] == '已移动']
        if not ok:
            return
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        p = os.path.join(root, '所函', '_迁出记录.md')
        rows = ['| %s | %s | `%s` | %s |' % (stamp, r['src'],
                                             os.path.relpath(r['dst'], root), r['why']) for r in ok]
        old = []
        if os.path.isfile(p):
            for ln in open(p, encoding='utf-8').read().splitlines():
                if ln.startswith('|') and not ln.startswith('|---') and ' 时间 ' not in ln:
                    old.append(ln)
        lines = ['# 散件盒迁出记录 — 所函\n',
                 '> 归位到案件夹，原件已移走；本表仅作索引，不重复存件。\n',
                 '| 时间 | 项 | 去向 | 依据 |', '|---|---|---|---|'] + rows + old
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        readme = os.path.join(root, '所函', '_代盖章（非经手案）', 'README.md')
        if not os.path.isfile(readme):
            with open(readme, 'w', encoding='utf-8') as f:
                f.write('# _代盖章（非经手案）\n\n'
                        '> 2026-09-12 爸爸定：**只代带教盖章所函、不经手的案件**不立卷，\n'
                        '> 归拢至本板块，与经手案件（`A个案\\`）分开存放。\n\n'
                        '| 项 | 依据 |\n|---|---|\n')
        with open(readme, 'a', encoding='utf-8') as f:
            for r in ok:
                f.write('| `%s` | %s |\n' % (r['src'], r['why']))

    if apply:
        good, note = _guard_trace('非经手件归拢', _hold_trace, _moves)
        if not good:
            for r in plan:
                if r['note'] == '已移动':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'box': '—', 'src': '留痕守卫', 'dst': '', 'why': note,
                         'ok': False, 'note': note})
    return plan


def batch_scatter2(apply=False):
    """批 5：散件盒收尾（第二批）——复用批 3 的移动逻辑，映射表换成 SCATTER_MAP2"""
    global SCATTER_MAP
    keep, SCATTER_MAP = SCATTER_MAP, SCATTER_MAP2
    try:
        return batch_scatter(apply=apply)
    finally:
        SCATTER_MAP = keep


def batch_bak(apply=False):
    """批 5：把编辑文书留下的 `.bak-*` 回滚点**移出**案件目录（移动，不是删除；可回退）"""
    dst_root = os.path.join(CLEAN_ROOT, '案件目录清理-%s' % datetime.now().strftime('%Y%m%d'))
    found = []
    for dp, dn, fn in os.walk(BASE):
        for f in fn:
            if any(p in f for p in BAK_SUFFIX_PAT):
                found.append(os.path.relpath(os.path.join(dp, f), BASE))
    plan = []
    _moves = []
    for rel in sorted(found):
        sp = os.path.join(BASE, rel)
        dp = os.path.join(dst_root, rel)
        rec = {'rel': rel, 'kind': '自造备份', 'why': '编辑文书时的 .bak-* 回滚点', 'ok': None, 'note': ''}
        if not os.path.exists(sp):
            rec['ok'] = False
            rec['note'] = '源不存在'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '备份区已有同名 → 跳过'
        else:
            rec['ok'] = True
            rec['note'] = '待移出'
            if apply:
                try:
                    os.makedirs(os.path.dirname(dp), exist_ok=True)
                    shutil.move(sp, dp)
                    rec['note'] = '已移出'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)

    def _bak_trace():
        ok = [r for r in plan if r['note'] == '已移出']
        if not ok:
            return
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        p = os.path.join(dst_root, '_manifest-bak.md')
        mode, head = ('a', False) if os.path.isfile(p) else ('w', True)
        with open(p, mode, encoding='utf-8') as f:
            if head:
                f.write('# 案件目录清理清单 —— 自造 `.bak-*` 回滚点\n\n'
                        '> 编辑文书时留下的回滚点。**移出，不是删除**。\n'
                        '> 回退：把备份区文件按原相对路径移回即可。\n')
            f.write('\n## %s（%d 项）\n\n| # | 原路径（相对 A个案） | 依据 |\n|---|---|---|\n'
                    % (stamp, len(ok)))
            for i, r in enumerate(ok, 1):
                f.write('| %d | `%s` | %s |\n' % (i, r['rel'], r['why']))

    if apply:
        good, note = _guard_trace('自造 .bak 移出', _bak_trace, _moves)
        if not good:
            for r in plan:
                if r['note'] == '已移出':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'rel': '留痕守卫', 'kind': '—', 'why': note, 'ok': False, 'note': note})
    return plan


def batch_rename(apply=False):
    """批 2：顶层案件夹改名（同盘 os.rename；目标已存在 → 跳过不覆盖）"""
    plan = []
    _moves = []
    for old, new, why in RENAME_MAP:
        sp, dp = os.path.join(BASE, old), os.path.join(BASE, new)
        rec = {'old': old, 'new': new, 'why': why, 'ok': None, 'note': ''}
        if not os.path.isdir(sp):
            rec['ok'] = False
            rec['note'] = '源不存在'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '目标已存在 → 跳过不覆盖'
        else:
            rec['ok'] = True
            rec['note'] = '待改名'
            if apply:
                try:
                    os.rename(sp, dp)
                    rec['note'] = '已改名'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)
    if apply:
        good, note = _guard_trace('案件夹改名', lambda: _rename_trace(plan), _moves)
        if not good:
            for r in plan:
                if r['note'] == '已改名':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'old': '留痕守卫', 'new': '', 'why': note, 'ok': False, 'note': note})
    return plan


def _rename_trace(plan):
    """留痕：A个案 根 `_案件夹改名对照表.md` 累积追加（不改历史行）"""
    ok = [r for r in plan if r['ok'] and r['note'] == '已改名']
    if not ok:
        return
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    p = os.path.join(BASE, '_案件夹改名对照表.md')
    head = not os.path.isfile(p)
    with open(p, 'a', encoding='utf-8') as f:
        if head:
            f.write('# 案件夹改名对照表（旧名 → 新名）\n\n'
                    '> 由 `case_workbench\\migration_apply.py --batch rename` 自动追加，**只追加不删行**。\n'
                    '> 查老路径时按本表反查；台账 B 列案号不受影响。\n\n'
                    '| 时间 | 旧夹名 | 新夹名 | 依据 |\n|---|---|---|---|\n')
        for r in ok:
            f.write('| %s | %s | %s | %s |\n' % (stamp, r['old'], r['new'], r['why']))


def batch_clean(apply=False):
    """清理批：把自造残留**移出**案件目录到备份区（可逆；不是删除）"""
    dst_root = os.path.join(CLEAN_ROOT, '案件目录清理-%s' % datetime.now().strftime('%Y%m%d'))
    plan = []
    _moves = []
    for rel, kind, why in CLEAN_LIST:
        sp = os.path.join(BASE, rel)
        dp = os.path.join(dst_root, rel)
        rec = {'rel': rel, 'kind': kind, 'why': why, 'ok': None, 'note': ''}
        if not os.path.exists(sp):
            rec['ok'] = False
            rec['note'] = '源不存在（可能已清理）'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '备份区已有同名 → 跳过'
        else:
            rec['ok'] = True
            rec['note'] = '待移出'
            if apply:
                try:
                    os.makedirs(os.path.dirname(dp), exist_ok=True)
                    shutil.move(sp, dp)
                    rec['note'] = '已移出'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)

    def _clean_trace():
        ok = [r for r in plan if r['note'] == '已移出']
        if not ok:
            return
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        p = os.path.join(dst_root, '_manifest.md')
        mode, head = ('a', False) if os.path.isfile(p) else ('w', True)
        with open(p, mode, encoding='utf-8') as f:
            if head:
                f.write('# 案件目录清理清单（自造残留移出）\n\n'
                        '> **这些文件是移出，不是删除**。\n'
                        '> 回退：把下表「备份区路径」的文件移回「原路径」即可（结构已保留）。\n')
            f.write('\n## %s（%d 项）\n\n'
                    '| # | 类别 | 原路径（相对 A个案） | 依据 |\n|---|---|---|---|\n' % (stamp, len(ok)))
            for i, r in enumerate(ok, 1):
                f.write('| %d | %s | `%s` | %s |\n' % (i, r['kind'], r['rel'], r['why']))

    if apply:
        good, note = _guard_trace('自造残留移出', _clean_trace, _moves)
        if not good:
            for r in plan:
                if r['note'] == '已移出':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'rel': '留痕守卫', 'kind': '—', 'why': note, 'ok': False, 'note': note})
    return plan


# ── 批 6：错放件（内容属另一案件）移出 ──
MISPLACED_LIST = [
    # (相对 A个案 的路径, 判定, 依据)
    (r'A海大\5.6福建海大VS林国伟\林国伟案件需盖章的资料\需盖章文件.zip', '错放重复',
     'md5 b0f161a5… 与 林健荣案同文件完全相同：内含「民事起诉状-福建海大饲料VS林健荣等人」'
     '「财产保全申请书-福建海大VS林健荣等人」等 12 件、mtime 2026-02-04；'
     '林健荣案夹内已有同 md5 正本 → 本件系复制未替换，不是林国伟材料'),
]

# ── 批 6：`A海大` 容器内子夹命名（**保留日期前缀**，2026-09-12 爸爸定：前缀=拿到材料的日期）──
#    格式：`[日期前缀]当事人VS对方[等]_案由`；容器内已知属诉讼，省 `民-A-` 类别段。
#    ⚠️ 台账无记录的 3 项（林国伟 / 南通海大 / 霍山）另行确认后方可执行。
RENAME_MAP_HAIDA = [
    # (旧夹名[相对 A个案], 新夹名, 依据)
    (r'A海大\5.6揭阳海大vs林癸涵', r'A海大\5.6揭阳海大VS林癸涵等_买卖合同纠纷', '台账 海大r9'),
    (r'A海大\5.6福州海大vs陈嘉豪', r'A海大\5.6福州海大VS陈嘉豪等_买卖合同纠纷', '台账 海大r10'),
    (r'A海大\6.5福州海大vs许明竹', r'A海大\6.5福州海大VS许明竹等_买卖合同纠纷', '台账 海大r11'),
    (r'A海大\6.5福建海大vs新余钦', r'A海大\6.5福建海大VS新余钦丰祥等_买卖合同纠纷', '台账 海大r12'),
    (r'A海大\揭阳海大vs信源', r'A海大\揭阳海大VS惠州信源_买卖合同纠纷', '台账 海大r5'),
    (r'A海大\三明海大', r'A海大\三明海大VS李兴旺等_股东损害公司债权人利益责任纠纷',
     '⚠️ 台账 海大r6；案由经起诉状实读更正为「股东损害公司债权人利益责任纠纷」（非买卖合同）；'
     '六被告=彩源合作社成员 李兴旺/王继昌/王继熙/郑再留/张作镕/陈振'),
    (r'A海大\民-26003-福州海大饲料有限公司诉朱泽洪、黄敏胜-买卖合同纠纷',
     r'A海大\民-26003_福州海大VS朱泽洪、黄敏胜_买卖合同纠纷', '台账 海大r7；统一 VS/下划线'),
    (r'A海大\福州海大vs廖永娣', r'A海大\福州海大VS廖永灿等_买卖合同纠纷', '台账 海大r14'),
    (r'A海大\福州海大vs许锦虾等人', r'A海大\福州海大VS许锦虾等_买卖合同纠纷', '台账 海大r17'),
    (r'A海大\福州海大饲料VS周志伟等人', r'A海大\福州海大VS周志伟等_买卖合同纠纷', '台账 海大r13'),
    # ⚠️ 以下 3 项台账无记录（需爸爸确认后再执行）：
    (r'A海大\5.6福建海大VS林国伟', r'A海大\5.6福建海大VS林国伟等_买卖合同纠纷',
     '⚠️ 台账 海大表无此行（参考案 r4 是林健荣）；实读起诉状 = 原告福建海大饲料，'
     '被告 林国伟/王惠华/林伟玲/沈志清，起诉准备阶段；台账是否补行待爸爸定'),
    (r'A海大\南通海大', r'A海大\南通海大VS孙迎柱等_买卖合同纠纷', '⚠️ 属律师函线（律师函r4），阶段未定'),
    (r'A海大\福州海大vs霍山', r'A海大\福州海大VS霍山县盛塘水产等_买卖合同纠纷',
     '⚠️ 属律师函线（律师函r7），阶段未定'),
]


def batch_misplaced(apply=False):
    """批 6：把「内容属另一案件」的错放件移出案件目录（移动，不是删除）"""
    dst_root = os.path.join(CLEAN_ROOT, '案件目录清理-%s' % datetime.now().strftime('%Y%m%d'))
    plan = []
    _moves = []
    for rel, kind, why in MISPLACED_LIST:
        sp = os.path.join(BASE, rel)
        dp = os.path.join(dst_root, rel)
        rec = {'rel': rel, 'kind': kind, 'why': why, 'ok': None, 'note': ''}
        if not os.path.exists(sp):
            rec['ok'] = False
            rec['note'] = '源不存在（可能已移出）'
        elif os.path.exists(dp):
            rec['ok'] = False
            rec['note'] = '备份区已有同名 → 跳过'
        else:
            rec['ok'] = True
            rec['note'] = '待移出'
            if apply:
                try:
                    os.makedirs(os.path.dirname(dp), exist_ok=True)
                    shutil.move(sp, dp)
                    rec['note'] = '已移出'
                    _moves.append((sp, dp))
                except Exception as e:                  # noqa: BLE001
                    rec['ok'] = False
                    rec['note'] = repr(e)[:120]
        plan.append(rec)

    def _trace():
        ok = [r for r in plan if r['note'] == '已移出']
        if not ok:
            return
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        p = os.path.join(dst_root, '_manifest-misplaced.md')
        mode, head = ('a', False) if os.path.isfile(p) else ('w', True)
        with open(p, mode, encoding='utf-8') as f:
            if head:
                f.write('# 错放件移出清单（内容属另一案件）\n\n'
                        '> **移出，不是删除**；回退 = 按原相对路径移回。\n')
            f.write('\n## %s（%d 项）\n\n| # | 判定 | 原路径（相对 A个案） | 依据 |\n|---|---|---|---|\n'
                    % (stamp, len(ok)))
            for i, r in enumerate(ok, 1):
                f.write('| %d | %s | `%s` | %s |\n' % (i, r['kind'], r['rel'], r['why']))

    if apply:
        good, note = _guard_trace('错放件移出', _trace, _moves)
        if not good:
            for r in plan:
                if r['note'] == '已移出':
                    r['note'] = '已回滚（留痕失败）'
            plan.append({'rel': '留痕守卫', 'kind': '—', 'why': note, 'ok': False, 'note': note})
    return plan


def batch_rename2(apply=False):
    """批 6：`A海大` 容器内子夹命名（保留日期前缀）——复用批 2 改名逻辑"""
    global RENAME_MAP
    keep, RENAME_MAP = RENAME_MAP, RENAME_MAP_HAIDA
    try:
        return batch_rename(apply=apply)
    finally:
        RENAME_MAP = keep


def selftest():
    """回归用例（临时目录，不触碰真实案件）：
    ① 归位落到**案件夹**（不误落 A个案 根）② `_迁出记录` 保留式追加（旧行不丢）
    ③ 留痕失败 → **自动回滚**、源文件复位、不留半成品
    """
    import tempfile
    checks = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    if os.name == 'nt':
        try:
            # 用 reconfigure（而非新建 TextIOWrapper → 旧 wrapper 被 GC 时会关掉底层 buffer）
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:                                     # noqa: BLE001
            pass

    global BASE, SCATTER_MAP
    keep_base, keep_map = BASE, SCATTER_MAP
    saved_trace = globals()['_scatter_trace']
    tmp = tempfile.mkdtemp(prefix='mab_selftest_')
    try:
        case_root = os.path.join(tmp, 'A个案')
        box = os.path.join(tmp, '介绍信')
        os.makedirs(os.path.join(case_root, '案件甲'))
        os.makedirs(box)
        log = os.path.join(case_root, '案件甲', 'intake-log.md')
        io.open(log, 'w', encoding='utf-8').write(
            '# intake-log — 案件甲\n\n| 时间 | 原名 | 新名 | 归入 | 方式 |\n|---|---|---|---|---|\n'
            '| 旧 | old.pdf | （未改名） | 散件盒归位 | x → y |\n')
        rec_p = os.path.join(box, '_迁出记录.md')
        io.open(rec_p, 'w', encoding='utf-8').write(
            '# 散件盒迁出记录 — 介绍信\n\n| 时间 | 项 | 去向 | 依据 |\n|---|---|---|---|\n'
            '| 旧 | old.pdf | `P` | 旧依据 |\n')
        io.open(os.path.join(box, 'a.docx'), 'w', encoding='utf-8').write('A')

        BASE, SCATTER_MAP = case_root, [('介绍信', 'a.docx', False, '案件甲', '', '测试')]
        batch_scatter(apply=True)
        ck('归位到案件夹', os.path.isfile(os.path.join(case_root, '案件甲', 'a.docx')))
        ck('intake-log 落到案件夹（含新行）', 'a.docx' in io.open(log, encoding='utf-8').read())
        ck('未误落 A个案 根', not os.path.isfile(os.path.join(case_root, 'intake-log.md')))
        r = io.open(rec_p, encoding='utf-8').read()
        ck('_迁出记录 保留旧行', 'old.pdf' in r)
        ck('_迁出记录 追加新行', 'a.docx' in r)

        # 留痕失败 → 回滚
        io.open(os.path.join(box, 'b.docx'), 'w', encoding='utf-8').write('B')
        SCATTER_MAP = [('介绍信', 'b.docx', False, '案件甲', '', '测试2')]

        def boom(root, plan):
            raise RuntimeError('留痕故意失败')
        globals()['_scatter_trace'] = boom
        p2 = batch_scatter(apply=True)
        ck('回滚：源文件复位', os.path.isfile(os.path.join(box, 'b.docx')))
        ck('回滚：目标不存在', not os.path.exists(os.path.join(case_root, '案件甲', 'b.docx')))
        ck('回滚：plan 标记为已回滚', any('已回滚' in x['note'] for x in p2))

        # 守卫单元用例
        s, d = os.path.join(tmp, 's.txt'), os.path.join(tmp, 'd.txt')
        io.open(s, 'w', encoding='utf-8').write('x')
        import shutil as _sh
        _sh.move(s, d)
        good, note = _guard_trace('T', lambda: 1 / 0, [(s, d)])
        ck('守卫：失败判定', good is False)
        ck('守卫：已回滚', os.path.isfile(s) and not os.path.exists(d))
    finally:
        globals()['_scatter_trace'] = saved_trace
        BASE, SCATTER_MAP = keep_base, keep_map
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    bad = [n for n, okv in checks if not okv]
    for n, okv in checks:
        print('[%s] %s' % ('OK' if okv else 'FAIL', n))
    print('selftest: %d/%d 通过' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', required=True,
                    choices=['inbox', 'pipeline', 'scatter', 'scatter2', 'hold', 'rename',
                             'rename2', 'clean', 'bak', 'misplaced', 'selftest'])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    if args.batch == 'selftest':
        sys.exit(selftest())

    fn = {'inbox': batch_inbox, 'pipeline': batch_pipeline, 'scatter': batch_scatter,
          'scatter2': batch_scatter2, 'hold': batch_hold, 'rename': batch_rename,
          'rename2': batch_rename2, 'clean': batch_clean, 'bak': batch_bak,
          'misplaced': batch_misplaced}[args.batch]
    plan = fn(args.apply)
    tag = '执行' if args.apply else 'dry-run'
    print('[%s] 批 %s：待处理 %d 项' % (tag, args.batch, len(plan)))
    for r in plan:
        if args.batch == 'inbox':
            print('  - %-52s 缺: %s%s' % (r['case'][:52], '/'.join(r['missing']),
                                          '' if r['ok'] is None else
                                          ('  → %s' % ('OK' if r['ok'] else 'FAIL ' + r['note']))))
        elif args.batch == 'pipeline':
            print('  - %-52s → pipeline/%s' % (r['case'][:52],
                                               '' if r['ok'] is None else
                                               ('OK' if r['ok'] else 'FAIL ' + r['note'])))
        elif args.batch in ('rename', 'rename2'):
            print('  - %s\n      → %s   [%s]' % (r['old'][:60], r['new'][:60], r['note']))
        elif args.batch in ('clean', 'bak', 'misplaced'):
            print('  - [%-4s] %s   [%s]' % (r['kind'][:4], r['rel'][:78], r['note']))
        else:
            print('  - [%s] %-40s → %s  (%s)' % (r['box'], r['src'][:40],
                                                 os.path.relpath(r['dst'], BASE),
                                                 r['note']))
    if args.apply:
        bad = [r for r in plan if r['ok'] is False]
        print('结果：成功 %d / 失败 %d' % (len(plan) - len(bad), len(bad)))


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
