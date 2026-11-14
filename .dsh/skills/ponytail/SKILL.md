---
name: ponytail
description: "Lazy-senior-dev code discipline (adapted from ponytail, MIT): before writing any code, stop at the first rung that holds — skip it (YAGNI) / reuse what the codebase already has / stdlib / native platform feature / installed dependency / one line / only then the minimum that works. Cuts over-engineering in scripts, plugins, glue code and checks, while never cutting trust-boundary validation, data-loss handling, security, accessibility, or anything explicitly requested. Use when the user says '/ponytail', '极简实现', '别过度设计', '最小改动', or when about to write/modify code in this workspace. NOT for prompt engineering or creative deliverables (seedance-*, film-*, short-drama): the shortest prompt is the wrong goal there."
whenToUse: 显式 /ponytail 调用，或明确要"最小代码 / 别过度设计 / 把方案收一收"时。作用域只有**写与改代码**（scripts、plugins、管线胶水、check 工具）。提示词工程、剧本/文案/分镜等创作面不走本 skill；交付链文档（productions 四件套、质量闸、溯源）永远不在削减清单上。
---

# ponytail（懒惰高级工程师·写代码纪律）

显式调用的**写代码规则集**，改编自 [ponytail](https://github.com/DietrichGebert/ponytail)（MIT）。
它只决定**代码怎么写**：不改任何 skill 的流程、不动交付链、不碰提示词与创作。

## 七级阶梯（写代码前，停在第一个成立的）

1. 这东西需要存在吗？不需要 → 不写（YAGNI）
2. 代码库里已经有了？→ 复用现有 helper / util / 模式，不重写
3. 标准库能做？→ 用标准库
4. 平台原生特性能做？→ 用原生
5. 已装依赖能做？→ 用现成依赖
6. 一行能写？→ 就写一行
7. 以上都不行 → 写最小可用实现

阶梯是**读懂问题之后**再爬，不是省掉理解：先把要改的代码读了、真实调用链走一遍，再选档。
"在错的地方做最小改动"不是懒，是第二个 bug。

## 硬规则

- 不做没被要求的抽象；不加没人要的样板；不引可避免的依赖。删除优于新增，无聊优于聪明，文件数越少越好。
- 复杂请求要反问一句："真需要 X 吗，Y 够不够？"
- **修 bug = 修根因不修症状**：grep 该函数的**所有调用方**，在共享函数里修一次；只修工单点名的那条路径 = 兄弟调用方还坏着。
- 两个标准库方案一样大时，选边界情况正确的那个——懒是代码少，不是算法脆。
- **刻意偷懒要留痕**：明知有上限的简化（全局锁、O(n²) 扫描、朴素启发式）就地写 `ponytail:` 注释，写明上限和升级路径。
- **非平凡逻辑必须留一个可跑的最小检查**（一个 assert 自检或一个小检查脚本；不上框架、不做 fixture）——与本工作区 `scripts/check-*.py` 回归闸同一文化。一行琐事不用测。

## 🔴 豁免清单（这些不是 boilerplate，永远不许砍）

| 豁免对象 | 为什么 |
|---|---|
| `productions/<task>/` 交付四件套（BRIEF / ANALYSIS / TIMELINE / PROGRESS）与 `LOOP.md` | 状态机中枢 + 人审凭证，砍了交付链就断 |
| `docs/` 合同 / PRD / ADR | "先定框架再实现"的框架本体 |
| `scripts/check-*.py` 回归闸 | 踩过的坑的钉子，防复发 |
| `.dsh/skills/*/SKILL.md` 合同文件 | 说明书即运行时代码 |
| 素材溯源、口径 json、意图登记（`.dsh/intent.json`） | 合规与审计链 |
| 信任边界校验、防数据丢失的错误处理、安全、无障碍、用户显式要求的东西 | 原作者红线，同名同义 |

## 不适用（本 skill 在这些场景直接退场）

- **提示词工程**（seedance-* 全系、omni / super-clone / hypit 的提示词）——"一行搞定"对画质和创意是反效果，提示词按提示词库的写法写足。
- **创作交付**（剧本、故事、分镜、文案、短剧）——质量优先于字数。
- 出片/成片流程本身：各 skill 的合同说了算；ponytail 只影响**你写代码**那一段。

## 边界检查

动过 SKILL.md 或做过"大扫除"之后跑：

```
python scripts/check-ponytail.py
```

豁免对象必须原样在盘上，缺一即 FAIL。
