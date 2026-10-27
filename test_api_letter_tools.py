# -*- coding: utf-8 -*-
r"""test_api_letter_tools.py — 律师函页工具接口回归（批 46，2026-09-19）

覆盖：
  · 安全闸四场景（越界/存在 的**优先顺序**——越界必须先拒，防路径探测）
  · 打开根目录 / 重扫 的成功路径
  · EMS 上传（base64）边界 + 成功落盘 + 顺手识别
  · EMS 导入守卫（缺夹名 / 图不存在）

⚠️ 本测试**不真打开资源管理器**（会弹窗干扰），只测到接口返回；
   真实 explorer 行为由人工验收（已实测通过，见 README 批 46）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails, total = [], []


def ck(name, cond):
    total.append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        fails.append(name)


def main():
    import app as A
    import ems_ops

    roots = []
    for fn in ('case_root', 'biz_root'):
        f = getattr(A.case_ops, fn, None)
        if callable(f):
            try:
                roots.append(os.path.abspath(f()))
            except Exception:
                pass
    roots.append(os.path.abspath(r'D:\备份'))
    inroot = next((r for r in roots if os.path.isdir(r)), None)

    print('=== ① 安全闸顺序（越界必须先拒，防路径探测）===')
    ok, det = A._open_in_explorer(r'C:\Windows\__nope__')
    ck('越界+不存在 → 拒且理由是越界', (not ok) and ('体系内' in det))
    ok, det = A._open_in_explorer(r'C:\Windows')
    ck('越界+存在 → 拒且理由是越界', (not ok) and ('体系内' in det))
    ok, _det2 = A._open_in_explorer('')
    ck('空路径 → 拒', not ok)
    if inroot:
        ok, det = A._open_in_explorer(os.path.join(inroot, '__nope__'))
        ck('体系内+不存在 → 理由是「不存在」', (not ok) and ('不存在' in det))
    else:
        print('  SKIP 未找到体系内根目录（跳过 1 项）')

    print('=== ② 重扫接口 ===')
    r = A.api_letter_rescan()
    ck('重扫 ok', r.get('ok') is True)
    ck('重扫返回 count（int）', isinstance(r.get('count'), int))
    ck('重扫含 folders.root', bool((r.get('folders') or {}).get('root')))

    print('=== ③ 打开接口守卫 ===')
    r = A.api_letter_open_folder({'folder': '..\\..\\Windows'})
    ck('folder 越界 → error', bool(r.get('error')))
    r = A.api_letter_open_folder({'row': 999999})
    ck('不存在的 row → error', bool(r.get('error')))

    print('=== ④ EMS 上传边界 ===')
    ck('缺 data → error', bool(A.api_ems_upload({}).get('error')))
    ck('非图片扩展名 → error',
       bool(A.api_ems_upload({'name': 'a.txt', 'data': 'aGVsbG8='}).get('error')))
    ck('坏 base64 → error',
       bool(A.api_ems_upload({'name': 'a.png', 'data': '!!!bad!!!'}).get('error')))

    print('=== ⑤ EMS 上传成功（1x1 合法 PNG）===')
    png_b64 = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA'
               'hKmMIQAAAABJRU5ErkJggg==')
    r = A.api_ems_upload({'name': 'selftest_ems.png', 'data': png_b64})
    ck('上传成功', r.get('ok') is True)
    ck('返回可识别路径且文件存在', bool(r.get('path')) and os.path.isfile(r['path']))
    ck('顺手识别返回结构', isinstance(r.get('recognize'), dict))
    if r.get('path') and os.path.isfile(r['path']):
        os.unlink(r['path'])
        ck('测试上传已清理', not os.path.isfile(r['path']))

    print('=== ⑤b 直接打开文件（批 46.3）===')
    ck('缺 path → error', bool(A.api_letter_open_file({}).get('error')))
    ck('目录不能当文件打开 → error',
       bool(A.api_letter_open_file({'path': inroot or 'D:\\案件'}).get('error')))
    ck('越界文件的 path → error（安全闸）',
       bool(A.api_letter_open_file({'path': r'C:\Windows\notepad.exe'}).get('error')))
    ck('不存在的文件 → error',
       bool(A.api_letter_open_file({'folder': '委托人', 'path': '__nope__.jpg'}).get('error')))

    print('=== ⑤c 页面内预览（批 46.4）===')
    ck('缺 path → error', bool(A.api_letter_preview_file({}).get('error')))
    ck('越界路径 → error（安全闸）',
       bool(A.api_letter_preview_file({'path': r'C:\Windows\notepad.exe'}).get('error')))
    ck('不存在的图 → error',
       bool(A.api_letter_preview_file({'folder': '委托人', 'path': '__nope__.jpg'}).get('error')))
    # 批 46.6：PDF 改为**支持页内预览**（用户拍板「全在页面内」）——
    # 原断言「非图片 → error」已过期，改为验证 PDF 放行且 kind=pdf。
    _pdf = A.api_letter_preview_file({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\徐俊帆律师函（签收）.pdf'})
    _mp4 = A.api_letter_preview_file({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\TSA_SCREEN_20260609092927.mp4'})
    if _pdf.get('ok'):
        ck('PDF 支持页内预览（kind=pdf）',
           _pdf.get('kind') == 'pdf'
           and str(_pdf.get('data_url') or '').startswith('data:application/pdf'))
        # 仍不支持的类型（如 .mp4）应明确拒绝并给提示
        ck('不支持的类型 → error + 提示', bool(_mp4.get('error')) and 'hint' in _mp4)
    else:
        print('  SKIP PDF 预览 / 不支持类型断言（示例数据无该文件）')
    # 真图片：应返回 data_url
    real = A.api_letter_preview_file({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\徐俊帆签收.jpg'})
    if real.get('ok'):
        ck('真图片返回 data_url', str(real.get('data_url', '')).startswith('data:image/'))
        ck('真图片返回 name/size_kb', bool(real.get('name')) and real.get('size_kb') > 0)
    else:
        print('  SKIP 真图片不可用（%s）' % real.get('error'))

    print('=== ⑥ EMS 导入守卫 ===')
    ck('导入缺夹名 → error', bool(ems_ops.plan_import(__file__, '').get('error')))
    ck('导入不存在的图 → error',
       bool(ems_ops.plan_import('D:/__nope__.png', '测试夹').get('error')))

    # ---- 批 46.6：页面内文件操作（下载 + PDF 预览）----
    print()
    print('=== 批 46.6 页面内文件操作 ===')
    # 取「委托人」夹的文件清单（验证 files_list 结构 + 挑一个 PDF 做预览/下载）
    fd = A.api_letter_rescan()
    ck('rescan 返回 folders', bool(fd.get('folders')))
    _hd = None
    for _f in ((fd.get('folders') or {}).get('folders') or []):
        if _f.get('name') == '委托人':
            _hd = _f
    if _hd is None:
        print('  SKIP 示例数据无「委托人」律师函夹（接真实业务目录后这 3 项生效）')
    else:
        ck('委托人夹含 files_list（页内清单数据源）',
           isinstance(_hd.get('files_list'), list) and len(_hd['files_list']) > 0)
        ck('files_list 项含 name/kind/size_kb/mtime',
           all(k in (_hd['files_list'][0] or {})
               for k in ('name', 'kind', 'size_kb', 'mtime')))
        ck('subdirs 为列表（批 46.6 由计数改列表）',
           isinstance(_hd.get('subdirs'), list))
    relpdf = None
    for f in ((_hd or {}).get('files_list') or []):
        if f.get('name', '').lower().endswith('.pdf'):
            relpdf = (f['sub'] + '\\' + f['name']) if f.get('sub') else f['name']
            break
    if relpdf:
        d = A.api_letter_download({'folder': '委托人', 'path': relpdf})
        ck('download 返回 ok + b64', d.get('ok') and len(d.get('b64') or '') > 100)
        ck('download 带 filename', bool(d.get('filename')))
        pv = A.api_letter_preview_file({'folder': '委托人', 'path': relpdf})
        ck('PDF 预览放行（kind=pdf）', pv.get('kind') == 'pdf'
           and str(pv.get('data_url') or '').startswith('data:application/pdf'))
    else:
        print('  (委托人夹无 PDF，跳过 PDF 断言)')
    # 安全闸：越界必须先拒（download 同口径）
    ck('download 越界被拒（安全闸）',
       '安全闸' in (A.api_letter_download({'path': r'C:\Windows\win.ini'}).get('error') or ''))
    ck('download 缺 path → error', bool(A.api_letter_download({'folder': '委托人'}).get('error')))

    # ---- 批 47：file_view 直出（PDF 白屏的正解；勿改回 dataURL）----
    print()
    print('=== 批 47 file_view 直出 ===')
    ck('file_view 缺 path → error', bool(A.api_letter_file_view({}).get('error')))
    ck('file_view 越界 → error（安全闸，越界最先）',
       '安全闸' in (A.api_letter_file_view({'path': r'C:\Windows\win.ini'}).get('error') or ''))
    ck('file_view 不存在 → error',
       bool(A.api_letter_file_view({'folder': '委托人', 'path': '__nope__.jpg'}).get('error')))
    _fv_bad = A.api_letter_file_view({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\TSA_SCREEN_20260609092927.mp4'})
    if _hd is None:
        print('  SKIP file_view 非白名单断言（示例数据无该夹）')
    else:
        ck('file_view 非白名单（.mp4）→ error + hint',
           bool(_fv_bad.get('error')) and 'hint' in _fv_bad)
    # 真图片：raw 为 bytes + mime 正确
    _fv_img = A.api_letter_file_view({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\徐俊帆签收.jpg'})
    if not _fv_img.get('error'):
        ck('file_view 真图片 → raw(bytes) + mime=image/jpeg',
           isinstance(_fv_img.get('raw'), bytes) and _fv_img.get('mime') == 'image/jpeg')
        ck('file_view 返回 name/size_kb',
           bool(_fv_img.get('name')) and _fv_img.get('size_kb', 0) > 0)
    else:
        print('  SKIP file_view 真图片不可用（%s）' % _fv_img.get('error'))
    # 真图片：raw 为 bytes + mime 正确
    _fv_pdf = A.api_letter_file_view({'folder': '委托人',
        'path': r'律师函-福建闽兴VS陈飞鹏\律师函及签收记录\徐俊帆律师函（签收）.pdf'})
    if not _fv_pdf.get('error'):
        ck('file_view 真PDF → raw(bytes) + mime=application/pdf',
           isinstance(_fv_pdf.get('raw'), bytes) and _fv_pdf.get('mime') == 'application/pdf'
           and len(_fv_pdf['raw']) > 1000)
    else:
        print('  SKIP file_view 真PDF不可用（%s）' % _fv_pdf.get('error'))

    # ---- 批 48：folder_files 单夹实时扫描 ----
    ck('folder_files 缺 folder → error', bool(A.api_letter_folder_files({}).get('error')))
    ck('folder_files 越界夹名（..）→ 安全闸拒',
       '安全闸' in (A.api_letter_folder_files({'folder': '..'}).get('error') or ''))
    ck('folder_files 夹名含路径分隔 → 安全闸拒',
       '安全闸' in (A.api_letter_folder_files({'folder': '委托人\\..\\..'}).get('error') or ''))
    ck('folder_files 不存在夹 → error',
       bool(A.api_letter_folder_files({'folder': '不存在的夹XYZ'}).get('error')))
    _ff = A.api_letter_folder_files({'folder': '委托人'})
    if not _ff.get('error'):
        ck('folder_files 正常夹 → ok + files 列表含 kind/name/size_kb',
           _ff.get('ok') and _ff.get('files') and all(
               k in _ff['files'][0] for k in ('kind', 'name', 'size_kb', 'sub', 'mtime')))
    else:
        print('  SKIP folder_files 正常夹不可用（%s）' % _ff.get('error'))
    _ff_y = A.api_letter_folder_files({'folder': '杨记水产'})
    if not _ff_y.get('error'):
        _docs = [f for f in _ff_y.get('files', [])
                 if f.get('kind') == '律师函' and '_备份' not in str(f.get('sub') or '')]
        if _docs:
            ck('folder_files 杨记水产实时含盖章函（kind=律师函，排除 _备份）',
               any(str(f.get('name', '')).endswith('.pdf') for f in _docs))
        else:
            print('  SKIP 杨记水产暂无盖章函扫描件（放入后接口实时可见）')
    else:
        print('  SKIP folder_files 杨记水产不可用（%s）' % _ff_y.get('error'))

    print('--- 回归 %s（%d 项，失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


if __name__ == '__main__':
    sys.exit(main())
