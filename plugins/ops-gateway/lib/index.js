/**
 * Host half of ops-gateway —— 表单提交 → 叫醒那条会话的 agent（docs/PRD-input-form-single-port.md T13）。
 *
 * 为什么必须在【宿主侧】：客户点【提交这张表】就等于授权开工（2026-10-09 定的口径），但 agent 只在
 * 轮到自己说话时才动 ⇒ 表交了没人看见，链路断在这一格（用户原话"我提交了表单，但是没有往下走啊"）。
 * 开新一轮的钥匙是 `Agent.followup(message)`（内核 dsh-agent-loop/lib/index.js:789 → send(msg,"next-turn",true)），
 * 它只在 host 侧拿得到：客户端插件那侧的 ctx.remote 只暴露 commands/goals/session 之类，没有 agents。
 * 官方先例：dsh-schedule（提醒到点叫同一会话）、dsh-webhook（外部事件叫新会话）、dsh-goal-round-driver。
 *
 * 🔴 三条不许越过的线：
 *   1) 只【叫醒】，不【代发】：followup 送的是固定文案，不碰 composer、不 setDraft、不挂附件（AGENTS.md 铁律）；
 *   2) 唤醒 ≠ 批准：文案里明写花钱那一下仍要人在 /approval 点一次，绝不因为"表交了"就自己开工；
 *   3) apply() 一个字都不许抛出去 —— 插件树里任何 apply 抛错 = Cordis 判整棵失败 = 桌面端崩溃重启循环
 *      （2026-10-08 桌宠那次就是这么把主程序弄瘫 10 分钟的）。所以全部包在 try 里，失败只 warn。
 */
export const name = "ops-gateway";

// 🔴 只 inject【宿主侧真的存在】的服务：2026-10-09 我多写了 "logger"（和 "timer"），
//    Cordis 判 "ops-gateway: pending (waiting for service: logger)" ⇒ 条目不激活
//    ⇒ 整棵插件树失败 ⇒ harness exit 1 ⇒ 桌面端崩溃重启循环。宿主侧官方先例（dsh-schedule/
//    dsh-webhook/dsh-goal-round-driver）都只 inject agents 这类，没有 logger；定时器它们直接用裸 setTimeout。
export const inject = ["agents"];

const WAKE_URL = "http://127.0.0.1:8100/api/skills/form/wake";
const ACK_URL = "http://127.0.0.1:8100/api/skills/form/wake/ack";
const TICK_MS = 5000;                       // 与右栏 presence 同周期：客户交完表最多等 5 秒
const WAKE_REV = "wake-20261012f";          // 排查"跑的是哪版"用（宿主侧没有 rev 就查不到；改这份文件必须一起改这里）
const MAX_PER_TICK = 3;                     // 一轮最多叫 3 条：多会话同时交表时不许把驱动堵死
// 🔴 一次 HTTP 最多等多久。8002/8100 这类本机服务"TCP 收下但不回话"是真的发生过的
//    （2026-10-11 现网：8100 keep-alive 攒到积压打满，端口 LISTENING 却一律拒新连接）。
//    没有这个超时会要命：`run()` 里 `if (running) return` 是"一轮只跑一个"的守卫，
//    一次挂住 = `running` 永远是 true = **这条唤醒线静默停摆到进程重启**，而且不打一行日志。
//    2026-10-12 现网形状：人 09:28:21 点了批准，队列里那条到 09:36 还没人领，
//    而 ops-gateway 最后一行日志停在昨晚 23:53:17 的 `form-wake: fetch failed`。
const FETCH_TIMEOUT_MS = 4000;
const ROUND_WATCHDOG_MS = 30000;            // 兜底：整轮超过 30 秒还没 settle 就强制复位（fetch 之外的 await 也可能挂）
const HEARTBEAT_EVERY = 12;                 // 12 × 5s = 一分钟一条心跳：让"停摆"变成看得见的东西，而不是等客户来问

// ── 收工拦截（agent/turn-stopping）──────────────────────────────────────────────
// 2026-10-12 现网 session-5282eabf：agent 把提交起起来就 turn/end 了，而"后台那条命令跑完"
// 不是表单提交/批准核销/编译跑完这三个事件源之一 ⇒ 没有任何东西会把它叫回来 ⇒ "必须盯到终态"
// 只是一句散文。这一格把它变成机制：回合要结束时问一次 8100"这条会话还有活没跑完吗"，
// 有 ⇒ 把它按回去继续盯。预算（90 秒一次、同一份活最多 6 次）是防止它变成无限烧 token。
const OBLIGATION_URL = "http://127.0.0.1:8100/api/skills/obligation";
const STOP_MIN_MS = 90000;
const STOP_MAX = 6;
const stopState = new Map();                // session -> {key, n, last}

/** 纯判据：这次收工拦不拦。抽成函数（并导出）是为了能被闸整块抠出去跑值表。 */
export function stopDecision(busy, items, prev, now, minMs, maxN) {
  if (!busy || !Array.isArray(items) || !items.length) return { act: "allow", why: "" };
  const key = items.map(i => String(i.kind) + ":" + String(i.who)).sort().join(",");
  const p = prev && prev.key === key ? prev : null;          // 活换了（捞完了/又提交了一条）⇒ 预算重置
  const n = p ? Number(p.n) || 0 : 0;
  if (n >= (maxN || STOP_MAX)) return { act: "allow", why: "budget-used-up" };
  if (p && now - Number(p.last || 0) < (minMs || STOP_MIN_MS)) return { act: "allow", why: "too-soon" };
  return { act: "steer", why: "", key, n: n + 1 };
}

/** 拦回去时对它说的话。🔴 导出只为让闸真跑它，判据不在闸里重写第二份。 */
export function stopText(v) {
  const kinds = ((v && v.items) || []).map(i => i.kind === "compiling"
    ? "编译还在跑（pid " + i.who + "，日志 " + i.where + "）"
    : "已提交、成片未落盘（任务号 " + i.who + "）").join("；");
  return "【还没到终态 · 不许收工】\n盘上事实：" + kinds + "。\n"
    + "1) 🔴 不许再提交一次（重投=双花），也不许自己拼 HTTP 绕过入口。\n"
    + "2) 编译在跑 ⇒ 看那份 compile.log 等它摘牌；成片没到手 ⇒ 跑 "
    + "`python .dsh/skills/super-clone/runner/run.py --stage wait`（只按盘上的号捞，一个提交请求都不发）。\n"
    + "3) 长命令一律 `run_in_background: true` + `job_output` 收：前台命令到 10 分钟会被工具强杀，"
    + "而一条 wan3-prime 生成实测 10–17 分钟 —— 这就是上次断在这儿。\n"
    + "4) 到手照旧：present → deliver_pack → 原样贴 markdown。同一条命令连续 3 次没进展就停下报证据，别无限循环。\n"
    + "（本条由 ops-gateway 在你要收工的那一刻发出，不是客户手打的。）";
}


// 本进程已叫过的 (session|at)。ack 失败/进程重启时它兜住"同一次提交只叫一次"。
const woken = new Set();
const waitNoted = new Set();                // "等着没叫"每个 key 只报一次，别每 5 秒刷屏

/** 叫醒消息。🔴 导出只为让闸能真跑它（同 wakeDecision 的理由：判据抽出来才测得动，别在闸里重写一遍）。 */
export function wakeText(item) {
  const kind = String((item && item.kind) || "form");
  if (kind === "compiled") {
    const skill = String((item && item.skill) || "该技能");
    return "【编译已完成（0 分，没提交）· 该你报价了】\n"
      + "客户交表时服务端就自动点了火（技能：" + skill + "），现在 `--stage compile` 跑完了。\n"
      + (item.ok
        ? "1) 先审编译结果：读该段 `storyboard.json` / `prompt.txt` / `prompt_meta.json`，对着 `frames/` 逐秒核动作与分镜。\n"
        : "1) 🔴 编译【失败】：先读任务目录里的 `compile.log` 找死因，如实报出来，别当成上游问题、也别静默重跑。\n")
      // 🔴 括号是必须的：`+` 比 `?:` 绑得紧，没括号时整条消息只剩第 1 步（2026-10-12 现网就是这么只剩一行，
      //    agent 拿不到"下一步该报价"，自己数出四件事把【补跑编译】当成必做步骤又起了一台）。
      + "2) 🔴 默认【不重跑】：只有分镜/动作标签对不上 `frames/` 才补 --extra-note 重跑同一条 --stage compile"
      + "（还是 0 分）；产物齐、分镜对得上就直接进第 3 步。不许绕开入口手搓脚本。\n"
      + "   重跑必须 `run_in_background: true`：一条编译实测 7–12 分钟，前台命令到 10 分钟会被工具强杀。\n"
      + "3) 认可后：跑 gateway.py approve 建待批条子（段数从盘上算），把 approve 打出的报价正文【原样】贴进消息，"
      + "等人点【批准，开始跑】—— 这一刀仍只有人能点。\n"
      + "4) 人批过之后由你提交 wan3.0、盯到终态、present → deliver_pack → 原样贴 markdown。\n"
      + "（本条由 8100 的编译完成事件触发，不是客户手打的。）";
  }
  if (kind === "compile_died") {
    const skill = String((item && item.skill) || "该技能");
    return "【编译半路死了 · 客户还在等 · 该你出声了】\n"
      + "客户交表时服务端点了火（技能：" + skill + "），那台 `--stage compile`（pid " + item.pid
      + "）进程已经没了，牌子却还挂在任务目录上 ⇒ 它没跑完，也没往队列里报过任何东西。\n"
      + "1) 先读 " + item.log + " 找死因，把死因如实说给人（上游 429 / 额度 / 编码崩 / 被杀），"
      + "🔴 不许把「表交了」当成「进管线了」汇报。\n"
      + "2) 同一条 --stage compile 重跑一次（还是 0 分、不提交）；入口现在自己挂牌，"
      + "再起第二台会被入口直接拒 ⇒ 别绕开入口手搓 driver。\n"
      + "   🔴 必须 `run_in_background: true` + `job_output` 收：一条编译实测 7–12 分钟，"
      + "前台命令到 10 分钟被工具强杀 —— 现网那两台就是这么死的，别用同一种跑法再死一次。\n"
      + "3) 重跑还是死 ⇒ 同一个死因最多试 3 次，然后停下报证据（compile.log + 账本 error），别死磕。\n"
      + "4) 编译出来才走报价那一刀：approve 建条子 ⇒ 人在批准窗点一次 ⇒ 你提交、盯终态、交付。\n"
      + "（本条由 8100 的「死牌子」判据触发 —— 它读的是盘上事实，不是谁的自述。）";
  }
  if (kind === "submit_died") {
    const skillSd = String((item && item.skill) || "该技能");
    // 🔴 批准即点火的回程那一半：人已经点过一次批准了，那台被服务端起来的提交腿却半路死了 ⇒
    //    没有这一格，盘上零条红、没人被叫醒，最后变成客户来问"我批了怎么没片"（2026-10-15 09:05 现网：
    //    三段全死在 NameError，agent 醒来只看到"没人提交"，于是自己重跑并回头问人"批了吗"）。
    return "【批准之后那台提交腿半路死了 · 客户已经点过一次批准 · 该你出声了】\n"
      + "客户在批准窗点了【批准，开始跑】（技能：" + skillSd + "），服务端在同一次请求里起了提交腿"
      + "（`--stage submit`，pid " + item.pid + "）—— 现在那个进程没了，而盘上【既没有任务号、也没有成片】"
      + "⇒ 那一次批准没换来任何一次提交，人还在等片。\n"
      + "1) 先读 " + item.log + " 找死因，原样说给人（这次现网是代码 NameError，不是上游忙 —— 别归因成排队）。\n"
      + "2) 🔴 不要重新报价、不要重新建条子：批准已经签出、条子还在。先跑 `gateway.py approvals`【只读】"
      + "确认剩余段数，再用同一条 `--stage submit` 补跑；入口自己防撞牌，别绕开它手搓提交脚本。\n"
      + "   必须 `run_in_background: true` + `job_output` 收：一条 wan3-prime 实测 5–17 分钟，前台 10 分钟被强杀。\n"
      + "3) 只要盘上已经出现任务号（哪怕崩在下载之前）⇒ 一律改跑 `--stage wait` 只捞不重投，重投 = 双花。\n"
      + "4) 同一死因最多试 3 次，然后停下报证据（submit.log 尾部 + 账本 error），别死磕。\n"
      + "（本条由 8100 的「死掉的提交牌子」判据触发 —— 读的是盘上事实，不是谁的自述。）";
  }
  if (kind === "approval") {
    const lane = String(item.capability || "?") + (item.purpose ? "/" + item.purpose : "");
    // 🔴 2026-10-14 批准即点火：人在批准窗点那一下，8100 已经自己把提交腿起起来了。
    //    这条唤醒从此只派【盯终态 + 交付】的活 —— 文案必须明说别再自己起/别再 call 一次，
    //    否则 agent 醒来又敲一遍 runner（2026-10-12 那台并发就是这么来的）。
    if (item.kicked_off) {
      return "【批准已生效 · 服务端已替你提交，这一轮你只做交付】\n"
        + "客户刚点了【批准，开始跑】：" + lane + " · " + item.count + " 段 · 上限 "
        + item.credits + " 分 · 条子 " + item.grant_id + "。\n"
        + "1) 🔴 8100 在同一刻已经把提交腿起起来了（pid " + (item.kickoff_pid || "?")
        + "，日志 <任务目录>/submit.log）—— 你【不要再 gateway call 一次】，也不要再敲 "
        + "`run.py --from-form --stage submit`：两台并发会互相覆盖同名产物，"
        + "而第二遍的段数已经被这张条子花完 ⇒ 只会拿到 approval_required。\n"
        + "2) 你要做的：读 submit.log 盯到终态（长命令一律 "
        + "run_in_background + job_output，前台 10 分钟被工具强杀）→ 出片后 present → "
        + "deliver_pack → 原样贴 markdown；一批里失败几段就只补跑那几段。\n"
        + "   这条腿现在【有没有在跑】只许问 8100 的只读口 GET /api/skills/obligation?session=<会话id> —— "
        + "判据在服务器那一份，插件（和你）都不许自己翻盘上的号文件去算，两份迟早漂。\n"
        + "3) 范围一变（换路由、加段数、超分数、扩片型）⇒ 这张条子花不出去，回来重新报价再批。\n"
        + "（本条由 8100 的批准核销事件触发，不是客户手打的。）";
    }
    const why = item.kickoff_why ? ("\n（服务端没替你点火的原因：" + item.kickoff_why
      + " ⇒ 这一条仍由你发起提交。）") : "";
    return "【批准已生效 · 接着往下跑，不要再问“点了没有”】\n"
      + "客户刚在批准窗/批准页点了【批准，开始跑】：" + lane
      + " · " + item.count + " 段 · 上限 " + item.credits + " 分 · 条子 " + item.grant_id + "。\n"
      + "1) 直接继续跑那条已批的方案 —— 不要再等一句确认，也不要重新报价（这一刀已经批了）。\n"
      + "2) 提交前照旧核验 skill 合同回执；自己盯到终态，出片后 present → deliver_pack → 原样贴 markdown。\n"
      + "3) 🔴 这张条子只覆盖上面写明的范围：换路由、加段数、超分数、扩片型 ⇒ 仍要重新报价并等下一次点击。\n"
      + "（本条由 8100 的批准核销事件触发，不是客户手打的。）" + why;
  }
  const skill = String((item && item.skill) || "该技能");
  return "【表单已提交 · 请接着往下做】\n"
    + "客户刚在右栏「填参数」提交了这条会话的参数（技能：" + skill + "）。\n"
    + "1) 先读参数：python scripts/intent.py payload   （键名就是引擎入参，不用再问客户）\n"
    + "2) 按该技能 SKILL.md 的流程继续跑，不要再等客户回一句\u201c开始\u201d。\n"
    + "   🔴 但【不要再自己敲 runner 起第二台】：交表那一下 8100 已经自动把编译起跑了（同一条任务起两台会"
    + "把 prompt/分镜/回执互相覆盖，入口现在也会直接拒）。盯 任务目录\\compile.log 等它跑完即可。\n"
    + "3) 🔴 要花钱的那一下仍然先报价，并等人在 http://127.0.0.1:8100/approval 点一次批准 —— 提交表单不等于批准。\n"
    + "（本条由 8100 的表单提交事件触发，不是客户手打的。）";
}

async function fetchJson(url, body) {
  // 🔴 必须带超时：挂住的 fetch 会让"一轮只跑一个"的守卫永久锁死整条唤醒线（见上面 FETCH_TIMEOUT_MS 的注释）。
  // AbortSignal.timeout 在 Node 17.3+ 有；宿主跑不了的话退化成"没有超时"，所以闸 W 组钉它存在。
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "text/plain;charset=UTF-8" }, body: JSON.stringify(body) }
    : { method: "GET" };
  try {
    opt.signal = AbortSignal.timeout(FETCH_TIMEOUT_MS);
  } catch (e) { /* 老运行时没这个 API：宁可没超时也别让取数抛在 signal 赋值上 */ }
  const r = await fetch(url, opt);
  if (!r.ok) return null;
  return await r.json().catch(() => null);
}

/** 纯判据：这一条该不该叫、叫完要不要销账。抽成函数是为了能被闸整块抠出去跑值表。 */
export function wakeDecision(item, agent, alreadyWoken) {
  if (!item || !item.session) return { act: "skip", why: "no-session" };
  if (alreadyWoken) return { act: "skip", why: "woken-in-process" };   // 同一次提交不叫第二遍
  if (!agent) return { act: "wait", why: "agent-not-live" };           // 会话没开着 ⇒ 不叫、也不销账
  if (agent.status !== "idle") return { act: "wait", why: "agent-busy" };  // 正在跑就别插话
  return { act: "wake", why: "" };
}

/** 留痕一律走 console：宿主日志桥会把它写进 harness.log（实测 [stderr] [ops-gateway] … 就是这么来的）。
 *  🔴 不去读日志服务 —— 那同样是"没 inject 一读就抛"的守卫属性，记日志不许变成故障点。 */
function note(level, msg) {
  try {
    (level === "info" ? console.log : console.warn).call(console, "[ops-gateway] " + msg);
  } catch (e) { /* 日志失败不许影响唤醒 */ }
}

async function tick(ctx) {
  const q = await fetchJson(WAKE_URL);
  const pending = (q && Array.isArray(q.pending)) ? q.pending : [];
  if (!pending.length) return 0;
  let done = 0;
  for (const item of pending.slice(0, MAX_PER_TICK)) {
    // 去重键与【服务端那格的键】同构：表单按会话、批准按条子。
    // 🔴 只用 session|at 的话，同一会话连着批两条（先批 1 段、补跑再批 1 段）会被当成同一次提交，第二条不叫。
    const key = (item.kind || "form") + "|" + (item.grant_id || item.session) + "|" + item.at;
    let agent = null;
    try {
      agent = ctx.agents && ctx.agents.get ? ctx.agents.get(item.session) : null;
    } catch (e) {
      agent = null;   // 读不到就当没活着：宁可这一轮不叫，也不许把异常抛出去
    }
    const d = wakeDecision(item, agent, woken.has(key));
    if (d.act !== "wake") {
      // 🔴 wait 必须出声：会话没开着/回合正忙时既不叫也不销账，过去这里一片安静，排查只能靠猜（2026-10-10 花了 40 分钟）
      if (d.act === "wait" && !waitNoted.has(key)) {
        waitNoted.add(key);
        note("warn", "form-wake 等着没叫: kind=" + (item.kind || "form")
          + " who=" + (item.grant_id || item.session) + " 原因=" + d.why);
      }
      continue;
    }
    woken.add(key);
    try {
      // 官方同款形状：createUserMessage({content:[{type:"text",text}], source:{kind:"plugin",plugin}})
      const { createUserMessage } = await import("@deepseek-ai/dsh-llm");
      agent.followup(createUserMessage({
        content: [{ type: "text", text: wakeText(item) }],
        source: { kind: "plugin", plugin: "weeksir-form-wake" },
      }));
    } catch (e) {
      woken.delete(key);   // 没叫成功 ⇒ 摘掉标记，下一轮重试（不销账）
      note("warn", "form-wake: followup 失败 " + e.message);
      continue;
    }
    try {
      // 销账按【来源】分流：表单那格按会话+at 配，批准那格按条子 id+at 配。
      // 🔴 不许拿 session 当唯一键：同一会话可以连着批两条（先批 1 段、补跑再批 1 段），
      //    按会话清会把后批那条一起抹掉 = 人明明点了，agent 却永远不被叫。
      const ackBody = item.kind === "approval"
        ? { kind: "approval", grant_id: item.grant_id, at: item.at }
        : (item.kind === "compile_died" || item.kind === "submit_died")
          // 🔴 两张死牌子都按 pid 销账：服务端只删【报上来的那台】的牌子，
          //    万一这一秒又点火起了新的一台，pid 对不上就不删 —— 免得把新那台的留痕摘了。
          //    （服务端那份 ack 判据两格共用，所以这里也必须共用，别各写一份。）
          ? { kind: item.kind, session: item.session, pid: item.pid }
          : { kind: (item.kind || "form"), session: item.session, at: item.at };
      await fetchJson(ACK_URL, ackBody);
      done++;
    } catch (e) {
      // 销账失败也没关系：woken 里已经记着，本进程不会重复叫；重启后顶多再叫一次
      note("warn", "form-wake: ack 失败 " + e.message);
    }
  }
  return done;
}

export function apply(ctx) {
  try {
    note("info", "ops-gateway host " + WAKE_REV);
    // ── 收工拦截：回合要结束时问一次"这条会话还有活吗"，有就把它按回去继续盯 ──────────
    // 🔴 三条安全线：① 读不到会话/8100 没回话/任何异常 ⇒ 一律【放行收工】（这条是拦截器，
    //      它坏了绝不能变成"谁都别想结束回合"）；② 不碰 composer、不代发，steer 送的是固定文案；
    //   ③ 有预算（见 stopDecision），同一份活最多拦 6 次，之后放人写报告。
    try {
      ctx.on("agent/turn-stopping", async ({ agent }) => {
        let sid = "";
        try {
          sid = String((agent && agent.session && agent.session.id) || "").trim();
          if (!sid) return;
          const v = await fetchJson(OBLIGATION_URL + "?session=" + encodeURIComponent(sid));
          const d = stopDecision(v && v.busy, v && v.items, stopState.get(sid), Date.now());
          if (d.act !== "steer") {
            if (!(v && v.busy)) stopState.delete(sid);
            if (d.why === "budget-used-up") {
              note("warn", "收工拦截: " + sid + " 同一份活已拦满 " + STOP_MAX + " 次仍没到终态 ⇒ 放它收工写报告");
              stopState.delete(sid);
            }
            return;
          }
          const { createUserMessage } = await import("@deepseek-ai/dsh-llm");
          agent.steer(createUserMessage({
            content: [{ type: "text", text: stopText(v) }],
            source: { kind: "plugin", plugin: "ops-gateway" }
          }));
          stopState.set(sid, { key: d.key, n: d.n, last: Date.now() });
          note("info", "收工拦截: " + sid + " 盘上还有没跑完的活 ⇒ 按回去继续盯（第 " + d.n + " 次）");
        } catch (e) {
          note("warn", "收工拦截失败（放行收工，绝不卡死回合）: " + (e && e.message));
        }
      });
    } catch (e) {
      // ctx.on 拿不到（宿主没给这个钩子）也不能影响唤醒主链
      note("warn", "收工拦截没挂上（唤醒链照常）: " + (e && e.message));
    }

    let running = false;
    let ticks = 0, hung = 0, lastRound = "never";
    const run = () => {
      if (running) {
        // 上一轮还压着：这本来就是"停摆"的样子，所以要说出来（静默跳过 = 八小时没人知道它死了）
        hung++;
        if (hung === 1 || hung % 60 === 0) {
          note("warn", "form-wake: 上一轮还没回来，跳过本轮（连续 " + hung + " 次，rev " + WAKE_REV + "）");
        }
        return;
      }
      running = true;
      hung = 0;
      ticks++;
      // 🔴 兜底看门狗：fetch 已经带超时，但 tick 里还有 await（followup / ack）—— 任何一处挂住
      //    都会让 `running` 永远为 true。30 秒后强制复位，宁可重复跑一轮，不可永久停摆。
      const watchdog = setTimeout(() => {
        note("warn", "form-wake: 一轮超过 " + ROUND_WATCHDOG_MS + "ms 没回来 ⇒ 强制复位（rev " + WAKE_REV + "）");
        running = false;
      }, ROUND_WATCHDOG_MS);
      Promise.resolve()
        .then(() => tick(ctx))
        .then(n => { lastRound = "ok(叫了 " + n + " 条)"; })
        .catch(e => { lastRound = "err:" + (e && e.message); note("warn", "form-wake: " + e.message); })
        .finally(() => {
          clearTimeout(watchdog);
          running = false;
          if (ticks % HEARTBEAT_EVERY === 0) {
            // 心跳：让"这条线还在跑"成为日志里能查到的事，而不是靠客户来问"我点了没反应"
            note("info", "ops-gateway 心跳 " + WAKE_REV + " 第" + ticks + "轮 · 上轮=" + lastRound);
          }
        });
    };
    // 🔴 不许读 ctx.timer：Cordis 的 ctx 是守卫代理，没 inject 的属性【一读就抛】
    //    （实测报错 "cannot get property \"timer\" without inject"）。宿主侧官方先例是裸定时器
    //    （dsh-schedule 用 setTimeout，全文件 ctx.timer 出现 0 次），所以这里直接用 setInterval。
    setInterval(run, TICK_MS);
  } catch (e) {
    // 🔴 到这里必须咽下去：apply 抛出去 = 整棵插件树失败 = 桌面端起不来
    try { console.warn("[ops-gateway] host apply 失败（表单唤醒不生效，聊天与出片照常）:", e && e.message); } catch (e2) {}
  }
}
