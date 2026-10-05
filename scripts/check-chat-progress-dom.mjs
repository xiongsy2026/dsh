#!/usr/bin/env node
/**
 * 执行中分组的【行为】真值表（假 DOM，整块抠源码来跑）。
 *
 * 为什么要有这个：check-chat-progress.py 钉的是"源码形状"（只用 data-*、只插一个节点…），
 * 但折叠这件事真正的对错在运行时 —— 收错东西 / 把用户消息收掉 / 双头 / 官方折过还抢，
 * 都得实际跑一遍才看得见。而且它不依赖 DSH 进程活着（闸的红必须有意义）。
 * 跑法：node scripts/check-chat-progress-dom.mjs
 */
import { readFileSync } from "node:fs";

const SRC = readFileSync("C:/dsh/plugins/super-clone-ui/lib/client.js", "utf8");
const a = SRC.indexOf("const WS_TURN_REV");
const b = SRC.indexOf("function apply(ctx)");
if (a < 0 || b < 0) { console.log("FAIL 抠不到分组段（源码结构变了，先改闸再改码）"); process.exit(1); }

/* ── 极简假 DOM：只实现分组逻辑真正用到的那几样 ── */
class El {
  constructor(tag) {
    this.tag = tag; this.attrs = {}; this.kids = []; this.parentNode = null; this._cls = ""; this._text = "";
    this._vhidden = false;   // 模拟虚拟列表用 CSS 把视口外的行藏掉（没有 hidden 属性，但 offsetParent=null）
  }
  // 真 DOM 里 hidden 是属性反射出来的布尔 —— shim 要照做，否则测不到那条判据
  get hidden() { return this.hasAttribute("hidden"); }
  // 假 DOM 里"看得见"= 既没 hidden、也没被虚拟列表藏、也没被我们自己的 [data-ws-fold]{display:none} 藏
  // （最后那条是故意仿真的：不仿就测不到 2026-10-05 那个"折完又觉得自己没得折"的自激抖动）
  get offsetParent() {
    if (this.hidden || this._vhidden || this.attrs["data-ws-fold"] === "1") return null;
    return this.parentNode ? this.parentNode.offsetParent : this;
  }
  // 真浏览器里 textContent="" 会连带清掉子节点 —— shim 必须照做，否则断言读到的是上一轮的旧子节点
  get textContent() { return this._text; }
  set textContent(v) { this._text = v; if (v === "") this.kids = []; bump(); }
  get className() { return this._cls; }
  set className(v) { this._cls = v; }
  get classList() { const s = this; return { contains: c => (" " + s._cls + " ").includes(" " + c + " ") }; }
  setAttribute(k, v) { bump(); this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { bump(); delete this.attrs[k]; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  appendChild(c) { bump(); c.parentNode = this; this.kids.push(c); return c; }
  insertBefore(c, ref) {
    bump();
    c.parentNode = this;
    const i = this.kids.indexOf(ref);
    if (i < 0) this.kids.push(c); else this.kids.splice(i, 0, c);
    return c;
  }
  remove() {
    bump();
    const p = this.parentNode; if (!p) return;
    const i = p.kids.indexOf(this); if (i >= 0) p.kids.splice(i, 1); this.parentNode = null;
  }
  addEventListener(ev, fn) { (this._ev = this._ev || {})[ev] = fn; }
  click() { if (this._ev && this._ev.click) this._ev.click(); }
  get previousElementSibling() {
    const p = this.parentNode; if (!p) return null;
    const i = p.kids.indexOf(this); return i > 0 ? p.kids[i - 1] : null;
  }
}
const styles = {};
// 🔴 数"这一跳到底往 DOM 写了几个字节"：稳态 tick 必须写 0 条，否则我们自己点着 MutationObserver
//    ⇒ 高度来回变 = 用户点名的"运行中上下闪缩"（2026-10-05）。
globalThis.WRITES = 0;
const bump = () => { globalThis.WRITES++; };
const doc = {
  body: new El("body"),
  getElementById: id => styles[id] || null,
  createElement: t => { const e = new El(t); if (t === "style") { Object.defineProperty(e, "id", { get() { return this._id; }, set(v) { this._id = v; styles[v] = this; } }); } return e; },
  _all: [],
  querySelectorAll(sel) {
    // 只支持本逻辑用到的那一条选择器
    if (sel !== "[data-chat-flow-kind][data-chat-turn]") throw new Error("假 DOM 没实现的选择器: " + sel);
    return this._all.filter(e => e.hasAttribute("data-chat-flow-kind") && e.hasAttribute("data-chat-turn"));
  },
};
globalThis.document = doc;
globalThis.window = { __ModuleLoader__: { load: () => {} } };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
const timers = [];
globalThis.setInterval = fn => { timers.push(fn); return 1; };
globalThis.setTimeout = (fn) => 1;

const { turnFoldTick, mountTurnFold } = new Function(
  "document", "window", "MutationObserver", "setInterval", "setTimeout",
  SRC.slice(a, b) + "\nreturn { turnFoldTick, turnFoldTick2: turnRelease, mountTurnFold };"
)(doc, globalThis.window, globalThis.MutationObserver, globalThis.setInterval, globalThis.setTimeout);
const tick = turnFoldTick;

const fails = [];
let total = 0;
const check = (name, got, want) => {
  total++;
  const j = v => JSON.stringify(v);
  const ok = j(got) === j(want);
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : ` => ${j(got)} 期望 ${j(want)}`));
  if (!ok) fails.push(name);
};

/* 造一轮：user + 官方那条 turn-process 控制行 + 3 条过程（assistant-step/reasoning/未知 kind）+ 一条 turn-error。
   official:true  ⇒ 控制行【活着】= 内核这轮自己折了 ⇒ 我们让位；
   official:false ⇒ 控制行挂了 data-turn-process-hidden（controllerInactive）= 内核没折 ⇒ 归我们折。
   —— 2026-10-05 线上就是这个判据反了，每一轮都被当成"官方已折"，一条都不收。 */
function buildTurn(turnId, opts = {}) {
  const flow = new El("div");
  doc.body.appendChild(flow);
  const mk = (kind) => { const e = new El("div"); e.setAttribute("data-chat-flow-kind", kind);
    e.setAttribute("data-chat-turn", turnId); flow.appendChild(e); doc._all.push(e); return e; };
  const user = mk("user");
  const tp = mk("turn-process");
  if (!opts.official) tp.setAttribute("data-turn-process-hidden", "");
  const p1 = mk("assistant-step"), p2 = mk("reasoning"), p3 = mk("brand-new-kind");
  const err = mk("turn-error");
  if (opts.answer) p1.setAttribute("data-turn-process-answer", "");
  return { flow, user, tp, p1, p2, p3, err };
}
const folded = e => e.getAttribute("data-ws-fold") === "1";
const heads = () => doc.body.kids.concat(...doc.body.kids.map(k => k.kids || [])).filter(e => e.classList.contains("ws-turn-head"));

// 1) 两轮都在 ⇒ 只处理最后一轮
const t1 = buildTurn("turn-1");
tick();
const t2 = buildTurn("turn-2");
tick();
check("1 最后一轮的过程条目被收（3 条里收 2 条）", [folded(t2.p1), folded(t2.p2)], [true, true]);
check("2 最后一条【永远露着】当当前动作（未知 kind 也算过程）", folded(t2.p3), false);
check("3 用户消息与报错永远不收", [folded(t2.user), folded(t2.err)], [false, false]);
check("4 上一轮不抢（官方折它的，我们不动）", [folded(t1.p1), folded(t1.p2)], [false, false]);

// 5) 组头存在且计数对
const h2 = t2.flow.kids.find(e => e.classList && e.classList.contains("ws-turn-head"));
check("5 组头插在组首（不是塞进条目里）", !!h2 && t2.flow.kids[0] === h2, true);
check("6 组头文案含「执行中 · 3 步 · Ns」", /^执行中 · 3 步 · \d+s$/.test(h2.kids[1].textContent), true);
check("7 只有一个头（重复 tick 不叠第二个）", tick() || t2.flow.kids.filter(e => e.classList.contains("ws-turn-head")).length, 1);

// 8) 点开 ⇒ 全露 + 文案变「收起」
h2.click();
check("8 点一下展开全部", [folded(t2.p1), folded(t2.p2), folded(t2.p3)], [false, false, false]);
check("9 展开态提示变「收起」", h2.kids[2].textContent, "收起");
h2.click();
check("10 再点收回（且当前动作仍露着）", [folded(t2.p1), folded(t2.p3)], [true, false]);

// 11) 官方折过的轮：我们立刻让位，撤自己的头
const t3 = buildTurn("turn-3", { official: true });
tick();
check("11 官方已折的组：不打标记", [folded(t3.p1), folded(t3.p2)], [false, false]);
check("12 官方已折的组：不留我们的头", t3.flow.kids.some(e => e.classList.contains("ws-turn-head")), false);

// 13) 只有一条过程 ⇒ 不折（没得折，别给个空组头）
doc._all = []; doc.body.kids = [];
const t4 = buildTurn("turn-4");
[t4.p2, t4.p3].forEach(e => { e.parentNode.remove(); doc._all.splice(doc._all.indexOf(e), 1); });
tick();
check("13 过程条目 <2 条 ⇒ 不折也不插头", [folded(t4.p1), t4.flow.kids.some(e => e.classList && e.classList.contains("ws-turn-head"))], [false, false]);

// 14) mountTurnFold 会声明计时（秒数要自己走）
const ctx = { setInterval: fn => { timers.push(fn); return 1; } };
mountTurnFold(ctx);
check("14 mountTurnFold 注册了 1 秒一跳（组头秒数不靠 DOM 变化）", timers.length >= 1, true);
check("15 样式表只插一份（不每帧堆 <style>）", tick() || Object.keys(styles).length, 1);

// ── 16/17/18：这次线上抓到的三个真问题（假 DOM 复现，不靠肉眼） ──
// 16) 内核折叠行失效（挂了 data-turn-process-hidden）⇒ 这轮【必须】归我们折
doc._all = []; doc.body.kids = [];
const t5 = buildTurn("turn-5");            // 默认 official:false = 内核没折
tick();
check("16 内核折叠行失效的轮：我们接管（不是让位）", [folded(t5.p1), folded(t5.p2), folded(t5.p3)], [true, true, false]);

// 17) 正文答案不收：收完客户就看不到结果了
doc._all = []; doc.body.kids = [];
const t6 = buildTurn("turn-6", { answer: true });
tick();
check("17 带 data-turn-process-answer 的那条永远不收", folded(t6.p1), false);
check("18 答案之外照收（组头计数不含答案）", [folded(t6.p2), /^执行中 · 2 步 · \d+s$/.test(t6.flow.kids.find(e => e.classList.contains("ws-turn-head")).kids[1].textContent)], [true, true]);

// 19) 虚拟列表把视口外的行用 CSS 藏了 ⇒ 只数看得见的，别为"其实没在屏上"的两条插空组头
doc._all = []; doc.body.kids = [];
const t8 = buildTurn("turn-8");
t8.p2._vhidden = true;                       // 3 条过程里 2 条在视口外
t8.p3._vhidden = true;
tick();
check("19 只剩 1 条看得见 ⇒ 不折、不插头", [folded(t8.p1), t8.flow.kids.some(e => e.classList && e.classList.contains("ws-turn-head"))], [false, false]);

// 20) 防自激：折叠本身会让条目"看不见"，判据不能再拿实时可见数当依据（2026-10-05 线上就是这里 250ms 抖一次）
//     🔴 必须【逐跳采样】：只看终态会被奇偶碰巧蒙对（旧版抖 4 次最后仍是"折着"，照样绿）
doc._all = []; doc.body.kids = [];
const t9 = buildTurn("turn-9");
const perTick = [];
for (let i = 0; i < 5; i++) { tick(); perTick.push([folded(t9.p1), folded(t9.p2), t9.flow.kids.filter(e => e.classList && e.classList.contains("ws-turn-head")).length]); }
check("20 连跳五次逐跳不抖：每跳都保持折着 + 恰好一个头",
      perTick.every(s => s[0] === true && s[1] === true && s[2] === 1), true);

// 21) 稳态那一跳必须【一个字都不写】：否则我们的写入点着自己的 observer ⇒ 整页高度来回跳（用户点名"上下闪缩"）
//     钉住时钟：秒数走字本身要写 4 次（textContent="" + 3 个 span），那是每秒一次、不改高度，不能算进这条。
const realNow = Date.now;
const t0 = realNow();
Date.now = () => t0;
tick();                                  // 暖一跳：把"时钟被钉住导致文案变了"这次重画吸收掉
globalThis.WRITES = 0;
tick(); tick();
const steadyWrites = globalThis.WRITES;
Date.now = realNow;
check("21 稳态（同一秒内）连跳两次的 DOM 写入数 = 0", steadyWrites, 0);

// 22) 模拟内核重绘：我们的头被抹掉、条目被换成不带标记的新节点 ⇒ 头要回来；且用户点开的展开态不能丢
function kernelRerender(t) {
  t.flow.kids = t.flow.kids.filter(e => !(e.classList && e.classList.contains("ws-turn-head")));
  for (const e of [t.p1, t.p2, t.p3]) delete e.attrs["data-ws-fold"];   // 直接改，别走 removeAttribute（不污染写入计数）
}
kernelRerender(t9);
tick();
check("22 重绘后组头自己回来（同步补，不等 250ms）", t9.flow.kids.filter(e => e.classList && e.classList.contains("ws-turn-head")).length, 1);
const h9 = t9.flow.kids.find(e => e.classList.contains("ws-turn-head"));
h9.click();
check("23 点开=展开全部", [folded(t9.p1), folded(t9.p2), folded(t9.p3)], [false, false, false]);
kernelRerender(t9);
tick();
check("24 展开态跨重绘不丢（选择记在内存里，不记在会被抹掉的头节点上）",
      [folded(t9.p1), folded(t9.p2), folded(t9.p3), (h9.kids[2] || {}).textContent], [false, false, false, "收起"]);
h9.click();
tick();
check("25 再点收回，且当前动作仍露着", [folded(t9.p1), folded(t9.p2), folded(t9.p3)], [true, true, false]);

console.log(`\n=== 执行中分组·行为真值表：${total} 条，失败 ${fails.length} ===`);
process.exit(fails.length ? 1 : 0);
