"""
dashboard.py - FX Dashboard (Phase A)
Generates a standalone HTML dashboard from MT5 trade history and opens it in the browser.

Usage: python dashboard.py --broker axiory --days 7
Output: logs/dashboard_YYYYMMDD.html
"""

import sys
import os
import json
import argparse
import webbrowser
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
except ImportError:
    print('[ERROR] MetaTrader5 not found: pip install MetaTrader5')
    sys.exit(1)

from broker_utils import connect_mt5, disconnect_mt5
from daily_report import fetch_open_positions, MAGIC_MAP

BASE_DIR = r'C:\Users\Administrator\fx_bot'
LOG_DIR = os.path.join(BASE_DIR, 'logs')
JST = timezone(timedelta(hours=9))

STRATEGY_COLORS = {
    'BB':         '#4e8ef7',
    'SMC_GBPAUD': '#4ecb71',
    'stat_arb':   '#f7a84e',
    'SMA_SQ':     '#c471f7',
}

# Phase1判定基準
PHASE1_PF_MIN  = 1.2
PHASE1_WR_MIN  = 50.0
PHASE1_DD_MAX  = 15.0   # % (peak比)
PHASE1_N_TARGET = 100   # 目標サンプル数
PHASE1_BB_PAIRS = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']


def fetch_deals_range(from_dt: datetime, to_dt: datetime) -> list:
    """history_deals_get でクローズ済み deal を取得し close_date を含めて返す。"""
    deals = mt5.history_deals_get(from_dt, to_dt)
    if deals is None:
        print('[WARN] history_deals_get failed: {}'.format(mt5.last_error()))
        return []

    entry_map = {}
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_IN:
            entry_map[d.position_id] = d

    rows = []
    for d in deals:
        if d.entry != mt5.DEAL_ENTRY_OUT:
            continue
        if d.magic not in MAGIC_MAP:
            continue

        entry_d = entry_map.get(d.position_id)
        open_price = entry_d.price if entry_d else d.price
        is_buy = (entry_d.type == mt5.DEAL_TYPE_BUY) if entry_d else (d.type == mt5.DEAL_TYPE_SELL)
        close_dt_jst = datetime.fromtimestamp(d.time, tz=JST)
        open_dt_jst  = datetime.fromtimestamp(entry_d.time, tz=JST) if entry_d else close_dt_jst

        rows.append({
            'ticket':      d.position_id,
            'symbol':      d.symbol,
            'type':        'BUY' if is_buy else 'SELL',
            'lots':        round(float(d.volume), 2),
            'open_price':  float(open_price),
            'close_price': float(d.price),
            'profit':      round(float(d.profit), 2),
            'magic':       d.magic,
            'strategy':    MAGIC_MAP[d.magic],
            'open_date':   open_dt_jst.strftime('%Y-%m-%d'),
            'open_time':   open_dt_jst.strftime('%H:%M'),
            'close_date':  close_dt_jst.strftime('%Y-%m-%d'),
            'close_time':  close_dt_jst.strftime('%H:%M'),
        })
    return rows


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FX Dashboard - __BROKER__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d1117; color: #c9d1d9;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 14px; padding: 16px; max-width: 1200px; margin: 0 auto;
}
h2 {
  color: #79c0ff; font-size: 13px; margin: 20px 0 10px;
  padding-bottom: 6px; border-bottom: 1px solid #21262d;
  text-transform: uppercase; letter-spacing: 1px;
}
.header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
}
.header h1 { color: #58a6ff; font-size: 22px; }
.header-meta { color: #8b949e; font-size: 12px; margin-top: 4px; }
.period-btns button {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  padding: 6px 16px; cursor: pointer; border-radius: 4px; margin-left: 6px;
  font-size: 13px; font-family: inherit;
}
.period-btns button.active { background: #1f6feb; border-color: #388bfd; color: #fff; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
@media (max-width: 700px) { .cards { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 400px) { .cards { grid-template-columns: 1fr; } }
.card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }
.card-label { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
.card-value { font-size: 28px; font-weight: bold; margin-top: 8px; }
.card-sub { color: #8b949e; font-size: 12px; margin-top: 6px; }
.pos { color: #3fb950; } .neg { color: #f85149; } .neu { color: #c9d1d9; }
.warn { color: #e3b341; }
.section { margin-bottom: 24px; }
.chart-wrap {
  background: #161b22; border: 1px solid #21262d; border-radius: 8px;
  padding: 16px; position: relative; height: 280px;
}
.table-wrap { background: #161b22; border: 1px solid #21262d; border-radius: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  background: #161b22; color: #8b949e; padding: 8px 12px;
  text-align: right; font-weight: normal; font-size: 11px;
  text-transform: uppercase; border-bottom: 1px solid #21262d;
}
th:first-child, th:nth-child(2) { text-align: left; }
td { padding: 8px 12px; border-bottom: 1px solid #0d1117; text-align: right; }
td:first-child, td:nth-child(2) { text-align: left; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #1c2128; }
.op-list { display: flex; flex-direction: column; gap: 8px; }
.op-card {
  background: #161b22; border: 1px solid #21262d; border-radius: 6px;
  padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
}
.op-symbol { font-weight: bold; color: #79c0ff; min-width: 80px; font-size: 15px; }
.op-tag { background: #21262d; padding: 3px 8px; border-radius: 4px; font-size: 12px; color: #8b949e; }
.op-strat { border-left-width: 3px; border-left-style: solid; padding-left: 8px; }
.op-pnl { font-size: 20px; font-weight: bold; margin-left: auto; }
.empty-msg {
  color: #8b949e; padding: 20px; text-align: center;
  background: #161b22; border: 1px solid #21262d; border-radius: 8px;
}
/* Phase1 panel */
.p1-table th:first-child { min-width: 80px; }
.progress-bar-bg {
  background: #21262d; border-radius: 4px; height: 6px; margin-top: 4px; width: 100%; min-width: 60px;
}
.progress-bar-fill { background: #4e8ef7; border-radius: 4px; height: 6px; }
.badge {
  display: inline-block; padding: 2px 7px; border-radius: 4px;
  font-size: 11px; font-weight: bold;
}
.badge-pass { background: #1a3a24; color: #3fb950; }
.badge-fail { background: #3a1a1a; color: #f85149; }
.badge-na   { background: #21262d; color: #8b949e; }
.nav-btn {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  padding: 2px 10px; cursor: pointer; border-radius: 4px;
  font-size: 16px; font-family: inherit; line-height: 1.4;
}
.nav-btn:hover { background: #2d333b; }
.nav-btn:disabled { opacity: 0.3; cursor: default; }
.hist-wrap {
  background: #161b22; border: 1px solid #21262d; border-radius: 8px;
  overflow-x: auto; -webkit-overflow-scrolling: touch;
}
.hist-table { font-size: 12px; white-space: nowrap; }
.hist-table th { font-size: 10px; }
.hist-table td { padding: 6px 10px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>FX Dashboard</h1>
    <div class="header-meta">__BROKER__ &nbsp;|&nbsp; Generated: __GENERATED_AT__ &nbsp;|&nbsp; Max: __DAYS__d</div>
  </div>
  <div class="period-btns">
    <button id="btn7"  onclick="setPeriod(7)">7日</button>
    <button id="btn30" onclick="setPeriod(30)">30日</button>
    <button id="btn90" onclick="setPeriod(90)">90日</button>
  </div>
</div>

<div class="section">
  <h2>オープンポジション</h2>
  <div id="open-section"></div>
</div>

<div class="cards">
  <div class="card">
    <div class="card-label">総損益</div>
    <div class="card-value" id="card-total">-</div>
    <div class="card-sub"  id="card-total-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">Profit Factor</div>
    <div class="card-value" id="card-pf">-</div>
    <div class="card-sub"  id="card-pf-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">勝率</div>
    <div class="card-value" id="card-wr">-</div>
    <div class="card-sub"  id="card-wr-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">Max Drawdown</div>
    <div class="card-value" id="card-dd">-</div>
    <div class="card-sub"  id="card-dd-sub"></div>
  </div>
</div>

<div class="section">
  <h2>Phase1 判定 (BB戦略・全期間)</h2>
  <div id="phase1-section"></div>
</div>

<div class="section">
  <h2>累積損益推移</h2>
  <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
</div>

<div class="section">
  <h2>ペア別パフォーマンス</h2>
  <div class="chart-wrap"><canvas id="pairChart"></canvas></div>
</div>

<div class="section">
  <h2>戦略別サマリー (期間フィルタ連動)</h2>
  <div id="strategy-section"></div>
</div>

<div class="section">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <h2 style="margin:0;border:none;padding:0">日別サマリー</h2>
    <button class="nav-btn" onclick="navDay(-1)">&#8249;</button>
    <span id="daily-date-label" style="color:#79c0ff;font-weight:bold;font-size:14px;min-width:110px;text-align:center">-</span>
    <button class="nav-btn" onclick="navDay(1)">&#8250;</button>
  </div>
  <div id="yesterday-section"></div>
</div>

<div class="section">
  <h2>全取引履歴</h2>
  <div id="all-trades-section"></div>
</div>

<script>
Chart.register(ChartDataLabels);

var ALL_TRADES      = __TRADES_JSON__;
var OPEN_POSITIONS  = __POSITIONS_JSON__;
var STRATEGY_COLORS = __STRATEGY_COLORS_JSON__;
var MAX_DAYS        = __DAYS__;
var YESTERDAY       = '__YESTERDAY__';
var PHASE1_PF_MIN   = __PHASE1_PF_MIN__;
var PHASE1_WR_MIN   = __PHASE1_WR_MIN__;
var PHASE1_DD_MAX   = __PHASE1_DD_MAX__;
var PHASE1_N_TARGET = __PHASE1_N_TARGET__;
var PHASE1_BB_PAIRS = __PHASE1_BB_PAIRS_JSON__;

var equityChart = null;
var pairChart   = null;

function getDateCutoff(days) {
  var d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function filterTrades(days) {
  var cutoff = getDateCutoff(days);
  return ALL_TRADES.filter(function(t) { return t.close_date >= cutoff; });
}

function fmt2(n) { return (n >= 0 ? '+' : '') + n.toFixed(2); }
function colorClass(n) { return n > 0 ? 'pos' : (n < 0 ? 'neg' : 'neu'); }

/* ── DD計算 ────────────────────────────────────────────────── */
function calcMaxDD(trades) {
  if (trades.length === 0) return { maxDD: 0, maxDDPct: 0 };
  var sorted = trades.slice().sort(function(a, b) {
    var ka = a.close_date + a.close_time, kb = b.close_date + b.close_time;
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });
  var cum = 0, peak = 0, maxDD = 0;
  sorted.forEach(function(t) {
    cum += t.profit;
    if (cum > peak) peak = cum;
    var dd = peak - cum;
    if (dd > maxDD) maxDD = dd;
  });
  var maxDDPct = peak > 0 ? maxDD / peak * 100 : 0;
  return { maxDD: +maxDD.toFixed(2), maxDDPct: +maxDDPct.toFixed(1) };
}

/* ── サマリーカード ─────────────────────────────────────────── */
function updateSummaryCards(trades) {
  var profits = trades.map(function(t) { return t.profit; });
  var wins    = profits.filter(function(p) { return p > 0; });
  var losses  = profits.filter(function(p) { return p < 0; });
  var total   = profits.reduce(function(a, b) { return a + b; }, 0);
  var gp      = wins.reduce(function(a, b) { return a + b; }, 0);
  var gl      = Math.abs(losses.reduce(function(a, b) { return a + b; }, 0));
  var pf      = gl > 0 ? gp / gl : 0;
  var wr      = trades.length > 0 ? wins.length / trades.length * 100 : 0;

  var totalEl = document.getElementById('card-total');
  totalEl.textContent = fmt2(total);
  totalEl.className   = 'card-value ' + colorClass(total);
  document.getElementById('card-total-sub').textContent = trades.length + '件 (JPY/USD混在)';

  var pfEl = document.getElementById('card-pf');
  pfEl.textContent = gl > 0 ? pf.toFixed(3) : 'N/A';
  pfEl.className   = 'card-value ' + (pf >= 1.2 ? 'pos' : (gl > 0 ? 'neg' : 'neu'));
  document.getElementById('card-pf-sub').textContent =
    '総利益: ' + gp.toFixed(2) + ' / 総損失: ' + gl.toFixed(2);

  var wrEl = document.getElementById('card-wr');
  wrEl.textContent = wr.toFixed(1) + '%';
  wrEl.className   = 'card-value ' + (wr >= 50 ? 'pos' : 'neg');
  document.getElementById('card-wr-sub').textContent =
    '勝: ' + wins.length + ' / 負: ' + losses.length + ' / 計: ' + trades.length;

  /* DD card */
  var dd = calcMaxDD(trades);
  var ddEl = document.getElementById('card-dd');
  ddEl.textContent = dd.maxDDPct.toFixed(1) + '%';
  ddEl.className = 'card-value ' +
    (dd.maxDDPct < 5 ? 'pos' : dd.maxDDPct < PHASE1_DD_MAX ? 'warn' : 'neg');
  document.getElementById('card-dd-sub').textContent =
    '絶対値: ' + dd.maxDD.toFixed(2) + ' (peak比)';
}

/* ── Phase1判定パネル ──────────────────────────────────────── */
function buildPhase1Panel() {
  var bbTrades = ALL_TRADES.filter(function(t) { return t.strategy === 'BB'; });
  var el = document.getElementById('phase1-section');

  if (bbTrades.length === 0) {
    el.innerHTML = '<div class="empty-msg">BB取引データなし</div>';
    return;
  }

  /* ペア別集計 */
  function pairStats(sym) {
    var pts = bbTrades.filter(function(t) { return t.symbol === sym; });
    if (pts.length === 0) return null;
    var wins = pts.filter(function(t) { return t.profit > 0; });
    var gp   = wins.reduce(function(a, t) { return a + t.profit; }, 0);
    var gl   = Math.abs(pts.filter(function(t) { return t.profit < 0; })
                          .reduce(function(a, t) { return a + t.profit; }, 0));
    var pf   = gl > 0 ? gp / gl : null;
    var wr   = wins.length / pts.length * 100;
    var dd   = calcMaxDD(pts);
    return { n: pts.length, pf: pf, wr: wr, dd: dd };
  }

  function badge(pass, naCondition) {
    if (naCondition) return '<span class="badge badge-na">N/A</span>';
    return pass
      ? '<span class="badge badge-pass">PASS</span>'
      : '<span class="badge badge-fail">FAIL</span>';
  }

  function progressBar(n) {
    var pct = Math.min(n / PHASE1_N_TARGET * 100, 100);
    return '<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:' +
      pct.toFixed(0) + '%"></div></div>';
  }

  var html = '<div class="table-wrap"><table class="p1-table"><thead><tr>' +
    '<th>ペア</th><th>n (目標' + PHASE1_N_TARGET + ')</th>' +
    '<th>PF ≥' + PHASE1_PF_MIN + '</th>' +
    '<th>WR ≥' + PHASE1_WR_MIN + '%</th>' +
    '<th>DD &lt;' + PHASE1_DD_MAX + '%</th>' +
    '<th>総合</th>' +
    '</tr></thead><tbody>';

  PHASE1_BB_PAIRS.forEach(function(sym) {
    var s = pairStats(sym);
    if (!s) {
      html += '<tr><td>' + sym + '</td><td colspan="5" style="color:#8b949e">データなし</td></tr>';
      return;
    }
    var pfPass = s.pf !== null && s.pf >= PHASE1_PF_MIN;
    var wrPass = s.wr >= PHASE1_WR_MIN;
    var ddPass = s.dd.maxDDPct < PHASE1_DD_MAX;
    var ddNA   = s.dd.maxDD === 0;
    var allPass = pfPass && wrPass && (ddNA || ddPass);

    html += '<tr>' +
      '<td style="font-weight:bold;color:#79c0ff">' + sym + '</td>' +
      '<td>' + s.n + progressBar(s.n) + '</td>' +
      '<td>' + (s.pf !== null ? s.pf.toFixed(3) : 'N/A') + '<br>' + badge(pfPass, s.pf === null) + '</td>' +
      '<td>' + s.wr.toFixed(1) + '%<br>' + badge(wrPass, false) + '</td>' +
      '<td>' + (ddNA ? 'N/A' : s.dd.maxDDPct.toFixed(1) + '%') + '<br>' + badge(ddPass, ddNA) + '</td>' +
      '<td>' + (allPass
        ? '<span class="badge badge-pass" style="font-size:13px">&#10003; 合格</span>'
        : '<span class="badge badge-fail" style="font-size:13px">&#10007; 不合格</span>') +
      '</td>' +
      '</tr>';
  });

  html += '</tbody></table></div>';
  el.innerHTML = html;
}

/* ── 戦略別サマリー ─────────────────────────────────────────── */
function buildStrategyTable(trades) {
  var el = document.getElementById('strategy-section');
  if (trades.length === 0) {
    el.innerHTML = '<div class="empty-msg">対象期間の取引なし</div>';
    return;
  }

  var groups = {};
  trades.forEach(function(t) {
    if (!groups[t.strategy]) groups[t.strategy] = [];
    groups[t.strategy].push(t);
  });

  var html = '<div class="table-wrap"><table><thead><tr>' +
    '<th>戦略</th><th>件数</th><th>勝率</th><th>PF</th><th>合計損益</th>' +
    '</tr></thead><tbody>';

  Object.keys(groups).sort().forEach(function(strat) {
    var pts  = groups[strat];
    var wins = pts.filter(function(t) { return t.profit > 0; });
    var gp   = wins.reduce(function(a, t) { return a + t.profit; }, 0);
    var gl   = Math.abs(pts.filter(function(t) { return t.profit < 0; })
                           .reduce(function(a, t) { return a + t.profit; }, 0));
    var pf   = gl > 0 ? (gp / gl).toFixed(3) : 'N/A';
    var wr   = (wins.length / pts.length * 100).toFixed(1) + '%';
    var total = +pts.reduce(function(a, t) { return a + t.profit; }, 0).toFixed(2);
    var color = STRATEGY_COLORS[strat] || '#aaaaaa';
    var cls   = colorClass(total);

    html += '<tr>' +
      '<td style="border-left:3px solid ' + color + ';padding-left:9px">' + strat + '</td>' +
      '<td>' + pts.length + '</td>' +
      '<td>' + wr + '</td>' +
      '<td>' + pf + '</td>' +
      '<td class="' + cls + '">' + fmt2(total) + '</td>' +
      '</tr>';
  });

  html += '</tbody></table></div>';
  el.innerHTML = html;
}

/* ── Equity chart ──────────────────────────────────────────── */
function buildEquityData(trades) {
  var sorted = trades.slice().sort(function(a, b) {
    var ka = a.close_date + a.close_time, kb = b.close_date + b.close_time;
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });
  var dateSet = {}, stratSet = {};
  sorted.forEach(function(t) { dateSet[t.close_date] = true; stratSet[t.strategy] = true; });
  var dates      = Object.keys(dateSet).sort();
  var strategies = Object.keys(stratSet).sort();
  var datasets   = [];

  var cumTotal = 0;
  datasets.push({
    label: 'Total',
    data: dates.map(function(date) {
      var pnl = sorted.filter(function(t) { return t.close_date === date; })
                      .reduce(function(a, t) { return a + t.profit; }, 0);
      cumTotal += pnl;
      return +cumTotal.toFixed(2);
    }),
    borderColor: '#ffffff', backgroundColor: 'transparent',
    borderWidth: 2, tension: 0.2,
    pointRadius: dates.length <= 14 ? 3 : 0,
    datalabels: { display: false },
  });

  strategies.forEach(function(strat) {
    var cum = 0;
    datasets.push({
      label: strat,
      data: dates.map(function(date) {
        var pnl = sorted.filter(function(t) { return t.strategy === strat && t.close_date === date; })
                        .reduce(function(a, t) { return a + t.profit; }, 0);
        cum += pnl;
        return +cum.toFixed(2);
      }),
      borderColor: STRATEGY_COLORS[strat] || '#aaaaaa',
      backgroundColor: 'transparent',
      borderWidth: 1.5, tension: 0.2, pointRadius: 0,
      datalabels: { display: false },
    });
  });

  return { labels: dates, datasets: datasets };
}

function buildPairData(trades) {
  var pairMap = {};
  trades.forEach(function(t) {
    if (!pairMap[t.symbol]) pairMap[t.symbol] = [];
    pairMap[t.symbol].push(t);
  });
  var symbols = Object.keys(pairMap).sort();
  var profits = symbols.map(function(sym) {
    return +pairMap[sym].reduce(function(a, t) { return a + t.profit; }, 0).toFixed(2);
  });
  var pfs = symbols.map(function(sym) {
    var pts = pairMap[sym];
    var gp  = pts.filter(function(t) { return t.profit > 0; }).reduce(function(a, t) { return a + t.profit; }, 0);
    var gl  = Math.abs(pts.filter(function(t) { return t.profit < 0; }).reduce(function(a, t) { return a + t.profit; }, 0));
    return gl > 0 ? +(gp / gl).toFixed(2) : null;
  });
  return { symbols: symbols, profits: profits, pfs: pfs };
}

function updateEquityChart(trades) {
  var data = buildEquityData(trades);
  if (equityChart) {
    equityChart.data = data; equityChart.update();
  } else {
    equityChart = new Chart(document.getElementById('equityChart'), {
      type: 'line', data: data,
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#8b949e', font: { size: 11 } } },
          datalabels: { display: false },
        },
        scales: {
          x: { ticks: { color: '#8b949e', maxTicksLimit: 12 }, grid: { color: '#21262d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
        },
      },
    });
  }
}

function updatePairChart(trades) {
  var pd      = buildPairData(trades);
  var pfsRef  = pd.pfs;
  var colors  = pd.profits.map(function(p) { return p >= 0 ? '#3fb950' : '#f85149'; });
  var chartData = {
    labels: pd.symbols,
    datasets: [{
      label: '合計損益', data: pd.profits, backgroundColor: colors,
      datalabels: {
        anchor: 'end', align: 'end',
        formatter: function(value, ctx) {
          var pf = pfsRef[ctx.dataIndex];
          return pf !== null ? 'PF:' + pf : '';
        },
        color: '#c9d1d9', font: { size: 10 },
      },
    }],
  };
  if (pairChart) {
    pairChart.data = chartData; pairChart.update();
  } else {
    pairChart = new Chart(document.getElementById('pairChart'), {
      type: 'bar', data: chartData,
      options: {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          datalabels: {},
          tooltip: {
            callbacks: {
              afterLabel: function(ctx) {
                var pf = pfsRef[ctx.dataIndex];
                return 'PF: ' + (pf !== null ? pf : 'N/A');
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
        },
      },
    });
  }
}

/* ── 日別サマリー（ナビゲーション付き） ──────────────────────── */
var DAY_DATES  = [];  // 取引のある日付リスト（降順）
var DAY_INDEX  = 0;   // 現在表示中のインデックス

function initDailyNav() {
  var today = new Date().toISOString().slice(0, 10);
  var dateSet = { [today]: true };
  ALL_TRADES.forEach(function(t) { dateSet[t.close_date] = true; });
  DAY_DATES = Object.keys(dateSet).sort().reverse();
  DAY_INDEX = 0;
  renderDailyTable();
}

function navDay(delta) {
  var next = DAY_INDEX + delta;
  if (next < 0 || next >= DAY_DATES.length) return;
  DAY_INDEX = next;
  renderDailyTable();
}

function renderDailyTable() {
  var date = DAY_DATES[DAY_INDEX];
  document.getElementById('daily-date-label').textContent = date;

  var prevBtn = document.querySelector('.nav-btn:first-of-type');
  var nextBtn = document.querySelector('.nav-btn:last-of-type');
  if (prevBtn) prevBtn.disabled = (DAY_INDEX >= DAY_DATES.length - 1);
  if (nextBtn) nextBtn.disabled = (DAY_INDEX <= 0);

  var trades = ALL_TRADES.filter(function(t) { return t.close_date === date; });
  var el = document.getElementById('yesterday-section');
  if (trades.length === 0) {
    el.innerHTML = '<div class="empty-msg">取引なし (' + date + ')</div>';
    return;
  }
  var groups = {};
  trades.forEach(function(t) {
    if (!groups[t.strategy]) groups[t.strategy] = {};
    if (!groups[t.strategy][t.symbol]) groups[t.strategy][t.symbol] = [];
    groups[t.strategy][t.symbol].push(t);
  });
  var html = '<div class="table-wrap"><table><thead><tr>' +
    '<th>戦略</th><th>ペア</th><th>件数</th><th>勝率</th><th>PF</th><th>合計損益</th>' +
    '</tr></thead><tbody>';
  Object.keys(groups).sort().forEach(function(strat) {
    Object.keys(groups[strat]).sort().forEach(function(sym) {
      var pts   = groups[strat][sym];
      var wins  = pts.filter(function(t) { return t.profit > 0; }).length;
      var total = +pts.reduce(function(a, t) { return a + t.profit; }, 0).toFixed(2);
      var gp    = pts.filter(function(t) { return t.profit > 0; }).reduce(function(a, t) { return a + t.profit; }, 0);
      var gl    = Math.abs(pts.filter(function(t) { return t.profit < 0; }).reduce(function(a, t) { return a + t.profit; }, 0));
      var pf    = gl > 0 ? (gp / gl).toFixed(3) : 'N/A';
      var wr    = (wins / pts.length * 100).toFixed(0) + '%';
      var cls   = colorClass(total);
      html += '<tr><td>' + strat + '</td><td>' + sym + '</td><td>' + pts.length +
        '</td><td>' + wr + '</td><td>' + pf + '</td>' +
        '<td class="' + cls + '">' + fmt2(total) + '</td></tr>';
    });
  });
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

/* ── 全取引履歴 ─────────────────────────────────────────────── */
function buildAllTradesSection() {
  var el = document.getElementById('all-trades-section');
  if (ALL_TRADES.length === 0) {
    el.innerHTML = '<div class="empty-msg">取引データなし</div>';
    return;
  }
  var sorted = ALL_TRADES.slice().sort(function(a, b) {
    var ka = a.close_date + a.close_time, kb = b.close_date + b.close_time;
    return ka > kb ? -1 : ka < kb ? 1 : 0;
  });
  var html = '<div class="hist-wrap"><table class="hist-table"><thead><tr>' +
    '<th style="text-align:left">決済日時</th>' +
    '<th style="text-align:left">エントリー日時</th>' +
    '<th style="text-align:left">ペア</th>' +
    '<th style="text-align:left">方向</th>' +
    '<th>ロット</th>' +
    '<th>エントリー</th>' +
    '<th>決済</th>' +
    '<th>損益</th>' +
    '<th style="text-align:left">戦略</th>' +
    '</tr></thead><tbody>';
  sorted.forEach(function(t) {
    var cls   = colorClass(t.profit);
    var color = STRATEGY_COLORS[t.strategy] || '#aaaaaa';
    var typeColor = t.type === 'BUY' ? '#3fb950' : '#f85149';
    html += '<tr>' +
      '<td style="text-align:left;white-space:nowrap">' + t.close_date + ' ' + t.close_time + '</td>' +
      '<td style="text-align:left;white-space:nowrap;color:#8b949e">' + (t.open_date || '-') + ' ' + (t.open_time || '') + '</td>' +
      '<td style="text-align:left;font-weight:bold;color:#79c0ff">' + t.symbol + '</td>' +
      '<td style="text-align:left;color:' + typeColor + '">' + t.type + '</td>' +
      '<td>' + t.lots.toFixed(2) + '</td>' +
      '<td>' + t.open_price.toFixed(5) + '</td>' +
      '<td>' + t.close_price.toFixed(5) + '</td>' +
      '<td class="' + cls + '">' + fmt2(t.profit) + '</td>' +
      '<td style="text-align:left;border-left:3px solid ' + color + ';padding-left:7px">' + t.strategy + '</td>' +
      '</tr>';
  });
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

/* ── オープンポジション ──────────────────────────────────────── */
function buildOpenPositions() {
  var el = document.getElementById('open-section');
  if (OPEN_POSITIONS.length === 0) {
    el.innerHTML = '<div class="empty-msg">オープンポジションなし</div>';
    return;
  }
  var html = '<div class="op-list">';
  OPEN_POSITIONS.forEach(function(p) {
    var color = STRATEGY_COLORS[p.strategy] || '#aaaaaa';
    var cls   = colorClass(p.profit);
    html += '<div class="op-card">' +
      '<span class="op-symbol">' + p.symbol + '</span>' +
      '<span class="op-tag op-strat" style="border-color:' + color + '">' + p.strategy + '</span>' +
      '<span class="op-tag">' + p.type + '</span>' +
      '<span class="op-tag">lots=' + p.lots.toFixed(2) + '</span>' +
      '<span class="op-tag">open=' + p.open.toFixed(5) + '</span>' +
      '<span class="op-tag">now=' + p.current.toFixed(5) + '</span>' +
      '<span class="op-pnl ' + cls + '">' + fmt2(p.profit) + '</span>' +
      '</div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

/* ── 期間切替 ───────────────────────────────────────────────── */
function setPeriod(days) {
  var effective = Math.min(days, MAX_DAYS);
  [7, 30, 90].forEach(function(d) {
    var btn = document.getElementById('btn' + d);
    if (btn) btn.className = (d === days) ? 'active' : '';
  });
  var trades = filterTrades(effective);
  updateSummaryCards(trades);
  updateEquityChart(trades);
  updatePairChart(trades);
  buildStrategyTable(trades);
}

/* ── 初期描画 ──────────────────────────────────────────────── */
setPeriod(Math.min(MAX_DAYS, 7));
buildPhase1Panel();
initDailyNav();
buildAllTradesSection();
buildOpenPositions();
</script>
</body>
</html>'''


def generate_html(trades: list, open_positions: list, broker: str,
                  days: int, generated_at: str, yesterday: str) -> str:
    html = HTML_TEMPLATE
    html = html.replace('__TRADES_JSON__',         json.dumps(trades,         ensure_ascii=False))
    html = html.replace('__POSITIONS_JSON__',       json.dumps(open_positions, ensure_ascii=False))
    html = html.replace('__STRATEGY_COLORS_JSON__', json.dumps(STRATEGY_COLORS))
    html = html.replace('__BROKER__',               broker)
    html = html.replace('__GENERATED_AT__',         generated_at)
    html = html.replace('__DAYS__',                 str(days))
    html = html.replace('__YESTERDAY__',            yesterday)
    html = html.replace('__PHASE1_PF_MIN__',        str(PHASE1_PF_MIN))
    html = html.replace('__PHASE1_WR_MIN__',        str(PHASE1_WR_MIN))
    html = html.replace('__PHASE1_DD_MAX__',        str(PHASE1_DD_MAX))
    html = html.replace('__PHASE1_N_TARGET__',      str(PHASE1_N_TARGET))
    html = html.replace('__PHASE1_BB_PAIRS_JSON__', json.dumps(PHASE1_BB_PAIRS))
    return html


def main():
    parser = argparse.ArgumentParser(description='FX Dashboard generator (Phase A)')
    parser.add_argument('--broker', default='axiory',
                        choices=['axiory', 'oanda', 'exness'],
                        help='Broker key (default: axiory)')
    parser.add_argument('--days', type=int, default=7,
                        choices=[7, 30, 90],
                        help='History period in days (default: 7)')
    args = parser.parse_args()

    print('[INFO] Connecting to MT5: broker={}'.format(args.broker))
    if not connect_mt5(args.broker):
        print('[ERROR] MT5 connection failed')
        sys.exit(1)

    try:
        now_jst  = datetime.now(tz=JST)
        to_utc   = now_jst.astimezone(timezone.utc)
        from_utc = (now_jst - timedelta(days=args.days)).astimezone(timezone.utc)
        yesterday = (now_jst - timedelta(days=1)).strftime('%Y-%m-%d')

        print('[INFO] Fetching {} days of history ({} -> {})'.format(
            args.days, from_utc.date(), to_utc.date()))

        trades         = fetch_deals_range(from_utc, to_utc)
        open_positions = fetch_open_positions()

        print('[INFO] Closed trades: {}  Open positions: {}'.format(
            len(trades), len(open_positions)))

        generated_at = now_jst.strftime('%Y-%m-%d %H:%M:%S') + ' JST'
        html = generate_html(trades, open_positions, args.broker,
                             args.days, generated_at, yesterday)

        os.makedirs(LOG_DIR, exist_ok=True)
        fname = 'dashboard_{}.html'.format(now_jst.strftime('%Y%m%d'))
        fpath = os.path.join(LOG_DIR, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(html)
        print('[INFO] Dashboard saved: {}'.format(fpath))

        webbrowser.open('file:///' + fpath.replace('\\', '/'))
        print('[INFO] Opened in browser.')

    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
