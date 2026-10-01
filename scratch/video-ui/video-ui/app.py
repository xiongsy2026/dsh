"""产品图生视频流水线 · 本地可视化 UI

启动：python app.py  （或双击 start_ui.bat）
浏览器：http://127.0.0.1:8765

Stage 1 剧本与 Stage 4 运镜词按用户定的分工不走 API —— 在页面上粘贴；
Stage 2 关键帧走 gflow-cli，Stage 5 视频走 zexapi omni，Stage 5c/5d 走 ffmpeg。
"""

import os
import queue
import re
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import safe_join

import genimg
import json
import postproc
import prompts
import store
import zexp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

PORT = int(os.environ.get("VIDEO_UI_PORT", "8765"))

# 剧本/运镜/产品卡/翻译等文本咽喉的默认模型：一处定义，值来自 store.DEFAULT_CONFIG（现为 agnes-3.0-flash）。
DEFAULT_SCRIPT_MODEL = store.DEFAULT_CONFIG["script_model"]

# VIDEO_UI_DRYRUN=1 -> 全程不调任何烧额度的通道（gflow / zexapi 生图 / chat / omni 出片），
# 用本地占位帧和 ffmpeg testsrc 占位段把整条链路跑通。测试脚本必须在这个模式下跑。
DRY_RUN = os.environ.get("VIDEO_UI_DRYRUN") == "1"

# ---------------------------------------------------------------- job runner
_QUEUE = queue.Queue()
_JOBS = {}
_LOCK = threading.Lock()


def job(run_name):
    with _LOCK:
        return _JOBS.setdefault(run_name, {"kind": None, "lines": [], "current": "", "active": False})


def log_line(run_name, text):
    entry = job(run_name)
    entry["lines"].append([time.strftime("%H:%M:%S"), str(text)])
    del entry["lines"][:-200]


def submit(run_name, kind, fn, *args):
    entry = job(run_name)
    if entry["active"]:
        return False, "有任务在跑（%s），等它结束" % entry["kind"]
    entry.update({"kind": kind, "active": True, "current": "排队中", "lines": []})
    _QUEUE.put((run_name, kind, fn, args))
    return True, "已排入队列"


def _worker():
    while True:
        run_name, kind, fn, args = _QUEUE.get()
        try:
            fn(run_name, *args)
        except Exception:
            log_line(run_name, "未捕获异常:\n" + traceback.format_exc()[-1500:])
        finally:
            entry = job(run_name)
            entry["active"] = False
            entry["current"] = ""
            entry["kind"] = None
            try:
                store.open_run(run_name).save()
            except Exception:
                pass
        _QUEUE.task_done()


# ---------------------------------------------------------------- helpers
def run_or_404(name):
    try:
        return store.open_run(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _asset_view(run):
    """给前端的资产列表（含缩略图 URL、是否主图、角色标签、是否已有特征卡）。"""
    group_ids = [a["id"] for a in run.product_group()]
    main_id = group_ids[0] if group_ids else None
    out = []
    for aid, a in run.assets().items():
        card = a.get("card") or {}
        kind = a.get("kind") or "product"
        out.append({
            "id": aid, "kind": kind, "name": a.get("name", ""),
            "file": a.get("file"), "thumb": a.get("thumb"),
            "main": aid == main_id,
            "role": ("主图" if aid == main_id else
                     ("细节图" if kind == "product" else ("角色" if kind == "character" else "道具"))),
            "has_card": bool(card.get("summary_en") or card.get("parts")),
            "card": card,
            "uncertain": (card.get("uncertain") or [])[:6],
        })
    order = {"product": 0, "character": 1, "prop": 2}
    out.sort(key=lambda x: (order.get(x["kind"], 3), not x["main"], x["name"]))
    return out


def public_state(run):
    entry = job(run.name)
    st = run.state
    mb, mk = prompts.missing_zh(run.script) if run.script else ([], [])
    zh_missing = len(mb) + len(mk)
    return {
        "name": run.name,
        "title": st.get("title"),
        "duration": st.get("duration"),
        "segments": st.get("segments"),
        "total_frames": st.get("total_frames"),
        "stage": st.get("stage"),
        "frames": st.get("frames", {}),
        "frame_review": st.get("frame_review", {}),
        "segment_status": st.get("segment_status", {}),
        "camera": st.get("camera", {}),
        "camera_source": st.get("camera_source", {}),
        "review": st.get("review", {}),
        "final": st.get("final", {}),
        "ai_raw": st.get("ai_raw"),
        "script_source": st.get("script_source"),
        "script_warnings": st.get("script_warnings", []),
        "country": st.get("country"),
        "voiceover_language": st.get("voiceover_language"),
        "selfcheck": st.get("selfcheck", {}),
        "product_card": st.get("product_card", {}),
        "brief": st.get("brief", {}),
        "zh_missing": zh_missing,
        "voiceover": effective_voiceover(run),
        "mode": run.mode,
        "assets": _asset_view(run),
        "structures": prompts.structures_for(run.mode),
        "story_draft": st.get("story_draft_text", ""),
        "story_raw": st.get("story_raw", ""),
        "eyes_checks": [[cid, label] for cid, label in eyes_checks(run.mode)],
        "has_product": bool(run.product_group()),
        "product_count": len(run.product_group()),
        "has_anchor": bool(run.anchor_image and run.anchor_image.exists()),
        "flags": run.stage_flags(),
        "job": {"kind": entry["kind"], "active": entry["active"], "current": entry["current"],
                "lines": entry["lines"][-60:]},
    }


def effective_voiceover(run, cfg=None):
    """口播语言优先级：本 run 的目标国家推导 > 本 run 显式设置 > 全局设置。"""
    cfg = cfg or store.load_config()
    vo = dict(cfg.get("voiceover") or {})
    country = run.state.get("country") or ""
    if prompts.COUNTRY_LANG.get(country):
        vo["language"] = prompts.COUNTRY_LANG[country]
        vo["source"] = "目标国家 " + country
    elif run.state.get("voiceover_language"):
        vo["language"] = run.state["voiceover_language"]
        vo["source"] = "本 run 设置"
    else:
        vo["source"] = "全局设置"
    return vo


def beat_view(run):
    out = []
    for beat in run.beats():
        idx = beat["_id"]
        info = run.state.get("frames", {}).get(store.frame_key(idx), {})
        out.append({
            "id": idx,
            "segment": beat["_segment"],
            "local_id": beat["_local_id"],
            "beat_function": beat.get("beat_function"),
            "story_beat": beat.get("story_beat"),
            "product_presence": beat.get("product_presence"),
            "cast": beat.get("cast"),
            "shot_motivation": beat.get("shot_motivation"),
            "emotion": beat.get("emotion"),
            "dialogue": beat.get("dialogue"),
            "duration": beat.get("duration"),
            "keyframe_prompt": beat.get("keyframe_prompt"),
            "keyframe_prompt_zh": beat.get("keyframe_prompt_zh"),
            "frame_file": info.get("file"),
            "thumb_file": info.get("thumb"),
            "frame_status": info.get("status"),
            "frame_error": info.get("error"),
            "refs_note": info.get("refs_note"),
            "prompt_final": info.get("prompt"),
        })
    return out


IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|webp)$", re.I)


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent / "templates"), "index.html")


@app.route("/focus")
def focus():
    """只读单栏流水线视图（agent 驱动时用；自动聚焦最新 run，无生成按钮）。"""
    return send_from_directory(str(Path(__file__).parent / "templates"), "focus.html")


@app.route("/static/<path:asset>")
def static_assets(asset):
    return send_from_directory(str(Path(__file__).parent / "static"), asset)


def doubao_status():
    st = {"playwright": False, "logged_in": False, "age_days": None}
    try:
        import importlib.util
        st["playwright"] = importlib.util.find_spec("playwright") is not None
        if st["playwright"]:
            import doubao
            state = Path(doubao.UI_ROOT) / "doubao_state.json"
            if state.exists():
                st["logged_in"] = True
                st["age_days"] = round((time.time() - state.stat().st_mtime) / 86400.0, 1)
    except Exception as exc:
        st["error"] = str(exc)[:140]
    return st


def gflow_status(cfg):
    """gflow 登录态：跑 `auth status`（不开浏览器/不烧额度，退出码 0=已登录）+ `auth list --json` 拿账号名。只读，不改登录。"""
    exe = cfg.get("gflow_exe", "")
    st = {"exe_present": bool(exe) and Path(exe).exists(), "logged_in": False,
          "account": None, "project_ok": bool((cfg.get("gflow_project") or "").strip())}
    if not st["exe_present"]:
        return st
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                           timeout=30, encoding="utf-8", errors="replace")
        st["logged_in"] = (r.returncode == 0)
    except Exception as exc:
        st["error"] = str(exc)[:140]
        return st
    try:
        r2 = subprocess.run([exe, "auth", "list", "--json"], capture_output=True, text=True,
                            timeout=30, encoding="utf-8", errors="replace")
        profiles = json.loads((r2.stdout or "").strip() or "[]")
        cur = next((p for p in profiles if p.get("is_default")), profiles[0] if profiles else None)
        if cur:
            st["account"] = cur.get("google_account")
    except Exception:
        pass
    return st


@app.after_request
def _cors_api(resp):
    # 允许 DSH 插件（dsh-app:// 源）跨源 fetch /api/*，用于探测"本会话有没有 run"
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@app.route("/api/bootstrap")
def bootstrap():
    cfg = store.load_config()
    session = (request.args.get("session") or "").strip() or None
    return jsonify({
        "session": session,
        "runs": store.list_runs(session),
        "config": {k: v for k, v in cfg.items()},
        "caps": postproc.capabilities(),
        "runs_root": str(store.RUNS_ROOT),
        "key_present": zexp.has_key(),
        "dry_run": DRY_RUN,
        "doubao": doubao_status(),
        "gflow": gflow_status(cfg),
        "eyes_checks": [[cid, label] for cid, label in EYES_CHECKS],
        "gflow_exe_present": Path(cfg.get("gflow_exe", "")).exists(),
        "now": datetime.now().isoformat(timespec="seconds"),
    })


@app.route("/api/session_key", methods=["POST"])
def session_key():
    key = (request.get_json(silent=True) or {}).get("key", "")
    zexp.set_session_key(key)
    return jsonify({"key_present": zexp.has_key()})


@app.route("/api/settings", methods=["POST"])
def settings():
    payload = request.get_json(silent=True) or {}
    cfg = store.load_config()
    for key in ("gflow_project", "gflow_exe", "image_backend", "image_model", "video_model",
                "video_size", "fallback_size", "zexapi_image_model", "doubao_model"):
        if key in payload and payload[key] is not None:
            cfg[key] = str(payload[key]).strip()
    for key in ("watermark", "voiceover"):
        if isinstance(payload.get(key), dict):
            cfg[key].update(payload[key])
    store.save_config(cfg)
    return jsonify({"config": cfg})


@app.route("/api/runs")
def runs_list():
    session = (request.args.get("session") or "").strip() or None
    return jsonify(store.list_runs(session))


@app.route("/api/run", methods=["POST"])
def create_run():
    title = (request.form.get("title") or "").strip()
    duration = request.form.get("duration") or "10"
    try:
        duration = float(duration)
    except ValueError:
        return jsonify({"error": "时长必须是数字"}), 400
    if not title:
        return jsonify({"error": "请给这次投放起个名"}), 400
    mode = (request.form.get("mode") or "product").strip()
    if mode not in ("product", "story"):
        return jsonify({"error": "mode 只能是 product 或 story"}), 400
    session = (request.form.get("session") or "").strip() or None
    run = store.create_run(title, duration, mode, session=session)
    product = request.files.get("product")
    if product and product.filename:
        if not IMAGE_EXT_RE.search(product.filename):
            return jsonify({"error": "产品图只接受 png/jpg/webp"}), 400
        ext = (IMAGE_EXT_RE.search(product.filename).group(0).lower())
        dest = run.root / ("product%s" % ext)
        product.save(str(dest))
        genimg.make_thumb(dest, run.root / "product_thumb.jpg")
        run.state["product_info"] = genimg.product_identity(dest)
        run.state["stage"] = "intake"
        run.save()
    return jsonify({"name": run.name, "state": public_state(run)})


@app.route("/api/run/<name>/anchor", methods=["POST"])
def upload_anchor(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    up = request.files.get("anchor")
    if not up or not up.filename:
        return jsonify({"error": "没收到文件"}), 400
    ext = (IMAGE_EXT_RE.search(up.filename).group(0).lower() if IMAGE_EXT_RE.search(up.filename) else ".png")
    dest = run.root / ("anchor%s" % ext)
    up.save(str(dest))
    run.state["stage"] = run.state.get("stage") or "intake"
    run.save()
    return jsonify({"ok": True, "file": dest.name})


@app.route("/api/run/<name>/product", methods=["POST"])
def upload_product(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    up = request.files.get("product")
    if not up or not up.filename:
        return jsonify({"error": "没收到文件"}), 400
    if not IMAGE_EXT_RE.search(up.filename):
        return jsonify({"error": "产品图只接受 png/jpg/webp"}), 400
    for old in run.root.glob("product.*"):
        if old.name != "product_thumb.jpg":
            old.unlink()
    dest = run.root / ("product%s" % IMAGE_EXT_RE.search(up.filename).group(0).lower())
    up.save(str(dest))
    genimg.make_thumb(dest, run.root / "product_thumb.jpg")
    run.state["product_info"] = genimg.product_identity(dest)
    run.save()
    return jsonify({"ok": True, "file": dest.name})


@app.route("/api/run/<name>/assets", methods=["POST"])
def upload_asset(name):
    """上传额外的参考图：剧情模式=角色/道具定妆图；产品模式=产品细节图 / 多个人物角色图。
    都是「可多张」，落 run 目录 assets/ 并在第 0 步以缩略图网格呈现。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    up = request.files.get("asset")
    if not up or not up.filename:
        return jsonify({"error": "没收到文件"}), 400
    if not IMAGE_EXT_RE.search(up.filename):
        return jsonify({"error": "只接受 png/jpg/webp"}), 400
    kind = (request.form.get("kind") or "character").strip()
    allowed = ("character", "prop") if run.mode == "story" else ("product", "character")
    if kind not in allowed:
        return jsonify({"error": "%s模式只接受 %s 图" % ("剧情" if run.mode == "story" else "产品",
                                                        " / ".join(allowed))}), 400
    aname = (request.form.get("name") or "").strip()[:40]
    if not aname:
        seq = len([a for a in run.assets().values() if (a.get("kind") or "product") == kind]) + 1
        aname = {"product": "产品细节图 %d" % seq, "character": "角色 %d" % seq, "prop": "道具 %d" % seq}[kind]
    aid = run.add_asset(up, kind, aname)
    if not aid:
        return jsonify({"error": "文件保存失败"}), 400
    run.state["stage"] = run.state.get("stage") or "intake"
    run.save()
    return jsonify({"ok": True, "asset_id": aid, "state": public_state(run)})


@app.route("/api/run/<name>/assets/<aid>", methods=["POST"])
def patch_asset(name, aid):
    """改名 / 标为出场。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    fields = {}
    if "name" in payload:
        fields["name"] = str(payload["name"]).strip()[:40]
    if not fields:
        return jsonify({"error": "没有可更新的字段"}), 400
    ok = run.update_asset(aid, **fields)
    run.save()
    return jsonify({"ok": ok, "state": public_state(run)})


@app.route("/api/run/<name>/assets/<aid>", methods=["DELETE"])
def delete_asset(name, aid):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if job(name)["active"]:
        return jsonify({"error": "任务运行中，稍后再删"}), 409
    if aid == "product" and aid not in (run.state.get("assets") or {}):
        # 老的单图模型：product.png 不在 state.assets 里，直接删文件并连带基准卡
        for p in list(run.root.glob("product.*")):
            try:
                p.unlink()
            except OSError:
                pass
        run.state.pop("product_card", None)
        run.save()
        return jsonify({"ok": True, "state": public_state(run), "removed": "legacy_product"})
    ok = run.remove_asset(aid)
    run.save()
    return jsonify({"ok": ok, "state": public_state(run)})


@app.route("/api/run/<name>/asset_card", methods=["POST"])
def asset_card(name):
    """Stage 0（剧情模式）：看角色/道具图提特征卡，走工作队列。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    aid = str(payload.get("asset_id", "")).strip()
    if aid not in run.assets():
        return jsonify({"error": "资产不存在"}), 400
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），不调 chat"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺少 API Key：特征卡要调多模态模型，先在设置里填 Key"}), 400
    if job(name)["active"]:
        return jsonify({"error": "有任务在跑，稍后再试"}), 409
    ok, msg = submit(name, "asset_card", do_asset_card, aid)
    return jsonify({"ok": ok, "detail": msg})


def do_asset_card(run_name, aid):
    run = store.open_run(run_name)
    entry = job(run_name)
    cfg = store.load_config()
    assets = run.assets()
    a = assets.get(aid)
    if not a:
        log_line(run_name, "资产已不存在，跳过")
        return
    path = run.root / (a.get("file") or "")
    if not path.exists():
        log_line(run_name, "资产图缺失：%s" % path)
        return
    entry["current"] = "特征提取 %s（%s）" % (a.get("name") or aid, a.get("kind"))
    uri = zexp.data_uri(path)
    try:
        text = zexp.chat(prompts.build_asset_card_messages(uri, a.get("kind", "character"),
                                                          a.get("name", "")),
                         model=cfg.get("script_model") or DEFAULT_SCRIPT_MODEL,
                         temperature=0.2, max_tokens=3000)
        card = prompts.extract_json(text)
    except Exception as exc:
        log_line(run_name, "特征卡失败：%s" % str(exc)[:200])
        entry["current"] = "特征卡失败"
        return
    if not card:
        log_line(run_name, "特征卡解析失败（模型没返回 JSON）")
        return
    card.setdefault("name_zh", a.get("name") or "")
    run.update_asset(aid, card=card)
    run.save()
    log_line(run_name, "已生成 %s 特征卡：%d 项特征" % (
        a.get("name") or aid, len(card.get("parts") or [])))
    entry["current"] = "特征卡就绪"


@app.route("/api/run/<name>")
def get_run(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    return jsonify({"state": public_state(run), "script": run.script, "beats": beat_view(run)})


@app.route("/api/run/<name>", methods=["DELETE"])
def delete_run(name):
    """删 run = 整个目录移到 video_runs/_trash/，列表里消失但还能找回。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if job(name)["active"]:
        return jsonify({"error": "这个 run 正在跑任务，先等它结束再删"}), 409
    payload = request.get_json(silent=True) or {}
    if str(payload.get("confirm", "")).strip() != run.name:
        return jsonify({"error": "确认词不对（要填 run 名）"}), 400
    dest, mode = store.trash_run(name)
    with _LOCK:
        _JOBS.pop(name, None)
    if mode == "moved":
        return jsonify({"ok": True, "mode": mode, "moved_to": str(dest)})
    return jsonify({"ok": True, "mode": mode, "moved_to": None,
                    "note": "目录里有文件被占用，搬不动；已打 .deleted 标记（列表已隐藏），关掉占用程序后可再删一次"})


@app.route("/api/run/<name>/script", methods=["POST"])
def put_script(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    raw = (request.get_json(silent=True) or {}).get("text", "")
    if not raw.strip():
        return jsonify({"error": "粘贴框是空的：AI 写剧本若已生成会自动载入，不用再点本按钮；要手工粘贴，请在框里贴完整 script.json（从 { 到 }）。",
                        "errors": ["空输入"]}), 400
    import json
    try:
        script = prompts.extract_json(raw)
    except Exception as exc:
        return jsonify({"error": "JSON 解析失败: %s（贴了说明文字或 ```json 围栏时，只保留 { … } 再试）" % exc,
                        "errors": [str(exc)]}), 400
    if isinstance(script.get("segments"), str):
        try:
            script["segments"] = json.loads(script["segments"])
        except Exception:
            pass
    segs = len(script.get("segments", [])) or run.state.get("segments") or 1
    script = prompts.normalize_script(script, run.state.get("duration"), segs, run.mode)
    script["mode"] = run.mode
    errors, warnings = prompts.validate_script(script, mode=run.mode, run=run)
    if errors:
        return jsonify({"errors": errors, "warnings": warnings, "accepted": False}), 400
    run.set_script(script)
    run.state["review"] = {}
    run.state["script_warnings"] = warnings
    run.save()
    return jsonify({"errors": [], "warnings": warnings, "accepted": True,
                    "state": public_state(run), "beats": beat_view(run)})


def do_translate(run_name):
    """一次 chat 补齐缺的中文对照（手工粘贴的剧本用）。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    cfg = store.load_config()
    script = run.script
    if not script:
        log_line(run_name, "剧本还没载入，没有可翻译的内容")
        return
    beats, keys = prompts.missing_zh(script)
    if not beats and not keys:
        log_line(run_name, "全部拍与 subject_bible 都已有中文对照，无需补齐")
        return
    entry["current"] = "补中文对照"
    log_line(run_name, "待补：%d 条 keyframe_prompt + %d 条 subject_bible" % (len(beats), len(keys)))
    try:
        text = zexp.chat(prompts.build_translate_messages(script, beats, keys),
                         model=cfg.get("script_model") or DEFAULT_SCRIPT_MODEL, temperature=0.2, max_tokens=6000)
        data = prompts.extract_json(text)
    except Exception as exc:
        log_line(run_name, "翻译失败：%s" % str(exc)[:400])
        return
    got = prompts.apply_translations(script, data)
    (run.root / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    run.state["script_warnings"] = prompts.validate_script(script, mode=run.mode, run=run)[1]
    run.save()
    left, left_keys = prompts.missing_zh(script)
    log_line(run_name, "✓ 补上 %d 条中文对照%s" % (got, ("，仍缺 %d 条" % (len(left) + len(left_keys))) if left or left_keys else "，已齐"))


def do_product_card(run_name, notes):
    """Stage 0：看产品实拍图（主图 + 细节图，可多张）提取「产品基准卡」。1 次多模态 chat。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    cfg = store.load_config()
    group = run.product_group()
    if not group:
        log_line(run_name, "还没有产品图，先上传")
        return
    entry["current"] = "提取产品特征"
    try:
        uris = [zexp.data_uri(run.root / a["file"], max_edge=1024) for a in group]
        log_line(run_name, "一起送进模型的产品图：%d 张（%s）" % (
            len(uris), "、".join(str(a.get("name") or a["id"]) for a in group)))
        text = zexp.chat(prompts.build_product_card_messages(uris[0], notes, uris[1:]),
                         model=cfg.get("script_model") or DEFAULT_SCRIPT_MODEL, temperature=0.2, max_tokens=3000)
        card = prompts.extract_json(text)
    except Exception as exc:
        log_line(run_name, "提取失败：%s" % str(exc)[:400])
        return
    if not prompts.card_line(card):
        log_line(run_name, "模型返回的卡片是空的，未采纳；可重试或自己在 Qoder 里看图写")
        return
    run.state["product_card"] = card
    (run.root / "product_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    run.save()
    log_line(run_name, "✓ 产品基准卡已生成（%d 个部件）：%s" % (
        len(card.get("parts") or []), str(card.get("summary_en") or "")[:150]))
    for u in (card.get("uncertain") or [])[:5]:
        log_line(run_name, "  ? 图上看不清，需人工确认：%s" % str(u)[:120])
    log_line(run_name, "  之后每一帧的产品清单都会用这张卡，剧本里模型自编的产品描述已被它覆盖。")


@app.route("/api/run/<name>/product_card", methods=["POST"])
def product_card(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），不调 chat"}), 400
    if not run.product_group():
        return jsonify({"error": "先上传产品图（主图或细节图任一即可）"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（提取产品特征要调 1 次多模态 chat）"}), 400
    payload = request.get_json(silent=True) or {}
    ok, msg = submit(name, "product_card", do_product_card, str(payload.get("notes", "")).strip()[:800])
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run/<name>/translate_script", methods=["POST"])
def translate_script(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），不调 chat"}), 400
    if not run.script:
        return jsonify({"error": "先载入剧本"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（补中文对照要调 1 次 chat）"}), 400
    beats, keys = prompts.missing_zh(run.script)
    if not beats and not keys:
        return jsonify({"error": "已经全有中文对照，不用补"}), 400
    ok, msg = submit(name, "translate", do_translate)
    return jsonify({"ok": ok, "msg": msg, "todo": len(beats) + len(keys)})


@app.route("/api/run/<name>/gate", methods=["POST"])
def gate(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    which = payload.get("gate")
    approve = bool(payload.get("approve"))
    rev = run.state.setdefault("review", {})
    if which == "script":
        rev["script_approved"] = approve
        run.state["stage"] = "frames" if approve else "script_review"
    elif which == "frames":
        pend = [k for k, v in run.state.get("frame_review", {}).items() if v.get("verdict") != "approved"]
        if approve and pend:
            return jsonify({"error": "还有 %d 帧未标记通过：%s" % (len(pend), ",".join(sorted(pend))),
                            "pending": pend}), 400
        rev["frames_approved"] = approve
        run.state["stage"] = "camera" if approve else "frames_review"
    else:
        return jsonify({"error": "未知 gate"}), 400
    run.save()
    return jsonify({"state": public_state(run)})


@app.route("/api/run/<name>/frame_review", methods=["POST"])
def frame_review(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    idx = store.frame_key(int(payload.get("idx", 0)))
    verdict = payload.get("verdict")
    if verdict not in ("approved", "rejected", "pending"):
        return jsonify({"error": "verdict 非法"}), 400
    entry = run.state.setdefault("frame_review", {}).setdefault(idx, {})
    entry["verdict"] = verdict
    entry["note"] = str(payload.get("note", ""))[:500]
    entry["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    run.state["review"]["frames_approved"] = False
    run.state["stage"] = "frames_review"
    run.save()
    log = run.root / "review.json"
    import json
    log.write_text(json.dumps(run.state.get("frame_review", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"state": public_state(run)})


# ---------------------------------------------------------------- Stage 2
def do_frames(run_name, targets, backend, ref_mode):
    run = store.open_run(run_name)
    cfg = store.load_config()
    script = run.script
    if not script:
        log_line(run_name, "没有剧本，无法生成关键帧")
        return
    beats = {b["_id"]: b for b in run.beats()}
    total = run.state.get("total_frames") or len(beats)
    for idx in targets:
        if idx > total:
            continue
        beat = beats.get(idx)
        if not beat:
            log_line(run_name, "拍 %d 不在剧本里" % idx)
            continue
        key = store.frame_key(idx)
        entry = job(run_name)
        entry["current"] = "关键帧 %d/%d" % (idx, total)
        run.state.setdefault("frames", {})[key] = {"status": "running", "backend": backend}
        run.state["stage"] = "frames"
        run.save()
        refs, note = prompts.reference_plan(run, idx, beat, ref_mode)
        roles = prompts.ref_roles(run, idx, beat, refs)
        prompt_final = prompts.assemble_frame_prompt(
            beat, script, ref_clause=prompts.reference_clause(refs, roles),
            card=run.state.get("product_card"), run=run)
        staging = run.root / "staging"
        started = time.time()
        cast_label = "、".join(beat.get("cast") or []) if run.mode == "story" else str(beat.get("product_presence"))
        try:
            log_line(run_name, "拍 %d（%s / %s）参考图：%s" % (idx, beat.get("beat_function"),
                                                            cast_label, note))
            if DRY_RUN:
                rel, thumb_rel = genimg.dry_frame(run, idx, "%s / %s" % (beat.get("beat_function"),
                                                                        cast_label))
                produced, url = None, None
                log_line(run_name, "  [dry-run] 本地占位帧，未调生图通道")
            elif backend == "gflow":
                produced = genimg.gflow_generate(cfg, prompt_final, refs, staging,
                                                 log=lambda m: log_line(run_name, "  " + m))
                url = None
                rel, thumb_rel = genimg.normalize_frame(produced, run, idx)
            elif backend == "doubao":
                dest = staging / ("frame_%s.png" % key)
                produced = genimg.doubao_generate(prompt_final, refs, dest,
                                                 log=lambda m: log_line(run_name, "  " + m),
                                                 model=cfg.get("doubao_model", "Seedream 4.5"))
                url = None
                rel, thumb_rel = genimg.normalize_frame(produced, run, idx)
            else:
                dest = staging / ("frame_%s.png" % key)
                produced, url = genimg.zex_generate(cfg, prompt_final, refs, dest,
                                                   on_update=lambda m: log_line(run_name, "  " + m))
                rel, thumb_rel = genimg.normalize_frame(produced, run, idx)
            run.state["frames"][key] = {
                "status": "done", "backend": "dry-run" if DRY_RUN else backend, "file": rel, "thumb": thumb_rel,
                "refs": [Path(r).name for r in refs], "refs_note": note,
                "prompt": prompt_final, "oss_url": url,
                "seconds": round(time.time() - started, 1),
                "presence": beat.get("product_presence"),
                "beat_function": beat.get("beat_function"),
            }
            run.state["frame_review"][key] = {"verdict": "pending", "note": ""}
            run.log_task("frames", key, {"task": "dry-run" if DRY_RUN else ("gflow" if backend == "gflow" else url),
                                         "seconds": round(time.time() - started, 1)})
            log_line(run_name, "拍 %d 完成 -> %s" % (idx, rel))
            run.state["review"]["frames_approved"] = False
            run.save()
        except Exception as exc:
            run.state["frames"][key] = {"status": "failed", "backend": backend,
                                        "error": str(exc)[:800], "prompt": prompt_final,
                                        "refs_note": note}
            run.state["stage"] = "frames"
            run.save()
            log_line(run_name, "拍 %d 失败：%s" % (idx, str(exc)[:500]))
            log_line(run_name, "后续帧依赖这一帧，已停止。修好后单独重跑这一帧。")
            return
    run.state["stage"] = "frames_review"
    run.save()
    log_line(run_name, "本批关键帧结束，去审核面板逐帧确认。")


def _strip_fence(text):
    body = (text or "").strip()
    m = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", body, re.S)
    return m.group(1).strip() if m else body


def do_ai_camera(run_name, targets, model):
    """Stage 4：每段 1 次多模态 chat（该段 6 张关键帧 + 剧本 beats）。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    total = run.state.get("segments") or 0
    for k in targets:
        uris, missing = [], []
        for local_id in range(1, 7):
            idx = (k - 1) * 6 + local_id
            info = run.state.get("frames", {}).get(store.frame_key(idx), {})
            thumb = run.root / (info.get("thumb") or "frames/thumb/frame_%02d.jpg" % idx)
            if not thumb.exists():
                missing.append(idx)
            else:
                uris.append(zexp.data_uri(thumb))
        if missing:
            log_line(run_name, "第 %d 段缺关键帧 %s，跳过（先把帧生成出来）" % (k, missing))
            continue
        entry["current"] = "运镜词 · 第 %d/%d 段" % (k, total)
        msgs = prompts.build_camera_messages(run, k, uris)
        log_line(run_name, "第 %d 段：chat %s 带 6 张关键帧生成运镜词" % (k, model))
        text, problems = "", []
        for attempt in range(1, 3):
            try:
                text = _strip_fence(zexp.chat(msgs, model=model, temperature=0.9, max_tokens=2500))
            except Exception as exc:
                log_line(run_name, "第 %d 段 chat 失败：%s" % (k, str(exc)[:400]))
                text = ""
                break
            problems = prompts.camera_sanity(text)
            if not problems:
                break
            log_line(run_name, "第 %d 段产物有问题：%s" % (k, " | ".join(problems)))
            if attempt < 2:
                msgs += [{"role": "assistant", "content": text[:4000]},
                         {"role": "user", "content": "以上输出不符合要求：" + "；".join(problems) +
                          "。请重写，只输出英文运镜提示词本身（150-300 words），不要任何解释或代码块。"}]
        if text:
            run.state.setdefault("camera", {})[str(k)] = text
            run.state.setdefault("camera_source", {})[str(k)] = "ai:%s" % model
            run.state["stage"] = "video"
            run.save()
            log_line(run_name, "第 %d 段运镜词已写入（%d 个英文词）%s" % (
                k, prompts.word_count(text), "，仍有提醒：%s" % "；".join(problems) if problems else ""))
        else:
            log_line(run_name, "第 %d 段没拿到运镜词，可以自己在框里手写后点保存" % k)


@app.route("/api/run/<name>/ai_camera", methods=["POST"])
def ai_camera(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if not run.state.get("review", {}).get("frames_approved"):
        return jsonify({"error": "关键帧审核门（Stage 3）未过，不能写运镜词"}), 400
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），AI 生成已禁用以免烧额度"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（AI 写运镜每段要调 1 次多模态 chat）"}), 400
    payload = request.get_json(silent=True) or {}
    total = run.state.get("segments") or 0
    targets = payload.get("segments")
    if targets in (None, "all"):
        targets = [k for k in range(1, total + 1)]
    else:
        targets = sorted(int(t) for t in targets)
    cfg = store.load_config()
    ok, msg = submit(name, "ai_camera", do_ai_camera, targets, cfg.get("script_model") or DEFAULT_SCRIPT_MODEL)
    return jsonify({"ok": ok, "msg": msg, "targets": targets})


@app.route("/api/run/<name>/frames", methods=["POST"])
def start_frames(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if not run.script:
        return jsonify({"error": "先生成/粘贴剧本"}), 400
    if not run.state.get("review", {}).get("script_approved"):
        return jsonify({"error": "剧本还没通过审核（Stage 1.5 强制检查点）"}), 400
    payload = request.get_json(silent=True) or {}
    backend = payload.get("backend") or store.load_config().get("image_backend", "gflow")
    ref_mode = payload.get("ref_mode") or "skill"
    targets = payload.get("targets")
    total = run.state.get("total_frames") or 0
    if targets == "all" or targets is None:
        targets = [i for i in range(1, total + 1)
                   if run.state.get("frames", {}).get(store.frame_key(i), {}).get("status") != "done"]
    else:
        targets = sorted(int(t) for t in targets)
    if not targets:
        return jsonify({"error": "没有待生成的帧"}), 400
    ok, msg = submit(name, "frames", do_frames, targets, backend, ref_mode)
    return jsonify({"ok": ok, "msg": msg, "targets": targets})


# ---------------------------------------------------------------- Stage 4/5
@app.route("/api/run/<name>/camera", methods=["POST"])
def put_camera(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    seg = int(payload.get("segment", 0))
    text = str(payload.get("text", "")).strip()
    if seg < 1 or seg > (run.state.get("segments") or 0):
        return jsonify({"error": "段号非法"}), 400
    if not run.state.get("review", {}).get("frames_approved"):
        return jsonify({"error": "关键帧还没通过审核（Stage 3 强制检查点）"}), 400
    run.state.setdefault("camera", {})[str(seg)] = text
    run.state.setdefault("camera_source", {})[str(seg)] = "manual"
    words = prompts.word_count(text)
    if text and words < 60:
        run.state.setdefault("camera_warning", {})[str(seg)] = "运镜词只有 %d 个英文词，建议 150-300" % words
    run.state["stage"] = "video"
    run.save()
    preview = prompts.assemble_video_prompt(run, seg, text, effective_voiceover(run))
    return jsonify({"ok": True, "words": words, "assembled": preview})


def do_segments(run_name, targets, model, size):
    run = store.open_run(run_name)
    cfg = store.load_config()
    entry = job(run_name)
    for k in targets:
        camera = (run.state.get("camera", {}) or {}).get(str(k), "").strip()
        if not camera:
            log_line(run_name, "第 %d 段缺运镜提示词，跳过" % k)
            continue
        seg = run.segments()[k - 1]
        presences = [str(b.get("product_presence")) for b in seg.get("beats", [])]
        has_product = any(p in ("natural", "focus") for p in presences)
        prompt_final = prompts.assemble_video_prompt(run, k, camera, effective_voiceover(run, cfg))
        images, sources = [], []
        for local_id in range(1, 7):
            idx = (k - 1) * 6 + local_id
            info = run.state.get("frames", {}).get(store.frame_key(idx), {})
            url = info.get("oss_url")
            if url:
                log = run.task_log().get("frames", {}).get(store.frame_key(idx), {})
                fresh = (time.time() - log.get("at", 0)) < 4.5 * 3600 if log.get("at") else False
                if fresh:
                    images.append(url)
                    sources.append("oss:%d" % idx)
                    continue
            thumb = run.root / (info.get("thumb") or "frames/thumb/frame_%02d.jpg" % idx)
            if not thumb.exists():
                log_line(run_name, "第 %d 段缺少帧 %d 缩样，中止该段" % (k, idx))
                images = None
                break
            images.append(zexp.data_uri(thumb))
            sources.append("thumb:%d" % idx)
        if images is None:
            continue
        if has_product and run.product_image:
            images.append(zexp.data_uri(run.product_image, max_edge=768))
            sources.append("product")
        images = images[:7]
        if DRY_RUN:
            dest = run.root / "segments" / ("segment_%d.mp4" % k)
            postproc.dry_segment(dest, 10, size)
            run.state["segment_status"][str(k)] = {
                "status": "done", "task_id": "dry-run", "model": "dry-run", "size": size,
                "file": "segments/%s" % dest.name, "probe": postproc.probe(dest),
                "prompt": prompt_final, "images": sources}
            run.save()
            log_line(run_name, "第 %d 段 [dry-run] 本地占位片，未调 omni（%d 张参考图：%s）"
                     % (k, len(images), ", ".join(sources)))
            continue
        if size == cfg.get("video_size"):
            degrade = [(model, size), (cfg.get("fallback_model"), cfg.get("fallback_size"))]
        else:
            degrade = [(model, size)]
        attempt = 0
        for attempt, (m, sz) in enumerate(degrade, start=1):
            run.state.setdefault("segment_status", {})[str(k)] = {
                "status": "submitting", "model": m, "size": sz, "prompt": prompt_final,
                "images": sources, "attempt": attempt}
            run.save()
            try:
                entry["current"] = "视频段 %d/%d 提交" % (k, run.state.get("segments"))
                task_id, _ = zexp.submit_video(prompt_final, images, model=m, size=sz)
                log_line(run_name, "第 %d 段 task_id=%s (%s %s)" % (k, task_id, m, sz))
                run.state["segment_status"][str(k)].update({"task_id": task_id, "status": "running"})
                run.save()
                last = {"t": 0}

                def on_update(status, progress, k=k):
                    now = time.time()
                    if now - last["t"] > 20:
                        last["t"] = now
                        log_line(run_name, "第 %d 段 %s %s%%" % (k, status, progress))
                        run.state["segment_status"][str(k)].update({"status": status, "progress": progress})
                        run.save()
                url, data = zexp.poll(task_id, interval=10, timeout=900, on_update=on_update)
                dest = run.root / "segments" / ("segment_%d.mp4" % k)
                zexp.download(url, dest)
                info = postproc.probe(dest)
                run.state["segment_status"][str(k)] = {
                    "status": "done", "task_id": task_id, "model": m, "size": sz,
                    "file": "segments/%s" % dest.name, "asset_url": url,
                    "probe": info, "attempt": attempt,
                    "prompt": prompt_final, "images": sources}
                run.log_task("segments", k, {"task_id": task_id, "model": m, "size": sz,
                                             "probe": info})
                run.save()
                log_line(run_name, "第 %d 段完成 %sx%s %.1fs -> %s" % (
                    k, info.get("width"), info.get("height"), info.get("duration") or 0, dest.name))
                break
            except Exception as exc:
                msg = str(exc)[:600]
                log_line(run_name, "第 %d 段失败：%s" % (k, msg))
                run.state["segment_status"][str(k)] = {"status": "failed", "error": msg,
                                                      "model": m, "size": sz, "attempt": attempt,
                                                      "prompt": prompt_final}
                run.save()
                if attempt >= len(degrade):
                    log_line(run_name, "第 %d 段降级链用尽。若是内容审核拦截，软化运镜词措辞后单跑这段。" % k)


@app.route("/api/run/<name>/segments", methods=["POST"])
def start_segments(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if not run.state.get("review", {}).get("frames_approved"):
        return jsonify({"error": "关键帧审核门未过"}), 400
    payload = request.get_json(silent=True) or {}
    cfg = store.load_config()
    total = run.state.get("segments") or 0
    targets = payload.get("targets")
    if targets in (None, "all"):
        targets = [k for k in range(1, total + 1)]
    else:
        targets = sorted(int(t) for t in targets)
    missing = [k for k in targets if not (run.state.get("camera", {}) or {}).get(str(k), "").strip()]
    if missing:
        return jsonify({"error": "这些段还没有运镜词：%s" % ",".join(map(str, missing))}), 400
    if not DRY_RUN and not zexp.has_key():
        return jsonify({"error": "缺 ZEXAPI_API_KEY：设在环境变量，或在页面顶部临时填入"}), 400
    ok, msg = submit(name, "video", do_segments, targets,
                     payload.get("model") or cfg.get("video_model"),
                     payload.get("size") or cfg.get("video_size"))
    return jsonify({"ok": ok, "msg": msg, "targets": targets})


def do_finalize(run_name):
    run = store.open_run(run_name)
    cfg = store.load_config()
    entry = job(run_name)
    total = run.state.get("segments") or 0
    files = []
    for k in range(1, total + 1):
        info = run.state.get("segment_status", {}).get(str(k), {})
        if info.get("status") != "done" or not info.get("file"):
            log_line(run_name, "第 %d 段还没出片，无法合成" % k)
            return
        files.append(run.root / info["file"])
    entry["current"] = "拼接"
    if total == 1:
        final = run.root / "video" / "final.mp4"
        postproc.copy(files[0], final)
        how = "单段直拷"
    else:
        final = run.root / "video" / "final.mp4"
        probe0 = postproc.probe(files[0])
        size = "%dx%d" % (probe0.get("width") or 1080, probe0.get("height") or 1920)
        _, how = postproc.concat(files, final, size=size, fps=probe0.get("fps") or 30)
    log_line(run_name, "拼接完成（%s）" % how)
    entry["current"] = "去水印"
    clean = run.root / "video" / "final_clean.mp4"
    try:
        _, how2 = postproc.delogo(final, clean, cfg.get("watermark", {}))
        log_line(run_name, "水印处理完成（%s）" % how2)
    except Exception as exc:
        log_line(run_name, "去水印失败：%s（成片仍可用，右下角水印未除）" % str(exc)[:400])
        clean = final
    info = postproc.probe(clean)
    run.state["final"] = {
        "concat": "video/final.mp4", "clean": ("video/%s" % clean.name),
        "strategy": how + " + " + ("delogo" if clean != final else "无去水印"),
        "probe": info,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    run.state["stage"] = "done"
    run.save()
    log_line(run_name, "交付：%s（%sx%s, %.1fs）" % (clean.name, info.get("width"),
                                                  info.get("height"), info.get("duration") or 0))


@app.route("/api/run/<name>/finalize", methods=["POST"])
def finalize(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    ok, msg = submit(name, "finalize", do_finalize)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run/<name>/story_outline", methods=["POST"])
def story_outline(name):
    """剧情模式第一步：1 次 chat 先出故事大纲，不直接写分镜剧本。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if run.mode != "story":
        return jsonify({"error": "故事大纲只用于剧情模式"}), 400
    payload = request.get_json(silent=True) or {}
    idea = str(payload.get("idea", "")).strip()
    structure = str(payload.get("structure", "")).strip()
    if structure and structure not in prompts.STORY_STRUCTURES:
        return jsonify({"error": "剧情模式的叙事结构只能选：%s" % "、".join(prompts.STORY_STRUCTURES)}), 400
    if not idea:
        return jsonify({"error": "先写一句故事前提 / 灵感（谁、什么处境、想要什么、遇到什么）"}), 400
    if not run.assets():
        return jsonify({"error": "剧情模式要先在第 0 步上传至少一个角色/道具图（名册为空故事无处安人）"}), 400
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），AI 生成已禁用以免烧额度"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（写故事大纲要调 1 次 chat）"}), 400
    if job(name)["active"]:
        return jsonify({"error": "有任务在跑，稍后再试"}), 409
    cfg = store.load_config()
    ok, msg = submit(name, "story_outline", do_story_outline,
                     idea, str(payload.get("platform", "通用")),
                     structure,
                     str(payload.get("country", "")).strip(),
                     str(payload.get("extra", "")).strip(),
                     cfg.get("script_model") or DEFAULT_SCRIPT_MODEL)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run/<name>/story_draft", methods=["POST"])
def story_draft(name):
    """剧情模式：把自己写/改过的大纲文本存下来（不花额度，纯保存）。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if run.mode != "story":
        return jsonify({"error": "故事大纲只用于剧情模式"}), 400
    txt = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    run.state["story_draft_text"] = txt
    if txt:
        run.state["stage"] = run.state.get("stage") or "intake"
    run.save()
    (run.root / "story_draft.txt").write_text(txt, encoding="utf-8")
    return jsonify({"ok": True, "chars": len(txt), "state": public_state(run)})


def do_story_outline(run_name, idea, platform, structure, country, extra, model):
    run = store.open_run(run_name)
    entry = job(run_name)
    st = run.state
    duration, segments = st.get("duration") or 10, st.get("segments") or 1
    assets = list(run.assets().values())
    msgs = prompts.build_story_outline_messages(duration, segments, platform, country,
                                                structure, assets, idea, extra)
    named = [a.get("name") or a["id"] for a in assets]
    log_line(run_name, "名册可用的人物/道具：%s" % "、".join(named))
    if country:
        log_line(run_name, "目标国家：%s → 台词/口播语言 %s" % (country, prompts.COUNTRY_LANG.get(country) or "默认英文"))
    text, outline = "", None
    for attempt in range(1, 3):
        entry["current"] = "故事大纲 · 第 %d 次生成" % attempt
        log_line(run_name, "chat %s 写故事大纲（第 %d/2 次，只出大纲不出生分镜）" % (model, attempt))
        try:
            text = zexp.chat(msgs, model=model)
        except Exception as exc:
            log_line(run_name, "chat 失败：%s" % str(exc)[:400])
            return
        try:
            outline = prompts.extract_json(text)
        except Exception as exc:
            log_line(run_name, "✗ 大纲解析失败：%s" % str(exc)[:200])
            outline = None
        if isinstance(outline, dict) and (outline.get("plot") or outline.get("logline")):
            break
        log_line(run_name, "△ 大纲不像样（缺 plot/logline），重写一次")
        outline = None
        if attempt < 2:
            msgs += [{"role": "assistant", "content": text[:9000]},
                     {"role": "user", "content": "上面输出的问题：缺 plot 五段或与名册不符。只修这些，重新完整输出整个 JSON（不要解释）。"}]
    if not outline:
        st["story_raw"] = text[:20000]
        run.save()
        log_line(run_name, "两次都没产出可用大纲。原始输出已放进编辑框，你自己改一版也行——改完点「按大纲写剧本」会直接用编辑框里的文本。")
        return
    off_roster = [c.get("name") for c in (outline.get("characters") or [])
                  if isinstance(c, dict) and c.get("name") and c["name"] not in named]
    off_roster += [pr.get("name") for pr in (outline.get("props") or [])
                   if isinstance(pr, dict) and pr.get("name") and pr["name"] not in named]
    if off_roster:
        log_line(run_name, "△ 大纲里这些人物/道具不在名册（%s）——要么在第 0 步补图，要么改成名册里的名字，否则下一步剧本校验会拦"
                 % "、".join(str(x) for x in off_roster[:6]))
    if (outline.get("beat_plan") or []) and len(outline["beat_plan"]) != segments * 6:
        log_line(run_name, "△ beat_plan %d 行，和 %d 拍不一致——下一步按段数为准"
                 % (len(outline["beat_plan"]), segments * 6))
    txt = prompts.outline_to_text(outline)
    st["story_outline"] = outline
    st["story_draft_text"] = txt
    st["country"] = country or st.get("country") or "通用"
    st["stage"] = st.get("stage") or "intake"
    run.save()
    (run.root / "story_outline.json").write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.root / "story_draft.txt").write_text(txt, encoding="utf-8")
    log_line(run_name, "✓ 故事大纲已出：%s ｜ %s" % (outline.get("title") or "（无标题）", outline.get("logline") or "")[:180])
    log_line(run_name, "  下一步：核对/改上面这段大纲，再点「② 按大纲写剧本」（会再生成一次，共 2 次 chat）")
    if outline.get("risks"):
        log_line(run_name, "  模型自己提示的风险：%s" % str(outline["risks"])[:200])


def do_ai_script(run_name, product_name, selling_points, platform, structure, extra, model, country="", story_draft=""):
    """Stage 1 走 chat：1 次生成 + 最多 2 次按校验错误重生成（SKILL 规定）。产品/剧情模式分支。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    st = run.state
    duration, segments = st.get("duration") or 10, st.get("segments") or 1
    story = run.mode == "story"
    assets = list(run.assets().values())
    if story:
        outline_txt = str(story_draft or "").strip() or str(st.get("story_draft_text") or "").strip()
        msgs = prompts.build_story_script_messages(duration, segments, platform, country,
                                                   structure, assets, extra, story=outline_txt)
        if outline_txt:
            log_line(run_name, "已带上第 ① 步确认的故事大纲（%d 字），剧本将逐拍展开它，不换故事" % len(outline_txt))
        else:
            log_line(run_name, "△ 没有故事大纲，直接凭「故事前提」写分镜——容易主线漂；建议先点「① 先写故事大纲」")
        named = [a.get("name") or a["id"] for a in assets]
        if named:
            log_line(run_name, "资产名册（剧本只能在这些角色/道具里挑）：%s" % "、".join(named))
        else:
            log_line(run_name, "△ 名册是空的：请先在第 0 步上传角色/道具图，否则剧本无处引用")
        with_card = [a for a in assets if (a.get("card") or {}).get("summary_en")]
        if len(with_card) < len(assets):
            log_line(run_name, "△ %d/%d 个资产还没提取特征卡，剧本里它们的外观只能靠名字脑补"
                     % (len(with_card), len(assets)))
    else:
        msgs = prompts.build_script_messages(product_name, selling_points, duration, segments,
                                             platform, structure, extra, country,
                                             card=run.state.get("product_card"))
        if run.state.get("product_card"):
            log_line(run_name, "已带上 Stage 0 的产品基准卡（模型不得自编产品描述）")
        else:
            log_line(run_name, "△ 没有产品基准卡：模型只能照文字卖点脑补产品外观，建议先回第 0 步「提取产品特征」")
    if country:
        log_line(run_name, "目标国家：%s → 台词/口播语言 %s" % (country, prompts.COUNTRY_LANG.get(country) or "默认英文"))
    errors, warnings, text = [], [], ""
    for attempt in range(1, 4):
        entry["current"] = "AI 剧本 · 第 %d 次生成" % attempt
        log_line(run_name, "chat %s 生成剧本（第 %d/3 次）" % (model, attempt))
        try:
            text = zexp.chat(msgs, model=model)
        except Exception as exc:
            log_line(run_name, "chat 失败：%s" % str(exc)[:400])
            return
        try:
            script = prompts.normalize_script(prompts.extract_json(text), duration, segments, run.mode)
        except Exception as exc:
            errors = ["解析失败: %s" % exc]
            log_line(run_name, "✗ %s" % errors[0])
        else:
            script["mode"] = run.mode
            (run.root / "script_ai.json").write_text(
                json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
            errors, warnings = prompts.validate_script(script, mode=run.mode, run=run)
            if not errors:
                run.set_script(script)
                run.state["review"] = {}
                run.state["script_warnings"] = warnings
                run.state["script_source"] = "ai:%s" % model
                run.state["country"] = country or "通用"
                run.state.pop("ai_raw", None)
                run.save()
                log_line(run_name, "✓ 剧本已生成并载入（结构：%s）。逐拍核对后点 Stage 1.5 通过。"
                         % script.get("narrative_structure", "?"))
                if warnings:
                    for w in warnings[:6]:
                        log_line(run_name, "  △ " + w)
                if not story:
                    card = run.state.get("product_card") or {}
                    summary = str(card.get("summary_en") or "").strip()
                    prod_txt = str(((script.get("subject_bible") or {}).get("product")) or "")
                    if summary and summary[:60] not in prod_txt:
                        log_line(run_name, "△ 剧本的 subject_bible.product 没照抄基准卡（模型自己改写了措辞）。"
                                           "生帧仍以基准卡为准，但建议核对剧本表格里的产品清单。")
                return
            log_line(run_name, "✗ 校验未过 %d 条：%s" % (len(errors), " | ".join(errors[:4])[:300]))
        if attempt < 3:
            msgs += [{"role": "assistant", "content": text[:9000]},
                     {"role": "user", "content": "你输出的 JSON 有以下问题，只修这些问题、保持其余内容不变，重新完整输出整个 JSON（不要任何解释）：\n- "
                      + "\n- ".join(errors[:14])}]
    run.state["ai_raw"] = text[:20000]
    run.state["script_source"] = "ai_failed"
    run.save()
    log_line(run_name, "3 次都没过校验。已把模型最后一次输出塞进上面的编辑框，改完手动点「校验并载入剧本」。")


@app.route("/api/run/<name>/ai_script", methods=["POST"])
def ai_script(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    payload = request.get_json(silent=True) or {}
    selling = str(payload.get("selling_points", "")).strip()
    structure = str(payload.get("structure", "")).strip()
    draft = str(payload.get("story_draft", "")).strip() or str(run.state.get("story_draft_text") or "").strip()
    if structure and structure not in prompts.structures_for(run.mode):
        return jsonify({"error": "「%s」不是本模式可选的叙事结构（%s：只能选 %s）" % (
            structure, "剧情模式" if run.mode == "story" else "产品模式",
            "、".join(prompts.structures_for(run.mode)))}), 400
    if run.mode == "story":
        if not run.assets():
            return jsonify({"error": "剧情模式要先在第 0 步上传至少一个角色/道具图（名册为空剧本无处引用）"}), 400
        if not draft:
            return jsonify({"error": "剧情模式要先写故事：点「① 先写故事大纲」生成，或自己把大纲粘进大纲框，再点「② 按大纲写剧本」"}), 400
    elif not selling:
        return jsonify({"error": "至少写一句产品卖点/故事方向"}), 400
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），AI 生成已禁用以免烧额度"}), 400
    if run.mode == "story" and not selling:
        selling = draft[:400]
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（AI 写剧本要调 1 次 chat）"}), 400
    cfg = store.load_config()
    ok, msg = submit(name, "ai_script", do_ai_script,
                     str(payload.get("product_name", "")).strip() or run.state.get("title", ""),
                     selling, str(payload.get("platform", "通用")),
                     structure,
                     str(payload.get("extra", "")).strip(),
                     cfg.get("script_model") or DEFAULT_SCRIPT_MODEL,
                     str(payload.get("country", "")).strip(),
                     draft if run.mode == "story" else "")
    return jsonify({"ok": ok, "msg": msg})


EYES_CHECKS = [
    ("frame_order", "帧序正确，运镜与剧本吻合"),
    ("product_identity", "产品外观/人物身份/场景跨帧一致（对照第 1 帧）"),
    ("product_complete", "natural/focus 帧产品完整无裁切，关键部件全可见"),
    ("product_scale", "产品在画面中尺寸符合真实佩戴/握持比例"),
    ("no_leak", "not_present 帧完全看不到产品（脖子/手腕/桌面/背景）"),
    ("no_artifact", "无多余文字、乱码、畸形手指、多余物品"),
    ("bridge", "段间衔接处画面连续不跳戏（硬切黑帧已由自动检查兜住）"),
]

STORY_EYES_CHECKS = [
    ("frame_order", "帧序正确，运镜与剧本吻合"),
    ("identity", "每个角色/道具的身份与场景跨帧一致（对照其定妆图与第 1 帧）"),
    ("cast_match", "每拍只有 cast 里的角色/道具入镜，没多出名册外的东西"),
    ("faces", "多角色同框时脸型/发色/服装没串（A 的脸长到 B 身上）"),
    ("no_artifact", "无多余文字、乱码、畸形手指、多余物品"),
    ("bridge", "段间衔接处画面连续不跳戏（硬切黑帧已由自动检查兜住）"),
]


def eyes_checks(mode):
    return STORY_EYES_CHECKS if mode == "story" else EYES_CHECKS


def do_selfcheck(run_name):
    """Stage 6 一键自查：能本地算的全算，算不了的明确标"要人眼"。零成本，不调任何 API。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    st = run.state
    segs = st.get("segments") or 0
    total_frames = st.get("total_frames") or 0
    results = []

    def add(cid, label, verdict, detail=""):
        results.append({"id": cid, "label": label, "verdict": verdict, "detail": str(detail)[:300]})
        log_line(run_name, "[%s] %s %s" % ({"pass": "✓", "warn": "△", "fail": "✗", "eyes": "👁"}[verdict],
                                           label, ("— " + str(detail)[:160]) if detail else ""))

    final_rel = st.get("final", {}).get("clean")
    final = run.root / final_rel if final_rel else None
    entry["current"] = "自查 · 成片规格"
    if not final or not final.is_file():
        add("final", "成片已生成", "fail", "还没拼接，先跑 Stage 6 的「拼接 + 去水印」")
        run.state["selfcheck"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": results}
        run.save()
        return
    info = postproc.probe(final)
    want = segs * 10.0
    dur = info.get("duration") or 0
    add("final", "成片可读，%s×%s / %.1fs" % (info.get("width"), info.get("height"), dur),
        "pass" if info.get("width") else "fail", info)
    add("duration", "总时长 = 段数×10s（%.1fs）" % want,
        "pass" if abs(dur - want) <= 0.8 else "warn", "实测 %.2fs，差 %.2fs" % (dur, dur - want))
    size_ok = "%dx%d" % (info.get("width") or 0, info.get("height") or 0)
    expect = (store.load_config().get("video_size") or "1080x1920")
    add("resolution", "分辨率符合提交值", "pass" if size_ok == expect else "warn",
        "%s，提交时是 %s（走了降级链就是这原因）" % (size_ok, expect))

    entry["current"] = "自查 · 音轨"
    vol = postproc.mean_volume(final)
    if not info.get("acodec"):
        add("audio", "有音轨（口播/音乐）", "fail", "没探到音频流——omni 口播可能没生成")
    elif vol is None:
        add("audio", "有音轨（口播/音乐）", "warn", "音量探测失败")
    else:
        add("audio", "有音轨，平均响度 %.1f dB" % vol, "pass" if vol > -55 else "fail",
            "接近静音，检查台词/口播是否写进提示词" if vol <= -55 else "")

    entry["current"] = "自查 · 黑帧"
    blacks = postproc.blackdetect(final)
    add("black", "无黑帧/黑转过场", "pass" if not blacks else "warn",
        ("发现 %d 段黑场：%s" % (len(blacks), blacks[:3])) if blacks else "blackdetect 无命中")

    entry["current"] = "自查 · 画面停滞"
    frz = postproc.freezedetect(final)
    add("freeze", "无 >=2.5s 画面冻结", "pass" if not frz else "warn",
        ("%d 处疑似冻结" % frz) if frz else "freezedetect 无命中")

    entry["current"] = "自查 · 分段规格"
    probes = {}
    for k in range(1, segs + 1):
        sinfo = (st.get("segment_status", {}) or {}).get(str(k), {})
        if sinfo.get("file"):
            probes[str(k)] = postproc.probe(run.root / sinfo["file"]) or {}
    keys = set("%sx%s@%s/%s" % (p.get("width"), p.get("height"), p.get("fps"), p.get("vcodec"))
               for p in probes.values())
    if len(keys) <= 1:
        add("segments", "各段编码规格一致（%d 段）" % len(probes), "pass", list(keys))
    else:
        add("segments", "各段编码规格一致", "warn",
            "规格不一致 %s——拼接走了重编码，衔接处可能有画质跳变" % sorted(keys))

    entry["current"] = "自查 · 关键帧"
    done = [k for k, v in (st.get("frames", {}) or {}).items() if v.get("status") == "done"]
    thumbs = [k for k in done if (run.root / (st["frames"][k].get("thumb") or "")).exists()]
    add("frames", "关键帧齐 %d/%d，缩样 %d 张" % (len(done), total_frames, len(thumbs)),
        "pass" if len(done) == total_frames and len(thumbs) == total_frames else "warn")

    entry["current"] = "自查 · 去水印"
    concat_rel = st.get("final", {}).get("concat")
    box = store.load_config().get("watermark", {})
    if not concat_rel or (run.root / concat_rel) == final:
        add("watermark", "delogo 确实动了水印区", "eyes", "单段直拷或未生成 final.mp4，无法对比")
    else:
        diff = postproc.region_diff(run.root / concat_rel, final, box)
        if diff is None:
            add("watermark", "delogo 确实动了水印区", "eyes", "抽帧失败，手动看右下角")
        else:
            add("watermark", "delogo 动了水印区（像素均差 %.2f）" % diff,
                "pass" if diff > 0.5 else "fail",
                "水印区几乎没变化：可能框位置不对或没跑 delogo" if diff <= 0.5 else "")

    entry["current"] = "自查 · 密钥残留"
    jsons = list(run.root.rglob("*.json"))
    txts = list(run.root.rglob("*.txt"))
    key = zexp.api_key()
    leaks = []
    if key:
        for path in jsons + txts + [store.CONFIG_PATH]:
            try:
                if path.is_file() and key in path.read_text(encoding="utf-8", errors="ignore"):
                    leaks.append(path.name)
            except OSError:
                continue
    add("keyleak", "run 目录与 config 无密钥残留", "pass" if not leaks else "fail",
        "在这些文件里发现了 Key：%s" % leaks if leaks else "扫了 %d 个文本文件" % (len(jsons) + len(txts) + 1))

    for cid, label in eyes_checks(run.mode):
        results.append({"id": cid, "label": label, "verdict": "eyes", "detail": "要人眼看，自动化查不了（接 VLM 另算额度）"})
    passed = sum(1 for r in results if r["verdict"] == "pass")
    warns = sum(1 for r in results if r["verdict"] == "warn")
    fails = sum(1 for r in results if r["verdict"] == "fail")
    st["selfcheck"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": results,
                       "summary": "%d 通过 · %d 提醒 · %d 不合格 · %d 项要人眼" % (
                           passed, warns, fails, len(eyes_checks(run.mode)))}
    st["stage"] = "done"
    run.save()
    log_line(run_name, "自查完成：%s" % st["selfcheck"]["summary"])


@app.route("/api/run/<name>/country", methods=["POST"])
def put_country(name):
    """目标国家/市场：决定台词与口播语言、文化语境。AI 生成与手工粘贴都适用。"""
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    country = str((request.get_json(silent=True) or {}).get("country", "")).strip()
    if country and country not in prompts.COUNTRY_LANG:
        return jsonify({"error": "未知国家：%s" % country}), 400
    run.state["country"] = country
    run.save()
    return jsonify({"ok": True, "voiceover": effective_voiceover(run)})


def do_reason_brief(run_name, hint, country, platform):
    """1 次 chat：拿产品基准卡推理卖点提炼 + 3 个故事方向，供用户挑一个填进卖点框。"""
    run = store.open_run(run_name)
    entry = job(run_name)
    cfg = store.load_config()
    st = run.state
    card = st.get("product_card") or {}
    entry["current"] = "推理卖点与故事方向"
    if not card:
        log_line(run_name, "△ 还没有产品基准卡，这一版只能凭产品名和你的想法猜；建议先回第 0 步「提取产品特征」")
    else:
        log_line(run_name, "已带上产品基准卡（%d 个部件），uncertain 项会被禁止当事实" % len(card.get("parts") or []))
    try:
        text = zexp.chat(prompts.build_brief_messages(st.get("title", ""), card, country, platform,
                                                     st.get("duration") or 10, st.get("segments") or 1, hint),
                         model=cfg.get("script_model") or DEFAULT_SCRIPT_MODEL, temperature=0.9, max_tokens=3000)
        data = prompts.extract_json(text)
    except Exception as exc:
        log_line(run_name, "推理失败：%s" % str(exc)[:400])
        return
    options = [o for o in (data.get("story_options") or []) if isinstance(o, dict)]
    if not options:
        log_line(run_name, "模型没给出故事方向，未采纳")
        return
    st["brief"] = {"selling_points": str(data.get("selling_points_zh") or "").strip(),
                   "options": options, "avoid": [str(a) for a in (data.get("avoid") or [])],
                   "country": country, "hint": hint, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (run.root / "brief.json").write_text(json.dumps(st["brief"], ensure_ascii=False, indent=2), encoding="utf-8")
    run.save()
    log_line(run_name, "✓ 推理出 %d 个故事方向，去第 1 步点一条填进卖点框" % len(options))


@app.route("/api/run/<name>/reason_brief", methods=["POST"])
def reason_brief(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    if DRY_RUN:
        return jsonify({"error": "当前是 dry-run 模式（VIDEO_UI_DRYRUN=1），不调 chat"}), 400
    if not zexp.chat_ready(store.load_config().get("script_model") or DEFAULT_SCRIPT_MODEL):
        return jsonify({"error": "缺 ZEXAPI_API_KEY（推理要调 1 次 chat）"}), 400
    payload = request.get_json(silent=True) or {}
    ok, msg = submit(name, "reason_brief", do_reason_brief,
                     str(payload.get("hint", "")).strip()[:800],
                     str(payload.get("country", "")).strip(),
                     str(payload.get("platform", "")).strip())
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run/<name>/selfcheck", methods=["POST"])
def selfcheck(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    ok, msg = submit(name, "selfcheck", do_selfcheck)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run/<name>/preview_prompt")
def preview_prompt(name):
    run = run_or_404(name)
    if isinstance(run, tuple):
        return run
    idx = int(request.args.get("frame", 0))
    seg = int(request.args.get("segment", 0))
    if idx:
        beat = next((b for b in run.beats() if b["_id"] == idx), None)
        if not beat:
            return jsonify({"error": "no beat"}), 400
        refs, note = prompts.reference_plan(run, idx, beat, request.args.get("ref_mode", "skill"))
        roles = prompts.ref_roles(run, idx, beat, refs)
        prompt_final = prompts.assemble_frame_prompt(
            beat, run.script, ref_clause=prompts.reference_clause(refs, roles),
            card=run.state.get("product_card"), run=run)
        return jsonify({"prompt": prompt_final, "refs": [Path(r).name for r in refs],
                        "roles": roles, "note": note,
                        "zh": beat.get("keyframe_prompt_zh") or ""})
    if seg:
        return jsonify({"prompt": prompts.assemble_video_prompt(
            run, seg, (run.state.get("camera", {}) or {}).get(str(seg), ""), effective_voiceover(run))})
    return jsonify({"error": "参数缺失"}), 400


@app.route("/files/<name>/<path:relpath>")
def serve_file(name, relpath):
    try:
        run = store.open_run(name)
    except Exception:
        return jsonify({"error": "run 不存在"}), 404
    safe = safe_join(str(run.root), relpath)
    if safe is None or not Path(safe).is_file():
        return jsonify({"error": "no such file"}), 404
    return send_from_directory(str(Path(safe).parent), Path(safe).name)


_worker_thread = threading.Thread(target=_worker, daemon=True, name="pipeline-worker")
_worker_thread.start()


if __name__ == "__main__":
    store.RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    print("产品图生视频 UI -> http://127.0.0.1:%d" % PORT)
    print("runs 目录:", store.RUNS_ROOT, "| zexapi key:", "已读到" if zexp.has_key() else "未设置")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False, use_reloader=False)
