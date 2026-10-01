# WEEKSIR Desktop 发版流程蓝本（RELEASE）

状态：蓝本（未落地）｜日期：2026-10-01｜负责人：产品/运维
用途：把"本地开发态"变成"能发给客户、能自动更新"的成品。当前**尚未有可分发安装包产物**，本文是搭 `build_release.py` 的施工图。

## 0. 现状（grounded，别自欺）
- **改造脚本**：`_setup/rebuild_weeksir_desktop.py` = 在**本机**装官方 pin 版 installer（`dsh-desktop-windows-x64-setup.exe`）→ **就地打补丁**（品牌/预设/harness/super-clone-ui 瘦身）。脚本第 189 行自述：**"keep auto-update disabled; patches are pinned to this build."** → 现在**自动更新是关的**（官方一更新就冲掉 Weeksir 补丁）。
- **技能两层**（app 代码坐实）：
  - 工作区层：`join(projectRoot, ".dsh/skills")` —— 从打开的工程根读。Weeksir 技能（omni/hypit/super-clone/weeksir/session-bind/…）在 `C:\dsh\.dsh\skills`，**只因你打开了 C:\dsh 才生效**。
  - 打包层：`node_modules/dsh-image-generation/skills`、`dsh-ppt/skills` 等**技能包**（package.json `files:["skills"]` + `dsh-plugin`），**随 App 发、随 App 更新**。
- **插件**：`C:\dsh\plugins\{super-clone-ui,ops-gateway}` 靠 **junction 链**进 app 的 node_modules（见部署链）。客户机没有 `C:\dsh` → junction 断。
- **结论**：现在的"产品" = 官方 App + 你本地工作区(skills) + 你本地 plugins(junction)。三者只有官方 App 会被装到客户机，**技能和插件根本不在分发包里**。

## 1. 发版必须解决的 4 件事
1. 产出物从"就地改造已装 App"→"**一个含 Weeksir 改造 + 内置技能 + 内置插件的成品 setup.exe**"。
2. 技能从**工作区层提升到打包层**（随包发、随包更新）。
3. 插件**去 junction、真复制进包**。
4. **代码签名 + 更新源**（electron-updater），且更新只走"你重打包的成品"、禁官方 auto-update feed。

## 2. 技能内置的两条路（选一条）
- **路 A（推荐·已被官方验证）**：仿 `dsh-image-generation`，做一个 **`weeksir-skills` 技能包**（`package.json` 里 `files:["skills"]` + `dsh-plugin` + `dsh.bundle.patch`），把 `.dsh/skills/*` 放进去，装进 app 的 node_modules。→ 随包发、随包更新，机制和官方内置技能一模一样。
- **路 B（需先确认）**：build 时把 `C:\dsh\.dsh\skills` 拷进 app 的某个**全局技能层目录**。⚠️ 前提=app 除 `projectRoot/.dsh/skills` 外还扫一个全局层——**此点未证实**，走 B 前先去 app 配置确认有没有"用户级/全局 skills 目录"。没有就走 A。
- **开发态不变**：平时仍改工作区 `.dsh/skills`（改完即时生效、不重打包，快）；**发版时**才把技能提升进 `weeksir-skills` 包。

## 3. 目标发版流水线（`build_release.py` 蓝本）
```
输入：C:\dsh 定制层（agents.md / .dsh/skills / plugins / _setup patches）+ 官方 pin installer
 0. 干净环境：跑官方 installer /S（现有 rebuild 的 --install）
 1. 现有 patch 链（apply_weeksir_desktop_brand / patch_inapp_weeksir / patch_reap_harness
    / patch_harness_stability / slim_ui_plugin / check_brand_patch / trim_presets）
 2. 内置插件：把 plugins/super-clone-ui（客户面）真拷进 resources/app/node_modules，
    写进 profile bundles + .package-map.json（去 junction）；ops-gateway(运维) 不进客户包
 3. 内置技能：把 .dsh/skills 打成 weeksir-skills 包（路 A）拷进 node_modules
 4. 密钥/隐私扫描：确保 .env / model_admin / 管线 / doubao_profile / cookies 不在包内
 5. 重打包：electron-builder（或 rcedit 改 icon + 重签）→ WEEKSIR-Setup-<ver>.exe
 6. 代码签名（证书）→ 否则客户机 SmartScreen 拦
 7. 产出更新件：<ver> 的 setup.exe + latest.yml（electron-updater generic feed）
 8. 上传到更新源（自建静态/CDN/对象存储）
输出：签名安装包 + 更新清单，客户机 App 查更 → 拉新版 → 重启即更（含新技能/插件）
```
版本规则：App 版本 = 语义化 `x.y.z`；每次发版 `latest.yml` 指向最新。技能/agents.md 的变更**攒进一次发版**，不必一改一发。

## 4. 自动更新与补丁冲突（关键）
- 官方 auto-update 会覆盖你的就地补丁 → **必须禁官方更新源**，更新只从"你重打包的成品"走（步骤 7-8）。
- 内核升级：`_setup/check_upstream.py`（监控官方 tag）+ `verify_kernel_consistency.py`（版本一致性闸）。官方出新版 → 先在本地对新版**重跑整条 patch 链 + 重打包** → 通过一致性闸 → 再作为新 `x.y.z` 发。

## 5. 安全红线（沿用既有约定）
- **密钥绝不进包/进技能**（走服务端/`.env`/网关，见 [[model-gateway-direction]]）。
- **运维面/管线/model_admin 不进客户包**（见 [[dsh-client-release-shaping]]）：ops-gateway、`_setup` 脚本、`video_runs`、`doubao_profile`、cookies 一律排除。
- 按账号/套餐控制技能可见性 → 静态包做不到，属 3.0 网关层。

## 6. 分阶段（别一次到位）
- **现在（自用/内测）**：继续用 `rebuild_weeksir_desktop.py` 就地装；给同事 = 拷改造后的 app 目录或手装一遍。够用。
- **要给外部客户**：**必须先建 §3 的 build_release + §1.4 签名 + 更新源**——这才是"发版流水线"的真实工作量。
- **3.0**：agent + 技能上服务端 → 客户端壳几乎不用发版，更新=改服务端（见 [[dsh-product-roadmap]]）。

## 7. 待确认 / 待办
- [ ] 确认 app 是否扫 `projectRoot` 之外的**全局技能层**（决定 §2 走 A 还是 B）。
- [ ] 代码签名证书（采购/配置）。
- [ ] 更新源托管位置（对象存储/CDN/自建静态）。
- [ ] `weeksir-skills` 包骨架（路 A）+ profile bundles 登记。
- [ ] `.gitignore` 复核：`_tmp_*.py`、`video_runs/`、`*.env`、`doubao_profile/`、cookies、`*.zip` 绝不进包/进库。

## 8. 与技能展示 dock 的关系
dock（见 [[PRD-skill-showcase]] / `docs/PRD-skill-showcase.md`）读 `ctx.remote.skills.list` = 上面两层合并后的技能清单。所以**内置技能提升到打包层后，dock/路由/执行三处同时生效**（一份 SKILL.md 单一来源）。
