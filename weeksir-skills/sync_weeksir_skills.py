"""把开发态 .dsh/skills 提升进 weeksir-skills 打包层（路 A）。
发版流水线 build_release 的一步：先跑本脚本同步，再把 weeksir-skills 拷进 App node_modules。

用法:
    python sync_weeksir_skills.py            # 同步（先清 skills/ 再拷）

安全：EXCLUDE 里的运维/内部技能绝不进客户包（见 docs/RELEASE.md §5）。
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

SRC = Path(r"C:\dsh\.dsh\skills")
DST = Path(r"C:\dsh\weeksir-skills\skills")

# 不进客户包的运维/内部技能（按需增删）。留空 = 全部内置。
EXCLUDE = {
    # "product-video-pipeline",  # 若是权威配方且 omni 依赖则保留
}


def main() -> int:
    if not SRC.exists():
        print(f"FAIL: 源不存在 {SRC}")
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for d in sorted(SRC.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "SKILL.md").exists():
            continue  # 只拷真技能目录
        if d.name in EXCLUDE:
            skipped.append(d.name)
            continue
        shutil.copytree(d, DST / d.name)
        copied.append(d.name)
    print(f"已内置 {len(copied)} 个技能 -> {DST}")
    for n in copied:
        print("  +", n)
    if skipped:
        print("排除（不进客户包）:", ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
