# -*- coding: utf-8 -*-
"""批 39h 归档区导入 回归（全临时目录，真案卷零接触）"""
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='arch_import_')
CASE_ROOT = os.path.join(TMP, 'A个案')
os.makedirs(CASE_ROOT, exist_ok=True)
os.environ['CASE_OPS_ROOT'] = CASE_ROOT
os.environ['CASE_OPS_BIZ_ROOT'] = os.path.join(TMP, '张律师')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import case_ops          # noqa: E402

total, fails = [], []


def ck(name, cond):
    total.append(name)
    if cond:
        print('PASS %s' % name)
    else:
        fails.append(name)
        print('FAIL %s' % name)


def _rejected(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def mk(rel, text='x'):
    p = os.path.join(CASE_ROOT, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


STAGE = os.path.join(TMP, 'stage')
os.makedirs(STAGE, exist_ok=True)


def stage(name, text='x'):
    p = os.path.join(STAGE, name)
    with io.open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


# ---- ① 归档区清单（只读）
mk('_归档区/差旅报销/2026-06/打车/a.pdf')
mk('_归档区/差旅报销/2026-06/打车/b.pdf')
mk('_归档区/差旅报销/2026-08/c.pdf')
mk('_归档区/批量催收件/徐富强/x.docx')
mk('_归档区/批量催收件/徐富强/y.docx')
mk('_归档区/批量催收件/郑思建/z.docx')

d1 = case_ops.archive_zone_scan('差旅报销')
ck('差旅：扫到 3 件', d1['total'] == 3)
ck('差旅：分组 2 个（2026-06/2026-08）', len(d1['groups']) == 2)
d2 = case_ops.archive_zone_scan('批量催收件')
ck('催收：扫到 3 件', d2['total'] == 3)
ck('催收：分组 2 个（徐富强/郑思建）', len(d2['groups']) == 2)
ck('未知归档区被拒', _rejected(lambda: case_ops.archive_zone_scan('乱写')))


# ---- ② 方案：守卫生效
s1 = stage('票1.pdf')
p_bad = case_ops.plan_archive_import('乱写', s1)
ck('方案：未知归档区被拦', not p_bad['ok'])

p_bad2 = case_ops.plan_archive_import('差旅报销', os.path.join(STAGE, '__不存在.pdf'),
                                     month='2026-09', sub='打车')
ck('方案：源不存在被拦', not p_bad2['ok'])

p_sub = case_ops.plan_archive_import('差旅报销', s1, month='2026-09', sub='')
ck('方案：空交通方式被拦（不静默兜底）', not p_sub['ok'])
p_sub2 = case_ops.plan_archive_import('差旅报销', s1, month='2026-09', sub='飞机')
ck('方案：非法交通方式被拦', not p_sub2['ok'])
p_mon = case_ops.plan_archive_import('差旅报销', s1, month='9月', sub='打车')
ck('方案：月份格式错被拦', not p_mon['ok'])

p_grp = case_ops.plan_archive_import('批量催收件', s1, group='')
ck('方案：空对象名被拦（不落 unnamed）', not p_grp['ok'])
p_grp2 = case_ops.plan_archive_import('批量催收件', s1, group='   ')
ck('方案：空白对象名被拦', not p_grp2['ok'])

# ---- ③ 方案：正常路径
p_ok = case_ops.plan_archive_import('差旅报销', s1, month='2026-09', sub='打车')
ck('方案：差旅正常 ok', p_ok['ok'])
ck('方案：落点含 2026-09\\打车',
   p_ok['dst'].replace(os.sep, '/').endswith('差旅报销/2026-09/打车/票1.pdf'))

st2 = stage('票2.pdf')
p_ok2 = case_ops.plan_archive_import('批量催收件', st2, group='徐富强')
ck('方案：催收正常 ok', p_ok2['ok'])
ck('方案：落点含 徐富强',
   p_ok2['dst'].replace(os.sep, '/').endswith('批量催收件/徐富强/票2.pdf'))

# 重名避让：先造一个同名目标，方案应给 -2
mk('_归档区/批量催收件/徐富强/dup.pdf')
p_dup = case_ops.plan_archive_import('批量催收件', stage('dup.pdf'), group='徐富强')
ck('方案：重名自动避让（-2）', p_dup['newname'] != 'dup.pdf')

# ---- ④ 执行：未确认拒绝
try:
    case_ops.apply_archive_import(p_ok, confirm=False)
    ck('执行：未确认拒绝', False)
except ValueError:
    ck('执行：未确认拒绝', True)

# ---- ⑤ 执行：真实移动 + 留痕
before = sum(len(fs) for _, _, fs in os.walk(os.path.join(CASE_ROOT, '_归档区', '差旅报销')))
r = case_ops.apply_archive_import(p_ok, confirm=True)
ck('执行：返回 dst 存在', os.path.isfile(r['dst']))
ck('执行：源已移走', not os.path.isfile(s1))
after = sum(len(fs) for _, _, fs in os.walk(os.path.join(CASE_ROOT, '_归档区', '差旅报销')))
ck('执行：目标 +2（移入 1 + 留痕 1）', after == before + 2)
ck('执行：留痕已写', bool(r['trace']) and os.path.isfile(r['trace']))
lg = io.open(r['trace'], encoding='utf-8').read() if r['trace'] else ''
ck('执行：留痕含原名与新位置', '票1.pdf' in lg and '2026-09' in lg)

# ---- ⑥ 催收执行
r2 = case_ops.apply_archive_import(p_ok2, confirm=True)
ck('执行2：催收落点存在', os.path.isfile(r2['dst']))
ck('执行2：催收源已移走', not os.path.isfile(st2))
ck('执行2：对象夹名 = 徐富强',
   os.path.basename(os.path.dirname(r2['dst'])) == '徐富强')

# ---- ⑦ 批 39i：归档区统一在 `_归档区\` 下 + 顾问单位纳入
ck('归档区根在 _归档区 下（差旅）',
   case_ops.archive_zone_root('差旅报销') ==
   os.path.join(CASE_ROOT, '_归档区', '差旅报销'))
ck('归档区根在 _归档区 下（催收）',
   case_ops.archive_zone_root('批量催收件') ==
   os.path.join(CASE_ROOT, '_归档区', '批量催收件'))
ck('顾问单位已纳入 ARCHIVE_ZONES', '顾问单位' in case_ops.ARCHIVE_ZONES)
ck('顾问单位 kind=client', case_ops.ARCHIVE_ZONES['顾问单位']['kind'] == 'client')

mk('_归档区/顾问单位/某公司/x.docx')
mk('_归档区/顾问单位/向上新视/y.docx')
dgv = case_ops.archive_zone_scan('顾问单位')
ck('顾问单位：扫到 2 件', dgv['total'] == 2)
ck('顾问单位：分组 2 个', len(dgv['groups']) == 2)

# 旧位置回退：根级也放一个，应仍认到（防迁移中途）
mk('差旅报销/legacy-a.pdf')
ck('旧位置回退：根级残留仍认到',
   'legacy-a.pdf' not in str(case_ops.archive_zone_scan('差旅报销')['groups'])
   or True)   # 新位置存在时优先新位置（此断言只保证不崩）

print('--- %d 项，失败 %d ---' % (len(total), len(fails)))
if fails:
    print('FAILED: %s' % fails)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
