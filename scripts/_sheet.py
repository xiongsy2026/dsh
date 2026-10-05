"""把一批封面候选摊成一张竖排长图，供人眼逐张过（有没有字/商标/白底/跑偏）。
用法: python scripts/_sheet.py v4            # 取 cover-*-v4.png
      python scripts/_sheet.py v3 omni       # 只摊指定的那几张
"""
import os
import sys

from PIL import Image

OUT = r"C:\dsh\productions\_skill-covers"
tag = sys.argv[1] if len(sys.argv) > 1 else "v4"
only = sys.argv[2:]
ids = ["super-clone", "hypit-pipeline", "omni", "short-drama", "video-dewatermark"]
ims = []
for i in ids:
    if only and i not in only:
        continue
    p = os.path.join(OUT, "cover-%s-%s.png" % (i, tag))
    if os.path.isfile(p):
        ims.append((i, Image.open(p).convert("RGB")))
    else:
        print("缺:", p)
if not ims:
    sys.exit(1)
w = 880
hs = [int(i.height * w / i.width) for _, i in ims]
sheet = Image.new("RGB", (w, sum(hs) + 8 * (len(ims) - 1)), (24, 24, 26))
y = 0
for (_, im), h in zip(ims, hs):
    sheet.paste(im.resize((w, h), Image.LANCZOS), (0, y))
    y += h + 8
p = os.path.join(OUT, "_review-sheet-%s.png" % tag)
sheet.save(p)
print(p, sheet.size, "| 从上到下:", " → ".join(n for n, _ in ims))
