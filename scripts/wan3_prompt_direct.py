# -*- coding: utf-8 -*-
"""wan3.0 人工稿直投腿（contract-human: super-clone-wan3-human@1 的 CLI 形状）。

人自己写好提示词 + 参考图/音频 ⇒ 不经 MiMo 分镜/编译器，直接提交 wan3-prime。
它【不绕合同】：按 SKILL.md 里已登记的 contract-human 交齐三格必需物证才提交——
  material-analysis：素材经 gateway.invoke("video.analyze") 真看过（网关台账记 sha8+凭证，不是自述）
  human-prompt：_human_prompt.json（prompt_sha8 绑死现在要发出去的正文）
  negative-lock：prompt.txt 最后一行逐字 = super_clone_wan3.STEP1_5_GLOBAL_CONSTRAINT
回执由 skill_contract 当场打钩；网关在花钱那一刀按回执自己声明的合同版本核验。

用法：
  python wan3_prompt_direct.py --task-dir <productions/xxx> --prompt-file <p.md> \
      --images a.jpg b.jpg [--audios v.mp3] --duration 15 --resolution 720P --ratio 9:16 \
      [--stage evidence|submit|all] [--out-name EP01G01_15s.mp4]
"""
import argparse
import json
import os
import sys
import time

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

from backend import gateway as gw                      # noqa: E402
from backend import skill_contract as sc               # noqa: E402
from backend.services import super_clone_wan3 as scw   # noqa: E402


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def build_final_prompt(prompt_text):
    """正文 + 逐字钉回的负向锁定（最后一行 = 后端常量，与编译档同一把判据）。"""
    body = prompt_text.rstrip()
    return body + "\n" + scw.STEP1_5_GLOBAL_CONSTRAINT + "\n"


def run_evidence(task_dir, prompt_file, images, audios):
    """交齐 contract-human 的三格物证 + 回执打钩。返回 (final_prompt, 提交参数用的路径清单)。"""
    c = sc.contract_for("super-clone", variant="human")
    if not c.get("steps"):
        raise SystemExit("读不到 contract-human（super-clone SKILL.md）⇒ 直投腿没有合同依据，拒")
    sid = gw._session_of({})
    sc.set_ctx("super-clone", task_dir, sid)
    sc.open_receipt("super-clone", task_dir, inputs=list(images) + list(audios), contract=c)

    # ── 1) material-analysis：管线自己经 gateway.invoke 看图（免批不免账，台账由网关记）──
    lanes = scw._analyze_lanes()
    if not lanes:
        raise SystemExit("看图腿链读不出来（REPLICA_WAN3_ANALYZE_PURPOSE）⇒ 0 分、一条都不发")
    spent, auto = 0, []
    for one in images:
        row = {"name": os.path.basename(one), "ok": False, "charged": 0, "error": "",
               "lane": "", "model": "", "tried": []}
        for ln in lanes:
            for _t in range(int(ln["tries"])):
                env = gw.invoke("video.analyze", ln["purpose"], "local",
                                {"path": one, "question": scw.MATERIAL_ANALYZE_QUESTION,
                                 "max_tokens": scw.MATERIAL_ANALYZE_TOKENS}) or {}
                cr = int(((env.get("credits") or {}).get("charged")) or 0)
                spent += cr
                row["charged"] += cr
                txt = str(((env.get("result") or {}).get("text")) or "").strip()
                good = bool(env.get("ok")) and bool(txt)
                row["tried"].append({"purpose": ln["purpose"], "ok": good, "charged": cr,
                                     "error": str(env.get("error") or ("正文为空" if not txt else ""))[:160]})
                if good:
                    row.update({"ok": True, "lane": ln["purpose"],
                                "model": scw._analyze_lane_model(ln["purpose"])})
                    break
            if row["ok"]:
                break
        auto.append(row)
        log("看图 %s ⇒ %s（%d 分）" % (row["name"], "过" if row["ok"] else "挂：" + row["error"], row["charged"]))
    missing = gw.analysis_coverage_problems([str(x) for x in images], gw.media_analysis_rows(), sid)
    ma = os.path.join(task_dir, "material_analysis.json")
    used = sorted({str(a.get("lane") or "") for a in auto if a.get("ok")})
    cell = scw._lane_cell("video.analyze", used[0]) if len(used) == 1 else scw._lane_cell("video.analyze", lanes[0]["purpose"])
    with open(ma, "w", encoding="utf-8") as f:
        json.dump({"session": sid or "未知",
                   "materials": [gw.file_identity(p) for p in list(images) + list(audios)],
                   "missing": missing, "ledger_rows": len(gw.media_analysis_rows()),
                   "executor": "pipeline:invoke:video.analyze",
                   "auto_ran": auto, "auto_spent": spent,
                   "analyze_chain": [{"purpose": ln["purpose"], "tries": ln["tries"],
                                      "model": scw._analyze_lane_model(ln["purpose"])} for ln in lanes],
                   "model_cell": cell, "credit_cap": scw.MATERIAL_ANALYZE_CREDIT_CAP},
                  f, ensure_ascii=False, indent=1)
    if missing:
        raise SystemExit("素材没看过 ⇒ 不提交：%s" % "；".join(missing)[:400])
    r = sc.tick("super-clone", task_dir, "material-analysis", evidence=ma, model=cell,
                impl="pipeline:invoke:video.analyze", contract=c)
    log("tick material-analysis ⇒ %s" % ("✅" if r.get("ok") else "❌ " + str(r.get("error"))))

    # ── 2) prompt.txt（正文+负向锁定）──
    with open(prompt_file, encoding="utf-8") as f:
        raw_prompt = f.read()
    final_prompt = build_final_prompt(raw_prompt)
    pt = os.path.join(task_dir, "prompt.txt")
    with open(pt, "w", encoding="utf-8") as f:
        f.write(final_prompt)
    log("prompt.txt 落盘（%d 字，末行=后端负向锁定逐字）" % len(final_prompt))

    # ── 3) human-prompt 物证（指纹绑死现在这份正文）──
    hp = os.path.join(task_dir, "_human_prompt.json")
    with open(hp, "w", encoding="utf-8") as f:
        json.dump({"prompt_sha8": sc.sha8(pt), "chars": len(final_prompt),
                   "saved_at": round(time.time(), 3),
                   "manifest_ids": ["图%d" % (i + 1) for i in range(len(images))]
                                   + ["音频%d" % (i + 1) for i in range(len(audios))],
                   "origin": "chat-attachment", "session": sid or "未知"},
                  f, ensure_ascii=False, indent=1)
    r = sc.tick("super-clone", task_dir, "human-prompt", evidence=hp, model=sc.NO_MODEL,
                impl="human:ui", contract=c)
    log("tick human-prompt ⇒ %s" % ("✅" if r.get("ok") else "❌ " + str(r.get("error"))))
    r = sc.tick("super-clone", task_dir, "negative-lock", evidence=pt, model=sc.NO_MODEL,
                impl="human:ui", contract=c)
    log("tick negative-lock ⇒ %s" % ("✅" if r.get("ok") else "❌ " + str(r.get("error"))))

    rec = sc.load_receipt(task_dir)
    problems = sc.check_receipt("super-clone", rec, inputs=(), contract=c)
    if problems:
        raise SystemExit("回执不合格 ⇒ 不提交：%s" % "；".join(map(str, problems))[:400])
    log("回执核验通过（%s）" % rec.get("contract_version"))
    return final_prompt


def run_submit(task_dir, final_prompt, images, audios, duration, resolution, ratio, out_name):
    out_dir = os.path.join(task_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name)
    params = {"prompt": final_prompt, "duration": duration, "resolution": resolution,
              "ratio": ratio, "image_urls": list(images), "audio_urls": list(audios),
              "output_path": out_path, "poll_timeout": 2400}
    # 提交参数包（合同 submit-package 那格的形状）
    with open(os.path.join(task_dir, "_wan3_request.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in params.items() if k != "prompt"}, f, ensure_ascii=False, indent=1)
    log("提交 wan3-prime：%ss %s %s，图%d 音频%d …" % (duration, ratio, resolution, len(images), len(audios)))
    env = gw.invoke("video.generate", "wan3-prime", "local", params) or {}
    ok = bool(env.get("ok"))
    res = env.get("result") or {}
    log("invoke ⇒ ok=%s credits=%s" % (ok, json.dumps(env.get("credits") or {}, ensure_ascii=False)))
    if not ok:
        log("error: %s" % str(env.get("error"))[:600])
    log(json.dumps({k: res.get(k) for k in ("path", "bytes", "job_id", "model", "upstream_status",
                                            "quoted_amount", "media_notes", "video_probe")},
                   ensure_ascii=False, default=str)[:900])
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--audios", nargs="*", default=[])
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--resolution", default="720P")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--out-name", default="EP01G01_15s.mp4")
    ap.add_argument("--stage", choices=["evidence", "submit", "all"], default="all")
    a = ap.parse_args()
    task_dir = os.path.abspath(a.task_dir)
    if a.stage in ("evidence", "all"):
        final_prompt = run_evidence(task_dir, a.prompt_file, a.images, a.audios)
        if a.stage == "evidence":
            log("物证齐了（未提交、未建预扣）。下一步：--stage submit")
            return 0
    else:
        with open(os.path.join(task_dir, "prompt.txt"), encoding="utf-8") as f:
            final_prompt = f.read()
    return run_submit(task_dir, final_prompt, a.images, a.audios, a.duration,
                      a.resolution, a.ratio, a.out_name)


if __name__ == "__main__":
    sys.exit(main())
