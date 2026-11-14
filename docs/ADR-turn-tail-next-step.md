# ADR：回合尾部的"下一步技能推荐"（替换"本轮文件改动"那一行）

- 状态：**Accepted（2026-10-08 用户拍 1/2/3 = 保留交付卡 / 最多 3 条去重 / 不显示价格）**
- 落点：`C:\dsh\plugins\super-clone-ui\lib\client.js`（不碰安装目录、不打编译产物补丁）
- 回归闸：`C:\dsh\scripts\check-turn-tail-recs.py`
- 起因（用户原话）：**"在每次对话结束后，都有这些文件改动的地方，可以改成，相关联的技能推荐，比如：你还可以：超级复刻 视频高清修复 去水印"**

## 1. 现场事实（全部核过，带 file:line）

| 事实 | 位置 |
|---|---|
| "本轮文件改动"那一行由官方 `dsh-client-ui-deliverables` 渲染 | `…/dsh-client-ui-deliverables/lib/client.js:893`（zh 字典 `produced.label`），组件 `ProducedFiles` :547，最多 6 个 chip :538 |
| 挂在链式槽 `conversation.chat.turnTail` | 注册 :964；宿主 `dsh-client-ui-chat/lib/client.js:3695`（`TurnTailNodeView` :3688，`turn/end` 才发布 :7298） |
| 官方目录把这个槽标为 **`replaceRisk: "none"`** | `dsh-cordis-client-runner/lib/client.js:2397-2433` ⇒ 第三方占它不算遮蔽官方 UI |
| 链式槽选举 = **首个 select 返回非 null 者独占**，按 priority 升序 | `dsh-client-ui-renderer/lib/client.js:831-849`；排序 `dsh-client-ui-slots/lib/index.js:130` |
| 官方注册**没写 priority**（=0）⇒ 我们 `priority: -1` 可顶 | `dsh-client-ui-deliverables/lib/client.js:964` |
| 数据源是同步的回合投影，不碰 fs、只算**成功**的 write/edit | `deliverables/lib/client.js:293-301`（参数里取 path）、:421-431（`isError` 不计）；读法 `owner.turn.data.get("deliverables")` → `{produced:[{seq,path}], presented:[…]}` |
| 交付大卡与"本轮文件改动"是**同一组件的两块** | :799 `produced.length>0 && ProducedFiles` / `presented.length>0 && 交付卡` ⇒ 无脑顶掉会连交付卡一起丢 |
| 藏那一行的 DOM 钩子 | :558 `data-produced-files-row`（**别钉 hash 类名** `P4kPIW_*`，:525-534） |
| 点击填 `/skill` 的现成机制 | `super-clone-ui/lib/client.js:227 setSkillPrompt`（显式 Range + insertText；`execCommand("selectAll")` 那个坑已修） |
| 技能文案/顺序唯一源 | `super-clone-ui/lib/client.js:758-792 SKILL_DOCK_META`，白名单 :794 |

## 2. 决策

**D1 只在"真有推荐"时抢位，否则返回 null 让官方原样渲染。**
⇒ 没推荐的回合零风险（交付卡、行为、样式全不动）；有推荐的回合由我们渲染，**必须把 `presented` 交付卡一起画回来**（用户拍板：保留交付卡）。

**D2 推荐由"本轮产物"反推，判据是纯函数 + 写死的表，不用 LLM、不猜语义。**
输入只有两样：`produced` 的路径集合、本轮用到的 skill（拿不到就按空）。规则表：

| 命中 | 追加推荐（按此顺序） |
|---|---|
| skill = `omni` / `super-clone` / `one-shot-video` / `short-drama` | video-enhance · video-dewatermark · super-clone |
| skill = `video-dewatermark` | video-enhance · super-clone |
| skill = `video-enhance` | video-dewatermark · super-clone |
| skill = `hypit-pipeline` | video-enhance · super-clone |
| 有 `.mp4` 产物但认不出是哪条 skill | video-enhance · video-dewatermark |
| 只有 `.png/.jpg/.webp` | super-clone · omni |
| 只有 md/txt/json（文案、提示词） | **不显示**（无产物不推荐，免得退化成广告） |

**D3 三条硬约束**（用户 2026-10-08 拍定）：① 交付卡保留；② 最多 3 条、去掉本轮已用的那个 skill；③ **卡片上不出现任何积分/价格数字** —— 价格必须从 `gateway.py resolve` 现读，写死在 UI 上迟早变假账。

**D4 进池的技能必须"真能跑且不违反既有闸"**：`product-video-pipeline`（直连 zexapi·未接网关）、`motion-transfer`（单价占位·引擎在别的进程花钱）、`voice-clone`（账本外例外腿）**不进推荐池**。

**D5 点击 = 只把 `/skill` 填进输入框**，不建 run、不挂台、不花钱。判定权仍在用户点 `/skill`（AGENTS.md 路由铁律，不许因为"点了推荐"就自动进引擎）。

## 3. 与既有规则的关系（必须说清，别当成推翻）

`feedback-showcase-ui-entry-only`（"展示类 UI 只在首页"）**不被推翻**：那条针对**凭空的能力广告**。本 ADR 的判据改成一句可判定的话——

> **推荐必须由本轮真实落盘的产物推出来；推不出来就不显示。**

凭空推荐 = 违规；接着产物推荐 = 服务。这条判据写成纯函数 `turnTailRecommendations()`，由闸跑值表。

## 4. 生效条件

只改 `plugins/super-clone-ui/lib/client.js` ⇒ **Ctrl+R 重载窗口即可**（该文件里已有实证注释）；
但 v2 还改了**官方 bundle** `@deepseek-ai/dsh-client-ui-deliverables/lib/client.js`，那是常驻 harness 进程 require 进去的
⇒ **要完全重启 DSH** 才换码（`Ctrl+R` 未必够）。一旦碰 `cordis.patch.yml` ⇒ 更是必须完全重启（patch 层只在 loader init 读一次）；本 ADR 不碰 patch 层。

## 5. 落地状态（2026-10-08 两版；v2 取代 v1）

### v1（已退役）：抢 `turnTail` 槽 + 自绘交付卡
`plugins/super-clone-ui` 以 `priority: -1` 顶掉官方 `Deliverables`，自己重画"本轮交付 + 你还可以"。
**为什么撤**：chain 槽独占（`dsh-client-ui-renderer/lib/client.js:831-849`，首个非 null 即 `break`），
我们一抢，官方那张卡整张不渲染 —— 原生预览、原生菜单、以及后来要加的**原生下载项**全跟着没了。
用户口径"下载控件要和 dsh 原生的一样"判了这版死刑。旧闸 `check-turn-tail-recs.py` 一并删除（它钉的正是这版的形状，且已崩）。

### v2（现行）：官方长 UI，我们出策略
| 件 | 位置 |
|---|---|
| 官方补丁（copy-on-write） | `scripts/patch-deliverables-weeksir.py` ⇒ 改 `@deepseek-ai/dsh-client-ui-deliverables/lib/client.js`；rev `weeksir-card-20261008a`；原版备份 `client.js.bak-weeksir-download-20261008` |
| 下载项 | 官方菜单 `items` 尾接 `.concat(weeksirDl ? [{id:"weeksir-download", icon: IconDownloadOutline16, label: t("presented.download")}] : [])` ⇒ 拼不出直链就不加这一项；文案走官方字典（zh「下载视频」/ en "Download"） |
| 推荐行 | 官方 `Deliverables` 的 children 里插 `weeksirRecsRow`，只用官方 CSS 类（`presented/toggle/hostStatus/description`）；钩子给空 ⇒ `return null` 整行不画 |
| 策略（唯一一份） | `plugins/super-clone-ui/lib/client.js`：`TURN_TAIL_RULES` + `turnTailRecommendations()`（判据不变）+ `weeksirDownloadUrl()` 路径→直链 + 四个 `window.__weeksir*` 注册 |
| 直链来源 | 8100 `/api/film/file?task=&rel=&dl=1`（`model_admin.py`）：`dl=1` ⇒ `Content-Disposition: attachment`；`FILM_INLINE_TYPES`/`FILM_DOWNLOADABLE` 在模块层，是**唯一权威源** |
| 闸 | `scripts/check-weeksir-card-hooks.py` **77 条**（H 真跑纯函数值表 / W 白名单对账含变异对照 / P 官方补丁形状 + "官方文件里不许出现业务规则" / S 不再抢槽）· `scripts/check-film-download.py` **13 条**（真打 8100：attachment / 206 / 416 / 越界 / 未知扩展名 / json 只内联不下载） |

**四个钩子 = 官方与我们的唯一接口**：`__weeksirDownloadUrl(absPath)` · `__weeksirDownload(url)` ·
`__weeksirTurnRecs(matched)` · `__weeksirPickRec(id)`。官方那份每个调用都包 `try`，
**钩子缺席 = 逐字节退回原生** —— 官方组件抛错会打死整棵插件树 = 桌面端崩溃重启循环（2026-10-08 whale-girl 的教训）。

**判据修正（用户点的"1"）**：推荐输入从 `produced` 改成 **`produced ∪ presented`**。
只吃 produced 时**成片回合恒不触发** —— 成片由引擎落盘、agent 显式 `present`，不在 produced 里，
所以 v1 在最该出推荐的那一类回合从来没出现过（"已落码 ≠ 生效"）。

**踩过的坑（别重复）**：① `slots.register` 是**两参数**（options 里带 `select`）；
② 藏官方行不能用兄弟选择器（同槽内先后我们说了不算）；③ 官方 bundle 是 **CRLF**，按 `\n` 切行会让每行末尾挂 `\r`，锚点永远配不上；
④ 打包产物缩进手数必错 ⇒ 补丁脚本改成**按行定位 + 断言唯一 + 不唯一就不落盘**，并且 `node --check` 过语法才认。

## 6. 已知边界（不许 over-claim）

- **`usedSkill` 仍恒传 `""`** ⇒ 按 skill 的那张表备着不用，推荐只由产物类型推。接 run 账本是另一次改动。
- **对话内表单也占这个 chain 槽**（`weeksir-skill-form`，priority -2）⇒ 表单出现的那些回合，官方卡（含刚加的原生下载项）不渲染。
  同一个根因、同一个修法（让表单也别抢槽）；闸里把这条打印成待办，不当已完工。
- **「本轮文件改动」那行现在保留**：v2 不顶掉官方任何一块，推荐是**追加一行**而不是替换。
  这与最初那句"把文件改动那行改成推荐"字面不同；要恢复"替换"就得重新加回藏行逻辑（= 又去动官方 UI），待拍。
- **Electron 会不会真弹"另存为"未实测**：主进程没有 `will-download`/`showSaveDialog`（`out/main/index.js` 只有 `showOpenDialog`，
  preload 里的 `download` 全是自动更新器），按 Electron 默认行为 attachment 下载应弹保存框 ——
  **这是文档口径不是实测口径**，要在 DSH 里真点一次才算证实。
- **生效要完全重启 DSH**：官方 bundle 由常驻 harness 进程 require，`Ctrl+R` 未必换码（没动 `cordis.patch.yml`，不需要走 patch 层）。
- 顺带修掉两个洞（同一条口，`check-film-download.py` 的 S2/S3 钉着）：越界判定以前只看 `rel` 不看 `task`；
  未知扩展名以前回落 `application/octet-stream` 照样发出去 = `productions/` 下**任何**文件都能被读走（含存过明文厂商 key 的状态文件）。
