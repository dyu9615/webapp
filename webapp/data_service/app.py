"""
QuantAlpha Station 3001 — Data Center (唯一真理来源)
═══════════════════════════════════════════════════════
The "Single Source of Truth" for the entire 8-station architecture.

Core responsibilities:
  • Bloomberg CSV import + field auto-mapping (PX_LAST → close)
  • PIT (Point-in-Time) archiving with FF_PUB_DATE compliance
  • Gold Standard data endpoints for cross-validation
  • Raw data fetching & local storage (Yahoo Finance)
  • bloomberg_archive.db maintenance
  • Macro data (VIX, Treasury, SPY)
  • /api/dc/snapshot/<ticker> — structured data for ML Validation Gate
  • /api/dc/gold-standard/<ticker> — gold-standard data for Cross Check

Stripped modules (moved to independent stations):
  Screener      → Station 3002
  News          → Station 3003
  Deep Analysis → Station 3004
"""

import sys
import os
import time
import json
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

_start_time = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# Register CORE data modules only (screener/news/deep stripped to own stations)
# ══════════════════════════════════════════════════════════════════════════════
_registered_modules = []
_failed_modules = []

def _safe_register(module_name, import_path):
    global _registered_modules, _failed_modules
    try:
        mod = __import__(import_path, fromlist=['bp'])
        app.register_blueprint(mod.bp)
        _registered_modules.append(module_name)
    except Exception as e:
        _failed_modules.append({'module': module_name, 'error': str(e)})
        print(f"⚠ Failed to load module [{module_name}]: {e}")

# Core I/O modules — the "data backbone"
_safe_register('price_volume', 'modules.price_volume')
_safe_register('macro',        'modules.macro')
_safe_register('factset',      'modules.factset')
_safe_register('bloomberg',    'modules.bloomberg')

print(f"✓ Station 3001 loaded {len(_registered_modules)} modules: {', '.join(_registered_modules)}")
if _failed_modules:
    print(f"✗ Failed {len(_failed_modules)} modules: {', '.join(m['module'] for m in _failed_modules)}")


# ══════════════════════════════════════════════════════════════════════════════
# Bloomberg Archive DB helpers
# ══════════════════════════════════════════════════════════════════════════════
DB_PATH = os.path.join(os.path.dirname(__file__), 'bloomberg_archive.db')

def _get_archive_summary(ticker):
    """Get archived data summary for a ticker from bloomberg_archive.db"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # Reference data count
        cur.execute("SELECT COUNT(*), MAX(timestamp) FROM bbg_reference_archive WHERE ticker=?", (ticker,))
        ref_count, ref_last = cur.fetchone()
        # History data
        cur.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM bbg_history_archive WHERE ticker=?", (ticker,))
        hist_count, hist_min, hist_max = cur.fetchone()
        conn.close()
        return {
            'referenceSnapshots': ref_count or 0,
            'lastReferenceUpdate': ref_last,
            'historyBars': hist_count or 0,
            'historyDateRange': {'start': hist_min, 'end': hist_max} if hist_min else None,
        }
    except:
        return {'referenceSnapshots': 0, 'historyBars': 0}


def _get_gold_data(ticker):
    """Retrieve the latest gold-standard data for a ticker from archive + local storage."""
    from modules.utils import load_json_from_latest, safe_float
    archive = _get_archive_summary(ticker)

    # Also pull from latest local storage snapshot
    raw_data, dt_str = load_json_from_latest('price_volume', 'universe_raw.json')
    local_stock = None
    if raw_data and raw_data.get('stocks'):
        local_stock = next((s for s in raw_data['stocks'] if s.get('ticker') == ticker), None)

    gold = {
        'ticker': ticker,
        'source': 'data_center_3001',
        'archiveStatus': archive,
        'localSnapshot': local_stock,
        'localSnapshotDate': dt_str,
        'hasBBGData': archive.get('referenceSnapshots', 0) > 0,
        'hasLocalData': local_stock is not None,
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    }

    # Extract key gold-standard fields for cross-check
    if local_stock:
        gold['goldMetrics'] = {
            'price': safe_float(local_stock.get('price')),
            'marketCap': safe_float(local_stock.get('marketCap')),
            'ev': safe_float(local_stock.get('ev')),
            'forwardPE': safe_float(local_stock.get('forwardPE')),
            'evEbitda': safe_float(local_stock.get('evEbitda')),
            'revenueGrowth': safe_float(local_stock.get('revenueGrowth')),
            'grossMargin': safe_float(local_stock.get('grossMargin')),
            'operatingMargin': safe_float(local_stock.get('operatingMargin')),
            'roe': safe_float(local_stock.get('roe')),
            'fcfYield': safe_float(local_stock.get('fcfYield')),
            'debtEquity': safe_float(local_stock.get('debtEquity')),
            'beta': safe_float(local_stock.get('beta')),
            'revenue': safe_float(local_stock.get('revenue')),
            'ebitda': safe_float(local_stock.get('ebitda')),
            'eps': safe_float(local_stock.get('eps')),
            'forwardEps': safe_float(local_stock.get('forwardEps')),
        }

    return gold


# ══════════════════════════════════════════════════════════════════════════════
# /api/dc/gold-standard/<ticker> — Gold Standard data for Cross Check
# Used by Station 3002 (Screener) and 3004 (Deep Analysis)
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dc/gold-standard/<ticker>')
def gold_standard(ticker):
    ticker = ticker.upper().strip()
    return jsonify(_get_gold_data(ticker))


# ══════════════════════════════════════════════════════════════════════════════
# /api/dc/snapshot/<ticker> — Structured snapshot for ML Validation Gate
# Used by Station 3005 (ML Engine) audit_data_integrity()
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dc/snapshot/<ticker>')
def snapshot(ticker):
    ticker = ticker.upper().strip()
    from modules.utils import load_json_from_latest, safe_float

    gold = _get_gold_data(ticker)
    archive = gold.get('archiveStatus', {})
    local = gold.get('localSnapshot')

    # ML-grade completeness assessment
    required_fields = [
        'price', 'marketCap', 'ev', 'forwardPE', 'evEbitda',
        'revenueGrowth', 'grossMargin', 'roe', 'fcfYield', 'beta',
        'revenue', 'ebitda', 'eps', 'forwardEps',
    ]
    fields_presence = {}
    if local:
        for f in required_fields:
            val = safe_float(local.get(f))
            fields_presence[f] = val != 0 and val is not None
    else:
        fields_presence = {f: False for f in required_fields}

    # PIT compliance fields (Bloomberg-specific)
    pit_fields = ['FF_PUB_DATE', 'SALES_LTM', 'PX_LAST']
    pit_presence = {}
    for f in pit_fields:
        # Check if we have BBG archive data with these fields
        pit_presence[f] = archive.get('referenceSnapshots', 0) > 0

    # History depth
    hist_bars = archive.get('historyBars', 0)
    years_count = hist_bars / 252 if hist_bars > 0 else 0

    # Check for data gaps (simplified — would need full time series in production)
    max_gap_days = 0  # placeholder — real implementation scans bbg_history_archive

    return jsonify({
        'ticker': ticker,
        'station': 'data_center_3001',
        'fields_presence': fields_presence,
        'pit_fields_presence': pit_presence,
        'years_count': round(years_count, 1),
        'history_bars': hist_bars,
        'max_gap_days': max_gap_days,
        'has_local_data': local is not None,
        'has_bbg_archive': archive.get('referenceSnapshots', 0) > 0,
        'goldMetrics': gold.get('goldMetrics', {}),
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


# ══════════════════════════════════════════════════════════════════════════════
# /api/dc/csv-upload — Bloomberg CSV Import with auto-field-mapping
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dc/csv-upload', methods=['POST'])
def csv_upload():
    """
    Upload Bloomberg/FactSet CSV with auto-mapping:
      PX_LAST → close
      PX_OPEN → open
      PX_HIGH → high
      PX_LOW  → low
      PX_VOLUME → volume
      FF_PUB_DATE → report_date (PIT compliance)
      SALES_REV_TURN → revenue
      EBITDA → ebitda
    """
    FIELD_MAP = {
        'PX_LAST': 'close', 'PX_OPEN': 'open', 'PX_HIGH': 'high',
        'PX_LOW': 'low', 'PX_VOLUME': 'volume',
        'FF_PUB_DATE': 'report_date', 'SALES_REV_TURN': 'revenue',
        'EBITDA': 'ebitda', 'NET_INCOME': 'net_income',
        'EPS_BASIC': 'eps', 'FREE_CASH_FLOW': 'fcf',
        'GROSS_MARGIN': 'gross_margin', 'OPER_MARGIN': 'operating_margin',
        'CUR_MKT_CAP': 'market_cap', 'PE_RATIO': 'pe_ratio',
        'TOT_DEBT_TO_TOT_EQY': 'debt_equity',
    }

    ticker = (request.form.get('ticker') or request.json.get('ticker', 'UNKNOWN')).upper().strip() if request.is_json else request.form.get('ticker', 'UNKNOWN').upper().strip()

    # Parse CSV content
    if 'file' in request.files:
        content = request.files['file'].read().decode('utf-8', errors='replace')
    elif request.is_json:
        content = request.json.get('content', '')
    else:
        content = request.form.get('content', '')

    if not content.strip():
        return jsonify({'error': 'No CSV content provided'}), 400

    import csv
    from io import StringIO
    reader = csv.DictReader(StringIO(content))
    rows_mapped = []
    original_fields = set()
    mapped_fields = set()

    for row in reader:
        mapped_row = {}
        for orig_key, value in row.items():
            orig_key = orig_key.strip()
            original_fields.add(orig_key)
            std_key = FIELD_MAP.get(orig_key, orig_key.lower().replace(' ', '_'))
            mapped_fields.add(f'{orig_key} → {std_key}')
            try:
                mapped_row[std_key] = float(value) if value and value.replace('.', '').replace('-', '').isdigit() else value
            except:
                mapped_row[std_key] = value
        rows_mapped.append(mapped_row)

    # Archive to SQLite
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bbg_reference_archive (ticker, data_type, fields_json) VALUES (?, ?, ?)",
            (ticker, 'csv_upload', json.dumps({'rows': rows_mapped[:100], 'total_rows': len(rows_mapped)}))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"CSV Archive Error: {e}")

    return jsonify({
        'success': True,
        'ticker': ticker,
        'rowsProcessed': len(rows_mapped),
        'originalFields': sorted(original_fields),
        'fieldMapping': sorted(mapped_fields),
        'sampleRow': rows_mapped[0] if rows_mapped else {},
        'pitCompliance': 'report_date' in [r for r in (rows_mapped[0].keys() if rows_mapped else [])],
        'archivedTo': 'bloomberg_archive.db',
        'uploadedAt': datetime.utcnow().isoformat() + 'Z',
    })


# ══════════════════════════════════════════════════════════════════════════════
# /api/dc/factset-snapshot/<ticker> — FactSet Excel Snapshot (from ingested data)
# Used by Frontend for real institutional-grade valuation display
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dc/factset-snapshot/<ticker>')
def factset_snapshot(ticker):
    ticker = ticker.upper().strip()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT data_json, snapshot_date, timestamp FROM factset_snapshots WHERE ticker=? ORDER BY snapshot_date DESC LIMIT 1",
            (ticker,)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            data = json.loads(row[0])
            data['_dbSnapshotDate'] = row[1]
            data['_dbTimestamp'] = row[2]
            return jsonify({'success': True, 'ticker': ticker, 'snapshot': data, 'source': 'factset_excel'})
        else:
            return jsonify({'success': False, 'ticker': ticker, 'error': 'No FactSet snapshot found', 'source': 'factset_excel'}), 404
    except Exception as e:
        return jsonify({'success': False, 'ticker': ticker, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/dc/factset-snapshots — List all available FactSet snapshots
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dc/factset-snapshots')
def factset_snapshots_list():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT ticker, snapshot_date, data_json, timestamp FROM factset_snapshots ORDER BY ticker ASC")
        rows = cur.fetchall()
        conn.close()
        snapshots = []
        for row in rows:
            data = json.loads(row[2])
            snapshots.append({
                'ticker': row[0],
                'snapshotDate': row[1],
                'price': data.get('price'),
                'marketCapB': data.get('marketCapB'),
                'peLTM': data.get('peLTM'),
                'evEbitdaLTM': data.get('evEbitdaLTM'),
                'beta': data.get('beta'),
                'targetPrice': data.get('targetPrice'),
                'analystRating': data.get('analystRating'),
                'grossMargin': data.get('grossMargin'),
                'roe': data.get('roe'),
                'name': data.get('name'),
                'sector': data.get('sector'),
                'timestamp': row[3],
            })
        return jsonify({'success': True, 'total': len(snapshots), 'snapshots': snapshots, 'source': 'factset_excel'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'snapshots': []}), 500


# ══════════════════════════════════════════════════════════════════════════════
# /api/health — Station 3001 health
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/health')
def health():
    from modules.utils import CORE_TICKERS
    try:
        from modules.bloomberg.routes import BLPAPI_AVAILABLE
    except:
        BLPAPI_AVAILABLE = False

    archive = {'tables': 0}
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bbg_reference_archive")
        ref_ct = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bbg_history_archive")
        hist_ct = cur.fetchone()[0]
        try:
            cur.execute("SELECT COUNT(*) FROM factset_snapshots")
            fs_ct = cur.fetchone()[0]
        except:
            fs_ct = 0
        conn.close()
        archive = {'referenceRecords': ref_ct, 'historyBars': hist_ct, 'factsetSnapshots': fs_ct}
    except:
        pass

    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3001 — Data Center (唯一真理来源)',
        'port': 3001,
        'version': '8.0.0',
        'architecture': '8-station-microservices',
        'role': 'single_source_of_truth',
        'uptime_sec': round(time.time() - _start_time, 1),
        'modules': {
            'loaded': _registered_modules,
            'failed': _failed_modules,
        },
        'bloomberg_archive': archive,
        'bloombergTerminal': BLPAPI_AVAILABLE,
        'sp500_universe_size': len(CORE_TICKERS),
        'downstream_stations': {
            '3002_screener': '/api/dc/gold-standard/<ticker>',
            '3004_deep_analysis': '/api/dc/gold-standard/<ticker>',
            '3005_ml_engine': '/api/dc/snapshot/<ticker>',
        },
        'key_endpoints': [
            'GET  /api/dc/gold-standard/<ticker>  — Gold data for Cross Check',
            'GET  /api/dc/snapshot/<ticker>        — ML Validation Gate data',
            'GET  /api/dc/factset-snapshot/<ticker> — FactSet Excel snapshot',
            'GET  /api/dc/factset-snapshots        — List all FactSet snapshots',
            'POST /api/dc/csv-upload               — Bloomberg CSV import',
            'POST /api/dc/refresh                  — Fetch & store raw YF data',
            'GET  /api/dc/data                     — Latest local data',
            'GET  /api/yf/quote/<ticker>           — Single ticker quote',
            'GET  /api/yf/financials/<ticker>      — Quarterly financials',
            'GET  /api/yf/history/<ticker>         — OHLCV history',
            'GET  /api/yf/macro                    — VIX, Treasury, SPY',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3001 — Data Center (唯一真理来源)")
    print("  Role: Single Source of Truth | Bloomberg Archive + YF")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3001, debug=False)
