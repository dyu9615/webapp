"""
QuantAlpha Station 3007 — Trading (实盘管理)
═══════════════════════════════════════════════
Responsibilities:
  • Open position management (entries, exits, cost basis)
  • Order execution logging
  • Real-time P&L tracking per position
  • Stop-loss and target price monitoring

Data Flow:
  Station 3005 (ML signals) → Station 3007 (trade execution)
  Station 3006 (backtest strategies) → Station 3007 (strategy deployment)
  Station 3007 → trade data → Station 3008 (Performance attribution)
"""

import sys
import os
import time
import math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_start_time = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# Portfolio positions (from original trading_service)
# ══════════════════════════════════════════════════════════════════════════════
POSITIONS = [
    {'id': 'pos-001', 'ticker': 'NVDA', 'name': 'NVIDIA Corp',
     'side': 'LONG', 'quantity': 150, 'avgCost': 485.20,
     'currentPrice': 892.50, 'marketValue': 133875.0,
     'unrealizedPnl': 61095.0, 'unrealizedPnlPct': 83.97,
     'weight': 22.3, 'sector': 'Information Technology',
     'entryDate': '2024-06-15', 'strategy': 'ML-Enhanced Momentum',
     'stopLoss': 750.0, 'targetPrice': 1000.0},
    {'id': 'pos-002', 'ticker': 'AAPL', 'name': 'Apple Inc',
     'side': 'LONG', 'quantity': 200, 'avgCost': 178.50,
     'currentPrice': 227.80, 'marketValue': 45560.0,
     'unrealizedPnl': 9860.0, 'unrealizedPnlPct': 27.62,
     'weight': 7.6, 'sector': 'Information Technology',
     'entryDate': '2024-08-20', 'strategy': 'Buy-the-Dip Core',
     'stopLoss': 200.0, 'targetPrice': 260.0},
    {'id': 'pos-003', 'ticker': 'GOOGL', 'name': 'Alphabet Inc',
     'side': 'LONG', 'quantity': 300, 'avgCost': 142.30,
     'currentPrice': 178.90, 'marketValue': 53670.0,
     'unrealizedPnl': 10980.0, 'unrealizedPnlPct': 25.72,
     'weight': 8.9, 'sector': 'Communication Services',
     'entryDate': '2024-10-05', 'strategy': 'Earnings Catalyst',
     'stopLoss': 155.0, 'targetPrice': 210.0},
    {'id': 'pos-004', 'ticker': 'META', 'name': 'Meta Platforms',
     'side': 'LONG', 'quantity': 80, 'avgCost': 355.40,
     'currentPrice': 612.80, 'marketValue': 49024.0,
     'unrealizedPnl': 20592.0, 'unrealizedPnlPct': 72.44,
     'weight': 8.2, 'sector': 'Communication Services',
     'entryDate': '2024-04-10', 'strategy': 'ML-Enhanced Momentum',
     'stopLoss': 520.0, 'targetPrice': 700.0},
    {'id': 'pos-005', 'ticker': 'MSFT', 'name': 'Microsoft Corp',
     'side': 'LONG', 'quantity': 120, 'avgCost': 380.60,
     'currentPrice': 428.50, 'marketValue': 51420.0,
     'unrealizedPnl': 5748.0, 'unrealizedPnlPct': 12.58,
     'weight': 8.6, 'sector': 'Information Technology',
     'entryDate': '2025-01-08', 'strategy': 'Buy-the-Dip Core',
     'stopLoss': 395.0, 'targetPrice': 480.0},
    {'id': 'pos-006', 'ticker': 'AMZN', 'name': 'Amazon.com Inc',
     'side': 'LONG', 'quantity': 250, 'avgCost': 172.90,
     'currentPrice': 218.30, 'marketValue': 54575.0,
     'unrealizedPnl': 11350.0, 'unrealizedPnlPct': 26.26,
     'weight': 9.1, 'sector': 'Consumer Discretionary',
     'entryDate': '2024-09-12', 'strategy': 'Contrarian VIX Spike',
     'stopLoss': 190.0, 'targetPrice': 250.0},
]

TRADE_LOG = [
    {'id': 'trd-101', 'ticker': 'AMD', 'side': 'LONG', 'quantity': 200,
     'entryPrice': 145.80, 'exitPrice': 168.20, 'entryDate': '2025-01-15', 'exitDate': '2025-02-10',
     'pnlDollar': 4480.0, 'pnlPct': 15.36, 'strategy': 'ML-Enhanced Momentum', 'exitReason': 'target_hit'},
    {'id': 'trd-102', 'ticker': 'TSLA', 'side': 'LONG', 'quantity': 50,
     'entryPrice': 242.50, 'exitPrice': 218.30, 'entryDate': '2025-02-01', 'exitDate': '2025-02-08',
     'pnlDollar': -1210.0, 'pnlPct': -9.98, 'strategy': 'Contrarian VIX Spike', 'exitReason': 'stop_loss'},
    {'id': 'trd-103', 'ticker': 'CRM', 'side': 'LONG', 'quantity': 100,
     'entryPrice': 275.40, 'exitPrice': 312.80, 'entryDate': '2025-01-20', 'exitDate': '2025-02-18',
     'pnlDollar': 3740.0, 'pnlPct': 13.58, 'strategy': 'Earnings Catalyst', 'exitReason': 'target_hit'},
    {'id': 'trd-104', 'ticker': 'NFLX', 'side': 'LONG', 'quantity': 30,
     'entryPrice': 685.00, 'exitPrice': 742.50, 'entryDate': '2025-02-05', 'exitDate': '2025-02-25',
     'pnlDollar': 1725.0, 'pnlPct': 8.39, 'strategy': 'Buy-the-Dip Core', 'exitReason': 'trailing_stop'},
]

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    total_mv = sum(p['marketValue'] for p in POSITIONS)
    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3007 — Trading (实盘管理)',
        'port': 3007,
        'version': '1.0.0',
        'architecture': '8-station-microservices',
        'uptime_sec': round(time.time() - _start_time, 1),
        'openPositions': len(POSITIONS),
        'totalMarketValue': total_mv,
        'recentTrades': len(TRADE_LOG),
        'endpoints': [
            'GET /api/trading/positions  — Open positions',
            'GET /api/trading/trades     — Recent trade log',
            'GET /api/trading/status     — Module status',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/trading/status')
def trading_status():
    return jsonify({
        'module': 'trading', 'status': 'operational', 'port': 3007,
        'openPositions': len(POSITIONS),
        'recentTrades': len(TRADE_LOG),
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/trading/positions')
def trading_positions():
    return jsonify({
        'total': len(POSITIONS),
        'totalMarketValue': sum(p['marketValue'] for p in POSITIONS),
        'totalUnrealizedPnl': sum(p['unrealizedPnl'] for p in POSITIONS),
        'positions': POSITIONS,
    })


@app.route('/api/trading/trades')
def trading_trades():
    strategy = request.args.get('strategy', '')
    trades = TRADE_LOG
    if strategy:
        trades = [t for t in trades if t['strategy'] == strategy]
    return jsonify({'total': len(trades), 'trades': trades})


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3007 — Trading (实盘管理)")
    print(f"  Positions: {len(POSITIONS)} | Trades: {len(TRADE_LOG)}")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3007, debug=False)
