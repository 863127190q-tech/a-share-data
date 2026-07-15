#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C1 个股资金流分层抓取(主力/超大单/大单/中单/小单)—— 本地为主,CI兜底。
接口 ak.stock_individual_fund_flow(stock, market) 返回五档分层 + 约100+交易日历史。
海外直连东财 push2his 地域墙(JSONDecodeError/ProxyError)→ 必须国内本地跑;CI必然失败并标注,设计如此。

用法(国内机器,关VPN):
  ./.venv/bin/python sentiment/fundflow/fetch_fundflow.py            # 抓接口能给的全部历史
  BACKFILL_START=20260601 BACKFILL_END=20260714 ... fetch_fundflow.py # 仅保留窗口内(可选)

产物: sentiment/fundflow/individual_flow.csv(累积,按 date+code 去重幂等)
  列: date, code, name, 主力净额, 超大单净额, 大单净额, 中单净额, 小单净额(=散户), 涨跌幅
每票独立 try/except;单票失败不影响其他;失败/被墙记 sentiment/_status.txt。
边界:主力/散户是按单笔金额分档的推断(超大单≈机构、小单≈散户),非真实席位,有噪音,作方向性参考。
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

import akshare as ak
import pandas as pd

FF = Path(__file__).resolve().parent
SENT = FF.parent
OUT = FF / "individual_flow.csv"
STATUS = SENT / "_status.txt"
CST = dt.timezone(dt.timedelta(hours=8))
GEO_ERRORS = ("ProxyError", "ConnectionError", "RemoteDisconnected", "MaxRetryError",
              "ConnectTimeout", "ReadTimeout", "SSLError", "JSONDecodeError")

COLMAP = [
    ("主力净额", "主力净流入-净额"), ("超大单净额", "超大单净流入-净额"),
    ("大单净额", "大单净流入-净额"), ("中单净额", "中单净流入-净额"),
    ("小单净额", "小单净流入-净额"),
]


def status(source, line):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    rows = {}
    if STATUS.exists():
        for ln in STATUS.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {now} | {line}"
    STATUS.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def market_of(code):
    return "sh" if code[0] in ("5", "6", "9") else "sz"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return ""


def main():
    watch = json.loads((FF / "watchlist.json").read_text(encoding="utf-8"))["watchlist"]
    bs, be = os.environ.get("BACKFILL_START"), os.environ.get("BACKFILL_END")
    win = (f"{bs[:4]}-{bs[4:6]}-{bs[6:]}", f"{be[:4]}-{be[4:6]}-{be[6:]}") if bs and be else None

    all_rows, ok, fails = [], 0, []
    for code, name in watch.items():
        try:
            df = ak.stock_individual_fund_flow(stock=code, market=market_of(code))
        except Exception as e:
            fails.append(f"{code}:{type(e).__name__}")
            continue
        if df is None or not len(df):
            fails.append(f"{code}:空")
            continue
        cols = {out: src for out, src in COLMAP if src in df.columns}
        if "主力净额" not in cols:
            fails.append(f"{code}:无分层列")
            continue
        ok += 1
        for _, r in df.iterrows():
            d = str(r["日期"])
            d = d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"
            if win and not (win[0] <= d <= win[1]):
                continue
            row = {"date": d, "code": code, "name": name}
            for out, src in cols.items():
                row[out] = num(r[src])
            row["涨跌幅"] = num(r.get("涨跌幅"))
            all_rows.append(row)
        time.sleep(0.4)

    if not all_rows:
        status("个股资金流分层", f"FAIL 0/{len(watch)}票(海外墙必然;需国内本地跑)。失败样例:{';'.join(fails[:4])}")
        print("FAIL: 无数据(海外被墙,需国内本地跑)")
        return

    cols = ["date", "code", "name", "主力净额", "超大单净额", "大单净额", "中单净额", "小单净额", "涨跌幅"]
    df_new = pd.DataFrame(all_rows).reindex(columns=cols)
    if OUT.exists():
        old = pd.read_csv(OUT, dtype={"date": str, "code": str})
        df_new = pd.concat([old, df_new], ignore_index=True)
    df_new["date"] = df_new["date"].astype(str)
    df_new["code"] = df_new["code"].astype(str).str.zfill(6)
    df_new = df_new.drop_duplicates(subset=["date", "code"], keep="last")
    df_new = df_new.sort_values(["date", "code"]).reset_index(drop=True)
    OUT.write_text(df_new.to_csv(index=False), encoding="utf-8-sig")
    span = f"{df_new['date'].min()}~{df_new['date'].max()}"
    msg = f"OK {ok}/{len(watch)}票 × {span} → individual_flow.csv({len(df_new)}行)"
    if fails:
        msg += f";失败{len(fails)}票:{';'.join(fails[:4])}"
    status("个股资金流分层", msg)
    print(msg)


if __name__ == "__main__":
    main()
