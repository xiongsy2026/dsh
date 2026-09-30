"""提示词拼装 + 剧本 JSON 校验。条款文本与 skills/product-video-pipeline/references/prompts.md 保持一致。"""

import re
from pathlib import Path

CONSISTENCY_CLAUSE = (
    "Keep the product's appearance, material, color and proportions exactly consistent with the "
    "reference product image; the person (if any) is the SAME person as in the reference frame - "
    "identical face, hairstyle, body type and outfit; keep the set dressing, lighting and color "
    "palette continuous with the reference frame. Vertical 9:16 composition, photorealistic "
    "cinematic still. Change only the action and camera position; never change the subject's "
    "identity or the environment."
)

NOT_PRESENT_CLAUSE = (
    "IMPORTANT: the product does NOT appear in this frame. No necklaces, bracelets, pendants, "
    "earrings or any jewelry of any kind on the person's neck, wrists, ears or body - bare skin, "
    "nothing worn. No decorative charms or accessories on the clothing. No product-related objects "
    "anywhere in the frame. No text, no watermarks."
)

BRIDGE_CLAUSE = (
    "This is a segment-transition frame: the image must stay continuous with the previous/next "
    "frame - same moment from a different angle, or a natural continuation of the same action. "
    "Keep pose logic, scene and lighting consistent with the adjacent frame; no scene jump."
)

NO_TEXT_CLAUSE = (
    "NO text, no words, no slogans, no overlays, no graphics, no watermarks anywhere in the image. "
    "Natural hands with correct anatomy."
)

VIDEO_ASSEMBLY = (
    "The people and the story carry the frame; where the product is present it already exists in "
    "the frame naturally - never push in on it or advertise it. Product appearance must match the "
    "reference keyframes exactly. The person's identity (face, hairstyle, outfit) and the set "
    "dressing must not change mid-shot. Motion only: show camera movement and action, no cuts to "
    "new scenes. Camera rhythm follows each beat's emotion - quick beats cut fast, slow beats push "
    "in gently, the climax holds. Vertical 9:16, photorealistic, smooth motion, no text overlays, "
    "no watermarks."
)

PRESENT_REQUIRED = {"beat_function", "story_beat", "product_presence", "shot_motivation",
                    "emotion", "dialogue", "sound", "duration", "keyframe_prompt"}
STORY_REQUIRED = {"beat_function", "story_beat", "cast", "shot_motivation",
                  "emotion", "dialogue", "sound", "duration", "keyframe_prompt"}
VALID_PRESENCE = {"not_present", "natural", "focus"}
CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def word_count(text):
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text or ""))


PRODUCT_LOCK_CLAUSE = (
    "PRODUCT FIDELITY RULE (checkable, non-negotiable): reproduce the product exactly as shown in the "
    "image identified above as the PRODUCT photo. You may change ONLY its orientation, position and the "
    "way it is held or worn. Never add, remove, rename or restyle any part. The part count, the part "
    "shapes, the engraving/text and the material finish must match that image item by item. If a part is "
    "hidden by the hand or the angle, do not invent a different part there - hide it or show it partially."
)

CARD_FIELDS = ("name_en", "name_zh", "summary_en", "summary_zh", "orientation", "material",
               "colors", "engraving", "size_ref")


def build_product_card_messages(image_uri, notes="", extra_uris=None):
    """Stage 0：让多模态模型看着产品图写出「产品基准卡」。可给多张（主图 + 细节图）。"""
    uris = [image_uri] + [u for u in (extra_uris or []) if u]
    n = len(uris)
    head = ("这是要植入广告视频的产品实拍图（共 %d 张：第 1 张是主图，其余是细节/局部图）。"
            "请把这几张图合起来当作同一个产品来取证，"
            "同一部件在多张图里说法要统一；只在某张图上看得清的部件，以那张图为准。" % n) if n > 1 else \
        "这是要植入广告视频的产品实拍图。"
    text = """%s请只根据图里真实看到的东西，输出这张 JSON（字段名完全一致，不要多余文字）：
{
  "name_en": "英文产品名（简短）",
  "name_zh": "中文名",
  "parts": [
    {"en": "英文：单个部件的名称+形状+位置+朝向+工艺，例如 'a crescent moon opening to the RIGHT, double-row pavé CZ along the outer edge'", "zh": "中文对照"}
  ],
  "orientation": "英文：主朝向/开口方向/正反面判据（如 the crescent opens to the viewer's right）",
  "material": "英文：材质与表面工艺（如 925 polished sterling silver, pavé cubic zirconia）",
  "colors": "英文：配色（金属色+宝石色+其他）",
  "engraving": "英文：图上可见的刻字/图案/数字，逐字写出；看不清就写 none visible",
  "size_ref": "英文：相对尺寸判据（如 the disc is about the width of two fingertips）",
  "summary_en": "英文 40-70 词：把上面各项压成一段可直接贴进生图提示词的产品基准描述",
  "summary_zh": "summary_en 的中文逐句对照",
  "uncertain": ["英文：图上看不清或无法判断的地方，列出来让人工确认"]
}
要求：只写你在图里真实看到的东西，不要推测型号、材质纯度或图上看不到的功能；看不清就写进 uncertain。%s""" % (
        head, ("\n用户补充信息（仅作旁证，与图冲突时以图为准，并把冲突写进 uncertain）：" + notes) if notes else "")
    parts = [{"type": "image_url", "image_url": {"url": u}} for u in uris]
    parts.append({"type": "text", "text": text})
    return [{"role": "system", "content": "你是产品视觉取证员，负责把产品实拍图转成一份可核查的特征基准卡。只输出 JSON。"},
            {"role": "user", "content": parts}]


def card_line(card):
    """基准卡 -> 逐帧复述用的英文产品清单（部件逐条编号，便于模型对齐）。"""
    if not card:
        return ""
    bits = []
    if card.get("summary_en"):
        bits.append(str(card["summary_en"]).strip())
    parts = [p for p in (card.get("parts") or []) if isinstance(p, dict) and p.get("en")]
    if parts:
        bits.append("Parts inventory: " + "; ".join(
            "(%d) %s" % (i + 1, str(p["en"]).strip()) for i, p in enumerate(parts)))
    for key, label in (("orientation", "Orientation"), ("material", "Material"),
                       ("colors", "Colors"), ("engraving", "Engraving/marks"), ("size_ref", "Size reference")):
        if card.get(key):
            bits.append("%s: %s" % (label, str(card[key]).strip()))
    return ("Product (must match the PRODUCT reference image exactly): " + " | ".join(bits)) if bits else ""


CARD_KIND_LABEL = {"product": "Product", "character": "Character", "prop": "Prop"}
CARD_KIND_REF = {"product": "the PRODUCT reference image", "character": "the CHARACTER reference image",
                 "prop": "the PROP reference image"}


def card_lock_lines(card, kind="product"):
    """资产基准卡 -> 固定产品锁块与负面块；优先读取 Stage 0 生成的 lock_block，避免逐帧自由描述产品外观。"""
    if not card:
        return []
    lines = []
    lock = str(card.get("product_lock_block") or "").strip()
    if kind == "product":
        if lock:
            lines.append("PRODUCT LOCK BLOCK (one-time source of truth, reused verbatim for every frame): " + lock)
        negative = str(card.get("product_negative_block") or "").strip()
        if negative:
            lines.append("PRODUCT NEGATIVE BLOCK: " + negative)
        return lines
    desc = str(card.get("summary_en") or card.get("desc") or "").strip()
    if desc:
        label = CARD_KIND_LABEL.get(kind, "Item")
        lines.append("%s (must match %s exactly): %s" % (label, CARD_KIND_REF.get(kind, "the reference image"), desc))
    return lines


def card_line(card, kind="product", name=""):
    """资产基准卡 -> 逐帧复述用的英文清单（部件/特征逐条编号，便于模型对齐）。"""
    if not card:
        return ""
    lock_lines = card_lock_lines(card, kind)
    if lock_lines:
        return " | ".join(lock_lines)
    bits = []
    if card.get("summary_en"):
        bits.append(str(card["summary_en"]).strip())
    parts = [p for p in (card.get("parts") or []) if isinstance(p, dict) and p.get("en")]
    if parts:
        bits.append("Parts inventory: " + "; ".join(
            "(%d) %s" % (i + 1, str(p["en"]).strip()) for i, p in enumerate(parts)))
    for key, label in (("orientation", "Orientation"), ("material", "Material"),
                       ("colors", "Colors"), ("engraving", "Engraving/marks"), ("size_ref", "Size reference")):
        if card.get(key):
            bits.append("%s: %s" % (label, str(card[key]).strip()))
    if not bits:
        return ""
    label = CARD_KIND_LABEL.get(kind, "Item")
    who = '%s "%s"' % (label, name) if name else label
    return ('%s (must match %s exactly): %s' % (who, CARD_KIND_REF.get(kind, "the reference image"),
                                                " | ".join(bits)))


def asset_feature(run, aid):
    """单个资产的特征行：优先用它的基准卡，退回剧本 subject_bible 里的描述。"""
    a = run.assets().get(aid) or {}
    kind = a.get("kind", "product")
    # 产品模式不加名字前缀，保持与旧版逐字一致
    name = "" if kind == "product" and run.mode == "product" else a.get("name", "")
    line = card_line(a.get("card") or {}, kind, name)
    if line:
        return line
    if a.get("kind") == "product" or run.mode == "product":
        return _bible_product_line(run.script)
    desc = (a.get("card") or {}).get("desc") or a.get("name") or ""
    return ('Character "%s" (must match the CHARACTER reference image exactly): %s' % (a.get("name", ""), desc)
            if a.get("kind") == "character" and desc else "")


def _bible_product_line(script):
    bible = (script or {}).get("subject_bible", {}) or {}
    prod = bible.get("product")
    if isinstance(prod, dict):
        parts = []
        for key in ("name", "appearance", "material", "features", "notes"):
            if prod.get(key):
                parts.append(str(prod[key]).strip())
        if parts:
            return "Product: " + " ".join(parts)
        return json_dumps_short(prod)
    return ("Product: " + str(prod).strip()) if prod else ""


def beat_cast(run, beat):
    """这一拍该出现哪些资产、该缺席哪些。
    产品模式由 product_presence 推导，只看产品这一组（主图 + 细节图算同一个主体，
    角色图是参考素材不是「出场名单」，都不该生成缺席条款），行为与旧版一致；
    剧情模式读每拍的 cast（角色/道具名）。"""
    assets = run.assets()
    if run.mode == "product":
        presence = str(beat.get("product_presence", "natural"))
        group = [a["id"] for a in run.product_group()]
        main = ["product"] if "product" in group else group[:1]
        on = presence in ("natural", "focus")
        return (main if on else []), ([] if on else main)
    cast = beat.get("cast")
    if isinstance(cast, str):
        cast = [c for c in re.split(r"[,，、;；\n]+", cast) if c.strip()]
    present = []
    for c in (cast or []):
        a = run.asset_by_name(str(c).strip())
        if a and a["id"] not in present:
            present.append(a["id"])
    return present, [a for a in assets if a not in present]


def product_feature_list(script, card=None):
    if card:
        line = card_line(card, "product")
        if line:
            return line
    bible_line = _bible_product_line(script)
    if card:
        return _sanitize_keyframe_product_text(bible_line, _product_ref_token(script, card))
    return bible_line


def json_dumps_short(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)[:1200]


def subject_summary(script):
    bible = (script or {}).get("subject_bible", {}) or {}
    bits = []
    for key in ("person", "character", "scene", "wardrobe", "lighting", "tone"):
        val = bible.get(key)
        if isinstance(val, dict):
            val = ", ".join("%s %s" % (k, v) for k, v in val.items())
        if val:
            bits.append("%s: %s" % (key, str(val).strip()))
    return "; ".join(bits)


COUNTRY_LANG = {
    "中国": "Chinese Mandarin", "美国": "American English", "英国": "British English",
    "加拿大": "Canadian English", "澳大利亚": "Australian English", "新西兰": "Australian English",
    "爱尔兰": "British English", "印度": "Indian English", "日本": "Japanese", "韩国": "Korean",
    "德国": "German", "法国": "French", "西班牙": "Spanish", "墨西哥": "Mexican Spanish",
    "巴西": "Brazilian Portuguese", "通用": "",
}

COUNTRY_NOTE = {
    "中国": "中国都市生活场景（小区公寓、飘窗、木质书桌、地铁通勤、办公室工位）、节日按 520/七夕/情人节/纪念日/生日，情感表达含蓄但真诚（不善言辞的男朋友、把话藏在礼物里），人物为东亚/中国面孔，台词用中文口语（如「我都不知道说什么好」「你怎么知道我想要这个」），避免翻译腔；平台多为抖音/小红书时节奏更快、前 1 秒必须有钩子",
    "美国": "美国家居（loft/apartment、string lights、kitchen island）、节日按 Valentine's Day / anniversary / Thanksgiving / Black Friday，情感表达直接外放，口语用美式（apartment / line / store）",
    "英国": "英式家居与街景（terraced house、kettle、pub garden），口语用英式（flat / queue / shop），情感克制但真诚",
    "加拿大": "北美家居，季节与冬季光线可用，口语美式偏加拿大用法",
    "澳大利亚": "澳洲住宅与户外生活方式（verandah、BBQ、beach light），口语偏澳式",
    "日本": "日式住宅（玄关脱鞋、榻榻米/紧凑公寓、温泉旅馆）、季节感（お盆/クリスマス/バレンタイン）、情感表达含蓄、留白多",
    "韩国": "韩式公寓与咖啡馆文化、节日（발렌타인데이/백화점），情感表达细腻",
    "德国": "德式家居与节奏（Feierabend、周末市集），直接务实的表达方式",
    "法国": "法式公寓与咖啡馆、浪漫克制的表达，节日按 la Saint-Valentin",
    "西班牙": "西语区家庭生活方式（晚晚餐、家庭聚会），热情外放",
    "墨西哥": "拉美家庭与节日（quinceañera、Día de Muertos、Navidad），色彩浓烈、家人群像",
    "巴西": "巴西家庭与节庆氛围（churrasco、Carnaval），色彩与光线浓烈",
    "印度": "印度家庭与节日（Diwali、婚礼季），色彩浓艳，家庭群像",
    "通用": "欧美通用家居与节日语境",
}

STORY_SCRIPT_SYSTEM = (
    "你是竖屏 9:16 短剧编剧兼导演，只写故事：有人物、有冲突、有情感弧线，观众因为故事留下来。"
    "这是纯剧情内容，不是广告——不出现商品、不喊卖点、不做购买引导。"
    "画面里出现的人物与道具必须严格沿用给定的名册与基准卡，跨镜头保持同一个人、同一件东西。"
    "多段视频把全局叙事弧线跨段展开，每段是一幕，有自己的小弧线但服务于全局起承转合。只输出 JSON。"
)

SCRIPT_SYSTEM = (
    "你是资深短视频编剧兼导演，精通竖屏 9:16 叙事广告创作。你的信条是：观众不为产品停留，但会为一个好故事停留到底。"
    "你写的是故事——有人物、有冲突、有情感弧线、有让人想看下去的看点。产品不是主角，只是自然出现在故事里。"
    "前 1.5 秒用故事钩子抓住人，不用产品。高潮属于人的情感，产品在那个时刻恰好在场。"
    "多段视频（20s+）把全局叙事弧线跨段展开——每段是一幕，有自己的小弧线，但服务于全局起承转合。"
    "只输出 JSON，不要输出任何其他内容。"
)

STRUCTURE_TABLES = {
    1: """10s（1 段 6 拍）可选结构：
- 悬念揭秘：钩子→铺垫→悬念→产品触发揭秘→情感高潮→CTA｜产品第 4 拍入场｜节拍 1/1.5/1.5/2/2.5/1.5
- 痛点解决：痛点→困境→挣扎→产品解围→满足→CTA｜第 4 拍｜1.5/1.5/1.5/2/2/1.5
- 情感故事：场景→需求→寻找→产品承载情感→高潮→CTA｜第 4 拍｜1.5/1.5/1.5/2.5/2/1
- UGC种草：真实场景→人物故事→自然提及→产品融入生活→个人感受→推荐｜第 3 拍｜1.2/1.5/1.5/1.5/2.5/1.8
- 高级质感：氛围→环境叙事→光影铺垫→产品自然入境→定格→品牌｜第 4 拍｜1.5/1.5/1.8/2/2/1.2
- 对比反转：现状→冲突→产品介入→反转→震撼→CTA｜第 3 拍｜1.2/1.2/1.5/2.5/2/1.6""",
    2: """20s（2 段 12 拍）：幕 1 讲故事+埋悬念，幕 2 产品入场+情感高潮+收尾。
- 悬念揭秘｜幕1 hook→铺垫→冲突→悬念加深→留白→衔接；幕2 揭秘触发(产品入场)→放大→情感高潮→感动→余韵→CTA｜产品全局第 7 拍
- 痛点解决｜幕1 痛点→困境→挣扎加深→困境顶点→留白→衔接；幕2 产品解围→效果→转变→满足→推荐→CTA｜全局第 7 拍
- 情感故事｜幕1 场景→人物→情感需求→寻找→期待→衔接；幕2 产品承载→高潮→感动→品牌升华→余韵→CTA｜全局第 7 拍
- UGC种草｜幕1 真实分享→人物故事→生活场景→自然提及→留白→衔接；幕2 产品融入→惊喜→个人感受→推荐→互动→CTA｜全局第 7 拍
- 对比反转｜幕1 现状困境→冲突→产品介入→初步反转→留白→衔接；幕2 完整反转→震撼→见证→满足→CTA｜全局第 3 拍
节拍建议：幕1 1.2/1.5/1.5/1.8/2/2，幕2 1.2/1.5/2.5/2/1.8/1""",
    3: """30s（3 段 18 拍）：幕 1 讲故事，幕 2 产品入场+转折，幕 3 情感高潮+收尾。
- 悬念揭秘｜幕1 hook→铺垫→冲突→暗示→悬念→衔接；幕2 产品触发揭秘→部分揭示→转折→加深→悬念升级→衔接；幕3 全面揭秘→情感高潮→感动→价值升华→CTA→品牌｜幕 2 第 1 拍
- 痛点解决｜幕1 痛点→困境→挣扎→顶点→留白→衔接；幕2 产品出现→初步效果→转变→见证→深入→衔接；幕3 完美效果→满足→证言→品牌承诺→CTA｜幕 2 第 1 拍
- 情感故事｜幕1 场景→人物→需求→寻找→期待→衔接；幕2 产品承载→使用→情感积累→转折→余韵→衔接；幕3 高潮→感动→品牌升华→CTA→收尾定格｜幕 2 第 1 拍
- UGC种草｜幕1 真实分享→人物故事→生活场景→暗示→留白→衔接；幕2 产品融入→惊喜→深度体验→个人感受→悬念→衔接；幕3 全面推荐→互动→总结→CTA→收尾｜幕 2 第 1 拍
节拍建议：幕1 1.2/1.5/1.5/1.5/2/2.3，幕2 1.3/1.5/1.5/1.7/2/2，幕3 1.2/1.5/2.5/2/1.8/1""",
}

# 结构 → 6 拍情绪/节拍弧线（与段数无关，产品模式与剧情模式都能用；
# 产品入场的具体拍号仍由 STRUCTURE_TABLES 与各模式规则约束）。
GENRE_ARCS = {
    "悬念揭秘": "异常钩子→线索铺垫→悬念加深→触发揭秘→情感高潮→CTA/余韵",
    "痛点解决": "痛点呈现→困境加剧→挣扎无解→方案登场→效果满足→推荐CTA",
    "情感故事": "场景铺陈→情感需求→寻找期待→情感承载→情绪高潮→温暖收尾",
    "UGC种草": "真实开场→人物故事→生活场景→自然融入→个人感受→真诚推荐",
    "高级质感": "氛围定调→环境叙事→光影铺垫→主体登场→质感定格→品牌余韵",
    "对比反转": "现状困境→冲突升级→介入→预期反转→震撼结果→CTA",
    "喜剧": "铺垫日常→反常初现→误会连环升级→荒诞峰值→神反转→会心收尾",
    "悬疑": "诡异钩子→线索铺陈→疑点加深→误导岔开→逼近真相→揭底反转",
    "治愈": "低气压开场→细微善意→陪伴累积→柔软转折→情绪释放→平静余韵",
    "热血励志": "困境起步→接连受挫→咬牙坚持→破局前夜→爆发高潮→达成升华",
    "恐怖惊悚": "平静日常→不安暗示→异象升级→惊吓峰值→亡命或反转→余悸收尾",
    "奇幻脑洞": "现实出现裂缝→奇想展开→规则颠覆→脑洞高潮→回落现实→回味",
    "反转": "铺垫常规预期→第一个意外→连锁反转→情绪顶点→真相收尾→余韵",
}

STRUCTURE_NAMES = list(GENRE_ARCS.keys())

# 两种模式的结构清单分开：产品模式=带货向 6 种；剧情模式=类型/情绪向 8 种（喜剧、悬疑等）。
PRODUCT_STRUCTURES = ["悬念揭秘", "痛点解决", "情感故事", "UGC种草", "高级质感", "对比反转"]
STORY_STRUCTURES = ["喜剧", "悬疑", "治愈", "热血励志", "恐怖惊悚", "奇幻脑洞", "情感故事", "反转"]


def structures_for(mode):
    return STORY_STRUCTURES if (mode or "product") == "story" else PRODUCT_STRUCTURES


def structure_hint(structure, mode="product"):
    """把选中的结构展开成一句可执行的 6 拍弧线，塞进剧本提示词。"""
    names = structures_for(mode)
    name = (structure or "").strip()
    arc = GENRE_ARCS.get(name)
    if arc:
        return "【结构「%s」的六拍弧线（照这个情绪走向铺 6 拍，段多则把弧线在两/三段里延展）】%s" % (name, arc)
    if name:
        return "【自定结构「%s」】自己把它拆成清晰的六拍情绪弧线，第一拍必须是钩子(hook)。" % name
    return "【可选叙事结构】%s——用户没指定就挑一个最贴内容的，并把结构名写进 narrative_structure。" % "、".join(names)

SCRIPT_SCHEMA = """{
  "title": "广告标题",
  "platform": "目标平台",
  "narrative_structure": "选用的叙事结构名",
  "duration": {duration},
  "segments_count": {segments},
  "global_arc": "全局故事弧线（1-2 句）",
  "story_hook": "第 1 拍用什么人物/冲突/悬念抓人（不涉及产品）",
  "emotional_core": "触动人的是什么情感（怀旧/治愈/惊喜/共鸣/向往/心疼）",
  "product_entry": "产品如何被角色或情节自然需要而出现",
  "subject_bible": {
    "product": "英文：产品外观与视觉特征，部件名称+形态+朝向+材质+配色（关键帧一致性锚点）",
    "product_zh": "上面这条的中文对照",
    "person": "英文：性别、年龄段、脸型、发型、体型、服装配色款式；无人物写 no person",
    "person_zh": "上面这条的中文对照",
    "scene": "英文：地点、陈设、光线基调、色调",
    "scene_zh": "上面这条的中文对照",
    "tone": "整体情绪基调"
  },
  "segments": [
    {"segment": 1, "phase": "阶段名", "segment_arc": "本段小弧线 + 如何衔接全局",
     "beats": [
       {"id": 1, "segment": 1, "time": "0.0-1.2s", "duration": 1.2,
        "beat_function": "hook|setup|build|suspense|foreshadow|bridge|reveal|twist|escalate|climax|emotion|satisfaction|cta|brand",
        "story_beat": "中文：本拍人物在做什么、情感如何变化（叙事主体）",
        "product_presence": "not_present|natural|focus",
        "shot": "景别与角度", "shot_motivation": "为什么用这个景别",
        "action": "画面内容与动作（人物和故事作主语）",
        "emotion": "本拍情绪标签", "dialogue": "英文台词（无则写 无）", "sound": "音乐/音效提示",
        "keyframe_prompt": "ENGLISH 40-90 words: subject + action + environment + light + palette, restating person & scene anchors",
        "keyframe_prompt_zh": "上面这条英文提示词的中文逐句对照（给中国人审核用，不会发给生图模型；不要机翻腔）"}
     ]}
  ],
  "slogan": "结尾文案", "cta": "行动号召"
}"""

SCRIPT_RULES = """硬性要求（违反即作废）：
1. 剧情优先：这首先是故事，其次才是广告。前 1.2 秒产品不出现，story_hook 不涉及产品。
2. product_presence：前 3 拍必须 not_present（除非所选结构表另有规定）；产品入场后从 natural 开始，只在 climax/cta 拍 focus。
3. segments 数组长度 = {segments}，每段 beats 恰好 6 拍，id 全局连续 1..{total}。
4. 每段 6 拍 duration 之和精确 = 10.0（钩子 1-1.2s，高潮 2-2.5s，收尾 1-1.5s，衔接拍 2s）。
5. 全局第 1 拍 beat_function = hook；cta/brand 只允许出现在全局最后 1-2 拍。
6. 每段末拍（id=6K）与下段首拍（id=6K+1）beat_function 标 bridge，画面必须连续（同一时刻不同角度或同一动作的自然延续）。
7. 情绪弧线跨全部 {total} 拍递进，不得每段重置；climax 拍情绪强度最高，且来自人的情感。
8. 人物一致性：全部 {total} 拍同一个人，脸型/发型/体型/服装全程不变；场景与光线基调全程不变（确需换景要在 subject_bible 与 action 显式写明且只换一次）。
9. keyframe_prompt 必须是英文 40-90 词（生图模型对英文更稳），且每条都要复述 subject_bible 的人物外观与场景要点；subject_bible 的 product/person/scene 也用英文。每条 keyframe_prompt 后面必须紧跟一条 keyframe_prompt_zh，是它的中文逐句对照（与英文一一对应，供人审核，不会发给模型）；subject_bible 同时给 product_zh / person_zh / scene_zh 三个中文对照字段。
10. {culture}
11. 只输出 JSON 本身，不要 markdown 代码块以外的任何解释文字。"""


CAMERA_SYSTEM = (
    "你是专业运镜指导，为竖屏叙事广告撰写运镜提示词。你的运镜服务于故事节奏与人物情感——故事钩子用快推快拉制造冲击，"
    "情感高潮用缓推停留让情绪落地，过渡拍流畅连接。运镜跟着人物走，产品出现时不刻意推近，保持故事的自然感。"
    "多段视频的运镜有全局节奏感：铺垫段流畅推进，转折段运镜变化增多，高潮段缓推停留。"
)


def build_camera_messages(run, seg_index, image_uris):
    """Stage 4 多模态：该段 6 张关键帧 + 剧本文本。输出英文 150-300 词。"""
    script = run.script or {}
    segs = run.segments()
    seg = segs[seg_index - 1]
    total = len(segs)
    beats = [{k: b.get(k) for k in ("id", "beat_function", "story_beat", "product_presence", "shot",
                                    "shot_motivation", "emotion", "dialogue", "sound", "duration")}
             for b in seg.get("beats", [])]
    import json
    phase = seg.get("phase") or seg.get("segment_phase") or ""
    start = sum(float(b.get("duration", 0)) for prev in segs[:seg_index - 1] for b in prev.get("beats", []))
    end = start + sum(float(b.get("duration", 0)) for b in beats)

    note = ""
    if total > 1:
        bits = ["本段为第 %d/%d 段（%s阶段），段间衔接靠关键帧画面的连续性保证，不需要额外转场特效。" % (seg_index, total, phase)]
        if seg_index > 1:
            bits.append("本段开头须衔接上一段末尾画面，运镜从上一段的结束构图自然过渡。")
        if seg_index < total:
            bits.append("本段末尾须为下一段开头留出画面连续点，运镜收束平稳。")
        note = "".join(bits)

    text = """以上是第 %d/%d 段（%.1f-%.1f 秒）的 6 个关键帧（按时间顺序）与剧本：
%s

本段在全局弧线中的阶段：%s
全局弧线概述：%s

请输出一段**英文**运镜提示词（150-300 words），描述这 10 秒内的镜头运动。要求：
- 运镜跟随人物和故事，不刻意推向产品；产品出现时保持镜头的自然叙事节奏
- 运镜须服务于各拍的 beat_function 与 emotion：hook 拍用快推/快拉制造冲击；climax 拍用缓推+停留让情感落地；bridge 拍用平滑过渡保持画面连续
- 运镜须匹配本段在全局弧线中的 phase：引入段流畅平稳，转折段运镜变化增多，高潮段缓推停留
- 标注每拍对应的运镜动作（push in / pull out / pan / tilt / track / hold）与速度
- 给出重点帧的停留时长建议
- 段间衔接自然
- 必须用英文输出（omni 视频模型对英文 prompt 兼容性最好，中文易触发内容审核误判）
- 只输出提示词本身（英文），不要 JSON、不要标题、不要解释
%s""" % (seg_index, total, start, end, json.dumps(beats, ensure_ascii=False, indent=1),
        phase, script.get("global_arc") or script.get("story_hook") or "", note)

    content = [{"type": "image_url", "image_url": {"url": u}} for u in image_uris]
    content.append({"type": "text", "text": text})
    return [{"role": "system", "content": CAMERA_SYSTEM}, {"role": "user", "content": content}]


TRANSLATE_SYSTEM = (
    "你是短视频分镜翻译。把英文生图提示词逐句翻成中文对照：信息一一对应，不增不减、不概括、不要机翻腔。"
    "只输出 JSON，不要任何解释。"
)


def missing_zh(script):
    """列出缺中文对照的拍与 subject_bible 字段。"""
    beats = []
    for si, seg in enumerate((script or {}).get("segments", []), start=1):
        for li, beat in enumerate(seg.get("beats", []), start=1):
            kp = str(beat.get("keyframe_prompt", "")).strip()
            if kp and not str(beat.get("keyframe_prompt_zh", "")).strip():
                beats.append({"id": (si - 1) * 6 + li, "en": kp})
    bible = (script or {}).get("subject_bible", {}) or {}
    keys = [k for k in ("product", "person", "scene")
            if str(bible.get(k) or "").strip() and not str(bible.get(k + "_zh") or "").strip()]
    return beats, keys


def build_translate_messages(script, beats, bible_keys):
    import json
    todo = {"beats": {str(b["id"]): b["en"] for b in beats}}
    if bible_keys:
        bible = (script or {}).get("subject_bible", {}) or {}
        todo["subject_bible"] = {k: str(bible.get(k)) for k in bible_keys}
    text = """把下面每条英文翻成中文对照，严格输出这个 JSON 结构（键完全一致，值用中文）：
{
  "beats": { "<拍号>": "中文对照" },
  "subject_bible": { "product_zh": "…", "person_zh": "…", "scene_zh": "…" }
}
要求：逐句对应，景别/光线/材质/部件朝向这类关键信息一个字都不能丢；没有 subject_bible 需求时该对象给 {}。
待翻译：%s""" % json.dumps(todo, ensure_ascii=False)
    return [{"role": "system", "content": TRANSLATE_SYSTEM}, {"role": "user", "content": text}]


def apply_translations(script, data):
    """把翻译结果并回剧本，返回补上的条数。"""
    got = 0
    beats_map = data.get("beats") or {}
    for si, seg in enumerate(script.get("segments", []), start=1):
        for li, beat in enumerate(seg.get("beats", []), start=1):
            zh = str(beats_map.get(str((si - 1) * 6 + li), "")).strip()
            if zh and not str(beat.get("keyframe_prompt_zh", "")).strip():
                beat["keyframe_prompt_zh"] = zh
                got += 1
    bible = script.setdefault("subject_bible", {})
    for key, val in (data.get("subject_bible") or {}).items():
        if val and not str(bible.get(key) or "").strip():
            bible[key] = str(val).strip()
            got += 1
    return got


def camera_sanity(text):
    """运镜词轻校验：英文、长度够、没夹带解释。"""
    problems = []
    body = (text or "").strip()
    if not body:
        return ["模型没返回内容"]
    if CJK.search(body):
        problems.append("含中文，omni 易被内容审核拦截")
    wc = word_count(body)
    if wc < 100:
        problems.append("只有 %d 个英文词，建议 150-300" % wc)
    if wc > 420:
        problems.append("多达 %d 个英文词，omni 可能吃不下，压到 300 以内" % wc)
    if body.startswith("```") or "```" in body:
        problems.append("带 markdown 代码块围栏")
    if any(body.lower().startswith(p) for p in ("here is", "here's", "sure", "certainly", "output:")):
        problems.append("开头像解释性文字而非提示词")
    return problems


def culture_text(country):
    country = (country or "").strip()
    lang = COUNTRY_LANG.get(country, "")
    note = COUNTRY_NOTE.get(country, COUNTRY_NOTE["通用"])
    out = ["目标国家/市场为「%s」：%s。人物外貌与族裔按该国真实人口与常见审美写（不要一律套欧美女性，除非目标国家就是欧美市场），剧情桥段、家居陈设、节日、送礼习惯都要落地到该国。" % (country or "通用", note)]
    if lang:
        out.append("dialogue 一律用 %s（母语者口语，每拍 5-15 词，避免翻译腔），用于视频模型同语言口播；subject_bible 的 person/scene 与 keyframe_prompt 仍然用英文。" % lang)
    else:
        out.append("dialogue 默认写英文台词（每拍 5-15 词，口语自然，如 \"Oh my god, this is beautiful\"），避免翻译腔。")
    return " ".join(out)


def build_script_messages(product_name, selling_points, duration, segments, platform="通用",
                          structure="", extra="", country="", card=None):
    total = segments * 6
    table = STRUCTURE_TABLES.get(segments) or STRUCTURE_TABLES[3] + """

40s+：按「引入→发展→转折→高潮+收尾」四幕展开，产品总在第 2 段或之后入场，第 1 段只讲故事。"""
    card_block = ""
    cl = card_line(card) if card else ""
    if cl:
        card_block = ("产品基准卡（Stage 0 从产品实拍图提取，是本片唯一的产品事实来源）：\n" + cl +
                      "\nsubject_bible.product 必须逐字采用上面这段英文（整体照抄即可），不得增删部件、"
                      "不得改写朝向/材质/配色，也不得补充图上看不到的功能；同时把它的中文对照写进 "
                      "subject_bible.product_zh。每一拍 keyframe_prompt 里凡涉及产品，沿用同一套部件说法。")
    user = "\n\n".join([x for x in [
        "产品名：%s\n产品卖点：%s\n画幅：9:16 竖屏，%s 秒，共 %d 个节拍，分 %d 段每段 6 拍\n目标平台：%s\n目标国家/市场：%s\n叙事结构：%s" % (
            product_name, selling_points, duration, total, segments, platform,
            country or "通用（欧美语境）",
            structure or "自选（看图与卖点自己挑一个最合适的，并把结构名写进 narrative_structure）"),
        card_block,
        table,
        structure_hint(structure),
        "请为这支产品广告写剧本，严格输出以下 JSON 结构（字段名与层级完全一致）：\n"
        + SCRIPT_SCHEMA.replace("{duration}", str(duration)).replace("{segments}", str(segments)),
        SCRIPT_RULES.replace("{segments}", str(segments)).replace("{total}", str(total))
        .replace("{culture}", culture_text(country)),
        "平台适配：抖音节奏快、前 1 秒必须有钩子、对白口语化；小红书 UGC 真实感、色调温暖；"
        "Instagram 视觉优先、对白可少；通用兼顾节奏与质感。",
    ]])
    if extra:
        user += "\n\n用户额外要求（优先级最高）：" + extra
    return [{"role": "system", "content": SCRIPT_SYSTEM}, {"role": "user", "content": user}]


def extract_json(text):
    """剥掉 ```json 围栏并截出第一个完整对象。"""
    import json
    import re
    body = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    start = body.find("{")
    if start < 0:
        raise ValueError("模型没返回 JSON")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(body)):
        ch = body[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(body[start:i + 1])
    raise ValueError("JSON 不完整（可能被 max_tokens 截断）")


BRIEF_SYSTEM = (
    "你是跨境电商短视频的投放策划。给你一份从产品实拍图提取的特征基准卡和目标市场，"
    "你要产出：能直接用的卖点提炼，以及 3 个彼此不同的故事方向（都要让产品被剧情需要才登场，不硬广）。"
    "只输出 JSON。"
)


def build_brief_messages(product_name, card, country, platform, duration, segments, hint=""):
    cl = card_line(card) if card else ""
    uncertain = (card or {}).get("uncertain") or []
    text = """产品名：%s
目标国家/市场：%s
目标平台：%s
本次时长：%s 秒，分 %d 段（每段 6 拍）

产品基准卡（从实拍图提取，是唯一可信的产品事实来源）：
%s

请输出这个 JSON（字段名完全一致）：
{
  "selling_points_zh": "中文 80-160 字：把基准卡里的可见特征翻译成买家能感知的卖点（材质/工艺→质感与耐看，投影功能→情感表达力），只说图里撑得住的话",
  "story_options": [
    {"title": "3-6 字方向名", "hook": "第 1 拍用什么人物/冲突/悬念抓人（不出现产品）",
     "emotion": "情感共鸣核心（怀旧/治愈/惊喜/共鸣/向往/心疼）", "scene": "符合该国真实生活的场景与节日",
     "product_entry": "产品如何被剧情自然需要而出现", "why_fit": "为什么适合这个产品+这个市场，一句话"}
  ],
  "avoid": ["中文：3 条别踩的坑，例如不要臆测图上看不到的规格、不要一开场就亮产品、不要翻译腔台词"]
}
要求：3 个方向的情感、场景、产品入场方式必须明显不同；人物族裔与家居陈设符合目标国家；
基准卡 uncertain 里列出的东西一律不许当成事实写%s""" % (
        product_name or "（未填，按基准卡判断）", country or "通用（欧美语境）", platform or "TikTok",
        duration, segments, cl or "（还没有基准卡——先回第 0 步「提取产品特征」，否则只能凭产品名猜）",
        ("：" + "；".join(str(u)[:80] for u in uncertain[:4])) if uncertain else "。")
    if hint:
        text += "\n\n用户已有的想法（优先顺着它展开，但产品事实仍以基准卡为准）：" + hint
    return [{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": text}]


def brief_to_text(option, selling_points=""):
    """把一条故事方向 + 卖点拼成可直接填进「产品卖点 + 故事方向」框的中文段落。"""
    bits = []
    if selling_points:
        bits.append("卖点：" + str(selling_points).strip())
    if option:
        bits.append("故事方向：%s —— 钩子：%s；情感：%s；场景：%s；产品入场：%s" % (
            option.get("title", ""), option.get("hook", ""), option.get("emotion", ""),
            option.get("scene", ""), option.get("product_entry", "")))
        if option.get("why_fit"):
            bits.append("为什么合适：" + str(option["why_fit"]).strip())
    return "\n".join(bits)


STORY_SCHEMA = """{
  "title": "短片标题",
  "platform": "目标平台",
  "narrative_structure": "选用的叙事结构名",
  "duration": {duration},
  "segments_count": {segments},
  "global_arc": "全局故事弧线（1-2 句）",
  "story_hook": "第 1 拍用什么人物/冲突/悬念抓人",
  "emotional_core": "触动人的是什么情感（怀旧/治愈/惊喜/共鸣/向往/心疼）",
  "ending_note": "结尾怎么收（纯剧情可以没有行动号召，或只留一句悬念/余韵）",
  "subject_bible": {
    "characters": [
      {"name": "与名册完全一致的角色名", "person": "英文：脸型/发型/体型/服装配色款式/饰品（照抄该角色基准卡，不得改写）", "person_zh": "中文对照"}
    ],
    "props": [
      {"name": "与名册完全一致的道具名", "product": "英文：形状/部件/朝向/材质/配色/纹路（照抄该道具基准卡）", "product_zh": "中文对照"}
    ],
    "scene": "英文：地点、陈设、光线基调、色调",
    "scene_zh": "中文对照",
    "tone": "整体情绪基调"
  },
  "segments": [
    {"segment": 1, "phase": "阶段名", "segment_arc": "本段小弧线 + 如何衔接全局",
     "beats": [
       {"id": 1, "segment": 1, "time": "0.0-1.2s", "duration": 1.2,
        "beat_function": "hook|setup|build|suspense|foreshadow|bridge|reveal|twist|escalate|climax|emotion|satisfaction|cta|brand",
        "story_beat": "中文：本拍谁在做什么、情感怎么变",
        "cast": ["这一拍画面里出现的人物名和道具名（必须来自名册；空镜头就写 []）"],
        "shot": "景别与角度", "shot_motivation": "为什么用这个景别",
        "action": "画面内容与动作",
        "emotion": "本拍情绪标签", "dialogue": "目标语言的台词（无则写 无）", "sound": "音乐/音效提示",
        "keyframe_prompt": "ENGLISH 40-90 words: who is in frame + action + environment + light + palette, restating each cast member's appearance anchors",
        "keyframe_prompt_zh": "上面这条英文的中文逐句对照"}
     ]}
  ],
  "slogan": "结尾文案（可留空字符串）",
  "cta": "行动号召（纯剧情可留空，或只写「看主页」这类引流）"
}"""

STORY_RULES = """硬性要求（违反即作废）：
1. 这是纯剧情短片，**没有产品**：不要出现商品展示、卖点口播、价格、购买引导。情感和内容本身就是目的。
2. 名册封闭：全部 {total} 拍里出现的人物与道具**只能来自上面那份资产名册**，名字必须逐字一致；不许发明新角色、不许给道具改名。空镜头的 cast 写 []。
3. 每拍必须给 `cast`（数组，元素是名册里的名字）与 `keyframe_prompt`（英文 40-90 词）+ `keyframe_prompt_zh`（中文逐句对照）。
4. segments 数组长度 = {segments}，每段 beats 恰好 6 拍，id 全局连续 1..{total}。
5. 每段 6 拍 duration 之和精确 = 10.0（钩子 1-1.2s，高潮 2-2.5s，收尾 1-1.5s，衔接拍 2s）。
6. 全局第 1 拍 beat_function = hook；cta/brand 只允许出现在全局最后 1-2 拍，而且**最多只写一句轻引流（如「看主页」）**，没有就留空。
7. 每段末拍（id=6K）与下段首拍（id=6K+1）beat_function 标 bridge，画面必须连续。
8. 情绪弧线跨全部 {total} 拍递进，不得每段重置；climax 拍情绪强度最高。
9. 一致性铁律：同一个人物在所有拍里脸型/发型/体型/服装/饰品完全不变；同一道具的形状/部件/朝向/材质/纹路完全不变；场景与光线基调不变（确需换景要显式写明且只换一次）。`keyframe_prompt` 里要复述每个出场资产的外观锚点描述。
10. 人物外貌与道具描述**必须照抄名册里给的基准卡**，不得增删特征、不得补图上看不到的细节。
11. keyframe_prompt 必须是英文（生图模型对英文更稳）；dialogue 用下面指定的台词语言。
12. 只输出 JSON 本身，不要任何解释。"""


def asset_roster_block(assets):
    """把角色/道具名册拼成给剧本模型看的块。"""
    lines = []
    for a in assets:
        kind = a.get("kind", "prop")
        cl = card_line(a.get("card") or {}, kind, a.get("name", ""))
        lines.append("- %s「%s」%s" % ("角色" if kind == "character" else "道具", a.get("name", ""),
                                       ("：" + cl) if cl else "（还没有提取特征卡，只按名字写）"))
    return "\n".join(lines)


STORY_TIMING_NOTE = """六拍时长分配（每段 6 拍之和必须精确 = 10.0 秒）：
- 10s（1 段 6 拍）：钩子 1-1.2 / 铺垫 1.5-1.8 / 发展 1.5-2 / 转折或留白 1.5-2 / 高潮 2-2.5 / 收尾 1-1.5
- 20s（2 段 12 拍）：幕1 讲故事+埋钩子，幕2 发展+高潮+收尾
- 30s（3 段 18 拍）：幕1 引入，幕2 发展+转折，幕3 高潮+收尾
纯剧情没有产品，不要「产品入场」这类拍位；推进靠事件与情绪，段与段之间画面必须接得上。"""

STORY_OUTLINE_SYSTEM = (
    "你是竖屏 9:16 短片的故事策划，负责在写分镜剧本之前先把故事定下来："
    "谁、在什么处境、想要什么、遇到什么阻碍、怎么转折、结尾落在什么情绪上。"
    "这是纯剧情内容，不是广告——不出现商品、不喊卖点、不做购买引导。"
    "人物与道具只能取自给定名册，名字逐字沿用，不许发明新角色或给道具改名。"
    "只输出 JSON，不要任何解释。"
)

STORY_OUTLINE_SCHEMA = """{
  "title": "短片标题",
  "logline": "一句话故事（谁+处境+冲突+走向）",
  "genre": "选用的类型/结构名",
  "tone": "情绪基调（如：温暖带点心酸 / 越看越毛）",
  "characters": [
    {"name": "名册里的角色名，逐字一致", "role": "本片里的身份", "want": "表面想要什么", "need": "真正缺什么", "arc": "从什么状态变成什么状态"}
  ],
  "props": [
    {"name": "名册里的道具名，逐字一致", "role_in_story": "它在剧情里承担什么（线索/情感载体/反转钥匙…）"}
  ],
  "setting": "时间与地点（一两句）",
  "plot": {
    "setup": "开场把观众放进什么处境，第一拍的钩子是什么",
    "development": "事情怎么一步步推进、冲突怎么升级",
    "turn": "转折点：什么信息或事件改变了局势",
    "climax": "情绪与事件的最高点，画面发生什么",
    "ending": "结尾落在什么情绪/画面上（纯剧情可以没有行动号召）"
  },
  "emotional_arc": "观众情绪随节拍的走向（一句）",
  "beat_plan": ["把上面剧情粗分成 %total 拍，每拍一行：谁在哪做什么、情绪怎么变（不要写英文提示词）"],
  "hook_alternatives": ["另给 1-2 个不同的开场钩子供替换"],
  "visual_motif": "可复用的视觉母题（光线/色彩/构图/道具特写），用于跨帧保持一致",
  "risks": "你觉得哪里最容易拍坏或看不懂，需要用户确认"
}"""


def outline_to_text(outline):
    """把故事大纲压成一段人可读、也可直接喂给剧本模型的纯文本。"""
    if not isinstance(outline, dict):
        return str(outline or "").strip()
    lines = ["标题：%s" % outline.get("title", ""),
             "一句话故事：%s" % outline.get("logline", ""),
             "类型/结构：%s ｜ 基调：%s" % (outline.get("genre", ""), outline.get("tone", "")),
             "设定：%s" % outline.get("setting", "")]
    chars = outline.get("characters") or []
    if chars:
        lines.append("人物：" + "；".join(
            "%s（想要%s／其实缺%s／弧线：%s）" % (c.get("name", ""), c.get("want", ""), c.get("need", ""), c.get("arc", ""))
            for c in chars if isinstance(c, dict)))
    props = outline.get("props") or []
    if props:
        lines.append("道具：" + "；".join(
            "%s（%s）" % (p.get("name", ""), p.get("role_in_story", "")) for p in props if isinstance(p, dict)))
    plot = outline.get("plot") or {}
    for k, label in (("setup", "开场"), ("development", "推进"), ("turn", "转折"), ("climax", "高潮"), ("ending", "结尾")):
        if plot.get(k):
            lines.append("%s：%s" % (label, plot[k]))
    if outline.get("emotional_arc"):
        lines.append("情绪弧线：%s" % outline["emotional_arc"])
    bp = outline.get("beat_plan") or []
    if bp:
        lines.append("逐拍规划：" + "｜".join(str(x) for x in bp))
    if outline.get("visual_motif"):
        lines.append("视觉母题：%s" % outline["visual_motif"])
    return "\n".join([x for x in lines if x])


def build_story_outline_messages(duration, segments, platform, country, structure, assets, idea="", extra=""):
    """剧情模式第一步：先出故事大纲（1 次 chat），给用户看过再展开成分镜剧本。"""
    total = segments * 6
    lang = COUNTRY_LANG.get(country or "", "")
    user = "\n\n".join([x for x in [
        "画幅：9:16 竖屏，成片 %s 秒 = %d 段 × 6 拍 = %d 个节拍\n目标平台：%s\n目标国家/市场：%s\n类型/结构：%s" % (
            duration, segments, total, platform or "通用", country or "通用",
            structure or "自选（挑一个最贴想法的）"),
        "这一步只写故事，不要写分镜、不要写英文生图提示词、不要排每秒时长。"
        "先把人物、冲突、转折、结局定下来，再给出 %d 拍的粗分规划。" % total,
        "资产名册（唯一可出场的人物与道具，名字必须逐字沿用，不许发明）：\n" + (asset_roster_block(assets) or "（空）"),
        structure_hint(structure, "story"),
        STORY_OUTLINE_SCHEMA.replace("%total", str(total)),
        "硬性要求：1) 纯剧情、无产品、无带货话术；2) characters/props 里的 name 必须逐字来自名册；"
        "3) plot 五段要真的能推进到 climax 并收在 ending，不许烂尾；"
        "4) beat_plan 恰好 %d 行，与后面的节拍一一对应，全局第 1 拍必须是钩子；"
        "5) 情绪弧线跨全部 %d 拍递进，不逐段重置；6) 只输出 JSON 本身。" % (total, total),
        culture_text(country),
        "台词/口播语言：%s（这一步基本不用写台词，最多在 beat_plan 里点一句）" % (lang or "跟随目标国家；未指定则英文"),
    ]])
    if idea:
        user += "\n\n用户的故事前提 / 灵感（顺着它展开，可以补足但不能跑题）：" + idea
    if extra:
        user += "\n\n用户额外要求（优先级最高）：" + extra
    return [{"role": "system", "content": STORY_OUTLINE_SYSTEM}, {"role": "user", "content": user}]


def build_story_script_messages(duration, segments, platform, country, structure, assets, extra="", story=""):
    total = segments * 6
    lang = COUNTRY_LANG.get(country or "", "")
    culture = culture_text(country)
    draft_block = ""
    if str(story or "").strip():
        draft_block = ("已确认的故事大纲（**必须严格照着它展开，不得改主线、结局、人物设定与道具用途**；"
                       "允许为了 6 拍节奏做量的调整，但不允许换故事）：\n" + str(story).strip())
    user = [
        "画幅：9:16 竖屏，%s 秒，共 %d 个节拍，分 %d 段每段 6 拍\n目标平台：%s\n目标国家/市场：%s\n叙事结构：%s\n纯剧情模式：没有产品，不要任何带货元素" % (
            duration, total, segments, platform or "通用", country or "通用",
            structure or "自选（挑一个最合适的并把结构名写进 narrative_structure）"),
        "资产名册（唯一可出场的人物与道具，名字必须逐字沿用）：\n" + (asset_roster_block(assets) or "（空）"),
        draft_block,
        STORY_TIMING_NOTE,
        structure_hint(structure, "story"),
        "请写这支短片的剧本，严格输出以下 JSON 结构（字段名与层级完全一致）：\n"
        + STORY_SCHEMA.replace("{duration}", str(duration)).replace("{segments}", str(segments)),
        STORY_RULES.replace("{segments}", str(segments)).replace("{total}", str(total)),
        culture,
        "台词语言：%s" % (lang or "跟随目标国家；未指定则英文"),
    ]
    if story:
        user.insert(1, "这一步是把上面那份已确认的大纲**逐拍展开**成分镜剧本：大纲的每一段剧情，落到对应的拍上。")
    user = "\n\n".join([x for x in user if x])
    if extra:
        user += "\n\n用户额外要求（优先级最高）：" + extra
    return [{"role": "system", "content": STORY_SCRIPT_SYSTEM}, {"role": "user", "content": user}]


def build_asset_card_messages(image_uri, kind="character", name=""):
    """Stage 0：看角色图/道具图提特征卡。"""
    if kind == "character":
        ask = """{
  "name_en": "英文称呼",
  "name_zh": "中文名/称呼",
  "parts": [
    {"en": "英文：一项固定外观特征（脸型/发型发色/体型/肤色/服装款式与配色/饰品/疤痕纹身等），可执行、可核查", "zh": "中文对照"}
  ],
  "orientation": "英文：惯常朝向或视角提示（如 faces camera slightly to her left）",
  "material": "英文：服装与饰品的主要材质观感",
  "colors": "英文：配色（发色+服装+饰品）",
  "engraving": "英文：可见的文字/图案/标识，逐字写出；没有写 none visible",
  "size_ref": "英文：身高体型相对参照（如 about average height, slim build）",
  "summary_en": "英文 40-70 词：把上面各项压成一段可直接贴进生图提示词的人物基准描述（不含姿势与情绪）",
  "summary_zh": "summary_en 的中文逐句对照",
  "uncertain": ["英文：图上看不清或无法判断的地方"]
}
注意：只写**恒定不变**的身份特征（脸、发型、体型、服装、饰品）。姿势、表情、动作属于每一拍的剧情，不要写进基准卡。"""
        head = "这是短片的角色定妆图。"
    else:
        ask = """{
  "name_en": "英文道具名",
  "name_zh": "中文道具名",
  "parts": [
    {"en": "英文：单个部件的名称+形状+位置+朝向+工艺", "zh": "中文对照"}
  ],
  "orientation": "英文：主朝向/正反面判据",
  "material": "英文：材质与表面工艺",
  "colors": "英文：配色",
  "engraving": "英文：可见刻字/图案/纹路，逐字写出；没有写 none visible",
  "size_ref": "英文：相对尺寸判据",
  "summary_en": "英文 40-70 词：可直接贴进生图提示词的道具基准描述",
  "summary_zh": "summary_en 的中文逐句对照",
  "uncertain": ["英文：图上看不清或无法判断的地方"]
}
注意：只写这件道具本身，不要写使用场景或剧情。"""
        head = "这是短片里会出现的道具图。"
    text = """%s
道具/角色名：%s

请只根据这张图，输出这个 JSON（字段名完全一致，不要多余文字）：
%s
要求：只写你在图里真实看到的东西，不要推测型号、材质纯度或图上看不到的东西；看不清就写进 uncertain。""" % (head, name or "（未命名）", ask)
    return [{"role": "system", "content": "你是视觉取证员，负责把角色定妆图或道具图转成一份可核查的特征基准卡。只输出 JSON。"},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_uri}},
                {"type": "text", "text": text}]}]


def snap_segment_durations(script, target=10.0):
    """每段 6 拍时长之和强制归一到 target（默认 10s，omni 单段合同）。
    按比例缩放并回填 time 轴，把校验里最易踩的「9.5s/10.3s 差一点」在源头消掉。"""
    for seg in script.get("segments", []):
        beats = seg.get("beats") or []
        if not beats:
            continue
        durs = []
        for b in beats:
            try:
                durs.append(float(b.get("duration", 0)))
            except (TypeError, ValueError):
                durs.append(0.0)
        total = sum(durs)
        if total > 0 and abs(total - target) > 1e-3:
            scaled = [round(d * target / total, 2) for d in durs]
            scaled[-1] = round(target - sum(scaled[:-1]), 2)  # 吸收舍入残差，保证整和精确=target
        else:
            scaled = [round(d, 2) for d in durs]
        t = 0.0
        for b, d in zip(beats, scaled):
            b["duration"] = d
            b["time"] = "%.1f-%.1fs" % (t, t + d)
            t += d
    return script


def normalize_script(script, duration, segments, mode="product"):
    """补齐 UI 依赖的字段，beat 字段名容错（character->person, shot 缺失等）。"""
    import json
    script.setdefault("duration", duration)
    script["segments_count"] = segments
    script["mode"] = mode
    bible = script.get("subject_bible")
    if isinstance(bible, dict) and "person" not in bible and "character" in bible:
        bible["person"] = bible.pop("character")
    for si, seg in enumerate(script.get("segments", []), start=1):
        seg.setdefault("segment", si)
        for li, beat in enumerate(seg.get("beats", []), start=1):
            beat.setdefault("id", (si - 1) * 6 + li)
            beat.setdefault("segment", si)
            for alias, key in (("productPresence", "product_presence"), ("presence", "product_presence"),
                               ("storyBeat", "story_beat"), ("beatFunction", "beat_function"),
                               ("keyframePrompt", "keyframe_prompt"), ("shotMotivation", "shot_motivation")):
                if alias in beat and key not in beat:
                    beat[key] = beat.pop(alias)
            beat.setdefault("duration", 10.0 / 6)
            if mode == "story":
                cast = beat.get("cast")
                if isinstance(cast, str):
                    cast = [c.strip() for c in re.split(r"[,，、;；\n]+", cast) if c.strip()]
                beat["cast"] = cast if isinstance(cast, list) else []
            else:
                beat.setdefault("product_presence", "not_present" if li <= 3 else "natural")
            beat.setdefault("dialogue", "无")
            beat.setdefault("sound", "")
    snap_segment_durations(script, target=10.0)
    return json.loads(json.dumps(script, ensure_ascii=False))


def is_bridge(beat, total_frames):
    if str(beat.get("beat_function", "")).lower() == "bridge":
        return True
    idx = beat.get("_id")
    if not idx:
        return False
    return idx % 6 == 0 or (idx - 1) % 6 == 0 and idx > 1


CAST_CONSISTENCY_CLAUSE = (
    "Every named person and object must stay identical to its own reference image - same face, "
    "hairstyle, body, outfit and accessories for people; same shape, material, color and markings for "
    "props. Keep the set dressing, lighting and color palette continuous with the reference frame. "
    "Vertical 9:16 composition, photorealistic cinematic still. Change only the action and camera "
    "position; never change anyone's identity or the environment."
)

CAST_LOCK_CLAUSE = (
    "IDENTITY LOCK (checkable, non-negotiable): for every person and prop that appears in this frame, "
    "reproduce it exactly as shown in its reference image named above. You may change ONLY position, "
    "orientation and the way it is held or worn. Never add, remove or restyle a part, a tattoo, a "
    "haircut or a garment. If two people appear, do not swap or blend their faces or outfits."
)


def absent_clause(kind, name):
    if kind == "product":
        return NOT_PRESENT_CLAUSE
    who = "the character \"%s\"" % name if kind == "character" else "the prop \"%s\"" % name
    return ('IMPORTANT: %s does NOT appear anywhere in this frame - not in the foreground, not in the '
            "background, not as a photo, reflection, poster or silhouette. No text, no watermarks." % who)


PRODUCT_APPEARANCE_TERMS = re.compile(
    r"(?i)\b(?:amber|brown|red|clear|transparent|translucent|glass|plastic|ribbed|screw|plastic)"
    r"[\w-]*(?:\s+(?:glass|plastic|label|cap|bottle|candy|gummy))?"
)


def _sanitize_keyframe_product_text(text, product_ref="the product"):
    """将 keyframe_prompt 里的产品外观描述替换为占位产品名，产品外观只由 lock block 提供。"""
    text = str(text or "")
    product_ref = str(product_ref or "the product").strip()
    bottle_desc = (r"\b(?:'NGLEED'\s+)?(?:amber\s+glass|red\s+gummy|iron[-\s]?rich\s+gummy|"
                   r"clear\s+plastic|red\s+plastic|brown\s+plastic)\s+bottle\b")
    text = re.sub(r"\ban?\s+" + bottle_desc,
                  lambda m: ("an " if m.group(1) and m.group(1).lower() == "a " else "the ") + product_ref,
                  text, flags=re.IGNORECASE)
    text = re.sub(bottle_desc, product_ref, text, flags=re.IGNORECASE)
    text = re.sub(r"\bbottles?\s+with\s+a\s+red\s+label\b", product_ref, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:'NGLEED'\s+)?NGLEED\s+bottles?\b", product_ref, text, flags=re.IGNORECASE)
    # 兜底：品牌名 NGLEED 出现时，把它的整句（. ; , 前的词）连同外观词压成占位名，
    # 保证任何"NGLEED ... bottle with a red label ..."都不再留在 keyframe 里。
    def _collapse_ngleed_sentence(match):
        prefix = match.group(1) or ""
        return prefix + product_ref
    text = re.sub(r"((?:[,.]\s*|\s*)*)(\b'?\s*NGLEED\b[\w'’\s,]*(?:bottle|gummy|candy|product|label|cap)?\b)",
                  _collapse_ngleed_sentence, text)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def _product_ref_token(script, card=None):
    card = card or {}
    ref = card.get("product_ref") or (script or {}).get("subject_bible", {}).get("product_ref")
    if ref:
        return str(ref).strip()
    name = str((script or {}).get("title") or card.get("name_zh") or card.get("name_en") or "the product")
    return name


def assemble_frame_prompt(beat, script, extra="", ref_clause="", card=None, run=None):
    """Stage 2 单帧最终 prompt（全英文）。产品模式：keyframe 占位 + 固定 lock/negative block；剧情模式按出场资产拼。"""
    product_ref = _product_ref_token(script, card if card is not None else run.state.get("product_card") if run else None)
    chunks = [_sanitize_keyframe_product_text(str(beat.get("keyframe_prompt", "")), product_ref).strip()]
    if run is None:
        presence = str(beat.get("product_presence", "natural"))
        present, absent = (["product"] if presence in ("natural", "focus") else []), []
        feats = [product_feature_list(script, card)] if present else []
        consistency, lock = CONSISTENCY_CLAUSE, (PRODUCT_LOCK_CLAUSE if present else "")
        exclusions = [NOT_PRESENT_CLAUSE] if not present else [NO_TEXT_CLAUSE]
    else:
        present, absent = beat_cast(run, beat)
        feats = [asset_feature(run, aid) for aid in present]
        feats = [f for f in feats if f]
        if run.mode == "product":
            consistency, lock = CONSISTENCY_CLAUSE, (PRODUCT_LOCK_CLAUSE if present else "")
            assets = run.assets()
            exclusions = [absent_clause((assets[a].get("kind") or "product"),
                                        assets[a].get("name", "")) for a in absent] or [NO_TEXT_CLAUSE]
        else:
            consistency = CAST_CONSISTENCY_CLAUSE
            lock = CAST_LOCK_CLAUSE if present else ""
            assets = run.assets()
            exclusions = [absent_clause(assets[a].get("kind", "prop"), assets[a].get("name", ""))
                          for a in absent]
            exclusions.append(NO_TEXT_CLAUSE)
    for f in feats:
        chunks.append(f)
    summary = subject_summary(script)
    if summary:
        chunks.append("Fixed subject & setting - do not alter: " + summary)
    chunks.append(consistency)
    if ref_clause:
        chunks.append(ref_clause)
    if lock:
        chunks.append(lock)
    chunks.extend([e for e in exclusions if e])
    if is_bridge(beat, script.get("total_frames") if isinstance(script, dict) else None):
        chunks.append(BRIDGE_CLAUSE)
    if extra:
        chunks.append(str(extra).strip())
    return "\n".join([c for c in chunks if c])


def _frame_path(run, n):
    info = run.state.get("frames", {}).get("%02d" % n) or {}
    path = (run.root / info["file"]) if info.get("file") else None
    return path if path and Path(path).exists() else None


def _asset_path(run, a):
    p = run.root / (a.get("file") or "")
    return p if a.get("file") and p.exists() else None


def _detail_extra(run, present):
    """产品模式：主图之外最多再带 5 张细节图（有的话），帮模型锁死部件与工艺。返回资产 id。"""
    if run.mode != "product" or not present:
        return []
    group = [a["id"] for a in run.product_group() if a["id"] != present[0]]
    return group[:5]


def reference_plan(run, idx, beat, mode="skill"):
    """参考图选择：出场资产的图（主资产在前）+ 第 1 帧 + 上一帧，上限 6（产品模式最多主图+5 细节图）。
    铁律：这一拍不出场的资产，它的图绝不给模型看（串产品与串脸是同一个病）。"""
    first, prev = _frame_path(run, 1), (_frame_path(run, idx - 1) if idx > 1 else None)
    anchor = run.anchor_image if (run.anchor_image and run.anchor_image.exists()) else None
    assets = run.assets()
    present, absent = beat_cast(run, beat)
    extras = _detail_extra(run, present)
    cap = 6
    names = {aid: (assets[aid].get("name") or aid) for aid in present + extras}

    if idx == 1:
        if present:
            picks, labels = [], []
            for aid in [present[0]] + extras:
                p = _asset_path(run, assets[aid])
                if p is not None and p not in picks:
                    picks.append(p)
                    labels.append(names[aid])
            return (picks, "首帧即出场：%s" % " + ".join(labels)) if picks else ([], "无参考图（文生图）")
        return [], "无参考图（文生图）"

    if mode == "prev_only":
        pick = prev or first
        return ([pick], "只给上一帧（降级）") if pick else ([], "无参考图（文生图）")
    if mode == "anchor_only":
        pick = anchor or first
        return ([pick], "人锚单图模式") if pick else ([], "无参考图（文生图）")

    refs, labels = [], []
    for aid in (present[:2] + extras):
        p = _asset_path(run, assets[aid])
        if p is not None and p not in refs:
            refs.append(p)
            labels.append(names[aid])
    if first is not None and first not in refs:
        refs.append(first)
        labels.append("第1帧人锚")
    if prev is not None and prev not in refs:
        refs.append(prev)
        labels.append("上一帧")
    refs = refs[:cap]
    if not refs:
        return [], ("无参考图（文生图）")
    return refs, "出场 " + " + ".join(labels) + ("；不出场不给图：" + "、".join(
        (assets[a].get("name") or a) for a in absent) if absent else "")


def ref_roles(run, idx, beat, refs):
    """按实际传入顺序说清每张参考图是谁，避免模型把资产图和人锚/上一帧混起来。"""
    assets = run.assets()
    first, prev = _frame_path(run, 1), (_frame_path(run, idx - 1) if idx > 1 else None)
    anchor = run.anchor_image if (run.anchor_image and run.anchor_image.exists()) else None
    lookup = {}
    for aid, a in assets.items():
        p = _asset_path(run, a)
        if p is not None:
            lookup[str(p)] = (a.get("kind", "product"), a.get("name") or aid)
    if anchor is not None:
        lookup[str(anchor)] = ("character", "the anchor portrait")
    if first is not None:
        lookup.setdefault(str(first), ("scene", None))
    if prev is not None:
        lookup.setdefault(str(prev), ("prev", None))

    roles = []
    for p in refs:
        kind, name = lookup.get(str(p), (None, None))
        if kind == "product":
            roles.append("the PRODUCT photo - the ONLY source of truth for the product's shape, parts, "
                         "orientation, material and color")
        elif kind == "character":
            roles.append('the CHARACTER reference for "%s" - the ONLY source of truth for this person\'s face, '
                         "hairstyle, body and outfit" % (name or "unnamed"))
        elif kind == "prop":
            roles.append('the PROP reference for "%s" - keep its exact shape, material and markings' % (name or "unnamed"))
        elif kind == "scene":
            roles.append("frame 01, the CHARACTER and SCENE anchor - same face, hair, outfit, room and "
                         "lighting as this frame")
        elif kind == "prev":
            roles.append("the PREVIOUS frame - continue the action and camera from exactly here")
        else:
            roles.append("a reference image")
    return roles


def reference_clause(refs, roles):
    if not refs:
        return ""
    if len(refs) == 1:
        return ("The ONE attached reference image is %s. Do not treat it as anything else." % roles[0])
    parts = ["%d) %s" % (i + 1, r) for i, r in enumerate(roles)]
    return ("Attached reference images, in this exact order:\n   " + "\n   ".join(parts) +
            "\n   Never mix them up: only the image named above carries the product's true appearance; "
            "the others are for identity, scene and motion continuity.")


def assemble_video_prompt(run, seg_index, camera_prompt, voiceover=None):
    """Stage 5 单段最终 prompt（英文）。camera_prompt 由用户粘贴（Stage 4 手写）。"""
    script = run.script or {}
    seg = run.segments()[seg_index - 1]
    beats = seg.get("beats", [])
    total = run.state.get("segments") or 1
    lines = [(camera_prompt or "").strip()]
    vo = voiceover if voiceover is not None else {}
    if vo.get("enabled", True):
        gender = vo.get("gender", "female")
        lang = vo.get("language", "American English")
        lines.insert(0, "The video includes a %s %s voiceover speaking naturally throughout." % (gender, lang))
        lines.append(" ".join('The %s narrator says: "%s"' % (lang.split()[0], str(b.get("dialogue")).strip())
                              for b in beats if b.get("dialogue") and str(b.get("dialogue")).strip() not in ("无", "none", "None")))
    lines.append(VIDEO_ASSEMBLY)
    phase = seg.get("phase") or seg.get("segment_phase") or ""
    if phase:
        lines.append("This segment sits in the '%s' phase of the global arc: %s" % (phase, script.get("global_arc", "")))
    if total > 1:
        bits = ["This is segment %d of %d." % (seg_index, total)]
        if seg_index > 1:
            bits.append("Open on a continuation of the previous segment's final frame.")
        if seg_index < total:
            bits.append("End on a frame that leads naturally into the next segment.")
        bits.append("No transition effects - continuity comes from the imagery itself.")
        lines.append(" ".join(bits))
    return "\n".join([l for l in lines if l])


def validate_script(script, mode=None, run=None):
    """Stage 1 校验。返回 (errors, warnings)。产品模式行为与旧版一致。"""
    errors, warnings = [], []
    if not isinstance(script, dict):
        return ["剧本必须是 JSON 对象"], []
    mode = mode or script.get("mode") or "product"
    story = mode == "story"
    segments = script.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["缺少 segments 数组"], []
    s = len(segments)
    duration = script.get("duration") or s * 10
    if s >= 2 and not script.get("global_arc"):
        warnings.append("多段剧本缺 global_arc（全局叙事弧线）")
    required_top = ("story_hook", "emotional_core") if story else ("story_hook", "emotional_core", "product_entry")
    for key in required_top:
        if not script.get(key):
            errors.append("缺少%s字段: %s" % ("故事要素" if story else "故事三要素", key))
    if not story and not (script.get("subject_bible") or {}).get("product"):
        warnings.append("subject_bible.product 为空：产品特征清单无法逐帧固定")
    roster = set()
    if story:
        if run is not None:
            roster = {a.get("name") or aid for aid, a in run.assets().items()}
            if not roster:
                errors.append("剧情模式还没有上传任何角色/道具图（第 0 步名册是空的）")
        else:
            roster = {c.get("name") for c in ((script.get("subject_bible") or {}).get("characters") or [])
                      if isinstance(c, dict) and c.get("name")}
            roster |= {p.get("name") for p in ((script.get("subject_bible") or {}).get("props") or [])
                       if isinstance(p, dict) and p.get("name")}

    entry_beat = None
    prev_presence = None
    missing_zh = []
    unknown_cast = []
    any_cast = False
    need = STORY_REQUIRED if story else PRESENT_REQUIRED
    for si, seg in enumerate(segments, start=1):
        beats = seg.get("beats")
        if not isinstance(beats, list) or len(beats) != 6:
            errors.append("第 %d 段 beats 数量 = %s，应为 6" % (si, len(beats) if isinstance(beats, list) else "非数组"))
            continue
        total_dur = 0.0
        for li, beat in enumerate(beats, start=1):
            gid = (si - 1) * 6 + li
            missing = [k for k in need if k not in beat]
            if missing:
                errors.append("第 %d 拍缺字段: %s" % (gid, ", ".join(sorted(missing))))
            if story:
                cast = beat.get("cast")
                if not isinstance(cast, list):
                    if "cast" in beat:
                        errors.append("第 %d 拍 cast 必须是数组（名册里的名字）" % gid)
                else:
                    if cast:
                        any_cast = True
                    for c in cast:
                        if roster and str(c).strip() not in roster:
                            unknown_cast.append((gid, str(c).strip()))
            presence = str(beat.get("product_presence", ""))
            if not story:
                if presence and presence not in VALID_PRESENCE:
                    errors.append("第 %d 拍 product_presence 非法: %s" % (gid, presence))
                if presence in ("natural", "focus") and entry_beat is None:
                    entry_beat = gid
                prev_presence = presence
            try:
                total_dur += float(beat.get("duration", 0))
            except (TypeError, ValueError):
                errors.append("第 %d 拍 duration 非数字: %r" % (gid, beat.get("duration")))
            kp = str(beat.get("keyframe_prompt", ""))
            wc = word_count(kp)
            if kp and not (30 <= wc <= 120):
                warnings.append("第 %d 拍 keyframe_prompt 词数 %d（建议 40-90 英文词）" % (gid, wc))
            if kp and CJK.search(kp):
                errors.append("第 %d 拍 keyframe_prompt 含中文，生图必须用英文" % gid)
            if kp and not str(beat.get("keyframe_prompt_zh", "")).strip():
                missing_zh.append(gid)
        if abs(total_dur - 10.0) > 0.26:
            errors.append("第 %d 段 duration 之和 = %.2fs，应为 10s" % (si, total_dur))
    if missing_zh:
        warnings.append("%d 拍缺中文对照 keyframe_prompt_zh（%s）——点「补中文对照」一次补齐"
                        % (len(missing_zh), "、".join(str(x) for x in missing_zh[:8])))
    first_fn = str(((segments[0].get("beats") or [{}])[0]).get("beat_function", "")).lower()
    if first_fn and first_fn != "hook":
        errors.append("全局第 1 拍 beat_function 应为 hook，当前: %s" % first_fn)
    if not story and s == 1 and str(segments[0]["beats"][0].get("story_beat", "")).find("产品") >= 0:
        warnings.append("第 1 拍剧情疑似直接讲产品：违背剧情优先")
    head = [str(b.get("beat_function", "")).lower() for seg in segments for b in seg.get("beats", [])][:-2]
    if any("cta" in x or "brand" in x for x in head):
        errors.append("cta/brand 拍出现在全局最后 2 拍之前")
    if not story and s > 1 and entry_beat and entry_beat <= 3:
        warnings.append("产品在拍 1-3 就入场（默认前 3 拍不出现产品），确认是剧情需要")
    if story:
        if unknown_cast:
            errors.append("cast 里出现了名册外的名字（不许发明角色/道具）：%s"
                          % "、".join("拍%d:%s" % (g, n) for g, n in unknown_cast[:8]))
        if not any_cast:
            warnings.append("全部 6 拍都没有任何 cast —— 整片是空镜？确认是有意为之")
        banned = ("购买", "下单", "链接", "价格", "优惠", "折扣", "buy now", "shop now")
        hit = [w for seg in segments for b in seg.get("beats", []) for w in banned
               if w in str(b.get("dialogue", "")) + str(b.get("story_beat", ""))]
        if hit:
            errors.append("纯剧情模式出现带货话术（%s）——产品模式才有卖点位" % "、".join(sorted(set(hit))))
    return errors, warnings
