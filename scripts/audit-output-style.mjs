#!/usr/bin/env node
/**
 * 说话密度审计（W8 的量具，不是闸）。
 *
 * 为什么先做审计而不是闸：今天的真实密度是"每步一条旁白"（实测一条会话 123 步 / 122 条 assistant/message
 * ≈ 1.0），而全局层 §6 刚定下的目标是 ≤0.3。规则刚落地就挂闸 = 一条永远红的假保险。
 * 所以这里只出数，等密度真的降下来再把阈值变成闸（docs/PRD-chat-progress.md §6）。
 *
 * 账本是【多帧 zstd 拼接】：zlib.zstdDecompressSync 一次只解第一帧 ⇒ 必须按魔数切帧循环解。
 * 跑法：node scripts/audit-output-style.mjs [--limit N]
 */
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const HOME = process.env.DSH_HOME || path.join(process.env.APPDATA || "", "dsh-desktop", "harness");
const SESS = path.join(HOME, "sessions");
const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
const PREVIEW = /(接下来我|下面我|我先|现在我去|接下来将|下一步我会)/;
const TARGET = 0.3;
const limit = (() => { const i = process.argv.indexOf("--limit"); return i > 0 ? Number(process.argv[i + 1]) : 12; })();

function unpack(file) {
  const buf = fs.readFileSync(file);
  const at = [];
  for (let i = -1; (i = buf.indexOf(MAGIC, i + 1)) >= 0;) at.push(i);
  let text = "";
  for (let k = 0; k < at.length; k++) {
    try { text += zlib.zstdDecompressSync(buf.subarray(at[k], k + 1 < at.length ? at[k + 1] : buf.length)).toString("utf8"); }
    catch (e) { /* 尾帧被截断是常态（进程还在写），跳过 */ }
  }
  return text;
}

const rows = [];
if (!fs.existsSync(SESS)) { console.log("找不到会话目录：" + SESS); process.exit(0); }
for (const slug of fs.readdirSync(SESS)) {
  const d0 = path.join(SESS, slug);
  if (!fs.statSync(d0).isDirectory()) continue;
  for (const dir of fs.readdirSync(d0)) {
    const f = path.join(d0, dir, "session.v3.jsonl.zstd");
    if (!fs.existsSync(f) || fs.statSync(f).size < 4000) continue;
    const st = fs.statSync(f);
    const text = unpack(f);
    let steps = 0, msgs = 0, msgsAll = 0, turns = 0, chars = 0, preview = 0, errInText = 0, big = 0;
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      let o; try { o = JSON.parse(line); } catch (e) { continue; }
      if (o.type === "step/start") steps++;
      else if (o.type === "turn/start") turns++;
      else if (o.type === "assistant/message") {
        // 🔴 一个 assistant/message 通常只装 tool-call（每步一条，这是内核的正常节奏，不是"话多"）。
        //    真正的旁白 = content 里的 text 块。上一版按"消息条数"算密度，把 153 步/152 条误读成"每步一条旁白"。
        msgsAll++;
        const m = (o.data && o.data.message) || {};
        const parts = (Array.isArray(m.content) ? m.content : []).filter(b => b && b.type === "text");
        const t = parts.map(b => b.text || "").join(" ").trim();
        if (!t) continue;
        msgs++;
        chars += t.length;
        if (PREVIEW.test(t)) preview++;
        if (/Error:|error:/.test(t)) errInText++;
        if (t.length > 400) big++;
      }
    }
    if (steps === 0) continue;
    rows.push({ slug: slug.slice(0, 14), sid: (dir.match(/session-(\w{8})/) || [])[1] || "?", mtime: st.mtime.toISOString().slice(5, 16),
      turns, steps, msgs, msgsAll, density: +(msgs / steps).toFixed(2), avgChars: msgs ? Math.round(chars / msgs) : 0,
      preview, errInText, big });
  }
}
rows.sort((a, b) => b.mtime.localeCompare(a.mtime));
const show = rows.slice(0, limit);
console.log("会话目录=" + SESS + "  有步数的会话=" + rows.length + "（下面列最近 " + show.length + " 条）\n");
console.log("mtime          会话          轮   步  消息  正文块  密度  均字数  预告  含Error  >400字");
for (const r of show) {
  console.log(`${r.mtime}  ${r.sid}  ${String(r.turns).padStart(2)} ${String(r.steps).padStart(3)} ${String(r.msgsAll).padStart(5)} ${String(r.msgs).padStart(6)} ` +
    `${String(r.density).padStart(5)} ${String(r.avgChars).padStart(6)} ${String(r.preview).padStart(5)} ${String(r.errInText).padStart(11)} ${String(r.big).padStart(7)}`);
}
const dens = rows.map(r => r.density).filter(n => isFinite(n));
const avg = dens.length ? +(dens.reduce((a, b) => a + b, 0) / dens.length).toFixed(2) : 0;
console.log(`\n平均密度 = ${avg}（目标 ≤ ${TARGET}）⇒ ${avg <= TARGET ? "达标，可以把它变成闸" : "未达标：全局层 §6 刚落地，先降密度再挂闸（现在挂=永远红的假保险）"}`);
console.log("口径：密度 = 含正文的 assistant 消息数 ÷ step/start 条数（消息总数另列，多数只装 tool-call）；预告=正文里出现「接下来我/下面我/我先…」；均字数=每条正文平均长度。");
