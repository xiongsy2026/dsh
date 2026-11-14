#!/usr/bin/env python
"""技能封面 v4：5 张【电影剧照质感】的能力大片（2026-10-04 用户定：还是用视频大片图片代替）。

v1/v2 输在白底器物（像同一组产品图），v3 输在素材本身（我们真跑出来的帧多是白底或带真人）。
这版照小云雀那三张卡学到的两条一起用：
  ① 机制画进画面里（同机位换品 / 胶片格分裂 / 擦除前后对比）——但用【场景内的光与物体】表达，不贴几何符号；
  ② 主体处在环境里 + 电影打光调色（anamorphic、体积雾、青橙调、颗粒）——满幅，不留大片负空间。

🔴 老规矩一条没放松：prompt 里不出现任何字面量（颜色码/比例数字/数量字都可能被画出来）；
   不写任何真实品牌；人物一律写成虚构电影角色（不用真人肖像）；出图后我逐张人眼过字/商标/跑偏。
产出：productions/_skill-covers/cover-<skill>-v4.png
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
# 统一质感尾注（这就是"像一家"的来源：同一套镜头语言，不是同一句负面词）
CINE = ("cinematic film still, shot on 35mm anamorphic lens, teal shadows with warm amber highlights, "
        "volumetric haze, gentle film grain, shallow depth of field, subject placed inside a real "
        "environment rather than on a plain backdrop, one warm orange light accent as the only "
        "saturated colour, lower part of the frame naturally darker so a title can be overlaid later, ")
NEG = ("no letters, no words, no characters, no digits, no captions, no subtitles, no watermark text, "
       "no logo, no brand marks, no recognizable real product packaging, no celebrity likeness, "
       "no flat white studio backdrop, no collage layout")

SHOTS = [
    ("super-clone",
     "a single frame split into two halves by a thin vertical seam of warm orange light: on the left a "
     "night street market scene where a woman in a red coat holds an unbranded matte black tumbler, on "
     "the right the exact same framing, same camera height, same crowd and same neon signage, but she "
     "holds an unbranded clear glass jar instead, so the only thing that changed is the product in her hand"),
    ("hypit-pipeline",
     "inside a dark editing suite, a long horizontal ribbon of glowing picture frames floats in the air "
     "above a desk, each frame showing a different shot of the same rain-soaked neon alley, and one frame "
     "near the middle is visibly dividing into two as a seam of warm orange light runs through it, soft "
     "projected haze and lens flares around the ribbon"),
    ("omni",
     "in a dim photo studio, a printed square product photograph is clipped to a light stand on the left, "
     "while on the right a tall vertical phone-shaped screen shows the very same unbranded product as a "
     "dramatic hero shot with swirling mist and motion blur, a warm orange beam of light arcing out of "
     "the photograph and into the vertical screen"),
    ("short-drama",
     "a tense confrontation across a dining table in a dim apartment at dusk, a young woman on one side "
     "and an older man on the other, both in three-quarter profile and backlit, while between them a "
     "small unbranded product box stands on the table catching a single shaft of warm window light with "
     "dust motes floating in the beam, making the product the thing they are both looking at"),
    ("video-dewatermark",
     "a sweeping desert dune landscape at golden hour, with a wide vertical band of warm orange light "
     "being wiped across the frame: on the left side of the band several soft translucent frosted "
     "rounded patches float above the image obscuring the sand, on the right side the same dunes are "
     "perfectly clean and crisp, and a round glass loupe hovers near the seam revealing fine sand grain"),
]

for sid, body in SHOTS:
    dst = os.path.join(OUT, "cover-%s-v4.png" % sid)
    env = g.invoke("image.generate", "default", account="local",
                   params={"prompt": CINE + body + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
    c = env.get("credits") or {}
    print("[%-18s] ok=%s 扣=%s 余额=%s" % (sid, env.get("ok"), c.get("charged"), c.get("balance_after")), flush=True)
    if not env.get("ok"):
        print("     错误:", str(env.get("error") or env.get("result"))[:240], flush=True)
        continue
    print("     落盘:", dst, os.path.getsize(dst) // 1024, "KB", flush=True)

print("\n下一步：scripts/_sheet.py 摊成一张图逐张人眼过（有没有字/商标/白底/跑偏），过了才挂 dock。")
