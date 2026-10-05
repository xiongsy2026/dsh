#!/usr/bin/env python
"""技能封面 v5「电商版」：主体是货、视觉母语是电商，不是影视（PRD §8.6 的六条取向）。

v4 那套是电影剧照（青橙调/体积雾/颗粒/人物对峙）——好看，但那是【内容创作工具】的语言。
我们的客户是电商运营，他判断一个能力值不值，看的是"我的货放进去好不好看、像不像能出单的画面"。
六条取向（逐条落到下面的提示词里）：
  1 商品是主角（占画面重心，人最多当使用情境）      2 机制符号换成电商语汇（竖屏信息流 / 转台 / A-B 变体）
  3 光用商业棚拍 + 真实场景，不用电影暗调            4 缩略图优先：228×132 里也要一眼认出品类
  5 🔴 合规：图上不出现极限词/功效/价格数字，连形状化角标都不做
  6 品类锚定我们真做过的货（软糖 / 鞋 / 袜子 / 包 / 连衣裙）

🔴 老规矩：提示词里不写任何字面量（颜色码/比例数字/数量字都可能被画出来）；不写真实品牌；
   出图后我逐张人眼过（有没有字 / 商标 / 人脸 / 又跑回电影感）。

用法：python make-skill-covers-v5-ecom.py          # 只出 2 张样张（8 分）
      python make-skill-covers-v5-ecom.py --all    # 全套 5 张（20 分）
产出：productions/_skill-covers/cover-<skill>-v5-ecom.png（v4 那套保留，改名可回退）
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\video-skill")
from backend import gateway as g  # noqa: E402

OUT = r"C:\dsh\productions\_skill-covers"
ALL = "--all" in sys.argv

# 统一"电商棚拍"质感尾注 —— 让五张像一家的来源（不是同一句负面词，是同一套镜头语言）
ECOM = ("professional e-commerce product photography, bright high-key seamless studio backdrop, "
        "softbox lighting with one clean contact shadow under the product, the product is unmistakably "
        "the hero and fills most of the frame, crisp edges and high colour clarity so it stays readable "
        "when shrunk to a very small thumbnail, the lower part of the frame kept calm and slightly "
        "deeper in tone so a title can be overlaid later, wide horizontal composition, ")
NEG = ("no letters, no words, no characters, no digits, no captions, no subtitles, no price tags, "
       "no badges, no logo, no brand marks, no readable packaging text, no people, no human faces, "
       "no dark cinematic colour grade, no film grain, no night street scene, no fog or haze")

SHOTS = [
    ("super-clone",
     "two identical phone-sized vertical video frames standing side by side, both showing the exact same "
     "camera angle of unbranded white sneakers on a slow-turning acrylic turntable, and the only thing that "
     "differs between the two frames is the product itself, one thin warm orange seam of light running "
     "between the frames"),
    ("omni",
     "an unbranded matte cosmetic jar filled with red gummy candies sits on the left of the frame, and to "
     "its right a tall vertical phone screen shows the very same jar in a bright kitchen-counter lifestyle "
     "shot, a thin warm orange arc of light curving out of the jar into that vertical screen"),
    ("hypit-pipeline",
     "one tall vertical phone screen in the centre showing an unbranded canvas tote bag on a turntable, "
     "and behind it several identical vertical screens fan out to both sides like a grid of ad variants, "
     "each one slightly different in angle or background, one warm orange outline highlighting the front "
     "screen"),
    ("short-drama",
     "a bright bathroom scene where an unbranded jar of red gummy candies sits on the edge of a mirror "
     "shelf catching the strongest light in the frame, with two blurred human silhouettes facing each "
     "other behind it out of focus, the product between them clearly the thing the shot is about"),
    ("video-dewatermark",
     "a tall vertical phone screen showing an unbranded pair of white sneakers on a bright studio "
     "backdrop, with a wide vertical band of warm orange light being wiped across it: on the near side of "
     "the band several soft frosted rounded patches sit flat against the picture like stuck-on stickers, "
     "on the far side the same screen is perfectly clean"),
]

PICKS = SHOTS[:2] if not ALL else SHOTS
print("模式:", "全套 %d 张" % len(PICKS) if ALL else "样张 2 张（先定风格，点头再 --all 出全套）")
for sid, body in PICKS:
    dst = os.path.join(OUT, "cover-%s-v5-ecom.png" % sid)
    env = g.invoke("image.generate", "default", account="local",
                   params={"prompt": ECOM + body + NEG, "ratio": "16:9", "size": "2K", "output_path": dst})
    c = env.get("credits") or {}
    print("[%-18s] ok=%s 扣=%s 余额=%s" % (sid, env.get("ok"), c.get("charged"), c.get("balance_after")), flush=True)
    if not env.get("ok"):
        print("     错误:", str(env.get("error") or env.get("result"))[:240], flush=True)
        continue
    print("     落盘:", dst, os.path.getsize(dst) // 1024, "KB", flush=True)

print("\n下一步: python scripts/_sheet.py v5-ecom 摊图人眼过（字/商标/人脸/有没有又跑回电影感）")
