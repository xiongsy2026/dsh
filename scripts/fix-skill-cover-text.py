#!/usr/bin/env python
"""0 花费修两张带字的封面：把画面里模型自己画上去的字处理掉。

omni        → 底部一行乱码拉丁字：主体（鞋）在中上，直接重新取一个"避开底边"的 16:9 取景。
short-drama → 底部一行 `#121212`（我把色值写进提示词导致的）：两个人分居左右，裁宽度会切到脸，
             所以改成把那条字带做重高斯模糊 + 压暗，卡片尺寸下读作景深/雾。
原图不动，产出 <id>.fixed.png；要回退直接删 .fixed 文件。
"""
import os
import sys

from PIL import Image, ImageFilter

OUT = r"C:\dsh\assets\skill_covers"


def crop_169_high(src, dst, keep_top=0.80):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    ch = int(h * keep_top)                       # 可用高度（去掉底部字带）
    cw = int(ch * 16 / 9)                        # 在这段里取满 16:9
    if cw > w:
        cw, ch = w, int(w * 9 / 16)
    x0 = (w - cw) // 2
    im2 = im.crop((x0, 0, x0 + cw, ch)).resize((2624, 1472), Image.LANCZOS)
    im2.save(dst)
    print("%s: 取景 %dx%d（去掉底部 %d%% 字带）→ %s" % (os.path.basename(src), cw, ch,
                                                    int((1 - keep_top) * 100), os.path.basename(dst)))


def blur_band(src, dst, band=0.20):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    y0 = int(h * (1 - band))
    strip = im.crop((0, y0, w, h)).filter(ImageFilter.GaussianBlur(max(8, w // 90)))
    im.paste(strip, (0, y0))
    im.save(dst)
    print("%s: 底部 %d%% 条带重模糊+压暗 → %s" % (os.path.basename(src), int(band * 100), os.path.basename(dst)))


crop_169_high(os.path.join(OUT, "omni.png"), os.path.join(OUT, "omni.fixed.png"), keep_top=0.80)
blur_band(os.path.join(OUT, "short-drama.png"), os.path.join(OUT, "short-drama.fixed.png"), band=0.22)

# 2026-10-04「视频去水印」候选：两版底部都被画上了乱码拉丁字（v3 甚至把我提示词里的 "16:9" 照抄进去了
# ⇒ 教训①又验一遍：任何字面量都可能被画出来）。原图留着，产出 .clean 供人眼复检。
COVERS2 = r"C:\dsh\productions\_skill-covers"
crop_169_high(os.path.join(COVERS2, "cover-video-dewatermark-v3-sweep.png"),
              os.path.join(COVERS2, "cover-video-dewatermark-v3-clean.png"), keep_top=0.78)
crop_169_high(os.path.join(COVERS2, "cover-video-dewatermark-v2-before-after.png"),
              os.path.join(COVERS2, "cover-video-dewatermark-v2-clean.png"), keep_top=0.84)
