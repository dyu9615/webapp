"""
QuantAlpha News Station — Premium Data Aggregator
══════════════════════════════════════════════════
Bloomberg (RapidAPI) + Reuters (RSS) + Congress.gov (API)

All results cached in SQLite with 15-min TTL.
Graceful degradation: returns empty list if API keys missing or requests fail.

Environment Variables:
  RAPIDAPI_KEY       — RapidAPI key for Bloomberg Market & Financial News
  CONGRESS_API_KEY   — Free key from api.congress.gov
"""

import os
import json
import time
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ── Configuration ──────────────────────────────────────────────────────────
RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY', '')
BLOOMBERG_HOST = 'bloomberg-market-and-financial-news.p.rapidapi.com'
CACHE_TTL = 900  # 15 minutes

REUTERS_FEEDS = [
    ('https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best', 'Reuters Business'),
    ('https://www.reutersagency.com/feed/?best-topics=tech&post_type=best', 'Reuters Tech'),
]

CONGRESS_API_BASE = 'https://api.congress.gov/v3'
CONGRESS_API_KEY = os.environ.get('CONGRESS_API_KEY', '')
CONGRESS_KEYWORDS = [
    'artificial intelligence', 'semiconductor', 'antitrust',
    'chip', 'CHIPS Act', 'AI regulation', 'data privacy',
    'big tech', 'export control',
]


# ── Database Setup ─────────────────────────────────────────────────────────

def init_premium_tables(db_path: str) -> None:
    """Create premium_cache table if not exists."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS premium_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            category TEXT DEFAULT '',
            data_json TEXT NOT NULL,
            fetched_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_premium_source ON premium_cache(source, category)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[bloomberg_listener] DB init error: {e}")


# ── Generic Cache Layer ────────────────────────────────────────────────────

def _get_cached(db_path: str, source: str, category: str = '') -> list | None:
    """Return cached data if not expired, else None."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            '''SELECT data_json FROM premium_cache
               WHERE source=? AND category=? AND expires_at > datetime('now')
               ORDER BY fetched_at DESC LIMIT 1''',
            (source, category)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"[bloomberg_listener] Cache read error: {e}")
    return None


def _set_cache(db_path: str, source: str, category: str, data: list) -> None:
    """Store data in cache with TTL."""
    try:
        expires = (datetime.utcnow() + timedelta(seconds=CACHE_TTL)).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Remove old entries for this source+category
        cur.execute('DELETE FROM premium_cache WHERE source=? AND category=?', (source, category))
        cur.execute(
            'INSERT INTO premium_cache (source, category, data_json, expires_at) VALUES (?, ?, ?, ?)',
            (source, category, json.dumps(data), expires)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[bloomberg_listener] Cache write error: {e}")


# ── Bloomberg (RapidAPI) ───────────────────────────────────────────────────

def fetch_bloomberg_news(db_path: str, category: str = 'market') -> list:
    """Fetch Bloomberg news via RapidAPI. Cached with 15-min TTL.

    Categories: 'market' (Market Wraps), 'tech' (Tech Deals), 'general'
    Returns list of article dicts with ai_status='pending' for scoring pipeline.
    """
    # Check cache first
    cached = _get_cached(db_path, 'bloomberg', category)
    if cached is not None:
        return cached

    if not RAPIDAPI_KEY:
        return []

    # Map category to Bloomberg API endpoint
    category_map = {
        'market': '/market/auto-complete?query=market',
        'tech': '/stories/list?template=TECHNOLOGY',
        'deals': '/stories/list?template=DEALS',
        'general': '/stories/list?template=HOMEPAGE',
    }
    endpoint = category_map.get(category, category_map['general'])

    try:
        url = f'https://{BLOOMBERG_HOST}{endpoint}'
        req = urllib.request.Request(url, headers={
            'X-RapidAPI-Key': RAPIDAPI_KEY,
            'X-RapidAPI-Host': BLOOMBERG_HOST,
            'User-Agent': 'QuantAlpha/3.0',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())

        # Parse response — Bloomberg API returns stories in various formats
        articles = []
        stories = []
        if isinstance(raw, dict):
            stories = raw.get('stories', raw.get('result', raw.get('articles', [])))
            if isinstance(stories, dict):
                stories = stories.get('stories', [])
        elif isinstance(raw, list):
            stories = raw

        for story in stories[:30]:
            if isinstance(story, dict):
                title = story.get('title', story.get('headline', ''))
                if not title:
                    continue
                articles.append({
                    'title': title,
                    'url': story.get('url', story.get('longURL', '')),
                    'source': 'Bloomberg (RapidAPI)',
                    'published_at': story.get('published_at', story.get('publishedAt',
                                    story.get('updatedAt', datetime.utcnow().isoformat()))),
                    'summary': story.get('summary', story.get('abstract', ''))[:300],
                    'category': category,
                    'ai_status': 'pending',
                    'sentiment_score': None,
                    'sentiment_label': None,
                    'cached_at': datetime.utcnow().isoformat(),
                })

        _set_cache(db_path, 'bloomberg', category, articles)
        return articles

    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[bloomberg_listener] Rate limited (429). Using cache or empty.")
        else:
            print(f"[bloomberg_listener] Bloomberg API error {e.code}: {e.reason}")
        return []
    except Exception as e:
        print(f"[bloomberg_listener] Bloomberg fetch error: {e}")
        return []


# ── Reuters (RSS) ──────────────────────────────────────────────────────────

def fetch_reuters_rss(db_path: str) -> list:
    """Fetch Reuters Business + Tech RSS feeds. Cached with 15-min TTL."""
    cached = _get_cached(db_path, 'reuters', 'all')
    if cached is not None:
        return cached

    articles = []
    for feed_url, source_name in REUTERS_FEEDS:
        try:
            req = urllib.request.Request(feed_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                root = ET.fromstring(resp.read())

            channel = root.find('channel') or root
            for item in channel.findall('item')[:15]:
                title = (item.findtext('title') or '').strip()
                if not title:
                    continue
                articles.append({
                    'title': title,
                    'url': item.findtext('link') or '',
                    'source': source_name,
                    'published_at': item.findtext('pubDate') or '',
                    'summary': (item.findtext('description') or '')[:300],
                    'category': 'business' if 'Business' in source_name else 'tech',
                    'ai_status': 'pending',
                    'sentiment_score': None,
                    'sentiment_label': None,
                })
        except Exception as e:
            print(f"[bloomberg_listener] Reuters RSS error ({source_name}): {e}")

    _set_cache(db_path, 'reuters', 'all', articles)
    return articles


# ── Congress.gov API ───────────────────────────────────────────────────────

def fetch_congress_bills(db_path: str) -> list:
    """Search Congress.gov for bills matching tech/AI/semiconductor keywords.

    Requires CONGRESS_API_KEY env var (free from api.congress.gov).
    Returns list of bill dicts with keyword matches highlighted.
    """
    cached = _get_cached(db_path, 'congress', 'bills')
    if cached is not None:
        return cached

    if not CONGRESS_API_KEY:
        return []

    bills = []
    # Search for each keyword group
    search_queries = [
        'artificial+intelligence',
        'semiconductor+chip',
        'antitrust+big+tech',
    ]

    for query in search_queries:
        try:
            url = (f'{CONGRESS_API_BASE}/bill?query={query}'
                   f'&api_key={CONGRESS_API_KEY}'
                   f'&format=json&limit=10&sort=updateDate+desc')
            req = urllib.request.Request(url, headers={
                'User-Agent': 'QuantAlpha/3.0',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            for bill in data.get('bills', []):
                title = bill.get('title', '')
                bill_id = f"{bill.get('type', '')}{bill.get('number', '')}-{bill.get('congress', '')}"

                # Check which keywords this bill matches
                title_lower = title.lower()
                matched_keywords = [kw for kw in CONGRESS_KEYWORDS if kw.lower() in title_lower]

                # Calculate relevance score based on keyword matches
                relevance = min(1.0, len(matched_keywords) * 0.3) if matched_keywords else 0.1

                bills.append({
                    'bill_id': bill_id,
                    'title': title,
                    'url': bill.get('url', f'https://congress.gov/bill/{bill.get("congress", "")}/{bill.get("type", "").lower()}/{bill.get("number", "")}'),
                    'introduced_date': bill.get('introducedDate', ''),
                    'sponsor': bill.get('sponsors', [{}])[0].get('fullName', '') if bill.get('sponsors') else '',
                    'keywords_matched': matched_keywords,
                    'relevance_score': round(relevance, 2),
                    'status': bill.get('latestAction', {}).get('text', '')[:200] if bill.get('latestAction') else '',
                })
        except Exception as e:
            print(f"[bloomberg_listener] Congress.gov error ({query}): {e}")

    # Deduplicate by bill_id and sort by relevance
    seen = set()
    unique_bills = []
    for b in sorted(bills, key=lambda x: x['relevance_score'], reverse=True):
        if b['bill_id'] not in seen:
            seen.add(b['bill_id'])
            unique_bills.append(b)

    _set_cache(db_path, 'congress', 'bills', unique_bills)
    return unique_bills


# ── Aggregate All Sources ──────────────────────────────────────────────────

def get_all_premium_news(db_path: str) -> dict:
    """Fetch all premium sources. Returns {bloomberg: [...], reuters: [...], congress: [...]}."""
    return {
        'bloomberg': fetch_bloomberg_news(db_path, 'general'),
        'reuters': fetch_reuters_rss(db_path),
        'congress': fetch_congress_bills(db_path),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }
