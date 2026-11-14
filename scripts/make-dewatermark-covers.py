#!/usr/bin/env python
"""「视频去水印」这张卡的封面候选 3 版（PRD §8.5：出 2-3 版候选 → 产品选 1 版打勾才上线）。

规格照已上线的 4 张：16:9 / 深底 / 底部留渐变给白字叠 / 16px 安全区。
🔴 两条实测教训写死在这里：
  ① 提示词里【不要出现任何希望显示的字面量】——上次我把 `#121212` 当颜色写进去，模型就当字符画出来了；
  ② `no text, no letters` 这类负面词【不可靠】（两张照样带乱码字）⇒ 出图后必须人眼过一遍。
所以这 3 版一律用【抽象形状】表达"水印被清掉"，不画任何字符。
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
NEG = ("no letters, no words, no captions, no subtitles, no logo, no brand marks, no readable characters, "
       "dark charcoal background, smooth bottom gradient for a white text overlay, "
       "subject centered, generous safe margins on all sides, 16:9 horizontal composition, high detail")

# 三个方向：① 擦除带扫过 ② 半幅前后对比 ③ 单帧 + 光带溶解斑块
SHOTS = [
    ("v1-wipe", "photorealistic macro still from a product video: an unbranded white sneaker on a dark glossy "
                "surface, a soft translucent cleaning sweep band crossing the lower third of the frame, the area "
                "behind the band slightly hazier than the crisp area ahead of it, cinematic rim light, shallow "
                "depth of field, subtle film grain, "),
    ("v2-before-after", "photorealistic vertical film frame split down the middle by a thin light seam: left half "
                        "shows an unbranded sneaker product shot with several soft translucent rounded blobs floating "
                        "over the image, right half shows the identical shot perfectly clean and sharp, dark studio "
                        "background, cinematic grading, shallow depth of field, "),
    ("v3-sweep", "photorealistic film still of a plain unbranded sneaker mid-rotation on a dark set, a horizontal "
                 "beam of light sweeping across the frame dissolving soft translucent rectangles into sparkles as it "
                 "passes, clean negative space around the subject, anamorphic flare, moody commercial lighting, "),
]

for tag, head in SHOTS:
    dst = os.path.join(OUT, "cover-video-dewatermark-%s.png" % tag)
    env = g.invoke("image.generate", "default", account="local",
                   params={"prompt": head + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
    c = env.get("credits") or {}
    res = env.get("result") or {}
    print("[%s] ok=%s 扣=%s 依据=%s 余额=%s" % (tag, env.get("ok"), c.get("charged"), c.get("basis"),
                                             c.get("balance_after")), flush=True)
    if not env.get("ok"):
        print("   错误:", str(env.get("error") or res)[:260], flush=True)
        continue
    print("   落盘:", dst, os.path.getsize(dst) // 1024, "KB", flush=True)

print("\n目录:", OUT)
for f in sorted(x for x in os.listdir(OUT) if "dewatermark" in x):
    p = os.path.join(OUT, f)
    try:
        from PIL import Image
        with Image.open(p) as im:
            print("  %-44s %s  %.2f MB" % (f, im.size, os.path.getsize(p) / 1048576))
    except Exception as e:
        print("  ", f, "读不了", e)
