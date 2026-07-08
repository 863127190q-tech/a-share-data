#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史行情回填(本地运行,量大不进GitHub Action;支持断点续跑)。

用法:
  pip install akshare pandas -U
  python backfill_market.py 20260511 20260702

对区间内每个A股交易日:
  1. 情绪面板:涨停池/炸板池/跌停池 → data/YYYYMMDD/{zt,zb,dt}_pool.csv(与现行结构一致)
  2. 结算宇宙:清单 = 推文中出现的A股名称(全名/去ST前缀/6位代码/$代码) ∪ AI复合体固定名单
     ∪ 全市场市值前300 ∪ 关键ETF(159516/159558/589130);
     每只用历史日线接口一次拉全区间,拆写为 data/YYYYMMDD/hist_universe.csv
     (代码/名称/开盘/最高/最低/收盘/昨收/涨跌幅/成交额)
断点文件 .backfill_checkpoint.json(不入库);失败项记录其中,重跑自动补。
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import akshare as ak

CKPT = Path(".backfill_checkpoint.json")
STATUS = Path("tinghu/_status.txt")
TWEETS = Path("tinghu/tweets.jsonl")
CST = dt.timezone(dt.timedelta(hours=8))

AI_CODES = [
    "300308", "300502", "688256", "002384", "301308", "603986", "002371",
    "688008", "001309", "603256", "688525", "300223", "600667",
    "000021", "688361", "688409",
]
ETF_CODES = ["159516", "159558", "589130"]
TOP_N = 300


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


def load_ckpt():
    if CKPT.exists():
        return json.loads(CKPT.read_text(encoding="utf-8"))
    return {"pools_done": [], "hist_done": [], "fails": []}


def save_ckpt(c):
    CKPT.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def retry(fn, n=3, wait=5):
    for i in range(n):
        try:
            return fn()
        except Exception:
            if i == n - 1:
                raise
            time.sleep(wait)


def read_csv_dict(path):
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def trade_days(start, end):
    """交易日历:优先新浪工具表,失败则用平安银行日线的日期序列兜底。"""
    try:
        df = retry(lambda: ak.tool_trade_date_hist_sina())
        days = [str(d).replace("-", "") for d in df["trade_date"].astype(str)]
    except Exception:
        df = retry(lambda: ak.stock_zh_a_hist(symbol="000001", period="daily",
                                              start_date=start, end_date=end, adjust=""))
        days = [str(d).replace("-", "") for d in df["日期"].astype(str)]
    return [d for d in days if start <= d <= end]


def build_universe():
    """结算宇宙:推文提及 ∪ AI名单 ∪ 市值前300。返回 {代码: 名称}。"""
    stocks = read_csv_dict("data/latest/all_stocks.csv")
    name2code = {}
    for r in stocks:
        code, name = r.get("代码", "").zfill(6), (r.get("名称") or "").strip()
        if code and name:
            name2code[name] = code
    uni = {}

    # 1) AI复合体固定名单
    code2name = {c: n for n, c in name2code.items()}
    for c in AI_CODES:
        uni[c] = code2name.get(c, c)

    # 2) 推文提及:全名 / 去ST·退市前缀 / 空格变体 / 6位代码 / $代码
    text_all = ""
    if TWEETS.exists():
        for ln in TWEETS.read_text(encoding="utf-8").split("\n"):
            if ln.strip():
                try:
                    text_all += json.loads(ln).get("text", "") + "\n"
                except json.JSONDecodeError:
                    pass
    mentioned = 0
    for name, code in name2code.items():
        variants = {name, name.replace(" ", ""), re.sub(r"^(\*?ST|XD|XR|DR)", "", name)}
        if any(v and v in text_all for v in variants):
            if code not in uni:
                uni[code] = name
                mentioned += 1
    for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text_all):
        c = m.group(1)
        if c in code2name and c not in uni:
            uni[c] = code2name[c]
            mentioned += 1

    # 3) 市值前300
    def f0(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0
    ranked = sorted(stocks, key=lambda r: -f0(r.get("总市值")))
    for r in ranked[:TOP_N]:
        c = r.get("代码", "").zfill(6)
        if c not in uni:
            uni[c] = (r.get("名称") or "").strip()

    print(f"[宇宙] 共{len(uni)}只(推文提及新增{mentioned},AI名单{len(AI_CODES)},市值前{TOP_N})")
    return uni


def append_day_rows(day_rows, date, rows):
    day_rows.setdefault(date, []).extend(rows)


def hist_one(code, name, start, end, is_etf=False):
    """一只标的整段日线 → [(date, row), ...]"""
    if is_etf:
        df = retry(lambda: ak.fund_etf_hist_em(symbol=code, period="daily",
                                               start_date=start, end_date=end, adjust=""))
    else:
        df = retry(lambda: ak.stock_zh_a_hist(symbol=code, period="daily",
                                              start_date=start, end_date=end, adjust=""))
    out = []
    for _, r in df.iterrows():
        date = str(r["日期"]).replace("-", "")[:8]
        close, chg = float(r["收盘"]), float(r.get("涨跌额") or 0)
        out.append((date, {
            "代码": code, "名称": name,
            "开盘": r["开盘"], "最高": r["最高"], "最低": r["最低"], "收盘": r["收盘"],
            "昨收": round(close - chg, 4), "涨跌幅": r["涨跌幅"], "成交额": r["成交额"],
        }))
    return out


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20260511"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260702"
    ck = load_ckpt()
    days = trade_days(start, end)
    print(f"[日历] {start}~{end} 共{len(days)}个交易日")

    # ---- 1) 情绪面板逐日回填 ----
    pool_jobs = {"zt_pool.csv": ak.stock_zt_pool_em,
                 "zb_pool.csv": ak.stock_zt_pool_zbgc_em,
                 "dt_pool.csv": ak.stock_zt_pool_dtgc_em}
    for d in days:
        if d in ck["pools_done"]:
            continue
        ok = True
        for fname, fn in pool_jobs.items():
            try:
                df = retry(lambda fn=fn, d=d: fn(date=d))
                Path(f"data/{d}").mkdir(parents=True, exist_ok=True)
                df.to_csv(f"data/{d}/{fname}", index=False, encoding="utf-8-sig")
            except Exception as e:
                ok = False
                ck["fails"].append(f"pool {d} {fname} {type(e).__name__}")
            time.sleep(0.6)
        if ok:
            ck["pools_done"].append(d)
        save_ckpt(ck)
        print(f"[情绪面板] {d} {'OK' if ok else 'FAIL(已记录)'} ({days.index(d)+1}/{len(days)})")

    # ---- 2) 结算宇宙 + ETF ----
    uni = build_universe()
    etf_names = {}
    try:
        for r in read_csv_dict("data/latest/etf.csv"):
            etf_names[r.get("代码", "")] = (r.get("名称") or "").strip()
    except Exception:
        pass
    targets = [(c, n, False) for c, n in uni.items()] + \
              [(c, etf_names.get(c, c), True) for c in ETF_CODES]

    day_rows = {}
    todo = [t for t in targets if t[0] not in ck["hist_done"]]
    print(f"[日线] 需拉{len(todo)}只(已完成{len(targets)-len(todo)}只将从缓存补齐)")
    # 注意:hist_done 的缓存粒度是"整段",断点续跑时已完成标的的行不在内存里,
    # 因此续跑会重建当日文件缺失的部分——用"追加+去重"落盘保证幂等。
    for i, (code, name, is_etf) in enumerate(todo):
        try:
            for date, row in hist_one(code, name, start, end, is_etf):
                if start <= date <= end:
                    append_day_rows(day_rows, date, [row])
            ck["hist_done"].append(code)
        except Exception as e:
            ck["fails"].append(f"hist {code} {type(e).__name__}")
            print(f"  [日线] {code} {name} FAIL {type(e).__name__}")
        if i % 20 == 0:
            save_ckpt(ck)
            print(f"  [日线] 进度 {i+1}/{len(todo)}")
        time.sleep(0.5)
    save_ckpt(ck)

    # ---- 3) 落盘(追加+按代码去重,幂等) ----
    import csv
    cols = ["代码", "名称", "开盘", "最高", "最低", "收盘", "昨收", "涨跌幅", "成交额"]
    for date, rows in sorted(day_rows.items()):
        p = Path(f"data/{date}/hist_universe.csv")
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = {}
        if p.exists():
            for r in read_csv_dict(p):
                merged[r.get("代码", "").zfill(6)] = r
        for r in rows:
            merged[str(r["代码"]).zfill(6)] = {k: r.get(k, "") for k in cols}
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for _, r in sorted(merged.items()):
                w.writerow({k: r.get(k, "") for k in cols})
    print(f"[落盘] hist_universe.csv 写入{len(day_rows)}个交易日")

    fails = ck["fails"]
    msg = (f"OK {start}~{end} 交易日{len(days)}个 情绪面板完成{len(ck['pools_done'])}日 "
           f"日线完成{len(ck['hist_done'])}只 失败{len(fails)}项")
    if fails:
        msg += f"(前5项:{'; '.join(fails[:5])})"
    update_status(f"HIST-行情回填 {start}~{end}", msg)
    print(msg)


if __name__ == "__main__":
    main()
