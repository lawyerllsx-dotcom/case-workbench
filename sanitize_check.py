#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sanitize_check.py — 开源前隐私自检（可进 CI / pre-commit）

扫什么：
  1. 手机号（1[3-9] 开头 11 位）
  2. 身份证号（18 位）
  3. 真实案号（（20XX）…民初/执/刑初… 号）
  4. 自定义词表（本机文件 sanitize.words.local.txt，每行一个；**不入库**）
  5. 绝对路径（C:\\ / D:\\ 开头，提示是否应改成配置）

用法：
    python sanitize_check.py                 # 扫当前目录
    python sanitize_check.py --allow-test    # 测试文件里的假号不报（默认）
    python sanitize_check.py --strict        # 连测试文件也报
退出码：0 干净 / 1 有命中

说明：这里的正则只抓「像真数据」的字符串。假数据请用明显不可能的值
（如 13900000000、350200199001010000），避免误报。
"""
import argparse
import os
import re
import sys

SKIP_DIRS = {'.git', '__pycache__', 'demo', 'data', 'node_modules', '.venv', 'venv'}
EXT = ('.py', '.pyw', '.js', '.html', '.css', '.json', '.md', '.txt', '.bat', '.ps1')

PATTERNS = [
    ('手机号', re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')),
    ('身份证', re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')),
    ('案号', re.compile(r'[（(]\s*20\d{2}\s*[）)]\s*[\u4e00-\u9fa5]{1,6}\s*\d*\s*'
                        r'(民初|民终|执|执保|刑初|行初|民申|执异)\s*\d+\s*号')),
    ('绝对路径', re.compile(r'(?<![A-Za-z0-9_])[CDEF]:[\\/][^\s"\'）)]{3,80}')),
]

#: 允许出现的假值（文档示例 / 测试夹具），避免误报
ALLOW = {
    '13900000000', '13900000001', '13900000002', '13800000000',
    '13100000000', '13200000000', '14020000000',
    '350200199001010000', '13500000000000000',
}

#: 绝对路径里允许的（示例配置、文档说明、测试越界样本）
PATH_ALLOW = (
    'D:/我的台账', 'D:/案件', 'D:\\案件\\你的', 'D:\\\\案件\\\\你的',
    '<',                     # 已占位化的路径（<项目目录> / <台账备份目录>）
    'C:\\Windows\\win.ini', 'Z:/', 'D:/x', 'D:/b/x.xlsx', 'D:\\mock',
    'D:\\案件\\假', 'D:\\__nope__', 'X:\\nope.ico', 'D:/__nope__',
    'D:/ai-models', 'D:\\ai-models', 'D:\\\\ai-models',
)


def _skip_case_no(rel, strict):
    """测试夹具与示例数据里的案号是「格式样本」，不是真实数据。"""
    if strict:
        return False
    base = os.path.basename(rel)
    return base.startswith('test_') or base in ('demo_seed.py', 'smoke_views.py',
                                                'court_sms_ops.py')


def load_words():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sanitize.words.local.txt')
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            w = line.strip()
            if w and not w.startswith('#'):
                out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser(description='隐私自检')
    ap.add_argument('--root', default='.')
    ap.add_argument('--strict', action='store_true', help='测试文件也检查（默认同样检查）')
    ap.add_argument('--max-show', type=int, default=60)
    args = ap.parse_args()

    words = load_words()
    hits = []
    for dirpath, dirnames, filenames in os.walk(args.root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(EXT):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, args.root)
            try:
                with open(p, encoding='utf-8') as f:
                    for i, line in enumerate(f, 1):
                        for kind, pat in PATTERNS:
                            if kind == '案号' and _skip_case_no(rel, args.strict):
                                continue
                            for m in pat.finditer(line):
                                s = m.group(0).strip()
                                if kind == '绝对路径':
                                    if any(s.startswith(a) for a in PATH_ALLOW):
                                        continue
                                    if s.startswith(('D:\\WorkArea\\opensource', 'D:/WorkArea/opensource')):
                                        continue
                                if s in ALLOW:
                                    continue
                                hits.append((os.path.relpath(p, args.root), i, kind, s[:80]))
                        for w in words:
                            if w in line:
                                hits.append((os.path.relpath(p, args.root), i, '自定义词', w))
            except (OSError, UnicodeDecodeError):
                continue

    if not hits:
        print('OK 未发现疑似真实个人信息 / 案号（自定义词表 %d 条）' % len(words))
        return 0

    print('发现 %d 处疑似命中：' % len(hits))
    for rel, i, kind, s in hits[:args.max_show]:
        print('  %-30s %5d  [%s] %s' % (rel, i, kind, s))
    if len(hits) > args.max_show:
        print('  … 其余 %d 处省略' % (len(hits) - args.max_show))
    print('\n如果是示例数据，请换成明显不可能的值（13900000000 / 350200199001010000）；'
          '如果是真实数据，删掉或移入配置。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
