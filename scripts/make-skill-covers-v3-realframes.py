#!/usr/bin/env python
"""技能封面 v3：主体一律用【我们自己成片的真实帧】，构图借小云雀那套"把机制画进图里"的手法。

为什么这么改（2026-10-04 用户顶回的原话："你这配图怎么都这么随性"）：
  v1/v2 是拿提示词让模型现画的"氛围图"，五张全是暗底+素色器物 ⇒ 像同一组产品图，不像五种能力。
  小云雀的卡之所以一眼懂，是因为画面里画着【机制】：半线框车身 / 一排片段缩略 / 左右擦除对比。
  ⇒ 借那个手法，素材全用盘上真跑出来的帧与生成图，**0 生图花费**（不烧积分）。

参考只借构图规则，不搬对方素材（字节旗下美术/成片帧，进我们卖的产品=素材侵权）。

🔴 一张都不许出现文字：标题/tagline 由 dock 的 CSS 叠，图里画字必然和卡片文字打架。
产出：productions/_skill-covers/cover-<skill>-v3.png（944×528 = PRD §8.5 的 472×264 @2x）
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

ROOT = r"C:\dsh"
OUT = os.path.join(ROOT, "productions", "_skill-covers")
W, H = 944, 528                    # 2x 输出，dock 里显示 236x132
ORANGE = (250, 111, 8)             # --dsw-alias-brand-primary，只做点缀
os.makedirs(OUT, exist_ok=True)

SRC = {
    "ref":        os.path.join(ROOT, "hypit_runs/productions/replica-final/reference.mp4"),
    "replica":    os.path.join(ROOT, "hypit_runs/productions/replica-final/outputs/final-8.5s.mp4"),
    "clip1":      os.path.join(ROOT, "productions/hypit-clone-video/outputs/429dd7b6ad214809.mp4"),
    "clip2":      os.path.join(ROOT, "productions/hypit-clone-video/outputs/9318aa9d0d2b4a6d.mp4"),
    "clip3":      os.path.join(ROOT, "productions/hypit-clone-video/outputs/e6aa8ab9183f4ea0.mp4"),
    "product":    os.path.join(ROOT, "video_runs/20260930-155139-富铁软糖30s-4699/product.jpg"),
    "keyframe":   os.path.join(ROOT, "video_runs/20260930-155139-富铁软糖30s-4699/assets/prop1.jpg"),
    "drama":      os.path.join(ROOT, "video_runs/20261002-174602-SPARK烟花棒30s剧情带货-4362/assets/prop1.png"),
}


def preflight():
    miss = [k for k, p in SRC.items() if not os.path.isfile(p)]
    if miss:
        print("缺素材，先看是哪几个：", ", ".join("%s=%s" % (k, SRC[k]) for k in miss))
        sys.exit(2)


def frame(video, t=1.2):
    """从视频抽一帧。用数组 argv，不走 shell。"""
    dst = os.path.join(tempfile.gettempdir(), "coverframe-%s-%.2f.jpg" % (os.path.basename(video)[:8], t))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", video,
                    "-frames:v", "1", "-q:v", "2", dst], timeout=120, check=True)
    return dst


def cover(img, w=W, h=H, focus_y=0.5):
    """按目标比例裁切（focus_y 决定竖向取景：0=保顶，1=保底），再缩放到 w×h。"""
    im = img.convert("RGB")
    src_r, dst_r = im.width / im.height, w / h
    if src_r > dst_r:                       # 太宽 ⇒ 裁两侧
        cw, ch = int(im.height * dst_r), im.height
        x0, y0 = (im.width - cw) // 2, 0
    else:                                   # 太高 ⇒ 按 focus_y 裁上下
        cw, ch = im.width, int(im.width / dst_r)
        y0 = int((im.height - ch) * focus_y)
        x0 = 0
    return im.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), Image.LANCZOS)


def bottom_shade(im, band=0.46, alpha=190):
    """底部压一层渐变黑：dock 把标题/tagline 叠在这儿，不压白字读不出。
    ⚠️ 方向别搞反：渐变必须【上透明→下最暗】。上一版多 flip 了一次，于是最暗压在带上沿、
       文字区反而是亮的，白底素材上会裂出一道硬边（2026-10-04 出图后人眼才发现）。
    """
    g = Image.new("L", (1, 256), 0)
    for y in range(256):
        g.putpixel((0, y), int(alpha * (y / 255.0) ** 1.8))
    mask = g.resize((im.width, int(im.height * band)))
    pad = Image.new("L", im.size, 0)
    pad.paste(mask, (0, im.height - mask.height))
    black = Image.new("RGB", im.size, (8, 9, 12))
    return Image.composite(black, im, pad)


def rounded(im, r):
    """给缩略图/卡片做圆角 + 1px 亮边（dock 卡是 12px 圆角，这里等比放大）。"""
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=r, fill=255)
    out = Image.new("RGB", im.size, (0, 0, 0))
    out.paste(im, (0, 0), m)
    d = ImageDraw.Draw(out)
    d.rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=r, outline=(205, 209, 214, 255), width=3)
    return out


def paste(base, tile, box):
    x, y, w, h = box
    base.paste(tile.resize((w, h), Image.LANCZOS), (x, y))


def save(im, name):
    p = os.path.join(OUT, "cover-%s-v3.png" % name)
    im.save(p)
    print("  %-22s %dx%d  %4d KB" % (name, im.width, im.height, os.path.getsize(p) / 1024))


def c_super_clone():
    """原片帧 | 复刻帧，中间一条橙缝 = "同一条片子，只换商品身份"。"""
    a = cover(Image.open(frame(SRC["ref"])), w=W // 2, h=H, focus_y=0.45)
    b = cover(Image.open(frame(SRC["replica"])), w=W // 2, h=H, focus_y=0.45)
    im = Image.new("RGB", (W, H), (8, 9, 12))
    im.paste(a, (0, 0))
    im.paste(b, (W // 2, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([W // 2 - 3, 0, W // 2 + 3, H], fill=ORANGE)      # 擦除缝
    return bottom_shade(im)


def c_hypit():
    """满幅成片帧 + 下方一排真实分段缩略 = "整组镜头一眼看到"（借他们的预演手法）。"""
    im = cover(Image.open(frame(SRC["clip3"], 1.0)))
    tw, th, gap = 236, 132, 16
    x = 40
    for f in (SRC["clip1"], SRC["clip2"], SRC["clip3"]):
        tile = rounded(cover(Image.open(frame(f, 0.8)), w=tw, h=th), 14)
        im.paste(tile, (x, H - th - 56))
        x += tw + gap
    return bottom_shade(im, band=0.40)


def c_omni():
    """左侧真实产品图（输入）→ 满幅生成帧（输出）+ 一条橙色光箭头 = 图生竖屏片。"""
    im = cover(Image.open(SRC["keyframe"]), focus_y=0.4)
    card = rounded(cover(Image.open(SRC["product"]), w=260, h=260), 18)
    im.paste(card, (44, H - 260 - 70))
    d = ImageDraw.Draw(im)
    y = H - 70 - 130
    d.line([324, y, 444, y], fill=ORANGE, width=9)                     # 箭杆
    d.polygon([(444, y - 19), (480, y), (444, y + 19)], fill=ORANGE)   # 箭头
    return bottom_shade(im)


def c_short_drama():
    """满幅真实剧情帧 + 底沿八个点 = 八阶段链路（点用几何，绝不写字）。"""
    im = cover(Image.open(SRC["drama"]), focus_y=0.35)
    d = ImageDraw.Draw(im)
    n, r, y = 8, 9, H - 46
    total = n * (2 * r) + (n - 1) * 26
    x = (W - total) // 2
    for i in range(n):
        d.ellipse([x, y - r, x + 2 * r, y + r], fill=ORANGE if i < 3 else (150, 152, 156))
        x += 2 * r + 26
    return bottom_shade(im, band=0.34)


if __name__ == "__main__":
    preflight()
    # 2026-10-04 逐格人眼过完候选帧后的取舍（规则：有真人脸/第三方硬字幕的一律不进客户界面）：
    #   可用 = 鞋 seg01/03（只有手腿）· 软糖生成帧 prop1/3/4 · 客户产品图 product.jpg（罐身中文是客户包装本身）
    #   不可用 = 袜子 seg02/08（真人主播脸 + "不闷脚/不捂脚/不臭脚"硬字幕）
    #            replica-final 成片帧（真人侧身）· hypit-clone 生成段（仿真人正脸）
    #   ⇒ 超级复刻【没有】合格的"原片 vs 复刻"无脸对照素材 ⇒ 这张继续用 v2，等无脸素材再重做
    #   ⇒ Hypit（缺多段无脸素材）· 短剧（唯一"剧情帧"是带中文卖点的详情图）也继续用 v2
    #   ⇒ 视频去水印这条腿一次没真跑过 ⇒ 故意不给图，dock 走首字母兜底
    print("本轮只出 Omni 的真素材版，其余按上面的取舍：")
    save(c_omni(), "omni")
    print("产出目录:", OUT)
