#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：装机版 primitives 里【markdown 内联图点开放大】这块补丁的形状（Z 组）。

为什么单独一套（2026-10-10 用户"强制要求：图片在回复里不点就能看见，点一下能放大"）：
  这块补丁打在 @deepseek-ai/dsh-client-ui-primitives 上 —— 那份包被【每个界面】共用，
  它抛错 = Cordis 判整棵插件树失败 = 桌面端崩溃重启循环（whale-girl 2026-10-08 实测）。
  所以这里钉的重点不是"有没有放大"，而是【会不会崩】和【会不会长成第二套样式系统】。

四条判据的形状：
  Z1-Z2 rev 与回退点（rev 从补丁脚本现读，闸里不抄旧串）；
  Z3 崩点：Fragment / jsxs / useState 必须是模块级 import、Modal 必须是模块级函数
         —— 内核升级把 Modal 改名或挪走，这里立刻红，而不是等运行时 ReferenceError；
  Z4 覆盖：装机包里 `function MarkdownImage(` 只许有一处定义（有第二份 = 我只盖了一半，
         另一半的图照样点不开，而这种"看起来修好了"最坑）；
  Z5-Z6 灯箱必须借官方 Modal（headless），且注入的 CSS 只许那两条 weeksir-md-zoom 选择器；
  Z7 变异对照：坏法（摘掉点击 / 换成自绘 div / CSS 越界 / 整块删）必须各红各的那一条。
"""
import io
import json
import os
import re
import sys

ROOT = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\node_modules"
        r"\@deepseek-ai")
TARGET = os.path.join(ROOT, "dsh-client-ui-primitives", "lib", "index.js")
BAK = TARGET + ".bak-weeksir-md-zoom-20261010"
PATCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch-markdown-image-zoom.py")
CSS_ID = "weeksir-md-zoom-css"

out = []


def _safe(s):
    """cp936 控制台上一个 ⇒ 就能把闸崩在半路 —— 崩不等于红，崩了等于没跑。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(_safe(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r 期望 %r" % (got, want))))


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def md_image_body(src):
    """抠出 `function MarkdownImage(` 到下一个顶格 `}` 的函数体（判据要绑在这个组件里，不许全文乱撞）。"""
    i = src.find("function MarkdownImage(")
    if i < 0:
        return ""
    j = src.find("\n}", i)
    return src[i:j] if j > 0 else ""


def zoom_css_text(src):
    m = re.search(r"weeksirZoomTag\.textContent = \"([^\"]*)\"", src)
    return m.group(1) if m else ""


def problems(src):
    """纯函数：这份装机文件离"补丁该有的形状"差哪几样。判据抽出来，坏法才能在同一份判据上跑。"""
    p = []
    body = md_image_body(src)
    if not body:
        return ["没有 MarkdownImage 这个组件（官方结构变了 ⇒ 先读新版再改锚点，别放宽判定）"]
    if "onClick:" not in body or "setWeeksirZoomed(true)" not in body:
        p.append("图没有点开入口（onClick 没了 —— 回车那条不算，它只补键盘）")
    if 'role: "button"' not in body or "tabIndex: 0" not in body:
        p.append("点开没有键盘入口（只剩鼠标能放大 = 审核时 Tab 过不去）")
    if "jsx(Modal," not in body:
        p.append("放大没借官方 Modal（自绘弹层 = 第二套遮罩/焦点/Esc 逻辑）")
    if "headless: true" not in body:
        p.append("官方 Modal 没走 headless（会多出标题栏与关闭按钮的文案）")
    if 'className: "weeksir-md-zoom"' not in body:
        p.append("灯箱没有我们的宽度覆盖（官方 .dialog 是 min(380px,100%)，比内联图还小）")
    if 'jsx("div"' in body or 'jsx("button"' in body:
        p.append("组件里出现自绘 div/button（样式系统开始长第二份）")
    # 🔴 必须带词边界：`css$23.imageAlt` 里也含 "css$23.image" 这串，裸数会把 2 数成 3（我第一版就这么误判过）。
    n_img = len(re.findall(r"css\$23\.image\b", body))
    if n_img != 2:
        p.append("内联图与放大图不是同一个官方类（%d 处，应为 2：缩略 + 放大）" % n_img)
    if "onError:" not in body:
        p.append("加载失败回落 alt 那条官方行为被改掉了")
    if src.count('id = "%s"' % CSS_ID) != 1:
        p.append("宽度覆盖的注入块不是恰好一处（%d 处）" % src.count('id = "%s"' % CSS_ID))
    if "try {" not in src.split(CSS_ID)[0][-400:]:
        p.append("注入块没包 try（document 缺席或 CSP 挡下就该崩整棵树）")
    selectors = re.findall(r"([^{}]+)\{", zoom_css_text(src))
    bad_sel = [s.strip() for s in selectors if not s.strip().startswith(".weeksir-md-zoom")]
    if bad_sel:
        p.append("注入的 CSS 越界改了别人的选择器：%s" % bad_sel)
    if len(selectors) > 2:
        p.append("注入的 CSS 长过两条（现在 %d 条 = 开始变成第二套样式系统）" % len(selectors))
    return p


def main():
    for p in (TARGET, BAK, PATCHER):
        if not os.path.exists(p):
            print(_safe("FAIL 缺文件: %s" % p))
            return 1
    src = read(TARGET)
    bak = read(BAK)
    patcher = read(PATCHER)

    # ---- Z1-Z2 rev 与回退点 ----
    rev_m = re.search(r'^REV = "([^"]+)"', patcher, re.M)
    rev = rev_m.group(1) if rev_m else ""
    check("Z1 补丁脚本里 REV 有且只有一个", (bool(rev), patcher.count('REV = "')), (True, 1))
    check("Z2 装机文件带的 rev 就是补丁脚本那个（闸里没抄旧串）", rev in src, True)
    check("Z2b 原版备份在盘且确实没有补丁痕迹（可回退）",
          [os.path.exists(BAK), "weeksir" in bak], [True, False])
    check("Z2c 正向对照：原版里既没有放大状态也没有宽度覆盖",
          ["weeksirZoomed" in bak, "weeksir-md-zoom" in bak], [False, False])

    # ---- Z3 崩点：用到的名字必须真在模块作用域 ----
    head = src[:src.find("function MarkdownImage(")]
    check("Z3 Fragment/jsx/jsxs 来自 react/jsx-runtime 的模块级 import",
          bool(re.search(r'^import \{ Fragment, jsx, jsxs \} from "react/jsx-runtime";$', head, re.M)), True)
    check("Z3b useState 是模块级 import（不是某个组件里的局部变量）",
          bool(re.search(r'^import \{[^}]*\buseState\b', head, re.M)), True)
    check("Z3c Modal 是【模块级】函数声明（顶格、非嵌套）——内核升级把它挪走/改名要在这里红",
          bool(re.search(r"^function Modal\(", head, re.M)), True)
    check("Z3d Modal 确实有 headless 这个形参（我们依赖它去掉标题栏与关闭按钮文案）",
          "headless = false" in [l for l in head.split("\n") if l.startswith("function Modal(")][0], True)
    check("Z3e css$23 就是 markdown 那份 CSS 模块（图与 alt 回落都靠它）",
          'import css$23 from "./markdown/MarkdownText.module.css"' in head, True)

    # ---- Z4 覆盖对照：全装机包里只能有一处 MarkdownImage 定义 ----
    dupes = []
    for dirpath, _dirs, files in os.walk(ROOT):
        for fn in files:
            if not fn.endswith(".js"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                n = read(path).count("function MarkdownImage(")
            except OSError:
                continue
            if n:
                dupes.append((os.path.relpath(path, ROOT), n))
    check("Z4 全装机包里 MarkdownImage 的定义只有一处（有第二份 = 只盖了一半，另一处点不开）",
          sum(n for _p, n in dupes), 1)
    check("Z4b 那份 markdown 渲染器就是交付路径用的（primitives，不是 conversation/renderer 里另抄的）",
          [d[0].replace("\\", "/") for d in dupes], ["dsh-client-ui-primitives/lib/index.js"])

    # ---- Z5-Z6 形状 ----
    check("Z5 这块补丁该有的形状：问题清单为空", problems(src), [])
    _css = [os.path.join(os.path.dirname(TARGET), "markdown", "MarkdownText.module.css"),
            os.path.join(os.path.dirname(TARGET), "Modal.module.css")]
    check("Z6 官方 CSS 文件没被我们改过（宽度覆盖走注入 ⇒ 回退只需还原一个 JS）",
          [os.path.isfile(f) and "weeksir" in read(f) for f in _css], [False, False])
    check("Z6b 宽度覆盖只有两条选择器、且都在 .weeksir-md-zoom 下",
          [s.strip().startswith(".weeksir-md-zoom") for s in re.findall(r"([^{}]+)\{", zoom_css_text(src))],
          [True, True])

    # ---- Z7 变异对照：每种坏法必须红在它自己那一条 ----
    body = md_image_body(src)
    mut_no_click = src.replace("\t\t\tonClick: () => {\n\t\t\t\tsetWeeksirZoomed(true);\n\t\t\t},\n", "", 1)
    check("Z7a 变异 摘掉 onClick ⇒ 必须红（且锚点真的命中了，不是空跑）",
          [mut_no_click != src, any("点开入口" in x for x in problems(mut_no_click))], [True, True])
    mut_self_drawn = src.replace('jsx(Modal, {\n\t\t\topen: weeksirZoomed',
                                 'jsx("div", {\n\t\t\topen: weeksirZoomed', 1)
    check("Z7b 变异 换成自绘 div ⇒ 必须红（官方 Modal 是唯一弹层来源）",
          [mut_self_drawn != src, any("官方 Modal" in x or "自绘" in x for x in problems(mut_self_drawn))],
          [True, True])
    mut_css_greedy = src.replace(zoom_css_text(src), zoom_css_text(src) + ".dsh-anything{color:red}", 1)
    check("Z7c 变异 CSS 里越界改别人的选择器 ⇒ 必须红",
          [mut_css_greedy != src, any("越界" in x for x in problems(mut_css_greedy))], [True, True])
    mut_dropped = src.replace(body, body.replace('className: "weeksir-md-zoom"', "", 1), 1)
    check("Z7d 变异 摘掉宽度覆盖 ⇒ 必须红（不摘的话灯箱比内联图还小，等于没放大）",
          [mut_dropped != src, any("宽度覆盖" in x for x in problems(mut_dropped))], [True, True])
    check("Z7e 判据自己不许是摆设：整块删掉必须报出问题（不是空清单）",
          bool(problems(src.replace(body, "", 1))), True)

    print("\n=== 内联图【点开放大】补丁闸：%d 条，失败 %d ===" % (len(out), sum(1 for ok, _ in out if not ok)))
    for _ok, name in out:
        if not _ok:
            print("  FAIL:", _safe(name))
    return 1 if any(not ok for ok, _ in out) else 0


if __name__ == "__main__":
    sys.exit(main())
