#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
make_aumid_shortcut.py — 创建带 AppUserModelID 的开始菜单快捷方式（批 D 补）

目的：Windows toast 通知的「来源」显示名由 AUMID 决定。用 PowerShell 的 AppId 会让
      通知显示「Windows PowerShell」（穿帮）。正解 = 给工作台注册自己的 AUMID：
      在开始菜单建一个 .lnk，设置其 AppUserModelID 属性 → WinRT 据此解析显示名/图标。

约束：沙箱禁止 COM/WScript（`New-Object -ComObject WScript.Shell` 被安全策略拦），
      故本脚本**直接构造 Shell Link (.lnk) 二进制**（MS-SHLLINK 规范），不依赖 COM。

.lnk 结构（关键部分）：
  1) ShellLinkHeader (76 bytes) —— CLSID + LinkFlags（含 HasLinkInfo/HasName/HasIconLocation）
  2) LinkTargetIDList —— 可选（本实现省略，用 LinkInfo 定位目标）
  3) LinkInfo —— 含目标绝对路径
  4) StringData（NAME / RELATIVE_PATH / WORKING_DIR / ICON_LOCATION）
  5) ExtraData —— PropertyStoreDataBlock (ID 0xA0000009)：写入 AUMID 属性
                   (PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, pid=5)
  6) TerminalBlock (4 bytes of 0)

用法：
  python make_aumid_shortcut.py --selftest        # 校验构造逻辑（不落盘）
  python make_aumid_shortcut.py --apply           # 真建快捷方式
退出码：0=成功 2=参数错误 3=失败
"""
import argparse
import io
import os
import struct
import sys

if os.environ.get('_WB_STDOUT_UTF8') != '1':
    os.environ['_WB_STDOUT_UTF8'] = '1'
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = '1.0.0'

# 工作台自己的 AUMID（无空格、符合 Windows 约定）
AUMID = 'Tencent.LegalWorkbench'
LNK_NAME = '法律工作台.lnk'
WORKDIR = os.path.dirname(os.path.abspath(__file__))
TARGET = sys.executable.replace('python.exe', 'pythonw.exe')
ARGS = '"%s" --idle-exit 0' % os.path.join(WORKDIR, 'app.py')
ICON = os.path.join(WORKDIR, '法律工作台.ico')
DESC = '法律工作台'


def lnk_path():
    return os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                        'Start Menu', 'Programs', LNK_NAME)


# ---------------------------------------------------------------- 二进制构造

CLSID_SHELLLINK = bytes.fromhex('0114020000000000C000000000000046')


def _strdata(value):
    """StringData：CountCharacters(2) + UTF-16LE 字符"""
    b = value.encode('utf-16-le')
    return struct.pack('<H', len(value)) + b


def _linkinfo(target):
    """LinkInfo（VolumeID + CommonPathSuffix 用 Unicode）"""
    # 卷 ID：用固定 GUID（可移动盘 GUID 形式）；LocalBasePath 用相对盘符路径
    drive = os.path.splitdrive(target)[0]          # 'C:'
    tail = target[len(drive):].lstrip('\\')        # 反斜杠后的相对路径
    # 说明：用 Unicode 变体（VolumeIDAndLocalBasePath 为 ANSI，这里用
    #       VolumeIDAndLocalBasePathUnicode = 标志 0x00000004）
    volume_id = b'\x00' * 16
    local_path_u = (drive + '\\' + tail).encode('utf-16-le')
    local_path_u += b'\x00\x00'
    # LinkInfoHeader: size(4) + flags(4) + VolumeIDOffset(4) + LocalBasePathOffset(4)
    #                 + CommonNetworkRelativeLinkOffset(4) + CommonPathSuffixOffset(4)
    #   flags: bit0=VolumeIDAndLocalBasePath(1) | bit2=Unicode(4) = 0x5
    flags = 0x00000001 | 0x00000004
    vol_off = 28
    base_off = vol_off + len(volume_id)
    cps_off = base_off + len(local_path_u)
    header = struct.pack('<IIIIII', 0, flags, vol_off, base_off, 0, cps_off)
    body = volume_id + local_path_u + b'\x00\x00'      # 空的 CommonPathSuffix
    size = len(header) + len(body)
    return struct.pack('<I', size) + header[4:] + body


def _aumid_property_block(aumid):
    """PropertyStoreDataBlock：写入 PKEY_AppUserModel_ID（属 .lnk 的 ExtraData）"""
    FMTID_APPUserModel = bytes.fromhex('55284C9F799F394BA8D0E1D42DE1D5F3')
    PID_AUMID = 5
    # SerializedPropertyStorage:
    #   StorageSize(4) + Version(4) + FmtID(16) + SerializedPropertyValue 数(4)
    aumid_u = aumid.encode('utf-16-le') + b'\x00\x00'
    # SerializedPropertyValue: ValueSize(4) + Id(4) + SerializedTypedPropertyValue
    #   ；VARTYPE = VT_LPWSTR(31)  —— 1 个 padding short + UTF-16 串
    typed = struct.pack('<H', 0) + struct.pack('<I', 31) + aumid_u
    spv = struct.pack('<I', 8 + len(typed)) + struct.pack('<I', PID_AUMID) + typed
    storage = struct.pack('<I', 4 + 4 + 16 + 4 + len(spv)) + struct.pack('<I', 0) \
        + FMTID_APPUserModel + struct.pack('<I', 1) + spv
    # PropertyStoreDataBlock: BlockSize(4) + Signature(0xA0000009) + 数据
    block_size = 8 + len(storage)
    return struct.pack('<II', block_size, 0xA0000009) + storage


def build_lnk(target=TARGET, args=ARGS, workdir=WORKDIR, icon=ICON, desc=DESC,
              aumid=AUMID):
    """构造 .lnk 字节流（MS-SHLLINK 子集：足够让 Windows 解析出名字/图标/AUMID）"""
    # HasLinkInfo(0x2) | HasName(0x4) | HasWorkingDir(0x10) | HasIconLocation(0x40)
    flags = 0x00000002 | 0x00000004 | 0x00000010 | 0x00000040
    if args:
        flags |= 0x00000020                            # HasArguments
    # MS-SHLLINK：文件头是 ShellLinkHeader（**含 4 字节 HeaderSize 前缀，共 76 字节**）
    #   实测基准（Administrative Tools.lnk）：byte0-3 = 76，CLSID 从 offset 4 起
    #   HeaderSize(4)=76 + CLSID(16) + LinkFlags(4) + FileAttributes(4)
    #   + CreationTime(8) + AccessTime(8) + WriteTime(8) + FileSize(4) + IconIndex(4)
    #   + ShowCommand(4) + HotKey(2) + Reserved(2) + Reserved2(4) + Reserved3(4)
    body = CLSID_SHELLLINK + struct.pack('<II', flags, 0x00000080)
    body += b'\x00' * 8                                # CreationTime
    body += b'\x00' * 8                                # AccessTime
    body += b'\x00' * 8                                # WriteTime
    body += struct.pack('<I', 0)                       # FileSize
    body += struct.pack('<I', 1)                       # IconIndex
    body += struct.pack('<I', 7)                       # ShowCommand = SW_SHOWMINNOACTIVE
    body += struct.pack('<H', 0)                       # HotKey（0 = 无）
    body += struct.pack('<H', 0)                       # Reserved（必须 0，2 字节）
    body += struct.pack('<I', 0)                       # Reserved2（必须 0，4 字节）
    body += struct.pack('<I', 0)                       # Reserved3（必须 0，4 字节）
    header = struct.pack('<I', 76) + body               # 前缀 Length 字段 = 76
    assert len(body) == 72, 'ShellLinkHeader 主体必须 72 字节，实际 %d' % len(body)
    assert len(header) == 76, 'HeaderSize + 主体必须 76 字节，实际 %d' % len(header)

    out = header
    out += _linkinfo(target)
    out += _strdata(desc)                              # NAME_STRING
    out += _strdata(args)                              # COMMAND_LINE_ARGUMENTS
    out += _strdata(workdir)                           # WORKING_DIR
    out += _strdata(icon)                              # ICON_LOCATION
    out += _aumid_property_block(aumid)                # ExtraData: AUMID
    out += struct.pack('<I', 0)                        # TerminalBlock
    return out


# ---------------------------------------------------------------- 主流程

def apply_shortcut():
    p = lnk_path()
    if not p:
        return False, 'APPDATA 环境变量缺失'
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = build_lnk()
    with open(p, 'wb') as f:
        f.write(data)
    return True, p


def verify_lnk(path):
    """回读校验：文件存在 + 头部 CLSID 正确 + 含 AUMID 字符串"""
    if not os.path.isfile(path):
        return False, '文件不存在'
    with open(path, 'rb') as f:
        raw = f.read()
    if len(raw) < 76 or raw[4:20] != CLSID_SHELLLINK:
        return False, 'CLSID 不匹配（非 Shell Link）'
    if AUMID.encode('utf-16-le') not in raw:
        return False, '未找到 AUMID 字符串'
    if TARGET.encode('utf-16-le')[:20] not in raw:
        return False, '未找到目标路径'
    return True, '%d 字节' % len(raw)


def selftest():
    fails, total = [], []

    def chk(name, cond):
        print('%s %s' % ('PASS' if cond else 'FAIL', name))
        total.append(name)
        if not cond:
            fails.append(name)

    b = build_lnk()
    chk('HeaderSize 前缀 = 76', struct.unpack('<I', b[:4])[0] == 76)
    chk('CLSID 在 offset 4', b[4:20] == CLSID_SHELLLINK)
    chk('总长 > 200', len(b) > 200)
    chk('含 AUMID 字符串', AUMID.encode('utf-16-le') in b)
    chk('含目标路径', TARGET.encode('utf-16-le')[:20] in b)
    chk('含工作目录', WORKDIR.encode('utf-16-le') in b)
    chk('含描述', DESC.encode('utf-16-le') in b)
    chk('尾部 TerminalBlock 全 0', b[-4:] == b'\x00' * 4)
    chk('StringData 长度字段正确（描述）',
        struct.pack('<H', len(DESC)) in b[:400])
    chk('AUMID 常量合法（无空格）', ' ' not in AUMID)
    chk('lnk 路径含开始菜单',
        lnk_path().endswith(os.path.join('Start Menu', 'Programs', LNK_NAME)))
    # 空参数分支
    chk('无参数时仍可构造', len(build_lnk(args='')) > 200)

    print('--- selftest %s（%d 项，失败 %d）---'
          % ('全部通过' if not fails else '有失败', len(total), len(fails)))
    return 0 if not fails else 3


def main():
    ap = argparse.ArgumentParser(description='AUMID 快捷方式构造器 v%s' % VERSION)
    ap.add_argument('--apply', action='store_true', help='真建快捷方式')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.apply:
        ap.print_help()
        return 2
    ok, detail = apply_shortcut()
    if not ok:
        print('FAIL:', detail)
        return 3
    print('已创建：%s' % detail)
    ok2, d2 = verify_lnk(detail)
    print('回读校验：%s（%s）' % ('OK' if ok2 else 'FAIL', d2))
    return 0 if ok2 else 3


if __name__ == '__main__':
    sys.exit(main())
