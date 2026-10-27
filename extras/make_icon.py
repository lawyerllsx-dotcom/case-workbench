# -*- coding: utf-8 -*-
"""法律工作台图标生成器（纯本地，PIL；无网络依赖）

设计：深靛蓝圆角底 + 金色天平（司法中立意象），4x 超采样抗锯齿，输出多尺寸 .ico
用法： python make_icon.py [输出路径]   默认 <项目目录>\\法律工作台.ico
"""
import os
import sys

from PIL import Image, ImageDraw

S = 1024                      # 超采样画布
BASE = S // 4                 # 输出 256
C_DARK = (22, 35, 62)         # 底深
C_DEEP = (31, 56, 100)        # 底浅
C_GOLD = (232, 196, 106)      # 金
C_GOLD_HI = (250, 228, 160)   # 金高光
C_WHITE = (245, 248, 252)


def _sc(v):
    return v * S // 256


def gradient_bg():
    img = Image.new('RGB', (S, S), C_DARK)
    d = ImageDraw.Draw(img)
    for y in range(S):
        t = y / (S - 1)
        r = int(C_DEEP[0] + (C_DARK[0] - C_DEEP[0]) * t)
        g = int(C_DEEP[1] + (C_DARK[1] - C_DEEP[1]) * t)
        b = int(C_DEEP[2] + (C_DARK[2] - C_DEEP[2]) * t)
        d.line([(0, y), (S, y)], fill=(r, g, b))
    # 左上高光
    hl = Image.new('L', (S, S), 0)
    ImageDraw.Draw(hl).ellipse([_sc(-120), _sc(-160), _sc(190), _sc(120)], fill=46)
    img = Image.composite(Image.new('RGB', (S, S), (120, 160, 220)), img, hl)
    return img


def rounded_mask():
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([_sc(6), _sc(6), _sc(250), _sc(250)],
                                        radius=_sc(48), fill=255)
    return m


def draw_scale(img):
    """天平：横梁 + 立柱 + 底座 + 双托盘"""
    d = ImageDraw.Draw(img)
    lw = _sc(7)                       # 主杆宽
    cx = _sc(128)

    # ── 底座 ──
    d.rounded_rectangle([_sc(92), _sc(196), _sc(164), _sc(196) + lw],
                        radius=lw // 2, fill=C_GOLD)
    d.rounded_rectangle([_sc(112), _sc(184), _sc(144), _sc(196) + lw // 2],
                        radius=_sc(3), fill=C_GOLD)
    # ── 立柱 ──
    d.rounded_rectangle([cx - lw // 2, _sc(88), cx + lw // 2, _sc(190)],
                        radius=lw // 2, fill=C_GOLD)
    # ── 横梁 ──
    d.rounded_rectangle([_sc(44), _sc(96), _sc(212), _sc(96) + lw],
                        radius=lw // 2, fill=C_GOLD)
    # ── 顶珠 ──
    d.ellipse([cx - _sc(13), _sc(74), cx + _sc(13), _sc(100)], fill=C_GOLD_HI)
    d.ellipse([cx - _sc(6), _sc(81), cx + _sc(6), _sc(93)], fill=C_GOLD)

    # ── 两侧托盘 ──
    for side in (-1, 1):
        x = cx + side * _sc(68)       # 吊点
        top, bot = _sc(112), _sc(150)
        # 吊绳
        d.rounded_rectangle([x - _sc(3), _sc(100), x + _sc(3), top],
                            radius=_sc(2), fill=C_WHITE)
        # 承水盘（下弧 + 平沿）
        d.pieslice([x - _sc(34), top - _sc(52), x + _sc(34), top + _sc(44)],
                   start=0, end=180, fill=C_WHITE)
        d.rounded_rectangle([x - _sc(36), top - _sc(4), x + _sc(36), top + _sc(4)],
                            radius=_sc(4), fill=C_GOLD_HI)
        # 盘内金色液面
        d.pieslice([x - _sc(26), top - _sc(28), x + _sc(26), top + _sc(20)],
                   start=0, end=180, fill=C_GOLD)

    # ── 底部装饰细线 ──
    d.rounded_rectangle([_sc(84), _sc(216), _sc(172), _sc(216) + _sc(4)],
                        radius=_sc(2), fill=(120, 150, 200))


def build(size=BASE):
    img = gradient_bg()
    draw_scale(img)
    img = img.resize((size, size), Image.LANCZOS)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    m = rounded_mask().resize((size, size), Image.LANCZOS)
    out.putalpha(m)
    return out


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '法律工作台.ico')
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    base = build(256)
    base.save(dst, format='ICO', sizes=sizes)
    print('ICON %s %d bytes' % (dst, os.path.getsize(dst)))
    png = os.path.splitext(dst)[0] + '.png'
    build(256).save(png, format='PNG')
    print('PNG  %s %d bytes' % (png, os.path.getsize(png)))


if __name__ == '__main__':
    main()
