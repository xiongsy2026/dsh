# 超级复刻管线（从 video-skill 搬运）

源码：`D:\video-skill\backend\services\super_clone_wan3.py`  
页面：`http://127.0.0.1:8090/super-clone-wan3`

## 步骤

| 步 | 名称 | 做什么 |
| --- | --- | --- |
| 0 | 预处理 | 视频存在、ffprobe 时长、可选 trim、锁 `target_duration`、固定 media_manifest 图N |
| 1 | MiMo | 原视频 → canonical storyboard JSON（只解析，不拼 prompt） |
| 1.5 | 迁移编译器 | storyboard + 产品图/脸图 → Wan3 prompt。合同见 `compiler-contract.md` |
| 2 | Layer 2 Linter | 只修客观错误（物理文本、图N 越界/错绑）。宁漏勿改。见 `linter-contract.md` |
| 3 | Wan 3.0 | 参考视频（该段原片）+ 图 + prompt → 成片 |
| 后 | 难单拼接 | 各段按原窗口裁切，原声音轨主时钟贴回 |

## 前端默认

- 原片不限时长，>15s 自动 15s 切段拼接
- 产品图必选 1–10
- 产品名必填
- 换脸：上传面部图；只取脸/发型
- 换背景：文字，不上传背景图
- 成片默认无字幕、无花字、无账号水印

## 入口校验

- 最短裁剪 1s；超级复刻不强制单段 ≤15s（超长由 `plan_15s_windows` 切）
- 尾段短于 2s 时向前一段借时
- `product_name` 空则拒绝

## 多脸

代码已支持 `extra_model_image_paths`。Agent 难单不要把多张脸一次塞进整段 prompt。按 TIMELINE 每段只传一张脸。
