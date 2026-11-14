#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：交付卡上【下载视频】+【你还可以】的契约（2026-10-08 改挂点后的形状）。

背景（为什么要有这个闸）：官方交付卡与我们的推荐抢同一个 chain 槽
（dsh-client-ui-renderer/lib/client.js:831-849，首个非 null 独占）⇒ 谁抢到谁独占。
定案是"官方长 UI、我们出策略"，于是契约变成三件事，每件都可能悄悄坏：
  1. 官方补丁只读四个可选钩子，钩子缺席必须退回原生（不能抛——抛了整棵插件树死，
     whale-girl 2026-10-08 那次就是 apply() 抛错把桌面端弄成崩溃重启循环）；
  2. 客户端可下载扩展名必须 ⊆ 服务端 FILM_DOWNLOADABLE（否则按钮点出 404 = 名字顶着不具备的东西）；
  3. 推荐判据必须吃 produced ∪ presented（只吃 produced 时成片回合恒不触发 = 已落码≠生效）。

H 组 = 真跑我们插件里的两个纯函数（node 跑真身，不是 Python 重写一遍判据）。
W 组 = 白名单对账 + 变异对照（塞一个服务端没有的扩展名必须判红）。
P 组 = 官方补丁形状（钩子都包 try、条件加项、空数组不画行、且官方文件里不许出现第二份业务规则）。
S 组 = 我们插件侧形状（不再抢槽、旧自绘代码清干净、四个钩子都注册）。
P13~P16 = 下载项文案必须跟着【文件真实类型】走（2026-10-10 事故：png/jpg 也吃"下载视频"那句）。
          判据不吃文案字符串，吃真身：node 里 eval 装机文件那一行 label，t() 查装机字典里的真串。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

# 🔴 控制台是 cp936 时，断言名里的 ⊆/✓ 这类字符会把闸自己崩掉（崩 ≠ 红，整表就没了）。
#    只换 errors，不换 encoding：换编码会把"环境相关"重新引回来。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(HERE), "plugins", "super-clone-ui", "lib", "client.js")
OFFICIAL = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\node_modules"
            r"\@deepseek-ai\dsh-client-ui-deliverables\lib\client.js")
OFFICIAL_BAK = OFFICIAL + ".bak-weeksir-download-20261008"
PRIMITIVES = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\node_modules"
              r"\@deepseek-ai\dsh-client-ui-primitives\lib\index.js")
MODEL_ADMIN = os.path.join(BACKEND, r"model_admin.py")
PATCHER = os.path.join(HERE, "patch-deliverables-weeksir.py")

PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print("%-4s %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


# ---------------- 从 client.js 里抠出真身，交给 node 跑值表 ----------------
def slice_between(src, start_marker, end_marker):
    i = src.index(start_marker)
    j = src.index(end_marker, i)
    return src[i:j]


def build_js(src):
    rules = slice_between(src, "const TURN_TAIL_MAX", "\n    // 可下载扩展名")
    hooks = slice_between(src, "const WEEKSIR_DL_EXT", "\n    function weeksirTriggerDownload")
    recs = slice_between(src, "    function weeksirRecsFromMatched", "\n    function registerWeeksirCardHooks")
    return (
        "const SKILL_DOCK_META = { 'video-enhance': { title: '视频高清修复', tagline: '超分' },"
        " 'video-dewatermark': { title: '视频去水印' }, 'super-clone': { title: '超级复刻' },"
        " omni: { title: 'Omni 出片' } };\n"
        + rules + "\n" + hooks + "\n" + recs + "\n"
        + "module.exports = { weeksirDownloadUrl, weeksirRecsFromMatched, turnTailRecommendations, WEEKSIR_DL_EXT };\n"
    )


# ---------- 下载项文案：从装机文件抠真身跑值表（2026-10-10 事故：png/jpg 也吃"下载视频"那句） ----------
LABEL_RE = re.compile(r"^\t+label: (t\(weeksirDlKind.*)$", re.M)
DICT_ROW_RE = re.compile(r'^\t{3}"presented\.download\.(image|video|file)": "([^"]*)",\s*$')


def label_expr(official_src):
    """装机文件里那一行 label 表达式（跑的就是它，不在闸里重写一遍三元式）。"""
    m = LABEL_RE.search(official_src)
    return m.group(1).rstrip(",") if m else ""


def label_dicts(official_src):
    """按中英文两份字典分别收 download.* 的文案（两份字典由 presented.defaultApp 定位）。

    🔴 键要存成 t() 真正收到的那个全名（"presented.download.image"），存短名会让值表全 MISSING。
    """
    lines = official_src.split("\n")
    anchors = [i for i, l in enumerate(lines) if '"presented.defaultApp":' in l]
    out = []
    for n, start in enumerate(anchors):
        stop = anchors[n + 1] if n + 1 < len(anchors) else len(lines)
        d = {}
        for line in lines[start:stop]:
            m = DICT_ROW_RE.match(line)
            if m:
                d["presented.download." + m.group(1)] = m.group(2)
        out.append(d)
    return out


def run_label_table(expr, dicts):
    """node 里 eval 那一行真身，t() 查的是装机字典里的真串 ⇒ 键丢了会露成 MISSING，不会静默回落。"""
    mod = "".join([
        "const tables = %s;\n" % json.dumps(dicts, ensure_ascii=False),
        "const expr = %s;\n" % json.dumps(expr),
        "const out = {};\n",
        "tables.forEach((dict, i) => {\n",
        "  const t = (k) => (k in dict ? dict[k] : 'MISSING:' + k);\n",
        "  for (const kind of ['image', 'video', 'file', 'code', '']) {\n",
        "    let r;\n",
        "    try { const weeksirDlKind = kind; r = eval(expr); } catch (e) { r = 'THREW:' + e.message; }\n",
        "    out[i + '|' + kind] = r;\n",
        "  }\n",
        "});\n",
        "console.log(JSON.stringify(out));\n",
    ])
    path = os.path.join(tempfile.gettempdir(), "weeksir_label_mod.js")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(mod)
    r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("FAIL node 跑文案真身失败: %s" % (r.stderr or r.stdout)[:400])
    return json.loads(r.stdout)


def ext_type_table(primitives_src):
    """官方 primitives 的 EXTENSION_TYPES（唯一权威源）——闸里不抄第二份扩展名表。"""
    body = slice_between(primitives_src, "const EXTENSION_TYPES = {", "\nconst NAME_TYPES")
    return dict(re.findall(r'(\w+): "(\w+)"', body))


def run_tables(js_path):
    runner = r"""
const m = require(process.argv[2]);
const out = [];
const dl = m.weeksirDownloadUrl, recs = m.weeksirRecsFromMatched;
const P = (p) => dl(p);
const cases = [
  ['win productions mp4', 'C:\\dsh\\productions\\t1\\outputs\\a.mp4'],
  ['posix productions mp4', 'C:/dsh/productions/t1/outputs/a.mp4'],
  ['嵌套子目录', 'C:/dsh/productions/t1/05_shots/videos/SEG-1.mp4'],
  ['中文名要编码', 'C:/dsh/productions/淘宝爆款01/outputs/成片 1.mp4'],
  ['mov', 'C:/dsh/productions/t/a.mov'],
  ['png', 'C:/dsh/productions/t/a.png'],
  ['不在 productions 下', 'C:/dsh/video_runs/a.mp4'],
  ['根目录文件', 'C:/dsh/a.mp4'],
  ['txt 不可下载', 'C:/dsh/productions/t/a.txt'],
  ['json 不可下载', 'C:/dsh/productions/t/phase_state.json'],
  ['env 不可下载', 'C:/dsh/productions/t/.env'],
  ['无扩展名', 'C:/dsh/productions/t/a'],
  ['大写扩展名', 'C:/dsh/productions/t/A.MP4'],
  ['productions 本身当 task 名', 'C:/dsh/productions/a.mp4'],
  ['空串', ''],
  ['null', null],
  ['数字', 42],
  ['对象', { path: 'C:/dsh/productions/t/a.mp4' }],
];
for (const [name, p] of cases) {
  let r;
  try { r = P(p); } catch (e) { r = 'THREW:' + e.message; }
  out.push(['H ' + name, r, null === r ? null : (typeof r === 'string' ? r : String(r))]);
}
const rcases = [
  ['只有 presented 有 mp4（成片回合）', { produced: [], presented: [{ path: 'C:/dsh/productions/t/o.mp4' }] }],
  ['只有 produced 有 mp4', { produced: [{ path: 'C:/dsh/productions/t/o.mp4' }], presented: [] }],
  ['两者都有', { produced: [{ path: 'C:/dsh/productions/t/a.mp4' }], presented: [{ path: 'C:/dsh/productions/t/b.mp4' }] }],
  ['只有图', { produced: [], presented: [{ path: 'C:/dsh/productions/t/a.png' }] }],
  ['只有文案', { produced: [{ path: 'C:/dsh/productions/t/a.md' }], presented: [] }],
  ['两者都空', { produced: [], presented: [] }],
  ['matched 为 null', null],
  ['matched 是空对象', {}],
  ['produced 是字符串数组（旧形状）', { produced: ['C:/dsh/productions/t/a.mp4'], presented: [] }],
  ['脏路径混进来', { produced: [null, 7, {}], presented: [{}, { path: 3 }] }],
];
for (const [name, matched] of rcases) {
  let r;
  try { r = (recs(matched) || []).map((x) => x && x.id); } catch (e) { r = 'THREW:' + e.message; }
  out.push(['R ' + name, JSON.stringify(r), JSON.stringify(r)]);
}
out.push(['L 标签取官方 dock 文案', (recs(rcases[0][1])[0] || {}).label, '视频高清修复']);
const tt = m.turnTailRecommendations;
const tcases = [
  ['T 上限 3 条（多产物也不超）', [['C:/p/t/1.mp4','C:/p/t/2.mp4','C:/p/t/3.png','C:/p/t/4.jpg'], '']],
  ['T 同 id 去重', [['C:/p/t/a.mp4','C:/p/t/b.mp4'], '']],
  ['T 排除本轮已用的技能', [['C:/p/t/a.mp4'], 'video-enhance']],
  ['T 脏输入 paths=null', [null, '']],
  ['T 脏输入 paths=字符串', ['C:/p/t/a.mp4', '']],
  ['T 脏输入 数组混 null/数字/对象', [[null, 7, {}, 'C:/p/t/a.mp4'], '']],
  ['T 脏输入 skill=数字', [['C:/p/t/a.mp4'], 7]],
  ['T 只有文案不推荐', [['C:/p/t/a.md','C:/p/t/b.json'], '']],
  ['T 空数组', [[], '']],
];
for (const [name, args] of tcases) {
  let r;
  try { r = tt(args[0], args[1]); } catch (e) { r = 'THREW:' + e.message; }
  out.push([name, JSON.stringify(r), JSON.stringify(r)]);
}
console.log(JSON.stringify(out));
"""
    with io.open(os.path.join(tempfile.gettempdir(), "_wg_runner.js"), "w", encoding="utf-8") as f:
        f.write(runner)
    r = subprocess.run(["node", os.path.join(tempfile.gettempdir(), "_wg_runner.js"), js_path],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("FAIL node 跑真身失败: %s" % (r.stderr or r.stdout)[:400])
    return json.loads(r.stdout)


def main():
    for p in (CLIENT, OFFICIAL, MODEL_ADMIN, PRIMITIVES, PATCHER):
        if not os.path.exists(p):
            print("FAIL 缺文件:", p)
            return 1
    client = read(CLIENT)
    official = read(OFFICIAL)
    admin = read(MODEL_ADMIN)
    primitives = read(PRIMITIVES)

    # ---------- H 组：真跑纯函数值表 ----------
    js = build_js(client)
    tmp = os.path.join(tempfile.gettempdir(), "weeksir_card_hooks_mod.js")
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    rows = run_tables(tmp)
    want_dl = {
        'H win productions mp4': "http://127.0.0.1:8100/api/film/file?task=t1&rel=outputs%2Fa.mp4&dl=1",
        'H posix productions mp4': "http://127.0.0.1:8100/api/film/file?task=t1&rel=outputs%2Fa.mp4&dl=1",
        'H 嵌套子目录': "http://127.0.0.1:8100/api/film/file?task=t1&rel=05_shots%2Fvideos%2FSEG-1.mp4&dl=1",
        'H mov': "http://127.0.0.1:8100/api/film/file?task=t&rel=a.mov&dl=1",
        'H png': "http://127.0.0.1:8100/api/film/file?task=t&rel=a.png&dl=1",
        'H 大写扩展名': "http://127.0.0.1:8100/api/film/file?task=t&rel=A.MP4&dl=1",
        'H 不在 productions 下': None, 'H 根目录文件': None, 'H txt 不可下载': None,
        'H json 不可下载': None, 'H env 不可下载': None, 'H 无扩展名': None,
        'H productions 本身当 task 名': None, 'H 空串': None, 'H null': None,
        'H 数字': None, 'H 对象': None,
    }
    got = {name: third for name, _second, third in rows}
    for name, want in want_dl.items():
        check(name, got.get(name), want)
    # 中文名：只验"编码过且能解回来"，不钉死字节（各 node 版本 encodeURIComponent 对空格一致，对中文稳定）
    cn = got.get('H 中文名要编码') or ""
    check('H 中文名已 URL 编码', "%" in cn and " " not in cn and cn.endswith("&dl=1"), True)
    want_recs = {
        'R 只有 presented 有 mp4（成片回合）': '["video-enhance","video-dewatermark"]',
        'R 只有 produced 有 mp4': '["video-enhance","video-dewatermark"]',
        'R 两者都有': '["video-enhance","video-dewatermark"]',
        'R 只有图': '["super-clone","omni"]',
        'R 只有文案': '[]',
        'R 两者都空': '[]',
        'R matched 为 null': '[]',
        'R matched 是空对象': '[]',
        'R produced 是字符串数组（旧形状）': '["video-enhance","video-dewatermark"]',
        'R 脏路径混进来': '[]',
    }
    for name, want in want_recs.items():
        check(name, got.get(name), want)
    lbl = dict((n, t) for n, _s, t in rows).get('L 标签取官方 dock 文案')
    check('L 推荐项带官方 dock 文案', lbl, "视频高清修复")
    want_t = {
        'T 上限 3 条（多产物也不超）': '["video-enhance","video-dewatermark"]',
        'T 同 id 去重': '["video-enhance","video-dewatermark"]',
        # 按 skill 的表先走（video-enhance → 去水印+超级复刻），再叠产物表；video-enhance 自己不重复出现
        'T 排除本轮已用的技能': '["video-dewatermark","super-clone"]',
        'T 脏输入 paths=null': '[]',
        'T 脏输入 paths=字符串': '["video-enhance","video-dewatermark"]',
        'T 脏输入 数组混 null/数字/对象': '["video-enhance","video-dewatermark"]',
        'T 脏输入 skill=数字': '["video-enhance","video-dewatermark"]',
        'T 只有文案不推荐': '[]',
        'T 空数组': '[]',
    }
    for name, want in want_t.items():
        check(name, got.get(name), want)
    pool = {"video-enhance", "video-dewatermark", "super-clone", "omni"}
    leaked = sorted({i for name, _s, third in rows if name.startswith(("R ", "T "))
                     for i in json.loads(third or "[]")} - pool)
    check('T 反向对照 推荐池是封闭集合（未接网关/占位价/账本外的腿都不许冒头）', leaked, [])
    # 反向对照：判据不能是"有路径就推荐"（那 md 也会出，退化成广告）
    check('H 反向对照 纯文案回合必须空', got.get('R 只有文案'), '[]')

    # ---------- W 组：白名单对账（服务端是唯一权威源）----------
    m = re.search(r"^FILM_INLINE_TYPES\s*=\s*\{(.*?)\}", admin, re.M | re.S)
    check('W 服务端内联白名单在模块层', bool(m), True)
    inline = set(x.lower() for x in re.findall(r'"([a-z0-9]+)"\s*:', m.group(1))) if m else set()
    md = re.search(r"^FILM_DOWNLOADABLE\s*=\s*tuple\(k for k in FILM_INLINE_TYPES if k != \"(\w+)\"\)", admin, re.M)
    check('W 服务端下载白名单由内联表派生（不抄第二份）', bool(md), True)
    downloadable = (inline - {md.group(1).lower()}) if md else set()
    cm = re.search(r"const WEEKSIR_DL_EXT\s*=\s*\[([^\]]*)\]", client)
    client_ext = set(x.strip().strip('"\'').lower() for x in cm.group(1).split(",") if x.strip()) if cm else set()
    check('W 客户端下载表 ⊆ 服务端可下载表', client_ext <= downloadable, True)
    check('W 服务端可下载表 = 客户端下载表（谁都不许多）', client_ext == downloadable, True)
    # 变异对照：客户端多一个服务端没有的扩展名 ⇒ 上面两条必须红
    mutated = client_ext | {"avi"}
    check('W 变异对照 客户端多一项必须判红', (mutated <= downloadable), False)
    check('W json 不在可下载表（看板数据不算交付物）', "json" in downloadable, False)

    # ---------- P 组：官方补丁形状 ----------
    # rev 的唯一源 = 补丁脚本里那一行（闸不抄第二份：改了补丁忘了改闸 = 假绿）
    patcher = read(PATCHER)
    rev_m = re.search(r'^REV = "([^"]+)"', patcher, re.M)
    rev = rev_m.group(1) if rev_m else ""
    check('P0 补丁脚本里 REV 有且只有一个', (bool(rev), patcher.count('REV = "')), (True, 1))
    check('P1 装机文件带的 rev 就是补丁脚本那个（闸里没抄旧串）', rev in official, True)
    for hook in ("__weeksirDownloadUrl", "__weeksirDownload", "__weeksirTurnRecs", "__weeksirPickRec"):
        check('P2 官方读钩子 %s' % hook, hook in official, True)
    check('P3 所有钩子走同一个 weeksirHook 取值口（只有一处 typeof window 判定）',
          official.count('typeof window !== "undefined" && window[name]'), 1)
    check('P4 四个钩子调用各包一层 try（≥4 处 try）',
          official.count("try {") >= 4, True)
    check('P5 下载项是条件加项（无 URL 就不加）', "}].concat(weeksirDl ? [{" in official, True)
    check('P6 推荐行空数组整行不画', "if (recs.length === 0) return null;" in official, True)
    check('P7 官方用原生图标 IconDownloadOutline16', "IconDownloadOutline16" in official, True)
    check('P8 官方用原生 CSS 类（不自绘样式：不出现内联 style）',
          "style:" in official.split("function weeksirRecsRow")[1].split("function cardDescription")[0], False)
    check('P9 中英推荐行标题齐（recsLabel 各 2 处 + 1 处引用）',
          official.count('"presented.recsLabel"'), 3)
    # 🔴 官方那份里不许出现第二份业务规则
    for bad in ("productions", "8100", "video-enhance", "TURN_TAIL_RULES", "SKILL_DOCK_META"):
        check('P10 官方文件不含业务规则 %s' % bad, bad in official, False)
    check('P11 原版备份在盘（可回退）', os.path.exists(OFFICIAL_BAK), True)
    check('P12 备份里没有 weeksir 痕迹（确实是原版）', "weeksir" in read(OFFICIAL_BAK), False)

    # ---------- P13~P16：下载项的文案必须跟着【文件真实类型】走 ----------
    # 事故形状（2026-10-10 用户点名）：png/jpg 的卡上那句写死成"下载视频"。
    # 老断言只数 '"presented.download"' 出现几次 ⇒ 文案与类型对不上它看不见，所以现在改成跑真身。
    expr = label_expr(official)
    dicts = label_dicts(official)
    check('P13 中英文典各 3 个类型位（不多不少、顺序=补丁脚本 DICT）',
          [len(d) for d in dicts], [3, 3])
    check('P13b 写死的单串 "presented.download": 必须已消失（防回退成一句盖全部）',
          bool(re.search(r'^\t{3}"presented\.download":', official, re.M)), False)
    check('P13c 每个类型位在装机文件里恰好 3 处（中典 + 英典 + label 那一行）',
          [official.count('"presented.download.%s"' % k) for k in ("image", "video", "file")], [3, 3, 3])
    table = run_label_table(expr, dicts) if expr else {}
    want_label = {
        '0|image': "下载图片", '0|video': "下载视频", '0|file': "下载文件",
        '0|code': "下载文件", '0|': "下载文件",
        '1|image': "Download image", '1|video': "Download video", '1|file': "Download file",
        '1|code': "Download file", '1|': "Download file",
    }
    for k, v in sorted(want_label.items()):
        check('P14 文案真身 %s' % k, table.get(k), v)
    # 反向对照 1：label 退回常量（就是这次的事故形状）⇒ 抠表达式这一步必须抠不到
    mutated_const = official.replace(expr, 't("presented.download.video")') if expr else official
    check('P14b 变异对照 退回常量必须判红', label_expr(mutated_const) != "", False)
    # 反向对照 2：摘掉中文那份的 image 键 ⇒ 真身要露成 MISSING，不许静默回落成别的词
    dicts_missing = [dict((k, v) for k, v in dicts[0].items() if k != "presented.download.image"), dicts[1]]
    tbl_missing = run_label_table(expr, dicts_missing) if expr else {}
    check('P14c 变异对照 键丢了要看得见（MISSING，不是悄悄回落）',
          tbl_missing.get('0|image'), "MISSING:presented.download.image")
    # 类型判定只许一处，且必须包 try（官方组件抛错 = 打死整棵插件树）
    kind_seg = official.split("function weeksirFileKind")[1].split("// ==== end weeksir 补丁")[0] \
        if "function weeksirFileKind" in official else ""
    check('P15 类型判定收在 weeksirFileKind 一处、包 try、且用官方 classifyFileType',
          (bool(kind_seg), "try {" in kind_seg, "classifyFileType" in kind_seg), (True, True, True))
    check('P15b 官方文件里没有第二份扩展名表',
          [x for x in ('"jpg"', '"png"', '"mp4"', '"webm"', '"mov"', '"jpeg"', '"webp"') if x in official], [])
    check('P15c 装机版 primitives 确实导出 classifyFileType（内核升级摘了要在这里红，不是运行时才崩）',
          "classifyFileType" in primitives.split("export {")[-1], True)
    # 选词与图标同源：官方那张 EXTENSION_TYPES 必须把每个可下载扩展名归到预期桶
    ext_types = ext_type_table(primitives)
    want_bucket = {"mp4": "video", "mov": "video", "webm": "video",
                   "png": "image", "jpg": "image", "jpeg": "image", "webp": "image"}
    check('P16 官方类型表把每个可下载扩展名归到预期桶（图标与文案同一个判据）',
          sorted(e for e in client_ext if ext_types.get(e) != want_bucket.get(e)), [])

    # ---------- S 组：我们插件侧形状 ----------
    check('S1 谁都不再抢 turnTail 槽（表单 2026-10-09 整块下线 ⇒ 官方交付卡永不被顶掉）',
          client.count('slots.inject("conversation.chat.turnTail"'), 0)
    check('S1b 表单那块是【整块摘的】不是留着不调用（标记串在插件里已不存在）',
          "weeksir-skill-form" in client, False)
    print('      ℹ️ 原"已知冲突"（表单也占这个 chain 槽 ⇒ 顶掉整张官方卡）已随 10-09 表单下线消解：'
          '现在这个槽上我们零注入，S1 就是把"消解"这件事钉住的——谁再把东西挂回 turnTail，S1 立刻红。')
    check('S2 旧自绘组件已退役', "makeTurnTailBody" in client or "mountTurnTailRecs" in client, False)
    check('S3 不再藏官方行（官方 UI 归官方）', "data-weeksir-turntail" in client, False)
    for hook in ("__weeksirDownloadUrl", "__weeksirDownload", "__weeksirTurnRecs", "__weeksirPickRec"):
        check('S4 插件注册 %s' % hook, ("window.%s =" % hook) in client, True)
    check('S5 注册函数在 apply() 里被调用', "registerWeeksirCardHooks(ctx);" in client, True)
    check('S6 点推荐只填输入框（不建 run / 不挂台 / 不调引擎）',
          "setSkillPrompt" in client.split("window.__weeksirPickRec")[1][:900], True)
    check('S7 判据表只有一份（RULES 在插件、官方那份没有）',
          ("TURN_TAIL_RULES" in client, "TURN_TAIL_RULES" in official), (True, False))
    check('S8 退役前的备份在盘', os.path.exists(CLIENT + ".bak-weeksir-card-hooks"), True)

    print("\n=== 交付卡下载/推荐契约闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
