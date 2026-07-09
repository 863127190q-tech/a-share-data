#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F2 哨兵基线与转向检测(v3.1,最高优先级层)。
本质:盯固定的高信号源等【转向】——信号在纵向变化里,不在横截面总量里。
『留几手开始FOMO AI』『平时不聊股票的人晒开户』就是要捕捉的事件;哨兵沉默也是读数。

模式:
  --fetch    拉取 active/proposed 哨兵的时间线(twitterapi from:handle 窗口搜索,量小费用低)
             → timelines/{handle}.jsonl;窗口用 SINCE_BJ/UNTIL_BJ 环境变量(默认近35天)
  --export   导出未判读时间线条目 → pending_sentinel/batch_*.json(交Claude会话判读)
             判读schema: {"i","topic":"股票|币圈|其他","stance":"看多|看空|中性","fomo":0-3}
  --ingest   回收 judged_batch_*.json → timeline_judged.csv
  --detect   对每哨兵建基线(检测日前30天话题分布+情绪均值),对目标窗口逐日检测:
             转向(基线股票话题占比<15% 且 当日≥2条股票话题)
             跳变(fomo均值较基线跳升≥1.5 且 当日≥2条)
             晒单/破圈由判读signals位直接触发
             → sentinel_events.csv(date,handle,type,event,from_to,confidence,url,market,regime)
权重原则:一条哨兵转向事件 ≈ 民调层一次相变的信号量级,日报单列不与计数混算。
边界:名单冷启动注定粗糙;哨兵停更/删推如实标注;只读公开内容、只存必要字段。
"""
import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import pandas as pd

SD = Path(__file__).resolve().parent
SENT = SD.parent
TL, PENDING = SD / "timelines", SD / "pending_sentinel"
TJUDGED = SD / "timeline_judged.csv"
EVENTS = SD / "sentinel_events.csv"
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = dt.timezone(dt.timedelta(hours=8))
BATCH = 100
JCOLS = ["handle", "day", "url", "text_head", "topic", "stance", "fomo", "signals"]

JUDGE_PROMPT = """你是哨兵推文判读员。对每条推文判读该作者当时的状态,输出JSON数组,每条:
{"i":序号,"topic":"股票|币圈|其他","stance":"看多|看空|中性","fomo":0-3,
 "signals":["晒单"|"晒开户"|"喊单"|"认亏"](没有则空数组)}
fomo刻度:0=无涉市场,1=平静提及,2=兴奋/焦虑参与,3=重仓宣言/梭哈/错过恐慌。只输出JSON数组。"""


def status(source, line):
    p = SENT / "_status.txt"
    rows = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {dt.datetime.now(CST).strftime('%Y-%m-%d %H:%M')} | {line}"
    p.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def sentinels(include_proposed=True):
    data = json.loads((SD / "sentinel_list.json").read_text(encoding="utf-8"))
    ok = ("active", "proposed") if include_proposed else ("active",)
    return [s for s in data["sentinels"]
            if s["status"] in ok and re.fullmatch(r"[A-Za-z0-9_]{2,15}", s["handle"] or "")]


def parse_created(s):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    return None


def do_fetch():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    if not key or not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        status("sentinel_fetch", "SKIP 未配置TWEET_API_KEY")
        return
    now = dt.datetime.now(CST)
    since = os.environ.get("SINCE_BJ") or (now - dt.timedelta(days=35)).strftime("%Y-%m-%d %H:%M")
    until = os.environ.get("UNTIL_BJ") or now.strftime("%Y-%m-%d %H:%M")
    s0 = dt.datetime.strptime(since, "%Y-%m-%d %H:%M").replace(tzinfo=CST)
    u0 = dt.datetime.strptime(until, "%Y-%m-%d %H:%M").replace(tzinfo=CST)
    import requests
    TL.mkdir(parents=True, exist_ok=True)
    total, cost_n = 0, 0
    for s in sentinels():
        h = s["handle"]
        path = TL / f"{h}.jsonl"
        recs = {}
        if path.exists():
            for ln in path.read_text(encoding="utf-8").split("\n"):
                if ln.strip():
                    r = json.loads(ln)
                    recs[r["url"].rsplit("/", 1)[-1]] = r
        query = f"from:{h} since_time:{int(s0.timestamp())} until_time:{int(u0.timestamp())}"
        cursor, pages = "", 0
        while pages < int(os.environ.get("MAX_PAGES_PER_SENTINEL", "15")):
            if pages or total:
                time.sleep(6)
            params = {"query": query, "queryType": "Latest"}
            if cursor:
                params["cursor"] = cursor
            for _a in range(4):
                r = requests.get(API, params=params, headers={"X-API-Key": key}, timeout=30)
                if r.status_code != 429:
                    break
                time.sleep(12)
            if r.status_code != 200:
                status("sentinel_fetch", f"PARTIAL {h}: HTTP{r.status_code}")
                break
            data = r.json()
            pages += 1
            for t in data.get("tweets") or []:
                cost_n += 1
                tid = str(t.get("id") or "")
                created = parse_created(t.get("createdAt"))
                if not tid or created is None:
                    continue
                recs[tid] = {
                    "ts": created.astimezone(CST).isoformat(timespec="seconds"),
                    "url": f"https://x.com/{h}/status/{tid}",
                    "text": re.sub("[  \x85\x0b\x0c\r]", "\n", t.get("text") or "").strip(),
                }
            cursor = data.get("next_cursor") or ""
            if not data.get("has_next_page") or not cursor or not data.get("tweets"):
                break
        with open(path, "w", encoding="utf-8") as f:
            for tid in sorted(recs):
                f.write(json.dumps(recs[tid], ensure_ascii=False) + "\n")
        total += len(recs)
        print(f"{h}: 时间线{len(recs)}条", flush=True)
    status("sentinel_fetch", f"OK {len(sentinels())}哨兵 时间线计{total}条 本次拉{cost_n}条 费用约${cost_n*0.00015:.3f}")


def load_judged():
    if TJUDGED.exists():
        return pd.read_csv(TJUDGED, dtype={"day": str})
    return pd.DataFrame(columns=JCOLS)


def do_export():
    judged = set(load_judged()["url"])
    todo = []
    for f in sorted(TL.glob("*.jsonl")):
        h = f.stem
        for ln in f.read_text(encoding="utf-8").split("\n"):
            if ln.strip():
                r = json.loads(ln)
                if r["url"] not in judged and r.get("text"):
                    todo.append({"handle": h, **r})
    PENDING.mkdir(parents=True, exist_ok=True)
    for old in PENDING.glob("batch_*.json"):
        old.unlink()
    n = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        items = [{"i": j, "handle": r["handle"], "url": r["url"], "day": r["ts"][:10],
                  "text": r["text"][:400]} for j, r in enumerate(batch)]
        (PENDING / f"batch_{i // BATCH:03d}.json").write_text(
            json.dumps({"prompt": JUDGE_PROMPT, "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        n += 1
    print(f"导出{len(todo)}条哨兵推文待判 → {n}批")
    status("sentinel_judge", f"EXPORT 待判{len(todo)}条/{n}批")


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
                "handle": it["handle"], "day": it["day"], "url": it["url"],
                "text_head": it["text"][:60].replace("\n", " ").replace(",", ";"),
                "topic": j.get("topic", "其他"), "stance": j.get("stance", "中性"),
                "fomo": int(j.get("fomo") or 0), "signals": ";".join(j.get("signals") or []),
            })
        got += len(rows)
        dfs.append(pd.DataFrame(rows, columns=JCOLS))
    df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["url"], keep="last")
    df.sort_values(["handle", "day"]).to_csv(TJUDGED, index=False, encoding="utf-8-sig")
    print(f"回收{got}条,timeline_judged.csv 现{len(df)}条")
    status("sentinel_judge", f"INGEST +{got}条,累计{len(df)}条")


def do_detect():
    import sys
    sys.path.insert(0, str(SENT))
    from regime import tag

    df = load_judged()
    if not len(df):
        print("timeline_judged.csv 为空")
        return
    df["is_stock"] = df["topic"] == "股票"
    events = []
    for h, sub in df.groupby("handle"):
        sub = sub.sort_values("day")
        for d in sorted(sub["day"].unique()):
            day_rows = sub[sub["day"] == d]
            base = sub[(sub["day"] < d) &
                       (sub["day"] >= (dt.date.fromisoformat(d) - dt.timedelta(days=30)).isoformat())]
            if len(base) < 5:
                continue  # 基线不足,不判(冷启动期沉默)
            base_stock_share = base["is_stock"].mean()
            base_fomo = base[base["fomo"] > 0]["fomo"].mean() if (base["fomo"] > 0).any() else 0
            day_stock = day_rows[day_rows["is_stock"]]
            # 转向:基线几乎不聊股票 → 当日≥2条股票话题
            if base_stock_share < 0.15 and len(day_stock) >= 2:
                events.append([d, h, "转向", f"股票话题占比 {base_stock_share:.0%}→当日{len(day_stock)}条",
                               0.8, day_stock.iloc[0]["url"]])
            # 跳变:fomo均值跳升
            if len(day_stock) >= 2:
                day_fomo = day_stock["fomo"].mean()
                if day_fomo - base_fomo >= 1.5:
                    events.append([d, h, "跳变", f"fomo {base_fomo:.1f}→{day_fomo:.1f}",
                                   0.7, day_stock.iloc[0]["url"]])
            # 晒单/晒开户/喊单(判读信号位直接触发)
            for _, r in day_rows.iterrows():
                for sig in str(r["signals"]).split(";"):
                    if sig in ("晒单", "晒开户", "喊单"):
                        events.append([d, h, "破圈" if sig == "晒开户" else sig,
                                       f"signals={sig}", 0.75, r["url"]])
    slist = {s["handle"]: s["type"] for s in sentinels()}
    out = []
    for d, h, ev, ft, conf, url in events:
        market, regime = tag(d)
        out.append([d, h, slist.get(h, "?"), ev, ft, conf, url, market, regime])
    cols = ["date", "handle", "type", "event", "from_to", "confidence", "url", "market", "regime"]
    pd.DataFrame(out, columns=cols).drop_duplicates(
        subset=["date", "handle", "event", "url"]).sort_values(["date", "handle"]).to_csv(
        EVENTS, index=False, encoding="utf-8-sig")
    print(f"sentinel_events.csv: {len(out)}事件")
    status("sentinel_detect", f"OK 事件{len(out)}条(基线≥5条才判,不足即沉默)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=['fetch', 'export', 'ingest', 'detect'],
                    nargs="?", default="detect")
    a = ap.parse_args()
    {"fetch": do_fetch, "export": do_export,
     "ingest": do_ingest, "detect": do_detect}[a.mode]()
