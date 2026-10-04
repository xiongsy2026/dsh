#!/usr/bin/env python
"""技能封面候选 4 张（PRD §8.5 规格：472×264 / 16:9 / 深底 / 底部渐变 / 16px 安全区）。

只出【候选】，不接进 dock —— 未经封面登记表打勾的图不上线（§8.5 铁律）。
文字一律由前端叠，画面里不要任何字/字母/logo（生成模型画字必乱码，是实测坑）。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\video-skill")
from backend import gateway as g  # noqa: E402

OUT = r"C:\dsh\assets\skill_covers"
os.makedirs(OUT, exist_ok=True)
NEG = ("no text, no letters, no words, no logo, no brand marks, no stripes, no watermark, "
       "no captions, dark charcoal #121212 background, smooth bottom gradient for white text overlay, "
       "subject centered, 16 pixel safe margins on all sides, 16:9 horizontal composition, high detail")

COVERS = [
    # 风格定调（2026-10-04）：学小云雀的"核心能力一目了然"= 封面就是【真实成片的一帧】，
    # 不是概念插画/节点图（PRD §8.5 候选①）。品牌色只进 dock 的 CSS，不烧进图里。
    ("super-clone", "photorealistic still frame from a real product video: a plain unbranded white sneaker "
     "standing on a marble counter in a bright modern living room, camera mid-dolly so the background has "
     "gentle motion blur, cinematic commercial lighting, shallow depth of field, subtle film grain, "
     "looks exactly like a frame grabbed from a viral e-commerce short video, "),
    ("hypit-pipeline", "photorealistic still frame from a polished vertical brand film: a fashion model in a "
     "long dark coat walking through a neon-lit corridor, crisp subject focus with creamy background bokeh, "
     "anamorphic flare, graded cinematic color, looks like a frame from a real finished commercial, "),
    ("omni", "photorealistic hero shot from a real e-commerce ad: an unbranded plain white running sneaker "
     "levitating above a soft studio backdrop with a fine mist ring at its base, ultra-clean studio lighting, "
     "glossy product-render realism, shallow depth of field, looks like a frame from a real product video, "),
    ("short-drama", "photorealistic film still from a tense office drama scene: two business people facing "
     "each other across a desk, one holding a small product box, dramatic window sidelight, warm cinematic "
     "color grade, shallow depth of field, film grain, looks like a frame from a real short drama episode, "),
]

for sid, head in COVERS:
    dst = os.path.join(OUT, sid + ".png")
    env = g.invoke("image.generate", "default", account="local",
                   params={"prompt": head + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
    res = env.get("result") or {}
    c = env.get("credits") or {}
    print("[%s] ok=%s 扣=%s 依据=%s 余额=%s" % (sid, env.get("ok"), c.get("charged"), c.get("basis"),
                                            c.get("balance_after")), flush=True)
    if not env.get("ok"):
        print("   错误:", str(env.get("error") or res)[:260], flush=True)
        continue
    print("   原图:", res.get("path"), res.get("url", "")[:60], flush=True)

print("\n产物目录:", OUT)
for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    try:
        from PIL import Image
        with Image.open(p) as im:
            print("  %-22s %s %.2f MB" % (f, im.size, os.path.getsize(p) / 1048576))
    except Exception as e:
        print("  ", f, "读不了", e)
