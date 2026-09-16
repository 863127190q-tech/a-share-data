#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
哨兵自动发现 · 第4步:过往战绩回测与打分(决定谁才是真哨兵)
输入: sentiment/sentinel/stance_{市场}.csv(判读产物: handle,date,stance,fomo,n_tweets)
基准: A股=本地data/结算宇宙等权指数;美股=新浪日线(QQQ);加密=Binance(BTCUSDT)
核心指标(对每个账号):
  lead = 该账号"看多日"之后的N日平均收益 − "看空日"之后的N日平均收益
    lead > 0 → 风向标(说对方向,情绪领先行情同向)
    lead < 0 → 反向指标(他一喊多就跌 / 喊空就涨,负相关同样有用,且往往更值钱)
  |lead| 越大信号越强;另给 hit(方向命中率)与样本量,样本<5天不下结论。
产物: sentiment/sentinel/scores_{市场}.csv + sentinel_compare.md(三市场横向比较)
边界:窗口仅2-3个月、样本有限,这是"筛选器"不是"业绩证明";负lead同样可用但需更多样本确认。
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd

SD = Path(__file__).resolve().parent
SENT = SD.parent
REPO = SENT.parent
CFG = json.loads((SD / "markets.json").read_text(encoding="utf-8"))
FWD = [1, 3, 5]  # 前瞻天数


def bench_a_share(symbol="sh000300"):
    """A股:真指数(沪深300,新浪源)。
    注:早先试过用本地结算宇宙等权均价自造指数,但前期hist_universe(330只大票)
    与后期all_stocks(5800只)成分不同,均价直接跳水造出-80%的假跌幅,已弃用。"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    return pd.Series(pd.to_numeric(df["close"], errors="coerce").values,
                     index=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")).sort_index()


def bench_us(symbol="QQQ"):
    import akshare as ak
    df = ak.stock_us_daily(symbol=symbol)
    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").values,
                  index=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"))
    return s.sort_index()


def bench_binance(symbol="BTCUSDT"):
    import requests
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol, "interval": "1d", "limit": 400}, timeout=20)
    r.raise_for_status()
    rows = {}
    for k in r.json():
        d = dt.datetime.utcfromtimestamp(k[0] / 1000).strftime("%Y-%m-%d")
        rows[d] = float(k[4])
    return pd.Series(rows).sort_index()


def get_bench(market):
    b = CFG["markets"][market]["bench"]
    if b["kind"] == "local_universe":
        return bench_a_share(b.get("symbol", "sh000300")), b.get("symbol", "sh000300")
    if b["kind"] == "us_daily":
        return bench_us(b["symbol"]), b["symbol"]
    return bench_binance(b["symbol"]), b["symbol"]


def fwd_returns(px):
    """各前瞻期收益(按该序列自身的交易日)。"""
    out = {}
    for n in FWD:
        out[n] = (px.shift(-n) / px - 1) * 100
    return out


def score_market(market):
    sp = SD / f"stance_{market}.csv"
    if not sp.exists():
        return None, f"{market}: 无 stance_{market}.csv(判读未完成)"
    st = pd.read_csv(sp, dtype={"date": str, "handle": str})
    try:
        px, bname = get_bench(market)
    except Exception as e:
        return None, f"{market}: 基准获取失败 {type(e).__name__}"
    fw = fwd_returns(px)
    idx = set(px.index)

    rows = []
    for h, sub in st.groupby("handle"):
        rec = {"handle": h, "market": market, "bench": bname}
        # 只保留基准有交易的日期(A股/美股跳过周末;加密全周)
        sub = sub[sub["date"].isin(idx)]
        bull = sub[sub["stance"] == "看多"]["date"].tolist()
        bear = sub[sub["stance"] == "看空"]["date"].tolist()
        rec["n_看多"], rec["n_看空"] = len(bull), len(bear)
        best = None
        for n in FWD:
            b1 = fw[n].reindex(bull).dropna()
            b2 = fw[n].reindex(bear).dropna()
            if len(b1) >= 3 and len(b2) >= 3:
                lead = b1.mean() - b2.mean()
                hit = ((b1 > 0).mean() + (b2 < 0).mean()) / 2 * 100
                rec[f"lead_{n}d"] = round(lead, 2)
                rec[f"hit_{n}d"] = round(hit, 1)
                if best is None or abs(lead) > abs(best[1]):
                    best = (n, lead, hit)
            else:
                rec[f"lead_{n}d"] = ""
                rec[f"hit_{n}d"] = ""
        if best:
            rec["best_n"], rec["best_lead"], rec["best_hit"] = best[0], round(best[1], 2), round(best[2], 1)
            rec["类型"] = "风向标(同向领先)" if best[1] > 0 else "反向指标(逆向领先)"
            rec["强度"] = round(abs(best[1]), 2)
        else:
            rec["best_n"] = rec["best_lead"] = rec["best_hit"] = ""
            rec["类型"], rec["强度"] = "样本不足", 0
        rows.append(rec)
    if not rows:
        return None, f"{market}: stance为空"
    df = pd.DataFrame(rows).sort_values("强度", ascending=False)
    df.to_csv(SD / f"scores_{market}.csv", index=False, encoding="utf-8-sig")
    return df, f"{market}: {len(df)}个账号已评分(基准{bname})"


def main():
    results, notes = {}, []
    for market in CFG["markets"]:
        df, note = score_market(market)
        notes.append(note)
        if df is not None:
            results[market] = df
        print(note)
    if not results:
        return

    L = ["# 三市场哨兵自动发现 · 战绩横向比较", "",
         f"> 评测窗口 {CFG['eval_window']['start']}~{CFG['eval_window']['end']};"
         "lead=看多日后N日均收益 − 看空日后N日均收益(正=风向标,负=反向指标);"
         "样本<3天不下结论。窗口仅2-3个月,这是**筛选器**不是业绩证明。", ""]
    L.append("## 各市场最强哨兵(按|lead|)")
    L.append("")
    L.append("| 市场 | handle | 类型 | 最佳前瞻 | lead(%) | 命中率 | 看多/看空天数 |")
    L.append("|---|---|---|---|---|---|---|")
    summary = []
    for m, df in results.items():
        ok = df[df["类型"] != "样本不足"]
        for _, r in ok.head(3).iterrows():
            L.append(f"| {m} | @{r['handle']} | {r['类型']} | {r['best_n']}日 | "
                     f"{r['best_lead']:+} | {r['best_hit']}% | {r['n_看多']}/{r['n_看空']} |")
        summary.append((m, len(df), len(ok),
                        round(ok["强度"].mean(), 2) if len(ok) else 0,
                        round(ok["强度"].max(), 2) if len(ok) else 0))
    L += ["", "## 哪个市场做得更好", "",
          "| 市场 | 评分账号 | 样本充分 | 平均强度 | 最强 |", "|---|---|---|---|---|"]
    for m, n, nok, avg, mx in summary:
        L.append(f"| {m} | {n} | {nok} | {avg} | {mx} |")
    L += ["", "*强度=|lead|,越大表示该账号的表态与后续行情的关联越强(不论同向或逆向)。*",
          "*『样本充分』= 看多与看空各≥3天,否则不下结论。*"]
    (SD / "sentinel_compare.md").write_text("\n".join(L), encoding="utf-8")
    print("→ sentinel_compare.md")


if __name__ == "__main__":
    main()
