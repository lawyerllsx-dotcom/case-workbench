# -*- coding: utf-8 -*-
"""批量跑 test_*.py，记录通过/失败与耗时，输出机器可读汇总。"""
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
files = sorted(glob.glob(os.path.join(ROOT, 'test_*.py')))
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
    results.append({'file': name, 'rc': rc, 'sec': dt, 'tail': tail})

ok = [r for r in results if r['rc'] == 0]
bad = [r for r in results if r['rc'] != 0]
print('=== %d/%d PASS ===' % (len(ok), len(results)))
for r in results:
    print('%-28s rc=%-3d %6.1fs  %s' % (r['file'], r['rc'], r['sec'], '' if r['rc'] == 0 else r['tail']))
with open(os.path.join(ROOT, 'tmp_test_result.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print('\n失败 %d 件：%s' % (len(bad), ', '.join(r['file'] for r in bad)))
