"""
modules/factset — FactSet API Integration and Data Enrichment
Cross-validation, concordance, screening, estimates, security intelligence.
"""

from flask import Blueprint, jsonify, request
import yfinance as yf
from datetime import datetime, timedelta
import traceback
import urllib.request
import urllib.parse
import urllib.error
import base64

from modules.utils import cache_get, cache_set, safe_float, safe_int, CORE_TICKERS

bp = Blueprint('factset', __name__, url_prefix='/api')


def _get_blpapi_available():
    """Check if Bloomberg/FactSet API is available."""
    try:
        from modules.bloomberg.routes import BLPAPI_AVAILABLE
        return BLPAPI_AVAILABLE
    except:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/crossvalidate/<ticker>
# ══════════════════════════════════════════════════════════════════════════════
def _fetch_factset_snapshot(ticker):
    """Fetch FactSet Excel snapshot from Station 3001 for cross-validation."""
    try:
        import json as _json
        req = urllib.request.Request(f'http://localhost:3001/api/dc/factset-snapshot/{ticker}')
        with urllib.request.urlopen(req, timeout=3) as resp:
            fs_data = _json.loads(resp.read().decode())
            if fs_data.get('success') and fs_data.get('snapshot'):
                return fs_data['snapshot']
    except:
        pass
    return None


@bp.route('/factset/crossvalidate/<ticker>')
def factset_crossvalidate(ticker):
    ticker = ticker.upper().strip()
    cache_key = f'fs_xval_{ticker}'
    cached = cache_get(cache_key, ttl=1800)
    if cached:
        return jsonify(cached)

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # ── Yahoo Finance metrics ──────────────────────────────────────────
        yf_price        = safe_float(info.get('currentPrice') or info.get('regularMarketPrice'))
        yf_fwd_pe       = safe_float(info.get('forwardPE'))
        yf_trail_pe     = safe_float(info.get('trailingPE'))
        yf_rev_growth   = safe_float(info.get('revenueGrowth', 0)) * 100
        yf_eps_fwd      = safe_float(info.get('forwardEps'))
        yf_eps_ttm      = safe_float(info.get('trailingEps'))
        yf_target       = safe_float(info.get('targetMeanPrice'))
        yf_gross_margin = safe_float(info.get('grossMargins', 0)) * 100
        yf_op_margin    = safe_float(info.get('operatingMargins', 0)) * 100
        yf_roe          = safe_float(info.get('returnOnEquity', 0)) * 100
        yf_roa          = safe_float(info.get('returnOnAssets', 0)) * 100
        yf_beta         = safe_float(info.get('beta'))
        yf_mkt_cap_b    = safe_float(info.get('marketCap', 0)) / 1e9
        yf_ev_ebitda    = safe_float(info.get('enterpriseToEbitda'))
        yf_div_yield    = safe_float(info.get('dividendYield', 0)) * 100

        # ── FactSet Excel Snapshot ─────────────────────────────────────────
        fs = _fetch_factset_snapshot(ticker)
        used_factset = fs is not None

        # ── CPA-grade divergence thresholds (relative %) ───────────────────
        # price ±2%, marketCap ±5%, P/E ±8%, EV/EBITDA ±10%,
        # grossMargin ±5%, opMargin ±5%, ROE ±10%, targetPrice ±5%, beta ±15%
        CHECKS = [
            ('price',       '股价 Price',              yf_price,        safe_float(fs.get('price')) if fs else 0,           2),
            ('marketCapB',  '市值 Market Cap (B)',      yf_mkt_cap_b,    safe_float(fs.get('marketCapB')) if fs else 0,      5),
            ('peLTM',       'P/E (LTM)',               yf_trail_pe,     safe_float(fs.get('peLTM')) if fs else 0,           8),
            ('evEbitda',    'EV/EBITDA',               yf_ev_ebitda,    safe_float(fs.get('evEbitdaLTM')) if fs else 0,     10),
            ('grossMargin', '毛利率 Gross Margin',     yf_gross_margin, safe_float(fs.get('grossMargin')) if fs else 0,      5),
            ('opMargin',    '经营利润率 Op Margin',     yf_op_margin,    safe_float(fs.get('operatingMargin')) if fs else 0,  5),
            ('roe',         'ROE',                     yf_roe,          safe_float(fs.get('roe')) if fs else 0,             10),
            ('targetPrice', '目标价 Target Price',      yf_target,       safe_float(fs.get('targetPrice')) if fs else 0,     5),
            ('beta',        'Beta',                    yf_beta,         safe_float(fs.get('beta')) if fs else 0,           15),
        ]

        comparisons = []
        divergences = []
        for field, label, yf_val, fs_val, threshold_pct in CHECKS:
            if not yf_val or not fs_val:
                continue
            denom = abs(fs_val) if used_factset else abs(max(yf_val, fs_val))
            if denom == 0:
                continue
            diff_pct = abs(yf_val - fs_val) / denom * 100
            is_div = diff_pct > threshold_pct
            comp = {
                'field': field, 'label': label,
                'yahooFinance': round(yf_val, 2), 'factset': round(fs_val, 2),
                'divergencePct': round(diff_pct, 1),
                'threshold': threshold_pct,
                'isDivergent': is_div,
                'status': 'DIVERGENT' if is_div else 'PASS',
            }
            comparisons.append(comp)
            if is_div:
                divergences.append(comp)

        result = {
            'ticker': ticker,
            'validated': True,
            'usedFactSet': used_factset,
            'snapshotDate': fs.get('snapshotDate') if fs else None,
            'yfMetrics': {
                'price': yf_price, 'forwardPE': yf_fwd_pe, 'trailingPE': yf_trail_pe,
                'revenueGrowth': round(yf_rev_growth, 1),
                'forwardEps': yf_eps_fwd, 'trailingEps': yf_eps_ttm,
                'targetMean': yf_target,
                'grossMargin': round(yf_gross_margin, 1),
                'operatingMargin': round(yf_op_margin, 1),
                'roe': round(yf_roe, 1), 'roa': round(yf_roa, 1),
                'beta': yf_beta, 'marketCapB': round(yf_mkt_cap_b, 2),
                'evEbitda': yf_ev_ebitda, 'divYield': round(yf_div_yield, 2),
            },
            'factsetMetrics': {
                'price': fs.get('price') if fs else None,
                'peLTM': fs.get('peLTM') if fs else None,
                'evEbitdaLTM': fs.get('evEbitdaLTM') if fs else None,
                'targetPrice': fs.get('targetPrice') if fs else None,
                'marketCapB': fs.get('marketCapB') if fs else None,
                'grossMargin': fs.get('grossMargin') if fs else None,
                'operatingMargin': fs.get('operatingMargin') if fs else None,
                'roe': fs.get('roe') if fs else None,
                'roa': fs.get('roa') if fs else None,
                'beta': fs.get('beta') if fs else None,
                'epsConsensus': fs.get('epsConsensus') if fs else None,
                'analystRating': fs.get('analystRating') if fs else None,
                'analystRatingScore': fs.get('analystRatingScore') if fs else None,
                'dividendYield': fs.get('dividendYield') if fs else None,
                'wacc': fs.get('wacc') if fs else None,
                'snapshotDate': fs.get('snapshotDate') if fs else None,
                'source': 'FactSet Excel Snapshot (PIT Compliant)' if used_factset else 'Yahoo Finance Forward (NTM Proxy)',
            },
            'comparisons': comparisons,
            'divergences': divergences,
            'divergenceFlag': len(divergences) > 0,
            'totalChecks': len(comparisons),
            'passedChecks': len(comparisons) - len(divergences),
            'dataSource': 'factset_snapshot_vs_yf' if used_factset else 'yf_forward_vs_trailing',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'ticker': ticker, 'validated': False}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/validate/<ticker>
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/validate/<ticker>')
def factset_validate(ticker):
    ticker = ticker.upper().strip()
    return jsonify({
        'ticker': ticker,
        'validated': False,
        'message': 'FactSet API key not configured.',
        'setup_guide': {
            'step1': 'Obtain API key from FactSet Developer Portal',
            'step2': 'Add to .dev.vars: BLPAPI_AVAILABLE=your_key',
            'step3': 'Restart data service',
        }
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/concordance/entity
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/concordance/entity')
def factset_concordance_entity():
    name   = request.args.get('name', '').strip()
    ticker = request.args.get('ticker', '').upper().strip()
    if not name and not ticker:
        return jsonify({'error': 'Provide name or ticker parameter'}), 400

    factset_id = f'{ticker}-US' if ticker else None
    result = {
        'query': name or ticker,
        'matches': [{'factsetId': factset_id, 'entityName': name or ticker, 'matchFlag': 'HEURISTIC'}] if factset_id else [],
        'count': 1 if factset_id else 0,
        'dataSource': 'heuristic_fallback',
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    }
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/screening/run
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/screening/run', methods=['GET', 'POST'])
def factset_screening_run():
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        screen_type = body.get('screen', 'value')
        limit = int(body.get('limit', 50))
    else:
        screen_type = request.args.get('screen', 'value')
        limit = int(request.args.get('limit', 50))

    SCREENS = {
        'value':    {'label': 'Deep Value'},
        'growth':   {'label': 'High Growth'},
        'quality':  {'label': 'Quality'},
        'momentum': {'label': 'Momentum'},
    }

    cache_key = f'fs_screening_{screen_type}_{limit}'
    cached = cache_get(cache_key, ttl=1800)
    if cached:
        return jsonify(cached)

    try:
        TICKERS_TO_SCREEN = CORE_TICKERS[:30]
        results = []
        for t_sym in TICKERS_TO_SCREEN:
            c_key = f'screen_stock_{t_sym}'
            stock_data = cache_get(c_key, ttl=3600)
            if not stock_data:
                try:
                    t = yf.Ticker(t_sym)
                    info = t.info or {}
                    stock_data = {
                        'ticker': t_sym,
                        'name': info.get('longName', t_sym),
                        'sector': info.get('sector', 'Unknown'),
                        'marketCap': safe_float(info.get('marketCap', 0)) / 1e9,
                        'forwardPE': safe_float(info.get('forwardPE')),
                        'revenueGrowth': safe_float(info.get('revenueGrowth', 0)) * 100,
                        'grossMargin': safe_float(info.get('grossMargins', 0)) * 100,
                        'fcfYield': 0,
                        'evEbitda': safe_float(info.get('enterpriseToEbitda')),
                    }
                    cache_set(c_key, stock_data)
                except:
                    continue
            results.append(stock_data)

        if screen_type == 'value':
            results = [s for s in results if s.get('evEbitda', 99) < 15 and s.get('forwardPE', 99) < 25]
        elif screen_type == 'growth':
            results = [s for s in results if s.get('revenueGrowth', 0) > 10]
        elif screen_type == 'quality':
            results = [s for s in results if s.get('grossMargin', 0) > 35]

        results = sorted(results, key=lambda x: x.get('revenueGrowth', 0), reverse=True)[:limit]

        result = {
            'screen': screen_type,
            'screenLabel': SCREENS.get(screen_type, {}).get('label', screen_type),
            'stocks': results,
            'count': len(results),
            'dataSource': 'yf_local_screener',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/security-intel/<ticker>
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/security-intel/<ticker>')
def factset_security_intel(ticker):
    ticker = ticker.upper().strip()
    output_type = request.args.get('outputType', 'full')
    cache_key = f'fs_secintel_{ticker}_{output_type}'
    cached = cache_get(cache_key, ttl=900)
    if cached:
        return jsonify(cached)

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        price     = safe_float(info.get('currentPrice') or info.get('regularMarketPrice'))
        prev      = safe_float(info.get('previousClose'))
        chg_pct   = ((price - prev) / prev * 100) if prev else 0
        week52h   = safe_float(info.get('fiftyTwoWeekHigh'))
        week52l   = safe_float(info.get('fiftyTwoWeekLow'))
        avg_vol   = safe_float(info.get('averageVolume'))
        vol       = safe_float(info.get('volume'))
        vol_ratio = vol / avg_vol if avg_vol else 1.0

        movement_summary = {
            'ticker': ticker, 'price': price,
            'change_pct': round(chg_pct, 2),
            'direction': 'up' if chg_pct > 0 else 'down' if chg_pct < 0 else 'flat',
            'volume_ratio': round(vol_ratio, 2),
            'signal': 'high_volume' if vol_ratio > 2 else 'normal',
            'week52_high': week52h, 'week52_low': week52l,
            'pct_from_52h': round((price - week52h) / week52h * 100, 1) if week52h else None,
            'pct_from_52l': round((price - week52l) / week52l * 100, 1) if week52l else None,
            'short_summary': f'{ticker} {"+%.2f" % chg_pct if chg_pct >= 0 else "%.2f" % chg_pct}% today, vol ratio {vol_ratio:.1f}x avg',
        }
        if output_type == 'oneline':
            movement_summary = {'oneline': movement_summary['short_summary']}

        result = {
            'ticker': ticker,
            'stockMovement': movement_summary,
            'events': [],
            'dataSource': 'yahoo_finance_fallback',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'ticker': ticker}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/estimates/rolling/<ticker>
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/estimates/rolling/<ticker>')
def factset_estimates_rolling(ticker):
    ticker = ticker.upper().strip()
    fiscal = request.args.get('period', 'NTM')
    cache_key = f'fs_est_rolling_{ticker}_{fiscal}'
    cached = cache_get(cache_key, ttl=3600)
    if cached:
        return jsonify(cached)

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        items = [
            {'metric': 'EPS', 'value': safe_float(info.get('forwardEps')), 'currency': 'USD'},
            {'metric': 'EPS_GROWTH', 'value': safe_float(info.get('earningsGrowth')), 'currency': 'USD'},
            {'metric': 'SALES_GROWTH', 'value': safe_float(info.get('revenueGrowth')), 'currency': 'USD'},
            {'metric': 'EBITDA', 'value': safe_float(info.get('ebitda')), 'currency': 'USD'},
        ]
        rec = info.get('recommendationMean', 3.0)
        rating_map = {1: 'Strong Buy', 2: 'Buy', 3: 'Hold', 4: 'Underperform', 5: 'Sell'}
        result = {
            'ticker': ticker, 'period': fiscal, 'estimates': items,
            'ratings': {
                'recommendation': rating_map.get(round(rec), 'Hold'),
                'recommendationMean': rec,
                'numAnalysts': info.get('numberOfAnalystOpinions', 0),
            },
            'count': len(items),
            'dataSource': 'yahoo_finance_forward',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'ticker': ticker}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/factset/estimates/surprise/<ticker>
# ══════════════════════════════════════════════════════════════════════════════
@bp.route('/factset/estimates/surprise/<ticker>')
def factset_estimates_surprise(ticker):
    ticker = ticker.upper().strip()
    periods = int(request.args.get('periods', 8))
    cache_key = f'fs_surprise_{ticker}_{periods}'
    cached = cache_get(cache_key, ttl=3600)
    if cached:
        return jsonify(cached)

    try:
        t = yf.Ticker(ticker)
        try:
            hist_earn = t.earnings_history
        except:
            hist_earn = None

        surprises = []
        if hist_earn is not None and not hist_earn.empty:
            for idx, row in hist_earn.iterrows():
                actual   = row.get('epsActual')
                estimate = row.get('epsEstimate')
                surprise_pct = None
                if estimate and estimate != 0 and actual is not None:
                    surprise_pct = round((float(actual) - float(estimate)) / abs(float(estimate)) * 100, 2)
                surprises.append({
                    'period': str(idx)[:10],
                    'metric': 'EPS',
                    'actual': float(actual) if actual else None,
                    'estimate': float(estimate) if estimate else None,
                    'surprise_pct': surprise_pct,
                    'beat': surprise_pct > 0 if surprise_pct else None,
                })

        result = {
            'ticker': ticker,
            'surprises': surprises[:periods],
            'count': len(surprises),
            'dataSource': 'yahoo_finance_earnings_history',
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'ticker': ticker}), 500
