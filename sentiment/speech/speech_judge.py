#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A2/A3/A4 模型判读+相变+样本(v3.1)。搜索层只负责圈定『在聊股票』,情绪判断交语言模型。

判读通道(三选一,按可用性):
  --export   把 pool/ 里未判读的条目按批导出 pending/batch_NNN.json(交 Claude 会话或API判读)
  --ingest   回收 pending/judged_batch_NNN.json → speech_judged.csv
  --api      若配置 ANTHROPIC_API_KEY,直接批量调用API判读(未配置则提示走export/ingest)
  --aggregate 由 speech_judged.csv 聚合出 speech_daily.csv(计数+20日z+相变+环境标签)
              并生成 speech_samples/YYYYMMDD.md(按信号源权重排序的原话样本)

判读标签 schema(每条):
  polarity ∈ 贪婪/恐惧/投降/嘲讽反讽/中性;intensity ∈ 1-3;confidence ∈ 0-1;
  signals ⊆ {新韭菜入场,晒盈利,晒逃顶,喊单};topic_market ∈ A股/美股/币圈/混合
相变判定(A3):对高置信(≥0.7)贪婪/投降/恐惧计数做20日基线z,
  z>2 且 绝对量>基线×3 且 ≥5条 → 群体级(贪婪群体=顶部红灯,投降群体=底部绿灯)。
边界:模型判读会错(尤其反讽,预期八九成);原话样本+人工抽查兜底。
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd

SP = Path(__file__).resolve().parent
SENT = SP.parent
POOL, PENDING, SAMPLES = SP / "pool", SP / "pending", SP / "speech_samples"
JUDGED = SP / "speech_judged.csv"
DAILY = SP / "speech_daily.csv"
CST = dt.timezone(dt.timedelta(hours=8))
BATCH = 100
JCOLS = ["day", "url", "author", "followers", "engage", "polarity", "intensity",
         "confidence", "signals", "topic_market"]

JUDGE_PROMPT = """你是中文财经言论情绪判读员。对下面每条推特言论逐条判读,输出JSON数组,每条:
{"i":序号,"polarity":"贪婪|恐惧|投降|嘲讽反讽|中性","intensity":1-3,"confidence":0-1,
 "signals":["新韭菜入场"|"晒盈利"|"晒逃顶"|"喊单"](没有则空数组),"topic_market":"A股|美股|币圈|混合"}
判读要点:反讽要按真实立场判(『A股yyds,又绿了』=嘲讽反讽);『我妈都开户了』类=贪婪+新韭菜入场;
投降=认亏离场/卸载软件/再也不玩;不聊行情的闲聊=中性低置信。只输出JSON数组。"""


def status(source, line):
    p = SENT / "_status.txt"
    rows = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {dt.datetime.now(CST).strftime('%Y-%m-%d %H:%M')} | {line}"
    p.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def load_pool():
    recs = []
    for f in sorted(POOL.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").split("\n"):
            if ln.strip():
                recs.append(json.loads(ln))
    return recs


def load_judged():
    if JUDGED.exists():
        return pd.read_csv(JUDGED, dtype={"day": str})
    return pd.DataFrame(columns=JCOLS)


def do_export():
    judged = set(load_judged()["url"])
    todo = [r for r in load_pool() if r["url"] not in judged and r.get("text")]
    PENDING.mkdir(parents=True, exist_ok=True)
    for old in PENDING.glob("batch_*.json"):
        old.unlink()  # 重新导出,避免陈旧批次
    n = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        items = [{"i": j, "url": r["url"], "day": r["day"], "author": r["author"],
                  "followers": r["followers"], "engage": r["engage"], "text": r["text"][:400]}
                 for j, r in enumerate(batch)]
        (PENDING / f"batch_{i // BATCH:03d}.json").write_text(
            json.dumps({"prompt": JUDGE_PROMPT, "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        n += 1
    print(f"导出{len(todo)}条待判 → {n}个批次(pending/batch_*.json);"
          f"判读后以 judged_batch_*.json 回收(--ingest)")
    status("speech_judge", f"EXPORT 待判{len(todo)}条/{n}批")


def do_ingest():
    dfs = [load_judged()]
    got = 0
    for jf in sorted(PENDING.glob("judged_batch_*.json")):
        src = PENDING / jf.name.replace("judged_", "")
        if not src.exists():
            continue
        items = {it["i"]: it for it in json.loads(src.read_text(encoding="utf-8"))["items"]}
        rows = []
        for j in json.loads(jf.read_text(encoding="utf-8")):
            it = items.get(j.get("i"))
            if not it:
                continue
            rows.append({
                "day": it["day"], "url": it["url"], "author": it["author"],
                "followers": it["followers"], "engage": it["engage"],
                "polarity": j.get("polarity", "中性"),
                "intensity": int(j.get("intensity") or 1),
                "confidence": float(j.get("confidence") or 0),
                "signals": ";".join(j.get("signals") or []),
                "topic_market": j.get("topic_market", "混合"),
            })
        got += len(rows)
        dfs.append(pd.DataFrame(rows, columns=JCOLS))
    df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["url"], keep="last")
    df = df.sort_values(["day", "url"]).reset_index(drop=True)
    JUDGED.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(JUDGED, index=False, encoding="utf-8-sig")
    print(f"回收{got}条判读,speech_judged.csv 现{len(df)}条")
    status("speech_judge", f"INGEST +{got}条,累计{len(df)}条")


def source_tier(row, sentinels):
    if str(row["author"]).lower() in sentinels:
        return 0, "哨兵"
    if (row["followers"] or 0) >= 50000:
        return 1, "大V"
    if (row["engage"] or 0) >= 100:
        return 2, "高互动"
    return 3, "普通"


def do_aggregate():
    import sys
    sys.path.insert(0, str(SENT))
    from regime import tag

    df = load_judged()
    if not len(df):
        print("speech_judged.csv 为空,先 --export/--ingest")
        return
    slp = SENT / "sentinel" / "sentinel_list.json"
    sentinels = set()
    if slp.exists():  # 哨兵名单已按用户决定移除;存在时才启用"哨兵"分层
        slist = json.loads(slp.read_text(encoding="utf-8"))
        sentinels = {s["handle"].lower() for s in slist["sentinels"]}

    # 民调计数剔除营销喊单(拉客广告≠散户情绪;明细与样本仍保留可查)
    df["_ad"] = df["signals"].astype(str).str.contains("喊单")
    hi = df[(df["confidence"] >= 0.7) & (~df["_ad"])]
    days = sorted(df["day"].unique())
    out = []
    hist = {p: {} for p in ("贪婪", "恐惧", "投降", "嘲讽反讽", "中性")}
    for d in days:
        sub = hi[hi["day"] == d]
        for pol in hist:
            hist[pol][d] = int((sub["polarity"] == pol).sum())
    for d in days:
        market, regime = tag(d)
        for pol in ("贪婪", "恐惧", "投降", "嘲讽反讽", "中性"):
            series = pd.Series(hist[pol]).sort_index()
            past = series[series.index < d].tail(20)
            x = series[d]
            if len(past) >= 5 and past.std(ddof=0) > 0:
                z = (x - past.mean()) / past.std(ddof=0)
            else:
                z = float("nan")
            phase = ""
            if pol in ("贪婪", "投降", "恐惧") and pd.notna(z):
                if z > 2 and x > past.mean() * 3 and x >= 5:
                    phase = "群体级"
                elif z > 1:
                    phase = "抬升"
            out.append([d, pol, x, round(z, 2) if pd.notna(z) else "", phase, market, regime])
    pd.DataFrame(out, columns=["date", "polarity", "high_conf_count", "zscore_20d",
                               "phase", "market", "regime"]).to_csv(DAILY, index=False, encoding="utf-8-sig")
    print(f"speech_daily.csv: {len(days)}日 × 5极性")

    # A4 样本上桌:按信号源权重排序
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for d in days:
        sub = df[(df["day"] == d) & (df["polarity"] != "中性")].copy()
        if not len(sub):
            continue
        tiers = sub.apply(lambda r: source_tier(r, sentinels), axis=1)
        sub["tier"], sub["tier_name"] = [t[0] for t in tiers], [t[1] for t in tiers]
        sub["score"] = sub["confidence"] * sub["intensity"]
        market, regime = tag(d)
        L = [f"# 言论样本 {d}(市场:{market} 体制:{regime})",
             "", "> 排序:哨兵>大V>高互动>普通;原话是主菜,分数是注脚。判读会错(尤其反讽),终审在人。", ""]
        for tier in range(4):
            tsub = sub[sub["tier"] == tier].sort_values("score", ascending=False).head(5)
            if not len(tsub):
                continue
            L.append(f"## {tsub.iloc[0]['tier_name']}")
            for _, r in tsub.iterrows():
                sigs = str(r["signals"]);  sig = f" 🚩{sigs}" if sigs not in ("", "nan") else ""
                L.append(f"- **{r['polarity']}×{r['intensity']}**(置信{r['confidence']:.2f},"
                         f"{r['topic_market']}){sig} @{r['author']} [原文]({r['url']})")
            L.append("")
        (SAMPLES / f"{d.replace('-', '')}.md").write_text("\n".join(L), encoding="utf-8")
    status("speech_judge", f"AGGREGATE {len(days)}日聚合+样本完成")


def do_api():
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("未配置ANTHROPIC_API_KEY;请走 --export → Claude会话判读 → --ingest 通道")
        return
    print("API通道预留:配置密钥后可实现全自动;当前版本请用export/ingest。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=['export', 'ingest', 'aggregate', 'api'],
                    nargs="?", default="aggregate")
    a = ap.parse_args()
    {"export": do_export, "ingest": do_ingest,
     "aggregate": do_aggregate, "api": do_api}[a.mode]()
