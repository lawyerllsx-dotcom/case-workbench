# -*- coding: utf-8 -*-
r"""court_sms_ops.py — 法院短信送达文书拉取（批 61，2026-10-13；CaseBoard 0.4.18 court_sms 的 Python 原生重实现）

粘贴「人民法院在线服务（一张网）」（zxfw.court.gov.cn）的送达短信 →
解析（法院 / 案号 / 链接参数 sdbh·qdbh·sdsin）→ 拉送达文书列表 → 下载落案件夹 `00-收件箱`。

只支持一张网纯 API 路径：送达链接参数自带签名，POST getWsListBySdbhNew 即可拿文书列表
（无需登录/token/验证码/浏览器——接口形态参照 CaseBoard 真机实测版）。
省级自建平台（要浏览器自动化 + 凭证）不做——短信里没有 zxfw 链接就明说不支持。

纪律：
  · 只发起「拉取」请求（到法院官方平台），不上传任何本地数据（零外发红线不受影响）；
  · 文书落案件夹 `00-收件箱`（原名清洗 + 重名避让），原件零改动；
  · `download(apply=False)` 零落盘；`apply=True` 才真正下载；写临时名再原子改名；
  · 单文件下载上限 100MB；下载地址必须是 https；响应仅接受 JSON。
"""
import json
import os
import re
import urllib.request

ZXFW_LIST_API = 'https://zxfw.court.gov.cn/yzw/yzw-zxfw-sdfw/api/v1/sdfw/getWsListBySdbhNew'
TIMEOUT_LIST = 40
TIMEOUT_DL = 120
MAX_BYTES = 100 * 1024 * 1024
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36')

_RE_COURT = re.compile(r'【([^【】]*?法院)】')
_RE_CASENO = re.compile(
    r'[（(]\s*\d{4}\s*[）)]\s*[\u4e00-\u9fa5]{1,3}\s*\d{2,6}\s*[\u4e00-\u9fa5]{1,4}\s*\d+\s*号')


def _default_opener():
    r"""直连通道（绕环境代理——本机 HTTP_PROXY 会连 127.0.0.1 也带偏）。
    独立函数 + 模块级工厂：测试可整体替换（零网络桩）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


_OPENER_FACTORY = _default_opener


def normalize_case_no(s):
    r"""案号归一化：去所有空白 + 全角括号 → 半角。比对两边都归一，否则
    「（2026）闽0211民初329号」与「(2026)闽 0211 民初 329 号」会匹配不上。"""
    return ''.join(c for c in str(s or '') if not c.isspace()) \
        .replace('（', '(').replace('）', ')')


def match_case_no(cell, target):
    r"""台账「案号」单元格 vs 短信案号：拆分多条 + 逐条包含比对。

    批 73（2026-10-16 实踩）：台账常一格存两条案号（民初+执保，换行分隔；
    个别带「民事调解书」类后缀）——整串全等必不命中（当事人甲案 4195 号即被
    「民初4195号（换行）执保523号」拼接串挡住，全库 7/29 行受影响）。
    规则：按行/顿号/分号/逗号拆多条 → 逐条归一化 → 判条中包含 target；
    target 为带「号」结尾的完整案号，包含比对不会把「民初419号」误配到
    「民初4195号」（编号被「号」截断完整）。任一条命中即命中。
    """
    t = normalize_case_no(target)
    if not t:
        return False
    for part in re.split(r'[\n\r、;；,，]+', str(cell or '')):
        if t in normalize_case_no(part):
            return True
    return False


def parse_sms(text):
    r"""短信文本 → {'court', 'case_no', 'link'}（link=None = 没有一张网送达链接）。

    link 必须三参数齐（sdbh/qdbh/sdsin）且文本含 zxfw.court.gov.cn 才认——
    避免误抓其它平台带同名参数的链接。
    """
    text = str(text or '')
    court = None
    m = _RE_COURT.search(text)
    if m:
        court = m.group(1).strip()
    case_no = None
    m = _RE_CASENO.search(text)
    if m:
        case_no = m.group(0).strip()
    link = None
    if 'zxfw.court.gov.cn' in text:
        got = {}
        for k in ('sdbh', 'qdbh', 'sdsin'):
            mm = re.compile(k + r'=([^&\s#"\'>]+)').search(text)
            if mm:
                got[k] = mm.group(1)
        if len(got) == 3:
            link = {'sdbh': got['sdbh'], 'qdbh': got['qdbh'], 'sdsin': got['sdsin']}
    return {'court': court, 'case_no': case_no, 'link': link}


def fetch_docs(link):
    r"""POST getWsListBySdbhNew → 文书列表。错误透传真错。

    → {'ok': True, 'docs': [{'name','ext','url','court'}]}（字段来自法院响应
      c_wsmc/c_wjgs/wjlj/c_fymc；wjlj 是预签名下载 URL，有时效，用前现拉）。
    → {'ok': False, 'error': ...}（HTTP 错误 / 非 JSON / 业务 code != 200）。
    """
    body = json.dumps({'sdbh': link.get('sdbh', ''), 'qdbh': link.get('qdbh', ''),
                       'sdsin': link.get('sdsin', '')}).encode('utf-8')
    req = urllib.request.Request(
        ZXFW_LIST_API, data=body, method='POST',
        headers={'Content-Type': 'application/json',
                 'Origin': 'https://zxfw.court.gov.cn',
                 'Referer': 'https://zxfw.court.gov.cn/zxfw/',
                 'User-Agent': _UA})
    try:
        resp = _OPENER_FACTORY().open(req, timeout=TIMEOUT_LIST)
        text = resp.read().decode('utf-8', errors='replace')
    except Exception as e:                                    # noqa: BLE001
        return {'ok': False, 'error': '请求一张网失败：%s' % e, 'docs': []}
    try:
        data = json.loads(text)
    except Exception:                                         # noqa: BLE001
        return {'ok': False, 'error': '一张网响应非 JSON：%s' % text[:200], 'docs': []}
    code = data.get('code')
    try:
        code = int(code)
    except (TypeError, ValueError):
        pass
    if code != 200:
        return {'ok': False,
                'error': '一张网业务错误（code=%s）：%s' % (code, data.get('msg') or '未知'),
                'docs': []}
    docs = []
    for d in (data.get('data') or []):
        if not isinstance(d, dict):
            continue
        docs.append({'name': str(d.get('c_wsmc') or '未命名文书').strip(),
                     'ext': (str(d.get('c_wjgs') or 'pdf').strip().lower() or 'pdf'),
                     'url': str(d.get('wjlj') or '').strip(),
                     'court': str(d.get('c_fymc') or '').strip()})
    return {'ok': True, 'docs': docs}


def _safe_filename(name, ext):
    r"""文书名 → 安全文件名：非法字符替换 + 扩展名白名单化。

    批 80：法院文书名自带扩展名（如「…交纳诉讼费用通知书.pdf」）→ 先剥再拼，
    不再产出「.pdf.pdf」双扩展名（非凡世家立案送达包实踩）。
    """
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(name or '')).strip(' .')
    ext = re.sub(r'[^0-9a-zA-Z]', '', str(ext or '').lower()) or 'pdf'
    if name.lower().endswith('.' + ext):
        name = name[:-(len(ext) + 1)]
    return '%s.%s' % (name[:80] or '法院送达文书', ext)


def _avoid_dup(target_dir, fname):
    base, ext = os.path.splitext(fname)
    cand, i = fname, 2
    while os.path.exists(os.path.join(target_dir, cand)):
        cand = '%s-%d%s' % (base, i, ext)
        i += 1
    return cand


def _download_one(url, dest):
    r"""GET 预签名 URL → 落盘（写临时名再原子改名；超限即断）。"""
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    resp = _OPENER_FACTORY().open(req, timeout=TIMEOUT_DL)
    data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('文件超过 %dMB 上限' % (MAX_BYTES // 1024 // 1024))
    tmp = dest + '.tmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return len(data)


def download(case_dir, link, apply=False, subdir='00-收件箱'):
    r"""拉取文书（apply=False 零落盘）或下载落 `<目录>\<subdir>`（批 67：subdir='' → 直接落目录本身，供「短信文书暂存」用）。

    apply=True → {'ok': True, 'downloaded': [{'name','size_kb'}], 'failed': [...],
                  'inbox': '<subdir>'}
    （目录不存在会新建；同名自动避让 -2/-3；非 https 地址直接拒收。）
    """
    r0 = fetch_docs(link)
    if not r0.get('ok'):
        return {'ok': False, 'error': r0.get('error') or '拉取失败', 'docs': []}
    inbox = os.path.join(case_dir, subdir) if subdir else case_dir
    if not apply:
        return {'dry_run': True, 'docs': r0['docs'], 'inbox': subdir or '.',
                'inbox_exists': os.path.isdir(inbox),
                'count': len(r0['docs'])}
    os.makedirs(inbox, exist_ok=True)
    downloaded, failed = [], []
    for d in r0['docs']:
        if not d['url'].startswith('https://'):
            failed.append({'name': d['name'], 'error': '下载地址非 https（安全阀）'})
            continue
        fn = _avoid_dup(inbox, _safe_filename(d['name'], d['ext']))
        try:
            n = _download_one(d['url'], os.path.join(inbox, fn))
            downloaded.append({'name': fn, 'size_kb': round(n / 1024.0, 1)})
        except Exception as e:                                # noqa: BLE001
            failed.append({'name': d['name'], 'error': str(e)})
    return {'ok': True, 'dry_run': False, 'downloaded': downloaded, 'failed': failed,
            'inbox': subdir or '.'}
