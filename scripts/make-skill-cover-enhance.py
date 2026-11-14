# -*- coding: utf-8 -*-
"""技能封面 v5「电商版」第 7 张：video-enhance（视频高清修复）。
单张 4 分。主体 = 一条模糊竖屏片被扫过变清晰（一条橙色光带扫过竖屏，近侧糊远侧清），
电商棚拍质感，无文字无商标无人脸。
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
dst = os.path.join(OUT, "cover-video-enhance-v5-ecom.png")

ECOM = ("professional e-commerce product photography, bright high-key seamless studio backdrop, "
        "softbox lighting with one clean contact shadow under the product, the product is unmistakably "
        "the hero and fills most of the frame, crisp edges and high colour clarity so it stays readable "
        "when shrunk to a very small thumbnail, the lower part of the frame kept calm and slightly "
        "deeper in tone so a title can be overlaid later, wide horizontal composition, ")
NEG = ("no letters, no words, no characters, no digits, no captions, no subtitles, no price tags, "
       "no badges, no logo, no brand marks, no readable packaging text, no people, no human faces, "
       "no dark cinematic colour grade, no film grain, no night street scene, no fog or haze")

BODY = ("a tall vertical phone screen standing upright in the centre showing an unbranded pair of "
        "black ankle socks on a living-room floor, with one thin warm orange band of light sweeping "
        "vertically across the screen: on the near side of the band the picture on that part of the "
        "screen is softly blurred and low-detail, on the far side the same screen is perfectly sharp "
        "and crisp, one continuous screen, the blur-to-sharp transition happens exactly at the orange band")

env = g.invoke("image.generate", "default", account="local",
               params={"prompt": ECOM + BODY + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
c = env.get("credits") or {}
print("[video-enhance] ok=%s 扣=%s 余额=%s" % (env.get("ok"), c.get("charged"), c.get("balance_after")), flush=True)
if not env.get("ok"):
    print("   错误:", str(env.get("error") or env.get("result"))[:240], flush=True)
else:
    print("   落盘:", dst, os.path.getsize(dst) // 1024, "KB", flush=True)
