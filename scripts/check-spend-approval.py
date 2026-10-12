#!/usr/bin/env python
r"""花钱批准闸（spend approval）回归 · 用户 2026-10-06 定的铁律："只要是生成视频相关的，必须算账我确认"。

跑法：python scripts/check-spend-approval.py

钉的是【规则】不是现场，全离线：批准条子文件与积分账本都指到临时目录，**绝不碰线上 .gateway_ledger.sqlite**。
1 G1 清单唯一源：视频类要批（generate+edit），文本/看图/生图/改图免批
2 G2-G11 真值表：把纯函数 approval_allows 整块抠出来跑（够/不够/过期/换purpose/换账户/通配）
3 G12-G15 顺序与副作用：闸必须钉在 precheck/preauth **之前**；拒绝时不得建 hold、不得发 HTTP
4 G16 真实调用一遍：无条子 ⇒ ok:false + 账本审计前后完全一致（0 分、0 预扣）
5 G17-G19 绕网关的那条腿（8090 /api/h3-studio/generate）认同一张条子；CLI 三个动词在位
"""
import json
import os
import re
import subprocess as sp
import sys
import tempfile

GW_DIR = r"D:\video-skill"
APP_PY = os.path.join(GW_DIR, "app.py")
GATEWAY_PY = os.path.join(GW_DIR, "backend", "gateway.py")

SCRATCH = tempfile.mkdtemp(prefix="spend-approval-gate-")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ["PYTHONPATH"] = GW_DIR
sys.path.insert(0, GW_DIR)

TOTAL = 0
fails = []


def _safe(s):
    """GBK 控制台上「⇒/🔴」会让 print 抛 UnicodeEncodeError 崩掉整个闸。
    🔴 只按输出流编码做 errors='replace'，绝不 sys.stdout.reconfigure（那会造成"零输出 + exit 0"的静默假绿）；
       也别写裸 s.encode()：str.encode() 默认 utf-8，永远不失败，等于没兜住。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    global TOTAL
    TOTAL += 1
    a = json.dumps(got, ensure_ascii=False, default=str)
    b = json.dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want))))
    if a != b:
        fails.append(name)


from backend import gateway as gw   # noqa: E402  环境变量已隔离，这里 import 不会读到线上簿

# 2026-10-08 花钱批准加了人证前置（弹窗点【是】+ 回敲确认码）。闸要直接构造条子，
# 所以这里显式声明自测模式。它只在 GATEWAY_SPEND_APPROVALS 已改到 scratch 时生效
# （gateway.py 里那条双保险），生产账本物理上签不出来。
gw.ALLOW_GRANT_WITHOUT_ATTEST = True

# ── G1 清单：2026-10-09 用户最终口径"除了生图片和推理，其他都要审批" ⇒ 判据【默认要批 + 免批白名单】
#    反向的理由：正向清单会在新增能力时静默漏掉（video.edit 就是这么被"免批"跑掉真分的）。
check("G1 免批白名单 = 生图片 + 两条推理类",
      sorted(gw.SPEND_APPROVAL_EXEMPT), ["image.generate", "text.complete", "video.analyze"])
check("G1a 底线：生视频绝不能免批", gw.needs_spend_approval("video.generate"), True)
check("G1b 免批的确实不拦", [gw.needs_spend_approval(c) for c in gw.SPEND_APPROVAL_EXEMPT],
      [False] * len(gw.SPEND_APPROVAL_EXEMPT))
check("G1b2 视频加工/译制/配音/换脸 全都要批（都产出媒体文件、都花真分）",
      [gw.needs_spend_approval(c) for c in ("video.edit", "lipsync", "audio.generate", "image.edit",
                                            "transcribe")], [True] * 5)
check("G1c 🔴 未知/以后新增的能力默认【要批】（正向清单会在这里静默放行）",
      [gw.needs_spend_approval("brand.new.capability"), gw.needs_spend_approval(""),
       gw.needs_spend_approval(None)], [True, True, True])
check("G1d REQUIRED 是 CAPABILITIES 派生的（两份不一致=有人另抄了一份清单）",
      sorted(gw.SPEND_APPROVAL_REQUIRED),
      sorted([c for c in gw.CAPABILITIES if c not in gw.SPEND_APPROVAL_EXEMPT]))
check("G1e 免批白名单里不许混进任何视频/音频产出类",
      [c for c in gw.SPEND_APPROVAL_EXEMPT if c.startswith(("video.generate", "video.edit", "lipsync", "audio"))], [])

# ── G2-G11 真值表：纯函数整块抠出来跑（只看终态会靠奇偶假绿）─────────────────────
NOW = 1_000_000.0
G = lambda **kw: dict({"id": "ok_t", "capability": "video.generate", "purpose": "omni-t2v",
                       "account": "local", "count": 3, "used": 0, "credits": 120,
                       "used_credits": 0, "expires": NOW + 3600}, **kw)
A = gw.approval_allows
cases = [
    ("G2 额度够、段数够 ⇒ 放行", A(G(), "video.generate", "omni-t2v", "local", NOW, 1, 40), True),
    ("G3 条子过期 ⇒ 拒", A(G(expires=NOW - 1), "video.generate", "omni-t2v", "local", NOW, 1, 40), False),
    ("G4 能力不对 ⇒ 拒（批的是图，不能拿去生视频）",
     A(G(capability="image.generate"), "video.generate", "omni-t2v", "local", NOW, 1, 40), False),
    ("G5 purpose 不对 ⇒ 拒（批的是 omni-t2v，不能拿去跑 h3）",
     A(G(purpose="h3"), "video.generate", "omni-t2v", "local", NOW, 1, 40), False),
    ("G6 purpose=* ⇒ 该能力下所有路由都算数",
     A(G(purpose="*"), "video.generate", "h3", "local", NOW, 1, 40), True),
    ("G7 账户不对 ⇒ 拒（别人会话的额度不是你的）",
     A(G(account="omni"), "video.generate", "omni-t2v", "local", NOW, 1, 40), False),
    ("G8 段数不够 ⇒ 拒（批 3 段要发 4 段）",
     A(G(count=3, used=1), "video.generate", "omni-t2v", "local", NOW, 3, 40), False),
    ("G9 分数不够 ⇒ 拒（剩余额度 40 分要发 60 分）",
     A(G(credits=120, used_credits=80), "video.generate", "omni-t2v", "local", NOW, 1, 60), False),
    ("G10 只用过 1 段 ⇒ 还剩 2 段放行", A(G(used=1), "video.generate", "omni-t2v", "local", NOW, 2, 80), True),
    ("G11 空条子/坏条子 ⇒ 拒（不许拿 None 蒙过）",
     [A({}, "video.generate", "omni-t2v", "local", NOW, 1, 1),
      A(None or {}, "video.generate", "omni-t2v", "local", NOW, 1, 1)], [False, False]),
]
for name, got, want in cases:
    check(name, got, want)

# ── G11b-G11e 会话隔离（2026-10-06 事故补的：不隔离就是互相偷花）───────────────────
# 事故：我拿一次不相干的端点探测，吃掉了另一会话为 "video-enhance 封面" 签的 1 段/4 分额度。
# 根因不是闸没跑（它跑了、也判对了 capability），而是条子只按 能力/purpose/账户 匹配 ⇒ 谁都能消费别人的额度。
SB = dict({"id": "ok_s", "capability": "image.generate", "purpose": "default", "account": "local",
           "count": 2, "used": 0, "credits": 8, "used_credits": 0, "expires": NOW + 3600,
           "session": "session-AAA"}, )
check("G11b 条子绑了会话 ⇒ 同一条会话能用",
      A(SB, "image.generate", "default", "local", NOW, 1, 4, "session-AAA"), True)
check("G11c 条子绑了会话 ⇒ 别的会话一律拒（这次事故的正解）",
      A(SB, "image.generate", "default", "local", NOW, 1, 4, "session-BBB"), False)
check("G11d 探测方不报会话 ⇒ 也不能用掉别人的额度（空会话 ≠ 主人的会话）",
      A(SB, "image.generate", "default", "local", NOW, 1, 4, ""), False)
check("G11e 没绑会话的条子=通用条子，谁都能用（这正是危险的那种，签的时候要写 --session）",
      A(dict(SB, session=""), "image.generate", "default", "local", NOW, 1, 4, "session-ZZZ"), True)

# ── G12-G15 顺序与副作用：闸必须在预扣之前，拒绝时不得留任何痕迹 ────────────────────
src = open(GATEWAY_PY, encoding="utf-8", errors="replace").read()
code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
i_gate = code.find("check_spend_approval(capability, r[\"purpose\"], account")
i_precheck = code.find("ledger().precheck(account, est)")
i_preauth = code.find("ledger().preauth(account, capability")
check("G12 invoke 里闸找得到、且在 precheck/preauth 之前（先扣再问=没闸）",
      [i_gate > 0, i_gate < i_precheck, i_gate < i_preauth], [True, True, True])
deny_body = code[i_gate:i_precheck]
check("G13 拒绝分支里不出现 preauth/commit/rollback（0 预扣才是 0 消耗）",
      [k in deny_body for k in ("preauth", "commit(", "rollback(")], [False, False, False])
# G14 原来把 "approval_required" 这句字面量当判据，可那句话是在 check_spend_approval() 里生成的
#     （invoke 只负责透传）⇒ 判错了对象。运行时真相由 G16c 验（信封里确有 quote）；这里只钉"不许吞掉"。
check("G14 拒绝分支把报价与错误原样透传给人（不吞、不改写成一句'不行'）",
      ["quote" in deny_body and '"error": gate["error"]' in deny_body], [True])
check("G15 needs_spend_approval 是唯一判入口（invoke 里不另写一份能力清单）",
      len(re.findall(r"SPEND_APPROVAL_REQUIRED", code.split("def invoke(")[1].split("def _cli")[0])), 0)

# ── G16 真跑一遍：无条子 ⇒ ok:false，且账本审计前后一模一样（不是"看起来没扣"）─────────
audit_before = gw.ledger().audit()
env = gw.invoke("video.generate", "omni-t2v", "local", {})
audit_after = gw.ledger().audit()
check("G16 无条子 ⇒ ok:false / charged=0 / 带 error",
      [env["ok"], env["credits"]["charged"], bool(env.get("error"))], [False, 0, True])
check("G16b 拒绝没动账本（holds/commits 前后完全一致）",
      [json.dumps(audit_before, sort_keys=True, default=str) ==
       json.dumps(audit_after, sort_keys=True, default=str)], [True])
check("G16c 拒绝时报价结构齐（capability/purpose/unit_price/credits/balance_after）",
      sorted(k for k in (env.get("approval") or {}).get("quote", {}) if
             k in ("capability", "purpose", "unit_price", "credits", "count", "balance_after")),
      sorted(["capability", "purpose", "unit_price", "credits", "count", "balance_after"]))
check("G16d 免批类不花钱就能过闸（文本腿不该被拦）",
      gw.check_spend_approval("text.complete", "agnes", "local", 1, 1)["ok"], True)
g_ok = gw.grant_spend_approval("video.generate", "omni-t2v", "local", 1, 9999, "本闸自测条子")
r_ok = gw.check_spend_approval("video.generate", "omni-t2v", "local", 1, 40)
check("G16e 有条子 ⇒ 放行并记账（正向对照，缺它就等于全程假绿）",
      [r_ok["ok"], r_ok["grant"] == g_ok["id"], r_ok["remaining"]], [True, True, 0])
check("G16f 同一条子用完即失效（再用就拒）",
      gw.check_spend_approval("video.generate", "omni-t2v", "local", 1, 40)["ok"], False)
gw.revoke_spend_approval(g_ok["id"])

# ── G17 绕网关的腿：8090 的 handler 调的是【同一个守卫】，守卫里再走同一张条子 ──────────
app_src = open(APP_PY, encoding="utf-8", errors="replace").read()
GATED_LEGENDS = [
    ("/api/h3-studio/generate", '"video.generate", "h3"'),
    ("/api/one-shot-replica/generate", '"video.generate", "*"'),
    ("/api/replica-wan3/submit", '"video.generate", "*"'),
    ("/api/hook-extend/submit", '"video.generate", "omni-t2v"'),
    ("/api/agnes-image/submit", '"image.generate", "default"'),
]
for path, expect_args in GATED_LEGENDS:
    seg = app_src[app_src.find('@app.post("%s")' % path):][:1500]
    check("G17 %s 走同一个花钱守卫（不是只靠文档禁调）" % path,
          [bool(seg), "_spend_or_423(" in seg, expect_args in seg], [True, True, True])
h = app_src[app_src.find("def _spend_or_423"):][:520]
check("G17a 守卫本体只做一件事：调 gateway 的同一个 spend_guard，把拒绝转成 423",
      ["spend_guard(" in h, "SpendApprovalRequired" in h, "HTTPException(423" in h], [True, True, True])
check("G17b 单价从价目表读（spend_guard 里 unit_price/上限），app.py 里不写死积分数",
      ["unit_price(" in src and "unit_price_ceiling(" in src,
       bool(re.search(r"credits\s*=\s*\d+", app_src))], [True, False])
check("G17c 免批腿不该被误拦（反推那条口仍然不调守卫）",
      "_spend_or_423(" in app_src[app_src.find('@app.post("/api/prompt-reverse/submit")'):][:900], False)
# 子进程 stdout 的编码由它自己的 locale 决定（这台机器上可能是 GBK）⇒ 不指定就是"父按 utf-8 解子写的 GBK"，
# 用法行被解成乱码、断言当然红。给子进程显式 PYTHONIOENCODING=utf-8，两边口径钉死。
# ⚠️ 这不是"遮 bug"：这条断言测的是动词能不能跑/回不回用法，不是测编码；编码本身由 gateway CLI 的 _cprint 负责。
_CLI_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
cli = sp.run([sys.executable, GATEWAY_PY, "approvals"], capture_output=True, text=True, env=_CLI_ENV,
             encoding="utf-8", errors="replace")
check("G18 CLI approvals 能跑（临时簿，不碰线上）",
      [cli.returncode, "批准条子" in (cli.stdout or "")], [0, True])
# G19 原来跑"无参数"想拿用法行，但无参数时 cmd 默认走 caps（不是 else 分支）⇒ 断言判错了对象。
#     用法串是源码里那句 print，直接在源码上钉；动词真的能跑由 G18 + 下面这条 approve 空参验证。
# 🔴 锚点必须唯一：2026-10-09 我加了一条 `upload` 子命令并写了它自己的用法串，
#     那句 "用法: python backend/gateway.py upload …" 比总用法串【先】出现 ⇒ split 拿到的成了它，
#     G19 三条同时假红。锚点改成带方括号的总用法串（只有分发器那一条有），并把"唯一"本身钉住。
USAGE_ANCHOR = "用法: python backend/gateway.py ["
check("G19a 用法串锚点在源码里唯一（不唯一 split 就会拿错段）",
      code.count(USAGE_ANCHOR), 1)
check("G19 用法串里三个动词都在（approve/approvals/revoke）",
      [v in code.split(USAGE_ANCHOR)[1][:520]
       for v in ("approve", "approvals", "revoke")], [True, True, True])
check("G19c 新增的 upload 动词也在用法串里（别让它变成只有源码知道的命令）",
      "upload <file>" in code.split(USAGE_ANCHOR)[1][:520], True)
ap = sp.run([sys.executable, GATEWAY_PY, "approve"], capture_output=True, text=True, env=_CLI_ENV,
            encoding="utf-8", errors="replace")
check("G19b approve 少参数时回用法并以 2 结束（不静默签一张空条子）",
      [ap.returncode, "用法" in (ap.stdout or "")], [2, True])

# ── G27 遗留预扣的回收口（2026-10-09：AGENTS.md 早就写着 holds-pending/holds-rollback，
#    但 CLI 里【根本没有这两个动词】⇒ 文档跑到代码前面，agent 照着敲只能撞用法串）────
def cli27(*args):
    return sp.run([sys.executable, GATEWAY_PY, *args], capture_output=True, text=True,
                  env=_CLI_ENV, encoding="utf-8", errors="replace", timeout=90)

_a0 = json.dumps(gw.ledger().audit(), sort_keys=True, default=str)
h = cli27("holds")
hj = {}
try:
    hj = json.loads(h.stdout or "{}")
except Exception:
    pass
check("G27 CLI 有 holds 动词且返回结构化清单",
      [h.returncode, "pending" in hj, "total_credit" in hj], [0, True, True])
check("G27b holds 是只读的（跑完账本审计逐字不变）",
      json.dumps(gw.ledger().audit(), sort_keys=True, default=str) == _a0, True)
check("G27c 新行带 ts/age_hours（旧行留 None，不假装知道年龄）",
      all(("age_hours" in r and "ts" in r) for r in hj.get("pending", [])), True)
for bad in ("all", "*", "--all", "-y"):
    rb = cli27("holds-rollback", bad)
    check("G27d 批量开关 %r 一律拒（退正在花的分=可以再花一次=双花）" % bad,
          [rb.returncode, json.dumps(gw.ledger().audit(), sort_keys=True, default=str) == _a0],
          [2, True])
rb0 = cli27("holds-rollback")
check("G27e 不给 id ⇒ 退 2 并回用法（不静默退任何东西）",
      [rb0.returncode, "hold_id" in (rb0.stdout or "")], [2, True])
_h = gw.ledger().preauth("local", "video.generate", "omni-t2v", 40)
rb1 = json.loads(cli27("holds-rollback", _h["hold_id"]).stdout or "{}")
check("G27f 指名退一笔 ⇒ 退款正确、余额回来",
      [rb1.get("ok"), rb1.get("refunded")], [True, 40])
rb2 = json.loads(cli27("holds-rollback", _h["hold_id"]).stdout or "{}")
check("G27g 同一 id 再退 ⇒ 拒（幂等，不重复退钱）", rb2.get("ok"), False)
_ws = open(r"C:\dsh\AGENTS.md", encoding="utf-8").read()   # WS_DOC 在下面才定义，这里自己读一次
check("G27h 文档里写的回收命令必须【与 CLI 实际动词逐字一致】（文档不许再跑到代码前面）",
      ["holds-rollback" in _ws, 'cmd == "holds"' in src, 'cmd == "holds-rollback"' in src,
       "holds-pending" in _ws], [True, True, True, False])

# ── G24 CLI 的"拒绝消息"必须读得出来（GBK 管道，故意不设 PYTHONIOENCODING）──────────
# 2026-10-06 实测：`gateway.py call image.generate doubao`（那时生图还要批）被闸正确拒了，但拒绝文案里带一个 `⇒`，
# print 在 GBK 控制台上抛 UnicodeEncodeError ⇒ 人只看到 traceback，最需要看的那句话丢了。
# 这条断言【故意】不给子进程 PYTHONIOENCODING，就是要复现那个环境；隔离条子文件是空的 ⇒
# 一定走拒绝分支 ⇒ 不起浏览器、0 消耗。
env_gbk = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONIOENCODING"}
env_gbk["PYTHONUTF8"] = "0"
callp = sp.run([sys.executable, GATEWAY_PY, "call", "video.generate", "omni-t2v", "--json",
                json.dumps({"prompt": "x", "duration": 5})],
               capture_output=True, env=env_gbk, timeout=180)
cout = (callp.stdout or b"").decode("gbk", "replace")
cerr = (callp.stderr or b"").decode("gbk", "replace")
check("G24 无条子时 CLI 退 0、拒绝消息在 GBK 管道下读得出来（不炸 traceback）、且 0 扣分",
      [callp.returncode, "approval_required" in cout, "UnicodeEncodeError" in cerr, '\"charged\": 0' in cout],
      [0, True, False, True])
check("G24b 拒绝时不落任何文件（闸在适配器之前，豆包浏览器根本没被拉起来）",
      os.path.exists(os.path.join(SCRATCH, "g24-nope.png")), False)

# ── G20/G21 行为级双向对照：把 call() 换成记账桩（不联网、不花钱），只验"闸拦不拦得住发起" ──
_real_call = gw.call
_hit = {"n": 0}


def _stub_call(capability, purpose, **params):
    _hit["n"] += 1
    return {"success": True, "stub": True}


gw.call = _stub_call
try:
    gw.invoke("video.generate", "omni-t2v", "local", {})
    blocked = [_hit["n"], gw.ledger().audit()["by_route"]]
    gw.grant_spend_approval("video.generate", "*", "local", 1, 1000, "自测：签一张就该放行")
    env_pass = gw.invoke("video.generate", "omni-t2v", "local", {})
    check("G20 没条子 ⇒ call() 一次都没被调到（真·发起不了，不是发起了再退款）", blocked[0], 0)
    check("G21 签了条子 ⇒ 同一条调用真的走到发起口（正向对照，防「永远拒」式的假安全）",
          _hit["n"], 1)
    print(_safe("     桩返回不是真实结构，信封 ok 可能为 false，这里只钉有没有调到发起口；本次 ok=%s"
                % env_pass.get("ok")))

    # ── G22-G23 失败退段：上游拒/参数错/落盘失败 ⇒ 批准段数必须还回来（0 消耗不该吃额度）──────
    # 🔴 额度要给得"明显够"，并且要断言错误来自【被桩模拟的发起阶段】而不是闸——
    #    否则条子不够用时调用压根没通过闸，"used 还是 0"就变成假绿（本会话第一版就是这么假绿过一次）。
    _g22 = gw.grant_spend_approval("video.edit", "enhance", "local", 2, 99999, "自测：失败要退段")
    gw.call = lambda capability, purpose, **params: {"success": False, "error": "模拟上游拒：一个提交都没发出去"}
    _e1 = gw.invoke("video.edit", "enhance", "local", {})
    _left = [x for x in gw._approvals_load()["grants"] if x["id"] == _g22["id"]][0]
    check("G22a 这次确实过了闸、死在发起阶段（不是被闸挡回来的）",
          ["approval_required" not in str(_e1.get("error")), "模拟上游拒" in str(_e1.get("error"))], [True, True])
    check("G22b 业务失败 ⇒ 段数与分数都退回 0（不留幽灵消耗）",
          [_e1["ok"], _left["used"], _left["used_credits"]], [False, 0, 0])

    def _boom(capability, purpose, **params):
        raise RuntimeError("模拟抛异常")
    gw.call = _boom
    _e2 = gw.invoke("video.edit", "enhance", "local", {})
    _left2 = [x for x in gw._approvals_load()["grants"] if x["id"] == _g22["id"]][0]
    check("G23 抛异常 ⇒ 同样退回，且同样是真的过了闸",
          [_e2["ok"], "approval_required" not in str(_e2.get("error")), _left2["used"]], [False, True, 0])
    gw.revoke_spend_approval(_g22["id"])
    # ── G25 段数的两种形状都得吃（2026-10-06 实测事故：H3 RPA 传 segments=[{...}] 进来，
    #    闸里那句 int(params.get("segments")) 直接 TypeError 把整次调用炸掉 —— 0 消耗但闸自己成了故障点）──
    g25 = gw.grant_spend_approval("video.generate", "h3-rpa", "local", 1, 99999, "自测：segments 列表算段数")
    gw.call = lambda capability, purpose, **params: {"success": True, "path": "stub.mp4"}

    def _try_call(purpose, params):
        """闸不许被被测代码炸掉：炸了就当场记下成【值】，让断言红，而不是整闸崩（崩了分不清是规则红还是闸坏了）。"""
        try:
            return gw.invoke("video.generate", purpose, "local", params)
        except Exception as ex:
            return {"ok": "raised:%s" % type(ex).__name__, "error": repr(ex)}

    e_seg1 = _try_call("h3-rpa", {"segments": [{"prompt": "a"}]})
    check("G25a segments=[1 项] ⇒ 不炸、过闸、真发起了一次（列表段数=len；原来这里 TypeError 当场炸闸）",
          [e_seg1.get("ok"), "approval_required" not in str(e_seg1.get("error") or "")], [True, True])
    e_seg2 = _try_call("h3-rpa", {"segments": [{"prompt": "a"}, {"prompt": "b"}]})
    _l25 = [x for x in gw._approvals_load()["grants"] if x["id"] == g25["id"]][0]
    check("G25b 额度只剩 1 段时来了一条 2 段 ⇒ 明确拒（拒，不是崩），已用量不被吃掉",
          [e_seg2.get("ok"), "approval_required" in str(e_seg2.get("error") or ""), _l25["used"]],
          [False, True, 1])
    e_garbage = _try_call("h3-rpa", {"segments": "看不懂的形状"})
    check("G25c 认不出的形状按 1 段算：闸不许成为故障点（不抛、正常走拒或放行）",
          [isinstance(e_garbage.get("ok"), bool), "raised" not in str(e_garbage.get("ok"))], [True, True])
    gw.revoke_spend_approval(g25["id"])
finally:
    gw.call = _real_call

# ── G26 文档与闸口配对（2026-10-09 用户发现：全局层还写"花钱前的报价=必停"，而闸只拦 video.generate；
#          且 AGENTS.md 把 /api/agnes-image/submit 那道【空转守卫】写成"实测无条子回 423"）──────────
# 判据只有一条方向：文档必须跟着代码的唯一源走。代码改了口径而文档没跟上 ⇒ 红，别指望人记得改文档。
def _read(p):
    return open(p, encoding="utf-8", errors="replace").read() if os.path.isfile(p) else ""


WS_DOC = _read(r"C:\dsh\AGENTS.md")
GLB_DOC = _read(os.path.expandvars(r"%APPDATA%\dsh-desktop\harness\AGENTS.md"))


def doc_gate_pairing(required_caps):
    """纯函数：给定【要批的清单】，算出文档该长什么样。抽出来才能拿变异对照跑。
       🔴 判据要落到【具体那一行】：早先只判"文档里出现过『空转』两个字"，结果 AGENTS.md 里
          RunningHub 那条变异对照的说明也含"空转" ⇒ 为错的理由通过（假绿）。现在按端点标签逐行判。"""
    src = _read(r"D:\video-skill\app.py")
    calls = re.findall(r'_spend_or_423\(\s*"([\w.]+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"', src)
    caps = sorted({c for c, _p, _l in calls})
    noops = sorted({c for c in caps if c not in required_caps})
    # 标签形如 "agnes-image 出图" ⇒ 取首词当文档里的锚（文档写的是 /api/agnes-image/submit）
    noop_anchors = sorted({l.split()[0].rstrip("：:") for c, _p, l in calls if c in noops})
    return {"caps": caps, "noops": noops, "anchors": noop_anchors, "must_note": bool(noop_anchors)}


def unannotated(doc, anchors):
    """返回【没被标注成空转】的空转守卫锚点：锚点出现的那一行（及其下一行）里找不到"空转"就算没标。"""
    lines = doc.splitlines()
    bad = []
    for a in anchors:
        hit = [i for i, l in enumerate(lines) if a in l]
        if not hit or not any("空转" in lines[i] or (i + 1 < len(lines) and "空转" in lines[i + 1]) for i in hit):
            bad.append(a)
    return bad


P = doc_gate_pairing(set(gw.SPEND_APPROVAL_REQUIRED))
check("G26 8090 侧守卫调到的能力（读码得到，不抄文档）", P["caps"], ["image.generate", "video.generate"])
check("G26b 其中【不拦的空转守卫】= image.generate（口径改小之后，它只剩形）", P["noops"], ["image.generate"])
check("G26c 🔴 每一个空转守卫都得在【提到它的那一行】被写明（写成『实测回 423』= 拿假保护骗自己）",
      unannotated(WS_DOC, P["anchors"]), [])
# 🔴 不写死常量名，也【不用正则问】：`def needs_spend_approval.*?return.*?([A-Z_]{6,})` 这种写法
#    只抓第一个 return 之后的第一个大写串 —— 2026-10-12 加了 purpose 级免批表（第二个 return 分支）
#    之后，它抓到的既不是能力级表也不是"整份判据"，全局层点名谁都判不对 = 假红。
#    判据有几份就该点几名：拿 AST 把函数体里读到的全大写常量一次问清。
def judge_sources(text, fname):
    import ast
    tree = ast.parse(text)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fname), None)
    if fn is None:
        return []
    return sorted({n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                   and n.id.isupper() and len(n.id) >= 6})


SOURCE_CONSTS = judge_sources(src, "needs_spend_approval")
check("G26d 全局层『必停四类』必须点名 needs_spend_approval 读的【每一份】判据（AST 问出来，不写死也不正则截断）",
      [bool(SOURCE_CONSTS), SOURCE_CONSTS and all(c in GLB_DOC for c in SOURCE_CONSTS),
       set(SOURCE_CONSTS) <= {"SPEND_APPROVAL_EXEMPT", "SPEND_APPROVAL_EXEMPT_PURPOSES",
                              "SPEND_APPROVAL_REQUIRED"}],
      [True, True, True])
check("G26e 豁免表 SPEND_APPROVAL_AT_SOURCE 现在是空的 ⇒ 文档必须写『当前 = 空』（别把没住户的机制写成还在跑）",
      ("当前 = 空" in WS_DOC), (len(gw.SPEND_APPROVAL_AT_SOURCE) == 0))
# 变异对照：把 image.generate 加回清单 ⇒ G26c 的期望值必须翻成"不再需要标注"（证明它判的是配对，不是恒真）
P2 = doc_gate_pairing({"video.generate", "image.generate"})
check("G26f 变异：清单扩回两类 ⇒ 空转守卫归零（G26c 该随之不再要求标注，证明它判的是配对不是恒真）",
      [P2["noops"], P2["anchors"], P2["must_note"]], [[], [], False])
P3 = doc_gate_pairing(set())
check("G26g 变异：清单被清空（闸整体失效）⇒ 所有端点都成空转，文档必须标注（这条不许静默）",
      [P3["noops"] == ["image.generate", "video.generate"], P3["must_note"]], [True, True])

# ── G28 会话归属只许一处读法（2026-10-10 现网：三处各写一遍，唯独批准闸那处漏了 env 回落
#    ⇒ 按会话绑的条子永远匹配不上，拒因又被上层吞成 generation_failed，账本零痕迹）────
GW_SRC = open(os.path.join(GW_DIR, "backend", "gateway.py"), encoding="utf-8",
              errors="replace").read()

check("G28 判据 _session_of 值表：params 有 / params 空+env 有 / 都没有 / params 不是字典",
      [gw._session_of({"session": "session-A"}), gw._session_of({}),
       gw._session_of(None), gw._session_of({"session": "  "})],
      ["session-A", "", "", ""])
# 🔴 针只钉【调用方 params 这一路】。第一次我把针写成泛指 `get("session")`，当场误伤三处合法的：
#    :2632 是拿条子记录的 owner 和这条会话比、:2723 读的是条子字段、:3381 读的是命中项 ——
#    那些是"条子的属主"，不是"这次调用属于谁"，本来就不该回落 env。判据写宽 = 一堆假红逼人绕过它。
check("G28b 🔴 读「这次调用属于哪条会话」只有一处：params 那一路不许再出现第二份组合读法",
      [GW_SRC.count('params.get("session")'), GW_SRC.count("_session_of(") >= 3,
       GW_SRC.count('p.get("session") or _os.environ.get("DSH_SESSION_ID")')],
      [0, True, 1])
_synth = ('def a(p):\n    return str(p.get("session") or _os.environ.get("DSH_SESSION_ID") or "")\n'
          'def invoke2(params):\n    return check(params, session=str(params.get("session") or ""))\n')
check("G28c 对照：合成一份「两处读法、invoke 那处漏 env」的源码 ⇒ 必须数出 1 处（证明 G28b 不是恒 0）",
      _synth.count('params.get("session")'), 1)
os.environ["DSH_SESSION_ID"] = "session-env-only"
try:
    _from_env = gw._session_of({})
finally:
    os.environ.pop("DSH_SESSION_ID", None)
check("G28d 对照：env 里有会话时，params 不给也必须算得出会话（wan3-prime 就是这个形状）",
      [_from_env, gw._session_of({})], ["session-env-only", ""])
# 🔴 G28e：把"再开第二条读路径"变成当场红。今天这条洞的全部根源就是「同一件事两处读法」：
#    批准闸 :2871 只读 params、守卫 :167 只读 env、CLI 又自己读一遍 env
#    ⇒ 单看每处都不算错，合起来就是"按会话绑的条子永远匹配不上"，而拒因还被吞成 generation_failed。
#    教训不是"那处少读了 env"，是"这条读法压根不该有三份"——所以判据改成钉【站点点数】。
SESSION_ENV_SITES_OK = {
    "gateway.py": "唯一一处：_session_of()",
    "skill_contract.py": "例外：回执逐行署名用，导 gateway 会成环",
}


def session_env_sites(root):
    found = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "node_modules")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            n = sum(1 for l in open(os.path.join(dp, f), encoding="utf-8", errors="replace")
                    if "DSH_SESSION_ID" in l and "environ" in l)
            if n:
                found[f] = n
    return found


def session_offenders(sites, allowed):
    """判据抽成纯函数 ⇒ 能跑真值表，而不是拿"现状"当"应该"。"""
    return sorted(k for k in sites if k not in allowed)


_sites = session_env_sites(os.path.join(GW_DIR, "backend"))
check("G28e 🔴 读 DSH_SESSION_ID 的站点必须等于白名单（新开一处=第二条路径，先想清楚再登记）",
      [sorted(_sites), sum(_sites.values()), session_offenders(_sites, SESSION_ENV_SITES_OK)],
      [sorted(SESSION_ENV_SITES_OK), 2, []])
check("G28f 对照：白名单外多一处（模拟有人在执行器里自己读 env）⇒ 必须点名（证明 G28e 有牙）",
      session_offenders(dict(list(_sites.items()) + [("wan3_client.py", 1)]), SESSION_ENV_SITES_OK),
      ["wan3_client.py"])

# ── P 只读探额度（2026-10-10 现网：另一会话拿【消费函数】当"批没批到"的探针，吃光用户批过的段
#    ⇒ 钱没扣但批准没了 ⇒ 只能重开待批、逼人第二次点。根因是缺一条只读路，不是它想绕。）────
_gws = open(os.path.join(GW_DIR, "backend", "gateway.py"), encoding="utf-8", errors="replace").read()


def _used_of(gid):
    try:
        d = json.loads(open(gw.SPEND_APPROVALS_PATH, encoding="utf-8").read())
    except (OSError, ValueError):
        return None
    for x in d.get("grants", []):
        if x.get("id") == gid:
            return int(x.get("used", 0))
    return None


_PSID = "gate-peek-session"
_g1 = gw.grant_spend_approval("video.generate", "wan3-prime", "local", 1, 60,
                             "自测：只读探测不许吃额度", session=_PSID)
_b1 = open(gw.SPEND_APPROVALS_PATH, "rb").read()
_p1 = gw.peek_spend_approval("video.generate", "wan3-prime", "local", count=1, credits=60, session=_PSID)
_a1 = open(gw.SPEND_APPROVALS_PATH, "rb").read()
check("P1 🔴 peek 判 ALLOWED 的那一下不消费（字节不变 + used 仍 0 + 自报 consumed=False）",
      [bool(_p1.get("allowed")), _p1.get("consumed"), _used_of(_g1["id"]), _a1 == _b1],
      [True, False, 0, True])
_c1 = gw.check_spend_approval("video.generate", "wan3-prime", "local", count=1, credits=60, session=_PSID)
_a1b = open(gw.SPEND_APPROVALS_PATH, "rb").read()
check("P2 反向对照：同一张条子改调消费函数 ⇒ 字节必须变、used 必须 1（证明 P1 的「字节相同」是有牙判据）",
      [bool(_c1.get("ok")), _a1b != _a1, _used_of(_g1["id"])], [True, True, 1])
_p3 = gw.peek_spend_approval("video.generate", "wan3-prime", "local", count=1, credits=60, session=_PSID)
check("P3 段吃光后 peek 必须报 allowed=False 并把下一步指回「重开待批」（不许再让人去碰消费口）",
      [_p3.get("allowed"), "待批" in str(_p3.get("next"))], [False, True])
_ps = _gws.find("def peek_spend_approval")
_pe = _gws.find("def check_spend_approval", _ps)
_pk = _gws[_ps:_pe] if _ps >= 0 and _pe > _ps else ""
check("P4 peek 体内零写动作（不 _approvals_save、不给 used 赋值、不调 consume_*）",
      [k in _pk for k in ("_approvals_save(", 'used"] =', "consume_spend_approval(")],
      [False, False, False])
check("P4b 反向对照：判据读到了函数体本体（不是空串混过 P4）", len(_pk) > 800, True)
_i = _gws.find('if cmd == "allowance":')
_seg = _gws[_i:_i + 2400] if _i >= 0 else ""
check("P5 CLI 有 allowance 只读口，且它调 peek_spend_approval 而不是消费判据",
      [_i >= 0, "peek_spend_approval(" in _seg, "check_spend_approval(" in _seg], [True, True, False])
check("P6 allowance 的拒答/用法里明说「只读、不清段」（agent 看得见这句就不会去试探）",
      ("只读" in _seg and "不清段" in _seg), True)
_p6 = gw.peek_spend_approval("image.generate", "generic", "local")
check("P7 免批腿分开回答：requires_approval=False、allowed=True、但没有条子可指（免批 ≠ 批过）",
      [_p6["requires_approval"], _p6["allowed"], _p6["grant"]], [False, True, ""])
_aa = _gws[_gws.find("def approval_allows"):_gws.find("def peek_spend_approval")
           if _gws.find("def peek_spend_approval") > _gws.find("def approval_allows")
           else _gws.find("def grant_spend_approval")]
check("P8 🔴 同道判定只有一份：peek 与 approval_allows 都读 grant_lane_matches，谁都不许自己比 capability",
      ["grant_lane_matches(" in _pk, "grant_lane_matches(" in _aa,
       'g.get("capability") ==' in _pk, 'g.get("capability") ==' in _aa],
      [True, True, False, False])
check("P8b 反向对照：把 peek 改回自己比 capability ⇒ 同一条判据必须抓到（证明 P8 有牙）",
      'g.get("capability") ==' in _pk.replace("grant_lane_matches(g, capability, purpose, account, now, session)",
                                              'g.get("capability") == capability'), True)


print(_safe("\n=== 花钱批准闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails))))
if fails:
    print(_safe("失败清单: " + " | ".join(f.split()[0] for f in fails)))
print(_safe("临时目录（条子/账本都在里面，不落仓库）: " + SCRATCH))
sys.exit(1 if fails else 0)
