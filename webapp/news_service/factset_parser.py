"""
QuantAlpha — Layer 1C: FactSet Top News Structured Parser
══════════════════════════════════════════════════════════
Regex-based extraction engine for FactSet daily PDFs.

Threat T1 Fix: ALL numbers come from regex parsing, NEVER from LLM.
This prevents hallucination contamination — LLM receives pre-parsed
structured data and is instructed to never recall/correct numbers.

PDF Schema (fixed daily format):
  1. UNITED STATES MARKET SYNOPSIS — index returns + macro narrative
  2. Notable Gainers — ticker, %, company, reason
  3. Notable Decliners — ticker, %, company, reason
  4. TOP STORIES — narrative sections with timestamps
  5. StreetAccount Event Preview — upcoming data releases
  6. ASIA PACIFIC / EUROPE TOP STORIES — international
  7. Sector Highlights — 11 sectors with % and drivers
  8. Estimate Monitor — analyst revisions table
  9. Benchmark News / Transcript Intelligence — earnings
 10. FRC Investment Research — third-party research

Each parser function:
  - Uses regex with fixed boundary markers
  - Returns structured Pydantic model or empty default
  - Never raises — failures return empty with extraction_failures noted
  - Never calls LLM
"""

import re
from typing import List, Optional, Tuple

# Import models — this module is imported by app.py which adds the path
try:
    from models import (
        MarketSynopsis, TickerImpact, EstimateRevision,
        SectorSnapshot, TopStory, EventPreview, FactSetStructured
    )
except ImportError:
    from news_service.models import (
        MarketSynopsis, TickerImpact, EstimateRevision,
        SectorSnapshot, TopStory, EventPreview, FactSetStructured
    )


# ══════════════════════════════════════════════════════════════════════════════
# Master Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def parse_factset_document(raw_text: str) -> FactSetStructured:
    """Parse a complete FactSet Top News PDF into structured data.

    Calls each section parser in try/except. Failures are recorded in
    extraction_failures but never propagate upward.

    Returns FactSetStructured with data_quality_score = weighted sum of
    successfully parsed sections.
    """
    if not raw_text or len(raw_text) < 100:
        return FactSetStructured(
            data_quality_score=0.0,
            extraction_failures=['empty_text']
        )

    result = FactSetStructured()
    failures = []

    # Extract report date
    result.report_date = _extract_report_date(raw_text)

    # Extract all tickers globally
    result.all_tickers = _extract_all_tickers(raw_text)

    # ── Parse each section independently ──
    try:
        result.synopsis = parse_synopsis(raw_text)
    except Exception as e:
        failures.append(f'synopsis: {str(e)[:80]}')
        print(f'[Parser] Synopsis extraction failed: {e}')

    try:
        result.gainers = parse_gainers(raw_text)
    except Exception as e:
        failures.append(f'gainers: {str(e)[:80]}')
        print(f'[Parser] Gainers extraction failed: {e}')

    try:
        result.decliners = parse_decliners(raw_text)
    except Exception as e:
        failures.append(f'decliners: {str(e)[:80]}')
        print(f'[Parser] Decliners extraction failed: {e}')

    try:
        result.top_stories = parse_top_stories(raw_text)
    except Exception as e:
        failures.append(f'top_stories: {str(e)[:80]}')
        print(f'[Parser] Top Stories extraction failed: {e}')

    try:
        result.event_previews = parse_event_preview(raw_text)
    except Exception as e:
        failures.append(f'event_preview: {str(e)[:80]}')
        print(f'[Parser] Event Preview extraction failed: {e}')

    try:
        result.estimate_revisions = parse_estimates(raw_text)
    except Exception as e:
        failures.append(f'estimates: {str(e)[:80]}')
        print(f'[Parser] Estimates extraction failed: {e}')

    try:
        result.sector_snapshots = parse_sectors(raw_text)
    except Exception as e:
        failures.append(f'sectors: {str(e)[:80]}')
        print(f'[Parser] Sectors extraction failed: {e}')

    result.extraction_failures = failures

    # ── Compute data quality score (weighted) ──
    weights = {
        'synopsis': 0.25,
        'gainers': 0.20,
        'decliners': 0.20,
        'estimates': 0.15,
        'sectors': 0.10,
        'top_stories': 0.10,
    }
    score = 0.0
    if result.synopsis and (result.synopsis.dow_pct is not None or result.synopsis.narrative):
        score += weights['synopsis']
    if result.gainers:
        score += weights['gainers']
    if result.decliners:
        score += weights['decliners']
    if result.estimate_revisions:
        score += weights['estimates']
    if result.sector_snapshots:
        score += weights['sectors']
    if result.top_stories:
        score += weights['top_stories']

    result.data_quality_score = round(score, 3)

    sections_found = sum([
        bool(result.synopsis), bool(result.gainers), bool(result.decliners),
        bool(result.top_stories), bool(result.estimate_revisions), bool(result.sector_snapshots),
    ])
    print(f'[Parser] Complete: {sections_found}/6 sections, '
          f'{len(result.all_tickers)} tickers, '
          f'quality={result.data_quality_score:.2f}, '
          f'{len(failures)} failures')

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Utility Extractors
# ══════════════════════════════════════════════════════════════════════════════

def _extract_report_date(text: str) -> str:
    """Extract date from 'Updated: HH:MM PM (EST/EDT), DD Mon YY' or timestamp patterns."""
    # Pattern 1: FactSet header format
    m = re.search(r"Updated:\s*\d{1,2}:\d{2}\s*[AP]M\s*\([^)]+\),?\s*(\d{1,2}\s+\w{3}\s*'\d{2})", text)
    if m:
        return m.group(1).strip()

    # Pattern 2: Date from timestamps like "3/5/2026 4:04:47 PM"
    m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M', text)
    if m:
        return m.group(1)

    # Pattern 3: Filename-style "Mar 5, 2026"
    m = re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4})', text)
    if m:
        return m.group(1)

    return ''


def _extract_all_tickers(text: str) -> List[str]:
    """Global ticker extraction from ~TICKER-REGION~ format.

    Supports: ~NVDA-US~, ~9618-HK~, ~ITX-ES~, ~NESTE-FI~, etc.
    Returns deduplicated sorted list.
    """
    # Primary: FactSet ~TICKER-XX~ format
    tickers = set()
    for m in re.finditer(r'~([A-Z]{1,5})-(?:US|HK|JP|GB|ES|DE|FI|IT|CN)~', text):
        tickers.add(m.group(1))

    # Secondary: TICKER-USA format from Estimate Monitor
    for m in re.finditer(r'([A-Z]{1,5})-USA?\b', text):
        t = m.group(1)
        # Filter out common false positives
        if t not in {'EST', 'EDT', 'PST', 'GMT', 'USD', 'LLC', 'INC', 'GDP', 'CPI',
                     'ADP', 'ISM', 'FED', 'SEC', 'ECB', 'IPO', 'ETF', 'ADR', 'CEO',
                     'CFO', 'COO', 'CTO', 'EPS', 'RPO', 'FRC', 'GAAP', 'THE', 'AND',
                     'FOR', 'REF', 'NEW', 'TOP', 'NET'}:
            tickers.add(t)

    return sorted(tickers)


def _extract_section(text: str, start_pattern: str, end_patterns: List[str]) -> str:
    """Extract text between start_pattern and the first matching end_pattern.

    Returns empty string if start pattern not found.
    """
    # Find start
    start_match = re.search(start_pattern, text, re.IGNORECASE)
    if not start_match:
        return ''

    start_pos = start_match.start()
    remaining = text[start_pos:]

    # Find earliest end
    end_pos = len(remaining)
    for ep in end_patterns:
        m = re.search(ep, remaining[100:], re.IGNORECASE)  # Skip past start marker
        if m:
            end_pos = min(end_pos, m.start() + 100)

    return remaining[:end_pos]


# ══════════════════════════════════════════════════════════════════════════════
# Section Parsers
# ══════════════════════════════════════════════════════════════════════════════

def parse_synopsis(text: str) -> Optional[MarketSynopsis]:
    """Parse 'UNITED STATES MARKET SYNOPSIS' section.

    Extracts:
    - 4 index returns: Dow, S&P 500, Nasdaq, Russell 2000
    - Narrative text (first 500 chars)
    - Macro drivers from keyword detection
    - Outperformers/underperformers from relative performance mentions
    """
    section = _extract_section(
        text,
        r'MARKET SYNOPSIS',
        [r'Notable Gainers', r'Notable Decliners', r'TOP STORIES']
    )
    if not section:
        return None

    synopsis = MarketSynopsis()

    # ── Extract index returns ──
    # Pattern: "Dow (1.61%)" or "Dow -1.61%" or "S&P 500 (0.56%)"
    # Parenthesized numbers are negative in FactSet convention
    index_map = {
        'dow': 'dow_pct',
        's&p 500': 'sp500_pct',
        's&p500': 'sp500_pct',
        'nasdaq': 'nasdaq_pct',
        'russell 2000': 'russell_pct',
        'russell': 'russell_pct',
    }

    # Pattern handles both +/- prefix and parenthesized (negative)
    for m in re.finditer(
        r'(Dow|S&P\s*500|Nasdaq|Russell\s*2000)\s*'
        r'(?:\(?([+-]?\d+\.?\d*)%\)?)',
        section, re.IGNORECASE
    ):
        name = m.group(1).lower().strip()
        pct_str = m.group(2)
        try:
            pct = float(pct_str)
            # In FactSet, parenthesized = negative: "Dow (1.61%)" means -1.61%
            # Check if the original text has parentheses around the number
            full_match = m.group(0)
            if '(' in full_match and pct > 0:
                pct = -pct
            for key, attr in index_map.items():
                if key in name:
                    setattr(synopsis, attr, round(pct, 3))
                    break
        except ValueError:
            pass

    # ── Extract narrative ──
    # Remove the header line, take first 500 chars of body
    lines = section.split('\n')
    body_lines = [l.strip() for l in lines[1:] if l.strip() and not re.match(r'^\d{1,2}/\d{1,2}/\d{4}', l.strip())]
    synopsis.narrative = ' '.join(body_lines)[:500]

    # ── Detect macro drivers ──
    text_lower = section.lower()
    driver_keywords = {
        'oil_strength': ['oil', 'crude', 'wti', 'brent', 'petroleum', 'energy price'],
        'yield_backup': ['yield', 'treasury', 'bond', 'curve', 'rate'],
        'dollar_bid': ['dollar', 'dxy', 'greenback', 'fx'],
        'gold_move': ['gold', 'precious metal', 'silver'],
        'inflation': ['inflation', 'cpi', 'pce', 'price pressure'],
        'fed_policy': ['fed', 'fomc', 'monetary policy', 'rate hike', 'rate cut'],
        'tariff': ['tariff', 'trade war', 'sanction', 'export control'],
        'geopolitical': ['geopolitical', 'conflict', 'war', 'iran', 'china tension'],
        'earnings': ['earnings', 'revenue beat', 'eps beat', 'guidance'],
        'vix_spike': ['vix', 'volatility', 'fear'],
    }
    for driver, keywords in driver_keywords.items():
        if any(kw in text_lower for kw in keywords):
            synopsis.macro_drivers.append(driver)

    # ── Detect outperformers/underperformers ──
    # Look for "outperformers included X, Y, Z" or "laggards included X, Y"
    outperf_match = re.search(
        r'(?:outperform|relative strength|leading)[^.]*?(?:included|were)\s+([^.]{10,200})',
        section, re.IGNORECASE
    )
    if outperf_match:
        items = re.findall(r'([a-zA-Z][a-zA-Z &/]+)', outperf_match.group(1))
        synopsis.outperformers = [i.strip() for i in items[:10] if len(i.strip()) > 2]

    underperf_match = re.search(
        r'(?:lagg|underperform|weak)[^.]*?(?:included|were)\s+([^.]{10,200})',
        section, re.IGNORECASE
    )
    if underperf_match:
        items = re.findall(r'([a-zA-Z][a-zA-Z &/]+)', underperf_match.group(1))
        synopsis.underperformers = [i.strip() for i in items[:10] if len(i.strip()) > 2]

    return synopsis


def parse_gainers(text: str) -> List[TickerImpact]:
    """Parse 'Notable Gainers:' section.

    Entry format: +18.7% ~AMPX-US~ (Amprius Technologies): reason text...
    """
    section = _extract_section(
        text,
        r'Notable Gainers:',
        [r'Notable Decliners:', r'TOP STORIES', r'ASIA PACIFIC', r'EUROPE']
    )
    if not section:
        return []

    return _parse_ticker_entries(section, positive=True)


def parse_decliners(text: str) -> List[TickerImpact]:
    """Parse 'Notable Decliners:' section.

    Entry format: -5.2% ~MRVL-US~ (Marvell Technology): reason text...
    """
    section = _extract_section(
        text,
        r'Notable Decliners:',
        [r'TOP STORIES', r'ASIA PACIFIC', r'EUROPE', r'Afternoon Headlines',
         r'StreetAccount Event', r'Risk off']
    )
    if not section:
        return []

    return _parse_ticker_entries(section, positive=False)


def _parse_ticker_entries(section: str, positive: bool = True) -> List[TickerImpact]:
    """Parse gainer/decliner entries from a section.

    Handles both FactSet ticker format and percentage patterns.
    """
    entries = []

    # Primary pattern: +18.7% ~AMPX-US~ (Company Name): reason...
    # Note: in the PDF text, spacing may vary due to PDF extraction
    pattern = re.compile(
        r'([+-]?\d+\.?\d*)%\s*~([A-Z]{1,5})-[A-Z]{2}~'
        r'\s*\(([^)]+)\):\s*(.*?)(?=\n[+-]?\d+\.?\d*%\s*~|\Z)',
        re.DOTALL
    )

    for m in pattern.finditer(section):
        try:
            pct = float(m.group(1))
            ticker = m.group(2).strip()
            company = m.group(3).strip()
            reason = m.group(4).strip().replace('\n', ' ')[:300]

            entries.append(TickerImpact(
                ticker=ticker,
                company=company,
                change_pct=round(pct, 2),
                reason=reason,
            ))
        except (ValueError, IndexError):
            continue

    # If primary pattern found nothing, try looser pattern
    if not entries:
        loose_pattern = re.compile(
            r'([+-]?\d+\.?\d*)%\s+~?([A-Z]{1,5})[-~]',
            re.DOTALL
        )
        for m in loose_pattern.finditer(section):
            try:
                pct = float(m.group(1))
                ticker = m.group(2).strip()
                if len(ticker) >= 2:
                    entries.append(TickerImpact(
                        ticker=ticker,
                        change_pct=round(pct, 2),
                    ))
            except ValueError:
                continue

    # Extract timestamps if present
    ts_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)', section)
    if ts_match:
        for entry in entries:
            entry.timestamp = ts_match.group(1)

    return entries


def parse_top_stories(text: str) -> List[TopStory]:
    """Parse narrative TOP STORIES sections.

    Splits by timestamp boundaries, extracts headline from first line.
    """
    # Find major story sections with timestamps
    stories = []

    # Pattern: headline followed by timestamp on next line
    # Stories are separated by timestamps like "3/5/2026 3:38:05 PM"
    story_pattern = re.compile(
        r'([^\n]{20,200}):\s*\n'
        r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s*\n'
        r'(.*?)(?=\n[^\n]{20,200}:\s*\n\d{1,2}/\d{1,2}/\d{4}|\nNotable|\nToday\'s Top News|\nStreetAccount Summary|\nEstimat|\nPrice Performance|\Z)',
        re.DOTALL
    )

    for m in story_pattern.finditer(text[:30000]):  # First 30K chars (main content)
        headline = m.group(1).strip()
        timestamp = m.group(2).strip()
        body = m.group(3).strip().replace('\n', ' ')[:500]

        # Skip if headline is too generic or a section header
        if any(skip in headline.lower() for skip in [
            'today\'s top news', 'reference', 'notes:', 'price performance',
            'estimate monitor', 'streetaccount summary - sector'
        ]):
            continue

        # Extract tickers from body
        tickers = list(set(re.findall(r'~([A-Z]{1,5})-[A-Z]{2}~', body + headline)))

        stories.append(TopStory(
            headline=headline,
            body=body,
            tickers_mentioned=sorted(tickers),
            timestamp=timestamp,
        ))

    return stories[:15]  # Cap at 15 stories


def parse_event_preview(text: str) -> List[EventPreview]:
    """Parse 'StreetAccount Event Preview' sections."""
    previews = []

    # Find Event Preview sections
    pattern = re.compile(
        r'StreetAccount Event Preview:\s*([^\n]+)\n'
        r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s*\n'
        r'(.*?)(?=\nStreetAccount|Today\'s Top News|\nAfternoon|\nReference|\Z)',
        re.DOTALL
    )

    for m in pattern.finditer(text):
        event_name = m.group(1).strip()
        timestamp = m.group(2).strip()
        body = m.group(3).strip()

        # Try to extract consensus from body
        consensus = ''
        cons_match = re.search(
            r'(?:consensus|expected|forecast)[^.]*?([+-]?\d+\.?\d*%?[^.]{0,50})',
            body, re.IGNORECASE
        )
        if cons_match:
            consensus = cons_match.group(1).strip()[:100]

        previews.append(EventPreview(
            event_name=event_name,
            consensus=consensus,
            narrative=body.replace('\n', ' ')[:300],
            timestamp=timestamp,
        ))

    return previews[:5]


def parse_estimates(text: str) -> List[EstimateRevision]:
    """Parse 'Estimate Monitor' table.

    Row format in PDF text (after extraction):
    AVGO-USA Broadcom Inc. Melius Research, Ben Reitzes Target Price 575.00 530.00 8.49%

    This is complex because PDF extraction may break columns across lines.
    We use a multi-strategy approach.
    """
    # Find the Estimate Monitor section
    section = _extract_section(
        text,
        r'Estimat(?:e\s+)?Monitor',
        [r'StreetAccount\s+W', r'StreetAccount\s+Sector', r'World News', r'Notable events']
    )
    if not section:
        return []

    revisions = []

    # Strategy 1: Look for TICKER-USA/US patterns followed by numeric values
    # Pattern: TICKER-USA CompanyName BrokerName, AnalystName Item Value PrevValue Revision%
    lines = section.split('\n')
    current_ticker = ''
    current_company = ''

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect ticker line: "AVGO-USA Broadcom Inc." or "AVGO-USA Broadcom\nInc."
        ticker_match = re.match(r'([A-Z]{1,5})-US[A ]?\s+(.+)', line)
        if ticker_match:
            current_ticker = ticker_match.group(1)
            current_company = ticker_match.group(2).strip()
            # Sometimes company name has the rest on same line
            continue

        # Detect revision data: "Target Price 575.00 530.00 8.49%"
        # or "FY '26 EPS 11.53 10.20 13.04%"
        rev_match = re.search(
            r'(Target\s+Price|(?:FY|Q\d)\s+\'\d{2}\s+(?:EPS|Sales|EBITDA|Same\s+Store)|Rating)\s+'
            r'([\d,.]+)\s+([\d,.]+)\s+([+-]?[\d.]+)%',
            line
        )
        if rev_match and current_ticker:
            try:
                item = rev_match.group(1).strip()
                value = float(rev_match.group(2).replace(',', ''))
                prev_value = float(rev_match.group(3).replace(',', ''))
                revision_pct = float(rev_match.group(4))

                # Extract broker/analyst if present on the line
                broker = ''
                analyst = ''
                broker_match = re.search(r'([A-Z][a-zA-Z &]+(?:Securities|Research|Capital|Partners|Cowen|ISI|Blair|James|Fargo|Stanley)?)\s*,\s*\n?\s*([A-Z][a-z]+ [A-Z][a-z]+)', line)
                if broker_match:
                    broker = broker_match.group(1).strip()
                    analyst = broker_match.group(2).strip()

                revisions.append(EstimateRevision(
                    ticker=current_ticker,
                    company=current_company,
                    broker=broker,
                    analyst=analyst,
                    item=item,
                    value=value,
                    prev_value=prev_value,
                    revision_pct=round(revision_pct, 2),
                ))
            except (ValueError, IndexError):
                continue

        # Rating changes: "Rating Hold Buy undefined" or "Rating Buy Hold"
        rating_match = re.search(r'Rating\s+(Buy|Hold|Sell)\s+(Buy|Hold|Sell)', line)
        if rating_match and current_ticker:
            revisions.append(EstimateRevision(
                ticker=current_ticker,
                company=current_company,
                item=f'Rating: {rating_match.group(2)} -> {rating_match.group(1)}',
            ))

    return revisions


def parse_sectors(text: str) -> List[SectorSnapshot]:
    """Parse 'Sector Highlights' section.

    Format: "Energy outperforming, with the S&P Energy Index +0.65%:"
    Also handles: (1.23%) parenthesized negative convention
    """
    section = _extract_section(
        text,
        r'Sector Highlights',
        [r'World News', r'Notable events', r'\Z']
    )
    if not section:
        return []

    snapshots = []

    # Known sector names in FactSet reports
    sector_names = [
        'Energy', 'Tech', 'Financials', 'Healthcare', 'Industrials',
        'Materials', 'Consumer Disc', 'Consumer Staples', 'Utilities',
        'Real Estate', 'Communication',
    ]

    # Pattern: "Energy outperforming, with the S&P Energy Index +0.65%:"
    # or "Healthcare underperforming, with the S&P Healthcare Index (1.97%):"
    for sector in sector_names:
        pattern = re.compile(
            rf'({re.escape(sector)})\s+'
            rf'(outperforming|underperforming|mixed|leading|lagging)[^:]*?'
            rf'(?:Index\s+)?([+-]?\(?\d+\.?\d*\)?%)',
            re.IGNORECASE
        )
        m = pattern.search(section)
        if m:
            status_word = m.group(2).lower()
            pct_str = m.group(3).replace('(', '-').replace(')', '').replace('%', '')
            try:
                pct = float(pct_str)
            except ValueError:
                pct = None

            status = 'outperforming' if status_word in ('outperforming', 'leading') else \
                     'underperforming' if status_word in ('underperforming', 'lagging') else 'neutral'

            # Extract narrative: text between this sector and the next
            sector_start = m.start()
            next_sector = None
            for other in sector_names:
                if other == sector:
                    continue
                nm = re.search(
                    rf'{re.escape(other)}\s+(?:outperforming|underperforming|mixed|leading|lagging)',
                    section[sector_start + 50:], re.IGNORECASE
                )
                if nm:
                    if next_sector is None or nm.start() < next_sector:
                        next_sector = nm.start()

            narrative_end = (sector_start + 50 + next_sector) if next_sector else (sector_start + 500)
            narrative = section[sector_start:narrative_end].replace('\n', ' ')[:200]

            snapshots.append(SectorSnapshot(
                sector=sector,
                index_pct=round(pct, 3) if pct is not None else None,
                status=status,
                narrative=narrative,
            ))

    return snapshots
