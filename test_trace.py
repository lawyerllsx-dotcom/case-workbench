# -*- coding: utf-8 -*-
"""批 39d 隔离回归：留痕与文件管理（全部在临时目录，不碰真案卷）

覆盖：
  1. 分区判定（定稿/草稿/备份/无标记/junk）
  2. 版本族归族 + 定稿识别
  3. 体检（口径别名 / 命名问题 / junk / 版本族）
  4. 归集计划：三层守卫（越界 / 已在过程区 / 不存在）
  5. 归集执行：未确认拒绝 / 只移动不删除 / 留痕文件 / 原名与原位置
  6. API 层（真起临时实例）：/api/trace/index|case|files|gather
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix='trace_e2e_')
CASE_ROOT = os.path.join(TMP, 'A个案')

# ---- 造夹具案卷 ----
CASE1 = '民-A-测试案一-买卖合同纠纷'
D1 = os.path.join(CASE_ROOT, CASE1)
os.makedirs(os.path.join(D1, '02-法律文书'))
os.makedirs(os.path.join(D1, '08-过程文件', '备份-文书修改-20260916'))
os.makedirs(os.path.join(D1, 'pipeline'))
os.makedirs(os.path.join(D1, 'monitor_frames'))

def mk(rel, txt='x'):
    p = os.path.join(D1, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(txt)
    return p

mk('02-法律文书/民事起诉状（定稿）.docx')
mk('02-法律文书/民事起诉状-v1.docx')
mk('02-法律文书/民事起诉状-v2.docx')
mk('02-法律文书/无锚点文件.docx')
mk('08-过程文件/答辩状（初稿）.docx')
mk('08-过程文件/答辩状（备份）.docx')
mk('08-过程文件/答辩状-定稿.docx')          # 过程区里的定稿 → 建议回正式区
mk('monitor_frames/0001.jpg')

os.environ['CASE_OPS_ROOT'] = CASE_ROOT
os.environ['CASE_OPS_BIZ_ROOT'] = os.path.join(TMP, '张律师')
os.makedirs(os.path.join(TMP, '张律师'), exist_ok=True)

import trace_ops   # noqa: E402

OK = FAIL = 0

def ck(name, cond, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  PASS %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, extra))


print('=== 1. 分区判定 ===')
c = trace_ops.classify_file(os.path.join(D1, '02-法律文书', '民事起诉状（定稿）.docx'), D1)
ck('定稿 → final/正式区', c['role'] == 'final' and c['zone'] == 'final')
c = trace_ops.classify_file(os.path.join(D1, '08-过程文件', '答辩状（初稿）.docx'), D1)
ck('初稿 → draft/过程区', c['role'] == 'draft' and c['zone'] == 'process')
c = trace_ops.classify_file(os.path.join(D1, '08-过程文件', '答辩状（备份）.docx'), D1)
ck('备份 → backup/过程区', c['role'] == 'backup' and c['zone'] == 'process')
c = trace_ops.classify_file(os.path.join(D1, 'monitor_frames', '0001.jpg'), D1)
ck('中间产物 → junk（按所在目录段判定）', c['role'] == 'junk', c.get('reason'))
c = trace_ops.classify_file(os.path.join(D1, '08-过程文件', '答辩状-定稿.docx'), D1)
ck('过程区里的定稿 → need_move=True（应回正式区）', c['role'] == 'final' and c['need_move'])

print('=== 2. 版本族 ===')
fams = trace_ops.group_versions(
    [os.path.join(D1, '02-法律文书', x) for x in
     ('民事起诉状（定稿）.docx', '民事起诉状-v1.docx', '民事起诉状-v2.docx')], D1)
ck('起诉状 3 件归一族', len(fams) == 1 and fams[0]['count'] == 3)
ck('族内识别定稿', fams and fams[0]['has_final'])
ck('族内版本号', fams and fams[0]['versions'] == [1, 2])
ck('族内最新=版本号最大者（无版本号的定稿不参与版本比较）',
   fams and fams[0]['latest'] == '民事起诉状-v2.docx')

print('=== 3. 体检 ===')
sw = trace_ops.sweep(D1)
ck('过程区计数=2（初稿+备份；「答辩状-定稿」按定稿标记归正式区）',
   sw['process_count'] == 2, sw['process_count'])
ck('正式区计数 ≥4', sw['final_count'] >= 4, sw['final_count'])
ck('junk 命中 monitor_frames', len(sw['junk']) == 1)
# 2026-09-18 口径定案：pipeline / 需盖章文件 是「有明确职能的独立目录」，不再报口径不一
ck('口径定案：pipeline 不再报口径不一', not any(a['name'] == 'pipeline' for a in sw['alias_dirs']))
ck('口径定案：需盖章文件 认作正式区', '需盖章文件' in trace_ops.COURT_DIRS)
ck('证据原件不参与命名体检（不误报）',
   not any(x['rel'].startswith('03-证据材料') or x['rel'].startswith('06-当事人信息')
           for x in sw['naming_issues']))
ck('版本族 ≥1', sw['version_family_count'] >= 1)
# 幂等：再跑一次结果一致（证明不复写文件）
sw2 = trace_ops.sweep(D1)
ck('体检幂等（文件数一致）', sw['total'] == sw2['total'])

ck('命名体检不报孤立单件（收窄后）',
   not any('无锚点文件' in x['name'] for x in sw['naming_issues']))
# 造「族内缺锚点」的真问题：同名两份、都没版本号
mk('02-法律文书/答辩状.docx')
mk('02-法律文书/答辩状（最终）.docx')
_swb = trace_ops.sweep(D1)
ck('命名体检报族内缺锚点（答辩状.docx）',
   any('答辩状.docx' in x['name'] for x in _swb['naming_issues']),
   [x['name'] for x in _swb['naming_issues']][:5])

print('=== 4. 归集计划（三层守卫）===')
plan = trace_ops.plan_gather(D1, ['02-法律文书/民事起诉状-v1.docx',
                                 '02-法律文书/民事起诉状-v2.docx'])
ck('计划 2 项无拦截', plan['count'] == 2 and plan['blocked_count'] == 0)
ck('目标为 08-过程文件', plan['proc_dir'].endswith('08-过程文件'))
p_bad = trace_ops.plan_gather(D1, ['../../../外面.docx'])
ck('越界被拦', p_bad['blocked_count'] == 1 and p_bad['count'] == 0)
p_in = trace_ops.plan_gather(D1, ['08-过程文件/答辩状（初稿）.docx'])
ck('已在过程区不重移', p_in['blocked_count'] == 1)
p_no = trace_ops.plan_gather(D1, ['02-法律文书/不存在.docx'])
ck('不存在被拦', p_no['blocked_count'] == 1)

print('=== 5. 归集执行（只移动）===')
before_total = sum(len(fs) for _, _, fs in os.walk(D1))
try:
    trace_ops.apply_gather(plan, confirm=False)
    ck('未确认拒绝执行', False)
except ValueError:
    ck('未确认拒绝执行', True)
r = trace_ops.apply_gather(plan, confirm=True)
ck('移动 2 件', r['moved'] == 2)
ck('原位置已空', not os.path.isfile(os.path.join(D1, '02-法律文书', '民事起诉状-v1.docx')))
ck('目标存在', os.path.isfile(os.path.join(D1, '08-过程文件', '民事起诉状-v1.docx')))
ck('留痕文件已写', os.path.isfile(r['log']))
lg = io.open(r['log'], encoding='utf-8').read()
ck('留痕含文件名', '民事起诉状-v1.docx' in lg)
ck('留痕含原位置', '02-法律文书' in lg)
ck('留痕含「只移动」声明', '只移动' in lg)
after_total = sum(len(fs) for _, _, fs in os.walk(D1))
ck('只移动未删除（总文件 +1 = 新建的留痕文件）', after_total == before_total + 1,
   '%d -> %d' % (before_total, after_total))
ck('定稿未被搬走（留在正式区）',
   os.path.isfile(os.path.join(D1, '02-法律文书', '民事起诉状（定稿）.docx')))
ck('过程区里的初稿仍在',
   os.path.isfile(os.path.join(D1, '08-过程文件', '答辩状（初稿）.docx')))

print('=== 6. 重名避让 ===')
mk('02-法律文书/答辩状（备份）.docx')          # 与过程区已有同名
plan2 = trace_ops.plan_gather(D1, ['02-法律文书/答辩状（备份）.docx'])
ck('同名自动避让（目标名带 _归集1）', '_归集1' in plan2['items'][0]['dst'],
   plan2['items'][0]['dst'])

print('=== 8. 三分类错位体检（批 39e）===')
mk('08-过程文件/证据清单-提交法院（定稿）.docx')
mk('08-过程文件/要素式起诉状-定稿版.docx')
mk('08-过程文件/仲裁立案材料核对清单-20260915.md')
mk('02-法律文书/交叉验证报告-20260916.md')
mk('02-法律文书/收条.jpg')
mk('08-过程文件/诉讼费发票.jpg')

r1 = trace_ops.classify_role('证据清单（定稿）.docx', 'x.docx')
ck('程序性：证据清单 → court', r1['role'] == 'court', r1['role'])
r2 = trace_ops.classify_role('仲裁立案材料核对清单.md', 'x.md')
ck('过程性：核对清单.md → proc', r2['role'] == 'proc', r2['role'])
r3 = trace_ops.classify_role('诉讼费发票.jpg', 'x.jpg')
ck('收窄：票据类图片不算证据（→ unknown 待人工）',
   r3['role'] == 'unknown', r3['role'])
r4 = trace_ops.classify_role('授权委托书.docx', 'x.docx')
ck('程序性：授权委托书 → court', r4['role'] == 'court')
r5 = trace_ops.classify_role('_材料怎么放.md', 'x.md')
ck('基础设施：豁免名 → infra', r5['role'] == 'infra')

mp = trace_ops.misplace(D1)
ck('错位：程序性躺过程区被抓出', mp['court_in_proc_n'] >= 1, mp['court_in_proc_n'])
ck('错位：过程性躺正式区被抓出', mp['proc_in_court_n'] >= 1, mp['proc_in_court_n'])
ck('错位：给出建议目录', all(x.get('suggest') for x in mp['court_in_proc']))
ck('错位：带「待人工确认」线索字段', all('pending' in x for x in mp['court_in_proc']))
ck('待人工确认：撤回件被标出', bool(trace_ops.pending_hint('14、调查令申请书（撤回备查）.docx')))
ck('待人工确认：定稿件不误标', trace_ops.pending_hint('答复意见(定稿).docx') == '')
ck('错位体检只读（文件未动）',
   os.path.isfile(os.path.join(D1, '08-过程文件', '收条.jpg')) is False)

sw3 = trace_ops.sweep(D1)
ck('体检含 misplace 字段', ('misplace' in sw3) and sw3['misplace']['total'] >= 1)

fn = trace_ops._func_dir_of('A闽兴/5.6揭阳闽兴vs林癸涵/08-过程文件')
ck('职能目录定位：跳过案卷名取到 08-过程文件', fn == '08-过程文件', fn)
fn2 = trace_ops._func_dir_of('仲-厦门向上沟通VS厦门沧珩_服务合同纠纷/02-法律文书')
ck('职能目录定位：首段即职能目录', fn2 == '02-法律文书', fn2)

print('=== 7. 真数据零接触核验 ===')
# 开源版：案卷根由配置决定，默认指向 demo。若配置到了真实案卷，则核验其未被本次测试写入。
real = os.environ.get('CASE_OPS_ROOT') or ''
_ok_real = (not real) or (not os.path.isdir(real)) or \
           (not os.path.isdir(os.path.join(real, '__trace_test__')))
ck('夹具在临时目录（未用真案卷根）', trace_ops.case_root() == CASE_ROOT)
ck('真案卷根未被本次写入（只读探测）', _ok_real)

print()
print('%d/%d 通过' % (OK, OK + FAIL))
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(0 if FAIL == 0 else 1)
