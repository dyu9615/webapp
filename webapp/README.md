# QuantAlpha — 机构量化交易平台

## Project Overview
- **Name**: QuantAlpha
- **Goal**: 机构级量化交易平台，集成宏观监控、选股筛选、深度分析、机器学习信号、回测引擎、交易管理
- **Architecture**: 8-Station Microservices (9 独立服务解耦)

## Microservices Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                    Frontend (Port 3000)                             │
│     Hono + Wrangler Pages · UI + Mock Data + 8-Station Proxy       │
└──┬─────┬─────┬─────┬──────┬──────┬──────┬──────┬──────────────────┘
   │     │     │     │      │      │      │      │
   ▼     ▼     ▼     ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
│ 3001 ││ 3002 ││ 3003 ││ 3004 ││ 3005 ││ 3006 ││ 3007 ││ 3008 │
│ Data ││Screen││ News ││ Deep ││  ML  ││Backt.││Trade ││ Perf │
│Center││  er  ││ Stn  ││Analys││Engine││Engine││  ing ││ ance │
└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘
```

### Service Details

| Service | Port | Type | Responsibilities |
|---------|------|------|------------------|
| **Frontend** | 3000 | Hono/Wrangler | UI, mock data, 8-station API gateway/proxy |
| **Data Center** | 3001 | Flask | Yahoo Finance, FactSet Excel, Bloomberg Archive (I/O-bound) |
| **Screener** | 3002 | Flask | Five-factor scoring + Gold Standard Cross Check |
| **News Station** | 3003 | Flask | NLP Sentiment Scoring + RSS aggregation |
| **Deep Analysis** | 3004 | Flask | 3-Layer + Non-GAAP + Cross Check |
| **ML Engine** | 3005 | Flask | Validation Gate + Signal Inference |
| **Backtest Engine** | 3006 | Flask | PIT-compliant historical replay |
| **Trading** | 3007 | Flask | Positions + Orders |
| **Performance** | 3008 | Flask | Sharpe/Sortino/VaR/Attribution |

## FactSet Data Integration (NEW)

### Ingested Stocks (Real FactSet Excel Snapshots)

| Ticker | Price | Market Cap | P/E | EV/EBITDA | Beta | Target | Rating |
|--------|-------|-----------|-----|-----------|------|--------|--------|
| **NVDA** | $183.04 | $4,448B | 37.3x | 33.2x | 1.76 | $267.52 | Buy (1.35) |
| **AAPL** | $262.52 | $3,854B | 33.2x | 25.2x | 1.07 | $298.87 | Overweight (1.50) |
| **MSFT** | $405.20 | $3,009B | 25.4x | 16.1x | 1.01 | $594.91 | Buy (1.12) |
| **JPM**  | $299.39 | $807B | 15.0x | N/A | 1.11 | $352.17 | Overweight (1.47) |
| **DELL** | $147.10 | $97.5B | 16.9x | N/A | 1.63 | $164.32 | Overweight (1.38) |

### Data Flow
```
FactSet Excel (.xlsx) → ingest_factset_excel.py → bloomberg_archive.db
                                                       ↓
Station 3001 (/api/dc/factset-snapshot/:ticker) → Hono Proxy → Frontend UI
```

### Key FactSet API Endpoints
- `GET /api/dc/factset-snapshot/:ticker` — Individual FactSet Excel snapshot
- `GET /api/dc/factset-snapshots` — List all ingested FactSet snapshots
- `POST /api/dc/csv-upload` — Bloomberg/FactSet CSV import with auto-mapping

## Quick Start

```bash
cd /home/user/webapp

# Install dependencies
npm install
pip3 install -r data_service/requirements.txt

# Ingest FactSet Excel data
python3 ingest_factset_excel.py

# Build frontend
npm run build

# Start all 9 services
pm2 start ecosystem.config.cjs

# Check status
pm2 list

# Check gateway health (all 8 backends)
curl http://localhost:3000/api/live/gateway/health

# Test FactSet data
curl http://localhost:3000/api/dc/factset-snapshots
curl http://localhost:3000/api/dc/factset-snapshot/NVDA
```

## API Endpoints

### Frontend (Port 3000) — Proxy Gateway
- `GET /api/live/gateway/health` — Aggregate health of all 8 backends
- `GET /api/dc/factset-snapshot/:ticker` — FactSet Excel snapshot (proxy)
- `GET /api/dc/factset-snapshots` — List all FactSet snapshots (proxy)
- `GET /api/live/quote/:ticker` → Data Service
- `GET /api/live/screener` → Screener
- `GET /api/live/deep/:ticker` → Deep Analysis
- `GET /api/live/ml/signal/:ticker` → ML Engine
- `GET /api/live/backtest/strategies` → Backtest Engine
- `GET /api/live/trading/positions` → Trading Service
- `GET /api/live/trading/performance` → Performance Service

### Data Service (Port 3001) — Single Source of Truth
- `GET /api/health` — Service health + archive stats
- `GET /api/dc/factset-snapshot/:ticker` — FactSet Excel snapshot data
- `GET /api/dc/factset-snapshots` — List all FactSet snapshots
- `GET /api/dc/gold-standard/:ticker` — Gold Standard data for Cross Check
- `GET /api/dc/snapshot/:ticker` — ML Validation Gate data
- `POST /api/dc/csv-upload` — Bloomberg CSV import with auto-mapping
- `GET /api/yf/quote/:ticker` — Yahoo Finance quote
- `GET /api/yf/macro` — VIX, Treasury yields

## Tech Stack
- **Frontend**: Hono + TypeScript + TailwindCSS (CDN) + Chart.js
- **Backend**: Python Flask x 8 microservices
- **Data Sources**: Yahoo Finance, FRED, CBOE, FactSet (Excel), Bloomberg (Local Archive)
- **Database**: SQLite (bloomberg_archive.db — factset_snapshots + bbg_reference_archive)
- **Process Manager**: PM2

## Project Structure

```
webapp/
├── src/
│   ├── index.tsx           # Hono frontend + 8-station proxy gateway
│   ├── renderer.tsx        # HTML renderer
│   └── data/
│       ├── mockData.ts     # Strategy/trading mock data
│       └── usMarketData.ts # SP500 universe (FactSet-updated)
├── public/static/
│   ├── app.js              # Frontend JavaScript (~7000 lines)
│   └── styles.css          # Custom CSS
├── data_service/           # Port 3001 — I/O bound data service
│   ├── app.py              # Flask app + FactSet snapshot endpoints
│   ├── bloomberg_archive.db # SQLite: factset_snapshots + bbg_reference
│   ├── storage.py          # DB initialization
│   └── modules/            # Domain modules (4 loaded)
├── screener_service/       # Port 3002 — Screener
├── news_service/           # Port 3003 — News intelligence
├── deep_analysis_service/  # Port 3004 — Deep analysis
├── ml_service/             # Port 3005 — ML engine
├── backtest_service/       # Port 3006 — Backtest engine
├── trading_service/        # Port 3007 — Trading
├── performance_service/    # Port 3008 — Performance analytics
├── ingest_factset_excel.py # FactSet Excel → SQLite ingestion script
├── ecosystem.config.cjs    # PM2 config for all 9 services
├── package.json
├── vite.config.ts
├── wrangler.jsonc
└── README.md
```

## Deployment Status
- **Platform**: Sandbox (PM2-managed)
- **GitHub**: https://github.com/dyu9615/US-stock-platform.git
- **Status**: All 9 services operational (8 backends + 1 frontend)
- **FactSet Data**: 5 stocks ingested (AAPL, DELL, JPM, MSFT, NVDA)
- **Last Updated**: 2026-03-05
