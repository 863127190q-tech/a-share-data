#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路线A·手动剪藏归档:解析 tinghu/inbox.md 粘贴区的新条目,
规范化追加进 tinghu/tweets.jsonl(以推文status ID去重、按时间升序),然后清空粘贴区。
无链接的条目原样保留在收件箱等用户补链接,不丢内容。

解析规则(宽松):
  - 条目之间用一行 --- 分隔;
  - 一个分隔块里只有一条推文链接时,整块视为一条(链接在前在后都行);
  - 块里有多条 @TingHu888 的链接时,按链接行切分成多条;
  - 正文里引用的他人推文链接不当作切分点,保留在正文里;
  - 日期行必须独立成行(如 2026-07-08 21:30)才生效,正文里顺带提到的日期不会误判。
"""
import datetime as dt
import json
import re
from pathlib import Path

INBOX = Path("tinghu/inbox.md")
TWEETS = Path("tinghu/tweets.jsonl")
STATUS = Path("tinghu/_status.txt")
HANDLE = "TingHu888"
CST = dt.timezone(dt.timedelta(hours=8))
MARKER = "<!-- ↓↓↓ 从这行往下粘贴,处理后会被自动清空 ↓↓↓ -->"

URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter|mobile\.twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)\S*")
DATE_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?(?:[ T]*(\d{1,2}):(\d{2}))?")
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
    """推文status ID全局唯一,用它去重,不受域名/用户名大小写影响。"""
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


def parse_ts_line(entry_text):
    """认"行首是日期"的行:要么日期后缀很短(独立时间行),要么是条目第一个内容行
    (时间行习惯写在最前)。正文中段顺带提到的日期不会误判。
    返回 (时间, 所在行, 匹配到的日期串)。"""
    first_content_seen = False
    for line in entry_text.splitlines():
        s = line.strip()
        if not s:
            continue
        is_first_content = not first_content_seen
        if not URL_RE.search(s):
            first_content_seen = True
        m = DATE_RE.search(s)
        if m and m.start() == 0 and (len(s) <= len(m.group(0)) + 8 or is_first_content):
            y, mo, d, hh, mm = m.groups()
            try:
                t = dt.datetime(int(y), int(mo), int(d), int(hh or 12), int(mm or 0), tzinfo=CST)
                return t, line, m.group(0)
            except ValueError:
                continue
    return None, None, None


def split_block(block):
    """一个粘贴块 → (条目文本列表, 剩余无链接文本)。"""
    lines = block.splitlines()
    hits = []
    for i, line in enumerate(lines):
        m = URL_RE.search(line)
        if m:
            hits.append((i, m))
    own = [(i, m) for i, m in hits if m.group(1).lower() == HANDLE.lower()]
    terms = own if own else hits  # 有本人链接时,他人链接只算正文
    if not terms:
        return [], block.strip()
    if len(terms) == 1:
        return [block], ""  # 单链接:整块一条,链接在前在后都行
    entries, start = [], 0
    for i, _m in terms:
        entries.append("\n".join(lines[start:i + 1]))
        start = i + 1
    leftover = "\n".join(lines[start:]).strip()
    return entries, leftover


def entry_to_record(entry_text):
    entry_text = BAD_LINESEP.sub("\n", entry_text)
    chosen = None
    for m in URL_RE.finditer(entry_text):
        if m.group(1).lower() == HANDLE.lower():
            chosen = m
            break
    if chosen is None:
        chosen = URL_RE.search(entry_text)
    url = f"https://x.com/{chosen.group(1)}/status/{chosen.group(2)}"

    ts, ts_line, ts_str = parse_ts_line(entry_text)
    note = ""
    if ts is None:
        ts = dt.datetime.now(CST)
        note = "未标注时间,按剪藏时间记录"

    lines = []
    for line in entry_text.splitlines():
        if ts_line is not None and line == ts_line:
            line = line.replace(ts_str, "", 1)  # 只剥日期串,同行说明文字保留
        stripped = line.replace(chosen.group(0), "").strip()
        if stripped:
            lines.append(stripped)
    text = "\n".join(lines).strip()
    if not text:
        note = (note + ";" if note else "") + "仅链接,正文未粘贴"
    return {
        "ts": ts.isoformat(timespec="seconds"),
        "url": url,
        "text": text,
        "src": "A",
        "note": note,
    }


def main():
    if not INBOX.exists():
        update_status("A-手动剪藏", "SKIP inbox.md不存在")
        return
    content = INBOX.read_text(encoding="utf-8")
    if MARKER not in content:
        update_status("A-手动剪藏", "FAIL inbox.md的粘贴区标记行被删除,请恢复该行")
        return
    header, body = content.split(MARKER, 1)
    header += MARKER

    entries, leftovers = [], []
    for block in re.split(r"\n-{3,}\s*\n|\n-{3,}\s*$", "\n" + body):
        if not block.strip():
            continue
        found, leftover = split_block(block)
        entries.extend(found)
        if leftover:
            leftovers.append(leftover)

    if not entries and not leftovers:
        update_status("A-手动剪藏", "OK 收件箱为空,无新条目")
        return

    rows = load_tweets()
    seen = {dedup_key(r.get("url")) for r in rows}
    added = 0
    for e in entries:
        rec = entry_to_record(e)
        k = dedup_key(rec["url"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(rec)
        added += 1
    if added:
        save_tweets(rows)

    new_body = "\n"
    for lo in leftovers:
        new_body += "\n---\n" + lo + "\n"
    INBOX.write_text(header + new_body, encoding="utf-8")

    msg = f"OK 解析{len(entries)}条,去重后新增{added}条"
    if leftovers:
        msg += f";{len(leftovers)}段无链接内容保留在收件箱待补"
    update_status("A-手动剪藏", msg)
    print(msg)


if __name__ == "__main__":
    main()
