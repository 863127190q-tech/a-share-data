#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯转推回捞:抓取 @TingHu888 指定时间窗内的纯转推(native RT) → tinghu/retweets.jsonl
与 tweets.jsonl 分开存放:tweets.jsonl 的既定口径是"滤纯转推",已被下游依赖,不动。

字段: {"ts":转推时间,"url":转推状态链接,"rt_of":原推作者,"rt_url":原推链接,
       "text":原推全文(截自retweeted_tweet,比RT @前缀截断版完整),"src":"B-rt","note":""}
接口: advanced_search  query = from:用户 include:nativeretweets filter:nativeretweets
      (标准推特搜索默认排除native RT,须显式include;若filter:语法不被支持则
       退化为仅include:并在本地只保留转推)
用法: TWEET_API_KEY=xxx SINCE_BJ="2026-01-25 00:00" UNTIL_BJ="2026-07-10 00:00" \
      python fetch_retweets_hist.py
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

RTS = Path("tinghu/retweets.jsonl")
STATUS = Path("tinghu/_status.txt")
HANDLE = "TingHu888"
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = dt.timezone(dt.timedelta(hours=8))
BAD_LINESEP = re.compile("[  \x85\x0b\x0c\r]")


def update_status(component, line):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    rows = {}
    if STATUS.exists():
        for ln in STATUS.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[component] = f"{component} | {now} | {line}"
    order = ["A-手动剪藏", "B-API采集", "JOIN-行情对齐"]
    out = [rows[k] for k in order if k in rows]
    out += [v for k, v in sorted(rows.items()) if k not in order]
    STATUS.write_text("\n".join(out) + "\n", encoding="utf-8")


def parse_created(s):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def is_rt(t):
    return bool(t.get("retweeted_tweet")) or (t.get("text") or "").startswith("RT @")


def rt_record(t):
    tid = str(t.get("id") or "")
    created = parse_created(t.get("createdAt"))
    if not tid or created is None:
        return None
    orig = t.get("retweeted_tweet") or {}
    oau = (orig.get("author") or {})
    rt_of = oau.get("userName") or oau.get("screen_name") or ""
    if not rt_of:
        m = re.match(r"RT @([A-Za-z0-9_]+):", t.get("text") or "")
        rt_of = m.group(1) if m else ""
    oid = str(orig.get("id") or "")
    text = (orig.get("text") or t.get("text") or "").strip()
    return {
        "ts": created.astimezone(CST).isoformat(timespec="seconds"),
        "url": f"https://x.com/{HANDLE}/status/{tid}",
        "rt_of": rt_of,
        "rt_url": f"https://x.com/{rt_of}/status/{oid}" if rt_of and oid else "",
        "text": BAD_LINESEP.sub("\n", text),
        "src": "B-rt",
        "note": "",
    }


def search(key, query, max_pages=25):
    import requests
    out, cursor, pages = [], "", 0
    while pages < max_pages:
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
            raise RuntimeError(f"HTTP{r.status_code}: {r.text[:100]}")
        data = r.json()
        pages += 1
        tweets = data.get("tweets") or []
        out.extend(tweets)
        cursor = data.get("next_cursor") or ""
        if not data.get("has_next_page") or not cursor or not tweets:
            break
    return out, pages


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    if not key or not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        update_status("B-rt纯转推", "SKIP 未配置TWEET_API_KEY")
        return
    since = dt.datetime.strptime(os.environ.get("SINCE_BJ", "2026-01-25 00:00"),
                                 "%Y-%m-%d %H:%M").replace(tzinfo=CST)
    until = dt.datetime.strptime(os.environ.get("UNTIL_BJ", "2026-07-10 00:00"),
                                 "%Y-%m-%d %H:%M").replace(tzinfo=CST)
    win = f"since_time:{int(since.timestamp())} until_time:{int(until.timestamp())}"

    fetched, pages = [], 0
    note = ""
    try:
        # 首选:服务端只给纯转推
        fetched, pages = search(key, f"from:{HANDLE} include:nativeretweets filter:nativeretweets {win}")
        if not fetched:
            # 退化:include全量,本地筛转推(费用略高,如实记录)
            note = ";filter:语法无结果,退化为include+本地筛"
            fetched, pages = search(key, f"from:{HANDLE} include:nativeretweets {win}")
    except Exception as e:
        update_status("B-rt纯转推", f"FAIL {type(e).__name__}: {str(e)[:100].replace(key, '[脱敏]')}")
        print(f"FAIL {type(e).__name__}")
        return

    rts = {}
    if RTS.exists():
        for ln in RTS.read_text(encoding="utf-8").split("\n"):
            if ln.strip():
                r = json.loads(ln)
                rts[r["url"].rsplit("/", 1)[-1]] = r
    added = 0
    for t in fetched:
        if not is_rt(t):
            continue
        rec = rt_record(t)
        if rec is None:
            continue
        tid = rec["url"].rsplit("/", 1)[-1]
        if tid not in rts:
            rts[tid] = rec
            added += 1
    rows = sorted(rts.values(), key=lambda r: r["ts"])
    RTS.parent.mkdir(parents=True, exist_ok=True)
    RTS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    cost = len(fetched) * 0.00015
    span = f"{rows[0]['ts'][:10]}~{rows[-1]['ts'][:10]}" if rows else "空"
    msg = (f"OK 窗口{since:%m-%d}~{until:%m-%d} 翻{pages}页拉{len(fetched)}条 "
           f"纯转推新增{added}条 库内{len(rows)}条({span}) 费用约${cost:.3f}{note}")
    update_status("B-rt纯转推", msg)
    print(msg)


if __name__ == "__main__":
    main()
