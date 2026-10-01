# weeksir-skills — WEEKSIR 内置技能包（打包层）

把开发态 `C:\dsh\.dsh\skills` 里的技能**提升成随 App 分发的打包层**，对应 `docs/RELEASE.md` §2「路 A」。

## 为什么需要它
DSH 技能有两层（app 代码坐实：`join(projectRoot, ".dsh/skills")`）：
- **工作区层**：`<工程根>/.dsh/skills` —— 你开发时改这里，打开 C:\dsh 即生效、不用重打包。
- **打包层**：`node_modules/<pkg>/skills`（如官方 `dsh-image-generation/skills`）—— 随安装包发、随 App 发版更新。

客户机没有 `C:\dsh`，所以**要发给客户的技能必须在打包层**。本包就是 Weeksir 的打包层载体。

## 结构
```
weeksir-skills/
  package.json        # dsh-plugin bundle 声明（files:["skills"], dsh.bundle.patch）
  cordis.patch.yml    # 把本包 insert 进 profile bundles
  index.js            # 无副作用入口（纯技能载体）
  sync_weeksir_skills.py  # 把 .dsh/skills 拷进 skills/（EXCLUDE 控运维技能不外泄）
  skills/             # 由 sync 生成，勿手改（.gitkeep 占位）
```

## 用法（发版流水线里的一步）
```
python sync_weeksir_skills.py     # 1. 提升：.dsh/skills -> skills/
# 2. build_release.py 把整个 weeksir-skills/ 拷进 App 的 node_modules（去 junction、真复制）
#    并把 weeksir-skills 写进 profile bundles + pnpm install + 重打包成 setup.exe
```
详见 `docs/RELEASE.md` §3。

## ⚠️ 待确认（落地前必查）
1. **纯技能包（无 client 逻辑）会不会被自动扫到 skills/**：官方 `dsh-image-generation` 是"技能+客户端插件"合体，本包是纯技能载体。若发现 bundle 必须提供 `apply(ctx)` 才加载 skills，就在 `index.js`/新增 `client.js` 补一个空 Cordis 注册（照 dsh-image-generation）。
2. **profile bundles 装配**：`cordis.patch.yml` 的 insert 是否足以让 harness 读本包，还是要像插件那样进 `dsh.profile.bundles` + junction/pnpm（见 [[dsh-stage-ui-architecture]] 部署链）。build_release 阶段实测。
3. **技能可见性/账号隔离**：本包是"全部客户同一份"。按账号裁剪技能属 3.0 网关层，静态包做不到。

## 安全
`sync_weeksir_skills.py` 的 `EXCLUDE` 必须挡住运维/内部技能（product-video-pipeline 若是内部权威配方需评估）；密钥/管线本就不在 skills 里，别拷进去。
