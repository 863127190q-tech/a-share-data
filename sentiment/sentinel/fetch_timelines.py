#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
哨兵自动发现 · 第2步:候选账号时间线抓取(供战绩回测)
对每个市场 candidates_{市场}.csv 的前 N 名,抓取评测窗口内的推文。
产物: sentiment/sentinel/timelines/{市场}/{handle}.jsonl  每行 {ts,url,text}
成本: 每条约$0.00015;脚本打印并记账到 _status.txt。
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

SD = Path(__file__).resolve().parent
SENT = SD.parent
TL = SD / "timelines"
CFG = json.loads((SD / "markets.json").read_text(encoding="utf-8"))
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = dt.timezone(dt.timedelta(hours=8))
MAX_PAGES = int(os.environ.get("MAX_PAGES_PER_ACCOUNT", "8"))


def status(source, line):
    p = SENT / "_status.txt"
    now = dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    rows = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {now} | {line}"
    p.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def parse_created(s):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    return None


def read_candidates(market, top_n):
    p = SD / f"candidates_{market}.csv"
    if not p.exists():
        return []
    out = []
    for i, ln in enumerate(p.read_text(encoding="utf-8-sig").splitlines()):
        if i == 0 or not ln.strip():
            continue
        parts = ln.split(",")
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9_]{2,15}", parts[0]):
            out.append(parts[0])
        if len(out) >= top_n:
            break
    return out


def fetch_one(key, handle, s0, u0):
    import requests
    path_dir = None
    recs = {}
    query = f"from:{handle} since_time:{int(s0.timestamp())} until_time:{int(u0.timestamp())}"
    cursor, pages, got = "", 0, 0
    while pages < MAX_PAGES:
        if pages:
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
            return recs, got, f"HTTP{r.status_code}"
        data = r.json()
        pages += 1
        tws = data.get("tweets") or []
        for t in tws:
            got += 1
            if (t.get("text") or "").startswith("RT @"):
                continue
            tid = str(t.get("id") or "")
            c = parse_created(t.get("createdAt"))
            if not tid or c is None:
                continue
            recs[tid] = {
                "ts": c.astimezone(CST).isoformat(timespec="seconds"),
                "url": f"https://x.com/{handle}/status/{tid}",
                "text": re.sub("[  \x85\x0b\x0c\r]", "\n", t.get("text") or "").strip(),
            }
        cursor = data.get("next_cursor") or ""
        if not data.get("has_next_page") or not cursor or not tws:
            break
    return recs, got, ""


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    if not key or not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        status("哨兵时间线", "SKIP 未配置TWEET_API_KEY")
        return
    w = CFG["eval_window"]
    s0 = dt.datetime.strptime(w["start"], "%Y-%m-%d").replace(tzinfo=CST)
    u0 = dt.datetime.strptime(w["end"], "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=CST)
    top_n = CFG["candidate_filter"]["top_n_per_market"]
    only = os.environ.get("ONLY_MARKET", "").strip()

    grand, done = 0, []
    for market in CFG["markets"]:
        if only and market != only:
            continue
        handles = read_candidates(market, top_n)
        if not handles:
            status(f"哨兵时间线-{market}", "SKIP 无候选(先跑discover_candidates)")
            continue
        outdir = TL / market
        outdir.mkdir(parents=True, exist_ok=True)
        ok = 0
        for h in handles:
            p = outdir / f"{h}.jsonl"
            if p.exists() and len(p.read_text(encoding="utf-8").strip().split("\n")) > 5:
                continue  # 已抓过,跳过(幂等省钱)
            recs, got, err = fetch_one(key, h, s0, u0)
            grand += got
            if recs:
                with open(p, "w", encoding="utf-8") as f:
                    for tid in sorted(recs, key=lambda k: recs[k]["ts"]):
                        f.write(json.dumps(recs[tid], ensure_ascii=False) + "\n")
                ok += 1
            print(f"  {market}/{h}: {len(recs)}条{' '+err if err else ''}", flush=True)
            if err:
                break
            time.sleep(2)
        done.append(f"{market}:{ok}/{len(handles)}号")
        status(f"哨兵时间线-{market}", f"OK {ok}/{len(handles)}个账号 窗口{w['start']}~{w['end']}")
    status("哨兵时间线", f"OK {'、'.join(done)};本次拉{grand}条,费用约${grand*0.00015:.3f}")
    print(f"合计拉取{grand}条,约${grand*0.00015:.3f}")


if __name__ == "__main__":
    main()
