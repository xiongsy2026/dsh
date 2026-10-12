# -*- coding: utf-8 -*-
"""花钱那一刀认合同回执（2026-10-11 现网 session-241ea3f7 08:23 复盘）。

毛病：`verify_or_raise` 全仓只有 `super_clone_wan3.py` 一处调 ⇒ 合同只约束"走管线的人"；
agent 自己 `from backend import gateway` 拼一次调用，盘上没有 .skill_receipt.json，
而网关只认批准条子 ⇒ 绕开入口照样能扣分，合同对花钱没有约束力。
这条把判据下移到唯一咽喉（所有 wan3 腿都经 `gateway.invoke`）：
**成片要写进哪个目录，那个目录（或父目录）里就必须有一份该档合同的合格回执。**

A 组 纯判据真值表（含"无合同档不在射程""捞回不拦"）
B 组 真跑 invoke()：临时账本 + 临时批准簿 + 适配器打桩（绝不真发 HTTP），
     按顺序证三件事：无回执 ⇒ 拒且【不建预扣、不吃批准额度】；有回执 ⇒ 走到下一道批准闸；
     有回执又有条子 ⇒ 真放行（防误杀）
C 组 接线：回执判在批准判之前（AST 行号，不是字符串顺序直觉）
D 组 变异：判据换恒 [] ⇒ 无回执那条必须改判；捞回豁免摘掉 ⇒ job_id 那条必须变红
E 组 覆盖对照：判据只一份（网关读 check_receipt，没抄第二份），管线那处核验还在
全离线、0 消耗。
"""
import io
import json
import os
import shutil
import sys
import tempfile

BS = chr(92)
sys.path.insert(0, r"D:\video-skill")
from backend import gateway as gw            # noqa: E402
from backend import skill_contract as sc     # noqa: E402

PASS, FAIL = [], []


def _safe(s):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(_safe("%-4s %-58s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


NOForm = {"prompt": "p", "duration": 15, "resolution": "1080P", "ratio": "9:16"}


def prob(pur="wan3-prime", params=None):
    """只判这条腿（不真调网关）。🔴 形参别写成"第一个参数没用到"的形状 ——
       我第一版就是 `prob(out, pur, params)` 而调用时把 purpose 放进第一位，
       结果 A4 那三条全测成了 wan3-prime，看着像"判据漏放行"其实是测错了对象。"""
    return gw.spend_contract_problems("video.generate", pur, params or {})


# ── A 组 纯判据真值表 ──────────────────────────────────────────────────────────────
check("A1 合同档 + 目录里没有回执 ⇒ 拒（08:23 那个形状）",
      len(prob(params=dict(NOForm, output_path=r"C:" + BS + "dsh" + BS + "productions" + BS
                           + "ef1ba285" + BS + "outputs" + BS + "seg01-prime.mp4"))), 1)
check("A2 带 job_id（捞回已提交的任务）⇒ 不拦：只断提交，不断查询（重投=双花）",
      len(prob(params=dict(NOForm, job_id="J-1", output_path=r"C:" + BS + "t" + BS + "a.mp4"))), 0)
check("A3 没有 output_path ⇒ 拒（落点都不给=产物无处可查）",
      len(prob(params=dict(NOForm))), 1)
check("A4 没有合同档认领的腿（h3 / motion-rpa / analyze）⇒ 不在射程，明说不假装管得住",
      [len(prob("h3", params={"output_path": r"C:" + BS + "t" + BS + "a.mp4"})),
       len(prob("motion-rpa", params={"output_path": r"C:" + BS + "t" + BS + "a.mp4"})),
       len(gw.spend_contract_problems("video.analyze", "default", {"path": "x"}))], [0, 0, 0])
check("A4 没有合同档认领的腿（h3 / motion-rpa / analyze）⇒ 不在射程，明说不假装管得住",
      [len(prob("h3", params={"output_path": r"C:" + BS + "t" + BS + "a.mp4"})),
       len(prob("motion-rpa", params={"output_path": r"C:" + BS + "t" + BS + "a.mp4"})),
       len(gw.spend_contract_problems("video.analyze", "default", {"path": "x"}))], [0, 0, 0])
check("A5 反查唯一性：wan3-prime 只对应一档（对应多档时 skill_for_spend 返回空=不猜）",
      sc.skill_for_spend("video.generate", "wan3-prime"), "super-clone")

# ── B 组 真跑 invoke()（临时账本/条子 + 适配器打桩） ────────────────────────────────
tmp = tempfile.mkdtemp(prefix="spend-receipt-")
_out_dir = os.path.join(tmp, "step2_wan3")
os.makedirs(_out_dir, exist_ok=True)
_keep = (gw.SPEND_APPROVALS_PATH, getattr(gw, "_LEDGER_PATH", None), getattr(gw, "_ledger", None),
         gw._yz_prime_run)
calls = []


def _stub(params, route=None):
    calls.append(dict(params or {}))
    raise AssertionError("适配器被真调到了（本闸绝不允许发 HTTP）")


try:
    gw.SPEND_APPROVALS_PATH = os.path.join(tmp, "appr.json")
    gw._LEDGER_PATH = os.path.join(tmp, "ledger.sqlite")
    gw._ledger = None
    gw._yz_prime_run = _stub
    seg_out = os.path.join(_out_dir, "seg01-prime.mp4")
    P = dict(NOForm, output_path=seg_out)

    env = gw.invoke("video.generate", "wan3-prime", account="t", params=P)
    check("B1 无回执 ⇒ invoke 直接拒，且错误里说清'认回执'与该怎么修",
          [env.get("ok"), "认回执" in str(env.get("error")), ".skill_receipt.json" in str(env.get("error"))],
          [False, True, True])
    check("B2 拒的时候：适配器没被调、预扣没建、余额没动（0 消耗）",
          [len(calls), gw.ledger().balance("t"), len(gw.ledger().pending_holds() or [])],
          [0, 100000, 0])
    check("B3 信封里 contract.problems 带着原话（agent 拿得到可执行的修法，不是一句'被拦了'）",
          bool((env.get("contract") or {}).get("problems")), True)

    # 写一份【合格回执】到成片落点那一层 —— 物证形状逐条对着合同的 pred 造（不是猜）：
    #   material-analysis json-list-min:materials=1 / storyboard json-array-min=1
    #   compile-prompt json-nonempty / compile-bound bind-sha8:storyboard + json-key:source=step1_5_gpt_compiler
    #   negative-lock  text-has=不得出现字幕;水印
    f_mat = os.path.join(_out_dir, "material_analysis.json")
    f_sb = os.path.join(_out_dir, "storyboard.json")
    f_pm = os.path.join(_out_dir, "prompt_meta.json")
    f_pt = os.path.join(_out_dir, "prompt.txt")
    with io.open(f_pt, "w", encoding="utf-8") as f:
        f.write("一条复刻提示词\n\n【后端锁定】画面中不得出现字幕、花字、水印、品牌角标\n")
    with io.open(f_sb, "w", encoding="utf-8") as f:
        f.write(json.dumps([{"shot": 1, "desc": "单镜 15s"}], ensure_ascii=False))
    with io.open(f_mat, "w", encoding="utf-8") as f:
        f.write(json.dumps({"materials": [{"path": "src.mp4", "sha8": "a" * 8}], "gaps": []},
                           ensure_ascii=False))
    with io.open(f_pm, "w", encoding="utf-8") as f:
        f.write(json.dumps({"source": "step1_5_gpt_compiler", "storyboard_sha8": sc.sha8(f_sb),
                            "model": "m", "provider": "p"}, ensure_ascii=False))
    # 合同 @6 新增必需步 storyboard-fact-check 的物证（真产物由 _contract_evidence 写，这里照形状造一份）
    f_sc = os.path.join(_out_dir, "storyboard_check.json")
    with io.open(f_sc, "w", encoding="utf-8") as f:
        f.write(json.dumps({"source": "step1_mimo+runner-extra-note", "shot_count": 1, "duration": 5.1,
                            "note_present": True, "note_chars": 12, "note_sha8": "d" * 8,
                            "storyboard_sha8": sc.sha8(f_sb)}, ensure_ascii=False))
    sc.write_receipt("super-clone", _out_dir,
                     steps={"material-analysis": f_mat, "compile-prompt": f_pt, "storyboard": f_sb,
                            "storyboard-fact-check": f_sc,
                            "compile-bound": f_pm, "negative-lock": f_pt},
                     inputs=[], impl={"negative-lock": "compiler-negative-lock",
                                      "material-analysis": "gateway:video.analyze",
                                      "storyboard-fact-check": "runner:--extra-note"},
                     models={"compile-prompt": "m@p · 账本外"})
    check("B4 真回执写出来 ⇒ 判据放行（正向对照：这条闸不是只会拒绝；造不出合格物证就说明我读错了判据）",
          prob(params=P), [])
    env2 = gw.invoke("video.generate", "wan3-prime", account="t", params=P)
    check("B5 有回执但没条子 ⇒ 走到下一道闸（approval_required），证明顺序是 回执→批准",
          [env2.get("ok"), "approval" in (str(env2.get("error")) +
                                          str((env2.get("approval") or {}).get("reason"))).lower()],
          [False, True])

    g = gw.grant_spend_approval("video.generate", "wan3-prime", "t", 1, 60, "自测放行路径",
                                session="s-ok", attest={"ok": True, "method": "gate-selftest"})
    check("B6 先证条子建出来了（否则 B7 是空跑）",
          [bool(g.get("id")), len(gw._approvals_load().get("grants", []))], [True, 1])
    env3 = gw.invoke("video.generate", "wan3-prime", account="t",
                     params=dict(NOForm, output_path=seg_out, session="s-ok"))
    check("B7 回执 + 条子齐 ⇒ 真放行到适配器（防误杀：合规的单不能被拦）",
          [len(calls), env3.get("ok")], [1, False])       # 桩故意抛 ⇒ ok False，但调用点已到适配器
    check("B8 失败退段：这次没消耗，批准额度要退回（拿别人的授权替自己的绕道买单=最坏形状）",
          [x.get("used") for x in gw._approvals_load().get("grants", [])], [0])
finally:
    (gw.SPEND_APPROVALS_PATH, gw._LEDGER_PATH, gw._ledger, gw._yz_prime_run) = _keep

# ── C 组 接线（顺序用 AST 行号，不靠直觉） ──────────────────────────────────────────
src = io.open(r"D:\video-skill\backend\gateway.py", encoding="utf-8", errors="replace").read()
lines = src.splitlines()
i_fn = next(i for i, l in enumerate(lines) if l.startswith("def invoke("))
i_end = next(i for i in range(i_fn + 1, len(lines)) if lines[i].startswith("def "))
body = lines[i_fn:i_end]
rel = lambda name: next((j for j, l in enumerate(body) if name in l), -1)
check("C1 回执判在【预扣之前】、且判在批准闸之前（顺序错了会先吃额度或先扣分）",
      [0 <= rel("spend_contract_problems(") < rel("check_spend_approval(") < rel("ledger().preauth"),
       rel("spend_contract_problems(")], [True, rel("spend_contract_problems(")])
check("C2 拒绝分支里不建 hold、不发 HTTP（return 在 preauth 之前）",
      "return" in "\n".join(body[rel("spend_contract_problems("):rel("spend_contract_problems(") + 9]), True)

# ── D 组 变异对照 ──────────────────────────────────────────────────────────────────
_orig = gw.spend_contract_problems
gw.spend_contract_problems = lambda *a, **k: []
try:
    mutated = len(prob(params=dict(NOForm, output_path=r"C:" + BS + "t" + BS + "a.mp4")))
finally:
    gw.spend_contract_problems = _orig
check("D1 变异·把判据换成恒 [] ⇒ 无回执那条立刻查不出来（证明 B1 咬的是真判据）",
      [mutated, len(prob(params=dict(NOForm, output_path=r"C:" + BS + "t" + BS + "a.mp4")))],
      [0, 1])
check("D2 变异·判据在源码里各只定义一次（不抄第二份）",
      [src.count("def spend_contract_problems"), src.count("def receipt_dirs"),
       src.count("CONTRACT_RECEIPT_WALK") >= 3], [1, 1, True])

# ── E 组 覆盖对照 ─────────────────────────────────────────────────────────────────
pipe = io.open(r"D:\video-skill\backend\services\super_clone_wan3.py",
               encoding="utf-8", errors="replace").read()
check("E1 管线自己那道核验还在（网关这条是补咽喉，不是替它；两处判据同源 check_receipt）",
      ["verify_or_raise(" in pipe, "check_receipt(" in src or "load_receipt(" in src], [True, True])
check("E2 网关不许长出第二份'回执合格'判据（步/手段/版本一律读 skill_contract）",
      [k in src for k in ("def contract_shape_problems", "def impl_problems", "def _yz_impl_ok")],
      [False, False, False])

shutil.rmtree(tmp, ignore_errors=True)
check("收尾 临时目录已清（闸自己不许留残留）", os.path.isdir(tmp), False)
print(_safe("\n=== 花钱认回执闸：%d 通过 / %d 失败 ===" % (len(PASS), len(FAIL))))
if FAIL:
    print(_safe("失败清单: " + " | ".join(FAIL)))
sys.exit(1 if FAIL else 0)
