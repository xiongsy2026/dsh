#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：网关 mimo 三条 lane 的版本口径（2026-10-09 升到 2.6）。

钉【规则】不钉【现场】：不探上游活没活（那是随机假红的假保险），只钉三件事——
  1. 三条 mimo lane 解析出的模型必须落在 2.6 家族（flash/pro），且【不许写裸 "mimo-v2.6"】
     —— 2026-10-09 GET /v1/models 实测这个 key 只有 mimo-v2.6-flash / -pro，裸名会被上游判 unknown-model；
  2. 模型名必须来自 config 字段（routes 的 fields.model），不许在 ROUTES 里写第二份字面量；
  3. _MODEL_FALLBACK['mimo'] 必须同族 —— 否则 .env 一缺值就静默掉回 2.5（假升级）。
M 组 = 变异对照：把 lane 改回 2.5 / 写成裸 2.6 / 把字段名换成字面量，三种都必须判红。
"""
import os
import re
import sys
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

PASS, FAIL = [], []
OK_FAMILY = {"mimo-v2.6-flash", "mimo-v2.6-pro"}
# ASR/TTS 是另一条版本线（mimo 只有 v2.5-asr，没有 2.6 档）⇒ 认它们自己的现网真名，
# 不拿聊天/视觉那两个值去卡。上游哪天出 2.6 的 ASR 就在这里加一行，别改 drift_hits 的判式。
OK_ASRTTS = {"mimo-v2.5-asr"}


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print("%-4s %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def main():
    from backend import gateway as g

    lanes = [("text.complete", "mimo"), ("text.complete", "mimo_pro"),
             ("video.analyze", "mimo"), ("video.analyze", "mimo_pro")]
    for cap, pur in lanes:
        r = g.resolve(cap, pur)
        check("%s/%s 落在 2.6 家族" % (cap, pur), r.get("model") in OK_FAMILY, True)
    check("_MODEL_FALLBACK['mimo'] 同族（.env 缺值也不掉回 2.5）",
          g._MODEL_FALLBACK.get("mimo") in OK_FAMILY, True)

    src = open(os.path.join(BACKEND, r"gateway.py"), encoding="utf-8").read()
    for cap, pur in lanes:
        line = [l for l in src.split("\n") if '"%s", "%s"' % (cap, pur) in l]
        check("%s/%s 的模型走 config 字段（不在 ROUTES 里写字面量）" % (cap, pur),
              bool(line) and re.search(r'"model":\s*"(?!mimo)[A-Z0-9_]+"', line[0]) is not None, True)
    cfg = open(os.path.join(BACKEND, r"config.py"), encoding="utf-8").read()
    check("config 里 TEXT_MODEL_MIMO 在位", "TEXT_MODEL_MIMO: str =" in cfg, True)
    check("🔴 全仓网关侧不许出现裸 \"mimo-v2.6\"（上游没这个 id）",
          re.search(r'"mimo-v2\.6"(?![-\w])', src) is not None, False)

    # ---- M 组：变异对照（证明这几条断言真的会红，不是恒真） ----
    def family_ok(model):
        return model in OK_FAMILY and not re.match(r"^mimo-v2\.6$", str(model))

    check("M1 变异：lane 改回 mimo-v2.5 必须判红", family_ok("mimo-v2.5"), False)
    check("M2 变异：写成裸 mimo-v2.6 必须判红", family_ok("mimo-v2.6"), False)
    check("M3 变异：模型名硬编码进 ROUTES 必须判红",
          re.search(r'"model":\s*"(?!mimo)[A-Z0-9_]+"', ' "model": "mimo-v2.6-flash" ') is not None, False)
    check("M4 对照：正解 mimo-v2.6-flash 判绿", family_ok("mimo-v2.6-flash"), True)

    # ---- B 组：mimo 视觉腿的预算（2026-10-09 实测：300 预算 ⇒ 299 reasoning + 空正文）----
    seg = src.split('if capability == "video.analyze":')[1].split("\n    if ")[0] if 'if capability == "video.analyze":' in src else ""
    # 🔴 变异/断言一律在【剥注释后的代码】上做：今天第三次被自己的注释坑到
    #    （replace("2500","300",1) 换中的是注释里那句"2500 时 708 reasoning"，变异根本没碰到代码）
    NL = chr(10)
    seg = NL.join(l for l in seg.split(NL) if not l.strip().startswith("#"))
    def budget_floor(text):
        # 代码形状：(2500 if str(model).startswith("mimo") else 300) ⇒ 要抓的是 if 前面那个数
        m = re.search(r'(\d+)\s+if\s+str\([^)]*\)\.startswith\("mimo"\)', text)
        return int(m.group(1)) if m else 0
    check("B1 mimo 视觉预算兜底在位且 ≥2500（实测值，不是拍的）", budget_floor(seg) >= 2500, True)
    check("B2 变异：把兜底改回 300 必须判红", budget_floor(seg.replace("2500", "300", 1)) >= 2500, False)
    check("B3 变异：整段兜底删掉必须判红", budget_floor("") >= 2500, False)
    check("B4 非 mimo 仍是 300（没顺手把 agnes 也改了）",
          re.search(r'else\s*300\)', seg) is not None, True)
    check("B5 调用方显式传 max_tokens 仍然优先", "params.get(\"max_tokens\") or" in seg, True)

    # ---- V 组：全仓覆盖（2026-10-13 用户："mimo 2.5 要升 2.6"）----
    # 钉两条规则，不钉现场（不去探上游活没活）：
    #   V1 活代码里不许再出现 mimo-v2.5 / -pro（ASR/TTS 没有 2.6 档，扫描里也不会出现，出现了就是回归）；
    #   V2 写死的 mimo 字面量必须【等于 config 那两个值】⇒ config 再升档时，写死的腿会立刻红（防"假升级"）。
    # 🔴 台账/历史产物一律不扫不改：video_runs/*/run.json、productions/**、outputs/*.json、docs 的历史节 ——
    #    那些是"当时真用了什么"的记录，改了就是伪造历史（回执逐格署名那条纪律的同一条道理）。
    BANNED = "mimo-v2." + "5"                                  # 拼接构造：别让本文件自己成为命中项
    ALLOWED = {g.resolve("text.complete", "mimo").get("model"),
               g.resolve("text.complete", "mimo_pro").get("model")}
    LIVE_ROOTS = [BACKEND, os.path.join(WS, "scratch", "video-ui", "video-ui")]
    SUBDIRS = ("backend", "pipeline")
    SKIP_PARTS = ("site-packages", os.sep + "docs" + os.sep, ".dbg", "node_modules", ".venv",
                  os.sep + "dist" + os.sep, "__pycache__", os.sep + ".git" + os.sep,
                  os.sep + "outputs" + os.sep, os.sep + "productions" + os.sep,
                  os.sep + "video_runs" + os.sep, os.sep + "skills" + os.sep)
    HERE = os.path.abspath(__file__)
    CFG = os.path.join(BACKEND, "config.py")         # 字面量的合法老家，不参与 V2

    def live_files():
        for root in LIVE_ROOTS:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):             # 根级 .py（那批 _probe/_video_understand 就在这层）
                if name.endswith(".py"):
                    yield os.path.join(root, name)
            for sub in SUBDIRS:
                for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
                    dirnames[:] = [d for d in dirnames if not any(p in dirpath + os.sep for p in SKIP_PARTS)]
                    for fn in filenames:
                        if fn.endswith(".py"):
                            yield os.path.join(dirpath, fn)
        vu = os.path.join(WS, "scratch", "video-ui", "video-ui")
        if os.path.isdir(vu):
            for fn in sorted(os.listdir(vu)):
                if fn.endswith((".py", ".json")):
                    yield os.path.join(vu, fn)

    def drift_hits(line):
        # 🔴 匹配【任何 mimo-v 开头的字面量】，不是只匹配 mimo-v2.* —— 否则上游出 mimo-v3 时这条会漏网。
        #    不匹配裸 "mimo"（那是 provider/purpose 名，满仓都是，误伤就等于恒红）。
        # ⚠️ ASR/TTS 是另一条版本线：mimo 只有 v2.5-asr（config 注释原话"mimo 有 v2.6-pro/v2.5-asr"），
        #    没有 2.6 档 ⇒ 认它们各自的现网真名，别拿聊天/视觉那两个值去卡它（2026-10-15 实测：
        #    prompt_reverser 新加的听写腿写 mimo-v2.5-asr 是对的，被 V2 判红 = 闸算错，不是码错）。
        return [m.group(1) for m in re.finditer(r'["\'](mimo-v[-\w.]*)["\']', line)
                if m.group(1) not in ALLOWED and m.group(1) not in OK_ASRTTS]

    seen, hits_25, hits_drift = set(), [], []
    for p in live_files():
        ap = os.path.abspath(p)
        if ap == HERE or any(s in ap for s in SKIP_PARTS):
            continue
        try:
            src = open(ap, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        seen.add(ap)
        for i, ln in enumerate(src.split("\n"), 1):
            if ln.strip().startswith("#"):
                continue                                     # 整行注释放过（config 那行在描述上游清单）
            if re.search(re.escape(BANNED) + r"(?![-\w])", ln):
                hits_25.append("%s:%d" % (os.path.basename(ap), i))
            if ap != CFG:
                for bad in drift_hits(ln):
                    hits_drift.append("%s:%d %s" % (os.path.basename(ap), i, bad))
    check("V1 活代码（扫了 %d 个文件）里不许再出现裸 %s" % (len(seen), BANNED), hits_25, [])
    check("V2 写死的 mimo 字面量必须等于 config 的值（config 再升档 ⇒ 这里立刻红）",
          sorted(set(hits_drift)), [])
    check("V2b 前提：ALLOWED 真读到了两个 2.6 档（读空=V2 是恒真假绿）",
          [len(ALLOWED), all(str(x).startswith("mimo-v2.6") for x in ALLOWED)], [2, True])
    check("V3 覆盖对照：这次改过的文件确实被扫到了（少一份=假绿）",
          all(os.path.basename(x) in {os.path.basename(y) for y in seen}
              for x in [os.path.join(BACKEND, r"pipeline\step1_mimo_parse.py"),
                        os.path.join(BACKEND, r"services\prompt_reverser.py"),
                        os.path.join(BACKEND, r"services\douyin_mall_clothing.py"),
                        os.path.join(BACKEND, r"services\tvc_agent.py"),
                        os.path.join(WS, "scratch", "video-ui", "video-ui", "zexp.py")]), True)
    check("V4 反向对照：含裸 %s 的一行必须被抓到（证明 V1 有牙，不是恒真）" % BANNED,
          bool(re.search(re.escape(BANNED) + r"(?![-\w])", 'payload = {"model": "%s"}' % BANNED)), True)
    check("V5 三向对照：野字面量被抓 / 合法 2.6 不抓 / 裸 \"mimo\"(provider 名)不误伤",
          [drift_hits('m = "mimo-v9.9-pro"'), drift_hits('m = "mimo-v2.6-flash"'), drift_hits('p = "mimo"')],
          [["mimo-v9.9-pro"], [], []])
    check("V5b ASR 档双向对照：现网真名 mimo-v2.5-asr 不误伤 / 野版本照样被抓",
          [drift_hits('m = "mimo-v2.5-asr"'), drift_hits('m = "mimo-v2.4-asr"')], [[], ["mimo-v2.4-asr"]])

    print("\n=== mimo lane 版本闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
