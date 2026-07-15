#!/bin/bash
# C3 个股资金流分层 · 本地一键回填(此脚本为【本地专用】,CI 不跑)
# 用途:国内出口机器上一条命令跑完:抓五档资金流历史 → 算散户/主力背离 → 提交推送。
# 前提:1) 关掉 VPN/加速器(东财是国内站,VPN反而挡它);2) 仓库根目录已有 .venv(否则用系统python3)。
# 用法:  bash sentiment/fundflow/backfill_local.sh
#        BACKFILL_START=20260601 BACKFILL_END=20260714 bash sentiment/fundflow/backfill_local.sh
set -e
cd "$(dirname "$0")/../.."   # → 仓库根目录

# 选 python:优先仓库 .venv,其次系统 python3
if [ -x ./.venv/bin/python ]; then PY=./.venv/bin/python; else PY=python3; fi
echo "[1/4] 用 $PY;确保已装 akshare pandas(若报缺库: $PY -m pip install akshare pandas)"

# 临时绕开代理直连东财(仅本进程;若你是TUN全局VPN,请先在VPN客户端里整个关掉)
export -n HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy 2>/dev/null || true
NOPROXY_ENV="env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy"

echo "[2/4] 抓五档资金流历史..."
$NOPROXY_ENV $PY sentiment/fundflow/fetch_fundflow.py

echo "[3/4] 算散户/主力背离..."
$PY sentiment/fundflow/retail_vs_main.py

echo "[4/4] 提交推送(这步需要能上GitHub,若你为抓东财关了VPN,请先开回VPN再执行本步)..."
git add sentiment/fundflow/ sentiment/_status.txt
git commit -m "fundflow: 本地回填个股资金流分层+背离 $(date +%Y%m%d-%H%M)" || echo "无改动"
git pull --rebase -X ours origin main 2>/dev/null || true
git push && echo "✅ 完成并已推送" || echo "⚠️ 推送失败:请确认VPN已开回、能上GitHub,再手动 git push"
