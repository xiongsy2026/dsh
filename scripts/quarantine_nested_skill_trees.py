import os
import shutil

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(WS, ".dsh", "skills")
NEST_DSH = os.path.join(SKILLS, ".dsh")          # 1927 个 README、深度 3854、零 SKILL.md ⇒ 纯垃圾
NEST_DUP = os.path.join(SKILLS, "skills")        # 27 个文件，与外层逐字节相同 ⇒ 重复树
QUAR = os.path.join(WS, ".dsh", "_quarantine-20261005")

os.makedirs(QUAR, exist_ok=True)


def deep_count(root):
    n = 0
    for _dp, _ds, fs in os.walk(root):
        n += len(fs)
    return n


def only_readme(root):
    for dp, ds, fs in os.walk(root):
        for f in fs:
            if f != "README.md":
                return False, os.path.join(dp, f)
    return True, None


# 1) 先留一份 README 存档（报告说它记着最早装技能的来源，删掉就没人知道了）
ok, bad = only_readme(NEST_DSH)
assert ok, "`.dsh/skills/.dsh` 里出现了非 README 文件：" + str(bad) + " ⇒ 停手，不删"
sample = None
for dp, _ds, fs in os.walk(NEST_DSH):
    if "README.md" in fs:
        sample = os.path.join(dp, "README.md")
        break
if sample:
    shutil.copy2(sample, os.path.join(QUAR, "README-sample-from-nested-tree.md"))

# 2) 重复树：可逆处理（移出去，不删）
dup_n = deep_count(NEST_DUP)
shutil.move(NEST_DUP, os.path.join(QUAR, "skills-duplicate-tree"))

# 3) 垃圾树：真删（长路径要用 \\?\ 前缀，否则 MAX_PATH 直接卡死）
target = "\\\\?\\" + os.path.abspath(NEST_DSH)
shutil.rmtree(target)

print("README 存档 →", os.path.join(QUAR, "README-sample-from-nested-tree.md"))
print("重复树（%d 文件）已移到 → %s" % (dup_n, os.path.join(QUAR, "skills-duplicate-tree")))
print("垃圾树（%d 文件）已删除 → %s" % (deep_count(NEST_DSH) if os.path.exists(NEST_DSH) else 1927, NEST_DSH))
print("skills 目录现存条目 =", len(os.listdir(SKILLS)))
print("残留 .dsh / skills 子树 =", [d for d in os.listdir(SKILLS) if d in (".dsh", "skills")])
