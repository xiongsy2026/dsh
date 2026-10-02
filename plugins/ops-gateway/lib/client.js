window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    const PV_BUILD = "pv-20261002f";   // 排查"到底加载了哪一版"用（改这个文件时顺手 +1 后缀）
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
        return { known: true, intent: !!s.intent, up: !!s.up, url: s.url || "", port: s.port, run: s.run || "",
                 reason: s.reason || "", how: s.how_to_start || w.start || "", idle: s.idle_age };
      });
    }

    // 导出条：只在 hypit 台上出现；没有 run（没表过意图）时按钮保持禁用——看可以，烧钱不行
    function ExportBar(props) {
      const run = props.run || "";
      const s = React.useState({ phase: "idle", msg: "", link: "", path: "", task: "", busy: false });
      const st = s[0], setSt = s[1];
      const timer = React.useRef ? React.useRef(null) : { current: null };

      function poll(id) {
        getJson(GW + "/api/hypit/export?id=" + encodeURIComponent(id)).then(j => {
          if (!j || j.error) { setSt(p => Object.assign({}, p, { phase: "failed", msg: (j && j.error && j.error.message) || "查不到这条导出", busy: false })); return; }
          if (j.state === "done") { setSt(p => Object.assign({}, p, { phase: "done", msg: "已导出 " + (j.bytes || 0) + " 字节", link: j.url || "", path: j.path || "", busy: false })); return; }
          if (j.state === "failed") { setSt(p => Object.assign({}, p, { phase: "failed", msg: j.error || "导出失败", busy: false })); return; }
          const txt = { queued: "排队中", planning: "验免费闸（plan）", building: "渲染合成中（本地·约 1~4 分钟）", fetching: "取回成片" }[j.state] || j.state;
          setSt(p => Object.assign({}, p, { phase: "busy", msg: txt + "…" + (j.elapsed ? " " + j.elapsed + "s" : "") }));
          timer.current = setTimeout(() => poll(id), 3000);
        });
      }

      function start() {
        if (st.busy || !run) return;
        setSt(p => Object.assign({}, p, { busy: true, phase: "busy", msg: "提交中…", link: "", path: "" }));
        // text/plain：浏览器不发预检；服务端按本机 Origin 放行（插件里不嵌 token）
        fetch(GW + "/api/hypit/export", {
          method: "POST", headers: { "Content-Type": "text/plain" },
          body: JSON.stringify({ run: run, task: (st.task || "").trim() })
        }).then(r => r.json().then(j => ({ code: r.status, j: j }))).then(res => {
          if (res.code !== 200 || !res.j || !res.j.id) {
            setSt(p => Object.assign({}, p, { phase: "failed", busy: false, msg: (res.j && res.j.error && res.j.error.message) || ("HTTP " + res.code) }));
            return;
          }
          setSt(p => Object.assign({}, p, { phase: "busy", msg: "已受理 " + res.j.id + "（0 分·只允许零生成腿工程）" }));
          poll(res.j.id);
        }).catch(e => setSt(p => Object.assign({}, p, { phase: "failed", busy: false, msg: "打不动网关：" + e })));
      }

      React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
      const btn = { padding: "3px 10px", fontSize: 12, borderRadius: 4, cursor: st.busy ? "default" : "pointer",
        border: "1px solid #fa6f08", background: st.busy ? "#3a2a1a" : "#fa6f08", color: "#fff" };
      const inp = { padding: "2px 6px", fontSize: 12, width: 150, background: "#1b1f27", color: "#e6e9ef",
        border: "1px solid #333a45", borderRadius: 4 };
      return h("div", { style: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                                 padding: "6px 8px", borderBottom: "1px solid #232833", fontSize: 12, background: "#151821" } },
        h("span", { style: { color: "#9aa4b2" }, title: run }, "工程 " + (run.split(/[\\/]/).pop() || "—")),
        h("input", { style: inp, placeholder: "任务目录（可留空）", value: st.task,
          onChange: e => setSt(p => Object.assign({}, p, { task: e.target.value })) }),
        h("button", { style: btn, disabled: st.busy || !run, onClick: start,
          title: run ? "build + get，只允许零生成腿的免费装配工程" : "这条会话还没登记 run 归属，不给点" },
          st.busy ? "导出中…" : "导出成片（免费）"),
        st.msg ? h("span", { style: { color: st.phase === "failed" ? "#ff7b72" : "#9aa4b2" } }, st.msg) : null,
        st.link ? h("a", { href: st.link, target: "_blank", style: { color: "#58a6ff" } }, "下载/播放") : null,
        st.path ? h("code", { style: { color: "#6b7280", fontSize: 11 } }, st.path) : null
      );
    }

    function WorkbenchBody(props) {
      const w = byKey[props && props.__wbKey];
      const sid = props && props.sessionId ? String(props.sessionId) : "";
      const s = React.useState({ st: null, forced: false, silent: false });
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
                title: "只是这一页显示出来，不改归属登记（导出仍要有 run 才能点）" }, "我就是要看，直接显示"))
          );
        }
        return shell(h("iframe", { src: w.fallback, title: w.label,
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } }));
      }
      // ② 有意图但引擎没起：台要在，里面说清楚怎么起——"进程不在"从来不是藏掉台的理由
      if (!st.up) {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, w.label + "：引擎没在跑"),
          h("div", null, "状态：", h("code", null, st.reason || "down"),
             st.idle == null ? null : h("span", null, "（" + st.idle + "s 前还有人看）")),
          h("div", { style: { marginTop: 8 } }, "起它：",
            h("code", null, st.how || w.start || "见对应 skill 的 monitor-contract"))
        );
      }
      // ③ 有意图 + 引擎活着：正常出台（hypit 的端口由 presence 口给，换端口不用改这里）
      const url = w.skill === "hypit" ? (st.url || "http://localhost:" + (st.port || 5179) + "/") : (st.url || w.fallback);
      return shell([w.key === HKEY ? h(ExportBar, { key: "bar", run: st.run }) : null,
        h("iframe", { key: "frame", src: url, title: w.label,
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } })]);
    }

    function bodyOf(w) {
      return function Body(props) {
        return h(WorkbenchBody, Object.assign({}, props, { __wbKey: w.key }));
      };
    }
    function titleOf(w) {
      return function Title() {
        return h("span", { title: w.scope === "ops" ? w.label + "（运维·全局·不自动弹）" : w.label + "（本会话有该流水线意图才弹）" }, w.label);
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
