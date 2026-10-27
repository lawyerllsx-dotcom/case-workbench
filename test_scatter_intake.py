# -*- coding: utf-8 -*-
"""批 38 冒烟：散件标记 + 散件入库（全隔离：临时 db + 临时案卷根 + 临时散件根）。

**绝不碰真数据**：WORKBENCH_DB / WORKBENCH_BACKUP_DIR / CASE_OPS_CASE_ROOT / CASE_OPS_BIZ_ROOT
全部指向 tempfile 目录。
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix='pw38_')
os.environ['WORKBENCH_DB'] = os.path.join(TMP, 'data', 'workbench.db')
os.environ['WORKBENCH_BACKUP_DIR'] = os.path.join(TMP, 'backup')
CASE_ROOT = os.path.join(TMP, 'A个案')
BIZ_ROOT = os.path.join(TMP, '张律师')
os.environ['CASE_OPS_ROOT'] = CASE_ROOT
os.environ['CASE_OPS_BIZ_ROOT'] = BIZ_ROOT

fails = []
total = []


def ck(name, cond):
    print('%s %s' % ('PASS' if cond else 'FAIL', name))
    total.append(name)
    if not cond:
        fails.append(name)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:                                        # noqa: BLE001
        return True


import case_ops          # noqa: E402
import ledger_ops        # noqa: E402
import tasks_ops         # noqa: E402

# ---- 假台账：注入两行，让散件能认到案件 ----
FAKE_ROWS = [{'sheet': '案件', 'row': 4, '案件': '福建闽兴vs林国伟-买卖合同纠纷',
             '委托人': '福建闽兴饲料有限公司', '案件类别': '诉讼', '承办律师': '张律师',
             '状态': '执行中', '案号': '', '法院': '', '开庭时间': '', '当前进度': '',
             '标的额': '', '基础律师费': '', '进度日志': ''}]
case_ops.ledger_case_rows = lambda: FAKE_ROWS          # 打桩（本进程内）
# 案件夹（顶层，匹配器按顶层夹名打分）
# ⚠️ 2026-09-18 批 39c 起匹配器把**案卷夹名**也当匹配源（规则 ①b），故此处夹名必须与
#    台账行的「案件」名指向同一案（只差大小写/前缀会导致被判「多案并存」而 blocked
#    ——那不是产品缺陷，是夹具与规则不同步）。
CASE_FOLDER = os.path.join(CASE_ROOT, '福建闽兴vs林国伟-买卖合同纠纷')
os.makedirs(os.path.join(CASE_FOLDER, '00-收件箱'), exist_ok=True)
# 批 71：案件夹判定＝含编号子夹（iter_case_folders 标准）——裸夹不识别；台账行→夹映射同步改多级扫描

# ---- 散件目录 + 一件真散件 ----
d = os.path.join(BIZ_ROOT, '律师函', '12林国伟')
os.makedirs(d, exist_ok=True)
src = os.path.join(d, '律师函-福建闽兴饲料有限公司致林国伟.doc')
with io.open(src, 'w', encoding='utf-8') as f:
    f.write('x')

# ============================================ ① 标记（meta 表）
tasks_ops.meta_set('scatter:律师函/12林国伟/律师函-福建闽兴饲料有限公司致林国伟.doc',
                   {'tag': '关注', 'note': '待跟进'})
m = tasks_ops.meta_map('scatter:')
ck('meta_set/meta_map 往返', len(m) == 1)
ck('meta 值正确', list(m.values())[0].get('tag') == '关注')
ck('meta_del 命中 1', tasks_ops.meta_del('scatter:律师函/12林国伟/律师函-福建闽兴饲料有限公司致林国伟.doc') == 1)
ck('meta_del 后为空', tasks_ops.meta_map('scatter:') == {})
ck('meta_del 再删返回 0', tasks_ops.meta_del('scatter:nope') == 0)

# ============================================ ② 路径守卫
ck('越界 .. 被拒', _raises(lambda: case_ops._scatter_src_rel('../x.doc')))
ck('绝对路径被拒', _raises(lambda: case_ops._scatter_src_rel('D:/x.doc')))
ck('非散件目录被拒', _raises(lambda: case_ops._scatter_src_rel('随便/甲.doc')))
ck('不存在的文件被拒', _raises(lambda: case_ops._scatter_src_rel('律师函/无.doc')))

# ============================================ ③ 入库计划
rel = '律师函/12林国伟/律师函-福建闽兴饲料有限公司致林国伟.doc'
plan = case_ops.plan_scatter_intake(rel, rows=FAKE_ROWS)
ck('计划认到案件', plan.get('case') == '福建闽兴vs林国伟-买卖合同纠纷')
ck('计划未 blocked', not plan.get('blocked'))
ck('计划目标在 00-收件箱', '00-收件箱' in (plan.get('dst') or ''))
ck('计划 src 存在', os.path.isfile(plan.get('src') or ''))

# 认不出的散件 → blocked，不动
d2 = os.path.join(BIZ_ROOT, '合同', '甲客户')
os.makedirs(d2, exist_ok=True)
p2 = os.path.join(d2, '服务合同-通用模板.docx')
with io.open(p2, 'w', encoding='utf-8') as f:
    f.write('y')
plan2 = case_ops.plan_scatter_intake('合同/甲客户/服务合同-通用模板.docx', rows=FAKE_ROWS)
ck('认不出的散件被拦（不硬套）', bool(plan2.get('blocked')))
ck('认不出时不写 dst', not plan2.get('dst'))

# ============================================ ④ 执行入库（真移动）
r = case_ops.apply_scatter_intake(rel, rows=FAKE_ROWS)
ck('源文件已移走', not os.path.isfile(src))
ck('目标文件存在', os.path.isfile(r['dst']))
ck('留痕文件已建', os.path.isfile(r.get('log') or ''))
_lg = io.open(r['log'], encoding='utf-8').read()
ck('留痕含文件名', '林国伟' in _lg)
ck('留痕含表头', '# 散件入库留痕' in _lg)
ck('台账零写入（打桩行未被改）', FAKE_ROWS[0]['案件'] == '福建闽兴vs林国伟-买卖合同纠纷')

# ============================================ ⑤ 批量计划
d3 = os.path.join(BIZ_ROOT, '律师函', '13林国伟')
os.makedirs(d3, exist_ok=True)
p3 = os.path.join(d3, '律师函-福建闽兴饲料有限公司致林国伟2.doc')
with io.open(p3, 'w', encoding='utf-8') as f:
    f.write('z')
items = case_ops.scatter_index(FAKE_ROWS).get('items', [])
matched = [x for x in items if x.get('matched')]
ck('散件索引认出 ≥1 件', len(matched) >= 1)
ck('索引项含 rel 字段', all('rel' in x for x in items))

# ============================================ ⑥ 同源同名避让
plan3 = case_ops.plan_scatter_intake('律师函/13林国伟/律师函-福建闽兴饲料有限公司致林国伟2.doc',
                                    rows=FAKE_ROWS)
ck('目标同名时自动避让', plan3.get('dst') != r['dst'])

# ============================================ ⑥b 跨源同名不判歧义（2026-09-18 批 39c 补三）
# 回归锁：台账行与案卷夹指同一案时，规则 ①b 让两者各进 hits → 曾误判「多案并存」。
# 断言：同一案名的多源命中不算 ambiguous，且 cases 去重后只剩 1 条。
_m = case_ops.scatter_match('律师函-福建闽兴饲料有限公司致林国伟.doc', FAKE_ROWS,
                            folder_name='林国伟',
                            folder_pool=[('福建闽兴vs林国伟-买卖合同纠纷',
                                          os.path.join(CASE_ROOT, '福建闽兴vs林国伟-买卖合同纠纷'))])
_names = [c.get('案件') for c in (_m.get('cases') or [])]
ck('跨源同名不判歧义', _m.get('ambiguous') is False)
ck('跨源同名去重为 1 条', len(_names) == len(set(_names)) == 1)
ck('跨源同名保留正确案名', _names and _names[0] == '福建闽兴vs林国伟-买卖合同纠纷')
# 反向对照：真的是两个不同案子 → 仍须判歧义（防修过头）
_m2 = case_ops.scatter_match('律师函-福建闽兴饲料有限公司致林国伟.doc', FAKE_ROWS,
                             folder_name='林国伟',
                             folder_pool=[('粤海商贸vs林国伟-另案纠纷',
                                           os.path.join(CASE_ROOT, '粤海商贸vs林国伟-另案纠纷'))])
ck('不同案名仍判歧义（防修过头）', _m2.get('ambiguous') is True)
ck('不同案名两候选都在', len(_m2.get('cases') or []) == 2)

# ============================================ ⑦ 批 39c：人工指定案件
# 先造一个真实案卷夹（人工指定要能真的找到夹）+ 一个**新散件**（旧的那个已被 ⑥ 移走）
CASE_A = os.path.join(CASE_ROOT, '民-A-测试案-人工指定')
os.makedirs(os.path.join(CASE_A, '00-收件箱'), exist_ok=True)
NEW_SCATTER = '律师函/13林国伟/律师函-人工指定测试.doc'
_nd = os.path.join(BIZ_ROOT, '律师函', '13林国伟')
os.makedirs(_nd, exist_ok=True)
with io.open(os.path.join(_nd, '律师函-人工指定测试.doc'), 'w', encoding='utf-8') as _f:
    _f.write('x')

plan4 = case_ops.plan_scatter_intake(NEW_SCATTER, rows=FAKE_ROWS,
                                     case_name='民-A-测试案-人工指定')
ck('人工指定：不报 blocked', not plan4.get('blocked'))
ck('人工指定：标记 manual', plan4.get('manual') is True)
ck('人工指定：case 用指定名', plan4.get('case') == '民-A-测试案-人工指定')
ck('人工指定：目标在指定案卷的 00-收件箱',
   '00-收件箱' in (plan4.get('dst') or '')
   and '民-A-测试案-人工指定' in (plan4.get('dst') or ''))

# 指定不存在的案名 → 拦（不自动建目录）
plan5 = case_ops.plan_scatter_intake(NEW_SCATTER, rows=FAKE_ROWS,
                                     case_name='不存在的案子XYZ')
ck('人工指定不存在的案 → 被拦', bool(plan5.get('blocked')))

# 候选清单：字段齐 + 含真实案卷夹（刚造的测试案）
opts = case_ops.scatter_case_options(rows=FAKE_ROWS)
ck('scatter_case_options 返回 ≥1 项', len(opts) >= 1)
ck('候选项含 case/folder/src 字段',
   all(('case' in o and 'folder' in o and 'src' in o) for o in opts))
ck('候选含真实案卷夹（刚造的测试案）',
   any(o.get('case') == '民-A-测试案-人工指定' for o in opts))

# 常规路径（不指定）也应带 candidates/options 字段（供前端下拉）
# ⚠️ plan4/plan5 是只读计划；此处先重建散件（前段执行用例可能已移走）
with io.open(os.path.join(_nd, '律师函-人工指定测试.doc'), 'w', encoding='utf-8') as _f:
    _f.write('x')
plan6 = case_ops.plan_scatter_intake(NEW_SCATTER, rows=FAKE_ROWS)
# 本件能被自动认到唯一案件（子夹名「13林国伟」是线索）→ 走成功分支，带 candidates
ck('常规路径：能自动认到唯一案件', not plan6.get('blocked'))
ck('常规路径：带 candidates 字段（供前端显示）', 'candidates' in plan6)

# 找一个**认不出/多案并存**的件，验证被拦时也带 candidates + options
# 用「根级 + 只有通用词」的散件（无子夹线索、无当事人片段）→ 认不出
with io.open(os.path.join(BIZ_ROOT, '律师函', '通用材料.doc'), 'w', encoding='utf-8') as _f:
    _f.write('x')
plan7 = case_ops.plan_scatter_intake('律师函/通用材料.doc', rows=FAKE_ROWS)
ck('认不出/多案并存时仍带 candidates + options（供人工指定）',
   ('candidates' in plan7) and ('options' in plan7))

# 执行路径：人工指定真落盘（临时目录内）
n_before = sum(len(fs) for _, _, fs in os.walk(CASE_A))
case_ops.apply_scatter_intake(NEW_SCATTER, rows=FAKE_ROWS,
                              case_name='民-A-测试案-人工指定')
n_after = sum(len(fs) for _, _, fs in os.walk(CASE_A))
inbox = os.path.join(CASE_A, '00-收件箱')
ck('人工指定执行后：源已移走',
   not os.path.isfile(os.path.join(BIZ_ROOT, '律师函', '13林国伟', '律师函-人工指定测试.doc')))
ck('人工指定执行后：目标存在',
   os.path.isfile(os.path.join(inbox, '律师函-人工指定测试.doc')))
ck('人工指定执行后：写留痕', os.path.isfile(os.path.join(inbox, '_散件入库.md')))
# +2 = 移入 1 件 + 新建 _散件入库.md 1 件（只移动不复制，源不在本夹内）
ck('人工指定执行后：本夹 +2（移入 1 + 留痕 1）', n_after == n_before + 2)

# ============================================ ⑧ 批 39c 补二：公司字号排除 + 夹名加权
# 公司字号**不得**当线索（实踩：福建闽兴把批量催收对象误挂到当事人丙案）
_m1 = case_ops.scatter_match('徐俊帆律师函（签收）.pdf', FAKE_ROWS, '')
ck('公司字号不当线索：徐俊帆 → 认不出', len(_m1['cases']) == 0)
_m2 = case_ops.scatter_match('陈飞鹏授信资料.pdf', FAKE_ROWS, '')
ck('公司字号不当线索：陈飞鹏 → 认不出', len(_m2['cases']) == 0)
# 真当事人仍应命中
_m3 = case_ops.scatter_match('林国伟身份证.pdf', FAKE_ROWS, '')
ck('真当事人仍命中：林国伟', len(_m3['cases']) >= 1)

# 夹名加权：2 字人名在**子夹名**里 → 加权后能认到
#   造一个「案子夹」（含 VS 特征）+ 一个「对象夹」
_CASE_H = os.path.join(CASE_ROOT, '民-A-福州闽兴vs霍山-买卖合同纠纷')
os.makedirs(os.path.join(_CASE_H, '00-收件箱'), exist_ok=True)
_fp = case_ops._case_folders_for_match()
ck('夹名池含「福州闽兴vs霍山」', any('霍山' in nm for nm, _ in _fp))
_m4 = case_ops.scatter_match('主债务人律师函.docx', FAKE_ROWS, '霍山', folder_pool=_fp)
ck('夹名加权：2 字人名在子夹名里 → 认到', len(_m4['cases']) >= 1)
_m5 = case_ops.scatter_match('主债务人律师函.docx', FAKE_ROWS, '', folder_pool=_fp)
ck('无夹名线索时同一文件认不出（对照）', len(_m5['cases']) == 0)

# 职能目录不进夹名池（实踩：需盖章文件/09-财产查控 等污染池子）
ck('职能目录不进夹名池（无 00-收件箱）', not any(nm == '00-收件箱' for nm, _ in _fp))
ck('职能目录不进夹名池（无 需盖章文件）', not any(nm == '需盖章文件' for nm, _ in _fp))

# ---- 批 39g：公司全名穿透 + 夹名全等豁免（2026-09-18 实踩） ----
# ① 公司全名（去字号后只剩地名）不得当线索 —— 实踩『福州闽兴饲料有限公司』穿透，
#    把「海王鳗业」的对账单挂到「朱泽洪案」。
#    隔离沙箱无真实案卷 → 改为**单元级**断言：公司全名在匹配器里被认作「无身份意义」。
_gp = case_ops._match_is_generic_piece if hasattr(case_ops, '_match_is_generic_piece') else None
# 走公开路径：用「公司全名 + 无关当事人」构造散件，应认不出
#     ⚠️ 必须放**根级**（无子夹线索）才测得出穿透 —— 放子夹里会被夹名规则命中。
_rootf = os.path.join(BIZ_ROOT, '律师函', '福州闽兴饲料有限公司一部2025-07厦门市海王鳗业有限责任公司月度对账单.pdf')
with io.open(_rootf, 'w', encoding='utf-8') as _f:
    _f.write('x')
_m9 = case_ops.plan_scatter_intake(
    '律师函/福州闽兴饲料有限公司一部2025-07厦门市海王鳗业有限责任公司月度对账单.pdf', FAKE_ROWS)
ck('公司全名不穿透：海王鳗业对账单不自动认案', bool(_m9.get('blocked')) or not _m9.get('case'))

# ② 豁免不得放宽为「片段 ⊂ 夹名」——`律师函-福建闽兴VS陈飞鹏` 整名放行会让
#    「福建闽兴」穿透，把陈飞鹏对账单误挂
_m11 = case_ops.scatter_match(
    '福建闽兴饲料有限公司2026-04陈飞鹏月度对账单.pdf',
    FAKE_ROWS, '律师函-福建闽兴VS陈飞鹏', folder_pool=_fp)
ck('夹名非全等不放行：陈飞鹏对账单不被误认', len(_m11['cases']) == 0)

# ③ 夹名全等豁免仍生效（「霍山」为子夹名时不被当公司字号剔除）
ck('夹名全等豁免：霍山批仍认到（≥1）', len(_m4['cases']) >= 1)

print('--- %d 项，失败 %d ---' % (len(total), len(fails)))
if fails:
    print('FAILED: %s' % fails)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
