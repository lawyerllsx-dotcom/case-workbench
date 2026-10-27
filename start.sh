#!/usr/bin/env bash
# 一键启动：生成示例数据（幂等）-> 起服务
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY demo_seed.py
exec $PY app.py
