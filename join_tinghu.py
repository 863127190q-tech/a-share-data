#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情对齐:把 tinghu/tweets.jsonl 里的推文按北京时间日期分组,
对每个存在 data/YYYYMMDD/ 行情数据的日期生成 tinghu/joined/YYYYMMDD.md:
推文原文 + 当日L1读数(涨跌家数/成交额/涨停跌停炸板/连板高度)+ AI复合体个股表现。
纯标准库实现,幂等:每次全量重建 joined/,与 tweets.jsonl 严格镜像。
"""
import csv
import datetime as dt
import json
from pathlib import Path

TWEETS = Path("tinghu/tweets.jsonl")
STATUS = Path("tinghu/_status.txt")
JOINED = Path("tinghu/joined")
CST = dt.timezone(dt.timedelta(hours=8))

# AI复合体观察清单(硬编码,按需增删)
AI_CODES = [
    "300308", "300502", "688256", "002384", "301308", "603986", "002371",
    "688008", "001309", "603256", "688525", "300223", "600667",
    "000021", "688361", "688409",
]


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


def load_tweets():
    rows = []
    if TWEETS.exists():
        # 按\n切行而非splitlines():推文里的U+2028等字符不该被当成行边界
        for ln in TWEETS.read_text(encoding="utf-8").split("\n"):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    return rows


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fmt_amount(total_yuan):
    if total_yuan >= 1e12:
        return f"{total_yuan / 1e12:.2f}万亿"
    return f"{total_yuan / 1e8:.0f}亿"


def l1_section(date):
    """当日L1读数,缺哪张表就注明哪张缺,不中断。"""
    folder = Path(f"data/{date}")
    lines, missing = [], []

    p = folder / "all_stocks.csv"
    if p.exists():
        rows = read_csv(p)
        pcts = [fnum(r.get("涨跌幅")) for r in rows]
        up = sum(1 for x in pcts if x is not None and x > 0)
        down = sum(1 for x in pcts if x is not None and x < 0)
        flat = sum(1 for x in pcts if x is not None and x == 0)
        amt = sum(fnum(r.get("成交额")) or 0 for r in rows)
        lines.append(f"- 全A {len(rows)} 只:上涨 **{up}** / 下跌 **{down}** / 平盘 {flat}")
        lines.append(f"- 两市成交额:**{fmt_amount(amt)}**")
    elif (folder / "hist_universe.csv").exists():
        rows = read_csv(folder / "hist_universe.csv")
        pcts = [fnum(r.get("涨跌幅")) for r in rows]
        up = sum(1 for x in pcts if x is not None and x > 0)
        down = sum(1 for x in pcts if x is not None and x < 0)
        lines.append(f"- ⚠️ 历史回填日,无全A快照;结算宇宙 {len(rows)} 只中:上涨 {up} / 下跌 {down}")
    else:
        missing.append("all_stocks")

    zt = zb = None
    p = folder / "zt_pool.csv"
    if p.exists():
        rows = read_csv(p)
        zt = len(rows)
        heights = [int(fnum(r.get("连板数")) or 1) for r in rows]
        top = max(heights) if heights else 0
        top_names = [r.get("名称", "") for r in rows if int(fnum(r.get("连板数")) or 1) == top][:5]
        board = f",最高 **{top}连板**({'、'.join(top_names)})" if top >= 2 else ""
        lines.append(f"- 涨停 **{zt}** 家{board}")
    else:
        missing.append("zt_pool")

    p = folder / "zb_pool.csv"
    if p.exists():
        zb = len(read_csv(p))
        rate = f",炸板率 {zb / (zb + zt) * 100:.0f}%" if zt is not None and (zb + zt) > 0 else ""
        lines.append(f"- 炸板 **{zb}** 家{rate}")
    else:
        missing.append("zb_pool")

    p = folder / "dt_pool.csv"
    if p.exists():
        lines.append(f"- 跌停 **{len(read_csv(p))}** 家")
    else:
        missing.append("dt_pool")

    if missing:
        lines.append(f"- ⚠️ 当日缺失:{'、'.join(missing)}(见 data/{date}/_report.txt)")
    return lines


def ai_section(date):
    p = Path(f"data/{date}/all_stocks.csv")
    price_col = "最新价"
    if not p.exists():
        p = Path(f"data/{date}/hist_universe.csv")  # 历史回填日回退
        price_col = "收盘"
    if not p.exists():
        return ["(all_stocks.csv 与 hist_universe.csv 均缺失,无法生成)"]
    by_code = {r.get("代码", "").zfill(6): r for r in read_csv(p)}
    perf = []
    for code in AI_CODES:
        r = by_code.get(code)
        if r is None:
            perf.append((None, code, "—", "无数据/停牌", "—"))
            continue
        pct = fnum(r.get("涨跌幅"))
        perf.append((pct, code, r.get("名称", ""), f"{pct:+.2f}%" if pct is not None else "—", r.get(price_col, "—")))
    perf.sort(key=lambda x: (x[0] is None, -(x[0] or 0)))
    out = ["| 代码 | 名称 | 涨跌幅 | 收盘价 |", "|---|---|---|---|"]
    for _pct, code, name, pct_s, price in perf:
        out.append(f"| {code} | {name} | {pct_s} | {price} |")
    return out


def tweet_section(tweets):
    out = []
    for t in tweets:
        hhmm = t.get("ts", "")[11:16]
        src = t.get("src", "?")
        out.append(f"**{hhmm}** · [原文]({t.get('url', '')}) · 来源{src}")
        text = t.get("text") or "(正文未存,见原链接)"
        out.extend("> " + ln for ln in text.splitlines())
        if t.get("note"):
            out.append(f"> *({t['note']})*")
        out.append("")
    return out


def main():
    try:
        tweets = load_tweets()
        groups = {}
        bad_ts = 0
        for t in tweets:
            try:
                d0 = dt.datetime.fromisoformat(t.get("ts", ""))
            except ValueError:
                bad_ts += 1
                continue
            if d0.tzinfo is None:
                d0 = d0.replace(tzinfo=CST)
            date = d0.astimezone(CST).strftime("%Y%m%d")  # 统一折算北京时间,凌晨归当天
            groups.setdefault(date, []).append(t)

        JOINED.mkdir(parents=True, exist_ok=True)
        wanted = {d for d in groups if Path(f"data/{d}").exists()}

        # 幂等:joined/ 与 tweets.jsonl 严格镜像,清掉不再对应的旧文件
        for f in JOINED.glob("*.md"):
            if f.stem not in wanted:
                f.unlink()

        written = 0
        for date in sorted(wanted):
            day = groups[date]
            day.sort(key=lambda t: t.get("ts", ""))
            md = [f"# {date[:4]}-{date[4:6]}-{date[6:]} · TingHu推文 × A股盘面", ""]
            md.append(f"## 推文({len(day)}条)")
            md.append("")
            md.extend(tweet_section(day))
            md.append("## 当日L1读数")
            md.append("")
            md.extend(l1_section(date))
            md.append("")
            md.append("## AI复合体当日表现(按涨跌幅排序)")
            md.append("")
            md.extend(ai_section(date))
            md.append("")
            (JOINED / f"{date}.md").write_text("\n".join(md), encoding="utf-8")
            written += 1

        no_data = len(groups) - len(wanted)
        msg = f"OK 推文共{len(tweets)}条,生成{written}份对齐文件"
        if no_data:
            msg += f",另有{no_data}个日期无行情数据(非交易日或早于建库)"
        if bad_ts:
            msg += f";⚠️{bad_ts}条时间戳无法解析被跳过"
        update_status("JOIN-行情对齐", msg)
        print(msg)
    except Exception as e:  # 对齐失败不应拖垮行情数据提交
        update_status("JOIN-行情对齐", f"FAIL {type(e).__name__}: {str(e)[:150]}")
        print(f"JOIN失败: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
