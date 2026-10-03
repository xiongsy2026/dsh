window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    const PV_BUILD = "pv-20261003b";   // 撤掉页签里的导出条（定案变更）；排查"到底加载了哪一版"用
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

    const WORKBENCHES = [
      { key: KEY, skill: "omni", scope: "session", auto: true, label: "流水线",
        fallback: "http://127.0.0.1:8765/", start: "计划任务 WeeksirVideoUI（start_video_ui.cmd）" },
      { key: HKEY, skill: "hypit", scope: "session", auto: true, label: "Hypit Studio",
        fallback: "http://localhost:5179/", start: "hypit studio --run <工程>.svrun --port 5179" },
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
        (j && (j.omni || j.hypit)) ? j : null) };
      return cache.p;
    }

    function stateOf(w, sid) {
      if (w.scope === "ops") return Promise.resolve({ known: true, intent: true, up: true, url: w.fallback, how: "" });
      return presence(sid).then(j => {
        if (!j) return { known: false, intent: false, up: false, url: "", reason: "gateway-silent" };
        const s = (j || {})[w.skill] || {};
        if (w.skill) BADGE[sid + "|" + w.key] = !s.intent ? "" : (s.up ? "就绪"
          : (s.state === "generating" || ((s.progress || {}).summary || {}).running ? "生成中" : "Studio 未起"));
        return { known: true, intent: !!s.intent, up: !!s.up, url: s.url || "", port: s.port, run: s.run || "",
                 reason: s.reason || "", how: s.how_to_start || w.start || "", idle: s.idle_age,
                 state: s.state || "", legs: s.active_legs || 0, progress: s.progress || null };
      });
    }

    function ProgressPanel(props) {
      const p = props.p || {}, sm = p.summary || {};
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
          s.error ? h("div", { style: { color: "#ff7b72", marginTop: 2, lineHeight: 1.5 } },
            s.error.slice(0, 130),
            s.upstream ? h("span", null, " 上游任务 ", h("code", { title: s.upstream }, s.upstream.slice(0, 16) + "…")) : null) : null
        ));
      return h("div", { style: { padding: "12px 14px", lineHeight: 1.7, fontSize: 13, color: "#9aa4b2",
                                 overflow: "auto", flex: 1 } },
        h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 4 } },
          props.generating ? "正在生成" : "生成进度（网关账本）"),
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
          if (x.known) setCur(p => Object.assign({}, p, { st: x, silent: false }));
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
        if (!cur.forced) {
          return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
            h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } },
               w.label + "：这个页签不属于当前会话"),
            h("div", null, "直接关掉这个页签就行（它是旧版本自动打开留下的；当前会话没做过 " + w.skill + " 的任务）。"),
            h("div", { style: { marginTop: 6, fontSize: 12, color: "#6b7280" } },
               "判定：", h("code", null, st.reason || "no-intent")),
            h("div", { style: { marginTop: 10 } },
              h("button", { style: ghost, onClick: () => setCur(p => Object.assign({}, p, { forced: true })),
                title: "只是这一页显示出来，不改归属登记" }, "我就是要看，直接显示"))
          );
        }
        return shell(h("iframe", { src: w.fallback, title: w.label,
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } }));
      }
      // ── hypit 的视图顺序（2026-10-03 定）：studio 是【查看器】，引擎在不在工作是另一件事 ──
      //   查看器活着 ⇒ 默认就给你官方 studio（任务页在左栏第三个 tab）；
      //   查看器没起但账本里有腿 ⇒ 进度台，文案说"正在生成"，绝不再写"引擎没在跑"；
      //   两者都没有 ⇒ 说"查看器没起、不影响生成"，不甩命令行给人抄。
      const prog = st.progress || {};
      const hasLegs = !!prog.known;
      const act = ((prog.summary || {}).running || 0) + ((prog.summary || {}).queued || 0);
      const generating = st.state === "generating" || act > 0;
      const toPanel = () => setCur(x => Object.assign({}, x, { view: "panel" }));
      const toFrame = () => setCur(x => Object.assign({}, x, { view: "frame" }));
      const panel = () => h(ProgressPanel, { p: prog, studio: !!st.up, generating: generating, onFrame: toFrame });
      const frame = () => shell([
        hasLegs ? h("div", { key: "sw", style: { padding: "6px 10px", borderBottom: "1px solid #232833", background: "#151821" } },
          h("button", { style: ghost, onClick: toPanel }, "看精简进度（网关账本）")) : null,
        h("iframe", { key: "frame",
          src: st.url || (w.skill === "hypit" ? "http://localhost:" + (st.port || 5179) + "/" : w.fallback),
          title: w.label, style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } })]);

      if (cur.view === "panel" && hasLegs) return panel();
      if (w.skill === "hypit" && st.up) return frame();
      if (w.skill === "hypit" && hasLegs) return panel();
      // ④ 有意图、但查看器不在：台不藏。说清"生成照常"，别把命令行丢给人
      if (!st.up) {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } },
             w.label + "：" + (generating ? "正在生成（studio 查看器没起）" : "studio 查看器没起")),
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

    function apply(ctx) {
      try { window.__OPS_GW_APPLY__ = true; } catch (e) {}
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
              const show = !!r.intent;
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

        if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function" &&
            typeof ctx.setInterval === "function" && ctx.sessions && ctx.sessions.list) {
          try { ctx.sessions.list.subscribe(() => resetAndReconcile()); } catch (e) {}
          ctx.setInterval(() => reconcile(curSession()), 4000);
          resetAndReconcile();
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
