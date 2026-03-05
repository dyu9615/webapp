/**
 * QuantAlpha — PM2 Ecosystem Configuration (8-Station Architecture)
 * ══════════════════════════════════════════════════════════════════
 * 9 independent microservices (Frontend + 8 Backend Stations):
 *
 *   Port 3000 — Hono Frontend       (UI + mock data + 8-station proxy gateway)
 *   Port 3001 — Data Center          (唯一真理来源 — Bloomberg/YF/FactSet)
 *   Port 3002 — Screener             (广度筛选 + Gold Standard Cross Check)
 *   Port 3003 — News Station         (情报中心 — NLP Sentiment Scoring)
 *   Port 3004 — Deep Analysis        (个股深钻 — 3-Layer + Non-GAAP + Cross Check)
 *   Port 3005 — ML Engine            (智能大脑 — Validation Gate + Signal Inference)
 *   Port 3006 — Backtest Engine      (策略仿真 — PIT Compliant Replay)
 *   Port 3007 — Trading              (实盘管理 — Positions + Orders)
 *   Port 3008 — Performance          (审计归因 — Sharpe/Sortino/VaR/Attribution)
 *
 * 一键启动: cd /home/user/webapp && pm2 start ecosystem.config.cjs
 * 查看日志: pm2 logs --nostream
 * 重启全部: pm2 restart all
 */

module.exports = {
  apps: [
    // ═══════════════════════════════════════════════════════════════════
    // Frontend: Hono + Wrangler (port 3000)
    // UI + Mock Data + 8-Station API Gateway Proxy
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'quant-frontend',
      script: 'npx',
      args: 'wrangler pages dev dist --ip 0.0.0.0 --port 3000',
      cwd: '/home/user/webapp',
      env: { NODE_ENV: 'development', PORT: 3000 },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3001: Data Center (唯一真理来源) — I/O bound
    // Bloomberg Archive + Yahoo Finance + FactSet + Macro
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3001-datacenter',
      script: 'python3',
      args: 'data_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3002: Screener (广度筛选) — CPU bound
    // Five-factor scoring + Gold Standard Cross Check
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3002-screener',
      script: 'python3',
      args: 'screener_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3003: News Station (情报中心) — I/O bound
    // RSS aggregation + NLP Sentiment Scoring
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3003-news',
      script: 'python3',
      args: 'news_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3004: Deep Analysis (个股深钻) — CPU bound
    // 3-Layer architecture + Non-GAAP + Cross Check
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3004-deepanalysis',
      script: 'python3',
      args: 'deep_analysis_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3005: ML Engine (智能大脑) — CPU bound
    // Validation Gate + Model Registry + Signal Inference
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3005-mlengine',
      script: 'python3',
      args: 'ml_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3006: Backtest Engine (策略仿真) — CPU bound
    // PIT-compliant historical replay + strategy testing
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3006-backtest',
      script: 'python3',
      args: 'backtest_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3007: Trading (实盘管理) — CPU bound
    // Position management + order execution log
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3007-trading',
      script: 'python3',
      args: 'trading_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },

    // ═══════════════════════════════════════════════════════════════════
    // Station 3008: Performance (审计归因) — CPU bound
    // Sharpe/Sortino/VaR + strategy attribution + research library
    // ═══════════════════════════════════════════════════════════════════
    {
      name: 'stn-3008-performance',
      script: 'python3',
      args: 'performance_service/app.py',
      cwd: '/home/user/webapp',
      env: { PYTHONUNBUFFERED: '1' },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
      restart_delay: 3000,
    },
  ]
}
