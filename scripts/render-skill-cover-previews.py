#!/usr/bin/env python
"""把封面候选渲染成 dock 卡片实际会长成的样子（944×528 = 472×264 @2x + 底部渐变 + 标题叠层）。

只做本地 PIL 合成，0 花费。给你审的是"上线后长这样"，不是裸图。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = r"C:\dsh\assets\skill_covers"
W, H = 944, 528
TITLES = [("super-clone", "超级复刻", "原片换产品 · 可选换脸换背景"),
          ("hypit-pipeline", "Hypit 视频裂变", "video-as-code 工程化出片"),
          ("omni", "Omni 出片", "竖屏产品广告 · 剧本到成片"),
          ("short-drama", "短剧 agent 带货", "剧情设计驱动的八阶段链路")]
FONT = r"C:\Windows\Fonts\msyh.ttc"


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT, size, index=(1 if bold else 0))
    except Exception:
        return ImageFont.truetype(FONT, size)


for sid, title, tagline in TITLES:
    src = os.path.join(OUT, sid + ".png")
    if not os.path.isfile(src):
        print("缺原图:", sid)
        continue
    im = Image.open(src).convert("RGB")
    # 等比铺满后居中裁 16:9（原图已是 16:9，这里只是保险）
    scale = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((x, y, x + W, y + H))

    # 底部渐变压暗（PRD §8.5：叠一层底部渐变让白字可读）
    grad = Image.new("L", (1, H), 0)
    for yy in range(H):
        p = max(0.0, (yy - H * 0.42) / (H * 0.58))
        grad.putpixel((0, yy), int(215 * (p ** 1.4)))
    grad = grad.resize((W, H))
    scrim = Image.new("RGBA", (W, H), (8, 9, 12, 0))
    scrim.putalpha(grad.filter(ImageFilter.GaussianBlur(6)))
    im = im.convert("RGBA")
    im.alpha_composite(scrim)

    d = ImageDraw.Draw(im)
    d.text((32, H - 96), title, font=font(38, True), fill=(255, 255, 255, 255))
    d.text((32, H - 46), tagline, font=font(21), fill=(214, 218, 226, 235))
    d.rounded_rectangle([W - 132, 26, W - 32, 62], radius=18, fill=(250, 111, 8, 255))   # DSH 橙
    d.text((W - 118, 33), "去使用 ›", font=font(20, True), fill=(24, 16, 8, 255))
    d.rounded_rectangle([16, 16, W - 16, H - 16], radius=24, outline=(255, 255, 255, 26), width=2)

    dst = os.path.join(OUT, "preview-" + sid + ".png")
    im.convert("RGB").save(dst)
    print("预览:", dst, im.size)
print("\n审这两组：裸图在 <id>.png，上线效果在 preview-<id>.png")
