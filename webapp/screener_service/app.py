"""
QuantAlpha Station 3002 — Screener (广度筛选 + Cross Check)
═══════════════════════════════════════════════════════════
Responsibilities:
  • Yahoo Finance full-universe screening (Level 1)
  • Gold Standard cross-validation via Station 3001 (Level 2)
  • Five-factor scoring: Growth, Valuation, Quality, Safety, Momentum
  • Divergence Alert: if |YF - Gold| / Gold > threshold, flag red

Data Flow:
  Station 3001 (Data Center) → raw data → Station 3002 (Screener)
  Station 3002 → Cross Check request → Station 3001 /api/dc/gold-standard/<ticker>
  Station 3002 → scored results → Frontend (3000)
"""

import sys
import os
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data_service'))

from flask import Flask, jsonify, request as flask_request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_start_time = time.time()

DATA_CENTER = 'http://localhost:3001'

# Divergence thresholds per field (CPA-grade: different fields have different materiality)
DIVERGENCE_THRESHOLDS = {
    'price': 0.02,           # 2% — tight for price
    'marketCap': 0.05,       # 5%
    'evEbitda': 0.10,        # 10% — SBC addback differences
    'forwardPE': 0.08,       # 8%
    'revenueGrowth': 0.15,   # 15% — estimate-dependent
    'grossMargin': 0.05,     # 5%
    'roe': 0.10,             # 10%
}


def safe_float(val, default=0.0):
    try:
        if val is None or val != val:
            return default
        return float(val)
    except:
        return default


def _compute_five_factor_scores(stocks):
    """Compute Growth/Valuation/Quality/Safety/Momentum scores (0-100)."""
    results = []
    for s in stocks:
        price = safe_float(s.get('price'))
        if price <= 0:
            continue
        mkt_cap_b = safe_float(s.get('marketCap'))
        if mkt_cap_b < 10:
            continue

        rev_growth  = safe_float(s.get('revenueGrowth'))
        gross_mgn   = safe_float(s.get('grossMargin'))
        op_mgn      = safe_float(s.get('operatingMargin'))
        ebitda_mgn  = safe_float(s.get('ebitdaMargin'))
        roe         = safe_float(s.get('roe'))
        forward_pe  = safe_float(s.get('forwardPE'))
        ev_ebitda   = safe_float(s.get('evEbitda'))
        fcf_yield   = safe_float(s.get('fcfYield'))
        de_ratio    = safe_float(s.get('debtEquity'))
        beta        = safe_float(s.get('beta', 1.0))
        week52_hi   = safe_float(s.get('week52High', 1))
        price52     = price / week52_hi if week52_hi > 0 else 0
        analyst_rt  = safe_float(s.get('analystRating', 3.0))
        price_tgt   = safe_float(s.get('priceTarget', price))
        upside      = ((price_tgt - price) / price * 100) if price > 0 else 0
        eps_growth  = safe_float(s.get('earningsGrowth'))
        short_pct   = safe_float(s.get('shortPct'))
        current_rt  = safe_float(s.get('currentRatio', 1.0))

        if gross_mgn < 0:
            continue

        # 1. Growth Score
        g_score = 50
        if rev_growth > 30: g_score += 40
        elif rev_growth > 15: g_score += 25
        elif rev_growth > 5: g_score += 10
        elif rev_growth < 0: g_score -= 30
        if eps_growth > 20: g_score += 10
        g_score = max(0, min(100, g_score))

        # 2. Valuation Score
        v_score = 50
        if ev_ebitda > 0:
            if ev_ebitda < 10: v_score += 35
            elif ev_ebitda < 18: v_score += 15
            elif ev_ebitda > 40: v_score -= 30
        if forward_pe > 0:
            if forward_pe < 15: v_score += 15
            elif forward_pe > 40: v_score -= 15
        if fcf_yield > 5: v_score += 10
        v_score = max(0, min(100, v_score))

        # 3. Quality Score
        q_score = 50
        if gross_mgn > 60: q_score += 25
        elif gross_mgn > 40: q_score += 15
        elif gross_mgn > 20: q_score += 5
        if roe > 25: q_score += 20
        elif roe > 15: q_score += 10
        if ebitda_mgn > 30: q_score += 10
        elif ebitda_mgn < 5: q_score -= 20
        q_score = max(0, min(100, q_score))

        # 4. Safety Score
        s_score = 70
        if de_ratio > 3: s_score -= 35
        elif de_ratio > 1.5: s_score -= 15
        if beta > 2: s_score -= 20
        elif beta > 1.5: s_score -= 10
        if short_pct > 10: s_score -= 15
        if current_rt > 2: s_score += 10
        elif current_rt < 1: s_score -= 20
        s_score = max(0, min(100, s_score))

        # 5. Momentum Score
        m_score = 50
        if price52 > 0.95: m_score += 30
        elif price52 > 0.85: m_score += 15
        elif price52 < 0.6: m_score -= 20
        if upside > 20: m_score += 15
        elif upside < -10: m_score -= 15
        if analyst_rt <= 1.5: m_score += 10
        elif analyst_rt >= 4: m_score -= 10
        m_score = max(0, min(100, m_score))

        composite = round((g_score + v_score + q_score + s_score + m_score) / 5, 1)

        results.append({
            **{k: s.get(k) for k in ['ticker', 'name', 'sector', 'industry']},
            'price': round(price, 2),
            'changePct': round(safe_float(s.get('changePct')), 2),
            'marketCap': round(mkt_cap_b, 1),
            'forwardPE': round(forward_pe, 1) if forward_pe else None,
            'evEbitda': round(ev_ebitda, 1) if ev_ebitda else None,
            'revenueGrowth': round(rev_growth, 1),
            'grossMargin': round(gross_mgn, 1),
            'roe': round(roe, 1),
            'fcfYield': round(fcf_yield, 2),
            'debtEquity': round(de_ratio, 2),
            'beta': round(beta, 2),
            'growthScore': g_score,
            'valuationScore': v_score,
            'qualityScore': q_score,
            'safetyScore': s_score,
            'momentumScore': m_score,
            'compositeScore': composite,
            'crossCheckStatus': 'pending',
            'divergenceAlerts': [],
        })

    results.sort(key=lambda x: x.get('compositeScore', 0), reverse=True)
    return results


def _cross_check_ticker(stock_data):
    """
    Cross Check: compare screener YF data against 3001 Gold Standard.
    Returns divergence alerts if |YF - Gold| / Gold > threshold.
    """
    ticker = stock_data.get('ticker')
    try:
        resp = requests.get(f'{DATA_CENTER}/api/dc/gold-standard/{ticker}', timeout=3)
        if resp.status_code != 200:
            return stock_data
        gold = resp.json()
        gold_metrics = gold.get('goldMetrics', {})
        if not gold_metrics:
            stock_data['crossCheckStatus'] = 'no_gold_data'
            return stock_data

        alerts = []
        for field, threshold in DIVERGENCE_THRESHOLDS.items():
            yf_val = safe_float(stock_data.get(field))
            gold_val = safe_float(gold_metrics.get(field))
            if gold_val == 0 or yf_val == 0:
                continue
            divergence = abs(yf_val - gold_val) / abs(gold_val)
            if divergence > threshold:
                alerts.append({
                    'field': field,
                    'yfValue': round(yf_val, 4),
                    'goldValue': round(gold_val, 4),
                    'divergencePct': round(divergence * 100, 1),
                    'threshold': f'{threshold * 100}%',
                    'severity': 'HIGH' if divergence > threshold * 2 else 'MEDIUM',
                })

        stock_data['crossCheckStatus'] = 'FAIL' if alerts else 'PASS'
        stock_data['divergenceAlerts'] = alerts
        stock_data['divergenceFlag'] = len(alerts) > 0
        return stock_data
    except:
        stock_data['crossCheckStatus'] = 'data_center_unavailable'
        return stock_data


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3002 — Screener (广度筛选)',
        'port': 3002,
        'version': '1.0.0',
        'architecture': '8-station-microservices',
        'uptime_sec': round(time.time() - _start_time, 1),
        'crossCheck': {
            'enabled': True,
            'dataCenter': DATA_CENTER,
            'thresholds': DIVERGENCE_THRESHOLDS,
        },
        'endpoints': [
            'GET /api/screener/run          — Full five-factor screener',
            'GET /api/screener/crosscheck/<ticker> — Cross Check single ticker',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/screener/run')
def screener_run():
    """
    Full five-factor screener:
    Level 1: YF data from Station 3001 local storage
    Level 2: Cross Check against Gold Standard (top N tickers)
    """
    crosscheck = flask_request.args.get('crosscheck', 'true').lower() == 'true'
    top_n_crosscheck = int(flask_request.args.get('crosscheck_top', '10'))

    # Fetch raw data from Station 3001 local storage
    try:
        resp = requests.get(f'{DATA_CENTER}/api/dc/data', timeout=10)
        if resp.status_code != 200:
            return jsonify({'error': 'Data Center unavailable', 'stocks': []}), 503
        raw = resp.json()
    except Exception as e:
        return jsonify({'error': str(e), 'stocks': [], 'needsRefresh': True}), 503

    stocks = raw.get('stocks', [])
    if not stocks:
        return jsonify({
            'stocks': [], 'count': 0, 'needsRefresh': True,
            'message': 'No local data. Go to Data Center (3001) → Refresh first.',
        })

    # Compute five-factor scores
    scored = _compute_five_factor_scores(stocks)

    # Cross Check top N against Gold Standard
    cross_checked = 0
    if crosscheck and scored:
        for stock in scored[:top_n_crosscheck]:
            stock = _cross_check_ticker(stock)
            cross_checked += 1

    return jsonify({
        'stocks': scored,
        'count': len(scored),
        'universe_size': len(stocks),
        'crossCheckEnabled': crosscheck,
        'crossCheckedCount': cross_checked,
        'divergenceThresholds': DIVERGENCE_THRESHOLDS,
        'rawDataFetchedAt': raw.get('fetchedAt'),
        'scoredAt': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/screener/crosscheck/<ticker>')
def crosscheck_single(ticker):
    """Cross Check a single ticker against Gold Standard."""
    ticker = ticker.upper().strip()
    try:
        resp = requests.get(f'{DATA_CENTER}/api/dc/gold-standard/{ticker}', timeout=5)
        gold = resp.json() if resp.status_code == 200 else {}
    except:
        gold = {}

    try:
        resp2 = requests.get(f'{DATA_CENTER}/api/yf/quote/{ticker}', timeout=8)
        yf_data = resp2.json() if resp2.status_code == 200 else {}
    except:
        yf_data = {}

    gold_metrics = gold.get('goldMetrics', {})
    alerts = []
    for field, threshold in DIVERGENCE_THRESHOLDS.items():
        yf_val = safe_float(yf_data.get(field))
        gold_val = safe_float(gold_metrics.get(field))
        if gold_val == 0 or yf_val == 0:
            continue
        divergence = abs(yf_val - gold_val) / abs(gold_val)
        if divergence > threshold:
            alerts.append({
                'field': field,
                'yfValue': round(yf_val, 4),
                'goldValue': round(gold_val, 4),
                'divergencePct': round(divergence * 100, 1),
                'threshold': f'{threshold * 100}%',
            })

    return jsonify({
        'ticker': ticker,
        'status': 'FAIL' if alerts else 'PASS',
        'divergenceAlerts': alerts,
        'alertCount': len(alerts),
        'yfData': {k: yf_data.get(k) for k in ['price', 'marketCap', 'evEbitda', 'forwardPE', 'revenueGrowth', 'grossMargin', 'roe']},
        'goldData': gold_metrics,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3002 — Screener (广度筛选 + Cross Check)")
    print("  Cross Check: YF ↔ Gold Standard (Data Center 3001)")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3002, debug=False)
