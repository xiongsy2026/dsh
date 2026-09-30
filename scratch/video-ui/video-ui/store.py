"""Run 目录与状态持久化。产物一律落在 H 盘，不碰 C 盘。"""

import json
import math
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = Path(os.environ.get("VIDEO_UI_RUNS", r"H:\AI数据\video_runs"))
CONFIG_PATH = UI_ROOT / "config.json"

SUBDIRS = ["frames", "frames/thumb", "segments", "video", "staging", "assets", "assets/thumb"]
ASSET_KINDS = {"product": "prod", "character": "char", "prop": "prop"}

DEFAULT_CONFIG = {
    "gflow_exe": r"C:\Users\Administrator\.local\bin\gflow-cli.exe",
    "gflow_project": "",
    "image_backend": "gflow",
    "image_model": "nano-pro",
    "zexapi_image_model": "nano_banana_pro-2K",
    "doubao_model": "Seedream 4.5",
    "video_model": "omni_flash-hd-10s",
    "script_model": "gemini-3.5-flash",
    "video_size": "1080x1920",
    "fallback_size": "720x1280",
    "fallback_model": "omni_flash-10s",
    "watermark": {"x": 880, "y": 1690, "w": 110, "h": 110},
    "voiceover": {"enabled": True, "gender": "female", "language": "American English"},
}

RUN_NAME_RE = re.compile(r"^[\w\-.\u4e00-\u9fff]{1,60}$")


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def segments_for(duration):
    return max(1, int(math.ceil(float(duration) / 10.0)))


def frame_key(idx):
    return "%02d" % idx


class Run(object):
    """一个 video_runs/<name> 目录。run.json 是 UI 状态，task_log.json 是 API 记录。"""

    def __init__(self, root):
        self.root = Path(root)
        self.name = self.root.name
        self.state = self._read(self.root / "run.json", {})

    # ---------- io ----------
    @staticmethod
    def _read(path, default):
        path = Path(path)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _write(path, data):
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self):
        self._write(self.root / "run.json", self.state)

    # ---------- script ----------
    @property
    def script(self):
        return self._read(self.root / "script.json", None)

    def set_script(self, script):
        self._write(self.root / "script.json", script)
        st = self.state
        st["duration"] = script.get("duration", st.get("duration"))
        st["segments"] = script.get("segments_count") or len(script.get("segments", [])) or st.get("segments")
        st["total_frames"] = st["segments"] * 6
        st["stage"] = "script_review"
        st.setdefault("frames", {})
        st.setdefault("frame_review", {})
        self.save()

    # ---------- task log ----------
    def log_task(self, kind, ref, record):
        log = self._read(self.root / "task_log.json", {})
        log.setdefault(kind, {})[str(ref)] = record
        self._write(self.root / "task_log.json", log)

    def task_log(self):
        return self._read(self.root / "task_log.json", {})

    # ---------- paths ----------
    def frame_file(self, idx):
        rel = self.state.get("frames", {}).get(frame_key(idx), {}).get("file")
        return self.root / rel if rel else None

    def thumb_file(self, idx):
        return self.root / "frames" / "thumb" / ("frame_%s.jpg" % frame_key(idx))

    @property
    def product_image(self):
        for cand in ("product.png", "product.jpg", "product.webp", "product.jpeg"):
            p = self.root / cand
            if p.exists():
                return p
        return None

    @property
    def anchor_image(self):
        """人锚 = 老的 anchor.* 文件；没有则退回名册里第一个角色图。"""
        p = self.root / "anchor.png"
        if p.exists():
            return p
        p = self.root / "anchor.jpg"
        if p.exists():
            return p
        chars = self.assets_by_kind("character")
        if chars:
            return self.root / chars[0]["file"]
        return p

    def assets_by_kind(self, kind):
        """同种资产按「主图在前」的稳定顺序返回（product: 老的 product.png 永远排第一）。"""
        out = [a for a in self.assets().values() if (a.get("kind") or "product") == kind]
        out.sort(key=lambda a: (a["id"] != "product", a["id"]))
        return out

    def product_group(self):
        """产品主图 + 细节图，算同一个逻辑主体：主图永远第一。"""
        return self.assets_by_kind("product")

    # ---------- 模式与资产（角色/道具/产品） ----------
    @property
    def mode(self):
        return self.state.get("mode") or "product"

    def assets(self):
        """统一视图：剧情模式用 state.assets；产品模式把老的 product.png + product_card 也当成一个资产。"""
        out = {}
        for aid, a in (self.state.get("assets") or {}).items():
            item = dict(a)
            item["id"] = aid
            out[aid] = item
        if self.mode == "product" and "product" not in out and self.product_image:
            out["product"] = {"id": "product", "kind": "product", "name": "产品主图",
                              "file": "product%s" % self.product_image.suffix,
                              "thumb": "product_thumb.jpg",
                              "card": self.state.get("product_card") or {}}
        return {k: v for k, v in out.items() if (self.root / (v.get("file") or "")).exists()}

    def asset_by_name(self, name):
        for aid, a in self.assets().items():
            if a.get("name") == name or aid == name:
                return a
        return None

    def add_asset(self, src, kind, name):
        import genimg
        kind = kind if kind in ASSET_KINDS else "prop"
        pfx = ASSET_KINDS[kind]
        # 只认 prod1/char1/prop1 这种「前缀+数字」的 id：老的 product 主图也以 prod 开头，别误当序号
        used = [int(m.group(1)) for a in self.assets().values()
                for m in [re.match(r"^%s(\d+)$" % pfx, str(a.get("id", "")))] if m] or [0]
        aid = "%s%d" % (pfx, max(used) + 1)
        fname = getattr(src, "filename", None) or str(src)
        ext = Path(fname).suffix.lower() or ".png"
        rel = "assets/%s%s" % (aid, ext)
        thumb_rel = "assets/thumb/%s.jpg" % aid
        (self.root / "assets").mkdir(parents=True, exist_ok=True)
        (self.root / "assets" / "thumb").mkdir(parents=True, exist_ok=True)
        dest = self.root / rel
        if hasattr(src, "save"):           # werkzeug FileStorage
            src.save(str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        genimg.make_thumb(dest, self.root / thumb_rel)
        self.state.setdefault("assets", {})[aid] = {
            "kind": kind, "name": (name or "").strip()[:40] or ("%s%d" % (kind, used[-1] + 1)),
            "file": rel, "thumb": thumb_rel, "card": {}}
        self.save()
        return aid

    def update_asset(self, aid, name=None, card=None):
        a = (self.state.get("assets") or {}).get(aid)
        if not a:
            return False
        if name is not None:
            a["name"] = str(name).strip()[:40] or a.get("name")
        if card is not None:
            a["card"] = card
        self.save()
        return True

    def remove_asset(self, aid):
        a = (self.state.get("assets") or {}).pop(aid, None)
        if not a:
            return False
        for rel in (a.get("file"), a.get("thumb")):
            try:
                if rel and (self.root / rel).exists():
                    (self.root / rel).unlink()
            except OSError:
                pass
        self.save()
        return True

    def beats(self):
        """把 segments 摊平成全局拍序，返回 [(idx, segment_index, beat_dict), ...]"""
        out = []
        script = self.script or {}
        for si, seg in enumerate(script.get("segments", []), start=1):
            for local_id, beat in enumerate(seg.get("beats", []), start=1):
                idx = (si - 1) * 6 + local_id
                entry = dict(beat)
                entry["_segment"] = si
                entry["_local_id"] = local_id
                entry["_id"] = idx
                out.append(entry)
        return out

    def segments(self):
        return (self.script or {}).get("segments", [])

    def stage_flags(self):
        st = self.state
        total = st.get("total_frames") or 0
        done = sum(1 for f in st.get("frames", {}).values() if f.get("status") == "done")
        approved = sum(1 for f in st.get("frame_review", {}).values() if f.get("verdict") == "approved")
        segs = st.get("segments") or 0
        vdone = sum(1 for s in st.get("segment_status", {}).values() if s.get("status") == "done")
        return {
            "has_script": bool(self.script),
            "script_approved": bool(st.get("review", {}).get("script_approved")),
            "frames_done": done,
            "frames_total": total,
            "frames_approved": approved,
            "frames_gate": bool(st.get("review", {}).get("frames_approved")),
            "segments_done": vdone,
            "segments_total": segs,
            "final": st.get("final", {}).get("clean") or st.get("final", {}).get("concat"),
        }


def create_run(title, duration, mode="product"):
    slug = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", title.strip())[:40] or "run"
    name = "%s-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), slug, ("%04d" % (int(time.time()) % 10000)))
    root = RUNS_ROOT / name
    if RUNS_ROOT.exists() and len(list(RUNS_ROOT.iterdir())) >= 400:
        raise RuntimeError("runs 目录过大，先手动清理")
    root.mkdir(parents=True, exist_ok=False)
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    run = Run(root)
    run.state = {
        "name": name,
        "title": title.strip(),
        "mode": mode if mode in ("product", "story") else "product",
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration": float(duration),
        "segments": segments_for(duration),
        "total_frames": segments_for(duration) * 6,
        "stage": "intake",
        "assets": {},
        "frames": {},
        "frame_review": {},
        "segment_status": {},
        "camera": {},
        "review": {},
        "final": {},
    }
    run.save()
    return run


def open_run(name):
    if not RUN_NAME_RE.match(name or ""):
        raise ValueError("非法 run 名")
    root = RUNS_ROOT / name
    if not root.is_dir():
        raise ValueError("run 不存在: %s" % name)
    return Run(root)


def dir_size(path):
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def trash_run(name):
    """删 run：只允许 os.rename 移到 _trash（重试几次），搬不动就打 .deleted 软删标记。
    绝不用 shutil.move —— 它在 Windows 上遇到占用文件会退化成「复制+删除」，可能删坏数据。"""
    run = open_run(name)
    trash = RUNS_ROOT / "_trash"
    trash.mkdir(exist_ok=True)
    dest = trash / ("%s--%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), run.name))
    last = None
    for _ in range(8):
        try:
            os.rename(str(run.root), str(dest))
            return dest, "moved"
        except OSError as exc:
            last = exc
            time.sleep(0.5)
    (run.root / ".deleted").write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return last, "hidden"


def list_runs():
    if not RUNS_ROOT.exists():
        return []
    out = []
    for p in sorted(RUNS_ROOT.iterdir(), key=lambda q: q.stat().st_mtime, reverse=True):
        if not (p / "run.json").exists() or (p / ".deleted").exists():
            continue
        try:
            run = Run(p)
        except Exception:
            continue
        st = run.state
        out.append({
            "name": p.name,
            "title": st.get("title", ""),
            "mode": run.mode,
            "duration": st.get("duration"),
            "segments": st.get("segments"),
            "stage": st.get("stage"),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "size_mb": round(dir_size(p) / 1048576.0, 1),
            "flags": run.stage_flags(),
        })
    return out
