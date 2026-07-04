#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场盘后数据包 · 本地与GitHub Action共用
一次运行拉取整个A股市场的当日数据,不再依赖任何"观察列表"。

用法:
  pip install akshare pandas -U
  python fetch_market_data.py            # 默认今天
  python fetch_market_data.py 20260706   # 指定日期(仅影响涨停/炸板/跌停池)

输出: data/YYYYMMDD/ 与 data/latest/ 各一份,含:
  all_stocks.csv  全A快照(5000+只):代码/名称/开高低收/涨跌幅/成交额/换手/市值 —— 收盘后即当日日K
  etf.csv         全ETF快照
  zt_pool.csv     涨停池(含连板数、封单额、首次/最后封板时间) → 连板梯队、涨停家数
  zb_pool.csv     炸板池 → 炸板率
  dt_pool.csv     跌停池
  industry.csv    行业板块涨跌表
  concept.csv     概念板块涨跌表
以上合计约1-2MB,足以离线计算:赚钱效应、连板梯队与晋级率、炸板率、
板块强弱轮动、任意个股结算——即情绪OS的全部L1温度计读数。

注:接口名基于akshare稳定版;若报错先 pip install akshare -U。
"""
import sys
import os
import time
import datetime as dt

import re

import akshare as ak
import requests

# ---------------------------------------------------------------------------
# 补丁(2026-07-04):akshare 把行情接口写死在 82/17/79/88.push2.eastmoney.com 等
# 东财编号镜像节点上,这些节点会轮动式地直接切断 GitHub Actions 服务器的连接
# (RemoteDisconnected)。经在 GitHub 服务器上实测,push2delay.eastmoney.com
# (延时行情节点)对同一 API 全部畅通。延时节点数据晚15分钟,而本任务在收盘后
# (北京时间15:10之后)运行,此时延时数据与最终收盘数据完全一致。
# 策略:每个请求先试原节点;一旦遇到连接被切断,本次运行内全部改走延时节点。
# 不影响 push2his / push2ex 等其他域名。
# ---------------------------------------------------------------------------
_orig_session_request = requests.Session.request
_delay_mode = False


def _rerouted_request(self, method, url, *args, **kwargs):
    global _delay_mode
    if isinstance(url, str):
        m = re.match(r"https?://\d+\.push2\.eastmoney\.com(/.*)", url)
        if m:
            if not _delay_mode:
                try:
                    return _orig_session_request(self, method, url, *args, **kwargs)
                except requests.exceptions.ConnectionError:
                    _delay_mode = True
                    print(f"[补丁] 原节点被拒,改走延时节点: {url.split('/api')[0]}")
            url = "https://push2delay.eastmoney.com" + m.group(1)
    return _orig_session_request(self, method, url, *args, **kwargs)


requests.Session.request = _rerouted_request


def save(df, folder, name):
    os.makedirs(folder, exist_ok=True)
    df.to_csv(os.path.join(folder, name), index=False, encoding="utf-8-sig")


def retry(fn, n=3, wait=6):
    for i in range(n):
        try:
            return fn()
        except Exception:
            if i == n - 1:
                raise
            time.sleep(wait)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().strftime("%Y%m%d")
    out, latest = f"data/{date}", "data/latest"

    jobs = {
        "all_stocks.csv": lambda: ak.stock_zh_a_spot_em(),
        "etf.csv":        lambda: ak.fund_etf_spot_em(),
        "zt_pool.csv":    lambda: ak.stock_zt_pool_em(date=date),
        "zb_pool.csv":    lambda: ak.stock_zt_pool_zbgc_em(date=date),
        "dt_pool.csv":    lambda: ak.stock_zt_pool_dtgc_em(date=date),
        "industry.csv":   lambda: ak.stock_board_industry_name_em(),
        "concept.csv":    lambda: ak.stock_board_concept_name_em(),
    }

    report = [date]
    for name, fn in jobs.items():
        try:
            df = retry(fn)
            save(df, out, name)
            save(df, latest, name)
            report.append(f"OK   {name}  {len(df)}行")
        except Exception as e:
            report.append(f"FAIL {name}  {type(e).__name__}: {e}")

    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    # latest 也放一份报告,便于远端确认数据日期
    with open(os.path.join(latest, "_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("\n".join(report))


if __name__ == "__main__":
    main()
