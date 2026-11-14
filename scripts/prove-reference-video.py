#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""钉死一个事实：wan3.0 这条 lane 到底有没有【读】我们送进去的参考视频。

为什么不看"任务 completed"就算数：completed 只证明它跑完了，不证明它照参考段走。
上游真读了 video_urls 的证据形状是 —— 成片与参考段在【同一时刻】的结构相似度，必须显著高于两个对照：
  · 平移对照 shift：同一条参考段、时间挪开 —— 若模型是按参考的时间轴走，错时就该不像
  · 错配对照 cross：换成另一条任务的参考段 —— 若只是"同风格电商片"，跨片也会像，那就不能算证据
没有这两个对照，"像"字不值钱（这跟判"某条腿白跑"要先给恒绿桩是同一条纪律）。

用法：
  python scripts/prove-reference-video.py <成片.mp4> <参考段.mp4> [--cross <另一条参考段.mp4>]
                                          [--samples 8] [--shift 5] [--out <物证json路径>]
0 分、0 上游请求：只在本地抽帧比像素。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

SIZE = (288, 512)          # 统一缩到这个尺寸再比（宽, 高）：消掉 720x960 与 1080x1920 的差异


def _safe(s):
    """按输出流编码丢字，绝不 reconfigure（那会造"零输出 + exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode(enc, "replace").decode(enc, "replace"))


def duration(path: str) -> float:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return float(n) / float(fps) if fps else 0.0


def gray_at(path: str, t: float):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, SIZE)


def score_pair(a: str, b: str, ts, bs, offset: float = 0.0):
    """同一组时间点上的 SSIM 均值/最差值；offset 把【第二条】的时间轴挪开（做平移对照用）。"""
    vals = []
    for t in ts:
        fa = gray_at(a, t)
        fb = gray_at(b, min(max(0.0, t + offset), bs - 0.15))
        if fa is None or fb is None:
            continue
        vals.append(float(ssim(fa, fb, data_range=255)))
    if not vals:
        return None
    return {"n": len(vals), "mean": round(float(np.mean(vals)), 4),
            "min": round(float(np.min(vals)), 4), "max": round(float(np.max(vals)), 4)}


def motion_series(path: str, samples: int = 96, stride: int = 2):
    """整段的【运动能量时间序列】：相邻采样帧的灰度平均绝对差，再归一到 samples 个格。

    为什么用它不用 SSIM：SSIM 比的是像素结构，而复刻本来就把商品/人脸/衣服/配色全换掉 ⇒
    2026-10-21 实测同刻 SSIM 0.275 反而低于跨任务错配 0.419 —— 那条判据在这种题上是失效的，
    拿它下结论就是把"生成不像源片"误读成"没读参考视频"。
    运动能量只看【什么时候在动、动得多大】：运镜与动作节奏的时间形状，与外观无关。
    """
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if n <= stride * 4:
        cap.release()
        return None
    prev, vals = None, []
    idx = range(0, n, stride)
    for i in idx:                                # 按帧号取（POS_FRAMES 比 POS_MSEC 稳）
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (160, 284)), cv2.COLOR_BGR2GRAY).astype("int16")
        if prev is not None:
            vals.append(float(np.mean(np.abs(g - prev))))
        prev = g
    cap.release()
    if len(vals) < 8:
        return None
    x_old = np.linspace(0.0, 1.0, len(vals))
    x_new = np.linspace(0.0, 1.0, samples)
    return np.interp(x_new, x_old, np.asarray(vals))


def corr_best(a, b, max_lag: int = 6):
    """允许小错位后的最大皮尔逊相关（±max_lag 个格）—— 复刻不要求帧级对齐，卡死 0 位移会冤枉它。"""
    if a is None or b is None:
        return None
    best, best_lag = -2.0, 0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:], b[:len(b) - lag] if lag else b
        else:
            x, y = a[:lag], b[-lag:]
        m = min(len(x), len(y))
        if m < 10:
            continue
        x, y = x[:m], y[:m]
        if np.std(x) < 1e-6 or np.std(y) < 1e-6:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best:
            best, best_lag = c, lag
    return None if best < -2.0 else {"corr": round(best, 4), "lag_cells": best_lag}


def selfcheck(path_a: str, path_b: str | None = None) -> dict:
    """仪表自检：同一条片子（或它的重编码）必须拿到接近 1 的相关，否则【不许输出结论】。

    🔴 为什么要有这一步：2026-10-21 我先用 SSIM 判，得到"同刻 0.275 < 跨任务错配 0.419"——
    那是判据失效，不是模型没读参考视频。没有自检，我就会拿一个坏仪表读数去说"机制没生效"，
    然后有人照着这句话去重跑花钱的单。判据自己算错 = 把合格对象判成红（同 PNG 忘滤波字节那次）。
    """
    if not path_b:                      # 没给副本 ⇒ 只能证明「序列取得到」，不算真自检
        ok = motion_series(path_a) is not None
        return {"ok": ok, "corr": None, "note": "只验了序列取得到；要 --selfcheck 给一条重编码才算真自检"}
    c = corr_best(motion_series(path_a), motion_series(path_b))
    v = None if c is None else c["corr"]
    return {"ok": bool(v is not None and v >= 0.95), "corr": v,
            "note": "同一条内容 vs 它的重编码：拿不到 ≥0.95 就说明仪表坏了，此时任何差值都不许当结论"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("film")
    p.add_argument("reference")
    p.add_argument("--cross", default="", help="另一条任务的参考段（错配对照）")
    p.add_argument("--samples", type=int, default=8)
    p.add_argument("--shift", type=float, default=5.0)
    p.add_argument("--out", default="")
    p.add_argument("--selfcheck", default="",
                   help="【参考段】自己的重编码副本。不给 ⇒ 只报数、不下结论（坏仪表读出的差值最害人）")
    a = p.parse_args()

    for f in (a.film, a.reference):
        if not os.path.isfile(f):
            _safe("文件不存在：%s" % f)
            return 2
    da, db = duration(a.film), duration(a.reference)
    span = min(da, db)
    if span < 1:
        _safe("两条里短的不足 1 秒，没法比")
        return 2
    ts = [span * (i + 0.5) / a.samples for i in range(a.samples)]

    same = score_pair(a.film, a.reference, ts, db)
    shifted = score_pair(a.film, a.reference, ts, db, offset=a.shift)
    cross = score_pair(a.film, a.cross, ts, duration(a.cross)) if a.cross and os.path.isfile(a.cross) else None

    mf = motion_series(a.film)
    mr = motion_series(a.reference)
    mc = motion_series(a.cross) if a.cross and os.path.isfile(a.cross) else None
    m_same = corr_best(mf, mr)
    m_cross = corr_best(mf, mc) if mc is not None else None
    m_shuffled = corr_best(mf, np.roll(mr, len(mr) // 2)) if mr is not None else None

    res = {"film": os.path.abspath(a.film), "reference": os.path.abspath(a.reference),
           "film_duration": round(da, 2), "reference_duration": round(db, 2),
           "samples": a.samples, "shift_seconds": a.shift,
           "motion_same": m_same, "motion_cross_task": m_cross, "motion_shuffled": m_shuffled,
           "same_time_ssim": same, "shifted_ssim": shifted, "cross_task_ssim": cross,
           "verdict": "",
           "how": "主判据=整段【运动能量时间序列】的相关（外观无关，只看什么时候在动、动多大）；"
                  "SSIM 一并记录但【不当判据】——2026-10-21 实测它的跨任务错配比同刻还高，"
                  "在这类'换产品换脸'的复刻题上是失效判据。0 上游请求。"}

    def fmt(x):
        return "corr=%s lag=%s" % (x["corr"], x["lag_cells"]) if x else "（没做）"
    _safe("成片 %s (%.2fs) ← 参考 %s (%.2fs)" % (os.path.basename(a.film), da, os.path.basename(a.reference), db))
    _safe("  主判据（运动时序）  同一条参考段 %s ｜ 跨任务错配 %s ｜ 半段错位 %s"
          % (fmt(m_same), fmt(m_cross), fmt(m_shuffled)))
    _safe("  旁证（SSIM，不作判据）同刻 %s ｜ 平移 %.0fs %s ｜ 错配 %s"
          % ((("mean=%.4f" % same["mean"]) if same else "—"), a.shift,
             (("mean=%.4f" % shifted["mean"]) if shifted else "—"),
             (("mean=%.4f" % cross["mean"]) if cross else "—")))

    sc = selfcheck(a.reference, a.selfcheck or None)
    res["selfcheck"] = sc
    _safe("  仪表自检：%s corr=%s ｜ %s" % ("过" if sc["ok"] else "🔴不过", sc["corr"], sc["note"]))

    parts = []
    if not sc["ok"]:
        parts.append("仪表自检没过（或没给 --selfcheck 副本）⇒ 这一轮【只报数、不下结论】。"
                     "坏仪表读出的差值会把人送去重跑花钱的单，那比没结论更坏")
    elif m_same:
        c = m_same["corr"]
        if c >= 0.40 and (m_cross is None or c >= m_cross["corr"] + 0.10):
            parts.append("动作节奏跟着参考段走：同段相关 %.3f，跨任务错配 %s，半段错位 %s ⇒ "
                         "参考视频不只是被下载，是【进了时间轴】"
                         % (c, ("%.3f" % m_cross["corr"]) if m_cross else "—",
                            ("%.3f" % m_shuffled["corr"]) if m_shuffled else "—"))
        elif m_cross is not None and m_cross["corr"] >= c - 0.02:
            parts.append("同段相关 %.3f 与跨任务错配 %.3f 持平 ⇒ 这个数说明不了它照参考段走，"
                         "只能说明'都是竖版带货片的运动形状'" % (c, m_cross["corr"]))
        else:
            parts.append("同段相关只有 %.3f ⇒ 成片没有跟着参考段的动作节奏走（别宣称'按原片复刻'）" % c)
    else:
        parts.append("运动序列取不到（帧数太少？）⇒ 不下结论")
    res["verdict"] = "；".join(parts)
    _safe("  结论：%s" % res["verdict"])

    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.film)), "reference_video_proven.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    _safe("  物证：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
