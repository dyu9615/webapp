"""
QuantAlpha Station 3003 — News Station (情报中心) v3.0
═══════════════════════════════════════════════════════
AI Intelligence Hub — 3-Layer Architecture:
  Layer 1: FactSet PDF AI Agent Workspace (pdfplumber + LLM + async)
  Layer 2: Web Search & Sentiment Scoring (RSS + NLP + Dashboard)
  Layer 3: Premium Data Sources (Bloomberg API + Reuters + Congress.gov)

Bug Prevention Controls:
  • Bug 1 Fix: pdfplumber with bounding-box column detection (replaces PyPDF2)
  • Bug 2 Fix: Pydantic validation + temperature=0 + structured JSON output
  • Bug 3 Fix: Async background worker + circuit breaker (never block Flask)

Data Flow:
  RSS Feeds → Station 3003 → Sentiment Analysis → Station 3004, 3005
  FactSet PDF → Async Queue → Background Worker → AI/Rule-based → SQLite → Frontend polls
  Bloomberg/Reuters/Congress → Cache (15min TTL) → Frontend
"""

import sys
import os
import time
import re
import json
import sqlite3
import threading
import uuid
import xml.etree.ElementTree as ET
import urllib.request
from datetime import datetime

from flask import Flask, jsonify, request as flask_request
from flask_cors import CORS

# Add news_service directory to path for local imports
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

_start_time = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# Database Setup — original tables + async job queue + premium cache
# ══════════════════════════════════════════════════════════════════════════════
DB_PATH = os.path.join(os.path.dirname(__file__), 'news_archive.db')


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Original tables (backward compatible)
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
    # NEW: Async agent job queue (Bug 3 Fix — never block Flask main thread)
    cur.execute('''CREATE TABLE IF NOT EXISTS agent_jobs (
        id TEXT PRIMARY KEY,
        filename TEXT,
        status TEXT DEFAULT 'pending',
        submitted_at TEXT,
        completed_at TEXT,
        raw_text TEXT,
        result_json TEXT,
        error TEXT,
        method TEXT DEFAULT 'pending'
    )''')
    # NEW: Premium source cache (Bloomberg, Reuters, Congress)
    cur.execute('''CREATE TABLE IF NOT EXISTS premium_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        category TEXT DEFAULT '',
        data_json TEXT NOT NULL,
        fetched_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


_init_db()

# Initialize premium data tables
try:
    from bloomberg_listener import init_premium_tables
    init_premium_tables(DB_PATH)
except Exception as e:
    print(f"[News Station] Premium tables init: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Circuit Breaker — prevents cascading failures when AI is unavailable
# ══════════════════════════════════════════════════════════════════════════════
_ai_fail_count = 0
_ai_cooldown_until = 0.0  # Unix timestamp
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN = 300  # 5 minutes
AI_MAX_RETRIES = 2


def _is_circuit_open() -> bool:
    """Check if AI circuit breaker is tripped (too many recent failures)."""
    global _ai_fail_count, _ai_cooldown_until
    if _ai_fail_count >= CIRCUIT_BREAKER_THRESHOLD:
        if time.time() < _ai_cooldown_until:
            return True
        # Cooldown expired, reset
        _ai_fail_count = 0
    return False


def _record_ai_failure():
    global _ai_fail_count, _ai_cooldown_until
    _ai_fail_count += 1
    if _ai_fail_count >= CIRCUIT_BREAKER_THRESHOLD:
        _ai_cooldown_until = time.time() + CIRCUIT_BREAKER_COOLDOWN
        print(f"[Circuit Breaker] OPEN — {_ai_fail_count} failures, cooldown {CIRCUIT_BREAKER_COOLDOWN}s")


def _record_ai_success():
    global _ai_fail_count
    _ai_fail_count = 0


# ══════════════════════════════════════════════════════════════════════════════
# Enhanced Keyword-based NLP Sentiment Engine (FinBERT-style keywords)
# This is the FALLBACK engine — used when AI is unavailable or circuit is open
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


# ══════════════════════════════════════════════════════════════════════════════
# PDF Text Extraction — pdfplumber with dual-column layout detection (Bug 1 Fix)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_pdf_text(file_content):
    """Extract text from PDF using pdfplumber (layout-aware, dual-column safe).

    Bug 1 Fix: Detects vertical separators in FactSet dual-column PDFs.
    Extracts left column then right column to preserve sentence boundaries.
    Falls back gracefully if pdfplumber is unavailable.
    """
    # Primary: pdfplumber with column detection
    try:
        import pdfplumber
        from io import BytesIO

        text_parts = []
        with pdfplumber.open(BytesIO(file_content)) as pdf:
            for page in pdf.pages:
                width = page.width
                midpoint = width / 2

                # Check for vertical lines near midpoint (column separator)
                lines = page.lines or []
                has_separator = any(
                    abs(line.get('x0', 0) - midpoint) < 30
                    and abs(line.get('top', 0) - line.get('bottom', 0)) > page.height * 0.3
                    for line in lines
                )

                if has_separator:
                    # Dual-column: extract left then right
                    left_bbox = (0, 0, midpoint - 5, page.height)
                    right_bbox = (midpoint + 5, 0, width, page.height)
                    try:
                        left_text = page.crop(left_bbox).extract_text() or ''
                        right_text = page.crop(right_bbox).extract_text() or ''
                        text_parts.append(left_text)
                        text_parts.append(right_text)
                    except Exception:
                        # Crop failed, use full page
                        text_parts.append(page.extract_text() or '')
                else:
                    # Single column or complex layout
                    text_parts.append(page.extract_text() or '')

        result = '\n'.join(text_parts)
        if result.strip():
            return result
    except ImportError:
        print("[PDF] pdfplumber not installed, trying PyPDF2 fallback")
    except Exception as e:
        print(f"[PDF] pdfplumber error: {e}, trying fallback")

    # Fallback: PyPDF2 (legacy, no column detection)
    try:
        from PyPDF2 import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(file_content))
        text = ''
        for page in reader.pages:
            text += page.extract_text() or ''
        return text
    except Exception as e:
        print(f"[PDF] All extraction methods failed: {e}")
        return ''


# ══════════════════════════════════════════════════════════════════════════════
# Rule-based Summary Generator (fallback when AI is unavailable)
# ══════════════════════════════════════════════════════════════════════════════

def _generate_factset_summary(text, max_words=300):
    """Generate a structured summary from FactSet report text (rule-based).
    This is the FALLBACK — used when AI circuit breaker is open or no API key.
    Returns a dict compatible with the SentimentResult schema.
    """
    if not text:
        return {
            'core_summary': 'No text extracted from PDF.',
            'impact_matrix': [],
            'macro_warnings': [],
            'overall_score': 0.0,
            'overall_label': 'neutral',
            'confidence': 0.0,
            # Legacy fields for backward compatibility
            'summary': 'No text extracted',
            'marketSynopsis': '',
            'notableGainers': [],
            'notableDecliners': [],
            'tickersMentioned': [],
            'tickerCount': 0,
            'overallSentiment': {'score': 0.0, 'label': 'neutral'},
            'wordCount': 0,
        }

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

    # Extract tickers
    ticker_pattern = re.compile(r'~([A-Z]{1,5})-US~|(?<![a-z])([A-Z]{2,5})(?=\s+[\+\-\$\d])')
    found_tickers = set()
    for match in ticker_pattern.finditer(text):
        t = match.group(1) or match.group(2)
        if t and len(t) <= 5 and t not in {
            'THE', 'AND', 'FOR', 'WITH', 'THIS', 'THAT', 'FROM', 'NEW',
            'USD', 'EST', 'EDT', 'PST', 'ISM', 'ADP', 'GDP', 'CPI', 'FED', 'SEC'
        }:
            found_tickers.add(t)

    # Overall sentiment via keyword engine
    overall_score, overall_label = _score_sentiment(text[:3000])

    # Build impact matrix from found tickers (rule-based: use overall sentiment)
    impact_matrix = []
    for t in sorted(found_tickers)[:10]:
        # Simple heuristic: check if ticker appears near bullish/bearish keywords
        ticker_context = ''
        for line in lines:
            if t in line:
                ticker_context += line + ' '
        t_score, t_label = _score_sentiment(ticker_context[:500]) if ticker_context else (0.0, 'neutral')
        impact_matrix.append({
            'ticker': t,
            'score': t_score,
            'label': t_label,
            'reasoning': f'Rule-based: keyword analysis of {len(ticker_context.split())} context words',
        })

    # Build macro warnings
    macro_warnings = []
    macro_terms = {
        'rate': ['interest rate', 'rate hike', 'fed funds', 'monetary policy'],
        'tariff': ['tariff', 'trade war', 'sanctions', 'export control'],
        'policy': ['regulation', 'antitrust', 'legislation', 'compliance'],
        'geopolitical': ['geopolitical', 'conflict', 'war', 'tensions'],
    }
    text_lower = text[:5000].lower()
    for mtype, keywords in macro_terms.items():
        matched = [kw for kw in keywords if kw in text_lower]
        if matched:
            macro_warnings.append({
                'type': mtype,
                'severity': 'high' if len(matched) >= 2 else 'medium',
                'description': f'Detected macro keywords: {", ".join(matched)}',
                'tickers_affected': sorted(found_tickers)[:5],
            })

    # Generate concise summary
    summary_parts = []
    if synopsis:
        summary_parts.append(synopsis[:300])
    if gainers:
        summary_parts.append(f"Gainers: {'; '.join(gainers[:3])}")
    if decliners:
        summary_parts.append(f"Decliners: {'; '.join(decliners[:3])}")
    core_summary = ' '.join(summary_parts)[:500] if summary_parts else f"Report mentions {len(found_tickers)} tickers with overall {overall_label} sentiment (score: {overall_score})."

    # Truncate legacy summary
    full_summary_parts = []
    if synopsis:
        full_summary_parts.append(f"Market: {synopsis[:300]}")
    if gainers:
        full_summary_parts.append(f"Gainers: {'; '.join(gainers[:3])}")
    if decliners:
        full_summary_parts.append(f"Decliners: {'; '.join(decliners[:3])}")
    full_summary_parts.append(f"Sentiment: {overall_label.upper()} (score: {overall_score})")
    full_summary_parts.append(f"Tickers: {', '.join(sorted(found_tickers)[:20])}")
    full_summary = '\n'.join(full_summary_parts)
    words = full_summary.split()
    if len(words) > max_words:
        full_summary = ' '.join(words[:max_words]) + '...'

    return {
        # New structured fields (SentimentResult compatible)
        'core_summary': core_summary,
        'impact_matrix': impact_matrix,
        'macro_warnings': macro_warnings,
        'overall_score': overall_score,
        'overall_label': overall_label,
        'confidence': 0.3,  # Low confidence for rule-based
        # Legacy fields (backward compatible)
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
# AI Analysis — LLM with structured output (Bug 2 Fix)
# ══════════════════════════════════════════════════════════════════════════════

def _ai_analyze_pdf(text: str) -> dict:
    """Call LLM API with structured JSON output for PDF analysis.

    Bug 2 Fix:
      - temperature=0 for deterministic output
      - response_format=json_object for structured JSON
      - Pydantic validation rejects malformed AI output
      - Graceful degradation: no API key → rule-based fallback (no crash)

    Env vars: OPENAI_API_KEY, AI_API_URL, AI_MODEL
    """
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        # No API key configured — silent fallback to rule-based
        return _generate_factset_summary(text)

    prompt = """Analyze this FactSet financial report and return ONLY a JSON object with these exact fields:
{
  "core_summary": "3-sentence digest of the report covering market direction, key movers, and outlook",
  "impact_matrix": [{"ticker": "NVDA", "score": 0.8, "label": "bullish", "reasoning": "Blackwell capacity expansion"}],
  "macro_warnings": [{"type": "tariff", "severity": "high", "description": "New semiconductor export controls", "tickers_affected": ["NVDA","AMD"]}],
  "overall_score": 0.3,
  "overall_label": "bullish",
  "confidence": 0.85
}

Rules:
- Scores MUST be between -1.0 and 1.0
- Labels MUST be exactly "bullish", "bearish", or "neutral"
- core_summary MUST be exactly 3 sentences
- severity MUST be "low", "medium", or "high"
- type MUST be "rate", "tariff", "policy", or "geopolitical"
- Return ONLY valid JSON, no markdown, no commentary

Report text (first 4000 chars):
""" + text[:4000]

    payload = json.dumps({
        'model': os.environ.get('AI_MODEL', 'gpt-4o-mini'),
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': 'You are a financial analyst. Return only valid JSON matching the exact schema requested.'},
            {'role': 'user', 'content': prompt},
        ],
    }).encode('utf-8')

    api_url = os.environ.get('AI_API_URL', 'https://api.openai.com/v1/chat/completions')
    req = urllib.request.Request(api_url, data=payload, headers={
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    })

    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())

    raw_content = body['choices'][0]['message']['content']
    parsed = json.loads(raw_content)

    # Validate with Pydantic (Bug 2 Fix — reject malformed output)
    try:
        from models import SentimentResult
        validated = SentimentResult(**parsed)
        result = validated.model_dump()
    except Exception as validation_err:
        print(f"[AI] Pydantic validation failed: {validation_err}")
        # Attempt to salvage what we can
        result = {
            'core_summary': parsed.get('core_summary', 'AI analysis completed but validation failed.'),
            'impact_matrix': [],
            'macro_warnings': [],
            'overall_score': max(-1.0, min(1.0, float(parsed.get('overall_score', 0)))),
            'overall_label': parsed.get('overall_label', 'neutral'),
            'confidence': 0.4,
        }

    # Add legacy fields for backward compatibility
    overall_score = result.get('overall_score', 0.0)
    overall_label = result.get('overall_label', 'neutral')
    tickers = [entry.get('ticker', '') for entry in result.get('impact_matrix', [])]
    result.update({
        'summary': result.get('core_summary', ''),
        'marketSynopsis': '',
        'notableGainers': [],
        'notableDecliners': [],
        'tickersMentioned': tickers,
        'tickerCount': len(tickers),
        'overallSentiment': {'score': overall_score, 'label': overall_label},
        'wordCount': len(result.get('core_summary', '').split()),
    })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Background Worker — async PDF processing (Bug 3 Fix)
# ══════════════════════════════════════════════════════════════════════════════

def _process_pdf_job(job_id: str):
    """Background worker: process a single PDF job.

    Bug 3 Fix: Runs in a daemon thread, never blocks Flask request handlers.
    Implements retry logic with circuit breaker fallback.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT raw_text, filename FROM agent_jobs WHERE id=?", (job_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        raw_text, filename = row

        cur.execute("UPDATE agent_jobs SET status='processing' WHERE id=?", (job_id,))
        conn.commit()
        conn.close()

        result = None
        method = 'pending'

        if _is_circuit_open():
            # Circuit breaker is open — skip AI, use rule-based
            result = _generate_factset_summary(raw_text)
            method = 'rule_based_circuit_breaker'
            print(f"[Job {job_id}] Circuit breaker OPEN, using rule-based")
        else:
            # Try AI with retries
            for attempt in range(AI_MAX_RETRIES + 1):
                try:
                    result = _ai_analyze_pdf(raw_text)
                    _record_ai_success()
                    method = 'ai'
                    print(f"[Job {job_id}] AI analysis succeeded (attempt {attempt + 1})")
                    break
                except Exception as e:
                    _record_ai_failure()
                    print(f"[Job {job_id}] AI attempt {attempt + 1} failed: {e}")
                    if attempt == AI_MAX_RETRIES:
                        # All retries exhausted — fallback to rule-based
                        result = _generate_factset_summary(raw_text)
                        method = 'rule_based_fallback'
                        print(f"[Job {job_id}] All AI retries failed, using rule-based")

        # Write result
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """UPDATE agent_jobs
               SET status='completed', completed_at=?, result_json=?, method=?
               WHERE id=?""",
            (datetime.utcnow().isoformat(), json.dumps(result), method, job_id)
        )
        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[Job {job_id}] Fatal error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "UPDATE agent_jobs SET status='failed', error=?, completed_at=? WHERE id=?",
                (str(e)[:500], datetime.utcnow().isoformat(), job_id)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — All original endpoints preserved + new v3.0 endpoints
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    report_count = 0
    sentiment_count = 0
    job_count = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM factset_reports")
        report_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sentiment_history")
        sentiment_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM agent_jobs")
        job_count = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass

    return jsonify({
        'status': 'ok',
        'service': 'QuantAlpha Station 3003 — News Station v3.0 (AI Intelligence Hub)',
        'port': 3003,
        'version': '3.0.0',
        'architecture': '8-station-microservices',
        'mode': 'ai_intelligence_hub',
        'uptime_sec': round(time.time() - _start_time, 1),
        'storage': {
            'factsetReports': report_count,
            'sentimentRecords': sentiment_count,
            'agentJobs': job_count,
            'database': 'news_archive.db',
        },
        'ai': {
            'available': not _is_circuit_open() and bool(os.environ.get('OPENAI_API_KEY', '')),
            'circuit_breaker': 'open' if _is_circuit_open() else 'closed',
            'fail_count': _ai_fail_count,
            'fallback': 'keyword_nlp_rule_based',
        },
        'nlp': {
            'engine': 'enhanced_keyword_based (FinBERT-style)',
            'bullishKeywords': len(BULLISH_KEYWORDS),
            'bearishKeywords': len(BEARISH_KEYWORDS),
        },
        'pdf_parser': 'pdfplumber (layout-aware, dual-column)',
        'premium_sources': ['Bloomberg (RapidAPI)', 'Reuters (RSS)', 'Congress.gov (API)'],
        'endpoints': [
            'GET  /api/news/scan/<ticker>            — Ticker-specific news + sentiment',
            'GET  /api/news/live                     — Market-wide news feed',
            'GET  /api/news/sentiment/<ticker>        — Pure sentiment score',
            'POST /api/news/factset/upload            — Sync PDF upload (legacy)',
            'POST /api/news/factset/upload-async      — Async PDF upload (AI agent)',
            'GET  /api/news/agent/job/<id>            — Poll async job status',
            'GET  /api/news/agent/status              — AI agent + circuit breaker',
            'GET  /api/news/sentiment-dashboard       — Composite market gauge',
            'GET  /api/news/bloomberg                 — Bloomberg news (cached)',
            'GET  /api/news/reuters                   — Reuters RSS feed',
            'GET  /api/news/congress                  — Congress.gov bills',
        ],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


# ── Original endpoints (unchanged) ────────────────────────────────────────

@app.route('/api/news/scan/<ticker>')
def news_scan(ticker):
    """Authoritative news scan with sentiment analysis for a specific ticker."""
    ticker = ticker.upper().strip()
    headlines = _parse_rss(
        f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US',
        'Yahoo Finance', ticker
    )
    google_headlines = _parse_rss(
        f'https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en',
        'Google News', ticker, limit=5
    )
    headlines.extend(google_headlines)

    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    bull_ct = sum(1 for h in headlines if h['sentiment'] == 'bullish')
    bear_ct = sum(1 for h in headlines if h['sentiment'] == 'bearish')
    neut_ct = sum(1 for h in headlines if h['sentiment'] == 'neutral')
    agg_label = 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral'

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


# ── Sync PDF Upload (legacy, backward compatible) ─────────────────────────

@app.route('/api/news/factset/upload', methods=['POST'])
def factset_upload():
    """Upload FactSet PDF report, extract text, generate summary (sync/legacy)."""
    max_words = int(flask_request.args.get('max_words', 300))

    if 'file' in flask_request.files:
        f = flask_request.files['file']
        filename = f.filename or 'unknown.pdf'
        content = f.read()
    elif flask_request.is_json:
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

    text = _extract_pdf_text(content)
    if not text:
        return jsonify({'error': 'Could not extract text from PDF'}), 400

    summary_data = _generate_factset_summary(text, max_words=max_words)

    date_match = re.search(
        r'(Mar|Feb|Jan|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2}),?\s*(20\d{2})',
        filename + ' ' + text[:500]
    )
    report_date = (f"{date_match.group(3)}-{date_match.group(1)}-{date_match.group(2)}"
                   if date_match else datetime.utcnow().strftime('%Y-%m-%d'))

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO factset_reports
            (filename, upload_date, report_date, file_size, content_text, ai_summary,
             market_synopsis, notable_gainers, notable_decliners, tickers_mentioned,
             sentiment_score, sentiment_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (filename, datetime.utcnow().isoformat(), report_date, len(content),
             text[:50000],
             json.dumps(summary_data),
             summary_data.get('marketSynopsis', ''),
             json.dumps(summary_data.get('notableGainers', [])),
             json.dumps(summary_data.get('notableDecliners', [])),
             json.dumps(summary_data.get('tickersMentioned', [])),
             summary_data.get('overallSentiment', {}).get('score', 0.0),
             summary_data.get('overallSentiment', {}).get('label', 'neutral')))
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


# ── Async PDF Upload (new v3.0 — returns immediately, processes in background) ──

@app.route('/api/news/factset/upload-async', methods=['POST'])
def factset_upload_async():
    """Upload PDF, store raw text, return job_id. Background worker processes.

    Bug 3 Fix: Never blocks the Flask request handler.
    Frontend polls /api/news/agent/job/<id> for results.
    """
    if 'file' in flask_request.files:
        f = flask_request.files['file']
        filename = f.filename or 'unknown.pdf'
        content = f.read()
    elif flask_request.is_json:
        data = flask_request.get_json()
        file_path = data.get('filePath', '')
        if file_path and os.path.exists(file_path):
            with open(file_path, 'rb') as fp:
                content = fp.read()
            filename = os.path.basename(file_path)
        else:
            return jsonify({'error': 'No file provided'}), 400
    else:
        return jsonify({'error': 'No file provided'}), 400

    # Extract text (pdfplumber — fast, no I/O blocking)
    text = _extract_pdf_text(content)
    if not text:
        return jsonify({'error': 'Could not extract text from PDF'}), 400

    # Create job
    job_id = uuid.uuid4().hex[:8]
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO agent_jobs (id, filename, status, submitted_at, raw_text)
               VALUES (?, ?, 'pending', ?, ?)""",
            (job_id, filename, datetime.utcnow().isoformat(), text[:50000])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'Database error: {e}'}), 500

    # Also store in factset_reports for backward compatibility
    try:
        date_match = re.search(
            r'(Mar|Feb|Jan|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2}),?\s*(20\d{2})',
            filename + ' ' + text[:500]
        )
        report_date = (f"{date_match.group(3)}-{date_match.group(1)}-{date_match.group(2)}"
                       if date_match else datetime.utcnow().strftime('%Y-%m-%d'))
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO factset_reports
            (filename, upload_date, report_date, file_size, content_text, ai_summary,
             sentiment_score, sentiment_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (filename, datetime.utcnow().isoformat(), report_date, len(content),
             text[:50000], json.dumps({'status': 'processing', 'jobId': job_id}), 0.0, 'pending'))
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Fire background processing thread
    worker = threading.Thread(target=_process_pdf_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify({
        'success': True,
        'jobId': job_id,
        'status': 'pending',
        'filename': filename,
        'textLength': len(text),
        'message': 'PDF uploaded. AI agent is processing in background. Poll /api/news/agent/job/' + job_id,
    })


# ── Agent Job Status (poll endpoint) ──────────────────────────────────────

@app.route('/api/news/agent/job/<job_id>')
def agent_job_status(job_id):
    """Poll endpoint: frontend checks async job completion."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, filename, status, submitted_at, completed_at,
                      result_json, error, method
               FROM agent_jobs WHERE id=?""",
            (job_id,)
        )
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'Database error: {e}'}), 500

    if not row:
        return jsonify({'error': 'Job not found'}), 404

    result = None
    if row[5]:
        try:
            result = json.loads(row[5])
        except Exception:
            result = {'raw': row[5]}

    return jsonify({
        'jobId': row[0],
        'filename': row[1],
        'status': row[2],
        'submittedAt': row[3],
        'completedAt': row[4],
        'result': result,
        'error': row[6],
        'method': row[7],
    })


# ── Agent Pipeline Status ─────────────────────────────────────────────────

@app.route('/api/news/agent/status')
def agent_status():
    """AI agent pipeline status including circuit breaker state."""
    pending = 0
    completed = 0
    failed = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM agent_jobs GROUP BY status")
        for status, count in cur.fetchall():
            if status == 'pending':
                pending = count
            elif status == 'completed':
                completed = count
            elif status == 'failed':
                failed = count
        conn.close()
    except Exception:
        pass

    return jsonify({
        'ai_available': not _is_circuit_open() and bool(os.environ.get('OPENAI_API_KEY', '')),
        'circuit_breaker': {
            'is_open': _is_circuit_open(),
            'fail_count': _ai_fail_count,
            'threshold': CIRCUIT_BREAKER_THRESHOLD,
            'cooldown_seconds': CIRCUIT_BREAKER_COOLDOWN,
            'cooldown_until': (
                datetime.fromtimestamp(_ai_cooldown_until).isoformat()
                if _ai_cooldown_until > 0 else None
            ),
        },
        'fallback_mode': 'rule_based_keyword_nlp',
        'jobs': {
            'pending': pending,
            'completed': completed,
            'failed': failed,
        },
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


# ── Sentiment Dashboard (composite gauge) ─────────────────────────────────

@app.route('/api/news/sentiment-dashboard')
def sentiment_dashboard():
    """Composite sentiment gauge: news avg + regime detection + position sizing.

    The Sentiment Dashboard provides:
    1. Regime Detection — identifies systemic risk (extreme_fear → extreme_greed)
    2. Position Sizing Signal — full/reduced/minimal based on market sentiment
    3. Contrarian Signal Potential — extreme readings suggest mean reversion
    """
    avg_news = 0.0
    record_count = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""SELECT AVG(score), COUNT(*) FROM sentiment_history
            WHERE recorded_at > datetime('now', '-24 hours')""")
        row = cur.fetchone()
        avg_news = row[0] or 0.0
        record_count = row[1] or 0
        conn.close()
    except Exception:
        pass

    # Regime detection
    if avg_news > 0.5:
        regime = 'extreme_greed'
    elif avg_news > 0.15:
        regime = 'bullish'
    elif avg_news > -0.15:
        regime = 'neutral'
    elif avg_news > -0.5:
        regime = 'bearish'
    else:
        regime = 'extreme_fear'

    # Position sizing signal
    abs_score = abs(avg_news)
    if abs_score < 0.3:
        sizing = 'full'
        sizing_pct = 100
    elif abs_score < 0.6:
        sizing = 'reduced'
        sizing_pct = 50
    else:
        sizing = 'minimal'
        sizing_pct = 25

    # Contrarian signal
    contrarian = abs_score > 0.6

    return jsonify({
        'composite_score': round(avg_news, 3),
        'regime': regime,
        'position_sizing': sizing,
        'position_sizing_pct': sizing_pct,
        'contrarian_signal': contrarian,
        'components': {
            'news_sentiment_avg': round(avg_news, 3),
            'sentiment_records_24h': record_count,
            'vix_proxy': 'Integrate from Station 3001 /api/yf/macro',
            'high_yield_spread': 'N/A',
        },
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


# ── Stored reports (unchanged) ────────────────────────────────────────────

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


# ── Sentiment history (unchanged) ─────────────────────────────────────────

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


# ── Web search (unchanged) ────────────────────────────────────────────────

@app.route('/api/news/web-search', methods=['POST'])
def web_search():
    """Search Google News RSS for a query and score sentiment."""
    body = flask_request.get_json(silent=True) or {}
    query = body.get('query', '').strip()
    if not query:
        return jsonify({'error': 'Provide {"query": "search term"}'}), 400

    encoded_query = urllib.request.quote(query)
    headlines = _parse_rss(
        f'https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en',
        'Google News Search', limit=20
    )

    scores = [h['sentimentScore'] for h in headlines if h['sentimentScore'] != 0]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    agg_label = 'bullish' if avg_score > 0.15 else 'bearish' if avg_score < -0.15 else 'neutral'

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO web_search_results (query, results_json, sentiment_score, sentiment_label)
                       VALUES (?, ?, ?, ?)''',
            (query, json.dumps(headlines[:10]), avg_score, agg_label))
        conn.commit()
        conn.close()
    except Exception:
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


# ══════════════════════════════════════════════════════════════════════════════
# Premium Data Source Endpoints (Layer 3)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/news/bloomberg')
def bloomberg_news():
    """Bloomberg Market News via RapidAPI (cached 15min)."""
    category = flask_request.args.get('category', 'market')
    try:
        from bloomberg_listener import fetch_bloomberg_news
        articles = fetch_bloomberg_news(DB_PATH, category)
        return jsonify({
            'articles': articles,
            'count': len(articles),
            'source': 'bloomberg_rapidapi',
            'category': category,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        })
    except Exception as e:
        return jsonify({
            'articles': [],
            'count': 0,
            'source': 'bloomberg_rapidapi',
            'error': str(e),
            'hint': 'Set RAPIDAPI_KEY environment variable for Bloomberg data',
        })


@app.route('/api/news/reuters')
def reuters_news():
    """Reuters RSS (Business + Technology)."""
    try:
        from bloomberg_listener import fetch_reuters_rss
        articles = fetch_reuters_rss(DB_PATH)
        return jsonify({
            'articles': articles,
            'count': len(articles),
            'source': 'reuters_rss',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        })
    except Exception as e:
        return jsonify({
            'articles': [],
            'count': 0,
            'source': 'reuters_rss',
            'error': str(e),
        })


@app.route('/api/news/congress')
def congress_bills():
    """Congress.gov bill monitoring for tech/AI/semiconductor keywords."""
    try:
        from bloomberg_listener import fetch_congress_bills
        bills = fetch_congress_bills(DB_PATH)
        # Count high-relevance bills (Red Flags)
        red_flags = [b for b in bills if b.get('relevance_score', 0) >= 0.6]
        return jsonify({
            'bills': bills,
            'count': len(bills),
            'red_flags': len(red_flags),
            'source': 'congress_gov_api',
            'monitored_keywords': [
                'artificial intelligence', 'semiconductor', 'antitrust',
                'CHIPS Act', 'AI regulation', 'export control',
            ],
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        })
    except Exception as e:
        return jsonify({
            'bills': [],
            'count': 0,
            'red_flags': 0,
            'source': 'congress_gov_api',
            'error': str(e),
            'hint': 'Set CONGRESS_API_KEY environment variable for Congress.gov data',
        })


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("  QuantAlpha Station 3003 — News Station v3.0")
    print("  AI Intelligence Hub | 3-Layer Architecture")
    print("  PDF: pdfplumber | AI: structured JSON + circuit breaker")
    print("  Premium: Bloomberg + Reuters + Congress.gov")
    print("=" * 60)
    app.run(host='0.0.0.0', port=3003, debug=False)
