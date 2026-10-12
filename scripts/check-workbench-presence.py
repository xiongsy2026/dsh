"""右栏 presence 回归断言（契约：docs/ADR-workbench-presence.md）

规则只有一条：**台跟着技能意图走**。
  intent（这条会话表过态要做哪条流水线，持久）决定页签弹不弹；
  up（引擎现在活着）只决定台里显示什么。新会话两条意图都没有 ⇒ 什么都不弹。

两段：
  A 函数层 —— 临时登记文件 + 临时 video_runs + 探针桩，全离线（不撞 5179/8765、不碰线上）
  B HTTP 层 —— 打常驻 8100 的真判定口，用完把自己塞进去的假主人摘干净

跑法：PYTHONIOENCODING=utf-8 python scripts/check-workbench-presence.py
退出码非 0 = 有断言挂。改归属/presence 逻辑必须先过这里。
"""
import json
import os
import shutil
import subprocess
import sys

os.environ.setdefault("HYPIT_NO_AUTOSTART", "1")   # 判定口现在会顺手起查看器，自测里必须关
import time
import urllib.error
import urllib.request

SCRATCH = r"C:\dsh\_scratch_b"
os.makedirs(SCRATCH, exist_ok=True)
os.environ["HYPIT_STUDIO_MARK"] = os.path.join(SCRATCH, "mark_selftest.json")
os.environ["HYPIT_JOBS_DB"] = os.path.join(SCRATCH, "presence_selftest.sqlite")   # studio_status 现在会读腿数，绝不能碰线上账本
# ⚠️ 意图文件也必须隔离：studio_claim 会顺手写意图，早先没隔离时把 5 条假会话写进了线上
#    .gateway_intent.json（就是"绝不往生产状态写假数据"那条老坑）。
os.environ["HYPIT_INTENT_MARK"] = os.path.join(SCRATCH, "intent_selftest.json")
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

RUN = "productions/hypit-e2e/assemble.svrun"
ME = "session-PRESENCE-SELFTEST"
GATEWAY = "http://127.0.0.1:8100/api/workbench/presence"

fails, total = [], [0]


def _safe(s):
    """GBK 控制台上「⇒」这类字符会把 print 打崩=闸看起来像没跑完。只按【输出流编码】replace，
    绝不 sys.stdout.reconfigure（那会造"零输出 + exit 0"的静默假绿），也别裸 encode()（默认 utf-8 永不失败）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(_safe(("PASS " if ok else "FAIL ") + name + " => " + json.dumps(got, ensure_ascii=False)[:150]))
    if not ok:
        fails.append((name, got, want))


def mark():
    return json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]


# ── 离线桩：A 段绝不碰真端口 ────────────────────────────────────────────────────
ALIVE = {"studio": True, "omni": True, "reverse": True}
SERVED = {"path": "productions\\hypit-e2e\\assemble.svml"}
hb._probe_studio = lambda port: dict(up=ALIVE["studio"], tasks=0, running=0, artifacts=0)
hb._probe_omni = lambda: ALIVE["omni"]
hb._probe_reverse = lambda: ALIVE["reverse"]      # 第 6 档（8090 反推台）也不撞真端口
hb._studio_served_path = lambda port: SERVED["path"]

# omni 的意图来自盘上的 run ⇒ 给一个临时 runs 目录
# 🔴 临时 scratch 是【固定路径】（studio_status 读腿数要它存在），上一轮如果半路崩了就会留下
#    假 running 腿：轻则把 A9d 判成 "generating"（看着像代码坏，其实是残留），重则 insert 撞 UNIQUE
#    直接崩在闸中间（2026-10-06 实测：上一次 GBK 崩在 A7，之后每次都在 jobs 插入那行崩）。
#    所以开局就把这几样清干净——线上库不在这里，HYPIT_JOBS_DB 已经指到 scratch。
for _p in (os.environ["HYPIT_INTENT_MARK"], os.environ["HYPIT_STUDIO_MARK"], os.environ["HYPIT_JOBS_DB"]):
    if os.path.exists(_p):
        os.remove(_p)                # 上一轮残留会让这一轮的排他判定莫名其妙
FAKE_RUNS = os.path.join(SCRATCH, "fake_video_runs")
shutil.rmtree(FAKE_RUNS, ignore_errors=True)
hb.VIDEO_RUNS = FAKE_RUNS


def refresh_runs():
    hb._RUNS_CACHE = {"ts": 0.0, "rows": []}


def make_run(name, session, approved=False):
    d = os.path.join(FAKE_RUNS, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
        json.dump({"session": session, "review": {"script_approved": approved}}, fh)


# ══ A1 hypit 意图 ════════════════════════════════════════════════════════════════
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:      # 旧格式：会话级只增不减那一版
    json.dump({"sessions": ["session-A", "session-B"], "session": "session-B",
               "run": "hypit-e2e", "port": 5179, "ts": time.time()}, f)
check("A1 旧格式一律判无意图", hb.studio_status("session-A")["reason"], "no-intent")
check("A1b 不带 session 不回 up", hb.studio_status()["up"], False)

hb.studio_claim("session-A", RUN, 5179)
check("A2 登记按 run 建索引", sorted(mark()), ["productions/hypit-e2e"])
hb.studio_claim("session-C", RUN, 5179)
check("A3 同 run 多人共看（名单累加，不是换主人）",
      mark()["productions/hypit-e2e"]["sessions"], ["session-A", "session-C"])
check("A4 两个主人会话都判 up", [hb.studio_status(s).get("up") for s in ("session-A", "session-C")],
      [True, True])
check("A5 第三会话无意图", hb.studio_status("session-D")["reason"], "no-intent")
hb.studio_release("session-A")
check("A6 release 只摘自己，另一个还在", mark()["productions/hypit-e2e"]["sessions"], ["session-C"])
# ⚠️ 语义变更（铁律三修）：studio-claim 会顺手把该会话钉成 hypit 意图，而 release 只撤 run 归属、
#    不撤意图 ⇒ 摘掉 claim 后是 not-claimed（台还挂、提示怎么起），要 no-intent 必须显式 intent-clear。
check("A7 摘掉 claim 但意图还在 ⇒ not-claimed", hb.studio_status("session-A")["reason"], "not-claimed")
hb.clear_intent("session-A")
check("A7b 显式撤销意图后才真的 no-intent", hb.studio_status("session-A")["reason"], "no-intent")
check("A8 空 session / 空 run 都拒",
      ["error" in hb.studio_claim("", RUN, 5179), "error" in hb.studio_claim("session-X", "", 5179)],
      [True, True])

# ══ A8b 🔴 裸跑 release 不许抹全局（2026-10-02 实际踩过：漏会话 id ⇒ 归属登记一次清光）══
hb.studio_claim("session-D", "productions/other/x.svrun", 5179)     # 第二个 run，用来验"不动别人"
before8 = sorted(mark())
r0 = hb.studio_release()                                            # 等价于 CLI 裸跑 studio-release
check("A8b 无参 release 被拒（回 error，不静默清空）", "error" in r0, True)
check("A8c 拒的时候一份登记都没动", sorted(mark()) == before8, True)
check("A8d 报错里带当前归属，让人知道该摘谁", "session-C" in json.dumps(r0, ensure_ascii=False), True)
hb.studio_release("session-D")
check("A8e 按会话释放照旧生效（名单空了才撤那个 run）", sorted(mark()), ["productions/hypit-e2e"])
r_all = hb.studio_release(all_sessions=True)      # 清空 = 整份文件删掉（生产里那次"文件不见了"就是这个）
check("A8f 只有显式 all_sessions 才允许整份清空",
      [r_all.get("scope"), (not os.path.exists(hb.STUDIO_MARK)) or mark() == {}], ["all", True])
hb.studio_claim("session-C", RUN, 5179)                             # 还给 A9 用

# ══ A9 意图是持久的：超时只影响"活着"那一层，不再撤销页签 ══════════════════════════
rec = mark()
for v in rec.values():
    v["ts"] -= hb.STUDIO_CLAIM_TTL + 1                     # 一天没人看
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
    json.dump({"runs": rec, "ts": 0}, f)
r = hb.studio_status("session-C")
check("A9 心跳超 TTL 仍算有意图（旧版在这撤销 ⇒ 老会话什么都不弹）", r["intent"], True)
check("A9b 台里照常显示引擎在跑", (r["up"], r.get("reason", "")), (True, ""))
ALIVE["studio"] = False
r = hb.studio_status("session-C")
check("A9c studio 死了：intent 保住、up 掉", (r["intent"], r["up"], r["reason"]),
      (True, False, "studio-down"))
check("A9d 无腿时台里仍给起法（字段留给 agent，页面不印命令行由 client 负责）",
      [r.get("state"), "hypit studio --run" in (r.get("how_to_start") or "")], ["viewer-down", True])
_c = hb._db()
_c.execute("insert into jobs(id,model,state,run,label,session,created,updated) values"
           "('l-live','seedance-2-mini','running','productions/hypit-e2e','generate','session-C',?,?)",
           (time.time(), time.time()))
_c.commit(); _c.close()
hb._PROGRESS_CACHE.update(sid=None, ts=0.0)
r2 = hb.studio_status("session-C")
# 服务端仍带 how_to_start 字段（给 agent/运维用），但状态已经是 generating：
# 页面按 state 走，绝不把"引擎没在跑/起它：<命令行>"印给人（client 侧有对应断言）。
check("A9e 🔴 有在途腿 ⇒ state=generating、腿数报出来（页面据此改口的依据）",
      [r2.get("state"), r2.get("active_legs")], ["generating", 1])
ALIVE["studio"] = True

# ══ A10 登记封顶：只裁最旧的，不裁"过期的" ════════════════════════════════════════
hb.studio_mark_clear_all()
for i in range(hb.STUDIO_INTENT_KEEP + 5):
    hb.studio_claim("session-many", "productions/r%02d/x.svrun" % i, 5179)
    rec = mark()
    for k, v in rec.items():
        v["ts"] = 1000 + int(k.rsplit("r", 1)[-1][:2])      # 造出明确的新旧序
    with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
        json.dump({"runs": rec, "ts": 0}, f)
kept = sorted(mark())
check("A10 登记封顶生效", len(kept) <= hb.STUDIO_INTENT_KEEP, True)
check("A10b 裁掉的是最旧那批（r00 不在了）", any("r00" in k for k in kept), False)
hb.studio_mark_clear_all()

# ══ A11 交叉核对：studio 自报它在服务哪个 run ══════════════════════════════════════
hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
SERVED["path"] = "productions\\other\\z.svml"
check("A12 自报别的 run → 台里提示 run-mismatch", hb.studio_status("session-B")["reason"], "run-mismatch")
check("A12b 但意图仍在（该弹还是弹）", hb.studio_status("session-B")["intent"], True)
SERVED["path"] = "productions\\b\\y.svml"
check("A13 匹配则 up 且 verified",
      [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")], [True, True])
SERVED["path"] = ""                                          # 生成工程 /__studio/session 会回 500
check("A14 拿不到自报 run 时退回只认登记",
      [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")], [True, False])

# ══ A15 omni：意图 = 显式登记；盘上的 run 只提供显示字段 ════════════════════════
# 🔴 2026-10-02 定稿：痕迹不再触发页签。旧写法（"盘上有带本会话标的 run 就算意图"）
#    因为 run 不过期，会让一条几小时前的 run 永久霸占右栏——实测事故：装了个纯提示词
#    skill，切回会话却在弹 omni 台。这等于替用户猜意图，砍掉。
make_run("20261001-aaa", "session-OMNI-1", approved=True)
time.sleep(0.02)
make_run("20261001-bbb", "session-OMNI-1")
make_run("20260930-old", None)                                # 老 run 没打标
refresh_runs()
check("A15 有本会话的 run、但没登记 ⇒ 不挂台（痕迹不当触发器）",
      hb.omni_status("session-OMNI-1")["intent"], False)
hb.set_intent("session-OMNI-1", "omni", "回归用")
o = hb.omni_status("session-OMNI-1")
check("A15a 登记后挂台", o["intent"], True)
check("A15b 台地址带上会话过滤", o["url"].endswith("session=session-OMNI-1"), True)
check("A15c 关口只作为信息（ready），不作为弹的条件", "ready" in o, True)
check("A15d 登记后取最新的 run 显示", o["run"], "20261001-bbb")
check("A15e 没表过态的会话无意图", hb.omni_status("session-NEVER")["reason"], "no-intent")
check("A15f source 只会是 explicit（run-tag 那档已废）", o["source"], "explicit")
check("A15g 老 run 没打标 ⇒ 不在本会话的 run 清单里（可见性靠标，不靠猜）",
      "20260930-old" not in [r["run"] for r in hb._runs_of_session("session-OMNI-1")], True)
ALIVE["omni"] = False
o = hb.omni_status("session-OMNI-1")
check("A15h video-ui 没起：意图在、up 掉", (o["intent"], o["up"]), (True, False))
ALIVE["omni"] = True

# ══ A16 判定口一次给两台 ══════════════════════════════════════════════════════════
hb.studio_claim("session-OMNI-1", RUN, 5179)
both = hb.workbench_presence("session-OMNI-1")
# 台键名单只有一个源 = hypit_bridge.PRESENCE_KEYS。这里过去一直自己抄一份（抄的那份每加一档就要人记着改，
# 2026-10-06 实测 check-svc-8100.py 那份停在三档、一直假红），现在改成读权威源 + 钉死"表里每条都真有状态函数"。
check("A16 一个口同时回答 PRESENCE_KEYS 里全部台（名字读权威源，不在闸里抄第二份）",
      sorted(both), sorted(hb.PRESENCE_KEYS))
check("A16k 名单与 presence 输出一一对应（不多不少；漏一条=右栏那一格永远不弹）",
      sorted(both) == sorted(hb.PRESENCE_KEYS), True)
# 2026-10-09 加了一档 "form"（输入表单台），它【不是可登记的意图档】：
#   引擎档 = 先能登记意图、再决定挂不挂；form 档 = 登记的那条档【配了字段表】才挂（判据在 model_admin）。
#   所以这里不再笼统要求 PRESENCE_KEYS ⊆ KNOWN_SKILLS，而是拆成两条更准的：
_ENGINE = tuple(k for k in hb.PRESENCE_KEYS if k != "form")
check("A16k2 引擎档必须都能登记意图（⊆ KNOWN_SKILLS）",
      [k in hb.KNOWN_SKILLS for k in _ENGINE], [True] * len(_ENGINE))
check("A16k2b form 档存在且【不可被登记】（它是字段表推出来的台，不是一条意图档）",
      ("form" in hb.PRESENCE_KEYS) and ("form" not in hb.KNOWN_SKILLS), True)
check("A16k2c 台名单里【除 form 之外】不许再来路不明的档（差集必须恰好等于 {form}）",
      sorted(set(hb.PRESENCE_KEYS) - set(hb.KNOWN_SKILLS)), ["form"])

# ══ A16-R 第 6 档：提示词反推台（intent-only 排他 + up 真探 8090）══════════════════
# ⚠️ 这一段是在【FORM_VIEW 没注册】的形状下跑的（8100 没装配表单档时判定口的样子，也是单元隔离的形状）。
#    线上 8100 一定注册了它 ⇒ 登记 prompt-reverse 后这一档会被表单台顶掉（intent=False）。
#    所以"引擎台照旧弹"在这里成立、在生产不成立 —— 顶替规则单独用 A16-T 钉，别拿这段当"线上就这样"。
hb.set_intent("session-REV-1", "prompt-reverse", "反推爆款01")
rv = hb.workbench_presence("session-REV-1")
check("A16r 登记 prompt-reverse ⇒ 只挂反推台（omni/hypit 一律不挂）",
      [rv["prompt-reverse"]["intent"], rv["omni"]["intent"], rv["hypit"]["intent"]], [True, False, False])
check("A16r2 判定口回的是 8090 引擎页地址 + 任务名带回来（只当信息给 agent；右栏已不摆这张页，见 0f/A16t）",
      [rv["prompt-reverse"]["url"], rv["prompt-reverse"]["task"], rv["prompt-reverse"]["source"]],
      ["http://127.0.0.1:8090/prompt-reverse", "反推爆款01", "explicit"])
check("A16r3 别的会话没登记 ⇒ 零 UI（不看痕迹）",
      [hb.prompt_reverse_status("session-NEVER-REV")["intent"],
       hb.prompt_reverse_status("session-NEVER-REV")["reason"]], [False, "no-intent"])
ALIVE["reverse"] = False
r2 = hb.prompt_reverse_status("session-REV-1")
check("A16r4 8090 没起：意图在、up 掉，且怎么起说得出",
      [r2["intent"], r2["up"], bool(r2.get("how_to_start"))], [True, False, True])

# ══ A16-T 表单台顶替（2026-10-09，排他规则的延伸）══════════════════════════════════
# 规则一句话：这条会话登记的【这一条线】配了输入表 ⇒ 该线的引擎页不再弹——同一条线的输入口
#   从引擎页换成了表单页，不是"关掉别人的台"。没配表的线（omni/hypit 已冻结）不受任何影响。
# 🔴 这里只桩掉 FORM_VIEW 这一个【依赖】（真函数在 model_admin 里，它才读得到 SKILL.md 的 form: 段；
#    "8100 到底注册没注册"由下面 B8 在真判定口上验）。被测对象是 workbench_presence 里的顶替规则本身，
#    没有把整块换掉 —— 那才是"全绿漏网"的老坑。
FORM_LANES = {"prompt-reverse", "product-video", "film"}
SURFACED = set()          # 哪些自测会话"agent 已经回填过了"（弹出时机的第二个条件）


def _fake_form_view(sid):
    """桩的是【依赖】(model_admin 那份才读得到 SKILL.md 的 form: 段)，不是被测逻辑。
    答复形状必须跟真函数一致：`intent`=该不该弹、`has_input`=这条线的输入口是不是表单。
    🔴 2026-10-10 加 has_input：顶替规则改读它了，桩不跟着改就会"闸绿、线上引擎页复活"。"""
    lane = str(((hb.intent_map() or {}).get(sid) or {}).get("skill") or "")
    if lane not in FORM_LANES:
        return {"intent": False, "has_input": False, "up": True, "alive": True,
                "reason": "intent-has-no-form", "intent_skill": lane}
    base = {"has_input": True, "up": True, "alive": True, "source": "explicit",
            "skill": lane, "intent_skill": lane,
            "url": "http://127.0.0.1:8100/ui/" + lane + "?session=" + sid}
    # 2026-10-11 改判：弹表只看【意图 + 配了字段表】，回填不再是前置（prefilled 只是标出来给页面看）
    return dict(base, intent=True, surfaced=True, prefilled=(sid in SURFACED))


hb.register_form_view(_fake_form_view)
hb.set_intent("session-REV-2", "prompt-reverse", "反推爆款02")
SURFACED.add("session-REV-2")        # 这条=agent 已回填 ⇒ 该弹
tv = hb.workbench_presence("session-REV-2")
check("A16t 有表的线 + 已回填 ⇒ 表单台弹、该线引擎台被顶成 reason=form-is-input",
      [tv["form"]["intent"], tv["prompt-reverse"]["intent"], tv["prompt-reverse"].get("reason"),
       str(tv["prompt-reverse"].get("url") or "").endswith("/prompt-reverse")],
      [True, False, "form-is-input", True])
# 🔴 2026-10-11 用户改判（推翻 10-10 那条）：弹表条件 = 意图 + 配了字段表，两样够就弹。
#    10-10 版拿"回填过"当弹表前置，现网实测【点完 skill 到看见表空窗 38 分钟】（20:18→20:56），
#    客户中间什么都看不见也催不动。压力没取消，是挪到【报价那一刀】：没回填 ⇒ approve 建不出条子
#    （gateway.form_prefill_blockers + check-quote-provenance 的 F 组钉着）。
#    顶替引擎页照旧必须生效 —— 否则就成了两个输入口并存。
hb.set_intent("session-REV-5", "prompt-reverse", "反推爆款05")     # 故意不放进 SURFACED
tv5 = hb.workbench_presence("session-REV-5")
check("A16t5 有表的线【还没回填】也照弹（改判），prefilled=False 要标出来，引擎页仍被顶住",
      [tv5["form"]["intent"], tv5["form"].get("surfaced"), tv5["form"].get("prefilled"),
       tv5["form"].get("has_input"),
       tv5["prompt-reverse"]["intent"], tv5["prompt-reverse"].get("reason")],
      [True, True, False, True, False, "form-is-input"])
_gw = open("D:/video-skill/backend/gateway.py", encoding="utf-8", errors="replace").read()
_ma = open("D:/video-skill/backend/model_admin.py", encoding="utf-8", errors="replace").read()
_ai = _gw.find("本来就不需要批准（免批类")   # approve 分支里唯一的一行，拿它当锚点比猜分支写法稳
_ab = _gw[_ai:_ai + 2000] if _ai >= 0 else ""
check("A16t6 🔴 压力只是换位置、没消失：判定口不再拿回填当弹表前置，但 approve 仍必须问回填",
      ["await-prefill" in _ma, "form_prefill_blockers(" in _ab, "surfaced = True" in _ma],
      [False, True, True])
# 反向对照①：omni 没配表 ⇒ 顶替【不许】发生，omni 台照旧弹。
#   谁把规则写成"只要有表单档就压掉引擎台"，这条立刻红 —— 误伤冻结线是客户当场看得见的。
hb.set_intent("session-OMNI-T", "omni", "鼻贴顶替对照")
to = hb.workbench_presence("session-OMNI-T")
check("A16t2 没配表的线（omni 已冻结）⇒ 表单档不弹、引擎台照常弹（顶替只认有表这一条）",
      [to["form"]["intent"], to["form"]["reason"], to["omni"]["intent"]],
      [False, "intent-has-no-form", True])
# 反向对照②：有表但没有引擎台的那一档（product-video）⇒ 表单台弹，且不炸、不误伤别的台
hb.set_intent("session-PV-T", "product-video", "一句话出片对照")
SURFACED.add("session-PV-T")     # 这条断言验的就是"表单台弹"，所以要给到第二个条件（已回填）
tp = hb.workbench_presence("session-PV-T")
check("A16t3 有表、无引擎台的一档（product-video）⇒ 只弹表单台，其余一律不弹，不报错",
      [tp["form"]["intent"], tp["omni"]["intent"], tp["hypit"]["intent"], tp["film"]["intent"]],
      [True, False, False, False])
hb.register_form_view(None)      # 摘掉桩：后面的组要按"没注册"的形状判
check("A16t4 桩摘掉后这一档恒不弹（没注册 = 不多一个空台，不是崩）",
      [hb.workbench_presence("session-REV-2")["form"]["intent"],
       hb.workbench_presence("session-REV-2")["form"]["reason"]], [False, "form-view-not-registered"])

# ══ A16-M 第 7 档：动作迁移 —— 【工作台已下线】（2026-10-08 引擎剥离进网关轨）══════════════
# 台下线但 motion-transfer 的意图登记保留（排他作用）：登记了它 ⇒ 别的台一律不挂，
# 而它自己也【永远不弹】——不许因为"登记过"就再长出一个台来。
hb.set_intent("session-MT-1", "motion-transfer", "女装带货-连衣裙01")
mt = hb.workbench_presence("session-MT-1")
check("A16m 登记 motion-transfer ⇒ 台下线不弹，但排他照样生效（omni/hypit/反推一律不挂）",
      [mt["motion-transfer"]["intent"], mt["omni"]["intent"], mt["hypit"]["intent"],
       mt["prompt-reverse"]["intent"]], [False, False, False, False])
check("A16m2 登记了也如实说『台已下线』（intent 恒 False + reason 可诊断，不骗人说正在生成）",
      [mt["motion-transfer"]["intent"], mt["motion-transfer"]["reason"]], [False, "workbench-retired"])
check("A16m3 别的会话没登记 ⇒ 零 UI（痕迹/旧任务永不触发页签）",
      [hb.motion_transfer_status("session-NEVER-MT")["intent"],
       hb.motion_transfer_status("session-NEVER-MT")["reason"]], [False, "no-intent"])
hb.set_intent("session-MT-2", "omni", "鼻贴成片")
mt2 = hb.motion_transfer_status("session-MT-2")
check("A16m4 同一条会话登记的是别的技能 ⇒ 动作迁移不挂（reason 说清是谁的意图）",
      [mt2["intent"], mt2["reason"]], [False, "intent-is-omni"])
check("A16m5 presence 键集仍含 motion-transfer（键不动、只是恒不弹；PRESENCE_KEYS 已钉名单）",
      "motion-transfer" in mt, True)
ALIVE["reverse"] = True
hb.set_intent("session-REV-1", "omni", "切回电商线")
r3 = hb.prompt_reverse_status("session-REV-1")
check("A16r5 改登记即让位（排他可逆，理由可诊断）",
      [r3["intent"], r3["reason"]], [False, "intent-is-omni"])
# 🔴 铁律三修推翻了旧断言（原来写"同会话跨两条流水线都弹=合法"）：显式登记是【排他】的，
#    claim 顺手钉了 hypit 意图 ⇒ omni 必须不挂。要回 omni 就重新 intent omni。
check("A16b 登记排他：说了 hypit 就不给 omni 台",
      [both["omni"]["intent"], both["hypit"]["intent"]], [False, True])
check("A16c omni 侧理由可诊断", both["omni"]["reason"], "intent-is-hypit")
hb.set_intent("session-OMNI-1", "omni", "切回电商线")
back = hb.workbench_presence("session-OMNI-1")
check("A16d 改登记即换台（排他可逆）", [back["omni"]["intent"], back["hypit"]["intent"]], [True, False])

# ── W 同工程有装配形态 ⇒ 台不许停在生成工程上（2026-10-03 "视频都出来了导演台还不同步"）──────
WSW = os.path.join(SCRATCH, "w_ws")
shutil.rmtree(WSW, ignore_errors=True)
WD = os.path.join(WSW, "productions", "wtest")
os.makedirs(WD)
with open(os.path.join(WD, "build.svrun"), "w", encoding="utf-8") as f:
    f.write("<run/>")
_saved_ws = hb.HYPIT_WORKSPACE
hb.HYPIT_WORKSPACE = WSW
SERVED["path"] = "productions\\wtest\\build.svml"
try:
    hb.studio_claim("session-W", "productions/wtest/build.svrun", 5179)
    st = hb.studio_status("session-W")
    check("W1 没有装配形态 ⇒ 台照旧开在 build.svrun（不误伤老行为）",
          [st.get("up"), st.get("run"), st.get("reason")], [True, "productions/wtest/build.svrun", None])
    with open(os.path.join(WD, "assemble.svrun"), "w", encoding="utf-8") as f:
        f.write("<run/>")
    st2 = hb.studio_status("session-W")
    check("W2 出现 assemble.svrun ⇒ 不许再报 up（旧台服务的是另一个文件，报上去=把 500 的台挂进 iframe）",
          [st2.get("up"), st2.get("reason"), st2.get("state")], [False, "assemble-preferred", "not-claimed"])
    check("W2b 目标换成装配工程，旧 claim 只作为显示",
          [str(st2.get("run")).endswith("assemble.svrun"), str(st2.get("stale_claim")).endswith("build.svrun")],
          [True, True])
    check("W2c _session_project 同口径（且保持相对路径形态，别突然变绝对）",
          hb._session_project("session-W").replace("\\", "/"), "productions/wtest/assemble.svrun")
    check("W2d 归属没被改写（run_key/claim 仍是这条会话那个工程，换的只是打开目标）",
          mark()["productions/wtest"]["run"], "productions/wtest/build.svrun")
finally:
    hb.HYPIT_WORKSPACE = _saved_ws
    hb.studio_release("session-W", "productions/wtest/build.svrun")
    hb.intent_clear("session-W") if hasattr(hb, "intent_clear") else None
    shutil.rmtree(WSW, ignore_errors=True)

# ══ B HTTP 层（真 8100 + 真 studio）═══════════════════════════════════════════════
# ⚠️ 这段会真写线上登记文件（8100 读的就是它），用完只摘自己那一条。
REAL_MARK = r"D:\video-skill\.gateway_studio.json"


def cli(*args):
    drop = ("HYPIT_STUDIO_MARK", "HYPIT_INTENT_MARK")
    env = {k: v for k, v in os.environ.items() if k not in drop}   # CLI 要写 8100 读的那两份
    return subprocess.run([sys.executable, r"D:\video-skill\backend\hypit_bridge.py", *args],
                          capture_output=True, text=True, cwd=r"D:\video-skill", env=env)


def http(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"omni": {"reason": "gateway-unreachable:" + type(e).__name__},
                "hypit": {"reason": "gateway-unreachable"}}


def port_open(port, timeout=1.2):
    """只问一句"这个端口有没有人接着"——用来把【依赖外部进程活着】的断言和规则本身分开。"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def real_runs():
    try:
        with open(REAL_MARK, encoding="utf-8") as f:
            return json.load(f).get("runs") or {}
    except (OSError, ValueError):
        return {}


if http(GATEWAY).get("hypit", {}).get("reason") != "ops-view":
    print("\n!! 8100 没在跑或判定口不是新版，跳过 HTTP 段")
    fails.append(("B0 网关可达", http(GATEWAY).get("hypit", {}).get("reason"), "ops-view"))
else:
    before = real_runs()
    j = http(GATEWAY)
    check("B1 不带 session 只回运维视图", [j["omni"]["reason"], j["hypit"]["reason"]], ["ops-view", "ops-view"])
    check("B2 陌生会话两台都无意图",
          [http(GATEWAY + "?session=session-NOT-MINE")[k]["intent"] for k in ("omni", "hypit")], [False, False])

    cli("studio-claim", ME, RUN, "5179")
    mine = http(GATEWAY + "?session=" + ME)["hypit"]
    # 🔴 B3/B4 钉的是"主人能不能拿到 up"，而这半件事【依赖那台 studio 真的活着】—— 而 8100 一重启，
    #    它自己养着的查看器子进程会被一起带走（2026-10-11 深夜实测：重启后这两条随机红）。
    #    依赖外部进程活着的断言是假保险：红了查不出规则坏没坏。所以先探端口，
    #    没人活着就【出声 SKIP】—— 不判红，也不算过。
    if not port_open(5179):
        print("\n!! B3/B4 SKIP：127.0.0.1:5179 上没有活着的 studio（多半是 8100 刚重启，"
              "把它养的那份查看器一起带走了）⇒ 这两条本轮不判，也不算通过")
    else:
        check("B3 主人拿到 up", mine.get("up"), True)
        check("B4 交叉核对生效（studio 自报路径对上）", mine.get("run_verified"), True)
    check("B5 端口由判定口给", mine.get("port"), 5179)

    # B8 🔴 装配线，不是规则：A16-T 拿桩验了顶替逻辑，但"8100 到底把 form_presence_view 注册进去没有"
    #     只有打真判定口才测得到 —— 漏了这条就是"盘上有码、线上没生效"（这一类我已经踩过两次）。
    FSID = "session-PRESENCE-B8"
    cli("intent", FSID, "prompt-reverse", "顶替装配对照")
    f8 = http(GATEWAY + "?session=" + FSID)
    check("B8 真 8100：登记有表的线【还没回填也弹】+ 引擎页仍被顶掉（这条验装配：新代码真在线上跑）",
          [f8.get("form", {}).get("intent"), f8.get("form", {}).get("surfaced"),
           f8.get("form", {}).get("prefilled"), f8.get("form", {}).get("has_input"),
           f8.get("prompt-reverse", {}).get("intent"),
           f8.get("prompt-reverse", {}).get("reason")],
          [True, True, False, True, False, "form-is-input"])
    cli("intent-clear", FSID)         # 自测登记不留在线上（B6/B7 会核对真的清掉了）

    cli("studio-release", ME)
    cli("intent-clear", ME)          # release 只撤 run 归属；意图要显式清，否则自测会话留在生产文件里
    after = real_runs()
    left = [k for k, v in after.items() if ME in ([str(x) for x in (v.get("sessions") or [])] + [str(v.get("session" or ""))])]
    check("B6 自测登记已清干净", left, [])
    # 🔴 B7 钉的是"别人的归属一条都不许少"，不是"键集合不许变"：本闸自己的 fixture（RUN=productions/hypit-e2e）
    #    收尾时 release 掉、键整个消失，是【应该的】—— 上一轮跑留下的键被这一轮收掉，原判据会把它当成
    #    "动了别人的登记"（2026-10-11 深夜实测：8100 重启后 studio 没了，B3/B4 转 SKIP，这条就冒出来了）。
    #    所以按【键上还有没有别人的会话】判，而不是按"少了没少"判。
    _gone = sorted(set(before) - set(after))
    _foreign = [k for k in _gone
                if [s for s in ([str(before[k].get("session") or "")] +
                                [str(x) for x in (before[k].get("sessions") or [])]) if s and ME not in s]]
    if _gone and not _foreign:
        print("   （B7 说明：消失的键 %s 上只有本闸自测会话 ⇒ 是 fixture 收尾，不算动了别人的）" % _gone)
    check("B7 没动到别人的登记（判据=键上还有别人的会话，不是键集合没变）", _foreign, [])

shutil.rmtree(FAKE_RUNS, ignore_errors=True)
if os.path.exists(hb.INTENT_MARK):
    os.remove(hb.INTENT_MARK)
if os.path.exists(hb.STUDIO_MARK):
    os.remove(hb.STUDIO_MARK)
if os.path.exists(os.environ["HYPIT_JOBS_DB"]):
    os.remove(os.environ["HYPIT_JOBS_DB"])

# ── V 复用必须验"这台到底在服务哪个工程"（2026-10-03：claim 默认记 5179，而 5179 挂着别的工程）──
_real_free = hb._free_studio_port
hb._free_studio_port = lambda: None          # 本段绝不允许真起进程：走到"新起"分支就会拿到 no-port
hb.studio_claim(ME, RUN, 5179)
SERVED["path"] = "productions\\hypit-e2e\\assemble.svml"        # studio 自报 .svml、claim 记 .svrun：同一个工程
r_same = hb.studio_up(ME, RUN)
check("V 同名工程不同扩展 ⇒ 复用同一台（不另起进程）", [r_same.get("reused"), r_same.get("port")], [True, 5179])
SERVED["path"] = "productions\\video_1789805487\\assemble.svml"   # 端口活着，但服务的是另一个工程
r_other = hb.studio_up(ME, RUN)
check("Vb 端口活着但服务别的工程 ⇒ 绝不复用（宁可去起新的）",
      [r_other.get("reused"), r_other.get("reason")], [None, "no-port"])
hb._free_studio_port = _real_free
hb.studio_release(ME, RUN)

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for name, got, want in fails:
    print("  FAIL", name, "got", got, "want", want)
sys.exit(1 if fails else 0)
