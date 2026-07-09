#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
散户群体情绪抓取(本地优先,海外CI兜底) —— 情绪框架 v2
测的是【大量散户的集体状态】,不是任何单人推文。TingHu 推文不在本脚本触及范围。

隔离约束(硬):只读现有 data/(复用涨跌停/涨跌家数),只写 sentiment/ 下产物,
绝不修改任何现有文件。每个数据源独立 try/except:某源失败(常见=东财/同花顺对海外IP
地域风控)只在 sentiment/_status.txt 记 "海外不可达,待本地补",不伪造、不阻塞其他源。

用法:
  pip install akshare pandas -U
  python sentiment/fetch_retail_sentiment.py                 # 今天
  python sentiment/fetch_retail_sentiment.py 20260616        # 指定日(快照源仍只能取当天)
  BACKFILL_START=20260601 BACKFILL_END=20260702 python sentiment/fetch_retail_sentiment.py
        # 对有历史的源(两融)+ 从data/派生的赚钱效应,回填一个区间

产物(全部 sentiment/ 下,长表 date,source,metric,value):
  market_daily.csv        市场级:赚钱效应/活跃度(legu)、涨跌停家数(复用data/)、两融余额
  sector_flow_YYYYMMDD.csv 板块资金流排行(散户/主力往哪个题材涌)
  watch_sentiment.csv     关注清单个股:人气榜排名、雪球关注度(个股散户资金流海外被墙,标注)
  account_open.csv        月度新增开户数(散户入场节奏)
"""
import datetime as dt
import os
import sys
from pathlib import Path

import akshare as ak
import pandas as pd

SENT = Path("sentiment")
DATA = Path("data")
STATUS = SENT / "_status.txt"
CST = dt.timezone(dt.timedelta(hours=8))

# 关注清单(可配置):存储/AI链 —— 散户情绪最集中的题材
WATCH = {
    "300308": "中际旭创", "300502": "新易盛", "688256": "寒武纪", "002384": "东山精密",
    "301308": "江波龙", "603986": "兆易创新", "002371": "北方华创", "688008": "澜起科技",
    "001309": "德明利", "603256": "宏和科技", "688525": "佰维存储", "300223": "北京君正",
    "600667": "太极实业", "000021": "深科技", "688361": "中科飞测", "688409": "富创精密",
}

GEO_ERRORS = ("ProxyError", "ConnectionError", "RemoteDisconnected", "MaxRetryError",
              "ConnectTimeout", "ReadTimeout", "SSLError")


def now():
    return dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def status(source, line):
    """更新 sentiment/_status.txt 中本源的状态行,不覆盖其他源。"""
    SENT.mkdir(parents=True, exist_ok=True)
    rows = {}
    if STATUS.exists():
        for ln in STATUS.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {now()} | {line}"
    STATUS.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def note_fail(source, e):
    name = type(e).__name__
    if name in GEO_ERRORS or "No tables found" in str(e):
        status(source, f"FAIL 海外不可达(地域风控?),待本地补:{name}")
    else:
        status(source, f"FAIL {name}: {str(e)[:80]}")


def append_long(path, new_rows):
    """长表(date,source,metric,value)追加+去重(同 date+source+metric 覆盖)。"""
    cols = ["date", "source", "metric", "value"]
    df_new = pd.DataFrame(new_rows, columns=cols)
    if path.exists():
        df_old = pd.read_csv(path, dtype={"date": str})
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    df["date"] = df["date"].astype(str)
    df = df.drop_duplicates(subset=["date", "source", "metric"], keep="last")
    df = df.sort_values(["date", "source", "metric"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_data_csv(date, name):
    p = DATA / date / name
    if p.exists():
        return pd.read_csv(p, encoding="utf-8-sig")
    return None


def breadth_rows_from_data(date):
    """从现有 data/(只读)派生当日赚钱效应/涨跌停家数。date=YYYYMMDD。"""
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    rows = []
    # 涨跌家数:优先全A快照,回退结算宇宙
    for src_name, src in [("all_stocks.csv", "全A"), ("hist_universe.csv", "宇宙")]:
        df = read_data_csv(date, src_name)
        if df is not None and "涨跌幅" in df.columns:
            pct = pd.to_numeric(df["涨跌幅"], errors="coerce")
            up, down = int((pct > 0).sum()), int((pct < 0).sum())
            rows.append([iso, f"data-{src}", "上涨家数", up])
            rows.append([iso, f"data-{src}", "下跌家数", down])
            if up + down > 0:
                rows.append([iso, f"data-{src}", "赚钱效应_涨跌比", round(up / (up + down) * 100, 2)])
            break
    # 涨停/炸板/跌停家数(池数据仅近30交易日源端可得;缺则跳过)
    for name, metric in [("zt_pool.csv", "涨停家数"), ("zb_pool.csv", "炸板家数"), ("dt_pool.csv", "跌停家数")]:
        df = read_data_csv(date, name)
        if df is not None:
            rows.append([iso, "data-池", metric, len(df)])
    return rows


def fetch_margin(iso_dates):
    """两融余额(上交所,有历史)。iso_dates=需要覆盖的ISO日期集合。"""
    if not iso_dates:
        return []
    ymd = sorted(d.replace("-", "") for d in iso_dates)
    try:
        df = ak.stock_margin_sse(start_date=ymd[0], end_date=ymd[-1])
    except ValueError:
        return []  # akshare 对空响应(如当日未发布)会抛Length mismatch,视作暂无数据
    if df is None or not len(df):
        return []
    rows = []
    for _, r in df.iterrows():
        d = str(r["信用交易日期"])
        d = d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"
        try:
            rows.append([d, "sse", "融资余额", float(r["融资余额"])])
        except (TypeError, ValueError):
            pass
    return rows


def fetch_market_activity(iso_today):
    """乐咕市场赚钱效应/活跃度(快照,仅当天)。"""
    df = ak.stock_market_activity_legu()
    kv = {str(r["item"]).strip(): r["value"] for _, r in df.iterrows()}
    rows = []
    for item in ("上涨", "下跌", "涨停", "跌停", "活跃度", "真实涨停", "真实跌停"):
        if item in kv:
            v = kv[item]
            if isinstance(v, str) and v.endswith("%"):
                try:
                    v = float(v.rstrip("%"))
                except ValueError:
                    continue
            rows.append([iso_today, "legu", item, v])
    return rows


def do_market_daily(iso_today, backfill):
    """市场级 market_daily.csv:赚钱效应(legu今天)+涨跌停家数(data/派生)+两融余额。"""
    path = SENT / "market_daily.csv"
    all_rows, ok_sources = [], []

    # 赚钱效应/涨跌停:从 data/ 派生。区间=回填窗口 或 仅今天
    if backfill:
        s, e = backfill
        dates = [p.name for p in sorted(DATA.glob("2026*")) if s <= p.name <= e]
    else:
        dates = [iso_today.replace("-", "")]
    br = []
    for d in dates:
        br.extend(breadth_rows_from_data(d))
    if br:
        all_rows.extend(br)
        ok_sources.append(f"赚钱效应/涨跌停(data派生{len(dates)}日)")

    # 市场活跃度(legu 快照,仅今天)
    try:
        r = fetch_market_activity(iso_today)
        if r:
            all_rows.extend(r)
            ok_sources.append("legu活跃度")
    except Exception as e:
        note_fail("legu市场活跃度", e)

    # 两融余额(有历史)
    try:
        want = {r[0] for r in br} or {iso_today}
        r = fetch_margin(want)
        if r:
            all_rows.extend(r)
            ok_sources.append(f"两融({len(r)}日)")
    except Exception as e:
        note_fail("两融margin_sse", e)

    if all_rows:
        append_long(path, all_rows)
        status("market_daily", f"OK 源:{'、'.join(ok_sources)}")
    else:
        status("market_daily", "FAIL 无任何市场级源可用")


def do_sector_flow(iso_today):
    """板块资金流排行(快照)。"""
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    except Exception as e:
        note_fail("板块资金流sector_flow", e)
        return
    keep = [c for c in ["名称", "今日涨跌幅", "今日主力净流入-净额", "今日主力净流入-净占比"] if c in df.columns]
    out = df[keep].copy()
    out.insert(0, "date", iso_today)
    p = SENT / f"sector_flow_{iso_today.replace('-', '')}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False, encoding="utf-8-sig")
    status("sector_flow", f"OK {len(out)}个板块 → {p.name}")


def do_watch(iso_today):
    """关注清单个股散户情绪:人气榜排名 + 雪球关注度(个股资金流海外被墙,标注)。"""
    rows, srcs = [], []
    # 东财人气榜(散户注意力)
    try:
        hr = ak.stock_hot_rank_em()
        code_col = "代码" if "代码" in hr.columns else hr.columns[1]
        rank_col = "当前排名" if "当前排名" in hr.columns else hr.columns[0]
        rankmap = {}
        for _, r in hr.iterrows():
            c = str(r[code_col])[-6:]
            rankmap[c] = r[rank_col]
        for code, name in WATCH.items():
            if code in rankmap:
                rows.append([iso_today, code, name, "东财人气榜", "排名", rankmap[code]])
        srcs.append(f"东财人气榜(清单命中{sum(1 for c in WATCH if c in rankmap)})")
    except Exception as e:
        note_fail("东财人气榜hot_rank", e)
    # 雪球关注度(散户社区关注)
    try:
        xq = ak.stock_hot_follow_xq(symbol="最热门")
        ccol = "股票代码" if "股票代码" in xq.columns else xq.columns[0]
        fcol = "关注" if "关注" in xq.columns else xq.columns[2]
        fmap = {}
        for _, r in xq.iterrows():
            fmap[str(r[ccol])[-6:]] = r[fcol]
        for code, name in WATCH.items():
            if code in fmap:
                rows.append([iso_today, code, name, "雪球", "关注度", fmap[code]])
        srcs.append(f"雪球关注(清单命中{sum(1 for c in WATCH if c in fmap)})")
    except Exception as e:
        note_fail("雪球关注hot_follow_xq", e)
    # 个股散户资金流:海外被墙,显式标注不伪造
    status("个股散户资金流", "SKIP individual_fund_flow 走push2his海外被墙,待本地补")

    if rows:
        path = SENT / "watch_sentiment.csv"
        cols = ["date", "code", "name", "source", "metric", "value"]
        df_new = pd.DataFrame(rows, columns=cols)
        if path.exists():
            df_old = pd.read_csv(path, dtype={"date": str, "code": str})
            df = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df = df_new
        df["date"] = df["date"].astype(str)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df = df.drop_duplicates(subset=["date", "code", "source", "metric"], keep="last")
        df = df.sort_values(["date", "code", "source"]).reset_index(drop=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        status("watch_sentiment", f"OK 源:{'、'.join(srcs)}")
    else:
        status("watch_sentiment", "FAIL 人气/关注源均不可达")


def do_account():
    """月度新增开户数(散户入场节奏)。"""
    try:
        df = ak.stock_account_statistics_em()
    except Exception as e:
        note_fail("开户数account_statistics", e)
        return
    p = SENT / "account_open.csv"
    df.to_csv(p, index=False, encoding="utf-8-sig")
    status("account_open", f"OK {len(df)}期月度开户数 → {p.name}")


def main():
    SENT.mkdir(parents=True, exist_ok=True)
    argdate = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().strftime("%Y%m%d")
    iso_today = f"{argdate[:4]}-{argdate[4:6]}-{argdate[6:]}"
    bs, be = os.environ.get("BACKFILL_START"), os.environ.get("BACKFILL_END")
    backfill = (bs, be) if bs and be else None

    if STATUS.exists():
        STATUS.unlink()  # 每次运行重置为当次快照,避免上一次的陈旧FAIL行误导
    status("run", f"START 日期={iso_today}" + (f" 回填={bs}~{be}" if backfill else ""))
    do_market_daily(iso_today, backfill)
    do_sector_flow(iso_today)
    do_watch(iso_today)
    do_account()
    status("run", "DONE")
    print("done; 见 sentiment/_status.txt")


if __name__ == "__main__":
    main()
