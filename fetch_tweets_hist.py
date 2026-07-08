#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史回捞:用 twitterapi.io 高级搜索按时间窗抓取 @TingHu888 的历史推文(含回复),
合并进 tinghu/tweets.jsonl(按推文ID去重、ts升序),src 标 "B-hist"。

用法(由 tweet_hist.yml 工作流调用,也可本地跑):
  TWEET_API_KEY=xxx SINCE_BJ="2026-05-11 00:00" UNTIL_BJ="2026-06-11 16:00" \
  SEG_LABEL="2026-05-11~06-10" python fetch_tweets_hist.py

接口: GET /twitter/tweet/advanced_search  query=from:用户 since_time:unix until_time:unix
      (时间戳按北京时间输入,脚本内转unix;queryType=Latest 按时间倒序返回)
计费: 约15积分/条($0.15/1k);免费档限速每5秒1请求,页间隔6秒+429自动重试。
中途失败(如积分耗尽)会保存已抓部分,并在 _status.txt 记录实际到达的时间边界。
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

TWEETS = Path("tinghu/tweets.jsonl")
STATUS = Path("tinghu/_status.txt")
HANDLE = "TingHu888"
API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
INFO_API = "https://api.twitterapi.io/oapi/my/info"
CST = dt.timezone(dt.timedelta(hours=8))

URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter|mobile\.twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)\S*")
BAD_LINESEP = re.compile("[\u2028\u2029\x85\x0b\x0c\r]")


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


def dedup_key(url):
    m = URL_RE.search(url or "")
    return m.group(2) if m else (url or "").strip().rstrip("/").lower()


def load_tweets():
    rows = []
    if TWEETS.exists():
        for ln in TWEETS.read_text(encoding="utf-8").split("\n"):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    return rows


def sort_key(row):
    try:
        t = dt.datetime.fromisoformat(row.get("ts", ""))
    except ValueError:
        return dt.datetime.max.replace(tzinfo=CST)
    return t if t.tzinfo else t.replace(tzinfo=CST)


def save_tweets(rows):
    rows.sort(key=sort_key)
    TWEETS.parent.mkdir(parents=True, exist_ok=True)
    TWEETS.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def parse_created(s):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def tweet_to_record(t):
    tid = str(t.get("id") or t.get("id_str") or "")
    m = URL_RE.search(t.get("url") or t.get("twitterUrl") or "")
    if m:
        url = f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    elif tid:
        url = f"https://x.com/{HANDLE}/status/{tid}"
    else:
        return None
    text = BAD_LINESEP.sub("\n", (t.get("text") or t.get("full_text") or "")).strip()
    note = ""
    created = parse_created(t.get("createdAt") or t.get("created_at"))
    if created is None:
        created = dt.datetime.now(CST)
        note = "发推时间解析失败,按采集时间记录"
    return {
        "ts": created.astimezone(CST).isoformat(timespec="seconds"),
        "url": url,
        "text": text,
        "src": "B-hist",
        "note": note,
    }


def is_retweet(t):
    if t.get("retweeted_tweet"):
        return True
    return (t.get("text") or "").startswith("RT @")


def bj(s):
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=CST)


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    label = os.environ.get("SEG_LABEL", "未命名段")
    comp = f"B-hist {label}"
    if not key:
        update_status(comp, "SKIP 未配置TWEET_API_KEY")
        return
    if not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        update_status(comp, "FAIL 密钥格式异常,请到Settings→Secrets重新粘贴")
        return

    def safe(s):
        s = re.sub(r"\s+", " ", str(s)).replace(key, "[密钥已脱敏]")
        return s[:120]

    since = bj(os.environ["SINCE_BJ"])
    until = bj(os.environ["UNTIL_BJ"])
    max_pages = min(200, max(1, int(os.environ.get("MAX_PAGES", "80") or "80")))

    import requests

    def credits():
        """查余额,拿不到就算了(余额数字不是敏感信息)。"""
        try:
            r = requests.get(INFO_API, headers={"X-API-Key": key}, timeout=15)
            d = r.json()
            for k_ in ("recharge_credits", "credits", "balance"):
                if k_ in d:
                    return d[k_]
            return d.get("data", {}).get("credits")
        except Exception:
            return None

    bal0 = credits()
    print(f"[额度] 开始前余额: {bal0 if bal0 is not None else '查询失败'}")

    rows = load_tweets()
    by_key = {dedup_key(r.get("url")): r for r in rows}
    fetched, added, upgraded, skipped_rt, pages = 0, 0, 0, 0, 0
    oldest_seen = None
    cursor = ""
    query = f"from:{HANDLE} since_time:{int(since.timestamp())} until_time:{int(until.timestamp())}"
    print(f"[查询] {query}")

    def finish(state, extra=""):
        if added or upgraded:
            save_tweets(rows)
        bal1 = credits()
        cost = fetched * 0.00015
        reach = oldest_seen.strftime("%Y-%m-%d %H:%M") if oldest_seen else "未触达"
        msg = (f"{state} 窗口{label} 翻{pages}页 拉取{fetched}条(滤转推{skipped_rt}) "
               f"新增{added}条 费用约${cost:.3f} 最早触达{reach}"
               f"{' 余额'+str(bal1) if bal1 is not None else ''}{extra}")
        update_status(comp, msg)
        print(msg)

    try:
        while pages < max_pages:
            if pages > 0:
                time.sleep(6)
            params = {"query": query, "queryType": "Latest"}
            if cursor:
                params["cursor"] = cursor
            for _attempt in range(4):
                r = requests.get(API, params=params, headers={"X-API-Key": key}, timeout=30)
                if r.status_code != 429:
                    break
                time.sleep(12)
            if r.status_code != 200:
                finish("PARTIAL", f" 中断原因HTTP{r.status_code}:{safe(r.text)}")
                return
            data = r.json()
            if data.get("status") and data.get("status") != "success":
                finish("PARTIAL", f" 中断原因:{safe(data.get('msg') or data.get('message'))}")
                return
            tweets = data.get("tweets") or []
            pages += 1
            for t in tweets:
                fetched += 1
                created = parse_created(t.get("createdAt") or t.get("created_at"))
                if created is not None:
                    c = created.astimezone(CST)
                    oldest_seen = c if oldest_seen is None or c < oldest_seen else oldest_seen
                if is_retweet(t):
                    skipped_rt += 1
                    continue
                rec = tweet_to_record(t)
                if rec is None:
                    continue
                k = dedup_key(rec["url"])
                old = by_key.get(k)
                if old is None:
                    by_key[k] = rec
                    rows.append(rec)
                    added += 1
                elif old.get("src") == "A":
                    old["ts"], old["src"] = rec["ts"], "B-hist"
                    if rec["text"]:
                        old["text"] = rec["text"]
                    old["note"] = "原A剪藏,已由B-hist校准"
                    upgraded += 1
            cursor = data.get("next_cursor") or ""
            if not data.get("has_next_page") or not cursor or not tweets:
                break
    except Exception as e:
        finish("PARTIAL", f" 中断原因{type(e).__name__}:{safe(e)}")
        return

    finish("OK")
    # 按日统计,便于发现可疑的零推文日(账号日均约15条,连续多日为0需人工留意)
    daily = {}
    for r_ in rows:
        d0 = r_.get("ts", "")[:10]
        if str(since.date()) <= d0 < str(until.date()):
            daily[d0] = daily.get(d0, 0) + 1
    zero_days = []
    cur = since.date()
    while cur < until.date():
        if str(cur) not in daily:
            zero_days.append(str(cur))
        cur += dt.timedelta(days=1)
    print(f"[按日分布] 窗口内有推文的天数{len(daily)},零推文日:{zero_days if zero_days else '无'}")


if __name__ == "__main__":
    main()
