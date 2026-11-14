window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    const PV_BUILD = "pv-20261009a";   // 第 6 档「提示词反推」台【下线】（输入搬进 8100 表单台，不再摆 8090 页面）；排查加载哪版用
    try { window.__OPS_GW_EVAL__ = true; console.log("[pipeline-view] module evaluated", PV_BUILD); } catch (e) {}

    // ── 右栏工作台规则（docs/ADR-workbench-presence.md）────────────────────────────
    //   哪个技能挂哪个台是一张写死的表；会话没表过意图（新会话）就什么都不挂。
    //   intent（意图，持久）决定页签弹不弹；up（引擎活着）只决定台里显示什么。
    //   判定统一走 8100 一个口：video-ui(:8765) 和 studio(:5179) 都不给 CORS，插件直连读不到响应。
    const GW = "http://127.0.0.1:8100";
    const PRESENCE = GW + "/api/workbench/presence?session=";
    const KEY = "weeksir-pipeline-view";
    const HKEY = "weeksir-hypit-view";
    const MGKEY = "weeksir-model-gateway";
    const FKEY = "weeksir-film-view";
    const PRKEY = "weeksir-prompt-reverse-view";
    const MTKEY = "weeksir-motion-transfer-view";
    const FFKEY = "weeksir-skill-form-view";   // 输入表单台（8100 /ui/<skill>）

    const WORKBENCHES = [
      { key: KEY, skill: "omni", scope: "session", auto: true, label: "流水线",
        fallback: "http://127.0.0.1:8765/", start: "计划任务 WeeksirVideoUI（start_video_ui.cmd）" },
      { key: HKEY, skill: "hypit", scope: "session", auto: true, label: "Hypit Studio",
        fallback: "http://localhost:5179/", start: "hypit studio --run <工程>.svrun --port 5179" },
      // film 全流程看板:短剧带货/影视类 skill（short-drama、ai-film-router 都登记 film）意图后弹出。
      // 8100 同域托管看板页（/api/film/board），不是外部引擎，up 恒 True；intent 决定弹不弹。
      { key: FKEY, skill: "film", scope: "session", auto: true, label: "短剧全流程",
        fallback: "http://127.0.0.1:8100/api/film/board", start: "8100 看板页（film 意图自动弹出，无独立引擎）" },
      // 提示词反推（第 6 档）：2026-10-09 工作台【下线】—— 输入搬到 8100 的表单台（/ui/prompt-reverse，
      // 字段表来自该 skill 的 SKILL.md），反推结果照旧回对话 ⇒ 右栏不再摆 8090 那张引擎页面。
      // 🔴 同一件事只留一个入口：8090 页面自己能收视频、这条表单也能收，两个口并存就必然出现
      //   "在页面填了一份、在对话里说了另一份"。条目只保留两件事：① 僵尸页签清理仍覆盖它；
      //   ② retired 双保险 —— 判定口那边 form-is-input 已把它顶成 intent=False，意外回 true 也永不弹。
      // 8090 仍是这条线的引擎进程（判定口照常真探它、页面地址由 presence 给，这里不抄第二份）。
      { key: PRKEY, skill: "prompt-reverse", scope: "session", auto: true, label: "提示词反推", retired: true },
      // 输入表单台（2026-10-09，docs/PRD-input-form-single-port.md）：台里画的是 8100 托管的
      // /ui/<skill>（一个模板 + query），字段表来自各 SKILL.md 的 form: 段。
      // 🔴 它不归某个引擎管，所以 skill="form" 直接对应判定口的 form 档：
      //    presence 里 form.intent = 【这条会话登记的意图档配了字段表没有】；没配表 ⇒ 恒不弹（不会多一个空台）。
      //    url 由判定口给（它才知道是哪个技能、哪条会话），这里不硬编第二份。
      { key: FFKEY, skill: "form", scope: "session", auto: true, label: "填参数",
        down: "8100 未起", start: "8100 常驻服务（WeeksirModelAdmin 看门狗，每 1 分钟自拉）" },
      // 动作迁移（第 7 档）：2026-10-08 工作台【下线】—— 引擎从 8091（D:\nzdh）剥离进网关轨
      // （backend/services/motion_rpa），跑动、出片与直链都直接在对话里报，不再有独立工作台。
      // 条目只保留两件事：① 僵尸页签清理仍覆盖它（1d 那条）；② retired 双保险 —— 判定口那边
      // intent 已恒 False，就算意外回 true，这里也永不弹。motion-transfer 的意图登记保留（排他用）。
      { key: MTKEY, skill: "motion-transfer", scope: "session", auto: true, label: "动作迁移", retired: true },
      // 运维台：全局可【手动】开（右栏"+"里选得到），但【绝不自动弹】——它跟技能无关，
      // 自动弹等于把内部控制台摆进每一条对话，包括客户会话。
      { key: MGKEY, skill: null, scope: "ops", auto: false, label: "模型网关", fallback: GW + "/" },
    ];
    const byKey = {};
    for (const w of WORKBENCHES) byKey[w.key] = w;

    function getJson(url) {
      return fetch(url).then(r => (r && r.ok ? r.json() : null)).catch(() => null);
    }

    // 对账循环和多个页签体共用一次探测（5 秒内不重复打）
    let cache = { sid: null, at: 0, p: Promise.resolve(null) };
    function presence(sid) {
      const now = Date.now();
      if (cache.sid === sid && now - cache.at < 5000) return cache.p;
      // 回 null = 判定口没回话（8100 没起 / 抖了 / 回了不是判定形状）。
      // 🔴 绝不能把 null 当"没意图"：那会让 8100 一重启就把每条会话的台全关掉再弹回来。
      cache = { sid: sid, at: now, p: getJson(PRESENCE + encodeURIComponent(sid || "")).then(j =>
        (j && (j.omni || j.hypit || j.film)) ? j : null) };
      return cache.p;
    }

    function stateOf(w, sid) {
      if (w.scope === "ops") return Promise.resolve({ known: true, intent: true, up: true, url: w.fallback, how: "" });
      return presence(sid).then(j => {
        if (!j) return { known: false, intent: false, up: false, url: "", reason: "gateway-silent" };
        const s = (j || {})[w.skill] || {};
        // film 看板:8100 同域托管,up 恒 True;badge 只标"已挂看板"
        if (w.skill === "film") {
          BADGE[sid + "|" + w.key] = s.intent ? "全流程看板" : "";
        } else if (w.skill) {
          BADGE[sid + "|" + w.key] = !s.intent ? "" : (s.up ? "就绪"
            : (s.state === "generating" || ((s.progress || {}).summary || {}).running ? "生成中" : (w.down || "Studio 未起")));
        }
        // 🔴 hypit 不许把 url 预填成 fallback：presence 不给 url，一填就永远开 fallback 那个端口（5179），
        //    而 5179 上是 10-02 的 hypit-e2e 工程 ⇒ 用户看到"视频牛头不对马嘴"（2026-10-03 实测）。
        //    hypit 的台必须由 presence 的 port 决定；没有 port 就不开 iframe（走进度台说清楚）。
        return { known: true, intent: !!s.intent, up: !!s.up, url: s.url || (w.skill === "hypit" ? "" : w.fallback), port: s.port, run: s.run || "",
                 reason: s.reason || "", how: s.how_to_start || w.start || "", idle: s.idle_age,
                 state: s.state || "", legs: s.active_legs || 0, progress: s.progress || null,
                 live: s.live_url || "", spend: s.spend_note || "", task: s.task || "" };
      });
    }

    function ProgressPanel(props) {
      const p = props.p || {}, sm = p.summary || {};
      // 🔴 看片就地播，不跳转（2026-10-03 用户定）：以前那个 target=_blank 的链接会把人甩出 DSH。
      //   能直接内嵌是因为主界面文档没有 CSP（唯一那条 content-security-policy 在配对页 handler 里），
      //   而 8100 静态口已支持 206/Range（拖进度条要它）——两条都由 scripts/check-workbench-client.mjs 钉着。
      const openS = React.useState({}), open = openS[0], setOpen = openS[1];
      const chip = (txt, bg, fg) => h("span", { key: txt, style: { padding: "1px 7px", borderRadius: 10,
        fontSize: 11, background: bg, color: fg || "#e6e9ef", marginRight: 5 } }, txt);
      const col = { succeeded: "#3fb950", failed: "#ff7b72", running: "#d29922", queued: "#6b7280" };
      const rows = (p.segments || []).slice(0, 12).map(s =>
        h("div", { key: s.id, style: { padding: "5px 0", borderBottom: "1px solid #1d222c", fontSize: 12 } },
          h("span", { style: { color: col[s.state] || "#8b949e", fontWeight: 600, marginRight: 6 } },
             s.state === "succeeded" ? "完成" : s.state === "failed" ? "失败" : s.state === "running" ? "进行中" : "排队"),
          h("span", { style: { color: "#e6e9ef" }, title: s.id }, (s.label || s.id.slice(0, 8)) + " · " + s.model),
          h("span", { style: { color: "#6b7280", marginLeft: 6 } },
             s.seconds + "s" + (s.ago < 3600 ? " · " + s.ago + "秒前" : "") + (s.credits ? " · " + s.credits + "分" : "")),
          // 🔴 成功的腿就地播：点一下展开播放器，再点收起；不新开页面、不离开右栏
          s.output ? h("button", { style: { marginLeft: 8, padding: "1px 9px", fontSize: 11, borderRadius: 4,
              cursor: "pointer", border: "1px solid #2f5a34", background: open[s.id] ? "#1d2f21" : "#12261a",
              color: "#3fb950", fontWeight: 600 },
              onClick: () => setOpen(x => Object.assign({}, x, { [s.id]: !x[s.id] })) },
              open[s.id] ? "收起" : "看片") : null,
          s.output ? h("span", { style: { color: "#6b7280", marginLeft: 6, fontSize: 11 } },
              s.output.replace(/^https?:\/\/[^/]+\//, "").slice(0, 40)) : null,
          // 🔴 段回传进工程的路径：导演台能看见片段就是因为它进了 clips/（装配工程引用的是本地素材，不是账本链接）
          s.clip ? h("span", { style: { color: "#3fb950", marginLeft: 6, fontSize: 11 },
              title: "已回传进工程：" + s.clip }, "→ " + s.clip.split("/").slice(-2).join("/")) : null,
          // 就地播：8100 静态口给的是 206/Range，所以能拖进度条；preload=metadata 免得十二条行一起拉流
          open[s.id] && s.output ? h("video", { controls: true, preload: "metadata", src: s.output,
              style: { display: "block", width: "100%", maxHeight: 300, background: "#000",
                       marginTop: 6, borderRadius: 4 } }) : null,
          s.error ? h("div", { style: { color: "#ff7b72", marginTop: 2, lineHeight: 1.5 } },
            s.error.slice(0, 130),
            s.upstream ? h("span", null, " 上游任务 ", h("code", { title: s.upstream }, s.upstream.slice(0, 16) + "…")) : null) : null
        ));
      return h("div", { style: { padding: "12px 14px", lineHeight: 1.7, fontSize: 13, color: "#9aa4b2",
                                 overflow: "auto", flex: 1 } },
        h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 4 } },
          props.generating ? "正在生成" : "生成进度（网关账本）"),
        props.notice ? h("div", { style: { fontSize: 12, color: "#8b949e", marginBottom: 6 } }, props.notice) : null,
        props.generating ? h("div", { style: { fontSize: 12, color: "#d29922", marginBottom: 6 } },
          "studio 查看器没起，但引擎在工作——这里就是它的进度视图。") : null,
        h("div", { style: { fontSize: 12, marginBottom: 8 } },
          chip("成功 " + (sm.ok || 0), "#12261a", "#3fb950"),
          chip("失败 " + (sm.failed || 0), "#2a1315", "#ff7b72"),
          chip("进行中 " + (sm.running || 0), "#2a2314", "#d29922"),
          chip("排队 " + (sm.queued || 0), "#1b1f27", "#8b949e"),
          chip("已扣 " + (sm.credits || 0) + " 分", "#1b1f27"),
          h("span", { style: { color: "#6b7280", fontSize: 11, marginLeft: 4 } },
             "来源：网关账本（永久，不依赖 studio）")),
        (p.projects || []).length ? h("div", { style: { fontSize: 12, color: "#6b7280", marginBottom: 8 } },
          "工程 " + p.projects.join("、")) : null,
        rows.length ? h("div", null, rows) : h("div", { style: { color: "#6b7280" } }, "本会话还没有生成腿。"),
        h("div", { style: { marginTop: 12, fontSize: 12, color: "#6b7280" } },
          props.studio
            ? h("button", { style: { padding: "3px 10px", fontSize: 12, borderRadius: 4, cursor: "pointer",
                border: "1px solid #3a4250", background: "#1b1f27", color: "#e6e9ef" }, onClick: props.onFrame },
                "打开导演台视图（时间轴/成片预览）")
            // 故意不再印命令行：起引擎是 agent/按钮的活，给人抄命令就是"该弹的东西没人执行"的老坑
            : "要看时间轴或成片预览，让 agent 起 studio 就行（可选，不影响这里的进度）。")
      );
    }

    function WorkbenchBody(props) {
      const w = byKey[props && props.__wbKey];
      const sid = props && props.sessionId ? String(props.sessionId) : "";
      const s = React.useState({ st: null, forced: false, silent: false, view: null });
      const cur = s[0], setCur = s[1];
      React.useEffect(() => {
        let alive = true;
        const tick = () => stateOf(w, sid).then(x => {
          if (!alive) return;
          // 判定口没回话：有旧判定就沿用旧判定（别闪），一点判定都没有才承认"不知道"
          if (x.known) setCur(p => Object.assign({}, p, { st: x, silent: false,
            // 查看器"没起→起来"的上升沿：把视图交还给官方 studio。自绘进度台只做兜底，
            // 不能因为用户之前点过一次就把官方界面永久挡住（同"别拿自绘视图抢官方界面"那条）
            view: (x.up && p.st && !p.st.up && p.view === "panel") ? null : p.view }));
          else setCur(p => Object.assign({}, p, { silent: !p.st }));
        });
        tick();
        const t = setInterval(tick, 5000);
        return () => { alive = false; clearInterval(t); };
      }, [sid]);
      const st = cur.st || { intent: false, up: false, reason: "cold" };
      const ghost = { padding: "3px 10px", fontSize: 12, borderRadius: 4, cursor: "pointer",
        border: "1px solid #3a4250", background: "#1b1f27", color: "#e6e9ef" };
      const shell = (node) => h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } }, node);

      // ⓪ 一次判定都没拿到（8100 没在跑）⇒ 说清是判定口哑了，别谎报"这个会话没意图"
      if (!cur.st && cur.silent) {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, w.label + "：判定口没回话"),
          h("div", null, "8100（统一网关/判定口）没在跑，所以现在说不出这条会话该挂哪个台。"),
          h("div", { style: { marginTop: 8 } }, "起它：",
            h("code", null, "计划任务 WeeksirModelAdmin（schtasks /Run /TN WeeksirModelAdmin）"))
        );
      }

      // ① 没意图（新会话 / 别的流水线）⇒ 不挂台；人主动点开时给一个出口，但不改归属登记
      if (!st.intent) {
        // 🔴 表单台【没有可强制显示的兜底页】：画哪张字段表由"这条会话登记的意图档"决定，
        //    没意图就不知道该渲染哪个技能的表 —— 给它一个 w.fallback 等于塞一张空表骗人。
        //    所以这一档只说明，不给"我就是要看"按钮（其余档照旧，人有权看别的会话的台）。
        const canForce = !!st.url || !!w.fallback;
        if (!cur.forced || !canForce) {
          return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
            h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } },
               w.label + "：这个页签不属于当前会话"),
            h("div", null, canForce
              ? "直接关掉这个页签就行（它是旧版本自动打开留下的；当前会话没做过 " + w.skill + " 的任务）。"
              : "填参数台要先在这条对话里点一下技能（" + w.skill + "），字段表按登记的意图才画得出来。没登记 ⇒ 这里本来就该是空的。"),
            h("div", { style: { marginTop: 6, fontSize: 12, color: "#6b7280" } },
               "判定：", h("code", null, st.reason || "no-intent")),
            canForce ? h("div", { style: { marginTop: 10 } },
              h("button", { style: ghost, onClick: () => setCur(p => Object.assign({}, p, { forced: true })),
                title: "只是这一页显示出来，不改归属登记" }, "我就是要看，直接显示")) : null
          );
        }
        return shell(h("iframe", { src: st.url || w.fallback, title: w.label,
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } }));
      }
      // ── hypit 的视图顺序（2026-10-03 定）：studio 是【查看器】，引擎在不在工作是另一件事 ──
      //   查看器活着 ⇒ 默认就给你官方 studio（任务页在左栏第三个 tab）；
      //   查看器没起 ⇒ 一律给精简进度台（官方查看器非要 .svrun 才起得来，登记后那段真空不能只印文字）。
      const prog = st.progress || {};
      const hasLegs = !!prog.known;
      const act = ((prog.summary || {}).running || 0) + ((prog.summary || {}).queued || 0);
      const generating = st.state === "generating" || act > 0;
      const noProj = st.state === "no-project";
      const notice = noProj
        ? "登记的工程还没有 .svrun（源文件正在写）。写出来那一刻查看器会自动起，你不用敲任何命令——这段时间这里就是进度视图。"
        : (st.up || generating || hasLegs ? null : "studio 查看器没起，不影响生成——下面是网关账本。");
      const toPanel = () => setCur(x => Object.assign({}, x, { view: "panel" }));
      const toFrame = () => setCur(x => Object.assign({}, x, { view: "frame" }));
      const panel = () => h(ProgressPanel, { p: prog, studio: !!st.up, generating: generating,
                                             notice: notice, onFrame: toFrame });
      const frame = () => shell([
        w.skill === "hypit" ? h("div", { key: "sw", style: { padding: "6px 10px", borderBottom: "1px solid #232833", background: "#151821" } },
          h("button", { style: ghost, onClick: toPanel }, "看精简进度（网关账本）")) : null,
        h("iframe", { key: "frame",
          src: st.url || (w.skill === "hypit" ? "http://localhost:" + st.port + "/" : w.fallback),
          title: w.label, style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } })]);

      if (cur.view === "panel" && (hasLegs || w.skill === "hypit")) return panel();
      // 🔴 hypit 的 iframe 只开 presence 报回来的那个端口。没有端口 ⇒ 回进度台，绝不回落到 5179
      //    （5179 上是 10-02 的 hypit-e2e 工程，回落就等于把别人的工程当成你的导演台 —— 2026-10-03 实测）
      if (w.skill === "hypit" && st.up && st.port) return frame();
      if (w.skill === "hypit" && (st.up || hasLegs)) return panel();
      // film 全流程看板:8100 同域静态页,up 恒 True,有意图就出 iframe
      if (w.skill === "film" && st.intent) return frame();
      // film 有意图但 8100 没回话(判定口静默):给"看板没连上"卡,别藏台
      if (w.skill === "film" && !st.up) {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, w.label + "：看板没连上"),
          h("div", null, "8100 判定口没回话。看板页由 8100 托管（读 productions/ 项目文件），不影响出片。"),
          h("div", { style: { marginTop: 6, fontSize: 12, color: "#6b7280" } }, "状态：", h("code", null, st.reason || "gateway-silent")));
      }
      // 表单台（2026-10-09）：台里是 8100 自己托管的 /ui/<skill>，地址由判定口给（这里不猜端口）。
      // 🔴 必须单独成支：落到下面 ④ 会印"studio 查看器没起"——这条线没有 studio，
      //    印出来就是把人引去查一个不存在的东西（判定口哪天真回 up:false，说的该是"8100 没连上"）。
      if (w.skill === "form" && st.up) return frame();
      if (w.skill === "form") {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, w.label + "：8100 没连上"),
          h("div", null, "表单页由 8100 托管，它现在没回话。填进去的参数只在提交时才落盘，不影响这条对话继续聊。"),
          h("div", { style: { marginTop: 6, fontSize: 12, color: "#6b7280" } }, "状态：", h("code", null, st.reason || "gateway-silent")));
      }
      if (w.skill === "hypit" && st.up) return frame();
      if (w.skill === "hypit" && hasLegs) return panel();
      // prompt-reverse（2026-10-09 工作台下线）：输入搬进 8100 的表单台，反推结果回对话 ⇒ 这一档
      // 【永不弹台】（判定口 form-is-input 已顶掉 intent + 上面 retired 双保险）。
      // 旧版本留下的僵尸页签被点开时，只给"已下线 + 去哪填"，不摆指向 8090 的死页面。
      if (w.skill === "prompt-reverse") {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, "提示词反推台已下线"),
          h("div", null, "要反推的视频、额外提示与用哪个脑，现在填右栏的「填参数」台（8100 托管的表单页）；"
            + "反推出来的提示词直接在对话里报。这条 skill 只出提示词、不出片，所以没有任何生成腿在扣着跑。"));
      }
      // motion-transfer（2026-10-08 工作台下线）：引擎剥离进网关轨（backend/services/motion_rpa），
      // 跑动、出片与直链直接在对话里报 ⇒ 这一档【永不弹台】（presence intent 恒 False + 上面 retired 双保险）。
      // 万一有旧版本留下的僵尸页签被打开，也只给"已下线"说明，不摆指向 8091 的死页面。
      if (w.skill === "motion-transfer") {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, "动作迁移工作台已下线"),
          h("div", null, "动作迁移引擎已并入统一网关轨（video-skill），出片进度、产物路径与直链都在对话里报，"
            + "不再有独立工作台。"));
      }
      // ④ 有意图、但查看器不在：台不藏。说清"生成照常"，别把命令行丢给人
      if (!st.up) {
        // 🔴 hypit 这一段不再走文字卡：精简版常驻，切换口一直在（用户 2026-10-03 定「精简版可以切换，挺好的」）
        if (w.skill === "hypit") return shell(panel());
        // 🔴 2026-10-03 分开两种"没起"：没有工程可打开 ≠ 起了但连不上（过去都印同一句，只能靠猜）
        // ④′ 只有 omni 会走到这张卡（hypit 上面已经回面板了）：no-project / generating 是 studio 独有的状态，这里不再判
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } },
             w.label + "：" + (generating ? "正在生成（引擎没在跑）" : "studio 查看器没起")),
          h("div", null, generating
            ? "网关账本里有 " + act + " 条腿在跑或排队，生成照常进行——这里只是看不到时间轴/预览，不是没在工作。"
            : "这不影响生成，只影响看不看得到时间轴与成片预览。"),
          h("div", { style: { marginTop: 6, fontSize: 12, color: "#6b7280" } }, "状态：",
             h("code", null, st.reason || "down"), st.idle == null ? null : "（" + st.idle + "s 前还有人看）"),
          h("div", { style: { marginTop: 8 } }, hasLegs
            ? h("button", { style: ghost, onClick: toPanel }, "看生成进度")
            : "要开查看器：让 agent 起 studio（人或客户不需要敲命令）。")
        );
      }
      // ⑤ 其余（omni 等）：有意图 + 引擎活着 ⇒ 正常出台
      return frame();
    }

    function bodyOf(w) {
      return function Body(props) {
        return h(WorkbenchBody, Object.assign({}, props, { __wbKey: w.key }));
      };
    }

    // ── RunningHub 网页台（2026-10-08 装上，2026-10-16 换掉实现）──────────────────
    //   runninghub.cn 回 X-Frame-Options: SAMEORIGIN ⇒ 原页嵌不进 DSH；转发登录态的反代 = 把账号凭据
    //   递出去 ⇒ 不做。剩下那版是 8123 无头镜像（c:\dsh\tools\rh_panel：headless 开页 + 250ms 截帧 +
    //   回传鼠标键盘），2026-10-16 用户判"映射的方式打开不行"：4fps 截图拖不动 ComfyUI 的节点和连线，
    //   而且它 launch_persistent_context 占的是 D:\nzdh\rh_profiles\rh_<手机号> —— 同一份画像只能有
    //   一个 Chromium ⇒ 面板一开，8091 那条真跑批的 RPA 就起不来（自造互斥）。
    //   现在这一格只做一件事：一条链接，把真页交给【系统默认浏览器】。内核 main/index.js:14098 对所有
    //   http(s) 的 window.open 都是 shell.openExternal + 应用内 deny ⇒ 不需要给任何服务加执行端点。
    //   🔴 仍是【手动开的浏览工具】，跟技能无关 ⇒ 不进 WORKBENCHES（不写 scope:"session"/auto，
    //     不碰 check-workbench-client 的计数闸）、不自动弹、不占意图排他。
    const RHKEY = "weeksir-rhweb-view";
    // 🔴 RH_WORKFLOW 与 D:\nzdh\backend\services\runninghub_rpa_client.py 的 WORKFLOW_URL 必须同一条
    //    工作流；两处各写一份迟早漂，由 scripts/check-engine-single-source.py 钉相等（改号只改那边）。
    const RH_HOME = "https://www.runninghub.cn/";
    const RH_WORKFLOW = "https://www.runninghub.cn/workflow/2106377827540103169?source=workspace";

    function rhLink(href, label) {
      return h("a", {
        href: href, target: "_blank", rel: "noopener noreferrer",
        style: { display: "block", margin: "7px 0", padding: "9px 11px", borderRadius: 6,
          background: "#1b2130", border: "1px solid #2c3446", color: "#e6e9ef",
          fontSize: 13, textDecoration: "none" }
      }, label);
    }

    function RhWebBody() {
      return h("div", { style: { height: "100%", overflow: "auto", padding: "12px 13px",
        background: "#0f1115", color: "#c9ced8" } },
        h("div", { style: { fontSize: 12, lineHeight: 1.7, marginBottom: 10 } },
          "这里嵌不出 RunningHub 的页面（它只允许同源内嵌），我们也不做转发您登录态的代理。"
          + "点下面任意一条，会在系统默认浏览器里打开真页：能拖节点、能连线、能看实时输出。"),
        rhLink(RH_WORKFLOW, "打开「动作迁移」那条工作流"),
        rhLink(RH_HOME, "打开 RunningHub 首页"),
        h("div", { style: { fontSize: 11, color: "#6b7280", marginTop: 12, lineHeight: 1.7 } },
          "第一次要在浏览器里登录一次该账号。8091 自动跑批用的是它自己那份浏览器画像，"
          + "和这个窗口互不影响 —— 以前那个镜像面板会跟它抢同一份画像，所以面板摘了。")
      );
    }
    const BADGE = {};                       // sid|skill → "生成中" / "Studio 未起" / "就绪"
    function titleOf(w) {
      return function Title(props) {
        const sid = props && props.sessionId ? String(props.sessionId) : "";
        const badge = BADGE[sid + "|" + w.key] || "";
        return h("span", { title: w.scope === "ops" ? w.label + "（运维·全局·不自动弹）" : w.label + "（本会话有该流水线意图才弹）" },
          w.label + (w.skill && badge ? " · " + badge : ""));
      };
    }

    // tabId 由 dock store 生成、插件面拿不到可靠值 ⇒ 逐个候选试关（closeIn 对不存在的 id 是无害 no-op），
    // 关不掉也不致命：页签体自己会显示"不属于当前会话"，不会把引擎内容露出来。
    function tryClose(ctx, sid, w) {
      const c = ctx.sidebarRight;
      if (!c) return;
      const ids = [w.key, "dsh-resource://" + w.key, "dsh-resource://weeksir/" + w.key];
      for (const id of ids) {
        try { if (typeof c.closeIn === "function") c.closeIn(sid, id); } catch (e) {}
      }
      try { if (typeof c.closeTab === "function") ids.forEach(id => c.closeTab(id)); } catch (e) {}
    }

    // ── 聊天区「过程行」降噪（2026-10-03 用户定：成功的命令/读取类行别刷屏；错误、进行中、交付物保留）──
    // 为什么落在 CSS 层：内核【本来就会折叠】这些行 —— 但 dsh-client-ui-chat/lib/client.js:1586 的
    // processWindowReady 里带 `processPresentation.turnClosed` ⇒ 只有整回合结束才折，回合进行中每一条
    // tool-call 都直接铺成一行（用户截图那一堆 Pwsh/代码/读取图片 就是这个窗口期）。
    // 回合结束后那行「N 次工具调用 · M 条消息」点开仍然能看到全部明细，所以这里只压"进行中"这一段。
    // 用的钩子全是内核自己印的稳定属性：data-chat-flow-kind（chat:1637）、data-variant/data-tool/data-state（tool:1255-1258）。
    // 只按【过程变体】收：bash/read/search/write/edit/code —— "others" 不碰（交付物 present、cordis_* 都落在里面）。
    // 任何一条只要处于 error/stopped/running 就不匹配 ⇒ 出错命令必然留着。
    // 浏览器不认 :has() 时整条规则被忽略 ⇒ 退化成"什么都不做"，不会误藏东西。
    // 开关（不动内核 DOM、不用重启）：控制台 `window.weeksirProcessRows(false)` 立刻全显；
    // 或 localStorage.weeksir_process_rows="1" 后 Ctrl+R 永久关。默认 on。
    const NOISE_CSS = ':root[data-weeksir-noise="on"] [data-chat-flow-kind="tool-call"]' +
      ':is(:has([data-variant="bash"]),:has([data-variant="read"]),:has([data-variant="search"]),' +
      ':has([data-variant="write"]),:has([data-variant="edit"]),:has([data-variant="code"]))' +
      ':has([data-state="ok"]):not(:has([data-state="error"])):not(:has([data-state="stopped"]))' +
      '{display:none!important}';
    function noiseOff() {
      try { return localStorage.getItem("weeksir_process_rows") === "1"; } catch (e) { return false; }
    }
    function installProcessNoiseStyle() {
      if (!document.getElementById("weeksir-process-noise")) {
        const tag = document.createElement("style");
        tag.id = "weeksir-process-noise";
        tag.dataset.pluginCss = "weeksir-process-noise";
        tag.textContent = NOISE_CSS;
        document.head.append(tag);
      }
      const sync = () => { document.documentElement.dataset.weeksirNoise = noiseOff() ? "off" : "on"; };
      sync();
      try {
        window.weeksirProcessRows = (on) => {
          try { localStorage.setItem("weeksir_process_rows", on ? "0" : "1"); } catch (e) {}
          sync();
          return document.documentElement.dataset.weeksirNoise;
        };
      } catch (e) {}
      return document.documentElement.dataset.weeksirNoise;
    }

    function apply(ctx) {
      try { window.__OPS_GW_APPLY__ = true; } catch (e) {}
      try { window.__OPS_GW_NOISE__ = installProcessNoiseStyle(); window.__OPS_GW_NOISE_CSS__ = NOISE_CSS; } catch (e) {}
      try {
        for (const w of WORKBENCHES) {
          ctx.effect(() => ctx.sidebarRightTabs.register({
            id: w.key, kind: w.key, priority: "extension", title: () => w.label
          }), "workbench type " + w.key);
          ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
            ctx.slots.register({ name: "sidebar.right.pane.tab", key: w.key }, bodyOf(w))
          ), "workbench body " + w.key);
          ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
            ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: w.key }, titleOf(w))
          ), "workbench title " + w.key);
        }

        // RunningHub 网页台：独立注册（不进 WORKBENCHES、不自动弹、不占意图排他），
        // guide 卡挂在「开始」页，点了就开（manual open only）。
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id: RHKEY, kind: RHKEY, priority: "extension",
          title: () => "RunningHub 网页",
          guide: [{ order: 60,
            title: () => "RunningHub 网页",
            description: () => "点一下就在您自己的浏览器里打开 RunningHub 真页（它的页面不让内嵌，右栏里放不了它的画面）" }]
        }), "rhweb type");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab", key: RHKEY }, RhWebBody)
        ), "rhweb body");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: RHKEY }, () => h("span", null, "RunningHub 网页"))
        ), "rhweb title");

        function curSession() {
          try { return (ctx.sessions && ctx.sessions.list && ctx.sessions.list.getSnapshot().current) || ""; } catch (e) { return ""; }
        }

        // 对账：状态按 (会话, 页签) 记。初值是 null（"还不知道"），所以：
        //   首次就判为无意图 ⇒ 也去关一次 —— 清掉旧版本代码（hypit 曾弹进每条会话）留下的僵尸页签。
        //   这是本次用户报"无关会话里出现这个提示"的根因：边沿触发等不到边沿，僵尸页签永远留着。
        const mem = {};
        function reconcile(sid) {
          for (const w of WORKBENCHES) {
            if (!w.auto) continue;
            const mk = sid + "|" + w.key;
            const st = mem[mk] || (mem[mk] = { armed: true, show: null, silentSince: 0 });
            stateOf(w, sid).then(r => {
              if (!r.known) {
                // 判定口没回话 ≠ 这条会话没意图：已挂的台保持不动（8100 抖一下不该闪断）。
                // 一点判定都没有过就维持"不弹"（fail-closed）；哑超过 90 秒才认输关掉。
                if (st.show === null) return;
                if (!st.silentSince) st.silentSince = Date.now();
                if (Date.now() - st.silentSince < 90000) return;
                st.silentSince = 0; st.show = false; st.armed = true;
                tryClose(ctx, sid, w);
                return;
              }
              st.silentSince = 0;
              const show = !!r.intent && !w.retired;   // retired 的档（动作迁移）永不弹，照旧走清理
              if (show === st.show) return;
              const was = st.show;
              st.show = show;
              if (show && st.armed) {
                st.armed = false;
                try { ctx.sidebarRight.openTab(w.key, { revealIfOpened: true }); } catch (e) {}
              } else if (!show) {
                st.armed = true;
                tryClose(ctx, sid, w);            // was===null（首次）也要关，别只等下降沿
              }
            }).catch(() => {});
          }
        }
        // 换会话：不清记忆（每个会话有自己的边沿），只把探测缓存作废，重查一次新会话的判定
        function resetAndReconcile() { cache = { sid: null, at: 0, p: cache.p }; reconcile(curSession()); }

        // 会话标题前缀（2026-10-05 用户要："确定了意图就在会话名前加 [超级复刻]/[omni出片]"）。
        // 只动【当前会话】、30 秒一次、已带前缀就不再 rename。rename 写的是 user 源标题，
        // 内核的自动摘要不会覆盖它（session-title: onUserMessage 直接 return）⇒ 安全。
        // 冷会话拿不到 binding ⇒ 静默跳过（那条会话被打开时自然会补上）。
        const TITLE_PREFIX = { "super-clone": "[复刻] ", hypit: "[hypit] ", omni: "[omni出片] ",
                               film: "[短剧] ", "product-video": "[产品片] ", "prompt-reverse": "[反推] " };
        const TITLE_MAX_BYTES = 80;   // 内核 maxTitleBytes:80，超了会被拒
        function utf8len(s) { try { return new TextEncoder().encode(s).length; } catch (e) { return s.length; } }
        function cutBytes(s, n) { let out = ""; for (const ch of s) { if (utf8len(out + ch) > n) break; out += ch; } return out; }
        function titlePrefixTick() {
          const sid = curSession();
          if (!sid) return;
          presence(sid).then(j => {
            if (!j) return;
            let skill = "";
            for (const w of WORKBENCHES) if (w.skill && (j[w.skill] || {}).intent) skill = w.skill;
            const pre = TITLE_PREFIX[skill];
            if (!pre) return;
            const snap = ctx.sessions.list.getSnapshot();
            const cur = (snap.byId && snap.byId[sid] && snap.byId[sid].title) || "";
            if (cur.indexOf(pre) === 0) return;
            const stripped = cur.replace(/^\[(复刻|hypit|omni出片|短剧|产品片|反推)\]\s*/, "");
            const next = pre + cutBytes(stripped || sid.slice(0, 12), TITLE_MAX_BYTES - utf8len(pre));
            try {
              const b = ctx.sessions.binding && ctx.sessions.binding(sid);
              if (b && b.session && typeof b.session.rename === "function") {
                b.session.rename(next).catch(() => {});
              }
            } catch (e) {}
          }).catch(() => {});
        }

        // 🔴 定时器只能走 ctx.timer（在 inject 名单里）。`ctx.setInterval` 是没注入的成员，
        //    Cordis 的守卫代理连 typeof 都会抛 "cannot get property without inject" —— 抛在这里
        //    会把【页签对账 + 标题前缀】整段跳过（页签还能出现是因为注册发生在更早的 401-426）。
        const every = (ctx.timer && typeof ctx.timer.setInterval === "function")
          ? (fn, ms) => ctx.timer.setInterval(fn, ms)
          : (fn, ms) => setInterval(fn, ms);
        if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function" &&
            ctx.sessions && ctx.sessions.list) {
          try { ctx.sessions.list.subscribe(() => resetAndReconcile()); } catch (e) {}
          every(() => reconcile(curSession()), 4000);
          every(titlePrefixTick, 30000);
          resetAndReconcile();
          titlePrefixTick();
        }
      } catch (e) {
        // 绝不因运维面板拖垮 DSH
        try { console.warn("[pipeline-view] register failed:", e); } catch (_) {}
      }
    }

    exports.apply = apply;
    exports.inject = ["slots", "sidebarRightTabs", "sidebarRight", "timer", "sessions"];
    return module.exports;
  }
});
