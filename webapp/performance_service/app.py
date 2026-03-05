"""
QuantAlpha Station 3008 — Performance v2.0 (审计归因 — Alpha Research Lab)
═══════════════════════════════════════════════════════════════════════════
Alpha Research Mode — Focus on Factor Effectiveness:
  • Information Ratio (IR): excess return per unit tracking error
  • Factor Autocorrelation: measure factor signal stability / turnover
  • Alpha Decay: test signal effectiveness at 5d / 20d / 60d horizons
  • Brinson Attribution: decompose alpha into allocation vs selection
  • IC/Rank IC analysis for factor utility
  • Standard metrics: Sharpe, Sortino, Max Drawdown, VaR, CVaR

Data Flow:
  Station 3007 → position/trade data → Station 3008
  Station 3005 → factor data → Station 3008 (for factor analysis)
"""

import sys
import os
import time
import math
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_start_time = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# Performance data (enhanced with Alpha Research metrics)
# ══════════════════════════════════════════════════════════════════════════════
PERFORMANCE = {
    'totalAssets': 600000, 'totalPnl': 125000, 'totalPnlPct': 26.32,
    'dailyPnl': 4250, 'dailyPnlPct': 0.71,
    'sharpe': 1.84, 'sortino': 2.56, 'maxDrawdown': -12.5, 'calmar': 1.47,
    'winRate': 64.8, 'avgHoldDays': 18.3, 'profitFactor': 2.31,
    'informationRatio': 1.12, 'trackingError': 8.4,
    'alpha': 5.2, 'beta': 0.73, 'treynorRatio': 7.12,
    'var95': -2.1, 'cvar95': -3.4,
    'monthlyReturns': [1.2, -0.8, 2.4, 1.1, -1.5, 3.2, 0.6, -0.3, 2.8, 1.9, -0.5, 4.1],
    'sectorExposure': {
        'Information Technology': 47.5,
        'Communication Services': 17.1,
        'Consumer Discretionary': 9.1,
        'Cash': 26.3,
    },
    'strategyAttribution': {
        'ML-Enhanced Momentum': {'pnl': 81687.0, 'pnlPct': 36.2, 'weight': 30.5},
        'Buy-the-Dip Core': {'pnl': 15608.0, 'pnlPct': 20.1, 'weight': 16.2},
        'Earnings Catalyst': {'pnl': 10980.0, 'pnlPct': 25.7, 'weight': 8.9},
        'Contrarian VIX Spike': {'pnl': 11350.0, 'pnlPct': 26.3, 'weight': 9.1},
    },
    'updatedAt': '2026-03-04T16:00:00Z',
}

# ══════════════════════════════════════════════════════════════════════════════
# Brinson Attribution Data (NEW)
# ══════════════════════════════════════════════════════════════════════════════
BRINSON_ATTRIBUTION = {
    'period': '2025-01-01 to 2025-12-31',
    'portfolioReturn': 26.32,
    'benchmarkReturn': 18.50,
    'excessReturn': 7.82,
    'decomposition': {
        'allocationEffect': 2.41,
        'selectionEffect': 4.89,
        'interactionEffect': 0.52,
        'totalActive': 7.82,
    },
    'sectorDetail': [
        {'sector': 'Information Technology', 'portWeight': 47.5, 'benchWeight': 32.1,
         'portReturn': 35.2, 'benchReturn': 28.1,
         'allocation': 1.42, 'selection': 3.38, 'interaction': 0.22},
        {'sector': 'Communication Services', 'portWeight': 17.1, 'benchWeight': 9.2,
         'portReturn': 22.8, 'benchReturn': 15.6,
         'allocation': 0.58, 'selection': 0.66, 'interaction': 0.15},
        {'sector': 'Consumer Discretionary', 'portWeight': 9.1, 'benchWeight': 10.8,
         'portReturn': 18.4, 'benchReturn': 12.3,
         'allocation': -0.12, 'selection': 0.66, 'interaction': 0.08},
        {'sector': 'Healthcare', 'portWeight': 0.0, 'benchWeight': 12.4,
         'portReturn': 0, 'benchReturn': 8.2,
         'allocation': 0.31, 'selection': 0, 'interaction': 0},
        {'sector': 'Financials', 'portWeight': 0.0, 'benchWeight': 13.1,
         'portReturn': 0, 'benchReturn': 14.5,
         'allocation': 0.22, 'selection': 0, 'interaction': 0},
        {'sector': 'Cash', 'portWeight': 26.3, 'benchWeight': 0.0,
         'portReturn': 5.0, 'benchReturn': 0,
         'allocation': 0, 'selection': 0.19, 'interaction': 0.07},
    ],
    'insight': 'Selection Effect (4.89%) dominated — your stock picking within sectors generated most alpha. Allocation Effect (2.41%) was positive due to overweight in Technology. Interaction Effect (0.52%) was minor.',
}

# ══════════════════════════════════════════════════════════════════════════════
# Factor Autocorrelation Data (NEW)
# ══════════════════════════════════════════════════════════════════════════════
def _generate_factor_autocorrelation():
    """Generate factor autocorrelation data — measures signal stability."""
    random.seed(99)
    factors = {
        'momentum_rsi': {'lag1': 0.82, 'lag5': 0.65, 'lag20': 0.31, 'turnover': 0.24},
        'value_ev_ebitda': {'lag1': 0.95, 'lag5': 0.91, 'lag20': 0.84, 'turnover': 0.08},
        'quality_accruals': {'lag1': 0.93, 'lag5': 0.87, 'lag20': 0.78, 'turnover': 0.11},
        'fcf_yield': {'lag1': 0.94, 'lag5': 0.89, 'lag20': 0.81, 'turnover': 0.09},
        'earnings_surprise': {'lag1': 0.45, 'lag5': 0.22, 'lag20': 0.08, 'turnover': 0.62},
        'sentiment_score': {'lag1': 0.38, 'lag5': 0.15, 'lag20': 0.05, 'turnover': 0.71},
        'operating_leverage': {'lag1': 0.91, 'lag5': 0.85, 'lag20': 0.76, 'turnover': 0.12},
        'rd_intensity': {'lag1': 0.97, 'lag5': 0.95, 'lag20': 0.92, 'turnover': 0.04},
    }
    return factors


# ══════════════════════════════════════════════════════════════════════════════
# Alpha Decay Data (NEW)
# ══════════════════════════════════════════════════════════════════════════════
def _generate_alpha_decay():
    """Generate alpha decay curves for each factor signal."""
    random.seed(77)
    factors = {
        'momentum_composite': {
            'horizons': [1, 2, 3, 5, 10, 20, 40, 60],
            'ic_values': [0.092, 0.085, 0.078, 0.068, 0.045, 0.028, 0.015, 0.008],
            'halflife_days': 8,
            'decayType': 'fast_exponential',
            'tradingImplication': 'Rebalance weekly for optimal capture. Monthly rebalance loses 60% of signal.',
        },
        'value_composite': {
            'horizons': [1, 2, 3, 5, 10, 20, 40, 60],
            'ic_values': [0.015, 0.021, 0.028, 0.038, 0.052, 0.065, 0.071, 0.068],
            'halflife_days': 45,
            'decayType': 'slow_buildup',
            'tradingImplication': 'Value signals strengthen over 1-2 months. Quarterly rebalance optimal.',
        },
        'quality_composite': {
            'horizons': [1, 2, 3, 5, 10, 20, 40, 60],
            'ic_values': [0.022, 0.028, 0.034, 0.042, 0.055, 0.062, 0.058, 0.051],
            'halflife_days': 30,
            'decayType': 'hump_shaped',
            'tradingImplication': 'Quality peaks at 20-day horizon. Monthly rebalance captures the sweet spot.',
        },
        'earnings_event': {
            'horizons': [1, 2, 3, 5, 10, 20, 40, 60],
            'ic_values': [0.112, 0.095, 0.072, 0.041, 0.018, 0.005, 0.002, 0.001],
            'halflife_days': 3,
            'decayType': 'ultra_fast',
            'tradingImplication': 'Event-driven signal. Must trade within 1-3 days of signal. Stale after 1 week.',
        },
        'sentiment_nlp': {
            'horizons': [1, 2, 3, 5, 10, 20, 40, 60],
            'ic_values': [0.048, 0.042, 0.035, 0.025, 0.012, 0.006, 0.003, 0.001],
            'halflife_days': 5,
            'decayType': 'fast_exponential',
            'tradingImplication': 'News sentiment signal decays rapidly. Intraday to weekly horizon only.',
        },
    }
    return factors


_POSITIONS = [
    {'ticker': 'NVDA', 'marketValue': 133875.0, 'unrealizedPnl': 61095.0, 'sector': 'Information Technology', 'weight': 22.3},
    {'ticker': 'AAPL', 'marketValue': 45560.0, 'unrealizedPnl': 9860.0, 'sector': 'Information Technology', 'weight': 7.6},
    {'ticker': 'GOOGL', 'marketValue': 53670.0, 'unrealizedPnl': 10980.0, 'sector': 'Communication Services', 'weight': 8.9},
    {'ticker': 'META', 'marketValue': 49024.0, 'unrealizedPnl': 20592.0, 'sector': 'Communication Services', 'weight': 8.2},
    {'ticker': 'MSFT', 'marketValue': 51420.0, 'unrealizedPnl': 5748.0, 'sector': 'Information Technology', 'weight': 8.6},
    {'ticker': 'AMZN', 'marketValue': 54575.0, 'unrealizedPnl': 11350.0, 'sector': 'Consumer Discretionary', 'weight': 9.1},
]

RESEARCH_PAPERS = [
    {'id': 'paper-001', 'title': 'Momentum Crashes and Recovery: A Cross-Sectional Analysis',
     'authors': 'Barroso, P. & Santa-Clara, P.', 'journal': 'Journal of Financial Economics',
     'year': 2015, 'tags': ['momentum', 'crash_risk', 'risk_management'],
     'aiSummary': 'Momentum strategies exhibit negative skewness. Risk-managed momentum eliminates crashes while preserving alpha.',
     'implementationStatus': 'deployed', 'backtestedSharpe': 1.42},
    {'id': 'paper-002', 'title': 'Deep Learning for Stock Selection: A Factor-Based Approach',
     'authors': 'Gu, S., Kelly, B. & Xiu, D.', 'journal': 'Review of Financial Studies',
     'year': 2020, 'tags': ['deep_learning', 'factor_investing', 'cross_section'],
     'aiSummary': 'Neural networks significantly outperform linear models in predicting cross-sectional returns.',
     'implementationStatus': 'testing', 'backtestedSharpe': 1.67},
    {'id': 'paper-003', 'title': 'Buy-the-Dip: Optimal Timing and Risk-Adjusted Returns',
     'authors': 'Internal Research — QuantAlpha', 'journal': 'Internal Working Paper',
     'year': 2026, 'tags': ['buy_the_dip', 'vix', 'mean_reversion'],
     'aiSummary': 'RSI oversold + VIX term structure backwardation = 63% win rate.',
     'implementationStatus': 'deployed', 'backtestedSharpe': 1.55},
    {'id': 'paper-004', 'title': 'Accruals Quality as a Factor: Implications for Alpha Generation',
     'authors': 'Sloan, R.', 'journal': 'The Accounting Review',
     'year': 1996, 'tags': ['accruals', 'accounting_alpha', 'quality_factor'],
     'aiSummary': 'Firms with high accruals (earnings >> cash flow) underperform. Accruals quality is a persistent alpha source.',
     'implementationStatus': 'deployed', 'backtestedSharpe': 1.18},
]

AI_STRATEGIES = [
    {'id': 'ai-strat-001', 'name': 'Volatility Risk Premium Harvesting',
     'source': 'paper-001', 'status': 'deployed',
     'description': 'Sell OTM puts on S&P 500 during contango.',
     'expectedSharpe': 0.95, 'riskLevel': 'medium'},
    {'id': 'ai-strat-002', 'name': 'Cross-Sectional LSTM Ranker',
     'source': 'paper-002', 'status': 'testing',
     'description': 'Rank top 20 stocks weekly using LSTM scores.',
     'expectedSharpe': 1.50, 'riskLevel': 'high'},
    {'id': 'ai-strat-003', 'name': 'Multi-Signal BTD with Regime Filter',
     'source': 'paper-003', 'status': 'deployed',
     'description': 'RSI + VIX + HY OAS triple confirmation.',
     'expectedSharpe': 1.55, 'riskLevel': 'low'},
    {'id': 'ai-strat-004', 'name': 'Accounting Alpha — Accruals Factor',
     'source': 'paper-004', 'status': 'deployed',
     'description': 'Long low-accrual, short high-accrual firms. Quality factor.',
     'expectedSharpe': 1.18, 'riskLevel': 'low'},
]


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3008 — Performance v2.0 (Alpha Research Lab)',
        'port': 3008, 'version': '2.0.0',
        'architecture': '8-station-microservices',
        'mode': 'alpha_research_lab',
        'uptime_sec': round(time.time() - _start_time, 1),
        'portfolioSharpe': PERFORMANCE['sharpe'],
        'portfolioAlpha': PERFORMANCE['alpha'],
        'informationRatio': PERFORMANCE['informationRatio'],
        'researchPapers': len(RESEARCH_PAPERS),
        'features': ['brinson_attribution', 'factor_autocorrelation', 'alpha_decay', 'ic_rank_ic'],
        'endpoints': [
            'GET /api/trading/performance     — Full performance metrics',
            'GET /api/trading/risk            — VaR, CVaR, stress',
            'GET /api/trading/attribution     — Strategy + sector attribution',
            'GET /api/trading/brinson         — Brinson attribution (Alpha Research)',
            'GET /api/trading/factor-autocorr — Factor autocorrelation + turnover',
            'GET /api/trading/alpha-decay     — Alpha decay curves (5d/20d/60d)',
            'GET /api/trading/ic-analysis     — IC / Rank IC factor utility',
            'GET /api/research/papers         — Factor research library',
            'GET /api/research/ai-strategies  — AI strategies',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/trading/performance')
def trading_performance():
    return jsonify(PERFORMANCE)


@app.route('/api/trading/risk')
def trading_risk():
    total_mv = sum(p['marketValue'] for p in _POSITIONS)
    tech_mv = sum(p['marketValue'] for p in _POSITIONS if 'Tech' in p.get('sector', ''))
    return jsonify({
        'var95_daily': PERFORMANCE['var95'],
        'cvar95_daily': PERFORMANCE['cvar95'],
        'var95_monthly': round(PERFORMANCE['var95'] * math.sqrt(21), 2),
        'beta': PERFORMANCE['beta'],
        'sectorExposure': PERFORMANCE['sectorExposure'],
        'concentrationRisk': {
            'topHolding': {'ticker': 'NVDA', 'weight': 22.3},
            'top3Weight': 40.2, 'top5Weight': 56.7,
            'herfindahlIndex': 0.092,
        },
        'stressScenarios': {
            'market_crash_10pct': round(total_mv * -0.10 * PERFORMANCE['beta'], 0),
            'sector_rotation_5pct': round(tech_mv * -0.05, 0),
            'vix_spike_40': round(total_mv * -0.03, 0),
        },
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/trading/attribution')
def trading_attribution():
    return jsonify({
        'strategyAttribution': PERFORMANCE['strategyAttribution'],
        'sectorAttribution': {
            sec: {
                'weight': weight,
                'pnl': round(sum(p['unrealizedPnl'] for p in _POSITIONS if p['sector'] == sec), 2),
                'positionCount': sum(1 for p in _POSITIONS if p['sector'] == sec),
            }
            for sec, weight in PERFORMANCE['sectorExposure'].items() if sec != 'Cash'
        },
        'monthlyReturns': PERFORMANCE['monthlyReturns'],
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
    })


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Brinson Attribution
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/trading/brinson')
def trading_brinson():
    """Brinson attribution — decompose excess return into allocation vs selection."""
    return jsonify(BRINSON_ATTRIBUTION)


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Factor Autocorrelation
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/trading/factor-autocorr')
def trading_factor_autocorr():
    """Factor autocorrelation — measure signal stability and turnover."""
    factors = _generate_factor_autocorrelation()
    return jsonify({
        'explanation': 'Factor Autocorrelation measures how stable a factor signal is over time. High autocorrelation (>0.9) = stable signal, low turnover, lower transaction costs. Low autocorrelation (<0.5) = rapidly changing signal, high turnover, expensive to trade.',
        'columns': {
            'lag1': '1-day autocorrelation',
            'lag5': '1-week autocorrelation',
            'lag20': '1-month autocorrelation',
            'turnover': 'Monthly portfolio turnover if used as sort variable',
        },
        'factors': factors,
        'insight': 'Value factors (EV/EBITDA, FCF Yield) are very stable (autocorr >0.9). Momentum factors are moderately stable. Event signals (earnings, sentiment) are unstable — high turnover costs.',
    })


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Alpha Decay Curves
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/trading/alpha-decay')
def trading_alpha_decay():
    """Alpha decay analysis — how quickly each signal loses predictive power."""
    factors = _generate_alpha_decay()
    return jsonify({
        'explanation': 'Alpha Decay measures how a factor\'s Information Coefficient (IC) changes across different forward-return horizons. Fast-decaying signals require high-frequency rebalancing; slow-buildup signals suit monthly/quarterly strategies.',
        'horizonUnit': 'trading_days',
        'factors': factors,
        'insight': 'Earnings event signals decay in 3 days (ultra-fast). Momentum in 8 days (fast). Value builds over 45 days. Quality peaks at 30 days then slowly declines. Match rebalance frequency to each factor\'s half-life.',
    })


# ══════════════════════════════════════════════════════════════════════════════
# NEW: IC / Rank IC Analysis
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/trading/ic-analysis')
def trading_ic_analysis():
    """IC (Information Coefficient) and Rank IC analysis for factor utility."""
    random.seed(55)

    # Generate monthly IC/Rank IC over 3 years
    months = [f"{2023 + i//12}-{(i%12)+1:02d}" for i in range(36)]
    factors = {
        'ev_ebitda_percentile': {'avg_ic': 0.048, 'avg_rank_ic': 0.055, 'ic_ir': 0.62},
        'fcf_yield': {'avg_ic': 0.042, 'avg_rank_ic': 0.049, 'ic_ir': 0.55},
        'accruals_quality': {'avg_ic': 0.038, 'avg_rank_ic': 0.044, 'ic_ir': 0.51},
        'rsi14_signal': {'avg_ic': 0.065, 'avg_rank_ic': 0.071, 'ic_ir': 0.78},
        'operating_leverage': {'avg_ic': 0.031, 'avg_rank_ic': 0.036, 'ic_ir': 0.42},
        'sentiment_composite': {'avg_ic': 0.028, 'avg_rank_ic': 0.033, 'ic_ir': 0.38},
        'rd_intensity': {'avg_ic': 0.025, 'avg_rank_ic': 0.030, 'ic_ir': 0.34},
    }

    # Generate time series for each factor
    for fname, fdata in factors.items():
        monthly_ic = []
        for i in range(36):
            ic = fdata['avg_ic'] + random.gauss(0, fdata['avg_ic'] * 0.5)
            monthly_ic.append({'month': months[i], 'ic': round(ic, 4)})
        fdata['monthlySeries'] = monthly_ic
        fdata['positive_months'] = sum(1 for m in monthly_ic if m['ic'] > 0)
        fdata['hit_rate'] = round(fdata['positive_months'] / 36 * 100, 1)

    return jsonify({
        'explanation': 'IC (Information Coefficient) measures Pearson correlation between factor values and subsequent returns. Rank IC uses Spearman rank correlation (more robust to outliers). IC IR = IC / StdDev(IC) — higher means more consistent signal.',
        'benchmarks': {
            'good_ic': '>0.05',
            'excellent_ic': '>0.08',
            'good_ic_ir': '>0.5 (statistically significant)',
            'excellent_ic_ir': '>1.0 (institutional-grade)',
        },
        'months': months,
        'factors': factors,
        'insight': 'RSI signal has highest IC (0.065) but decays fast. Value factors (EV/EBITDA, FCF) have moderate IC but are more stable. Accruals quality shows promising IC_IR (0.51) — consistent alpha source.',
    })


# Research Library
@app.route('/api/research/status')
def research_status():
    return jsonify({
        'module': 'research', 'status': 'operational', 'port': 3008,
        'totalPapers': len(RESEARCH_PAPERS),
        'deployedStrategies': sum(1 for s in AI_STRATEGIES if s['status'] == 'deployed'),
    })


@app.route('/api/research/papers')
def research_papers():
    tag = request.args.get('tag', '')
    papers = RESEARCH_PAPERS
    if tag:
        papers = [p for p in papers if any(tag.lower() in t.lower() for t in p['tags'])]
    return jsonify({'total': len(papers), 'papers': papers})


@app.route('/api/research/ai-strategies')
def research_ai_strategies():
    status = request.args.get('status', '')
    strats = AI_STRATEGIES
    if status:
        strats = [s for s in strats if s['status'] == status]
    return jsonify({'total': len(strats), 'strategies': strats})


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3008 — Performance v2.0 (Alpha Research Lab)")
    print(f"  Sharpe: {PERFORMANCE['sharpe']} | IR: {PERFORMANCE['informationRatio']}")
    print("  Brinson Attribution | Factor Autocorrelation | Alpha Decay")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3008, debug=False)
