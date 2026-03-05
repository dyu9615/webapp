"""
QuantAlpha Station 3004 — Deep Analysis (个股深钻)
═══════════════════════════════════════════════════
Three-layer architecture:
  Layer 1: Real-time data (via Station 3001) + News sentiment (via Station 3003)
  Layer 2: Non-GAAP adjustments, Adj.EBITDA, FCF, Net Leverage — Cross Check with Gold Standard
  Layer 3: AI summary + external chat integration

Cross Check Logic:
  divergence = (YF_Data - Gold_Data) / Gold_Data
  If audit passes → Layer 3 generates HIGHER confidence recommendation

Data Flow:
  Station 3001 → raw data → Station 3004
  Station 3003 → sentiment → Station 3004
  Station 3004 → deep analysis → Station 3005 (ML Engine)
"""

import sys
import os
import time
import requests
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data_service'))

from flask import Flask, jsonify, request as flask_request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_start_time = time.time()

DATA_CENTER = 'http://localhost:3001'
NEWS_STATION = 'http://localhost:3003'


def safe_float(val, default=0.0):
    try:
        if val is None or val != val:
            return default
        return float(val)
    except:
        return default


def safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except:
        return default


def _cross_check_with_gold(ticker, yf_metrics):
    """Cross Check YF deep analysis data against Gold Standard from 3001."""
    try:
        resp = requests.get(f'{DATA_CENTER}/api/dc/gold-standard/{ticker}', timeout=3)
        if resp.status_code != 200:
            return {'status': 'data_center_unavailable', 'alerts': []}
        gold = resp.json().get('goldMetrics', {})
        if not gold:
            return {'status': 'no_gold_data', 'alerts': []}

        alerts = []
        checks = [
            ('evEbitda', 0.10, 'EV/EBITDA'),
            ('grossMargin', 0.05, 'Gross Margin'),
            ('revenueGrowth', 0.15, 'Revenue Growth'),
            ('roe', 0.10, 'ROE'),
            ('forwardPE', 0.08, 'Forward P/E'),
        ]
        for field, threshold, label in checks:
            yf_val = safe_float(yf_metrics.get(field))
            gold_val = safe_float(gold.get(field))
            if gold_val == 0 or yf_val == 0:
                continue
            div = abs(yf_val - gold_val) / abs(gold_val)
            if div > threshold:
                alerts.append({
                    'field': label, 'yfValue': round(yf_val, 2),
                    'goldValue': round(gold_val, 2),
                    'divergencePct': round(div * 100, 1),
                })

        return {
            'status': 'PASS' if not alerts else 'FAIL',
            'alerts': alerts,
            'auditPassed': len(alerts) == 0,
        }
    except:
        return {'status': 'error', 'alerts': []}


def _get_sentiment(ticker):
    """Get sentiment score from Station 3003."""
    try:
        resp = requests.get(f'{NEWS_STATION}/api/news/sentiment/{ticker}', timeout=5)
        return resp.json() if resp.status_code == 200 else {'sentimentScore': 0, 'sentimentLabel': 'neutral'}
    except:
        return {'sentimentScore': 0, 'sentimentLabel': 'neutral'}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3004 — Deep Analysis (个股深钻)',
        'port': 3004,
        'version': '1.0.0',
        'architecture': '8-station-microservices',
        'uptime_sec': round(time.time() - _start_time, 1),
        'dependencies': {
            'dataCenter': DATA_CENTER,
            'newsStation': NEWS_STATION,
        },
        'endpoints': [
            'GET /api/deep/<ticker>         — Full 3-layer deep analysis',
            'GET /api/deep/quick/<ticker>   — Quick Layer 1+2 only',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


def _get_factset_snapshot(ticker):
    """Get FactSet Excel snapshot from Station 3001 for fallback data."""
    try:
        resp = requests.get(f'{DATA_CENTER}/api/dc/factset-snapshot/{ticker}', timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success') and data.get('snapshot'):
                return data['snapshot']
    except:
        pass
    return None


def _get_bloomberg_consensus(ticker):
    """Get consensus data from Station 3001 bloomberg module for fallback."""
    try:
        resp = requests.get(f'{DATA_CENTER}/api/bloomberg/consensus/{ticker}', timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


def _get_bbg_history(ticker, days=60):
    """Get OHLCV bars from bloomberg_archive in Station 3001 as price fallback."""
    try:
        resp = requests.get(f'{DATA_CENTER}/api/yf/history/{ticker}?period=3mo&interval=1d', timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            bars = data.get('bars', [])
            return bars[-days:] if bars else []
    except:
        pass
    return []


def _get_earnings_surprise(ticker):
    """Get earnings surprise data from Station 3001 factset module."""
    try:
        resp = requests.get(f'{DATA_CENTER}/api/factset/estimates/surprise/{ticker}?periods=8', timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('surprises', [])
    except:
        pass
    return []


@app.route('/api/deep/<ticker>')
def deep_analysis(ticker):
    """Full 3-layer deep analysis with Cross Check, Sentiment, and FactSet fallback."""
    ticker = ticker.upper().strip()
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        # ═══════════════════════════════════════════════════════════════
        # PRE-FETCH: FactSet snapshot for fallback
        # ═══════════════════════════════════════════════════════════════
        fs_snapshot = _get_factset_snapshot(ticker)

        # ═══════════════════════════════════════════════════════════════
        # LAYER 1: Real-Time Data + News Sentiment
        # ═══════════════════════════════════════════════════════════════
        price = safe_float(info.get('currentPrice') or info.get('regularMarketPrice'))
        prev_close = safe_float(info.get('previousClose'))
        change = round(price - prev_close, 4) if price and prev_close else 0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
        market_cap = safe_float(info.get('marketCap', 0))
        total_debt = safe_float(info.get('totalDebt', 0))
        cash = safe_float(info.get('totalCash', 0))
        minority = safe_float(info.get('minorityInterest', 0))
        preferred = safe_float(info.get('preferredStock', 0))
        ev = market_cap + total_debt + minority + preferred - cash
        ev_b = ev / 1e9

        # Sentiment from Station 3003
        sentiment = _get_sentiment(ticker)

        # ── Price history with fallback ──────────────────────────────
        price_history = []
        try:
            hist = t.history(period='1y', interval='1d')
            if hist is not None and not hist.empty:
                tail_n = min(60, len(hist))
                for idx, row in hist.tail(tail_n).iterrows():
                    price_history.append({'date': str(idx)[:10], 'close': round(float(row['Close']), 2)})
        except Exception as hist_err:
            print(f"YF history failed for {ticker}: {hist_err}")

        # Fallback: if YF history empty, try Station 3001 history endpoint
        if len(price_history) == 0:
            fallback_bars = _get_bbg_history(ticker, 60)
            for bar in fallback_bars:
                price_history.append({
                    'date': bar.get('date', ''),
                    'close': safe_float(bar.get('close', 0)),
                })

        # ── Analyst consensus with fallback ──────────────────────────
        target_mean = safe_float(info.get('targetMeanPrice'))
        target_high = safe_float(info.get('targetHighPrice'))
        target_low = safe_float(info.get('targetLowPrice'))
        num_analysts = safe_int(info.get('numberOfAnalystOpinions'))
        analyst_rating = safe_float(info.get('recommendationMean', 3.0))
        analyst_source = 'yahoo_finance'

        # If YF analyst data missing, fallback to Bloomberg/FactSet consensus
        if target_mean == 0 and num_analysts == 0:
            bbg_consensus = _get_bloomberg_consensus(ticker)
            if bbg_consensus:
                pt = bbg_consensus.get('priceTarget', {})
                target_mean = safe_float(pt.get('mean'))
                target_high = safe_float(pt.get('high'))
                target_low = safe_float(pt.get('low'))
                num_analysts = safe_int(pt.get('analystCount'))
                analyst_source = bbg_consensus.get('dataSource', 'bloomberg_fallback')

            # Further fallback: FactSet Excel snapshot
            if target_mean == 0 and fs_snapshot:
                target_mean = safe_float(fs_snapshot.get('targetPrice'))
                num_analysts = safe_int(fs_snapshot.get('brokerContributors'))
                r_label = fs_snapshot.get('analystRating', '')
                if r_label in ('Buy', 'Overweight', 'Strong Buy'):
                    analyst_rating = safe_float(fs_snapshot.get('analystRatingScore', 2.0))
                analyst_source = 'factset_excel_snapshot'

        layer1 = {
            'description': 'Layer 1: Real-Time Data + News Sentiment (via Station 3003)',
            'price': round(price, 2), 'prevClose': round(prev_close, 2),
            'change': round(change, 2), 'changePct': round(change_pct, 2),
            'marketCap_b': round(market_cap / 1e9, 2),
            'name': info.get('longName') or info.get('shortName', ticker),
            'sector': info.get('sector', '—'), 'industry': info.get('industry', '—'),
            'week52High': safe_float(info.get('fiftyTwoWeekHigh')),
            'week52Low': safe_float(info.get('fiftyTwoWeekLow')),
            'beta': safe_float(info.get('beta', 1.0)),
            'forwardPE': safe_float(info.get('forwardPE')),
            'trailingPE': safe_float(info.get('trailingPE')),
            'sentiment': sentiment,
            'priceHistory': price_history,
            'analyst': {
                'rating': analyst_rating,
                'numAnalysts': num_analysts,
                'targetMean': target_mean,
                'targetHigh': target_high,
                'targetLow': target_low,
                'upsidePct': round((target_mean / price - 1) * 100, 1) if price > 0 and target_mean > 0 else 0,
                'source': analyst_source,
            },
            'dataSource': 'yahoo_finance_live + station_3003_sentiment',
        }

        # ═══════════════════════════════════════════════════════════════
        # LAYER 2: Non-GAAP Adjustments + Cross Check with 3001
        # ═══════════════════════════════════════════════════════════════
        try:
            cfs = t.quarterly_cashflow
        except:
            cfs = None
        try:
            ics = t.quarterly_income_stmt
        except:
            ics = None

        def q_val(df, patterns, q=0, scale=1e6):
            if df is None or df.empty:
                return 0
            cols = list(df.columns)
            if q >= len(cols):
                return 0
            col = cols[q]
            for pat in patterns:
                for idx in df.index:
                    if pat.lower() in str(idx).lower():
                        val = df.loc[idx, col]
                        try:
                            return float(val) / scale if val == val else 0
                        except:
                            return 0
            return 0

        def ttm_val(df, patterns, scale=1e6):
            if df is None or df.empty:
                return 0
            n_quarters = min(4, len(df.columns) if df is not None and not df.empty else 0)
            return sum(q_val(df, patterns, q=i, scale=scale) for i in range(n_quarters))

        ocf_ttm = ttm_val(cfs, ['operating cash flow', 'total cash from operating activities'])
        capex_ttm = abs(ttm_val(cfs, ['capital expenditure', 'purchase of property plant']))
        sbc_ttm = abs(ttm_val(cfs, ['stock based compensation', 'share based compensation']))
        da_ttm = abs(ttm_val(cfs, ['depreciation', 'depreciation and amortization']))
        op_income_ttm = ttm_val(ics, ['operating income', 'ebit'])
        rev_ttm = ttm_val(ics, ['total revenue', 'revenue'])
        adj_ebitda_ttm = op_income_ttm + da_ttm + sbc_ttm
        fcf_ttm = ocf_ttm - capex_ttm
        adj_fcf_ttm = ocf_ttm - capex_ttm - sbc_ttm

        # ── Growth & Profitability with FactSet fallback ─────────────
        gross_margin = safe_float(info.get('grossMargins', 0)) * 100
        roe = safe_float(info.get('returnOnEquity', 0)) * 100
        revenue_growth = safe_float(info.get('revenueGrowth', 0)) * 100
        operating_margin = safe_float(info.get('operatingMargins', 0)) * 100
        net_margin = safe_float(info.get('profitMargins', 0)) * 100
        roa = safe_float(info.get('returnOnAssets', 0)) * 100
        earnings_growth = safe_float(info.get('earningsGrowth', 0)) * 100

        # Fallback to FactSet snapshot if YF income stmt is empty
        if (gross_margin == 0 or roe == 0) and fs_snapshot:
            if gross_margin == 0:
                gross_margin = safe_float(fs_snapshot.get('grossMargin', 0))
            if roe == 0:
                roe = safe_float(fs_snapshot.get('roe', 0))
            if operating_margin == 0:
                operating_margin = safe_float(fs_snapshot.get('operatingMargin', 0))
            if net_margin == 0:
                net_margin = safe_float(fs_snapshot.get('netMargin', 0))
            if roa == 0:
                roa = safe_float(fs_snapshot.get('roa', 0))

        ev_ebitda_adj = (ev_b * 1e9 / (adj_ebitda_ttm * 1e6)) if adj_ebitda_ttm != 0 else 0
        fcf_yield = (fcf_ttm * 1e6 / market_cap * 100) if market_cap > 0 else 0
        net_debt = (total_debt - cash) / 1e9
        net_leverage = (net_debt / (adj_ebitda_ttm / 1000)) if adj_ebitda_ttm != 0 else 0

        # Cross Check with Station 3001 Gold Standard
        yf_metrics = {
            'evEbitda': ev_ebitda_adj,
            'grossMargin': gross_margin,
            'revenueGrowth': revenue_growth,
            'roe': roe,
            'forwardPE': safe_float(info.get('forwardPE')),
        }
        cross_check = _cross_check_with_gold(ticker, yf_metrics)

        # Audit flags
        audit_flags = []
        if sbc_ttm > 0 and adj_ebitda_ttm > 0:
            sbc_pct = sbc_ttm / adj_ebitda_ttm * 100
            if sbc_pct > 15:
                audit_flags.append(f'SBC/Adj.EBITDA {sbc_pct:.0f}% — high non-cash distortion')
        if net_leverage > 3.0:
            audit_flags.append(f'Net Leverage {net_leverage:.1f}x > 3.0x — high debt risk')

        # ── Earnings surprise (Layer 2 core) ─────────────────────────
        earnings_surprise = _get_earnings_surprise(ticker)

        layer2 = {
            'description': 'Layer 2: Non-GAAP Adjustments + Cross Check (Station 3001)',
            'ev_decomp': {
                'market_cap_b': round(market_cap / 1e9, 2),
                'total_debt_b': round(total_debt / 1e9, 2),
                'cash_b': round(cash / 1e9, 2),
                'ev_b': round(ev_b, 2),
            },
            'adj_ebitda': {
                'op_income_ttm_m': round(op_income_ttm, 1),
                'da_ttm_m': round(da_ttm, 1),
                'sbc_ttm_m': round(sbc_ttm, 1),
                'adj_ebitda_ttm_m': round(adj_ebitda_ttm, 1),
            },
            'ev_multiples': {
                'ev_ebitda_adj': round(ev_ebitda_adj, 1),
                'forward_pe': safe_float(info.get('forwardPE')),
            },
            'fcf_analysis': {
                'ocf_ttm_m': round(ocf_ttm, 1),
                'capex_ttm_m': round(capex_ttm, 1),
                'fcf_ttm_m': round(fcf_ttm, 1),
                'adj_fcf_ttm_m': round(adj_fcf_ttm, 1),
                'fcf_yield_pct': round(fcf_yield, 2),
            },
            'leverage': {
                'net_debt_b': round(net_debt, 2),
                'net_leverage_x': round(net_leverage, 2),
                'risk': 'HIGH' if net_leverage > 3.0 else 'MEDIUM' if net_leverage > 1.5 else 'LOW',
            },
            'growth': {
                'revenue_growth_yoy_pct': round(revenue_growth, 1),
                'earnings_growth_yoy_pct': round(earnings_growth, 1),
                'gross_margin_pct': round(gross_margin, 1),
                'operating_margin_pct': round(operating_margin, 1),
                'net_margin_pct': round(net_margin, 1),
                'roe_pct': round(roe, 1),
                'roa_pct': round(roa, 1),
            },
            'earnings_surprise': earnings_surprise,
            'crossCheck': cross_check,
            'audit_flags': audit_flags,
        }

        # ═══════════════════════════════════════════════════════════════
        # LAYER 3: AI Summary — confidence adjusted by Cross Check
        # ═══════════════════════════════════════════════════════════════
        audit_passed = cross_check.get('auditPassed', False)
        confidence_boost = '(Cross Check PASSED — higher confidence)' if audit_passed else '(Cross Check FAILED — lower confidence, verify data)'

        ai_summary_text = (
            f"Ticker: {ticker} | {layer1['name']}\n"
            f"Sector: {layer1['sector']} | Price: ${layer1['price']} ({layer1['changePct']}%)\n"
            f"EV/Adj.EBITDA: {round(ev_ebitda_adj,1)}x | FCF Yield: {round(fcf_yield,2)}%\n"
            f"Net Leverage: {round(net_leverage,2)}x | Revenue Growth: {revenue_growth:.1f}%\n"
            f"Gross Margin: {gross_margin:.1f}% | ROE: {roe:.1f}%\n"
            f"Sentiment: {sentiment.get('sentimentLabel','neutral')} ({sentiment.get('sentimentScore',0)})\n"
            f"Audit: {confidence_boost}"
        )

        layer3 = {
            'description': 'Layer 3: AI Summary (confidence adjusted by Cross Check)',
            'confidenceLevel': 'HIGH' if audit_passed else 'MEDIUM',
            'confidenceNote': confidence_boost,
            'ai_summary': ai_summary_text,
            'sentimentIntegrated': True,
            'crossCheckIntegrated': True,
        }

        result = {
            'ticker': ticker, 'name': layer1['name'],
            'sector': layer1['sector'], 'industry': layer1['industry'],
            'layer1': layer1, 'layer2': layer2, 'layer3': layer3,
            # Backward-compatible quick access
            'price': layer1['price'], 'marketCap_b': layer1['marketCap_b'],
            'ev_decomp': layer2['ev_decomp'], 'adj_ebitda': layer2['adj_ebitda'],
            'ev_multiples': layer2['ev_multiples'], 'fcf_analysis': layer2['fcf_analysis'],
            'leverage': layer2['leverage'], 'audit_flags': layer2['audit_flags'],
            'growth': layer2['growth'],
            'analyst': layer1['analyst'],
            'earnings_surprise': earnings_surprise,
            'crossCheck': cross_check,
            'factset_validated': cross_check.get('auditPassed', False),
            'week52_high': layer1['week52High'],
            'week52_low': layer1['week52Low'],
            'beta': layer1['beta'],
            'dataSource': 'yahoo_finance + station_3001_crosscheck + station_3003_sentiment',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'ticker': ticker}), 500


@app.route('/api/deep/quick/<ticker>')
def deep_quick(ticker):
    """Quick Layer 1+2 analysis (no external AI prompt generation)."""
    return deep_analysis(ticker)


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3004 — Deep Analysis (个股深钻)")
    print("  3-Layer: Real-Time + Non-GAAP + AI (Cross Check enabled)")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3004, debug=False)
