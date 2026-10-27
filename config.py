#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.py — 统一配置加载（开源版）

优先级（高 → 低）：
  1. 环境变量（LEDGER_PATH / CASE_OPS_ROOT / WORKBENCH_DB 等，兼容旧用法）
  2. workbench.config.json（本机配置，不入库）
  3. workbench.config.example.json（示例，入库；首次运行可复制改名）

配置里的相对路径一律相对于**本文件所在目录**解析，仓库可整体搬走。
"""
import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(BASE_DIR, 'workbench.config.json')
EXAMPLE = os.path.join(BASE_DIR, 'workbench.config.example.json')

DEFAULTS = {
    'app': {'title': '法律工作台', 'port': 8899, 'host': '127.0.0.1'},
    'paths': {
        'case_root': 'demo/案件/在办',
        'archive_root': 'demo/案件/已结案',
        'biz_root': 'demo/案件',
        'ledger': 'demo/案件管理台账.xlsx',
        'ledger_backup_root': 'demo/备份/台账',
        'clean_root': 'demo/备份/清理',
        'db': 'data/workbench.db',
        'db_backup': 'demo/备份/工作台',
        'dup_out': 'docs/dup_groups.md',
        'batch_dir': 'demo/批量文书',
        'batch_backup': 'demo/备份/批量文书',
    },
    'ledger': {
        'data_start_row': 4,
        'sheets_case': ['案件', '其他案件'],
        'sheet_letter': '律师函',
        'sheet_contract': '合同审查',
        'sheet_payment': '执行款',
    },
    'lawyer': {'name': '张三', 'phone': '', 'license': '', 'firm': '', 'address': '',
               'default_court': ''},
    'llm': {'base_url': '', 'model': '', 'api_key_file': ''},
    'ocr': {'enabled': False, 'python': '', 'script': ''},
    'wiki': {'root': '', 'case_dir': '案件'},
    'sohan': {'template_doc': ''},
    'notification': {'bubble_port': 7777, 'webhook_url': ''},
}


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    """读配置：本机文件优先，缺失则用示例文件，最后回落内置默认值。"""
    data = copy.deepcopy(DEFAULTS)
    source = 'builtin(defaults)'
    for p in (EXAMPLE, LOCAL):          # 示例先铺底，本机覆盖
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    data = _deep_merge(data, json.load(f))
                source = os.path.basename(p)
            except (OSError, ValueError):
                pass
    data['_meta'] = {'source': source, 'base_dir': BASE_DIR,
                     'local_exists': os.path.isfile(LOCAL)}
    return data


CFG = load()


def get(dotted, default=None):
    """按点路径取值：get('app.port')。"""
    cur = CFG
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def abspath(p):
    """配置里的相对路径 → 绝对路径（空值原样返回）。"""
    if not p:
        return ''
    p = os.path.expanduser(str(p))
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(BASE_DIR, p))


def path(dotted, env=None):
    """取路径型配置；env 指定时环境变量优先（兼容旧脚本用法）。"""
    if env and os.environ.get(env):
        return os.path.abspath(os.path.expanduser(os.environ[env]))
    return abspath(get(dotted, ''))


def summary():
    m = CFG['_meta']
    lines = ['配置来源: %s' % m['source'], '基准目录: %s' % m['base_dir']]
    if not m['local_exists']:
        lines.append('提示: 未发现 workbench.config.json，当前使用示例配置；'
                     '复制 workbench.config.example.json 后按你的台账修改。')
    for k in ('case_root', 'archive_root', 'ledger', 'ledger_backup_root', 'db'):
        lines.append('  %-20s %s' % (k, abspath(get('paths.' + k, ''))))
    return '\n'.join(lines)


if __name__ == '__main__':
    print(summary())
