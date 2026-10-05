#!/usr/bin/env python
"""技能展示 dock 的闸：白名单只放已批准的几条、每条元数据齐、封面真能取到。

为什么钉这么死：dock 以前白名单留空 = 把 skills.list 的【全部 34 条】倒给新客户看
（seedance-horror-suspense / cs-inbox / film-expert-bridge 这种内部名全在屏上）。
这条一旦回退就是产品事故，所以每次改 client.js 都要跑它。默认 0 写入、只读本地 8100。
"""
import io
import json
import os
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CLIENT = r"C:\dsh\plugins\super-clone-ui\lib\client.js"
COVER_DIR = r"C:\dsh\productions\_skill-covers"
APPROVED = ["super-clone", "hypit-pipeline", "omni", "short-drama", "video-dewatermark"]   # 2026-10-04 用户点名这 4 条 + 去水印
# 待封面：故意【不给 cover】（出图要花钱·等逐个确认），卡片走"首字母 + 橙渐变"兜底。
# 分开列是为了区分"还没出图"和"塞了个会 404 的地址"——后者才是事故。
# 2026-10-04：video-dewatermark 的 v3-clean 已出图并挂上 ⇒ 这张表清空；机制留着，下次加新技能不图就先登记在这。
COVER_PENDING = []
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


src = open(CLIENT, encoding="utf-8").read()
meta = re.search(r"SKILL_DOCK_META = \{(.*?)\n    \};", src, re.S)
body = meta.group(1) if meta else ""
keys = re.findall(r'^\s*"?([\w-]+)"?:\s*\{', body, re.M)
allow = re.search(r"SKILL_DOCK_ALLOW = Object\.keys\(SKILL_DOCK_META\)", src)

check("1 找得到 SKILL_DOCK_META 这张表", bool(meta), True)
check("2 白名单 = 元数据表的键（只有一处清单，不留两个入口）", bool(allow), True)
check("3 露出的就是批准的那 4 条，顺序也对", keys, APPROVED)
check("4 没有内部技能混进来（seedance/cs/film/product-video 一律不许）",
      [k for k in keys if re.match(r"^(seedance|cs-|film-|product-video|session-bind|weeksir|skills$)", k)], [])

for sid in APPROVED:
    blk = re.search(r'"?%s"?:\s*\{(.*?)\}' % re.escape(sid), body, re.S)
    seg = blk.group(1) if blk else ""
    check("5 %s 有中文标题（不是内部 id 直接上屏）" % sid, bool(re.search(r'title:\s*"[^"]+"', seg)), True)
    check("6 %s 有 tagline（不是 whenToUse 长文本）" % sid, bool(re.search(r"tagline:\s*\S", seg)), True)
    m = re.search(r'cover:\s*"([^"]+)"', seg)
    url = m.group(1) if m else ""
    if sid in COVER_PENDING:
        check("7 %s 待封面：故意不给 cover URL（宁可走首字母兜底，不许塞会 404 的地址）" % sid, url, "")
        continue
    check("7 %s 封面走 8100 静态口且带 rel=" % sid, url.startswith("http://127.0.0.1:8100/api/film/file?") and "rel=" in url, True)
    rel = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("rel", [""])[0] if url else ""
    check("8 %s 封面文件真在盘上" % sid, bool(rel) and os.path.isfile(os.path.join(COVER_DIR, rel)), True)
    if url:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read()
            check("9 %s 封面取得到（200 且 >50KB）" % sid, r.status == 200 and len(data) > 50 * 1024, True)
        except Exception as e:
            check("9 %s 封面取得到" % sid, "ERR " + type(e).__name__, True)

check("10 卡片按 order 排（不是 skills.list 原序）", ".order || 99" in src, True)
check("11 有版本标记可查'加载了哪版'", "SKILL_DOCK_REV" in src and "dock-2026" in src, True)
check("12 点击只填输入框、不自动发，且保留已打的字", "actions.setDraft(draft + \"/\" + name + \" \")" in src, True)
check("13 皮肤仍用 DSH alias token（没自造色板）", src.count("--dsw-alias-") > 8, True)

# ---- 样式对齐 PRD §8（2026-10-04 补：以前只有"能点"，尺寸/叠层/tab 都没照设计做）----
css = re.search(r"st\.textContent = `(.*?)`;", src, re.S)
style = css.group(1) if css else ""
check("14 卡片圆角 12 / 封面跟着卡宽走 16:9（卡宽不再写死，见 36/37c）",
      (".ws-skill{flex:0 0 auto;border-radius:12px" in style, "aspect-ratio:16/9" in style), (True, True))
check("15 文字【叠在封面底部】，不是另起一块", ".ws-skill .bd{position:absolute;left:0;right:0;bottom:0" in style, True)
check("16 渐变在文字之下（z-index 分层，否则字压不住图）",
      ("z-index:1;pointer-events:none" in style, ".ws-skill .bd{position:absolute;left:0;right:0;bottom:0;z-index:2" in style), (True, True))
check("17 字号照 PRD：标题 14/20、描述 12/17",
      ("font-size:14px" in style and "line-height:20px" in style,
       "font-size:12px" in style and "line-height:17px" in style), (True, True))
check("18 tab 栏有 CSS（.ws-tabs/.ws-tab/.ws-tab.on 三件套）",
      all(k in style for k in (".ws-tabs{", ".ws-tab{", ".ws-tab.on{")), True)
check("19 N 阈值与上限来自常量（不是散在代码里的魔数）",
      ("SKILL_TAB_MIN = 4" in src, "SKILL_RECENT_MAX = 8" in src), (True, True))
check("20 tab 只在够数时上；不够退回两块（不给客户一个孤零零的 tab）",
      "const tabbed = skills.length >= SKILL_TAB_MIN;" in src and "if (tabbed) {" in src, True)
check("21a 默认选中「推荐技能」", 'React.useState("reco")' in src, True)
check("21b 空的「最近使用」不生成 tab", "if (tabbed && recentSkills.length) tabs.push" in src, True)
check("22 点卡片同时写最近使用（tab 有内容才会长出来）", "saveRecent(next); setRecent(next);" in src, True)
# 23-25：这三条是 2026-10-04 那次"dock 从写下那天起就没渲染过"的直接教训
# 钉"成员在不在"，不钉整行字面量 —— 加一项依赖（如 timer）不该让这条闸红（钉现场=假保险）
_inj = src[src.find("const inject = ["):]
_inj = _inj[:_inj.find("];") + 2]
check("23 声明了 remote/skills/sessions 依赖（cordis 的 ctx 是代理，没 inject 一读就抛，且被 catch 静默吞掉）",
      [k in _inj for k in ('"slots"', '"remote"', '"remote.skills"', '"sessions"')], [True] * 4)
check("24 挂载成功/失败各自有日志（不许再出现「什么都没发生」）",
      "已挂进 conversation.composer.dock" in src and "挂载失败（槽不存在或签名变了）" in src, True)
check("25 skills.list 拿到几条、名字是什么，也要打出来（白名单对不上时能自证）",
      "skills.list 返回" in src and "skills.list 调用失败" in src, True)

# ---- 26-30：2026-10-04 第二次"挂了但不显示"的根因（这三条各自都能让整个 dock 静默消失）----
# skills.list 走 typert Remote：回的是信封 {ok, value:{skills}, error}，不是数组。
# 上一版写成 Array.isArray(raw) ? raw : raw.skills —— 恒为空 ⇒ 一条卡都不出。
check("26 拆了 Remote 信封（先判 ok，再读 value.skills）",
      ("res.ok !== true" in src, "res.value && res.value.skills" in src), (True, True))
check("27 没留「把返回值当数组」的旧启发式（回退就等于又是零卡）",
      ("Array.isArray(raw)" in src, "raw.skills || raw.items" in src), (False, False))
check("28 失败信封要打出 code+message（不许再被 catch 吞成「什么都没发生」）",
      "skills/list 失败:" in src and "err && err.code" in src, True)
# 槽位真实 props = extensionZone = { session, input }（dsh-client-ui-conversation:14855），
# 不是 { sessionId } —— 读错键 ⇒ sessionId undefined ⇒ 上游 session/not-found。
check("29 sessionId 从 props.session.sessionId 取（不是 props.sessionId）",
      "props.session && props.session.sessionId" in src, True)
check("30 拿不到 sessionId 时有兜底且明说（不静默变空）",
      ("currentSessionId(ctx)" in src and "ctx.sessions.list.getSnapshot().current" in src,
       "拿不到 sessionId" in src), (True, True))
check("31 调用带 AbortController 且卸载时 abort（照内核 fetchCatalog 的生命周期）",
      ("new AbortController()" in src, "abort.abort()" in src), (True, True))

# ---- 32-33：2026-10-04 用户定的边界「只在首页出卡，对话中不许出现」----
# 判据照内核 dsh-client-ui-conversation:14775/14854 的 hero 规则（字段来自 session 快照 session.js:720-736）。
# 光钉源码字符串不算数，所以把函数整块抠出来跑真值表。
fn = re.search(r"function isHeroSession\(s\) \{.*?\n    \}", src, re.S)
check("32 hero 判定是独立函数（不是散在 JSX 里的三元）", bool(fn), True)
CASES = [
    ("没 session（首页草稿）", "undefined", True),
    ("空会话 blank=true", "{blank:true}", True),
    ("blank 还没算出来（刚打开）", "{}", True),
    ("已有内容 blank=false（对话中）", "{blank:false}", False),
    ("正在生成 running", "{blank:true,running:true}", False),
    ("首轮已提交 promptAttempted", "{blank:true,promptAttempted:true}", False),
    ("有内容 + 在跑", "{blank:false,running:true}", False),
]
js = (fn.group(0) if fn else "") + "\nconst T = [" + \
     ",".join("[%s,%s]" % (e, "true" if w else "false") for _, e, w in CASES) + \
     "];let bad=0;T.forEach(([s,w],i)=>{const g=!!isHeroSession(s);if(g!==w){bad++;console.log('MISMATCH#'+i+' got='+g+' want='+w);}});console.log('HERO '+(T.length-bad)+'/'+T.length);"
try:
    import subprocess
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, encoding="utf-8")
    got = (r.stdout or "").strip().splitlines()
    got = got[-1] if got else "ERR " + (r.stderr or "").strip()[:120]
except Exception as e:
    got = "EXC " + type(e).__name__
check("33 isHeroSession 真值表 7 条全对（首页出、对话中撤）", got, "HERO %d/%d" % (len(CASES), len(CASES)))
check("34 非首页时既不发 skills.list 也不渲染（一条都不留）",
      ("if (!hero) { setSkills([]); return; }" in src, "if (!hero || !skills.length) return null;" in src), (True, True))

# ---- 35-37：2026-10-04 用户顶回的间距（"距离靠近图片一些"：tab 那行离封面隔了一整行箭头）----
# ---- 35-38：分页轮播（2026-10-04 用户定："这里露出 4 个，第 5 个通过按钮切换出现"）----
check("35 一屏 4 张、按起始卡位整段平移（不是自由滚动露半张）",
      ("WS_PAGE_SIZE = 4" in src,
       'translateX(" + (-props.offset * (props.cardW + WS_GAP))' in src), (True, True))
check("36 卡宽是【量出容器宽度再算】，不是写死像素（右栏开合/窗口缩放都要 4 张刚好铺满）",
      ("Math.floor((pageW - (WS_PAGE_SIZE - 1) * WS_GAP) / WS_PAGE_SIZE)" in src,
       "new ResizeObserver(read)" in src), (True, True))
check("44 尾页回退一格补齐（5 张分 2 页时第 2 页也要 4 张，不能孤零零 1 张）",
      "const off = Math.min(pg * WS_PAGE_SIZE, Math.max(0, cur.items.length - WS_PAGE_SIZE));" in src, True)
# 45 把那一行原式抠进 node 跑真值表：钉的是"每一页都满 4 张"这条规则，不是钉字符串
_off = re.search(r"const off = (Math\.min\(pg \* WS_PAGE_SIZE, Math\.max\(0, cur\.items\.length - WS_PAGE_SIZE\)\));", src)
_js = ("const WS_PAGE_SIZE = 4;"
       "function off(n, pg){ return (%s); }" % (_off.group(1).replace("cur.items.length", "n") if _off else "0") +
       "let bad = [];"
       "for (let n = 4; n <= 12; n++) { const np = Math.max(1, Math.ceil(n / WS_PAGE_SIZE));"
       "  for (let pg = 0; pg < np; pg++) { const s = off(n, pg);"
       "    if (Math.min(s + WS_PAGE_SIZE, n) - s !== Math.min(WS_PAGE_SIZE, n)) bad.push(n + ':' + pg); } }"
       "console.log('FULL ' + (bad.length === 0 ? 'yes' : 'no[' + bad.join(',') + ']'));")
try:
    import subprocess
    r = subprocess.run(["node", "-e", _js], capture_output=True, text=True, timeout=30, encoding="utf-8")
    got = [(r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else
           "ERR " + " ".join((r.stderr or "").split())[:150]]
except Exception as e:
    got = ["EXC " + type(e).__name__]
check("45 真值表 n=4..12 × 每一页：可见卡数都等于 min(4,n)（尾页不残缺）", got, ["FULL yes"])
check("37 外层裁切 + 不再有自由滚动轨道（半张卡=看着像坏了）",
      (".ws-dock-page{overflow:hidden" in style, "overflow-x:auto" in style), (True, False))
# 间距与宽度几条各有各的来历，别合成一条（2026-10-04 截屏实证过"第 4 张被切"）
check("37b 整块 dock 离输入卡 18px / tab 行离封面 6px / 嵌套 margin-top 归零",
      (".ws-dock{margin:18px 2px 2px" in style, "margin:0 2px 6px" in style,
       ".ws-dock .ws-dock{margin-top:0}" in style), (True, True, True))
check("37c 整块宽=100%、CSS 里零写死卡宽、封面区跟着卡宽走 16:9",
      ("box-sizing:border-box;width:100%" in style,
       "width:calc(228px" in style,
       ".ws-skill{flex:0 0 auto;width:" in style,
       "aspect-ratio:16/9" in style), (True, False, False, True))
check("38 到头禁用箭头 + 有页码指示 + 切 tab 回第一页",
      ("disabled: page <= 0" in src, "disabled: page >= pages - 1" in src,
       "dots(pg, np)" in src, "setTab(t.id); setPage(0);" in src), (True, True, True, True))
# 39-40：待封面不是"少一张图就崩"，要有兜底；清单也别忘了数进 tab 阈值
check("39 没 cover 时兜底渲染首字母（不是空块/破图）",
      'm.cover ? h("img", { src: m.cover' in src and '.slice(0, 1).toUpperCase()' in src, True)
# 41-43：整页自动轮播（2026-10-04 用户定"一排一排滚，10 秒；鼠标悬停就停"）
check("41 整页自动轮播：10 秒一跳、翻到底回第一页",
      ("const WS_AUTO_MS = 10000;" in src,
       "setInterval(() => setPage((p) => (p + 1) % np), WS_AUTO_MS)" in src), (True, True))
check("42 三种情况必须停表：非首页 / 只有一页 / 鼠标悬停（悬停=用户明确要停）",
      ("if (!hero || np <= 1 || paused) return;" in src,
       "onMouseEnter: () => setPaused(true)" in src,
       "onMouseLeave: () => setPaused(false)" in src,
       "Object.assign({ className: \"ws-dock\" }, hover)" in src), (True, True, True, True))
check("43 手动点箭头后重新计时（page 进依赖），且定时器一定被 clearInterval",
      ("[hero, np, paused, page, tab, cardW]" in src, "clearInterval(t)" in src), (True, True))
check("40 白名单条数够上 tab 模式（≥SKILL_TAB_MIN·加人不会把 tab 加没了）",
      len(APPROVED) >= 4, True)

bad = [n for ok, n in out if not ok]
print("\n=== 技能 dock 闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)
