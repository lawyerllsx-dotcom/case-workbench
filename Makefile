# 一键命令（Windows 若没有 make，直接用 python 命令，见 README）
.PHONY: help check test smoke seed serve

help:
	@echo "make check  语法 + 隐私自检"
	@echo "make test   20 个接口回归"
	@echo "make smoke  19 页前端渲染"
	@echo "make seed   生成示例数据"
	@echo "make serve  启动工作台"

check:
	python -m compileall -q .
	python sanitize_check.py

test:
	python run_all_tests.py

smoke:
	python smoke_views.py --demo

seed:
	python demo_seed.py

serve:
	python app.py
