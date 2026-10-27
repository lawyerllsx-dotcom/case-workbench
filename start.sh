#!/usr/bin/env bash
# 一键启动：自检 -> 缺数据就生成 -> 起服务
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY verify.py || $PY demo_seed.py
exec $PY app.py
