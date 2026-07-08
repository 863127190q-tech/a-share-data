#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路线B·twitterapi.io 自动采集 @TingHu888 最新推文 → tinghu/tweets.jsonl
用法:  TWEET_API_KEY=xxx python fetch_tweets.py
密钥只从环境变量读取(由GitHub Secrets注入),绝不写入文件、日志或输出;
写入状态文件的任何报错信息都会先做脱敏(去密钥、压成单行、截断)。
未配置密钥时安静跳过并在 tinghu/_status.txt 记录,不报错、不中断流程。

接口: GET https://api.twitterapi.io/twitter/user/last_tweets  (2026-07-08 验证)
      认证头 X-API-Key;每页最多约20条,newest-first;计费约$0.003/页。
每次最多翻 MAX_PAGES 页(默认3,上限50);只有当某页出现"有正文候选、零新增、
且撞到了已归档的推文"时才提前收工——整页都是转推的页不会误触发提前停止。
按用户要求采集"所有推文,包括回复"(includeReplies=true);纯转推(RT)仍过滤——
那是他人的内容,带评论的引用推文有本人文字,不受影响会正常入库。
若同一推文此前由路线A手动剪藏过,则用B的权威时间与全文升级该条。
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
API = "https://api.twitterapi.io/twitter/user/last_tweets"
CST = dt.timezone(dt.timedelta(hours=8))

URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter|mobile\.twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)\S*")
# U+2028/U+2029/NEL等罕见换行符会破坏JSONL行结构,统一归一成\n
BAD_LINESEP = re.compile("[\u2028\u2029\x85\x0b\x0c\r]")


def update_status(component, line):
    """更新 tinghu/_status.txt 中本组件的状态行,不覆盖其他组件的行。"""
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


def canon_url(url):
    m = URL_RE.search(url or "")
    if m:
        return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    return (url or "").strip().rstrip("/")


def dedup_key(url):
    """推文status ID全局唯一,用它去重。"""
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
    url = canon_url(t.get("url") or t.get("twitterUrl") or (f"https://x.com/{HANDLE}/status/{tid}" if tid else ""))
    if not url:
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
        "src": "B",
        "note": note,
    }


def is_retweet(t):
    if t.get("retweeted_tweet"):
        return True
    return (t.get("text") or "").startswith("RT @")


def main():
    key = os.environ.get("TWEET_API_KEY", "").strip()
    if not key:
        update_status("B-API采集", "SKIP 未配置TWEET_API_KEY(在仓库Settings→Secrets→Actions添加后自动启用)")
        print("路线B跳过:未配置TWEET_API_KEY")
        return
    if not re.fullmatch(r"[\x21-\x7e]{8,}", key):
        # 含换行/空格等非法字符的密钥会被requests原样回显进报错信息,先行拦截,绝不让它进入任何输出
        update_status("B-API采集", "FAIL 密钥格式异常(含空白或非ASCII字符),请到Settings→Secrets重新粘贴一行完整密钥")
        print("路线B失败:密钥格式异常")
        return

    def safe(s):
        """写入公开仓库前脱敏:去密钥、压成单行、截断。"""
        s = re.sub(r"\s+", " ", str(s)).replace(key, "[密钥已脱敏]")
        return s[:120]

    try:
        max_pages = min(50, max(1, int(os.environ.get("MAX_PAGES", "3") or "3")))
    except ValueError:
        max_pages = 3

    import requests

    rows = load_tweets()
    by_key = {dedup_key(r.get("url")): r for r in rows}
    fetched, added, upgraded, skipped_rt = 0, 0, 0, 0
    cursor = ""

    def save_partial(reason):
        """中途失败也保住已拉到的数据,并如实记录。"""
        if added or upgraded:
            save_tweets(rows)
            reason += f"(已保存部分:新增{added}条)"
        update_status("B-API采集", reason)

    try:
        for page_no in range(max_pages):
            if page_no > 0:
                time.sleep(6)  # 免费档限速:每5秒最多1个请求
            params = {"userName": HANDLE, "includeReplies": "true"}
            if cursor:
                params["cursor"] = cursor
            for attempt in range(4):
                r = requests.get(API, params=params, headers={"X-API-Key": key}, timeout=30)
                if r.status_code != 429:
                    break
                time.sleep(12)  # 撞限速:等一等再试
            if r.status_code != 200:
                save_partial(f"FAIL HTTP{r.status_code}: {safe(r.text)}")
                print(f"路线B失败 HTTP{r.status_code}")
                return
            data = r.json()
            if data.get("status") and data.get("status") != "success":
                save_partial(f"FAIL 接口返回: {safe(data.get('msg') or data.get('message'))}")
                return
            tweets = data.get("tweets")
            if tweets is None and isinstance(data.get("data"), dict):
                tweets = data["data"].get("tweets")
            tweets = tweets or []
            page_candidates = page_new = page_dupes = 0
            for t in tweets:
                fetched += 1
                if is_retweet(t):
                    skipped_rt += 1
                    continue
                rec = tweet_to_record(t)
                if rec is None:
                    continue
                page_candidates += 1
                k = dedup_key(rec["url"])
                old = by_key.get(k)
                if old is None:
                    by_key[k] = rec
                    rows.append(rec)
                    added += 1
                    page_new += 1
                elif old.get("src") == "A":
                    # B的发推时间与全文是权威的,升级A手动剪藏的同一条
                    old["ts"], old["src"] = rec["ts"], "B"
                    if rec["text"]:
                        old["text"] = rec["text"]
                    old["note"] = "原A剪藏,已由B校准时间与全文"
                    upgraded += 1
                    page_dupes += 1
                else:
                    page_dupes += 1
            cursor = data.get("next_cursor") or ""
            if not data.get("has_next_page") or not cursor:
                break
            # 只有真撞上已归档推文才提前收工;整页转推不算,继续往下翻
            if page_candidates > 0 and page_new == 0 and page_dupes > 0:
                break
    except Exception as e:  # 网络异常等:如实记录(脱敏后),不中断整体
        save_partial(f"FAIL {type(e).__name__}: {safe(e)}")
        print(f"路线B失败: {type(e).__name__}")
        return

    if added or upgraded:
        save_tweets(rows)
    msg = f"OK 拉取{fetched}条(滤除转推{skipped_rt}条),新增{added}条,升级A条目{upgraded}条,库内共{len(rows)}条"
    update_status("B-API采集", msg)
    print(msg)


if __name__ == "__main__":
    main()
