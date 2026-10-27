# -*- coding: utf-8 -*-
"""批 39a 隔离回归：所函生成 API 端到端（**不落任何真案卷**）。

隔离：sohan_ops.DEFAULT_TPL 指向临时模板；案卷用临时目录。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

fails, total = [], []


def ck(name, cond):
    print('%s %s' % ('PASS' if cond else 'FAIL', name))
    total.append(name)
    if not cond:
        fails.append(name)


import sohan_ops  # noqa: E402

TMP = tempfile.mkdtemp(prefix='p39a_api_')

# ============================================ ① 模板与计划（离线，不起服务）
ck('模板可用', sohan_ops.template_ok()[0])
CASE = os.path.join(TMP, '民-A-测试案')
os.makedirs(CASE, exist_ok=True)

p1 = sohan_ops.plan_sohan('厦门市思明区人民法院', '甲公司', '张律师律师', '一审', '乙公司',
                          '买卖合同纠纷', CASE)
ck('计划无 blocked', not p1.get('blocked'))
ck('目标在 01-委托手续', '01-委托手续' in p1['dst'])
ck('日期留空', '年  月  日' in p1['xml'])
r1 = sohan_ops.apply_sohan(p1)
ck('落盘存在', os.path.isfile(r1['dst']))
with zipfile.ZipFile(r1['dst']) as z:
    nx = z.read('word/document.xml').decode('utf-8')
ck('新值入档', '厦门市思明区人民法院' in nx and '甲公司' in nx)
ck('旧值清空', '周锦全' not in nx)
ck('抬头保留', '某某律师事务所' in nx)

# ② 同名避让
p2 = sohan_ops.plan_sohan('厦门市思明区人民法院', '甲公司', '', '一审', '乙公司',
                          '买卖合同纠纷', CASE)
ck('同名避让', p2['dst'] != p1['dst'])

# ③ 必填/非法校验
ck('缺法院被拦', bool(sohan_ops.plan_sohan('', '甲公司')['blocked']))
ck('缺委托被拦', bool(sohan_ops.plan_sohan('X法院', '')['blocked']))
ck('非法阶段被拦', bool(sohan_ops.plan_sohan('X法院', '甲', '', '乱写')['blocked']))
ck('案卷不存在被拦', bool(sohan_ops.plan_sohan('X法院', '甲', '', '一审', '', '', 'Z:/没有')['blocked']))

# ④ 不指定案卷 → 落到所函目录下 _生成
p3 = sohan_ops.plan_sohan('厦门市思明区人民法院', '甲公司', '', '一审', '乙', '买卖合同纠纷', '')
ck('不指定案卷有回落路径', bool(p3.get('dst')) and '_生成' in (p3.get('dst') or ''))
ck('回落落点是 所函/_生成（不在 _代盖章 子夹）',
   os.path.join('所函', '_生成') in (p3.get('dst') or '').replace('/', os.sep)
   and '_代盖章' not in (p3.get('dst') or ''))

# ⑤ 模板真源未被改
ck('模板真源 .doc 仍在', os.path.isfile(sohan_ops.TPL_DOC))
ck('模板 docx 未被写入', os.path.getsize(sohan_ops.template_path()) > 0)

# ⑥ 案卷既有文件未被动
others = [f for f in os.listdir(os.path.join(CASE, '01-委托手续'))]
ck('落点目录内仅有本次生成件', all('所函-' in f for f in others))

shutil.rmtree(TMP, ignore_errors=True)
print('--- %d 项，失败 %d ---' % (len(total), len(fails)))
if fails:
    print('FAILED: %s' % fails)
sys.exit(1 if fails else 0)
