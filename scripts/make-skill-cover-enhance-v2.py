# -*- coding: utf-8 -*-
"""video-enhance 封面 v2：before/after 对比构图（左模糊右清晰，中间橙色分割光带）。"""
import json
import os
import sys

from pathlib import Path

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway  # noqa: E402

prompt = (
    "a single wide image showing a before-and-after comparison of one unbranded white sneaker product photo: "
    "the left half is a soft, blurred, low-detail, pixelated and washed-out version of the sneaker, "
    "the right half is the exact same sneaker in the exact same pose, perfectly sharp, crisp, high-detail and clean; "
    "a thin bright orange vertical seam of light divides the two halves down the middle; "
    "bright high-key seamless white studio backdrop, softbox lighting, wide horizontal composition, "
    "the bottom of the frame calm and slightly deeper in tone so a title can be overlaid later"
)
neg = ("no letters, no words, no characters, no digits, no captions, no subtitles, no price tags, "
       "no badges, no logo, no brand marks, no readable packaging text, no people, no human faces, "
       "no dark cinematic colour grade, no film grain, no night street scene, no fog or haze")

dst = os.path.join(WS, r"productions\_skill-covers\cover-video-enhance-c2.png")
env = gateway.invoke("image.generate", "default", account="local",
                     params={"prompt": prompt + ", " + neg, "ratio": "16:9", "size": "2K", "output_path": dst})
c = env.get("credits") or {}
print("[video-enhance-c2] ok=%s 扣=%s 余额=%s" % (env.get("ok"), c.get("charged"), c.get("balance_after")))
if not env.get("ok"):
    print("   错误:", str(env.get("error") or env.get("result"))[:240])
else:
    print("   落盘:", dst, os.path.getsize(dst) // 1024, "KB")
