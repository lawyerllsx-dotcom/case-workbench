# -*- coding: utf-8 -*-
"""实机真点击「分区详情」按钮（playwright），验证不是假按钮"""
import json, os, urllib.request

os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '0')
try:
    from playwright.sync_api import sync_playwright   # noqa: E402
except ImportError:
    print('SKIP 未安装 playwright（pip install playwright && playwright install chromium）')
    raise SystemExit(0)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp', 'shots-v39d')
os.makedirs(OUT, exist_ok=True)

o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with o.open('http://127.0.0.1:8899/api/trace/index', timeout=60) as r:
    d = json.loads(r.read().decode('utf-8'))
case = d['cases'][0]['case']
print('目标案:', case)

PASS = FAIL = 0
def ck(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, extra))

with sync_playwright() as pw:
    b = pw.chromium.launch(channel='msedge', headless=True)
    pg = b.new_page(viewport={'width': 1700, 'height': 1200})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append('console:' + m.text) if m.type == 'error' else None)

    pg.goto('http://127.0.0.1:8899/#trace', wait_until='networkidle')
    pg.wait_for_timeout(2500)

    btns = pg.locator("button:has-text('分区详情')")
    n = btns.count()
    ck('总览页有「分区详情」按钮', n > 0, 'count=%d' % n)

    before = pg.inner_text('#main')
    ck('点击前在总览页（无「返回总览」）', '返回总览' not in before)

    btns.first.click()
    pg.wait_for_timeout(2500)

    after = pg.inner_text('#main')
    ck('**点击后页面切换了**（不是假按钮）',
       ('返回总览' in after) and ('留痕与文件管理' not in after))
    ck('详情页出现「返回总览」', '返回总览' in after)
    ck('详情页出现「版本族」', '版本族' in after)
    ck('详情页出现「归集到过程区」', '归集到过程区' in after)
    ck('详情页出现「只报不改」三处', after.count('只报不改') >= 3, after.count('只报不改'))
    ck('详情页出现「目录口径体检」', '目录口径体检' in after)
    ck('详情页出现「命名合规体检」', '命名合规体检' in after)
    ck('详情页出现「中间产物残留」', '中间产物残留' in after)
    ck('批 39e：详情页出现「⑤ 错位体检」', '错位体检' in after)
    ck('批 39e：错位区块有三条分类', ('① 程序性' in after or '② 过程性' in after or '③ 证据' in after),
       after[after.find('错位体检'):after.find('错位体检')+80] if '错位体检' in after else '')
    ck('无 JS 报错', not errs, errs[:2])

    pg.screenshot(path=os.path.join(OUT, 'detail-after-click.png'), full_page=True)
    print('  截图: detail-after-click.png')

    # 再点「返回总览」验证可回
    pg.locator("button:has-text('返回总览')").first.click()
    pg.wait_for_timeout(2000)
    back = pg.inner_text('#main')
    ck('「返回总览」能回总览页', '留痕与文件管理' in back and '返回总览' not in back)

    # 版本族折叠：展开一组看内容
    det = pg.locator('details').first
    if det.count() > 0:
        det.click()
        pg.wait_for_timeout(600)
        pg.screenshot(path=os.path.join(OUT, 'detail-family-open.png'), full_page=True)
        print('  截图: detail-family-open.png（版本族展开）')

    b.close()

print()
print('%d/%d 通过' % (PASS, PASS + FAIL))
raise SystemExit(0 if FAIL == 0 else 1)
