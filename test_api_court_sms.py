# -*- coding: utf-8 -*-
r"""test_api_court_sms.py — 法院短信送达文书接口回归（批 61，2026-10-13）

覆盖：
  · 短信解析（法院 / 案号 / 三参数链接；无链接 / 非一张网 / 空文本）
  · 案号归一化（全角括号 + 内部空格）
  · fetch_docs 四场景（200 列表 / 业务错误透传 / 非 JSON / 网络异常）——**全桩零网络**
  · 安全闸（夹名越界先于一切 / 链接参数缺失与非法字符）
  · preview 组合（解析 + 台账匹配建议 + 文书列表；无链接与拉取失败如实说明）
  · 批 71：嵌套案件夹（分组夹下）映射 + 映射缺失时案件名兜底找夹 + 嵌套「总夹/子夹」下载目标
  · 批 72：下载即归位（判决书名 → 04-法院文件；认不准/重复/冲突留收件箱；留痕 intake-log）
  · 批 73：台账多行案号（民初+执保）逐条命中（实踩：整串全等时代 7/29 行匹配全失效）
  · 批 74：分组夹目标提醒（下载目标为含 ≥2 案件子夹的分组夹时 warn——防误落实踩）
  · 批 75：下载完成自动打开目标文件夹（归位目录去重；全留收件箱打开收件箱；dry-run 不开）
  · 批 76：-2 避让后缀不绕过判重（两通道：auto_file_names + plan_intake；异内容仍正常归位）
  · 批 80：立案送达包关键词补全（受理案件通知/廉政监督卡/诉讼风险提示/权利义务告知/交纳诉讼费 → 04；
    双向验：票据仍 05、裸「告知书」不进 04）+ 法院文书名自带扩展名 → 不拼 .pdf.pdf
  · download dry-run 零落盘 / apply 落 00-收件箱（重名避让、坏链接拒收）

⚠️ 全程临时目录替身案件根 + **打桩网络（FakeOpener）**，零外网、零真案卷；
   日志打桩，不写真实 workbench.log。
"""
import os
import shutil
import sys
import tempfile
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


class FakeResp:
    def __init__(self, data):
        self._d = data

    def read(self, n=-1):
        return self._d if n == -1 else self._d[:n]


class FakeOpener:
    r"""按 URL 分发：list API → list_json；文件 → files[url]。"""

    def __init__(self, list_json=None, files=None, fail_list=False):
        self.list_json = list_json
        self.files = files or {}
        self.fail_list = fail_list

    def open(self, req, timeout=None):
        url = getattr(req, 'full_url', str(req))
        if 'getWsListBySdbhNew' in url:
            if self.fail_list:
                raise OSError('simulated timeout')
            return FakeResp(self.list_json.encode('utf-8'))
        if url in self.files:
            return FakeResp(self.files[url])
        raise OSError('unexpected GET: ' + url)


SMS_OK = ('【厦门市思明区人民法院】张三，我院向您发送了（2026）闽0203民初1234号案件相关文书，'
          '请点击查阅：https://zxfw.court.gov.cn/zxfw/#/pagesSd/sd/index?qdbh=Q123&sdbh=S456&sdsin=T789')
LINK_OK = {'sdbh': 'S456', 'qdbh': 'Q123', 'sdsin': 'T789'}
LIST_TWO = ('{"code":200,"data":['
            '{"c_wsmc":"民事调解书","c_wjgs":"PDF","wjlj":"https://oss.example.gov.cn/a.pdf","c_fymc":"思明法院"},'
            '{"c_wsmc":"开庭传票","c_wjgs":"pdf","wjlj":"https://oss.example.gov.cn/b.pdf"}]}')
FILES = {'https://oss.example.gov.cn/a.pdf': b'%PDF-1.4 fake-a',
         'https://oss.example.gov.cn/b.pdf': b'%PDF-1.4 fake-b'}


def main():
    import app as A
    C = A.court_sms_ops

    tmp = tempfile.mkdtemp(prefix='wb_csms_test_')
    case = '测试-法院短信'
    cp = os.path.join(tmp, case)
    os.makedirs(cp)

    orig_root = A.case_ops.case_root
    orig_biz = A.case_ops.biz_root
    orig_rows = A.case_ops.ledger_case_rows
    orig_fblr = A.case_ops.folder_by_ledger_row
    orig_log = A.log
    orig_factory = C._OPENER_FACTORY
    orig_open_fn = A._open_in_explorer                           # 批 75：自动打开打桩
    log_calls = []
    opened_calls = []
    try:
        A.case_ops.case_root = lambda: tmp
        A.case_ops.biz_root = lambda: os.path.join(tmp, 'biz')       # 批 67：暂存区打桩
        A.log = lambda action, detail='': log_calls.append((action, detail))
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_TWO, files=FILES)
        A._open_in_explorer = lambda p, select=None: (opened_calls.append(p) or True, 'mock-open')

        print('=== ① 短信解析（模块层） ===')
        p = C.parse_sms(SMS_OK)
        ck('标准短信：法院/案号/三参数全解析',
           p['court'] == '厦门市思明区人民法院' and p['case_no'] == '（2026）闽0203民初1234号'
           and p['link'] == LINK_OK)
        ck('案号归一化（全角+空格）',
           C.normalize_case_no('（2026）闽 0211 民初 329 号') == '(2026)闽0211民初329号')
        # 批 73：多行案号逐条比对（实踩：当事人甲案 4195 被「民初+执保」双行拼接串挡死）
        _multi = '（2026）闽0122 民初4195号\n（2026）闽0122执保523号'
        ck('批73：多行案号命中（民初行）', C.match_case_no(_multi, '（2026）闽0122民初4195号'))
        ck('批73：多行案号命中（执保行）', C.match_case_no(_multi, '（2026）闽0122执保523号'))
        ck('批73：带后缀行命中（民事调解书）',
           C.match_case_no('（2025）闽0681民初1136号民事调解书\n（2026）闽0681执4245号',
                           '（2025）闽0681民初1136号'))
        ck('批73：相邻案号不误配（419 ≠ 4195）',
           not C.match_case_no('（2026）闽0122民初4195号', '（2026）闽0122民初419号'))
        ck('批73：空值安全（cell/target 任一为空均不命中）',
           (not C.match_case_no('', '（2026）闽0122民初4195号'))
           and (not C.match_case_no(_multi, '')))
        ck('无链接短信 → link=None', C.parse_sms('【某法院】通知……').get('link') is None)
        ck('非一张网域名 → link=None',
           C.parse_sms('山东平台 https://sd.sdcourt.gov.cn/x?qdbh=1&sdbh=2&sdsin=3').get('link') is None)
        ck('缺参数（只带两个）→ link=None',
           C.parse_sms('https://zxfw.court.gov.cn/a?qdbh=1&sdbh=2').get('link') is None)
        ck('空文本 → 全 None',
           all(C.parse_sms('').get(k) is None for k in ('court', 'case_no', 'link')))

        print('=== ② fetch_docs 四场景（桩网络） ===')
        r = C.fetch_docs(LINK_OK)
        ck('200：列表解析 + 格式归一（PDF→pdf）',
           r['ok'] and len(r['docs']) == 2 and r['docs'][0]['ext'] == 'pdf')
        ck('200：字段齐（name/url/court）',
           r['docs'][0]['url'].startswith('https://') and r['docs'][0]['court'] == '思明法院')
        C._OPENER_FACTORY = lambda: FakeOpener(list_json='{"code":401,"msg":"非法请求"}')
        r = C.fetch_docs(LINK_OK)
        ck('业务错误（401）透传', (not r['ok']) and '401' in r['error'])
        C._OPENER_FACTORY = lambda: FakeOpener(list_json='<html>x</html>')
        ck('非 JSON → 拒', '非 JSON' in C.fetch_docs(LINK_OK).get('error', ''))
        C._OPENER_FACTORY = lambda: FakeOpener(fail_list=True)
        ck('网络异常 → 透传', '失败' in C.fetch_docs(LINK_OK).get('error', ''))
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_TWO, files=FILES)

        print('=== ③ 安全闸（越界先于一切） ===')
        r = A.api_court_sms_download({'case': '..', 'link': LINK_OK})
        ck('case .. → 拒', 'error' in r)
        r = A.api_court_sms_download({'case': '不存在夹', 'link': LINK_OK})
        ck('case 不存在 → 拒', 'error' in r)
        r = A.api_court_sms_download({'case': case, 'link': {'sdbh': 'S', 'qdbh': 'Q'}})
        ck('链接参数缺一 → 拒', 'error' in r and '安全闸' in r['error'])
        r = A.api_court_sms_download({'case': case,
                                      'link': {'sdbh': 'a/../b', 'qdbh': 'Q', 'sdsin': 'T'}})
        ck('链接参数含非法字符 → 拒', 'error' in r)

        print('=== ④ preview 组合（API 层） ===')
        A.case_ops.ledger_case_rows = lambda: [
            {'sheet': '其他案件', 'row': 5, '案件': '张三诉李四案', '案号': '（2026）闽 0203 民初 1234 号',
             '委托人': '张三', '案件类别': '民事', '状态': '已立案', '法院': '思明法院',
             '开庭时间': '', '当前进度': '', '进度日志': '', '承办律师': '', '标的额': None, '基础律师费': None}]
        A.case_ops.folder_by_ledger_row = lambda rows=None: {('其他案件', 5): cp}
        r = A.api_court_sms_preview({'sms': SMS_OK})
        ck('preview：解析 + 匹配建议 + 文书列表（批 72 附拟归入）',
           r['parse']['case_no'] == '（2026）闽0203民初1234号'
           and (r['match'] or {}).get('suggest') == case and len(r['docs']) == 2
           and r['docs'][0].get('dir') == '04-法院文件')
        r = A.api_court_sms_preview({'sms': '【某法院】无链接通知'})
        ck('无链接 → fetch_note 明说不支持', '没有识别到' in r['fetch_note'] and not r['docs'])
        C._OPENER_FACTORY = lambda: FakeOpener(list_json='{"code":401,"msg":"非法请求"}')
        r = A.api_court_sms_preview({'sms': SMS_OK})
        ck('拉取失败 → fetch_note 带真错', '401' in r['fetch_note'])
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_TWO, files=FILES)
        A.case_ops.ledger_case_rows = lambda: []
        r = A.api_court_sms_preview({'sms': SMS_OK})
        ck('案号未命中 → 不硬指（note 提示手选）',
           (r['match'] or {}).get('suggest') is None and '手动选' in (r['match'] or {}).get('note', ''))

        print('=== ④-ter 批 73：多行案号端到端（当事人甲案 4195 实况回归） ===')
        SMS_4195 = ('【连江县人民法院】张律师你好，请查收（2026）闽0122民初4195号案件中被告民事上诉状。'
                    '点击查阅：https://zxfw.court.gov.cn/zxfw/#/pagesSd/sd/index?qdbh=Q4195&sdbh=S4195&sdsin=T4195')
        A.case_ops.ledger_case_rows = lambda: [
            {'sheet': '案件', 'row': 11, '案件': '福州闽兴vs当事人甲、吴群华、许明华、吴伟腾-买卖合同纠纷',
             '案号': '（2026）闽0122 民初4195号\n（2026）闽0122执保523号',
             '委托人': '福州闽兴饲料有限公司', '案件类别': '民事', '状态': '已立案', '法院': '连江法院',
             '开庭时间': '', '当前进度': '', '进度日志': '', '承办律师': '', '标的额': None, '基础律师费': None}]
        A.case_ops.folder_by_ledger_row = lambda rows=None: {('案件', 11): cp}
        r = A.api_court_sms_preview({'sms': SMS_4195})
        ck('批73：多行案号短信 → 正常命中建议（整串全等时代必 FAIL）',
           (r['match'] or {}).get('suggest') == case
           and r['parse']['case_no'] == '（2026）闽0122民初4195号')

        print('=== ④-bis 批 71：嵌套案件夹映射 + 案件名兜底（夹查找实踩回归） ===')
        A.case_ops.folder_by_ledger_row = orig_fblr           # 用真实现（case_root 仍打桩到 tmp）
        _nested = os.path.join(tmp, 'A闽兴', '民-A-26012_福建闽兴VS新余钦丰祥等_买卖合同纠纷')
        os.makedirs(os.path.join(_nested, '00-收件箱'), exist_ok=True)
        A.case_ops.ledger_case_rows = lambda: [
            {'sheet': '案件', 'row': 12,
             '案件': '福建闽兴vs新余钦丰祥农业开发公司、孔祥斌、何红英、孔令钦-买卖合同纠纷',
             '案号': '（2026）闽 0203 民初 1234 号', '委托人': '福建闽兴饲料有限公司',
             '案件类别': '诉讼', '状态': '已立案', '法院': '', '开庭时间': '', '当前进度': '',
             '进度日志': '', '承办律师': '', '标的额': None, '基础律师费': None}]
        r = A.api_court_sms_preview({'sms': SMS_OK})
        ck('嵌套夹（分组夹下）映射到建议（批71 修复点）',
           (r['match'] or {}).get('suggest') == '民-A-26012_福建闽兴VS新余钦丰祥等_买卖合同纠纷')
        A.case_ops.folder_by_ledger_row = lambda rows=None: {}  # 强制映射缺失 → 走兜底
        r = A.api_court_sms_preview({'sms': SMS_OK})
        ck('映射缺失 → 案件名兜底找夹（批71 兜底通道）',
           (r['match'] or {}).get('suggest') == '民-A-26012_福建闽兴VS新余钦丰祥等_买卖合同纠纷')

        print('=== ⑤ download dry-run / apply（批 72：下载即归位） ===')
        A.case_ops.ledger_case_rows = lambda: []
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': False})
        inbox = os.path.join(cp, '00-收件箱')
        ck('dry-run：count=2 + 拟归入预览 + 零落盘',
           r.get('dry_run') is True and r.get('count') == 2
           and (r.get('docs') or [{}])[0].get('dir') == '04-法院文件'
           and not os.path.isdir(inbox))
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': True})
        ck('apply：两份下载后直接归位 04-法院文件（语义化改名）',
           r.get('ok') is True and len(r['downloaded']) == 2
           and len(r.get('filed') or []) == 2
           and all(x['dir'] == '04-法院文件' for x in r['filed'])
           and not os.path.isfile(os.path.join(inbox, '民事调解书.pdf')))
        _fdir = os.path.join(cp, '04-法院文件')
        _files = os.listdir(_fdir) if os.path.isdir(_fdir) else []
        _tj = [f for f in _files if f.endswith('_民事调解书.pdf')]
        ck('落盘内容与桩一致（归位后读原文）',
           len(_tj) == 1 and len([f for f in _files if f.endswith('_开庭传票.pdf')]) == 1
           and open(os.path.join(_fdir, _tj[0]), 'rb').read() == b'%PDF-1.4 fake-a')
        ck('归位留痕进 intake-log',
           '民事调解书' in open(os.path.join(cp, 'intake-log.md'), encoding='utf-8').read())
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': True})
        ck('再下一次（同内容）→ 判重复留在收件箱（不覆盖不硬归）',
           len(r['downloaded']) == 2 and (r.get('filed') or []) == []
           and any('重复' in x['reason'] for x in (r.get('left') or []))
           and os.path.isfile(os.path.join(inbox, '民事调解书.pdf')))
        C._OPENER_FACTORY = lambda: FakeOpener(
            list_json='{"code":200,"data":[{"c_wsmc":"扫描件001","c_wjgs":"pdf",'
                      '"wjlj":"https://oss.example.gov.cn/a.pdf"}]}', files=FILES)
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': True})
        ck('认不准的文书留在 00-收件箱（不硬归）',
           r.get('ok') is True and (r.get('filed') or []) == []
           and len(r.get('left') or []) == 1
           and os.path.isfile(os.path.join(inbox, '扫描件001.pdf')))
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_TWO, files=FILES)
        ck('日志 COURT_SMS_DL / COURT_SMS_FILE 已记',
           any(a == 'COURT_SMS_DL' for a, _ in log_calls)
           and any(a == 'COURT_SMS_FILE' for a, _ in log_calls))

        print('=== ⑤-bis 批 71：嵌套「总夹/子夹」下载目标 ===')
        _n2 = os.path.join(tmp, '系列甲', '子案一')
        os.makedirs(_n2, exist_ok=True)
        r = A.api_court_sms_download({'case': '系列甲/子案一', 'link': LINK_OK, 'confirm': False})
        ck('嵌套目标 dry-run：count=2 且零落盘',
           r.get('dry_run') is True and r.get('count') == 2
           and not os.path.isdir(os.path.join(_n2, '00-收件箱')))
        r = A.api_court_sms_download({'case': '系列甲/子案一', 'link': LINK_OK, 'confirm': True})
        _nf = os.path.join(_n2, '04-法院文件')
        ck('嵌套目标 apply：直接归位到 子案一\\04-法院文件',
           r.get('ok') is True and len(r.get('filed') or []) == 2
           and os.path.isdir(_nf)
           and sum(1 for f in os.listdir(_nf) if f.endswith('_民事调解书.pdf')) == 1)
        r = A.api_court_sms_download({'case': '系列甲/子案一/再深', 'link': LINK_OK, 'confirm': False})
        ck('三段路径 → 拒（只收两段）', 'error' in r)
        r = A.api_court_sms_download({'case': '系列甲/../子案一', 'link': LINK_OK, 'confirm': False})
        ck('段内 .. → 拒', 'error' in r)

        print('=== ⑤-ter 批 74：分组夹目标提醒（防误落——A闽兴 实踩） ===')
        os.makedirs(os.path.join(tmp, 'A闽兴', '民-A-26013_福州闽兴VS当事人甲等_买卖合同纠纷',
                                 '00-收件箱'), exist_ok=True)
        r = A.api_court_sms_download({'case': 'A闽兴', 'link': LINK_OK, 'confirm': False})
        ck('分组夹目标 dry-run → warn 提醒（含子案数与改选指引）',
           r.get('dry_run') is True and '分组夹' in (r.get('warn') or '')
           and '子案项' in (r.get('warn') or ''))
        r = A.api_court_sms_download({'case': '系列甲/子案一', 'link': LINK_OK, 'confirm': False})
        ck('子案夹目标（下无案件子夹）→ 无 warn', not r.get('warn'))
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': False})
        ck('普通案件夹目标 → 无 warn', not r.get('warn'))

        print('=== ⑤-quater 批 75：下载完成自动打开目标文件夹 ===')
        _c75 = os.path.join(tmp, '测试-批75归位')
        os.makedirs(_c75, exist_ok=True)
        opened_calls.clear()
        r = A.api_court_sms_download({'case': '测试-批75归位', 'link': LINK_OK, 'confirm': True})
        ck('自动打开：按归位目录打开（去重）',
           r.get('opened') == ['04-法院文件']
           and any(x.endswith(os.path.join('测试-批75归位', '04-法院文件')) for x in opened_calls))
        opened_calls.clear()
        r = A.api_court_sms_download({'case': '测试-批75归位', 'link': LINK_OK, 'confirm': True})
        ck('自动打开：全留收件箱 → 打开 00-收件箱',
           r.get('opened') == ['00-收件箱']
           and any(x.endswith(os.path.join('测试-批75归位', '00-收件箱')) for x in opened_calls))
        opened_calls.clear()
        r = A.api_court_sms_download({'case': '测试-批75归位', 'link': LINK_OK, 'confirm': False})
        ck('dry-run 不触发自动打开（只 apply 开）',
           r.get('opened') is None and not opened_calls)

        print('=== ⑤-quinquies 批 76：-2 避让后缀不再绕过判重（防重复归位副本） ===')
        _c76 = os.path.join(tmp, '测试-批76重复')
        os.makedirs(os.path.join(_c76, '00-收件箱'), exist_ok=True)
        os.makedirs(os.path.join(_c76, '04-法院文件'), exist_ok=True)
        # 批 80 顺修：预埋「正本」日期必须动态取当天——写死 20261016 时，跨天后重下改名
        # 用今天日期，fold 路径对不上预埋名 → 误判不重复、归位出副本（日期敏感型假 FAIL）
        _d76 = datetime.now().strftime('%Y%m%d')
        open(os.path.join(_c76, '04-法院文件', _d76 + '_法院文件_民事调解书.pdf'),
             'wb').write(b'%PDF-1.4 fake-a')
        open(os.path.join(_c76, '00-收件箱', '民事调解书-2.pdf'), 'wb').write(b'%PDF-1.4 fake-a')
        af76 = A.case_ops.auto_file_names(_c76, ['民事调解书-2.pdf'])
        ck('批76：-2 副本与正本同内容 → 判重复留箱（不归位出 -2 副本）',
           af76['filed'] == [] and len(af76['left']) == 1
           and '重复' in af76['left'][0]['reason']
           and sorted(os.listdir(os.path.join(_c76, '04-法院文件')))
           == [_d76 + '_法院文件_民事调解书.pdf'])
        pl76 = A.case_ops.plan_intake(_c76)
        _dup76 = [x for x in pl76['items'] if x['name'] == '民事调解书-2.pdf']
        ck('批76：扫描通道（plan_intake）-2 副本同样判重复（不归位）',
           len(_dup76) == 1 and '重复' in _dup76[0]['status'])
        open(os.path.join(_c76, '04-法院文件', _d76 + '_法院文件_开庭传票.pdf'),
             'wb').write(b'%PDF-1.4 fake-b')
        open(os.path.join(_c76, '00-收件箱', '开庭传票-2.pdf'), 'wb').write(b'%PDF-1.4 fake-b2')
        af76b = A.case_ops.auto_file_names(_c76, ['开庭传票-2.pdf'])
        ck('批76：-2 副本内容不同（真·不同文件）→ 正常归位（保留 -2 名）',
           len(af76b['filed']) == 1 and '-2' in af76b['filed'][0]['newname'])

        print('=== ⑤-sexies 批 80：立案送达包关键词补全（非凡世家 6 件实踩）+ 双扩展名修复 ===')
        LIST_SIX = ('{"code":200,"data":['
                    '{"c_wsmc":"厦门非凡世家工贸有限公司_交纳诉讼费用通知书.pdf","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f1.pdf","c_fymc":"翔安法院"},'
                    '{"c_wsmc":"举证通知书（厦门非凡世家工贸有限公司）","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f2.pdf","c_fymc":"翔安法院"},'
                    '{"c_wsmc":"廉政监督卡（厦门非凡世家工贸有限公司）","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f3.pdf","c_fymc":"翔安法院"},'
                    '{"c_wsmc":"民事诉讼风险提示书厦门市翔安区人民法院","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f4.pdf","c_fymc":"翔安法院"},'
                    '{"c_wsmc":"受理案件通知书（BLZT_ERROR_POINT）","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f5.pdf","c_fymc":"翔安法院"},'
                    '{"c_wsmc":"诉讼权利义务告知书（告知当事人用）","c_wjgs":"pdf",'
                    '"wjlj":"https://oss.example.gov.cn/f6.pdf","c_fymc":"翔安法院"}]}')
        files6 = dict(('https://oss.example.gov.cn/f%d.pdf' % i,
                       ('%%PDF-1.4 six-%d' % i).encode()) for i in range(1, 7))
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_SIX, files=files6)
        opened_calls.clear()
        r = A.api_court_sms_download({'case': case, 'link': LINK_OK, 'confirm': True})
        ck('批80：6 份立案送达包全部直接归位 04-法院文件（受理案件/廉政/风险/权利义务/交纳诉讼费）',
           r.get('ok') is True and len(r.get('downloaded') or []) == 6
           and len(r.get('filed') or []) == 6
           and all(x['dir'] == '04-法院文件' for x in r['filed']))
        ck('批80：法院名自带 .pdf → 落盘名无 .pdf.pdf 双扩展名',
           not any(x['name'].lower().endswith('.pdf.pdf') for x in r['downloaded'])
           and any(x['name'] == '厦门非凡世家工贸有限公司_交纳诉讼费用通知书.pdf'
                   for x in r['downloaded']))
        ck('批80 双向验：票据正本仍归 05（诉讼费发票）；裸「告知书」不进 04（落 03）',
           A.case_ops.suggest_dir('诉讼费电子发票.pdf')['dir'] == '05-费用票据'
           and A.case_ops.suggest_dir('解除合同告知书.docx')['dir'] == '03-证据材料')
        C._OPENER_FACTORY = lambda: FakeOpener(list_json=LIST_TWO, files=FILES)

        print('=== ⑥ 批 67：短信文书暂存（案件未建时帮带教下载） ===')
        r = A.api_court_sms_download({'stash': True, 'link': LINK_OK, 'confirm': False})
        stash_dir = os.path.join(tmp, 'biz', '接收到的文件', '短信文书')
        ck('暂存 dry-run：count=2 + stash 标记 + 零落盘',
           r.get('dry_run') is True and r.get('count') == 2 and r.get('stash') is True
           and not os.path.isdir(stash_dir))
        r = A.api_court_sms_download({'stash': True, 'link': LINK_OK, 'confirm': True})
        ck('暂存 apply：两份落 接收到的文件\\短信文书（直接放，无 00-收件箱 子夹）',
           r.get('ok') is True and r.get('stash') is True and len(r['downloaded']) == 2
           and os.path.isfile(os.path.join(stash_dir, '民事调解书.pdf'))
           and not os.path.isdir(os.path.join(stash_dir, '00-收件箱')))
        ck('暂存日志：记「短信文书暂存」标签',
           any(a == 'COURT_SMS_DL' and '短信文书暂存' in d for a, d in log_calls))
        orig_open = A._open_in_explorer
        A._open_in_explorer = lambda p: (True, p)
        try:
            r = A.api_court_sms_open_stash()
        finally:
            A._open_in_explorer = orig_open
        ck('打开暂存夹：目录自动建 + 路径正确',
           r.get('ok') is True and (r.get('opened') or '').endswith('短信文书')
           and os.path.isdir(stash_dir))
    finally:
        A.case_ops.case_root = orig_root
        A.case_ops.biz_root = orig_biz
        A.case_ops.ledger_case_rows = orig_rows
        A.case_ops.folder_by_ledger_row = orig_fblr
        A.log = orig_log
        C._OPENER_FACTORY = orig_factory
        A._open_in_explorer = orig_open_fn
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n=== 结果：%d/%d 通过 ===' % (len(total) - len(fails), len(total)))
    if fails:
        print('FAIL 项：')
        for f in fails:
            print('  - ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
