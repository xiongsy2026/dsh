#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 skill 执行合同的【两张表】打出来（设计见 docs/ADR-skill-contract.md，判据在 skill_contract.py）。

    python scripts/contract_tables.py <skill> [段目录或工程目录]

- 只有 skill ⇒ 出【计划表】：报价那条消息要带上它（人在同意前就看见要走哪几步、每步授权什么手段）。
- 带目录 ⇒ 再出【执行表】：每一步是谁做的（逐行署名）、什么时候、什么手段、物证与指纹。
  目录里找不到回执 ⇒ 明说"这次不是从声明的入口进来的"，不许拿空表糊过去。
本脚本只读，不建预扣、不花钱、不写盘。
"""
import os
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

from backend import skill_contract as sc                        # noqa: E402


def _emit(s):
    """DSH 的 agent 控制台常是 GBK：✅/⇒ 会当场把打印崩掉（崩了 = 表没给出去，比给错更糟）。
       兜底走字节层，不 reconfigure 全局 stdout。"""
    try:
        print(s)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _emit("用法：python scripts/contract_tables.py <skill> [目录]")
        return 2
    skill, where = argv[0], (argv[1] if len(argv) > 1 else "")
    _emit(sc.plan_table(skill))
    if not where:
        return 0
    rec = sc.latest_receipt(where) if os.path.isdir(where) else {}
    if not rec:
        _emit("\n🔴 没有回执：%s ⇒ 这份产物不是从声明的入口出来的（无表可出）" % where)
        return 1
    _emit("")
    _emit(sc.who_table(rec))
    problems = sc.check_receipt(rec.get("skill", skill), rec,
                                inputs=[i.get("path", "") for i in rec.get("inputs", [])])
    _emit("\n" + (("🔴 未满足：" + "；".join(problems)) if problems else "必需步都有合格证据，无未说明的偏离。"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
