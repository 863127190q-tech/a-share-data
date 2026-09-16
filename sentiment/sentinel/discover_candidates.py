#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
哨兵自动发现 · 第1步:候选池(不靠人工名单)
按 markets.json 的三市场分别产出候选账号,依据:粉丝量级 + 发言频率(出现天数) + 互动量。
  A股  : 从存量言论池 sentiment/speech/pool/*.jsonl 免费挖(已含 author/followers/engage)
  美股/加密: 用 twitterapi advanced_search 按话题词采样若干天,收集作者画像(需 TWEET_API_KEY)
产物: sentiment/sentinel/candidates_{市场}.csv
  列: handle, followers, days_seen, n_tweets, engage_sum, lang, market, source
注:这一步只做"谁在持续且有影响力地聊这个市场",不判好坏;战绩由后续回测给分。
"""
import datetime as dt
import glob
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

SD = Path(__file__).resolve().parent
SENT = SD.parent
CFG = json.loads((SD / "markets.json").read_text(encoding="utf-8"))
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = dt.timezone(dt.timedelta(hours=8))


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


def mine_pool():
    """A股:从存量言论池免费挖候选。"""
    agg = defaultdict(lambda: {"n": 0, "days": set(), "fol": 0, "eng": 0})
    for f in glob.glob(str(SENT / "speech" / "pool" / "*.jsonl")):
        for ln in open(f, encoding="utf-8"):
            if not ln.strip():
                continue
            r = json.loads(ln)
            a = r.get("author")
            if not a or a == "?":
                continue
            b = agg[a]
            b["n"] += 1
            b["days"].add(r.get("day", ""))
            b["fol"] = max(b["fol"], r.get("followers") or 0)
            b["eng"] += r.get("engage") or 0
    return {a: {"n": v["n"], "days": len(v["days"]), "fol": v["fol"], "eng": v["eng"], "lang": "zh"}
            for a, v in agg.items()}


def search_authors(key, queries, days_back=21, pages_per_day=2):
    """美股/加密:按话题词抽样若干天,聚合作者画像。"""
    import requests
    agg = defaultdict(lambda: {"n": 0, "days": set(), "fol": 0, "eng": 0, "lang": ""})
    end = dt.datetime.now(CST).replace(hour=23, minute=59)
    fetched = 0
    # 抽样:每隔3天取一天,覆盖 days_back 天,控成本
    for back in range(1, days_back + 1, 3):
        d0 = (end - dt.timedelta(days=back)).replace(hour=0, minute=0)
        d1 = d0 + dt.timedelta(days=1)
        for q in queries:
            lang = "en" if "lang:en" in q else "zh"
            query = f"{q} since_time:{int(d0.timestamp())} until_time:{int(d1.timestamp())}"
            cursor = ""
            for _p in range(pages_per_day):
                if fetched:
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
                    return agg, fetched, f"HTTP{r.status_code}"
                data = r.json()
                tws = data.get("tweets") or []
                for t in tws:
                    fetched += 1
                    if (t.get("text") or "").startswith("RT @"):
                        continue
                    au = t.get("author") or {}
                    h = au.get("userName") or au.get("screen_name")
                    if not h:
                        continue
                    c = parse_created(t.get("createdAt"))
                    b = agg[h]
                    b["n"] += 1
                    b["days"].add(c.astimezone(CST).date().isoformat() if c else "")
                    b["fol"] = max(b["fol"], au.get("followers") or au.get("followers_count") or 0)
                    b["eng"] += sum(int(t.get(k) or 0) for k in ("likeCount", "retweetCount", "replyCount"))
                    b["lang"] = b["lang"] or lang
                cursor = data.get("next_cursor") or ""
                if not data.get("has_next_page") or not cursor or not tws:
                    break
    return agg, fetched, ""


def write_candidates(market, rows, source):
    flt = CFG["candidate_filter"]
    cand = [r for r in rows if r["fol"] >= flt["min_followers"] and r["days"] >= flt["min_days_seen"]]
    # 排序:持续性(天数) × 影响力(粉丝开方),兼顾互动
    cand.sort(key=lambda r: -(r["days"] * (r["fol"] ** 0.5) + r["eng"] ** 0.5))
    out = SD / f"candidates_{market}.csv"
    lines = ["handle,followers,days_seen,n_tweets,engage_sum,lang,market,source"]
    for r in cand:
        lines.append(f"{r['handle']},{r['fol']},{r['days']},{r['n']},{r['eng']},{r['lang']},{market},{source}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return len(cand)


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    total_cost_n = 0
    for market, mc in CFG["markets"].items():
        if mc.get("pool_mine"):
            agg = mine_pool()
            rows = [{"handle": a, **v} for a, v in agg.items()]
            n = write_candidates(market, rows, "pool")
            status(f"哨兵发现-{market}", f"OK 池挖候选{n}个(零成本,来自{len(agg)}个作者)")
            print(f"{market}: 候选{n}个(池挖)")
            continue
        if not key or not re.fullmatch(r"[\x21-\x7e]{8,}", key):
            status(f"哨兵发现-{market}", "SKIP 未配置TWEET_API_KEY")
            print(f"{market}: SKIP 无密钥")
            continue
        agg, fetched, err = search_authors(key, mc["queries"])
        total_cost_n += fetched
        rows = [{"handle": a, **v} for a, v in agg.items()]
        n = write_candidates(market, rows, "search")
        msg = f"OK 搜索候选{n}个(采样{fetched}条,费用约${fetched*0.00015:.3f})"
        if err:
            msg = f"PARTIAL {err};" + msg
        status(f"哨兵发现-{market}", msg)
        print(f"{market}: 候选{n}个(采样{fetched}条)")
    if total_cost_n:
        print(f"本次搜索合计 {total_cost_n} 条,约 ${total_cost_n*0.00015:.3f}")


if __name__ == "__main__":
    main()
