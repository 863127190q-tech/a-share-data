#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补齐三个"海外墙"源的历史 —— 情绪框架 v2(必须在【国内出口】本地跑)
- 个股散户资金流(小单净额,散户情绪核心代理)→ 聚合成市场级"散户资金净流向"
- 个股人气榜历史明细(排名)→ 聚合成"人气排名均值"(越低越热)
- 板块资金流历史(可得则收)
这三个源走东财 push2his/push2,海外节点(含本CI/沙盒代理)地域风控;
本脚本每次调用带重试碰轮动窗口,抓到几只就聚合几只,覆盖率写 _status.txt,不伪造。

用法(国内机器):
  BACKFILL_START=20260601 BACKFILL_END=20260702 python sentiment/backfill_retail_hist.py
产物:
  - 散户资金净流向/人气排名均值 → 追加进 market_daily.csv(source=agg)
  - 板块资金流历史(全行业逐日主力净流入)→ sentiment/sector_flow_hist.csv
    (date,sector,主力净流入亿元,主力净占比;判读板块跷跷板/吸血效应的直接仪器)
  覆盖率与不可达写 sentiment/_status.txt。无历史窗口环境变量时默认近45天(日常追加用)。
"""
import os
import time
from pathlib import Path

import akshare as ak
import pandas as pd

from fetch_retail_sentiment import WATCH, SENT, append_long, status, GEO_ERRORS

MARKET_DAILY = SENT / "market_daily.csv"


def _mkt(code):
    return "sh" if code[0] in ("5", "6", "9") else "sz"


def retry(fn, tries=8, wait=3):
    last = None
    for _ in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa
            last = e
            if type(e).__name__ not in GEO_ERRORS and "connect" not in str(e).lower():
                break
            time.sleep(wait)
    raise last


def iso(d):
    d = str(d).replace("-", "")[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def backfill(start_iso, end_iso):
    # ---- 1) 散户资金净流向:各关注股小单净额,按日聚合 ----
    retail = {}   # date -> 累计小单净额
    hit_ff = 0
    for code, name in WATCH.items():
        try:
            df = retry(lambda code=code: ak.stock_individual_fund_flow(stock=code, market=_mkt(code)))
        except Exception:
            continue
        col = next((c for c in df.columns if "小单" in c and "净额" in c), None)
        if col is None:
            continue
        hit_ff += 1
        for _, r in df.iterrows():
            d = iso(r["日期"])
            if start_iso <= d <= end_iso:
                try:
                    retail[d] = retail.get(d, 0.0) + float(r[col])
                except (TypeError, ValueError):
                    pass
        time.sleep(0.5)

    # ---- 2) 人气排名均值:各关注股人气历史明细,按日聚合 ----
    rank_sum, rank_cnt = {}, {}
    hit_hr = 0
    for code in WATCH:
        sym = f"{_mkt(code).upper()}{code}"
        try:
            df = retry(lambda sym=sym: ak.stock_hot_rank_detail_em(symbol=sym))
        except Exception:
            continue
        tcol = "时间" if "时间" in df.columns else df.columns[0]
        rcol = "排名" if "排名" in df.columns else None
        if rcol is None:
            continue
        hit_hr += 1
        for _, r in df.iterrows():
            d = iso(r[tcol])
            if start_iso <= d <= end_iso:
                try:
                    rank_sum[d] = rank_sum.get(d, 0.0) + float(r[rcol])
                    rank_cnt[d] = rank_cnt.get(d, 0) + 1
                except (TypeError, ValueError):
                    pass
        time.sleep(0.5)

    rows = []
    for d, v in sorted(retail.items()):
        rows.append([d, "agg", "散户资金净流向", round(v, 0)])
    for d in sorted(rank_cnt):
        rows.append([d, "agg", "人气排名均值", round(rank_sum[d] / rank_cnt[d], 1)])
    if rows:
        append_long(MARKET_DAILY, rows)

    status("散户资金净流向", f"{'OK' if hit_ff else 'FAIL'} 覆盖{hit_ff}/{len(WATCH)}只关注股(海外墙轮动;需国内出口补全)")
    status("人气排名均值", f"{'OK' if hit_hr else 'FAIL'} 覆盖{hit_hr}/{len(WATCH)}只关注股")

    # ---- 3) 板块资金流历史(逐板块回填 + 每日追加;判读板块跷跷板/吸血效应的直接仪器)----
    do_sector_flow_hist(start_iso, end_iso)


SECTOR_HIST = SENT / "sector_flow_hist.csv"


def do_sector_flow_hist(start_iso, end_iso):
    """全行业板块逐日主力净流入历史 → sentiment/sector_flow_hist.csv
    (date,sector,主力净流入亿元,主力净占比);幂等合并。海外墙则整项标注待本地补,不伪造。
    资金在板块间的此消彼长=跷跷板;某板块大额净流出伴另一板块净流入=吸血。"""
    try:
        names_df = retry(lambda: ak.stock_board_industry_name_em(), tries=4)
    except Exception:
        status("板块资金流历史", "FAIL 板块列表海外不可达,待本地补(整项)")
        return
    ncol = "板块名称" if "板块名称" in names_df.columns else names_df.columns[1]
    sectors = [str(x).strip() for x in names_df[ncol] if str(x).strip()]

    rows, hit = [], 0
    for name in sectors:
        try:
            df = retry(lambda name=name: ak.stock_sector_fund_flow_hist(symbol=name), tries=3)
        except Exception:
            continue
        dcol = "日期" if "日期" in df.columns else df.columns[0]
        amt = next((c for c in df.columns if "主力" in c and "净额" in c), None)
        pct = next((c for c in df.columns if "主力" in c and "净占比" in c), None)
        if amt is None:
            continue
        hit += 1
        for _, r in df.iterrows():
            d = iso(r[dcol])
            if start_iso <= d <= end_iso:
                try:
                    yi = round(float(r[amt]) / 1e8, 3)  # 元→亿元
                except (TypeError, ValueError):
                    continue
                p = ""
                try:
                    p = round(float(r[pct]), 2)
                except (TypeError, ValueError):
                    p = ""
                rows.append([d, name, yi, p])
        time.sleep(0.4)

    if not rows:
        status("板块资金流历史", f"FAIL 板块列表{len(sectors)}个但历史全部海外墙,待本地补")
        return
    cols = ["date", "sector", "主力净流入亿元", "主力净占比"]
    df_new = pd.DataFrame(rows, columns=cols)
    if SECTOR_HIST.exists():
        old = pd.read_csv(SECTOR_HIST, dtype={"date": str, "sector": str})
        df_new = pd.concat([old, df_new], ignore_index=True)
    df_new["date"] = df_new["date"].astype(str)
    df_new = df_new.drop_duplicates(subset=["date", "sector"], keep="last")
    df_new = df_new.sort_values(["date", "主力净流入亿元"], ascending=[True, False]).reset_index(drop=True)
    df_new.to_csv(SECTOR_HIST, index=False, encoding="utf-8-sig")
    span = f"{df_new['date'].min()}~{df_new['date'].max()}"
    status("板块资金流历史", f"OK 覆盖{hit}/{len(sectors)}个板块 × {span} → sector_flow_hist.csv")
    print(f"散户资金净流向 覆盖{hit_ff}/{len(WATCH)}; 人气 覆盖{hit_hr}/{len(WATCH)}; 板块历史 {'OK' if sector_ok else 'FAIL'}")


def main():
    import datetime as dt
    today = dt.date.today()
    # 默认滚动窗口:近45天(日常滚动用);首次历史回填用环境变量显式指定区间
    bs = os.environ.get("BACKFILL_START") or (today - dt.timedelta(days=45)).strftime("%Y%m%d")
    be = os.environ.get("BACKFILL_END") or today.strftime("%Y%m%d")
    backfill(iso(bs), iso(be))


if __name__ == "__main__":
    main()
