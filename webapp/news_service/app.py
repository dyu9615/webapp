"""
QuantAlpha Station 3003 — News Station (情报中心) v2.0
═══════════════════════════════════════════════════════
Alpha Research Lab Mode:
  • Multi-source RSS news aggregation (Yahoo Finance, Google News)
  • Per-ticker and market-wide news scanning
  • Keyword-based NLP Sentiment Scoring (bullish/bearish/neutral)
  • FactSet PDF Upload + AI Summary (字数限制)
  • Full Web Search with Sentiment Analysis
  • Historical sentiment storage for factor research
  • Investment mandate filtering

Data Flow:
  RSS Feeds → Station 3003 → Sentiment Analysis → Station 3004, 3005
  FactSet PDF → Station 3003 → AI Summary → Frontend
"""

import sys
import os
import time
import re
import json
import sqlite3
import xml.etree.ElementTree as ET
import urllib.request
from datetime import datetime

from flask import Flask, jsonify, request as flask_request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_start_time = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# Database Setup for PDF storage and sentiment history
# ══════════════════════════════════════════════════════════════════════════════
DB_PATH = os.path.join(os.path.dirname(__file__), 'news_archive.db')

def _init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS factset_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT, upload_date TEXT, report_date TEXT,
        file_size INTEGER, content_text TEXT, ai_summary TEXT,
        market_synopsis TEXT, notable_gainers TEXT, notable_decliners TEXT,
        tickers_mentioned TEXT, sentiment_score REAL, sentiment_label TEXT,
        uploaded_at TEXT DEFAULT (datetime('now'))
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS sentiment_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT, score REAL, label TEXT, headline_count INTEGER,
        bullish_count INTEGER, bearish_count INTEGER, neutral_count INTEGER,
        source TEXT, recorded_at TEXT DEFAULT (datetime('now'))
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS web_search_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT, results_json TEXT, sentiment_score REAL,
        sentiment_label TEXT, searched_at TEXT DEFAULT (datetime('now'))
    )''')
    conn.commit()
    conn.close()

_init_db()


# ══════════════════════════════════════════════════════════════════════════════
# Enhanced Keyword-based NLP Sentiment Engine (FinBERT-style keywords)
# ══════════════════════════════════════════════════════════════════════════════
BULLISH_KEYWORDS = [
    'beat', 'beats', 'surpass', 'exceeded', 'upgrade', 'upgraded', 'outperform',
    'strong', 'growth', 'surge', 'surges', 'rally', 'rallies', 'soar', 'soars',
    'bullish', 'record high', 'all-time high', 'breakout', 'momentum',
    'buy', 'overweight', 'positive', 'optimistic', 'profit', 'expansion',
    'accelerate', 'upside', 'catalyst', 'opportunity', 'raised guidance',
    'buyback', 'share repurchase', 'dividend increase', 'revenue beat',
    'earnings beat', 'margin expansion', 'new high', 'outperformance',
    'top pick', 'initiated buy', 'price target raised', 'acceleration',
]
BEARISH_KEYWORDS = [
    'miss', 'misses', 'decline', 'declined', 'downgrade', 'downgraded', 'underperform',
    'weak', 'weakness', 'slump', 'crash', 'plunge', 'sell-off', 'selloff',
    'bearish', 'recession', 'risk', 'warning', 'cut', 'layoff', 'layoffs',
    'sell', 'underweight', 'negative', 'pessimistic', 'loss', 'contraction',
    'decelerate', 'downside', 'threat', 'headwind', 'lowered guidance',
    'margin compression', 'revenue miss', 'earnings miss', 'guidance cut',
    'price target cut', 'initiated sell', 'bankruptcy', 'default risk',
    'tariff', 'trade war', 'sanctions', 'inflation fear', 'rate hike',
]

def _score_sentiment(text):
    """Score sentiment from -1.0 (very bearish) to +1.0 (very bullish)."""
    if not text:
        return 0.0, 'neutral'
    text_lower = text.lower()
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    total = bull_count + bear_count
    if total == 0:
        return 0.0, 'neutral'
    score = (bull_count - bear_count) / total
    label = 'bullish' if score > 0.15 else 'bearish' if score < -0.15 else 'neutral'
    return round(score, 3), label


def _parse_rss(url, source_name, ticker=None, limit=10):
    """Parse RSS feed and return enriched headline dicts with sentiment."""
    headlines = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            root = ET.fromstring(r.read())
        channel = root.find('channel') or root
        for item in channel.findall('item')[:limit]:
            title = (item.findtext('title') or '').strip()
            link = item.findtext('link') or ''
            pub = item.findtext('pubDate') or ''
            desc = (item.findtext('description') or '')[:300]
            if title:
                combined_text = f'{title} {desc}'
                score, label = _score_sentiment(combined_text)
                headlines.append({
                    'title': title, 'url': link, 'time': pub,
                    'description': desc[:200], 'source': source_name,
                    'tickers': [ticker] if ticker else [],
                    'sentimentScore': score, 'sentiment': label,
                })
    except Exception as e:
        print(f"RSS parse error ({source_name}): {e}")
    return headlines


def _extract_pdf_text(file_content):
    """Extract text from PDF binary content."""
    try:
        from PyPDF2 import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(file_content))
        text = ''
        for page in reader.pages:
            text += page.extract_text() or ''
        return text
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return ''


def _generate_factset_summary(text, max_words=300):
    """Generate a structured summary from FactSet report text (rule-based)."""
    if not text:
        return {'summary': 'No text extracted', 'sections': {}}

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Extract market synopsis
    synopsis = ''
    for i, line in enumerate(lines):
        if 'market synopsis' in line.lower() or 'us equities' in line.lower():
            synopsis = ' '.join(lines[i:i+5])[:500]
            break

    # Extract gainers/decliners
    gainers = []
    decliners = []
    for line in lines:
        if any(kw in line.lower() for kw in ['notable gainer', 'price gainer', 'outperform']):
            gainers.append(line[:200])
        if any(kw in line.lower() for kw in ['notable decliner', 'price loser', 'underperform']):
            decliners.append(line[:200])

    # Extract tickers (pattern: ~XXXX-US~ or just AAPL, NVDA etc)
    ticker_pattern = re.compile(r'~([A-Z]{1,5})-US~|(?<![a-z])([A-Z]{2,5})(?=\s+[\+\-\$\d])')
    found_tickers = set()
    for match in ticker_pattern.finditer(text):
        t = match.group(1) or match.group(2)
        if t and len(t) <= 5 and t not in {'THE', 'AND', 'FOR', 'WITH', 'THIS', 'THAT', 'FROM', 'NEW', 'USD', 'EST', 'EDT', 'PST', 'ISM', 'ADP', 'GDP', 'CPI', 'FED', 'SEC'}:
            found_tickers.add(t)

    # Overall sentiment
    overall_score, overall_label = _score_sentiment(text[:3000])

    # Generate concise summary (max_words)
    summary_parts = []
    if synopsis:
        summary_parts.append(f"📊 市场概况: {synopsis[:300]}")
    if gainers:
        summary_parts.append(f"📈 领涨: {'; '.join(gainers[:3])}")
    if decliners:
        summary_parts.append(f"📉 领跌: {'; '.join(decliners[:3])}")
    summary_parts.append(f"🎯 情绪: {overall_label.upper()} (score: {overall_score})")
    summary_parts.append(f"📋 提及股票: {', '.join(sorted(found_tickers)[:20])}")

    full_summary = '\n'.join(summary_parts)
    # Truncate to max_words
    words = full_summary.split()
    if len(words) > max_words:
        full_summary = ' '.join(words[:max_words]) + '...'

    return {
        'summary': full_summary,
        'marketSynopsis': synopsis[:500],
        'notableGainers': gainers[:5],
        'notableDecliners': decliners[:5],
        'tickersMentioned': sorted(found_tickers),
        'tickerCount': len(found_tickers),
        'overallSentiment': {'score': overall_score, 'label': overall_label},
        'wordCount': len(full_summary.split()),
    }


def _save_sentiment_history(ticker, score, label, headline_count, bullish, bearish, neutral, source='rss'):
    """Store sentiment data for historical backtesting."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO sentiment_history
            (ticker, score, label, headline_count, bullish_count, bearish_count, neutral_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (ticker, score, label, headline_count, bullish, bearish, neutral, source))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Sentiment save error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    # Count stored reports
    report_count = 0
    sentiment_count = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM factset_reports")
        report_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sentiment_history")
        sentiment_count = cur.fetchone()[0]
        conn.close()
    except:
        pass

    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3003 — News Station v2.0 (情报中心 + Alpha Research)',
        'port': 3003,
        'version': '2.0.0',
        'architecture': '8-station-microservices',
        'mode': 'alpha_research_lab',
        'uptime_sec': round(time.time() - _start_time, 1),
        'storage': {
            'factsetReports': report_count,
            'sentimentRecords': sentiment_count,
            'database': 'news_archive.db',
        },
        'nlp': {
            'engine': 'enhanced_keyword_based (FinBERT-style)',
            'bullishKeywords': len(BULLISH_KEYWORDS),
            'bearishKeywords': len(BEARISH_KEYWORDS),
        },
        'endpoints': [
            'GET  /api/news/scan/<ticker>       — Ticker-specific news + sentiment',
            'GET  /api/news/live                — Market-wide news feed',
            'GET  /api/news/ticker/<ticker>     — Alias for scan',
            'GET  /api/news/sentiment/<ticker>  — Pure sentiment score',
            'GET  /api/news/headlines           — FactSet-compatible headlines',
            'POST /api/news/factset/upload      — Upload FactSet PDF report',
            'GET  /api/news/factset/reports     — List stored FactSet reports',
            'GET  /api/news/factset/report/<id> — Get specific report + summary',
            'GET  /api/news/sentiment-history   — Historical sentiment data',
            'POST /api/news/web-search          — Web search with sentiment scoring',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/news/scan/<ticker>')
def news_scan(ticker):
    """Authoritative news scan with sentiment analysis for a specific ticker."""
    ticker = ticker.upper().strip()
    headlines = _parse_rss(
        f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US',
        'Yahoo Finance', ticker
    )
    # Also try Google News
    google_headlines = _parse_rss(
        f'https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en',
        'Google News', ticker, limit=5
    )
    headlines.extend(google_headlines)

    # Aggregate sentiment
    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    bull_ct = sum(1 for h in headlines if h['sentiment'] == 'bullish')
    bear_ct = sum(1 for h in headlines if h['sentiment'] == 'bearish')
    neut_ct = sum(1 for h in headlines if h['sentiment'] == 'neutral')
    agg_label = 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral'

    # Save to history
    _save_sentiment_history(ticker, avg_score, agg_label, len(headlines), bull_ct, bear_ct, neut_ct)

    return jsonify({
        'ticker': ticker,
        'headlines': headlines,
        'count': len(headlines),
        'aggregateSentiment': {
            'score': avg_score, 'label': agg_label,
            'bullishCount': bull_ct, 'bearishCount': bear_ct, 'neutralCount': neut_ct,
        },
        'dataSource': 'yahoo_finance_rss + google_news',
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/news/ticker/<ticker>')
def news_ticker(ticker):
    return news_scan(ticker)


@app.route('/api/news/live')
def news_live():
    """Market-wide live news aggregation with sentiment analysis."""
    category = flask_request.args.get('category', 'all')
    limit = int(flask_request.args.get('limit', 50))

    headlines = []
    headlines.extend(_parse_rss(
        'https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US',
        'Yahoo Finance Markets', limit=15
    ))
    headlines.extend(_parse_rss(
        'https://news.google.com/rss/search?q=stock+market+today&hl=en-US&gl=US&ceid=US:en',
        'Google News Markets', limit=10
    ))

    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0

    return jsonify({
        'category': category,
        'articles': headlines[:limit],
        'count': min(len(headlines), limit),
        'marketSentiment': {
            'score': avg_score,
            'label': 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral',
        },
        'dataSource': 'rss_aggregator (Yahoo Finance + Google News)',
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/news/sentiment/<ticker>')
def news_sentiment(ticker):
    """Pure sentiment score endpoint — consumed by Station 3004 and 3005."""
    ticker = ticker.upper().strip()
    headlines = _parse_rss(
        f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US',
        'Yahoo Finance', ticker, limit=15
    )
    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    bull_ct = sum(1 for h in headlines if h['sentiment'] == 'bullish')
    bear_ct = sum(1 for h in headlines if h['sentiment'] == 'bearish')
    neut_ct = sum(1 for h in headlines if h['sentiment'] == 'neutral')

    return jsonify({
        'ticker': ticker,
        'sentimentScore': avg_score,
        'sentimentLabel': 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral',
        'headlineCount': len(headlines),
        'bullish': bull_ct, 'bearish': bear_ct, 'neutral': neut_ct,
        'station': 'news_3003',
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/news/headlines')
def news_headlines():
    """FactSet-compatible headlines endpoint."""
    ticker = flask_request.args.get('ticker', '').upper().strip()
    category = flask_request.args.get('category', 'all')
    limit = int(flask_request.args.get('limit', 20))

    headlines = []
    if ticker:
        headlines.extend(_parse_rss(
            f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US',
            'Yahoo Finance', ticker
        ))
    headlines.extend(_parse_rss(
        'https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US',
        'Yahoo Finance Markets'
    ))

    return jsonify({
        'ticker': ticker or 'ALL', 'category': category,
        'headlines': headlines[:limit], 'count': min(len(headlines), limit),
        'sources': ['Yahoo Finance', 'Google News'],
        'dataSource': 'rss_aggregator_with_sentiment',
        'lastUpdated': datetime.utcnow().isoformat() + 'Z',
    })


# ══════════════════════════════════════════════════════════════════════════════
# NEW: FactSet PDF Upload, Storage, and AI Summary
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/news/factset/upload', methods=['POST'])
def factset_upload():
    """Upload FactSet PDF report, extract text, generate AI summary."""
    max_words = int(flask_request.args.get('max_words', 300))

    if 'file' in flask_request.files:
        f = flask_request.files['file']
        filename = f.filename or 'unknown.pdf'
        content = f.read()
    elif flask_request.is_json:
        # Accept base64 or file path
        data = flask_request.get_json()
        file_path = data.get('filePath', '')
        if file_path and os.path.exists(file_path):
            with open(file_path, 'rb') as fp:
                content = fp.read()
            filename = os.path.basename(file_path)
        else:
            return jsonify({'error': 'No file provided'}), 400
    else:
        return jsonify({'error': 'No file provided. Use multipart/form-data or JSON with filePath'}), 400

    # Extract text
    text = _extract_pdf_text(content)
    if not text:
        return jsonify({'error': 'Could not extract text from PDF'}), 400

    # Generate summary
    summary_data = _generate_factset_summary(text, max_words=max_words)

    # Extract report date from filename or content
    date_match = re.search(r'(Mar|Feb|Jan|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2}),?\s*(20\d{2})', filename + ' ' + text[:500])
    report_date = f"{date_match.group(3)}-{date_match.group(1)}-{date_match.group(2)}" if date_match else datetime.utcnow().strftime('%Y-%m-%d')

    # Store in database
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO factset_reports
            (filename, upload_date, report_date, file_size, content_text, ai_summary,
             market_synopsis, notable_gainers, notable_decliners, tickers_mentioned,
             sentiment_score, sentiment_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (filename, datetime.utcnow().isoformat(), report_date, len(content),
             text[:50000],  # Store first 50k chars
             json.dumps(summary_data),
             summary_data.get('marketSynopsis', ''),
             json.dumps(summary_data.get('notableGainers', [])),
             json.dumps(summary_data.get('notableDecliners', [])),
             json.dumps(summary_data.get('tickersMentioned', [])),
             summary_data['overallSentiment']['score'],
             summary_data['overallSentiment']['label']))
        report_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'Database error: {e}'}), 500

    return jsonify({
        'success': True,
        'reportId': report_id,
        'filename': filename,
        'reportDate': report_date,
        'fileSize': len(content),
        'textLength': len(text),
        'summary': summary_data,
        'storedAt': 'news_archive.db',
        'uploadedAt': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/api/news/factset/reports')
def factset_reports():
    """List all stored FactSet reports."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''SELECT id, filename, report_date, file_size, sentiment_score,
                       sentiment_label, tickers_mentioned, uploaded_at
                       FROM factset_reports ORDER BY id DESC LIMIT 50''')
        rows = cur.fetchall()
        conn.close()
        reports = []
        for row in rows:
            reports.append({
                'id': row[0], 'filename': row[1], 'reportDate': row[2],
                'fileSize': row[3], 'sentimentScore': row[4],
                'sentimentLabel': row[5],
                'tickerCount': len(json.loads(row[6])) if row[6] else 0,
                'uploadedAt': row[7],
            })
        return jsonify({'total': len(reports), 'reports': reports})
    except Exception as e:
        return jsonify({'total': 0, 'reports': [], 'error': str(e)})


@app.route('/api/news/factset/report/<int:report_id>')
def factset_report_detail(report_id):
    """Get a specific FactSet report with full summary."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''SELECT id, filename, report_date, file_size, ai_summary,
                       market_synopsis, notable_gainers, notable_decliners,
                       tickers_mentioned, sentiment_score, sentiment_label, uploaded_at
                       FROM factset_reports WHERE id=?''', (report_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Report not found'}), 404
        return jsonify({
            'id': row[0], 'filename': row[1], 'reportDate': row[2],
            'fileSize': row[3],
            'aiSummary': json.loads(row[4]) if row[4] else {},
            'marketSynopsis': row[5],
            'notableGainers': json.loads(row[6]) if row[6] else [],
            'notableDecliners': json.loads(row[7]) if row[7] else [],
            'tickersMentioned': json.loads(row[8]) if row[8] else [],
            'sentimentScore': row[9], 'sentimentLabel': row[10],
            'uploadedAt': row[11],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Historical Sentiment Data for Factor Research
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/news/sentiment-history')
def sentiment_history():
    """Get historical sentiment records for backtesting."""
    ticker = flask_request.args.get('ticker', '').upper()
    limit = int(flask_request.args.get('limit', 100))
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if ticker:
            cur.execute('''SELECT ticker, score, label, headline_count, bullish_count,
                           bearish_count, neutral_count, source, recorded_at
                           FROM sentiment_history WHERE ticker=?
                           ORDER BY recorded_at DESC LIMIT ?''', (ticker, limit))
        else:
            cur.execute('''SELECT ticker, score, label, headline_count, bullish_count,
                           bearish_count, neutral_count, source, recorded_at
                           FROM sentiment_history ORDER BY recorded_at DESC LIMIT ?''', (limit,))
        rows = cur.fetchall()
        conn.close()
        records = [{
            'ticker': r[0], 'score': r[1], 'label': r[2],
            'headlineCount': r[3], 'bullish': r[4], 'bearish': r[5],
            'neutral': r[6], 'source': r[7], 'recordedAt': r[8],
        } for r in rows]
        return jsonify({'total': len(records), 'records': records, 'ticker': ticker or 'ALL'})
    except Exception as e:
        return jsonify({'total': 0, 'records': [], 'error': str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Web Search with Sentiment Scoring
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/news/web-search', methods=['POST'])
def web_search():
    """Search Google News RSS for a query and score sentiment."""
    body = flask_request.get_json(silent=True) or {}
    query = body.get('query', '').strip()
    if not query:
        return jsonify({'error': 'Provide {"query": "search term"}'}), 400

    # Use Google News RSS
    encoded_query = urllib.request.quote(query)
    headlines = _parse_rss(
        f'https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en',
        'Google News Search', limit=20
    )

    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    agg_label = 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral'

    # Save search results
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO web_search_results (query, results_json, sentiment_score, sentiment_label)
                       VALUES (?, ?, ?, ?)''',
            (query, json.dumps(headlines[:10]), avg_score, agg_label))
        conn.commit()
        conn.close()
    except:
        pass

    return jsonify({
        'query': query,
        'headlines': headlines,
        'count': len(headlines),
        'aggregateSentiment': {
            'score': avg_score, 'label': agg_label,
            'bullishCount': sum(1 for h in headlines if h['sentiment'] == 'bullish'),
            'bearishCount': sum(1 for h in headlines if h['sentiment'] == 'bearish'),
            'neutralCount': sum(1 for h in headlines if h['sentiment'] == 'neutral'),
        },
        'dataSource': 'google_news_rss',
        'searchedAt': datetime.utcnow().isoformat() + 'Z',
    })


if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("  QuantAlpha Station 3003 — News Station v2.0 (Alpha Research)")
    print("  NLP: Enhanced keyword-based | FactSet PDF AI | Web Search")
    print("═══════════════════════════════════════════════════════════")
    app.run(host='0.0.0.0', port=3003, debug=False)
