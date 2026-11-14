#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给官方 markdown 内联图加【点开放大】（copy-on-write 补丁，装机的 primitives 包）。

为什么要它（2026-10-10 用户定"强制要求：图片在 agent 回复里不点就能看见，点一下能放大"）：
  `dsh-client-ui-primitives/lib/index.js:7371 MarkdownImage` 只画一个裸 <img>——
  没有 onClick、没有灯箱，全装机包里也搜不到任何 lightbox（IconFullscreenOutline16 零引用）。
  内联显示本身是通的（markdown/MarkdownText.module.css:289 `.image` 只有 max-width:100%，没有 max-height
  ⇒ 图按栏宽铺满，看得见画面），缺的只有"点开看大图"这一层。

分工（🔴 不自绘遮罩、不加第二套弹层）：
  官方 Modal  = portal / 点遮罩关 / Esc 关 / 圆角与底色，全现成；
  weeksir     = 两样：① 一条宽度覆盖（官方 Modal.module.css:28 `.dialog` 是 width:min(380px,100%)，
                          拿它当灯箱会比对话里的内联图【还小】）② img 上的点击/回车开 Modal。
  覆盖用注入 <style> + weeksir- 前缀类，不去改官方 .css：官方那份文件是内核升级要覆盖回来的，
  而且改了就没法用"删掉我们的块"整体回退。

🔴 整块包 try：这份包被每个界面共用，抛错 = Cordis 判整棵插件树失败 = 桌面端崩溃重启循环
   （whale-girl 2026-10-08 的教训）。任何一步失败 ⇒ 图照常显示，只是点不开，不骗人也不崩。

前提：目标必须是【未打过的原版】。检测到 weeksir-md-zoom 标记就拒绝落盘（不留半补丁状态）。
生效：renderer 侧包文件，热重载盖不住 ⇒ Ctrl+R 重载窗口。
"""
import io
import os
import shutil
import sys

# 🔴 cp936 控制台上 print("… ⇒ …") 会把脚本自己崩掉：盘已经写成功，退出码却报失败（今天第三次撞同一颗雷）。
#    只换 errors，不换 encoding。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

TARGET = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\node_modules"
          r"\@deepseek-ai\dsh-client-ui-primitives\lib\index.js")
PRISTINE_BAK = TARGET + ".bak-weeksir-md-zoom-20261010"
REV = "weeksir-md-zoom-20261010a"

# 锚点 = 原版 MarkdownImage 整块（模块级、1 个 tab 缩进）。整块替换，不做行插入：
# 这一版要改的是"返回一个元素"→"返回两个元素"，插行会把结构改歪。
ORIGINAL = """function MarkdownImage({ src, alt, destination }) {
\tconst [failed, setFailed] = useState(false);
\tif (failed) return jsx("span", {
\t\tclassName: css$23.imageAlt,
\t\tchildren: alt || destination
\t});
\treturn jsx("img", {
\t\tclassName: css$23.image,
\t\tsrc,
\t\talt,
\t\tonError: () => {
\t\t\tsetFailed(true);
\t\t},
\t\tloading: "lazy",
\t\tdecoding: "async",
\t\treferrerPolicy: "no-referrer"
\t});
}
"""

PATCHED = """// ==== weeksir 补丁 %s：markdown 内联图【点开放大】（壳用官方 Modal，这里只有点击与宽度覆盖）====
// 回退：还原同目录 index.js%s（原版），或删掉本块 + MarkdownImage 里的 weeksir 标记。
// 🔴 注入失败/Modal 缺席 ⇒ 图照常显示、只是点不开 ⇒ 绝不让这份被全界面共用的包抛错。
try {
\tif (typeof document !== "undefined" && document.getElementById("weeksir-md-zoom-css") === null) {
\t\tconst weeksirZoomTag = document.createElement("style");
\t\tweeksirZoomTag.id = "weeksir-md-zoom-css";
\t\tweeksirZoomTag.textContent = ".weeksir-md-zoom{width:min(1100px,92vw)}.weeksir-md-zoom img{max-height:86vh;width:auto;margin:0 auto}";
\t\tdocument.head.appendChild(weeksirZoomTag);
\t}
} catch (e) {}
/** Failed loads retain the authored alt or destination; a new source remounts the image. */
function MarkdownImage({ src, alt, destination }) {
\tconst [failed, setFailed] = useState(false);
\tconst [weeksirZoomed, setWeeksirZoomed] = useState(false);
\tif (failed) return jsx("span", {
\t\tclassName: css$23.imageAlt,
\t\tchildren: alt || destination
\t});
\treturn jsxs(Fragment, { children: [
\t\tjsx("img", {
\t\t\tclassName: css$23.image,
\t\t\tsrc,
\t\t\talt,
\t\t\tonError: () => {
\t\t\t\tsetFailed(true);
\t\t\t},
\t\t\tloading: "lazy",
\t\t\tdecoding: "async",
\t\t\treferrerPolicy: "no-referrer",
\t\t\trole: "button",
\t\t\ttabIndex: 0,
\t\t\ttitle: alt || destination,
\t\t\tonClick: () => {
\t\t\t\tsetWeeksirZoomed(true);
\t\t\t},
\t\t\tonKeyDown: (e) => {
\t\t\t\tif (e.key === "Enter") setWeeksirZoomed(true);
\t\t\t}
\t\t}),
\t\tjsx(Modal, {
\t\t\topen: weeksirZoomed,
\t\t\tonClose: () => {
\t\t\t\tsetWeeksirZoomed(false);
\t\t\t},
\t\t\ttitle: alt || destination,
\t\t\theadless: true,
\t\t\tclassName: "weeksir-md-zoom",
\t\t\tchildren: jsx("img", {
\t\t\t\tclassName: css$23.image,
\t\t\t\tsrc,
\t\t\t\talt,
\t\t\t\tdecoding: "async"
\t\t\t})
\t\t})
\t] });
}
""" % (REV, ".bak-weeksir-md-zoom-20261010")

DOC_COMMENT = "/** Failed loads retain the authored alt or destination; a new source remounts the image. */\n"


def main():
    raw = io.open(TARGET, "rb").read()
    crlf = raw.count(b"\r\n") > 0 and raw.count(b"\r\n") == raw.count(b"\n")
    src = raw.decode("utf-8").replace("\r\n", "\n")
    if REV in src:
        print("FAIL 目标已带 %s 标记 ⇒ 拒绝半补丁状态。" % REV)
        print("     重打请先还原：cp '%s' '%s'" % (os.path.basename(PRISTINE_BAK), os.path.basename(TARGET)))
        return 1
    if src.count(ORIGINAL) != 1:
        # 命中 0 次 = 官方这版结构变了（升级过）；>1 次 = 锚点不唯一，插两遍就是自伤。
        print("FAIL 原版 MarkdownImage 整块命中 %d 处（要求恰好 1 处），未落盘。" % src.count(ORIGINAL))
        print("     命中 0 处说明官方渲染器结构变了 ⇒ 先读新版实现再改锚点，别放宽判定。")
        return 1
    if not os.path.exists(PRISTINE_BAK):
        shutil.copy2(TARGET, PRISTINE_BAK)
        print("已存原版备份:", os.path.basename(PRISTINE_BAK))
    out = src.replace(ORIGINAL, PATCHED)
    # 正向对照：锚点里含那段官方 doc 注释，替换后它必须还在（丢了 = 我把注释一起吃掉了）。
    if out.count(DOC_COMMENT) < src.count(DOC_COMMENT):
        print("FAIL 替换吃掉了官方注释（%d → %d），未落盘。" % (src.count(DOC_COMMENT), out.count(DOC_COMMENT)))
        return 1
    if out == src:
        print("FAIL 没有任何改动")
        return 1
    tmp = TARGET + ".weeksir-tmp"
    with io.open(tmp, "wb") as f:
        f.write((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
    os.replace(tmp, TARGET)
    print("OK  %d → %d 字节（CRLF=%s）" % (len(src), len(out), crlf))
    print("\n=== markdown 内联图【点开放大】%s 已落盘 ⇒ 生效要 Ctrl+R 重载窗口 ===" % REV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
