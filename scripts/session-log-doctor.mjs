// 会话日志体检 / 修复器（只读默认；修复只写到新文件，绝不动原件）
//   node session-log-doctor.mjs check          <file ...>        用内核自己的校验器逐条过
//   node session-log-doctor.mjs scan                            扫全部存量会话，列出加载不出来的
//   node session-log-doctor.mjs repair-id <file> <out> <seq> <id>  给指定事件补空 callId/id/name
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const KERNEL = process.env.DSH_KERNEL_ENTRY
  || "file:///C:/Users/Administrator/AppData/Local/Programs/DSH%20Desktop/resources/app/node_modules/@deepseek-ai/dsh-session/lib/index.js";
const { adoptSessionEvent } = await import(KERNEL);

const SESSIONS_ROOT = process.env.DSH_SESSIONS_ROOT
  || "C:/Users/Administrator/AppData/Roaming/dsh-desktop/harness/sessions";
const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

export function frameOffsets(buf) {
  const offs = [];
  for (let i = buf.indexOf(MAGIC); i !== -1; i = buf.indexOf(MAGIC, i + 4)) offs.push(i);
  return offs;
}

export function decodeFrames(buf) {
  const offs = frameOffsets(buf);
  const chunks = [];
  for (let k = 0; k < offs.length; k++) {
    const end = k + 1 < offs.length ? offs[k + 1] : buf.length;
    chunks.push(zlib.zstdDecompressSync(buf.subarray(offs[k], end)));
  }
  return chunks;
}

/** 内核读路径对物理帧的硬判据：首帧必须正好一行 header；帧头保留位、连续帧都要能被它自己的扫描器吃下。 */
export function structureProblems(buf, chunks) {
  const bad = [];
  if (chunks.length === 0) return ["zero frames"];
  const first = chunks[0].toString("utf8");
  if (!first.endsWith("\n") || first.indexOf("\n") !== first.length - 1) bad.push("first frame is not exactly one header line");
  const offs = frameOffsets(buf);
  for (let k = 0; k < offs.length; k++) {
    const end = k + 1 < offs.length ? offs[k + 1] : buf.length;
    const frame = buf.subarray(offs[k], end);
    if (frame.readUInt32LE(0) !== 0xfd2fb528) bad.push(`frame ${k}: bad magic`);
    if ((frame[4] & 24) !== 0) bad.push(`frame ${k}: reserved header bit (descriptor=0x${frame[4].toString(16)})`);
  }
  return bad;
}

/** 逐条过内核校验器；返回 {rejected, problems}。header(type==="session") 走另一条校验，这里跳过。 */
export function validateLines(lines) {
  const rejected = [];
  let events = 0;
  for (const l of lines) {
    let obj;
    try { obj = JSON.parse(l); } catch (e) { rejected.push({ seq: null, type: "<unparseable>", message: e.message }); continue; }
    if (obj.type === "session") continue;
    events++;
    try { adoptSessionEvent(structuredClone(obj)); }
    catch (e) { rejected.push({ seq: obj.seq, type: obj.type, message: e.message }); }
  }
  return { events, rejected };
}

export function checkFile(file) {
  const buf = fs.readFileSync(file);
  const chunks = decodeFrames(buf);
  const lines = Buffer.concat(chunks).toString("utf8").split("\n").filter((l) => l.length > 0);
  const structure = structureProblems(buf, chunks);
  const { events, rejected } = validateLines(lines);
  return { file, frames: chunks.length, lines: lines.length, structure, events, rejected };
}

/** 重新分帧：保持【每个物理帧装哪几行】和原件一致，只换内容，帧数/行数/seq 序列都不变。 */
export function reframe(chunks, lines) {
  const perFrame = chunks.map((c) => c.toString("utf8").split("\n").filter((l) => l.length > 0).length);
  let cursor = 0;
  const out = [];
  for (const n of perFrame) {
    const slice = lines.slice(cursor, cursor + n);
    cursor += n;
    out.push(zlib.zstdCompressSync(Buffer.from(slice.join("\n") + "\n", "utf8")));
  }
  if (cursor !== lines.length) throw new Error(`reframe lost lines: consumed ${cursor} of ${lines.length}`);
  return Buffer.concat(out);
}

/** 给某一条事件补空 id：只认这一行里的空值，逐处替换并计数；改不动就抛错，不静默。 */
export function fillEmptyIds(lineText, id, name) {
  let hits = 0;
  let out = lineText;
  const swap = (needle, replacement) => {
    const n = out.split(needle).length - 1;
    if (n === 0) return;
    out = out.split(needle).join(replacement);
    hits += n;
  };
  swap('"id":""', `"id":${JSON.stringify(id)}`);
  swap('"callId":""', `"callId":${JSON.stringify(id)}`);
  swap('"toolCallId":""', `"toolCallId":${JSON.stringify(id)}`);
  if (name) swap('"name":""', `"name":${JSON.stringify(name)}`);
  if (hits === 0) throw new Error(`seq 那一行里没有可补的空 id —— 目标选错了，不写`);
  if (out.split("\n").length !== lineText.split("\n").length) throw new Error("补 id 改变了行数");
  return { text: out, hits };
}

function seqOf(lineText) {
  try { const o = JSON.parse(lineText); return typeof o.seq === "number" ? o.seq : null; } catch { return null; }
}

function allSessionFiles(root) {
  const acc = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".jsonl.zstd")) acc.push(p);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return acc;
}

const [, , mode, ...rest] = process.argv;

if (mode === "scan") {
  const files = allSessionFiles(SESSIONS_ROOT);
  const bad = [];
  for (const f of files) {
    let r;
    try { r = checkFile(f); } catch (e) { bad.push({ f, why: `读不出来: ${e.message}` }); continue; }
    if (r.structure.length || r.rejected.length) bad.push({ f, why: r.structure.join(" | ") || r.rejected.map((x) => `seq ${x.seq}: ${x.message}`).join(" | ") });
  }
  console.log(`扫了 ${files.length} 份存量会话，加载不出来的 ${bad.length} 份`);
  bad.forEach((b) => console.log("  ✗ " + b.f.replace(/.*sessions.(.{0,40}).*/, "$1…") + "\n      " + b.why));
  process.exit(bad.length ? 1 : 0);
}

if (mode === "check") {
  for (const f of rest) {
    const r = checkFile(f);
    console.log(`${r.file}\n  frames=${r.frames} lines=${r.lines} events=${r.events} 结构=${r.structure.length ? r.structure.join(" | ") : "OK"} 内核拒收=${r.rejected.length}`);
    r.rejected.slice(0, 5).forEach((x) => console.log(`    ✗ seq ${x.seq} ${x.type}: ${x.message}`));
    if (!r.rejected.length && !r.structure.length) console.log("    ✓ 全部事件都能被内核读回来");
  }
  process.exit(0);
}

if (mode === "repair-id") {
  const [file, out, seqWanted, id, name] = rest;
  const wanted = Number(seqWanted);
  if (!Number.isInteger(wanted) || !id) throw new Error("用法: repair-id <file> <out> <seq> <id> [name]");
  const buf = fs.readFileSync(file);
  const chunks = decodeFrames(buf);
  const lines = Buffer.concat(chunks).toString("utf8").split("\n").filter((l) => l.length > 0);
  // 补 id 必须一次补齐【配对的那几条】：assistant 的 tool-call 块、tool/call、tool/result。
  const targets = [];
  for (const l of lines) {
    const s = seqOf(l);
    if (s === null) continue;
    const o = JSON.parse(l);
    if (o.type === "tool/call" && o.data?.callId === "") targets.push(s);
    if (o.type === "tool/result" && o.data?.message?.source?.callId === "") targets.push(s);
    if (o.type === "assistant/message" && Array.isArray(o.data?.message?.content)
      && o.data.message.content.some((b) => b?.type === "tool-call" && !b.id)) targets.push(s);
  }
  if (!targets.includes(wanted)) throw new Error(`seq ${wanted} 不是空 callId 的那几条：候选 = ${targets.join(",")}`);
  let hits = 0;
  const fixed = lines.map((l) => {
    const s = seqOf(l);
    if (s === null || !targets.includes(s)) return l;
    const r = fillEmptyIds(l, id, name);
    hits += r.hits;
    return r.text;
  });
  if (fixed.length !== lines.length) throw new Error("行数变了");
  const packed = reframe(chunks, fixed);
  fs.writeFileSync(out, packed);
  const again = checkFile(out);
  const before = checkFile(file);
  console.log(`补了 ${targets.length} 条事件（seq ${targets.join(",")}），共 ${hits} 处空 id/name`);
  console.log(`原件 seq ${before.rejected.length ? "内核拒收 " + before.rejected.length + " 条" : "内核拒收 0 条"} → 修复件内核拒收 ${again.rejected.length} 条`);
  console.log(`修复件 frames=${again.frames}(原 ${before.frames}) lines=${again.lines}(原 ${before.lines}) events=${again.events}(原 ${before.events})`);
  again.rejected.slice(0, 5).forEach((x) => console.log(`  还剩 ✗ seq ${x.seq} ${x.type}: ${x.message}`));
  if (again.rejected.length || again.structure.length || again.frames !== before.frames || again.lines !== before.lines || again.events !== before.events) {
    console.log("⇒ 不合格：别拿去替换原件");
    process.exit(1);
  }
  console.log(`⇒ 写好了 ${out}（原件没动）`);
  process.exit(0);
}

console.log("用法: check <file...> | scan | repair-id <file> <out> <seq> <id> [name]");
