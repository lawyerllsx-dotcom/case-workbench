# -*- coding: utf-8 -*-
"""批量跑 test_*.py，记录通过/失败与耗时，输出机器可读汇总。"""
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))   # 本文件位于项目根
PY = sys.executable
files = sorted(glob.glob(os.path.join(ROOT, 'tests', 'test_*.py')))
results = []

for p in files:
    name = os.path.basename(p)
    t0 = time.time()
    try:
        r = subprocess.run([PY, p], cwd=ROOT, capture_output=True, encoding='utf-8',
                           errors='replace', timeout=300)
        rc, out, err = r.returncode, r.stdout or '', r.stderr or ''
    except subprocess.TimeoutExpired:
        rc, out, err = 124, '', 'TIMEOUT 300s'
    dt = round(time.time() - t0, 1)
    tail = (out.strip().splitlines() or [''])[-1][:160]
    if rc != 0 and not tail:
        tail = (err.strip().splitlines() or [''])[-1][:160]
    skipped = rc == 0 and ('SKIP' in out[:400])
    results.append({'file': name, 'rc': rc, 'sec': dt, 'tail': tail, 'skipped': skipped})

ok = [r for r in results if r['rc'] == 0 and not r.get('skipped')]
skip = [r for r in results if r['rc'] == 0 and r.get('skipped')]
bad = [r for r in results if r['rc'] != 0]
print('=== %d PASS / %d SKIP / %d FAIL（共 %d）==='
      % (len(ok), len(skip), len(bad), len(results)))
for r in results:
    tag = 'SKIP' if r.get('skipped') else ('PASS' if r['rc'] == 0 else 'FAIL')
    print('%-28s %-4s %6.1fs  %s' % (r['file'], tag, r['sec'], '' if r['rc'] == 0 else r['tail']))
with open(os.path.join(ROOT, 'tmp_test_result.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print('\n失败 %d 件：%s' % (len(bad), ', '.join(r['file'] for r in bad)))
