r"""工作区侧的意图登记 —— 点完 skill 后 agent 要跑的第一条命令（沙箱里也跑得动）。

为什么要有这个文件：登记动作过去只有一条路 `python backend/hypit_bridge.py intent ...`，
而那条命令要写 `.gateway_intent.json` —— **在工作区外**，DSH 自带的文件沙箱会直接拒
（实测报 `sandbox escalation … is not strictly wider`）⇒ 结果就是"用户明明点了 /hypit-pipeline，
右栏却什么都不弹"。本脚本只写自己工作区里的 `.dsh/intent.json`，8100 的判定口两个源合并读
（同会话取时间更新的那个），所以 agent 不需要任何提权。

用法（PowerShell 方言，会话 id 是 DSH 注入的托管变量）：
    python scripts/intent.py hypit "C:\\dsh\\hypit_runs\\productions\\<工程>\\build.svrun"
读得到 DSH_SESSION_ID 就不用带 --session；拿不到就显式给：
    python scripts/intent.py hypit <工程路径> --session session-xxxxxxxx

跑完会把判定口的答复打出来（show/state/port），当场就知道界面会不会出。
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🔴 Windows 控制台默认 GBK：不重设编码的话，下面那句中文 JSON 一打印就 UnicodeEncodeError，
#    整条登记命令在真实 agent 里直接失败（2026-10-03 实测踩到）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
MARK = os.path.join(WORKSPACE, ".dsh", "intent.json")
PRESENCE = "http://127.0.0.1:8100/api/workbench/presence?session="
FORM_API = "http://127.0.0.1:8100/api/skills/form?session="
FORM_ATTEMPTS_API = "http://127.0.0.1:8100/api/skills/form/attempts"
USAGE = ("用法: python scripts/intent.py <omni|hypit|super-clone|product-video|film|prompt-reverse|"
         "motion-transfer> [任务名或工程路径] [--session <会话id>]\n"
         "      python scripts/intent.py payload [--session <会话id>]"
         "   # 读对话内表单收上来的引擎入参（登记意图之后、追问客户之前先跑这条）\n"
         "      python scripts/intent.py prefill --set '<json>' [--session <会话id>]"
         "   # 把分析出来的参数【预填】进右栏那张表（客户仍需自己点提交）\n"
         "      python scripts/intent.py fields [--session <会话id>]"
         "   # 只读：这张表要哪些 key/类型/必填/条件显隐，并给一份可抄的 --set 模板（猜字段名会被拒）\n"
         "      python scripts/intent.py attempts [条数]"
         "   # 只读：客户点了【提交】但没成交的最近几次（缺哪格/哪条会话/拒因）——"
         "问\"我点了没反应\"先跑这条，别猜\n"
         "可选 --task-dir <已建好的目录>：agent 先建了家再登记时用（任务名仍走位置参数）")
FORM_API_POST = "http://127.0.0.1:8100/api/skills/form"
# 🔴 可登记的意图档名单。权威源是 `backend/hypit_bridge.py` 的 KNOWN_SKILLS，
#    但这条命令是"点完 skill 后 agent 跑的第一条"，不该为了拿一个名单去 import 整个网关（起不来就全瞎）。
#    所以这里留一份短名单，由 scripts/check-intent-rule.py 断言【两份必须相等】—— 漂了就红。
#    为什么必须校验：过去这条脚本对 args[0] 完全不判，打错一个字母就往意图簿里写出一条死档
#    （实测我自己把 skill="prefill" 写进去过 ⇒ 那条会话既不弹台也没法清，只靠 24h TTL 兜）。
LANES = ("omni", "hypit", "super-clone", "product-video", "film", "prompt-reverse", "motion-transfer")
# 聊天附件的落点（DSH 把用户上传的东西放这儿，agent 拿只读路径）
ATTACH_GLOBS = (os.path.expandvars(r"%APPDATA%\dsh-desktop\harness\attachments"),)

# 🔴 哪些档登记时建任务目录（素材规范化的推行范围）。2026-10-10 用户拍：先只推超级复刻。
#    hypit 故意不在这里 —— 它登记时传的是 `.svrun` 工程路径，工程目录本身就是任务目录，语义不同，单独一轮。
#    加一档之前先想清楚：这条线的表单素材要不要按任务分根。
TASK_DIR_LANES = ("super-clone",)
TASK_SUBDIRS = ("inputs", "src", "outputs", "frames")


def task_slug(skill, raw):
    """任务名 → ASCII 目录名。中文洗空 ⇒ 退到 `<skill>-<yyyymmdd-HHMM>`，绝不拿中文当目录名：
    8100 的静态口与交付直链都要求文件名 ASCII（实测过），中文目录会在交付那一步才炸。"""
    keep = re.sub(r"[^A-Za-z0-9._-]", "", str(raw or "")).strip("._-")[:48]
    if len(keep) >= 3:
        return keep
    return "%s-%s" % (skill, time.strftime("%Y%m%d-%H%M"))


def ensure_task_dir(skill, raw):
    """建（或复用）`productions/<slug>/` 与四个子目录，返回绝对路径。

    为什么由"登记意图"这一步来建：登记是点了 skill 之后【必然发生】的动作。绑在必然发生的动作上，
    比指望 agent 记得建目录可靠（同一条原则也用在"建 run 自动钉意图"上）。

    任务名有两种形状，都认：
      ① 一个目录（agent 已经先建好了家，或 hypit 那种传路径的写法）⇒ 就用它，绝不另起一个；
      ② 一个名字 ⇒ ASCII 化成 slug；同名目录已存在就复用（同一任务重登记不该长出第二个家）。
    """
    raw = str(raw or "").strip()
    cand = raw if os.path.isabs(raw) else (os.path.join(WORKSPACE, raw) if raw else "")
    if cand and os.path.isdir(cand):
        root = os.path.realpath(cand)
    else:
        root = os.path.join(WORKSPACE, "productions", task_slug(skill, raw))
    os.makedirs(root, exist_ok=True)
    for sub in TASK_SUBDIRS:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return os.path.realpath(root)


def die(msg, code=2):
    print(msg)
    sys.exit(code)


_SLOT_PREFIX = re.compile(r"^(?:\d{2}-[0-9a-fA-F]{8}-)+")


def slot_stem(src: str) -> str:
    """槽内落盘名里那半截「原名」：先剥掉上一轮回填拼上去的 `NN-sha8-` 前缀，再清洗、截断。

    🔴 为什么要剥（2026-10-14 现网 15:12）：把【已经归位过的槽内文件】当源再跑一次回填，
       不剥前缀就拼成 `01-317a8726-01-317a8726-video-317A8726-video16`，再被 32 字上限截断 ⇒
       注册出一个盘上根本不存在的路径；服务端存了它，客户点【提交给 agent 分析】就被
       「第 1 个文件不在表单上传目录里」拒掉 —— 连点四次同一死因，而素材好好地躺在槽里。
    """
    raw = _SLOT_PREFIX.sub("", os.path.splitext(os.path.basename(str(src or "")))[0])
    return re.sub(r"[^A-Za-z0-9._-]", "", raw)[:32] or "file"


def prefill_key_problems(values, keys, media_keys) -> list:
    """纯函数（闸整块抠出来跑值表）：回填的 key 有没有用错。
    🔴 两类都必须【过不去】，不能静默丢：
      ① 未知 key —— 2026-10-11 现网 20:56：agent 发的是 `source_video`/`ratio`（这条表的 key 是
         `video`/`aspect_ratio`，那名字来自 motion-transfer 那条线 ⇒ 跨线串味），服务端只记进 dropped；
      ② 媒体格塞进 values —— 服务端对媒体字段直接 continue（媒体只认 files），连 dropped 都不进，
         于是"product_images 填了"而表上还是空的。
    结果就是退出码 0、agent 以为回填成功、客户 21:02 把已经给过的视频和图【再传一遍】。"""
    bad = []
    media_sorted = "、".join(sorted(media_keys)) or "（这条线没有媒体格）"
    unknown = sorted(str(k) for k in (values or {}) if str(k) not in keys)
    misplaced = sorted(str(k) for k in (values or {}) if str(k) in media_keys)
    if unknown:
        bad.append("这些 key 不在这条表里：%s ｜ 合法 key：%s ｜ 媒体格（只走 files，不能进 values）：%s"
                   % ("、".join(unknown), "、".join(sorted(keys)), media_sorted))
    if misplaced:
        bad.append("媒体格 %s 不能走 values，只认 files —— 改成 "
                   '"files":[{"field":"%s","src":"<盘上路径>"}]'
                   % ("、".join(misplaced), misplaced[0]))
    return bad


def cmd_prefill(args, sid):
    """把 agent 分析出来的参数【预填】进右栏那张表。

    🔴 这条命令永远不会"替客户提交"：它只写 form_prefill.json（门控里回显成 defaults），
       载荷文件 form_payload.json 由服务端在【客户点提交】时才写。判据全在 8100 那一份，
       这里只搬运 —— 所以脚本里没有第二个校验器（两份必然漂）。
    图片/视频：聊天附件在 %APPDATA%\\dsh-desktop\\harness\\attachments 下，agent 有只读路径；
       这里把它复制进【表单上传目录】里（服务端那道 realpath 守卫认这个根），再以 path 交回去。
       复制而不是引用原路径：附件目录属于内核，服务不该读工作区外的东西。
    """
    if not sid:
        die("prefill 也要会话 id：$env:DSH_SESSION_ID 或 --session <会话id>")
    raw = ""
    if "--set" in args:
        i = args.index("--set")
        if i + 1 >= len(args):
            die("--set 后面要跟 JSON（或 @文件.json）\n" + USAGE)
        raw = args[i + 1]
    if not raw:
        die("prefill 需要 --set '<json>'\n" + USAGE)
    if raw.startswith("@"):
        try:
            with open(raw[1:], encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            die("读不到那个文件：%s" % str(e)[:120])
    try:
        body = json.loads(raw)
    except ValueError as e:
        die("--set 不是合法 JSON：%s" % str(e)[:120])
    if not isinstance(body, dict):
        die("--set 要是一个对象：{\"values\":{...},\"files\":[...],\"notes\":{...}}")

    # 先问门控：这条会话登记的是哪个 skill、字段表长什么样（唯一源仍是 8100，不在这抄字段）
    try:
        with urllib.request.urlopen(FORM_API + urllib.parse.quote(sid), timeout=8) as r:
            gate = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        die("读门控失败（8100 没起？）：%s: %s" % (type(e).__name__, str(e)[:120]), 3)
    if not gate.get("show"):
        die("这条会话现在不该有表单（reason=%s）—— 先登记意图（intent.py <skill>）再回填；"
            "登记了但那条线没配 form: 段，也不该硬塞。" % gate.get("reason"), 4)
    skill = gate.get("skill") or ""
    fields = gate.get("fields") or []
    keys = {f["key"] for f in fields}
    # 媒体槽的判据【读服务端下发的 accept】，不在脚本里再抄一份类型名单：
    # 原来这里写死 ("images","video")，加第七种类型 audio 之后，音频回填会被这行【静默丢掉】。
    media = {f["key"]: f for f in fields if f.get("accept")}

    # 🔴 素材根【只由服务端下发】。这里以前硬写 `.dsh/form-uploads/agent/<param>` —— 那是第二份路径
    #    规则，而且是跨会话共享的那一份：两条会话都回填 product_images ⇒ 同名互相覆盖，谁都不知道。
    _kp = prefill_key_problems(body.get("values") or {}, keys, set(media))
    if _kp:
        die("回填的 key 用错了 ⇒ 一条都没提交（未知字段被静默丢掉 = 客户第二天还得重传）：" + " / ".join(_kp)
            + " ｜ 先跑 python scripts/intent.py fields 看这张表到底要哪些 key（含可直接抄的模板）。", 6)
    updir = str(gate.get("upload_root") or "").strip()
    if not updir or not os.path.realpath(updir).startswith(os.path.realpath(WORKSPACE) + os.sep):
        die("门控没回一个【工作区内】的 upload_root ⇒ 不敢自己拼路径（自己拼就是第二份规则）。"
            "先确认 8100 已重启到新版。", 3)
    files_out, copied, skipped = [], [], []
    for n, item in enumerate(body.get("files") or []):
        fk = str(item.get("field") or "").strip()
        src = str(item.get("src") or item.get("path") or "").strip()
        if fk not in media:
            # 字段表没这一格 ⇒ 丢，不猜（服务端也会丢）
            skipped.append("%s=字段表里没这一格" % (fk or "(空 field)"))
            continue
        if not src or not os.path.isfile(src):
            # 文件不在 ⇒ 丢并【大声计数】：以前静默 continue，于是"只回填了一格文字"看不出来
            skipped.append("%s=文件不在盘上(%s)" % (fk or "(空 field)", "没给路径" if not src else src))
            continue
        # 扩展名/MIME 白名单【只在服务端判一份】（_form_prefill_paths 按槽查 FORM_*_EXT）。
        # 这里原来又抄了一份 png/jpg/mp4 名单 —— 抄第二份的必然结局是：服务端收了、这里先丢了。
        # 槽目录名用【字段 key】，与服务端回显口的主路径同一套布局（客户端自己上传也落这里）。
        slot = os.path.join(updir, re.sub(r"[^A-Za-z0-9._-]", "", fk)[:24] or "slot")
        os.makedirs(slot, exist_ok=True)
        ext = os.path.splitext(src)[1].lstrip(".").lower()
        stem = slot_stem(src)
        with open(src, "rb") as fh:
            sha8 = hashlib.sha256(fh.read()).hexdigest()[:8]
        dst = os.path.join(slot, "%02d-%s-%s.%s" % (n + 1, sha8, stem, ext))
        if os.path.realpath(os.path.dirname(dst)) != os.path.realpath(slot):
            skipped.append("%s=落盘路径会写出槽目录，已丢弃" % fk)
            continue                       # 双保险：绝不允许写出这个目录
        # 🔴 内容已经在那儿就别再写一次（2026-10-10 实测：重跑同一条回填撞 PermissionError[13] ——
        #    8100 的静态口/预览正握着那个文件）。同名同 sha8 = 同一份字节，直接复用；
        #    真要覆盖时写不进去就【明说】是哪一格、哪个文件、为什么，别抛 traceback 让整条命令消失。
        if os.path.isfile(dst):
            with open(dst, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest()[:8] == sha8:
                    copied.append(dst)
                    files_out.append({"field": fk, "path": dst})
                    continue
        try:
            shutil.copyfile(src, dst)
        except OSError as e:
            die("复制失败：%s（%s: %s）⇒ 这条素材没回填进去。多半是那个文件正被 8100 预览/静态口占用；"
                "先换个新文件名（或关掉右栏那张预览卡）再重跑同一条 --set。"
                % (dst, type(e).__name__, str(e)[:100]), 5)
        copied.append(dst)
        files_out.append({"field": fk, "path": dst})

    payload = {"session": sid, "skill": skill, "stage": "prefill",
               "values": {k: v for k, v in (body.get("values") or {}).items() if k in keys},
               "files": files_out,
               "notes": {k: str(v)[:120] for k, v in (body.get("notes") or {}).items() if k in keys}}
    dropped = []          # 未知 key 在上面就已经过不去，这里恒空（只为下面 print_obj 的字段形状不变）
    try:
        req = urllib.request.Request(FORM_API_POST, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "text/plain;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        die("回填失败（8100 没接受？）：%s: %s" % (type(e).__name__, str(e)[:160]), 3)
    print_obj = {"ok": bool(out.get("ok")), "session": sid, "skill": skill,
                 "filled": sorted(payload["values"]), "files_copied": len(copied),
                 "files_skipped": skipped,
                 "dropped_keys": sorted(dropped)}
    if out.get("ok"):
        # 🔴 回填覆盖率【由服务端算】（哪些必填格到现在还没值、素材根里哪些文件没被引用），
        #    这里只回显 —— 判据写第二份必然漂，漂了就变成"脚本说填好了、入口却拦着"。
        try:
            with urllib.request.urlopen(FORM_API + urllib.parse.quote(sid), timeout=8) as r:
                _g2 = json.loads(r.read().decode("utf-8"))
            _unf = [str(k) for k in (_g2.get("unfilled_required") or [])]
            _loose = [os.path.basename(str(p)) for p in (_g2.get("loose_materials") or [])]
            _gap = [str(k) for k in (_g2.get("prefill_gap") or [])]
            print_obj["unfilled_required"] = _unf
            print_obj["loose_materials"] = _loose
            print_obj["prefill_gap"] = _gap
            if _gap:
                print_obj["next"] = (
                    "🔴 这次回填不完整：必填素材格 %s 还没值，而素材根里躺着 %s 没被引用 —— "
                    "把它们用 --set 的 files 回填进对应格子（原片进 inputs\\，不是 src\\），"
                    "否则走 runner 入口会被【表单前置】拒。"
                    "只有客户手上确实没有的素材才留空让他自己传。"
                    % ("/".join(_gap), "、".join(_loose)))
            else:
                print_obj["next"] = ("表已在右栏预填好；🔴 客户还没提交 —— 不要当成参数已确认，"
                                     "等他在右栏点【提交这张表】再往下跑。")
        except Exception as e:
            print_obj["coverage_check"] = "读不到（%s: %s）—— 不代表已填齐，重试这条命令再看" % (
                type(e).__name__, str(e)[:120])
    else:
        print_obj["next"] = str(out.get("error"))[:160]
    print(json.dumps(print_obj, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 5


def fields_view(gate) -> dict:
    """纯函数（闸整块抠出来跑值表）：把门控回的那张表翻成【key 清单 + 可直接抄的 --set 模板】。

    🔴 抽成函数而不是写在 cmd_fields 里：写在 IO 函数里就没法测，而这段的产出正是"agent 抄的那份"，
       它错一个分支（媒体格进了 values、🔒 行被当可填项）就等于让客户白传一遍素材。
    """
    updir = str(gate.get("upload_root") or "")
    tpl_v, tpl_f, tpl_n, lines = {}, [], {}, []
    for f in (gate.get("fields") or []):
        k = str(f.get("key") or "")
        ft = str(f.get("type") or "")
        req = "必填" if f.get("required") else "选填"
        ci = f.get("show_if") or {}
        cond = ("  [仅当 %s=%s 才出现/才必填]" % (ci["key"], "/".join(ci.get("values") or []))
                if ci.get("key") else "")
        mark = ("  [%s]" % f["marker"]) if f.get("marker") else ""
        lines.append("%-18s %-8s %-4s %-16s %s%s%s"
                     % (k, ft, req, str(f.get("param") or "-"), str(f.get("label") or "")[:22], cond, mark))
        if f.get("accept"):                      # 媒体格：只认 files，模板里绝不进 values
            tpl_f.append({"field": k, "src": os.path.join(updir, k, "01-<sha8>-<名字>.<扩展名>")})
        elif ft == "locked":
            continue                             # 🔒 只读行没有引擎入参，别往模板里塞
        elif ft == "select":
            tpl_v[k] = str((f.get("options") or ["<选一档>"])[0])
        elif ft == "number":
            tpl_v[k] = 0
        else:
            tpl_v[k] = ("<仅当 %s 时才填：%s>" % (ci["key"], f.get("label") or k)) if ci.get("key") \
                else ("<%s>" % (f.get("label") or k))
        tpl_n[k] = "<这一格是凭什么填的（会显示在「AI 已填 · 可改」的悬停上）>"
    return {"fields": lines, "template": {"values": tpl_v, "files": tpl_f, "notes": tpl_n},
            "how": "抄这份改完写进文件，再跑 python scripts/intent.py prefill --set @模板.json"
                   "（🔴 别在 shell 里内联 JSON，引号会被吃掉；媒体只走 files，走 values 会被拒）"}


def cmd_fields(args, sid):
    """把这条会话【该填的那张表】原样列出来 + 一份可直接抄的 --set 模板。

    为什么要有这条：2026-10-11 现网 agent 猜字段名（`source_video`/`ratio` 是另一档 skill 的叫法），
    猜错还是静默的 ⇒ 客户第二天把素材重传一遍。让它不必猜，顺带堵掉跨线串味这条路。
    字段表仍读 8100 的门控（唯一源 = 该 skill 的 SKILL.md `form:` 段），这里不解析第二份。
    """
    if not sid:
        die("fields 也要会话 id：$env:DSH_SESSION_ID 或 --session <会话id>")
    try:
        with urllib.request.urlopen(FORM_API + urllib.parse.quote(sid), timeout=8) as r:
            gate = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        die("读门控失败（8100 没起？）：%s: %s" % (type(e).__name__, str(e)[:140]), 3)
    if not gate.get("show"):
        die("这条会话现在没有表单（reason=%s）—— 先跑 intent.py <档> [任务名] 登记意图。"
            % gate.get("reason"), 4)
    out = {"session": sid, "skill": gate.get("skill"), "task": gate.get("task"),
           "upload_root": str(gate.get("upload_root") or ""), "task_dir": gate.get("task_dir"),
           "prefilled": gate.get("prefilled"), "submitted": gate.get("submitted"),
           "unfilled_required": gate.get("unfilled_required"),
           "prefill_gap": gate.get("prefill_gap"),
           "loose_materials": [os.path.basename(str(x)) for x in (gate.get("loose_materials") or [])]}
    out.update(fields_view(gate))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv):
    args = [a for a in argv]
    sid = os.environ.get("DSH_SESSION_ID") or ""
    if "--session" in args:
        i = args.index("--session")
        if i + 1 >= len(args):
            die("--session 后面要跟会话 id\n" + USAGE)
        sid = args[i + 1]
        del args[i:i + 2]
    # `--task-dir` 给的是【已经建好的家】（agent 先 mkdir 再登记，或 hypit 那种传路径的写法）。
    # 显示用的任务名仍走位置参数 ⇒ 两个语义不互相顶。
    tdir_arg = ""
    if "--task-dir" in args:
        i = args.index("--task-dir")
        if i + 1 >= len(args):
            die("--task-dir 后面要跟目录\n" + USAGE)
        tdir_arg = args[i + 1]
        del args[i:i + 2]
    if not args or args[0] in ("-h", "--help"):
        die(USAGE)
    if args[0] == "fields":
        return cmd_fields(args[1:], sid)
    if args[0] == "attempts":
        # 只读：客户点了【提交】但没成交的最近几次（缺哪格 / 哪条会话 / 拒因）。
        # "我点了没反应"不该靠猜 —— 载荷文件里没那一行时，这里是唯一能查的现场。
        n = args[1] if len(args) > 1 else "10"
        try:
            with urllib.request.urlopen(FORM_ATTEMPTS_API + "?n=" + urllib.parse.quote(n), timeout=8) as r:
                out = json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            die("读不到提交台账（8100 没起？）：%s: %s" % (type(e).__name__, str(e)[:120]), 3)
        rows = out.get("recent") or []
        # 按输出流能力丢字（GBK 控制台上中文/箭头会当场把打印崩掉 = 命令看着像失败）
        blob = (json.dumps({"ok": True, "count": len(rows), "recent": rows}, ensure_ascii=False, indent=1)
                if rows else "最近没有【点了但没成交】的提交记录 ⇒ 客户那一下要么真提交了，要么根本没点到这张表")
        buf = getattr(sys.stdout, "buffer", None)
        data = (blob + "\n").encode(getattr(sys.stdout, "encoding", None) or "utf-8", "replace")
        (buf.write(data) if buf is not None else sys.stdout.write(blob))
        if buf is not None:
            buf.flush()
        return 0
    if args[0] == "prefill":
        return cmd_prefill(args[1:], sid)
    if args[0] == "payload":
        # 读【对话内表单】收上来的参数。判据（TTL、意图档匹配、param 名）全在 8100 那一份里，
        # 这里只搬运，不抄第二份 —— 所以 agent 拿到的就是可以直接喂给引擎的形状。
        if not sid:
            die("payload 也要会话 id：$env:DSH_SESSION_ID 或 --session <会话id>")
        try:
            with urllib.request.urlopen(FORM_API + urllib.parse.quote(sid), timeout=8) as r:
                j = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            die("读表单载荷失败（8100 没起？）：%s: %s" % (type(e).__name__, str(e)[:120]), 3)
        show = bool(j.get("show"))
        out = {"ok": bool(j.get("submitted")), "session": sid,
               "skill": j.get("skill") or "", "intent": j.get("intent") or "",
               "params": (j.get("params") or {}) if show else {},
               "reason": j.get("reason") or ("submitted" if j.get("submitted") else "not-submitted")}
        if show and not j.get("submitted"):
            out["next"] = ("这条会话已登记 %s，表单还没填 ⇒ 可以照旧用聊天收参数，别催。" % j.get("intent"))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    skill = args[0].strip()
    task = args[1].strip() if len(args) > 1 else ""
    if skill not in LANES:
        # 写之前先拒：宁可退出码 2 让人看见，也不要往意图簿里留一条谁也清不掉的死档
        die("没有 %r 这一档意图。可登记的只有：%s —— 要新增一条线，先在 hypit_bridge.KNOWN_SKILLS 加，"
            "再同步这里（闸会核对两份名单一致）。" % (skill, " | ".join(LANES)))
    if not sid:
        die("拿不到会话 id：DSH 会给子进程注入 $env:DSH_SESSION_ID（注意是 PowerShell 写法，"
            "不是 $DSH_SESSION_ID）。确认能读到再重试，或显式加 --session <会话id>。\n" + USAGE)

    data = {}
    if os.path.exists(MARK):
        try:
            with open(MARK, encoding="utf-8") as f:
                loaded = json.load(f)
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}
    rec = {"skill": skill, "task": task, "ts": time.time()}
    if skill in TASK_DIR_LANES:
        # 登记即建任务目录 ⇒ 素材从此有唯一的家（`productions/<slug>/inputs/<槽>`），
        # 服务端读这条 `task_dir` 决定上传根 —— "任务名→目录"这件事第一次有了主人。
        try:
            if tdir_arg and not os.path.isdir(tdir_arg):
                # 先验再建：反过来会先把 slug 目录建出来、再报错，留一个没人用的家
                die("--task-dir 指过去不是已存在的目录：%s" % tdir_arg[:120], 5)
            rec["task_dir"] = ensure_task_dir(skill, tdir_arg or task)
        except OSError as e:
            die("建任务目录失败：%s" % str(e)[:120], 5)
    data[sid] = rec
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    tmp = MARK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, MARK)

    out = {"written": MARK, "session": sid, "skill": skill, "task": task}
    if rec.get("task_dir"):
        out["task_dir"] = rec["task_dir"]
        out["inputs_dir"] = os.path.join(rec["task_dir"], "inputs")
    if skill == "hypit" and not task.lower().endswith((".svrun", ".svml")) and not os.path.isdir(task):
        # 契约要求"登记两次"，但文档是可选步骤 ⇒ 在这里当场提醒，别让右栏卡在 no-project 才发现
        out["hint"] = ("这次登记的任务名不是工程路径 ⇒ 右栏会显示 no-project（还没有可打开的工程）。"
                       "把 .svml/.svrun 写出来之后再跑一次："
                       "python scripts/intent.py hypit \"C:\\dsh\\hypit_runs\\productions\\<工程目录>\"")
    try:
        with urllib.request.urlopen(PRESENCE + urllib.parse.quote(sid), timeout=6) as r:
            p = json.loads(r.read().decode("utf-8"))
        # 回显【自己那一档】。以前只认 omni/hypit，film 登记完打印出来全是 null，
        # 看起来像"没生效"，其实台已经挂上了（2026-10-04 实测误导过一次排查）。
        bench = p.get(skill)
        form = p.get("form") or {}
        if form.get("intent"):
            # 输入表单档（2026-10-09）：登记的是【意图档】而不是页签名（product-video 就没有引擎台），
            # 这时该报"表单台挂上了 + 它的 url"，而不是让人误判成"判定口没有这一档"。
            out["presence"] = {"form": {"intent": True, "skill": form.get("skill"),
                                        "url": form.get("url"), "task": form.get("task")}}
            if isinstance(bench, dict):
                out["presence"][skill] = {k: bench.get(k) for k in ("intent", "up", "state", "reason")
                                          if bench.get(k) is not None}
        elif not isinstance(bench, dict):
            out["presence"] = ("判定口没有 %r 这一档；现有: %s" % (skill, "/".join(sorted(p)) or "(空)"))
        else:
            out["presence"] = {k: bench.get(k) for k in ("intent", "up", "state", "reason", "port", "url", "task")
                               if bench.get(k) is not None}
    except Exception as e:
        out["presence"] = "判定口没回话（8100 没起？）: %s: %s" % (type(e).__name__, str(e)[:120])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
