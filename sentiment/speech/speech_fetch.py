#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A1 宽口径捞取(v3.1):把『当天在聊中国股市』的中文言论池捞回来,不做任何情绪预判。
数据源 twitterapi.io advanced_search;查询词=话题词(见 speech_lexicon.json)。
密钥从环境变量 TWEET_API_KEY 读取(GitHub Secrets 注入),永不入文件/日志。

用法:
  TWEET_API_KEY=xxx python sentiment/speech/speech_fetch.py                # 昨天(北京时间,完整自然日)
  TWEET_API_KEY=xxx python sentiment/speech/speech_fetch.py 2026-06-05    # 指定日
  ... BACKFILL_START=2026-06-01 BACKFILL_END=2026-07-09 ...               # 区间回填
产物: sentiment/speech/pool/YYYYMMDD.jsonl
  每行 {"ts","url","text","author","followers","engage","day"}(按推文ID去重)
费用: 每页约20条≈$0.003;每日默认上限 MAX_PAGES_PER_DAY=6 页(≈120条,≈$0.018/日),
  实际抓取条数与费用写 sentiment/_status.txt。
"""
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

SP = Path(__file__).resolve().parent
SENT = SP.parent
POOL = SP / "pool"
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = dt.timezone(dt.timedelta(hours=8))
MAX_PAGES_PER_DAY = int(os.environ.get("MAX_PAGES_PER_DAY", "6") or "6")
BAD_LINESEP = re.compile("[  \x85\x0b\x0c\r]")


def status(source, line):
    p = SENT / "_status.txt"
    rows = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {dt.datetime.now(CST).strftime('%Y-%m-%d %H:%M')} | {line}"
    p.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


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


def fetch_day(key, day_iso, queries):
    """抓一个北京自然日的言论池。返回(条数, 页数)。"""
    d0 = dt.datetime.strptime(day_iso, "%Y-%m-%d").replace(tzinfo=CST)
    d1 = d0 + dt.timedelta(days=1)
    out_path = POOL / f"{day_iso.replace('-', '')}.jsonl"
    seen = set()
    recs = {}
    if out_path.exists():  # 幂等:重跑合并
        for ln in out_path.read_text(encoding="utf-8").split("\n"):
            if ln.strip():
                r = json.loads(ln)
                tid = r["url"].rsplit("/", 1)[-1]
                seen.add(tid)
                recs[tid] = r
    pages_total, fetched = 0, 0
    for q in queries:
        query = f"{q} since_time:{int(d0.timestamp())} until_time:{int(d1.timestamp())}"
        cursor = ""
        for _p in range(max(1, MAX_PAGES_PER_DAY // len(queries))):
            if pages_total:
                time.sleep(6)  # 免费档限速
            params = {"query": query, "queryType": "Latest"}
            if cursor:
                params["cursor"] = cursor
            for _a in range(4):
                r = requests.get(API, params=params, headers={"X-API-Key": key}, timeout=30)
                if r.status_code != 429:
                    break
                time.sleep(12)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP{r.status_code}: {re.sub(chr(10), ' ', r.text)[:100]}")
            data = r.json()
            pages_total += 1
            for t in data.get("tweets") or []:
                fetched += 1
                tid = str(t.get("id") or "")
                if not tid or tid in seen:
                    continue
                if (t.get("text") or "").startswith("RT @"):
                    continue
                created = parse_created(t.get("createdAt"))
                au = t.get("author") or {}
                seen.add(tid)
                handle = au.get("userName") or au.get("screen_name") or "?"
                recs[tid] = {
                    "ts": created.astimezone(CST).isoformat(timespec="seconds") if created else "",
                    "url": f"https://x.com/{handle}/status/{tid}",
                    "text": BAD_LINESEP.sub("\n", (t.get("text") or "")).strip(),
                    "author": handle,
                    "followers": au.get("followers") or au.get("followers_count") or 0,
                    "engage": sum(int(t.get(k) or 0) for k in ("likeCount", "retweetCount", "replyCount")),
                    "day": day_iso,
                }
            cursor = data.get("next_cursor") or ""
            if not data.get("has_next_page") or not cursor:
                break
    POOL.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for tid in sorted(recs):
            f.write(json.dumps(recs[tid], ensure_ascii=False) + "\n")
    return len(recs), pages_total, fetched


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    if not key or not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        status("speech_fetch", "SKIP 未配置或格式异常的TWEET_API_KEY")
        print("SKIP: no key")
        return
    lex = json.loads((SP / "speech_lexicon.json").read_text(encoding="utf-8"))
    queries = lex["query_batches"]

    bs, be = os.environ.get("BACKFILL_START"), os.environ.get("BACKFILL_END")
    if bs and be:
        days = []
        d = dt.date.fromisoformat(bs)
        while d <= dt.date.fromisoformat(be):
            days.append(d.isoformat())
            d += dt.timedelta(days=1)
    elif len(sys.argv) > 1:
        days = [sys.argv[1]]
    else:
        days = [(dt.datetime.now(CST).date() - dt.timedelta(days=1)).isoformat()]  # 默认昨天(完整日)

    total, cost_tweets = 0, 0
    for day in days:
        try:
            n, pages, fetched = fetch_day(key, day, queries)
            total += n
            cost_tweets += fetched
            print(f"{day}: 池内{n}条(本次翻{pages}页)", flush=True)
        except Exception as e:
            status("speech_fetch", f"PARTIAL {day}中断: {type(e).__name__} {str(e)[:80].replace(key, '[脱敏]')}")
            print(f"{day}: FAIL {type(e).__name__}")
            break
        time.sleep(3)
    cost = cost_tweets * 0.00015
    status("speech_fetch", f"OK {days[0]}~{days[-1]} 共{len(days)}日 池计{total}条 本次拉取{cost_tweets}条 费用约${cost:.3f}")


if __name__ == "__main__":
    main()
