#!/usr/bin/env python
"""技能封面 v2 一套 5 张 —— 画面内容按 PRD §8.6「功能 → 画面动作」逐张定稿。

为什么要重做一套：v1 的 5 张各自一个 prompt，底色/构图/主体语言全不同（摆一排像 5 个来源），
而且 3 张主体都是白鞋。v2 的规矩写在 §8.6，这里只负责把它落成一条幂等管线。

🔴 三条硬约束（都是踩出来的，改提示词前先读）：
  ① prompt 里【不出现任何字面量】：不写颜色码、不写比例数字、不写"6 格/8 个"这种数量字。
     上一版我把 `#121212` 和 `16:9` 写进去，模型就当字符画进画面了。比例走 API 参数，不走文字。
  ② 负面词不可靠（`no text` 挡不住），所以出图后【必须人眼逐张过】；有字用 scripts/fix-skill-cover-text.py 裁带（0 花费）。
  ③ 画面里不许出现可识别的第三方商标（v1 那张 Nike AF1 就是这么被刷掉的）。
产出：productions/_skill-covers/cover-<skill>-v2.png（不覆盖 v1，便于回退）。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway as g  # noqa: E402

OUT = os.path.join(WS, r"productions\_skill-covers")
os.makedirs(OUT, exist_ok=True)

# 统一视觉系统（§8.6）：同一深炭底 · 单一主体居中偏左 · 橙只做一条光带 · 底部留给叠字 · 无字无商标
STYLE = ("dark charcoal studio background with generous negative space, single hero subject placed "
         "slightly left of centre occupying about three quarters of the frame height, one thin warm "
         "orange rim of light as the only accent colour, crisp commercial product lighting, shallow "
         "depth of field, subtle film grain, photorealistic, lower quarter of the frame kept calm and "
         "dark for a text overlay to be added later, ")
NEG = ("no letters, no words, no characters, no digits, no captions, no subtitles, no watermark text, "
       "no logo, no brand marks, no recognizable trademarked product shape, no readable signage, "
       "no people faces turned to camera, no clutter")

SHOTS = [
    ("super-clone",       # 同机位两幅并置：左原片商品、右换成另一个产品 = "只换商品身份"
     "two side by side product shots of the exact same camera angle, same table surface and same "
     "lighting, split by a single thin vertical light seam: on the left a plain unbranded matte "
     "ceramic tumbler, on the right in the identical position a plain unbranded glass cosmetic jar, "
     "both framed identically so the only difference is the product itself"),
    ("hypit-pipeline",    # 胶片带 + 一格一分为二 = 裂变；几何细连线 = 代码化编排
     "a horizontal film strip of several rectangular frames floating in dark space, each frame "
     "holding a different shot of the same unbranded product scene, one frame in the middle visibly "
     "splitting into two as a bright thin orange seam runs through it, delicate thin geometric "
     "connector lines linking the frames like a node diagram, no writing anywhere"),
    ("omni",              # 方图 → 竖屏画框 + 橙色光箭头 = 一张产品图变成一条竖屏广告
     "a square printed product photograph of a plain unbranded white ceramic tumbler lying on the "
     "left, and on the right a tall vertical phone-video frame showing the same tumbler mid-motion "
     "with a soft mist ring and gentle motion blur, a single thin warm orange light arrow sweeping "
     "from the square photo into the vertical frame"),
    ("short-drama",       # 两人隔桌对峙 + 产品被一束光托起 = 剧情承载卖点
     "cinematic film still of two business people facing each other across a dark table in a moody "
     "office, both in profile and out of focus at the edges, while between them a small unbranded "
     "product box is lifted and lit by one focused beam of warm light making it the centre of the "
     "confrontation, a faint row of small glowing dots along the bottom edge suggesting stages, "
     "warm cinematic grade, film grain"),
    ("video-dewatermark",  # 同一帧左右半幅对比：左有抽象半透明块、右干净 = before/after
     "one single product photograph of a plain unbranded matte ceramic tumbler on a dark surface, "
     "divided down the middle by a bright vertical erasing light band: the left half is covered by "
     "several soft translucent rounded rectangles floating above the image like frosted glass "
     "patches, the right half is perfectly clean sharp and unobstructed, identical subject and "
     "camera on both sides"),
]

for sid, body in SHOTS:
    dst = os.path.join(OUT, "cover-%s-v2.png" % sid)
    env = g.invoke("image.generate", "default", account="local",
                   params={"prompt": STYLE + body + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
    c = env.get("credits") or {}
    print("[%-18s] ok=%s 扣=%s 余额=%s" % (sid, env.get("ok"), c.get("charged"), c.get("balance_after")), flush=True)
    if not env.get("ok"):
        print("     错误:", str(env.get("error") or env.get("result"))[:240], flush=True)
        continue
    print("     落盘:", dst, os.path.getsize(dst) // 1024, "KB", flush=True)

print("\n目录:", OUT)
for f in sorted(x for x in os.listdir(OUT) if x.endswith("-v2.png")):
    p = os.path.join(OUT, f)
    try:
        from PIL import Image
        with Image.open(p) as im:
            print("  %-34s %s  %.2f MB" % (f, im.size, os.path.getsize(p) / 1048576))
    except Exception as e:
        print("  ", f, "读不了", e)
