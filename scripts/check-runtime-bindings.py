r"""四表一致闸：profile bindings ↔ Provider LANES ↔ 桥白名单 ↔ 网关路由。

2026-10-03 的事故：SVML 写 model="2.5" ⇒ hypit 选的是【独立 capability】@hypit/seedance@1#seedance-2.5，
而 profile 只绑了 seedance-2-mini 一条 ⇒ 没有 binding 时 runtime-local 会在所有已装 provider 里随便挑，
官方 hypihub（offers 全部 15 条，见 provider-hypihub/src/mapping.ts）接住了它，直接 POST https://hypit.ai，
绕开我们的预扣/结算/审计 —— 靠那个账号恰好欠费（HTTP 402）才没花出去。
漏一条不只那一条走偏：runtime-local/src/config.ts:135-141 的 scopedProfile 只有在【每一条】请求都带
binding 时才收窄 provider 集合，所以一条漏绑 = 官方通道对全场保持开着。

断言全部 0 花费（静态为主）；--live 额外真跑一次 hypit plan（plan 本身不接外部工作）。
"""
import json
import os
import re
import shutil
import subprocess
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_SKILL = os.environ.get("DSH_VIDEO_SKILL") or r"D:\video-skill"
PROFILE = os.path.join(WS, "hypit_runs", "hypit.runtime.json")
PROVIDER = os.path.join(WS, "hypit_runs", "packages", "provider-gateway", "src", "provider.js")
PRODUCTIONS = os.path.join(WS, "hypit_runs", "productions")
sys.path.insert(0, VIDEO_SKILL)

TOTAL = [0]
FAILS = []


def check(name, got, want):
    TOTAL[0] += 1
    if got != want:
        FAILS.append(name)
        print("FAIL", name, "\n     实得", json.dumps(got, ensure_ascii=False)[:260],
              "\n     期望", json.dumps(want, ensure_ascii=False)[:260])
    else:
        print("PASS", name, "=>", json.dumps(got, ensure_ascii=False)[:80] if not isinstance(got, bool) else "ok")


def parse_provider(src):
    """从 provider.js 抠出每条 lane：capability 全名 / wire / seconds / resolutions / aspects / lastFrame。"""
    mods = {m: v for m, _, v in re.findall(
        r"const (SEEDANCE|MINIMAX)\s*=\s*\{\s*name:\s*\"([^\"]+)\",\s*version:\s*\"([^\"]+)\"\s*\}", src)}
    names = {m: n for m, n in re.findall(
        r"const (SEEDANCE|MINIMAX)\s*=\s*\{\s*name:\s*\"([^\"]+)\",\s*version:\s*\"[^\"]+\"\s*\}", src)}
    body = src[src.find("export const LANES"):]
    out = []
    for mo, cn, wire, sec, res, asp, lf, rfs in re.findall(
        r"\{\s*capability:\s*\{\s*module:\s*(\w+),\s*name:\s*\"([^\"]+)\"\s*\},\s*wire:\s*\"([^\"]+)\",\s*"
        r"seconds:\s*\[([^\]]*)\],\s*resolutions:\s*\[([^\]]*)\],\s*aspects:\s*(null|\[[^\]]*\]),\s*"
        r"lastFrame:\s*(true|false),\s*refs:\s*\[([^\]]*)\]", body):
        secs = tuple(float(x) for x in sec.split(",") if x.strip())
        ress = tuple(x.strip().strip('"') for x in res.split(",") if x.strip())
        asps = None if asp == "null" else tuple(x.strip().strip('"') for x in asp.strip("[]").split(","))
        refs = tuple(x.strip().strip('"') for x in rfs.split(",") if x.strip())
        out.append({"key": "%s@%s#%s" % (names[mo], mods[mo], cn), "wire": wire,
                    "seconds": secs, "resolutions": ress, "aspects": asps,
                    "last_frame": lf == "true", "refs": refs})
    return out


def main():
    profile = json.load(open(PROFILE, encoding="utf-8"))
    src = open(PROVIDER, encoding="utf-8").read()
    lanes = parse_provider(src)

    # ── ① 账本外端点必须一个都不剩（唯一出口）──────────────────────────────────────
    off = [k for k, v in profile["endpoints"].items()
           if "hypihub" in str(v.get("use", "")).lower()
           or "hypit.ai" in str((v.get("config") or {}).get("baseUrl", ""))]
    check("1 profile 里没有 hypihub / https://hypit.ai 这类账本外端点", off, [])
    check("1b 生成端点只剩本地两件 + 我们的网关", sorted(profile["endpoints"]),
          ["hyperframes.local", "media.local", "weeksir.gateway"])

    # ── ② LANES ↔ bindings 双向对齐 ──────────────────────────────────────────────
    check("2 provider 声明 3 条 lane（mini / 2.5 / minimax-h3）", len(lanes), 3)
    bound = set(profile["bindings"])
    keys = {l["key"] for l in lanes}
    check("2a 每条 lane 都在 profile 有 binding（漏一条=让给任意 offers 它的 provider）",
          sorted(k for k in keys if k not in bound), [])
    check("2b binding 里没有空绑（绑了但 provider 不声明，同样走偏）", sorted(k for k in bound if k not in keys), [])
    check("2c bindings 覆盖的视频 capability 就是这三条", sorted(bound), sorted([
        "@hypit/minimax-h3@1#minimax-h3", "@hypit/seedance@1#seedance-2-mini", "@hypit/seedance@1#seedance-2.5"]))

    # ── ③ 桥的白名单与档位必须和 provider 一致（两边都是闸：老 runtime 仍会发旧字段）──
    from backend import hypit_bridge as hb
    from backend import gateway as gw
    wires = {l["wire"] for l in lanes}
    check("3 每个 lane 的 wire model 桥都受理", sorted(wires - set(hb.HYPIT_MODELS)), [])
    for lane in lanes:
        spec = hb._lane_spec(lane["wire"])
        check("3b %s：provider 档位 == 桥档位" % lane["wire"],
              [lane["seconds"], lane["resolutions"], lane["aspects"], lane["last_frame"], lane["refs"]],
              [spec["seconds"], tuple(spec["resolutions"]),
               tuple(spec["aspects"]) if spec["aspects"] is not None else None,
               bool(spec["last_frame"]), tuple(spec["refs"])])
    check("3c 桥里多余（provider 不发）的模型只剩 CLI/内部用的两条",
          sorted(set(hb.HYPIT_MODELS) - wires), ["omni-flash-10s", "seedance-2.0"])

    # ── ④ 网关侧真有这条路由且已适配（否则预扣就炸：不烧钱但白跑）──────────────────
    check("4 这些 wire 指向的路由都在网关注册表里",
          sorted(w for w in set(hb.HYPIT_MODELS) if tuple(hb.HYPIT_MODELS[w]) not in gw.ROUTES), [])
    check("4b 都在「已适配」集合里（没适配=空头路由，不进池）",
          sorted(w for w in hb.HYPIT_MODELS if tuple(hb.HYPIT_MODELS[w]) not in getattr(gw, "_ADAPTED", set())), [])
    check("4c 2.5 与 h3 都有单价（报价不许凭记忆）",
          [gw.PRICE_ROUTES.get(("video.generate", "seedance-2.5")), gw.PRICE_ROUTES.get(("video.generate", "h3"))],
          [60, 60])

    # ── 4d /rates 的文案必须由 LANE_SPECS 生成（曾经写死"720p、最长 10 秒、只收图片参考"，与真实闸不符）──
    for lane in lanes:
        r = hb.handle_rates({"model": [lane["wire"]]})
        check("4d %s 的 /rates 说的是这条路由的真话（参考类型 + 档位字段同源）" % lane["wire"],
              [("+".join(lane["refs"]) in r["description"]),
               ("只收图片参考" in r["description"]) if lane["refs"] != ("image",) else False,
               r.get("lane", {}).get("refs") == list(lane["refs"]),
               r.get("lane", {}).get("seconds") == list(lane["seconds"])],
              [True, False, True, True])
    check("4e 未登记的模型不编文案", hb.handle_rates({"model": ["nope-xyz"]}).get("error", {}).get("code"),
          "UNKNOWN_MODEL")

    # ── ⑤ 禁令：一个 capability 固定一个 wire model，不许从字面量猜 ─────────────────
    code_only = "\n".join(line.split("//")[0] for line in src.splitlines())   # 别拿注释里的"旧马甲"把自己钉死
    check("5 provider 里没有 when{...} 猜模型的马甲（写 mini 实扣 H3 那种）",
          re.findall(r"when:\s*\{[^{}]*\}", code_only), [])
    check("5b 每条 lane 的 routes 固定单条 wire", src.count("routes: [{ model: lane.wire }]"), 1)
    check("5c profile 不再声明 hypihub 的 oauth 凭据引用",
          "hypihub.oauth" in json.dumps(profile, ensure_ascii=False), False)

    # ── ⑥ 盘上工程不许写到未绑定的字面量 ──────────────────────────────────────────
    allowed = {"mini": "@hypit/seedance@1#seedance-2-mini", "seedance-2-mini": "@hypit/seedance@1#seedance-2-mini",
               "2.5": "@hypit/seedance@1#seedance-2.5", "seedance-2.5": "@hypit/seedance@1#seedance-2.5"}
    bad = []
    for root, _d, files in os.walk(PRODUCTIONS):
        for f in files:
            if not f.endswith(".svml"):
                continue
            path = os.path.join(root, f)
            text = open(path, encoding="utf-8", errors="replace").read()
            lit = set(re.findall(r"<seedance:[A-Za-z]+[^>]*?\bmodel=\"([^\"]+)\"", text))
            unbound = sorted(x for x in lit if allowed.get(x, "—") not in bound)
            if unbound:
                bad.append([os.path.relpath(path, WS), unbound])
    check("6 盘上 .svml 没有写到未绑定 capability 的 model 字面量", bad, [])

    # ── ⑦ --live：真跑一次 plan，确认路由与"官方端点不在场" ────────────────────────
    if "--live" in sys.argv:
        cwd = os.path.join(WS, "hypit_runs")
        for label, rel, want_ep in [("mini", os.path.join("productions", "shoe-replica", "build.svrun"),
                                     "weeksir.gateway")]:
            p = subprocess.run([shutil.which("hypit.cmd") or shutil.which("hypit") or "hypit",
                               "plan", rel, "--json"], capture_output=True, text=True,
                               cwd=cwd, timeout=300)
            try:
                d = json.loads(p.stdout)
            except ValueError:
                check("7 live plan(%s) 出 JSON" % label, p.stdout[:200] or p.stderr[:200], "可解析")
                continue
            gen = [x for x in d.get("providers", []) if "seedance" in str(x.get("capability"))]
            check("7 live plan(%s)：seedance 腿全部落在 %s" % (label, want_ep),
                  sorted({x.get("endpoint") for x in gen}), [want_ep])
            check("7b live plan(%s)：有腿、且没有一条走偏或没主" % label,
                  [d.get("providerRequestCount", 0) > 0, d.get("unresolvedRequestCount"), d.get("ok")],
                  [True, 0, True])

    print("\n=== 四表一致闸：%d 条断言，失败 %d%s ===" % (
        TOTAL[0], len(FAILS), ("（--live 未开，plan 实跑 2 条没计）" if "--live" not in sys.argv else "")))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
