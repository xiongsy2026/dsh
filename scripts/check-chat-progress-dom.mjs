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
  }
  // 真浏览器里 textContent="" 会连带清掉子节点 —— shim 必须照做，否则断言读到的是上一轮的旧子节点
  get textContent() { return this._text; }
  set textContent(v) { this._text = v; if (v === "") this.kids = []; }
  get className() { return this._cls; }
  set className(v) { this._cls = v; }
  get classList() { const s = this; return { contains: c => (" " + s._cls + " ").includes(" " + c + " ") }; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  appendChild(c) { c.parentNode = this; this.kids.push(c); return c; }
  insertBefore(c, ref) {
    c.parentNode = this;
    const i = this.kids.indexOf(ref);
    if (i < 0) this.kids.push(c); else this.kids.splice(i, 0, c);
    return c;
  }
  remove() {
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

/* 造一轮：user + 3 条过程（assistant-step/reasoning/未知 kind）+ 一条 turn-error */
function buildTurn(turnId, opts = {}) {
  const flow = new El("div");
  doc.body.appendChild(flow);
  const mk = (kind) => { const e = new El("div"); e.setAttribute("data-chat-flow-kind", kind);
    e.setAttribute("data-chat-turn", turnId); flow.appendChild(e); doc._all.push(e); return e; };
  const user = mk("user");
  const p1 = mk("assistant-step"), p2 = mk("reasoning"), p3 = mk("brand-new-kind");
  const err = mk("turn-error");
  if (opts.official) [p1, p2, p3].forEach(e => e.setAttribute("data-turn-process-hidden", ""));
  return { flow, user, p1, p2, p3, err };
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

console.log(`\n=== 执行中分组·行为真值表：${total} 条，失败 ${fails.length} ===`);
process.exit(fails.length ? 1 : 0);
