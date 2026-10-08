import React, { useState, useEffect, useRef, useCallback } from 'react'
import { createChart, CrosshairMode } from 'lightweight-charts'
import {
  Activity, Settings, BarChart2, TrendingUp, TrendingDown, Shield,
  Wifi, WifiOff, RefreshCw, Play, Square, AlertCircle,
  ChevronDown, CheckCircle, XCircle, Clock, Zap, BookOpen, Send, Cpu,
  Moon, Sun, LayoutDashboard, Globe, ChevronLeft, ChevronRight, Menu,
  Calendar, Target, Trash2, Filter, Code2, Sparkles, Briefcase, LogOut, Lock
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as ReTooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'
import ResearchStudio from './components/ResearchStudio.jsx'

// ── API helpers ──────────────────────────────────────────────────────────────
const API = {
  get:  (url)       => fetch(url).then(r => r.json()),
  post: (url, body) => fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) }).then(r => r.json()),
}

// ── Live Trading strategies ────────────────────────────────────────────────
// Alpha Combo (CUSUM 1.25 Tuned) is the DEFAULT (index 0) -- promoted
// 2026-08-22 after beating the CUSUM 1.5 baseline and every dual-engine
// pyramid variant tried this research session under train/validate/full
// discipline. Time-Gated Alpha Combo (index 1) is Alpha Combo plus an
// entry-time filter, promoted 2026-08-23 after the same discipline plus a
// deep trade-level audit -- BankNifty-only validation, see
// STRATEGY_REGISTRY.md at the repo root. The other 3 are the research
// strategies promoted earlier. regime_trend_range / multi_agent remain
// selectable on the Backtest page, just no longer live. Single source of
// truth for every dropdown/label surface that shows live strategy options.
const LIVE_STRATEGY_OPTIONS = [
  { v: 'custom_alpha_combo_cusum125', l: 'Alpha Combo (CUSUM 1.25)' },
  { v: 'custom_time_gated_alpha_combo', l: 'Time-Gated Alpha Combo' },
  { v: 'custom_regime_v1_trend_range_final', l: 'Regime T/R V1 Final' },
  { v: 'custom_option_b_ram_rf', l: 'Option B: Ram > Range Filter' },
  { v: 'custom_cusum15_nodonchian_cd8', l: 'CUSUM 1.5 (No Donchian)' },
]
const LIVE_STRATEGY_LABELS = Object.fromEntries(LIVE_STRATEGY_OPTIONS.map(o => [o.v, o.l]))
// Full list of live strategy ids, for grids/panels that should show EVERY
// live strategy (Strategy Agreement, Secondary Strategy Signal, etc.) --
// always derived from LIVE_STRATEGY_OPTIONS so a future strategy swap there
// (add/remove/replace) automatically shows up everywhere without having to
// remember a second list. Do NOT filter this one for "doesn't have X data"
// reasons -- that caused a real bug 2026-10-01 (Option B silently vanished
// from every grid that iterated this list, not just its regime badge, when
// it was filtered out here instead of from NO_REGIME_STRATEGY_IDS below).
const LIVE_STRATEGY_IDS = LIVE_STRATEGY_OPTIONS.map(o => o.v)
// Subset with NO regime classifier (TRENDING_UP/DOWN/SIDEWAYS, confidence,
// playbook) -- used ONLY to suppress that one badge for strategies that
// can't produce it, never to hide them from a strategy list/grid. Option B
// (custom_option_b_ram_rf, replaced HalfTrend+Hull here 2026-10-01) is a
// plain Ram/Range-Filter combo with no regime classifier at all. Add future
// non-regime-based strategies here, not by removing them from
// LIVE_STRATEGY_IDS above.
const NO_REGIME_STRATEGY_IDS = new Set(['custom_option_b_ram_rf'])

// ── Theme Hook ──────────────────────────────────────────────────────────────
function useTheme() {
  const [theme, setThemeState] = useState(() => {
    const cached = localStorage.getItem('app-theme')
    return cached === 'dark' ? 'dark' : 'light'
  })
  
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('app-theme', theme)
  }, [theme])

  const setTheme = (t) => setThemeState(t)
  const toggle = () => setThemeState(p => p === 'light' ? 'dark' : 'light')
  return { theme, setTheme, toggle }
}

function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < breakpoint)
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [breakpoint])
  return isMobile
}

// chart-aware colors (lightweight-charts needs hex)
const chartColors = (theme) => theme === 'dark' ? {
  bg: '#111525', text: '#636882', grid: '#1e2235',
  up: '#22c55e', down: '#ef4444',
} : {
  bg: '#ffffff', text: '#6b7188', grid: '#ebedf3',
  up: '#10b981', down: '#ef4444',
}

// ── Utility ─────────────────────────────────────────────────────────────────
const V = (name) => `var(--${name})`
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-IN', {maximumFractionDigits:2})
// Signed rupee amount. Negative amounts used to lose their minus sign (only the red
// colour showed a loss): -45 was shown as "₹45".
const fmtPnl = n => n == null ? '—' : (n>=0?'+':'-') + '₹' + Math.abs(n).toLocaleString('en-IN',{maximumFractionDigits:0})
const clr = n => n > 0 ? V('green') : n < 0 ? V('red') : V('text-muted')

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  componentDidCatch(error, info) { console.error('React Error:', error, info) }
  render() {
    if (this.state.hasError) return <div style={{color:V('red'),padding:20,background:V('bg-primary')}}>
      <h3>Component Error</h3><pre style={{fontSize:11}}>{this.state.error?.toString()}</pre>
      <button onClick={()=>this.setState({hasError:false})} style={{marginTop:8,color:V('accent'),cursor:'pointer',background:'none',border:'none'}}>Retry</button>
    </div>
    return this.props.children
  }
}

// IST time formatter
const toIST = (dateStr) => {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false })
  } catch { return '' }
}
// Signal times: time only for today, date + time otherwise -- a "15:25:00" left
// over from yesterday must not read as a fresh signal.
const toISTSignalTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const day = x => x.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' })
    return day(d) === day(new Date()) ? toIST(dateStr) : toISTDateTime(dateStr)
  } catch { return '' }
}
const toISTDateTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', hour12:false })
  } catch { return '' }
}

// ── Sub-components ───────────────────────────────────────────────────────────

function Badge({ label, color }) {
  const c = color || V('accent')
  return <span style={{
    background: `color-mix(in srgb, ${c} 12%, transparent)`,
    color: c, border:`1px solid color-mix(in srgb, ${c} 25%, transparent)`,
    borderRadius:20, padding:'2px 10px', fontSize:11, fontWeight:600, whiteSpace:'nowrap',
  }}>{label}</span>
}

function Card({ children, style={}, className='', ...rest }) {
  return <div className={`fade-in ${className}`} style={{
    background: V('bg-secondary'), border:`1px solid ${V('border')}`,
    borderRadius: V('radius-lg'), padding:18,
    boxShadow: V('shadow-sm'), transition:'box-shadow 0.2s',
    ...style
  }} {...rest}>{children}</div>
}

function MetricBox({ label, value, color, sub }) {
  const c = color || V('text-primary')
  return (
    <div style={{
      background: V('bg-tertiary'), border:`1px solid ${V('border-light')}`,
      borderRadius: V('radius-md'), padding:'14px 16px',
    }}>
      <div style={{ color:V('text-muted'), fontSize:10, textTransform:'uppercase', letterSpacing:'0.1em', marginBottom:6, fontWeight:500 }}>{label}</div>
      <div style={{ color:c, fontSize:20, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", lineHeight:1.2 }}>{value}</div>
      {sub && <div style={{ color:V('text-muted'), fontSize:10, marginTop:4 }}>{sub}</div>}
    </div>
  )
}

// ── Piotroski / Altman "why" references ─────────────────────────────────────
// Both scores' captions in the UI say "some weaker checks" / "grey zone" —
// these turn the already-computed checks/components dicts into the specific
// named reasons behind that, instead of leaving the caution unexplained.
const PIOTROSKI_CHECK_LABELS = {
  positive_net_income: 'net income',
  positive_operating_cashflow: 'operating cash flow',
  roa_improved: 'return on assets (YoY)',
  cfo_exceeds_net_income: 'cash flow vs. net income',
  leverage_decreased: 'leverage (debt ratio)',
  current_ratio_improved: 'current ratio (liquidity)',
  no_new_shares_issued: 'share capital (dilution)',
  operating_margin_improved: 'operating margin (YoY)',
  gross_margin_improved: 'gross margin (YoY)',
  asset_turnover_improved: 'asset turnover (YoY)',
}

function piotroskiFailedChecks(checks) {
  if (!checks) return []
  return Object.entries(checks).filter(([, pass]) => pass === false).map(([key]) => PIOTROSKI_CHECK_LABELS[key] || key)
}

// "How to read these numbers" — plain-language guide under the ratio tiles.
// Rule-of-thumb bands are generic; sector matters a lot (IT/FMCG usually trade
// at higher P/E than banks/oil/PSU), which the text says so users don't treat
// them as hard pass/fail lines. The per-company "reading" is deterministic.
function medianOf(nums) {
  const a = nums.filter(n => Number.isFinite(n)).sort((x, y) => x - y)
  if (!a.length) return null
  const mid = Math.floor(a.length / 2)
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2
}

function ratioGuide(ratios, peers, symbol) {
  const r = ratios || {}
  const peerPEs = (peers || []).filter(p => p.Name !== symbol).map(p => parseFloat(String(p.PE).replace(/,/g, ''))).filter(n => n > 0)
  const peerMedianPE = medianOf(peerPEs)
  const G = '#22c55e', A = '#f59e0b', R = '#ef4444', N = '#94a3b8'

  const pe = r.pe, pb = r.pb, roe = r.roe, eps = r.eps, opm = r.opm, npm = r.npm
  const items = []

  {
    let tone = N, reading = 'Not available.'
    if (pe != null) {
      if (pe <= 0) { tone = R; reading = 'No positive profit to price against (loss-making or data missing).' }
      else {
        const band = pe < 15 ? 'on the lower side' : pe <= 25 ? 'in the moderate range' : 'on the higher side'
        tone = pe < 15 ? G : pe <= 25 ? A : R
        reading = `${pe} is ${band}.`
        if (peerMedianPE) reading += ` Sector peers' median P/E is ${peerMedianPE.toFixed(1)}, so this stock is priced ${pe < peerMedianPE * 0.9 ? 'cheaper than' : pe > peerMedianPE * 1.1 ? 'richer than' : 'in line with'} its peers.`
      }
    }
    items.push({
      label: 'P/E (Price ÷ Earnings)', tone, reading,
      what: 'How many rupees you pay for every ₹1 of the company\'s yearly profit. P/E of 20 means you pay 20 years\' worth of current profit.',
      range: 'Rough guide: under 15 = cheap-looking, 15–25 = fair for a steady business, above 25 = expensive / high growth expected. Always compare with same-sector companies — IT and FMCG normally trade higher than banks, oil or PSUs.',
      lowHigh: 'Lower: cheaper per rupee of profit, but can mean slow growth or hidden trouble. Higher: market expects strong growth — you pay a premium and there is less room for disappointment.',
    })
  }
  {
    let tone = N, reading = 'Not available.'
    if (pb != null) {
      tone = pb < 1 ? A : pb <= 3 ? G : A
      const band = pb < 1 ? 'below 1 — priced under its net worth (a bargain or a warning sign)' : pb <= 3 ? 'in the normal range' : 'on the higher side'
      reading = `${pb} is ${band}.`
      if (pb > 3 && roe != null && roe >= 20) reading += ` A high P/B is more justifiable here because ROE is strong (${roe}%).`
      else if (pb > 3) reading += ' A high P/B needs strong, sustained returns (ROE) to be justified.'
    }
    items.push({
      label: 'P/B (Price ÷ Book Value)', tone, reading,
      what: 'Share price compared with the company\'s net worth (assets minus debts) per share. P/B of 2 means you pay ₹2 for every ₹1 of net assets on its books.',
      range: 'Rough guide: below 1 = trades under net worth, 1–3 = normal, above 3 = paying up for earning power. Most meaningful for banks, NBFCs and asset-heavy businesses; less so for asset-light ones (IT, brands).',
      lowHigh: 'Lower: cheaper vs assets, but check why the market is doubtful. Higher: market values the business well above its assets — fine if returns (ROE) are high, risky if not.',
    })
  }
  {
    let tone = N, reading = 'Not available.'
    if (roe != null) {
      tone = roe >= 15 ? G : roe >= 10 ? A : R
      reading = `${roe}% is ${roe >= 20 ? 'excellent' : roe >= 15 ? 'good' : roe >= 10 ? 'average' : 'weak'}.`
    }
    items.push({
      label: 'ROE % (Return on Equity)', tone, reading,
      what: 'How much profit the company earns each year for every ₹100 of shareholders\' money invested in it.',
      range: 'Rough guide: below 10% = weak, 10–15% = average, 15–20% = good, above 20% = excellent. Check debt too — heavy borrowing can inflate ROE.',
      lowHigh: 'Higher is better: the business turns shareholders\' capital into profit efficiently. Persistently low means capital is not being used well.',
    })
  }
  {
    items.push({
      label: 'EPS (Earnings Per Share)', tone: N, reading: eps != null ? `₹${eps} profit per share over the last 12 months.` : 'Not available.',
      what: 'The company\'s yearly profit divided by its number of shares — the profit "belonging" to one share.',
      range: 'No universal good/bad level — a ₹5 EPS is not "worse" than ₹100. What matters is whether EPS is growing year after year, and how it compares with the share price (that\'s the P/E).',
      lowHigh: 'Rising EPS over time = business growing profits. Falling EPS = earnings shrinking. Do not compare EPS across different companies.',
    })
  }
  {
    let tone = N, reading = 'Not available.'
    if (opm != null) {
      tone = opm >= 20 ? G : opm >= 10 ? A : R
      reading = `${opm}% — ${opm >= 20 ? 'strong' : opm >= 10 ? 'moderate' : 'thin'} margin from core operations.`
    }
    items.push({
      label: 'OPM % (Operating Profit Margin)', tone, reading,
      what: 'Out of every ₹100 of sales, how much is left as profit from the core business, before interest, tax and one-off items.',
      range: 'Depends heavily on sector: trading/retail/oil often 3–10%, manufacturing 10–20%, IT/pharma/FMCG 20–30%+. Compare with peers, and check whether it is stable or improving.',
      lowHigh: 'Higher: strong pricing power / cost control. Lower: thin cushion — small cost increases can wipe out profit.',
    })
  }
  {
    let tone = N, reading = 'Not available.'
    if (npm != null) {
      tone = npm >= 10 ? G : npm >= 5 ? A : R
      reading = `${npm}% — ${npm >= 20 ? 'excellent' : npm >= 10 ? 'good' : npm >= 5 ? 'average' : 'thin'} bottom-line margin.`
    }
    items.push({
      label: 'NPM % (Net Profit Margin)', tone, reading,
      what: 'Out of every ₹100 of sales, how much is finally left as profit after all costs, interest and tax.',
      range: 'Rough guide: below 5% = thin, 5–10% = average, 10–20% = good, above 20% = excellent. Big gap between OPM and NPM usually means heavy interest or tax burden.',
      lowHigh: 'Higher: more of each sale reaches shareholders. Lower: profit is easily hurt by higher costs or interest rates.',
    })
  }
  return items
}

const ALTMAN_COMPONENT_LABELS = {
  x1_working_capital_ratio: 'working capital',
  x2_retained_earnings_ratio: 'retained earnings relative to assets',
  x3_ebit_ratio: 'operating profitability (EBIT)',
  x4_market_value_to_liabilities: 'equity relative to liabilities (leverage)',
  x4_book_equity_to_liabilities: 'equity relative to liabilities (leverage)',
  x5_asset_turnover: 'asset turnover',
}

function altmanWeakPoints(components) {
  if (!components) return []
  return Object.entries(components)
    .filter(([, v]) => typeof v === 'number' && v < 0.15)
    .sort(([, a], [, b]) => a - b)
    .slice(0, 2)
    .map(([key, v]) => `${v < 0 ? 'negative' : 'low'} ${ALTMAN_COMPONENT_LABELS[key] || key}`)
}

// ── Sidebar ──────────────────────────────────────────────────────────────────
function Sidebar({ tab, setTab, theme, toggleTheme, onSettings, onConnect, connected, collapsed, setCollapsed, isMobile, mobileOpen, setMobileOpen }) {
  const navItems = [
    { id:'dashboard',       icon:<LayoutDashboard size={18}/>, label:'Dashboard' },
    { id:'market_ctx',      icon:<Globe size={18}/>,           label:'Market Context' },
    { id:'live',            icon:<Activity size={18}/>,        label:'Live Trading' },
    { id:'auto_monitor',    icon:<Zap size={18}/>,             label:'Auto Trade' },
    { id:'backtest',        icon:<BarChart2 size={18}/>,       label:'Backtest' },
    { id:'strategy_details', icon:<BookOpen size={18}/>,       label:'Strategy Details' },
    { id:'sig_journal',     icon:<TrendingUp size={18}/>,      label:'Strategy Signals Log' },
    { id:'journal',         icon:<BookOpen size={18}/>,        label:'Broker Journal' },
    { id:'performance',     icon:<BarChart2 size={18}/>,       label:'Performance' },
    { id:'research_studio', icon:<Code2 size={18}/>,           label:'Research Studio' },
    { id:'cas',             icon:<AlertCircle size={18}/>,     label:'CAS' },
    { id:'investment',      icon:<Briefcase size={18}/>,       label:'Investment' },
  ]

  const sideW = collapsed ? 64 : 240

  return (
    <>
    {isMobile && mobileOpen && <div onClick={() => setMobileOpen(false)} style={{
      position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', zIndex:19,
    }} />}
    <div style={{
      width: isMobile ? 260 : sideW, minHeight:'100vh', background: V('sidebar-bg'),
      borderRight:`1px solid ${V('sidebar-border')}`, display:'flex', flexDirection:'column',
      position:'fixed', left:0, top:0, zIndex:20, transition: isMobile ? 'transform 0.3s ease' : 'width 0.25s ease',
      overflow:'hidden',
      transform: isMobile ? (mobileOpen ? 'translateX(0)' : 'translateX(-100%)') : 'none',
    }}>
      {/* Brand */}
      <div style={{
        padding: collapsed ? '20px 0' : '20px 20px', display:'flex', alignItems:'center',
        gap:10, borderBottom:`1px solid ${V('sidebar-border')}`, minHeight:64,
        justifyContent: collapsed ? 'center' : 'flex-start',
      }}>
        <div style={{
          width:32, height:32, borderRadius:8,
          background:'linear-gradient(135deg, #4f6ef7, #8b5cf6)',
          display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
        }}>
          <Zap size={16} color="#fff" />
        </div>
        {(!collapsed || isMobile) && <div>
          <div style={{ color:'#fff', fontWeight:800, fontSize:15, lineHeight:1.2 }}>AlgoTrader</div>
          <div style={{ color: V('sidebar-text'), fontSize:10, fontWeight:500 }}>Pro Platform</div>
        </div>}
      </div>

      {/* Nav */}
      <nav style={{ flex:1, padding:'12px 8px', display:'flex', flexDirection:'column', gap:2 }}>
        {navItems.map(item => {
          const active = tab === item.id
          return (
            <button key={item.id} onClick={() => { setTab(item.id); if (isMobile) setMobileOpen(false) }} title={collapsed ? item.label : undefined} style={{
              display:'flex', alignItems:'center', gap:12,
              padding: collapsed ? '10px 0' : '10px 14px',
              justifyContent: collapsed ? 'center' : 'flex-start',
              background: active ? V('sidebar-active-bg') : 'transparent',
              color: active ? V('sidebar-active') : V('sidebar-text'),
              border:'none', borderRadius: V('radius-sm'), cursor:'pointer',
              fontSize:13, fontWeight: active ? 600 : 400, width:'100%',
              transition:'all 0.15s',
            }}
            onMouseEnter={e => { if(!active) e.currentTarget.style.background = V('sidebar-hover-bg') }}
            onMouseLeave={e => { if(!active) e.currentTarget.style.background = 'transparent' }}
            >
              {item.icon}
              {(!collapsed || isMobile) && <span>{item.label}</span>}
            </button>
          )
        })}
      </nav>

      {/* Bottom controls */}
      <div style={{ padding:'12px 8px', borderTop:`1px solid ${V('sidebar-border')}`, display:'flex', flexDirection:'column', gap:2 }}>
        {/* Connection status */}
        <button onClick={onConnect} title={collapsed ? (connected ? 'Connected' : 'Connect') : undefined} style={{
          display:'flex', alignItems:'center', gap:12,
          padding: collapsed ? '10px 0' : '10px 14px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background:'transparent', border:'none', borderRadius: V('radius-sm'),
          color: connected ? V('green') : V('red'), cursor:'pointer', fontSize:12, width:'100%',
        }}>
          {connected ? <Wifi size={16}/> : <WifiOff size={16}/>}
          {(!collapsed || isMobile) && <span style={{fontWeight:500}}>{connected ? 'Connected' : 'Disconnected'}</span>}
        </button>

        {/* Settings */}
        <button onClick={onSettings} title={collapsed ? 'Settings' : undefined} style={{
          display:'flex', alignItems:'center', gap:12,
          padding: collapsed ? '10px 0' : '10px 14px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background:'transparent', color:V('sidebar-text'), border:'none',
          borderRadius: V('radius-sm'), cursor:'pointer', fontSize:13, width:'100%',
        }}>
          <Settings size={16}/> {(!collapsed || isMobile) && <span>Settings</span>}
        </button>

        {/* Logout */}
        <button onClick={async () => {
          try { await fetch('/api/auth/logout', { method:'POST' }) } catch (_) {}
          window.location.reload()
        }} title={collapsed ? 'Log out' : undefined} style={{
          display:'flex', alignItems:'center', gap:12,
          padding: collapsed ? '10px 0' : '10px 14px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background:'transparent', color:V('sidebar-text'), border:'none',
          borderRadius: V('radius-sm'), cursor:'pointer', fontSize:13, width:'100%',
        }}>
          <LogOut size={16}/> {(!collapsed || isMobile) && <span>Log out</span>}
        </button>

        {/* Theme toggle */}
        <button onClick={toggleTheme} title={collapsed ? 'Toggle theme' : undefined} style={{
          display:'flex', alignItems:'center', gap:12,
          padding: collapsed ? '10px 0' : '10px 14px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background:'transparent', color:V('sidebar-text'), border:'none',
          borderRadius: V('radius-sm'), cursor:'pointer', fontSize:13, width:'100%',
        }}>
          {theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}
          {(!collapsed || isMobile) && <span>{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>}
        </button>

        {/* Collapse toggle */}
        {!isMobile && <button onClick={() => setCollapsed(!collapsed)} style={{
          display:'flex', alignItems:'center', justifyContent:'center',
          padding:'8px 0', background:'transparent', color:V('sidebar-text'),
          border:'none', borderRadius: V('radius-sm'), cursor:'pointer', width:'100%',
        }}>
          {collapsed ? <ChevronRight size={16}/> : <ChevronLeft size={16}/>}
        </button>}
      </div>
    </div>
    </>
  )
}

function MobileTopBar({ onMenuClick, title }) {
  return (
    <div style={{
      position:'fixed', top:0, left:0, right:0, height:52, zIndex:30,
      background: V('sidebar-bg'), borderBottom:`1px solid ${V('sidebar-border')}`,
      display:'flex', alignItems:'center', padding:'0 16px', gap:12,
    }}>
      <button onClick={onMenuClick} style={{
        background:'none', border:'none', color:'#fff', cursor:'pointer', padding:4,
      }}>
        <Menu size={22}/>
      </button>
      <div style={{
        width:28, height:28, borderRadius:6,
        background:'linear-gradient(135deg, #4f6ef7, #8b5cf6)',
        display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
      }}>
        <Zap size={14} color="#fff" />
      </div>
      <div style={{ color:'#fff', fontWeight:700, fontSize:14 }}>AlgoTrader</div>
    </div>
  )
}

// ── Page Header ──────────────────────────────────────────────────────────────
function PageHeader({ title, subtitle, children }) {
  return (
    <div style={{
      display:'flex', justifyContent:'space-between', alignItems:'center',
      marginBottom:20, flexWrap:'wrap', gap:12,
    }}>
      <div>
        <h1 style={{ fontSize:22, fontWeight:700, color:V('text-primary'), margin:0 }}>{title}</h1>
        {subtitle && <p style={{ fontSize:13, color:V('text-muted'), marginTop:2 }}>{subtitle}</p>}
      </div>
      {children && <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>{children}</div>}
    </div>
  )
}

// ── Select / Input styled ────────────────────────────────────────────────────
function StyledSelect({ value, onChange, options, style={} }) {
  return (
    <select value={value||''} onChange={onChange} style={{
      background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`,
      borderRadius:V('radius-sm'), padding:'6px 10px', fontSize:12, fontWeight:500,
      cursor:'pointer', outline:'none', ...style
    }}>
      {options.map(o => <option key={o.v||o} value={o.v||o}>{o.l||o}</option>)}
    </select>
  )
}

function StyledButton({ onClick, children, variant='default', disabled, style={} }) {
  const variants = {
    default: { bg:V('bg-tertiary'), border:V('border'), color:V('text-secondary') },
    primary: { bg:V('accent-bg'), border:V('accent'), color:V('accent') },
    success: { bg:V('green-bg'), border:V('green'), color:V('green') },
    danger:  { bg:V('red-bg'), border:V('red'), color:V('red') },
    warning: { bg:V('yellow-bg'), border:V('yellow'), color:V('yellow') },
    purple:  { bg:V('purple-bg'), border:V('purple'), color:V('purple') },
  }
  const v = variants[variant] || variants.default
  return (
    <button onClick={onClick} disabled={disabled} style={{
      display:'flex', gap:6, alignItems:'center', justifyContent:'center',
      background: disabled ? V('bg-tertiary') : v.bg,
      border:`1px solid ${disabled ? V('border') : v.border}`,
      color: disabled ? V('text-muted') : v.color,
      borderRadius:V('radius-sm'), padding:'7px 14px', cursor: disabled?'not-allowed':'pointer',
      fontSize:12, fontWeight:600, transition:'all 0.15s', whiteSpace:'nowrap', ...style
    }}>
      {children}
    </button>
  )
}

// ── Live Chart ───────────────────────────────────────────────────────────────
function LiveChart({ instrument, timeframe, signals, strategy, refreshChart, theme, loading: externalLoading }) {
  const chartRef  = useRef(null)
  const chartInst = useRef(null)
  const candleSer = useRef(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [debugInfo, setDebugInfo] = useState({ strategy: '', markersCount: 0 })
  const cc = chartColors(theme)

  const loadData = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const data = await API.get(`/api/chart/${timeframe}?instrument=${instrument}`)
      if (!data.candles) { setErr('No data'); return }
      const candles = data.candles.map(c => ({
        time: c.time, open: c.open, high: c.high, low: c.low, close: c.close
      }))
      candleSer.current?.setData(candles)
      window.debugCandles = candles
      const markers = (signals || [])
        .filter(s => s.signal && s.signal !== 'HOLD' && s.signal !== 'ERROR' && s.time)
        .map(s => {
          const isLong = s.signal === 'LONG'
          const isShort = s.signal === 'SHORT'
          const isShortExit = s.signal === 'SHORT_EXIT'
          const isLongExit = s.signal === 'LONG_EXIT'
          const isEntry = isLong || isShort
          const isExit = isShortExit || isLongExit
          const p = s.strategy === 'custom_alpha_combo_cusum125' ? 'A' : s.strategy === 'custom_time_gated_alpha_combo' ? 'T' : s.strategy === 'custom_regime_v1_trend_range_final' ? 'V' : s.strategy === 'custom_option_b_ram_rf' ? 'R' : s.strategy === 'custom_halftrend_hull_standalone' ? 'H' : s.strategy === 'custom_cusum15_nodonchian_cd8' ? 'C' : (s.strategy === 'broker_sync' ? 'B' : 'M')
          const up = isLong || isShortExit
          const t = Math.floor(new Date(s.time).getTime()/1000) + 19800
          return {
            time: t,
            position: up ? 'belowBar' : 'aboveBar',
            color: isEntry ? (isLong ? '#06b6d4' : '#e879f9') : '#fbbf24',
            shape: up ? 'arrowUp' : 'arrowDown',
            text: isEntry ? `${p}:${s.signal[0]}` : `${p}:X`,
            size: 0.5,
          }
        })
        .filter(m => !isNaN(m.time) && m.time > 0)
        .sort((a, b) => a.time - b.time)
      window.debugMarkers = markers
      window.debugStrategy = strategy
      setDebugInfo({ strategy, markersCount: markers.length })
      candleSer.current?.setMarkers(markers)
    } catch(e) { setErr('Failed to load chart data') }
    finally { setLoading(false) }
  }, [instrument, timeframe, signals, strategy, refreshChart])

  useEffect(() => {
    if (!chartRef.current) return
    const chart = createChart(chartRef.current, {
      layout:      { background:{ color: cc.bg }, textColor: cc.text },
      grid:        { vertLines:{ color: cc.grid }, horzLines:{ color: cc.grid } },
      crosshair:   { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: cc.grid },
      timeScale:   { borderColor: cc.grid, timeVisible: true, secondsVisible: false },
      width:  chartRef.current.offsetWidth,
      height: window.innerWidth < 768 ? 320 : 520,
    })
    candleSer.current = chart.addCandlestickSeries({
      upColor: cc.up, downColor: cc.down,
      borderUpColor: cc.up, borderDownColor: cc.down,
      wickUpColor: cc.up, wickDownColor: cc.down,
    })
    chartInst.current = chart
    const ro = new ResizeObserver(() => chart.applyOptions({ width: chartRef.current?.offsetWidth || 800 }))
    ro.observe(chartRef.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [theme])

  useEffect(() => {
    const handleCandleUpdate = (e) => {
      if (candleSer.current && e.detail) {
        candleSer.current.update(e.detail)
      }
    }
    window.addEventListener('candle_update', handleCandleUpdate)
    return () => window.removeEventListener('candle_update', handleCandleUpdate)
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <Card style={{ padding:0, overflow:'hidden' }}>
      <div style={{
        padding:'14px 18px', borderBottom:`1px solid ${V('border')}`,
        display:'flex', justifyContent:'space-between', alignItems:'center',
      }}>
        <div style={{ display:'flex', gap:10, alignItems:'center' }}>
          <BarChart2 size={16} style={{color:V('accent')}} />
          <span style={{ color:V('text-primary'), fontWeight:600, fontSize:14 }}>{instrument}</span>
          <Badge label={timeframe === 'DAY' ? 'Daily' : `${timeframe} Min`} color={V('accent')} />
          {debugInfo.markersCount > 0 && <Badge label={`${debugInfo.markersCount} signals`} color={V('green')} />}
          {externalLoading && debugInfo.markersCount === 0 && <span style={{ color:V('text-muted'), fontSize:11 }}>Loading signals...</span>}
        </div>
        <button onClick={loadData} style={{
          background:'none', border:'none', color:V('text-muted'), cursor:'pointer', padding:4,
        }}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      </div>
      {err && <div style={{ padding:16, color:V('red'), textAlign:'center', fontSize:13 }}>{err}</div>}
      <div ref={chartRef} style={{ width:'100%' }} />
    </Card>
  )
}

// ── Signal Panel (Regime-aware) ─────────────────────────────────────────────
function SignalPanel({ signal, onManualTrade, position, connected, ltp, lastEntry }) {
  const m = window.innerWidth < 768
  if (!signal || !signal.signal) return null
  const sig = signal.signal
  const isEntry = sig === 'LONG' || sig === 'SHORT'
  const isExit  = sig === 'LONG_EXIT' || sig === 'SHORT_EXIT'
  const isHold  = sig === 'HOLD'
  const isRegime = LIVE_STRATEGY_IDS.includes(signal.strategy) && !NO_REGIME_STRATEGY_IDS.has(signal.strategy)
  const showActiveEntry = isHold && lastEntry && (lastEntry.signal === 'LONG' || lastEntry.signal === 'SHORT')
  const displaySig = showActiveEntry ? lastEntry.signal : sig
  const sigColor = displaySig==='LONG'||displaySig==='LONG_EXIT' ? V('green') : displaySig==='SHORT'||displaySig==='SHORT_EXIT' ? V('red') : V('yellow')
  const sigIcon  = displaySig==='LONG' ? '📈' : displaySig==='SHORT' ? '📉' : (displaySig||'').includes('EXIT') ? '🚪' : '⏸'
  const regimeColor = r => r?.startsWith('TRENDING_UP') ? V('green') : r?.startsWith('TRENDING_DOWN') ? V('red') : r === 'TRANSITION' ? V('yellow') : V('text-muted')

  return (
    <Card style={{ borderLeft:`3px solid ${sigColor}`, height:'100%', boxSizing:'border-box', display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ color:V('text-muted'), fontSize:11, marginBottom:4, fontWeight:500 }}>{LIVE_STRATEGY_LABELS[signal.strategy] || 'Strategy Signal'}</div>
          <div style={{ color:sigColor, fontSize:24, fontWeight:800 }}>{sigIcon} {showActiveEntry ? `${lastEntry.signal} (active)` : sig}</div>
          {signal.time && <div style={{ color:V('text-muted'), fontSize:10, marginTop:2 }}>{toISTSignalTime(signal.time)}</div>}
        </div>
        <div style={{ textAlign:'right' }}>
          <div style={{ color:V('text-primary'), fontSize:20, fontFamily:"'JetBrains Mono', monospace", fontWeight:700 }}>{fmt(ltp || signal.close || signal.entry)}</div>
          <div style={{ color:V('text-muted'), fontSize:10 }}>LTP</div>
        </div>
      </div>

      {isRegime && signal.regime && (
        <div style={{ display:'flex', gap:6, marginTop:10, flexWrap:'wrap', alignItems:'center' }}>
          <Badge label={signal.regime === 'TRENDING_UP' ? '▲ TRENDING UP' : signal.regime === 'TRENDING_DOWN' ? '▼ TRENDING DOWN' : signal.regime === 'TRANSITION' ? '↔ TRANSITION' : '◆ SIDEWAYS'} color={regimeColor(signal.regime)} />
          {signal.regime_confidence > 0 && <span style={{ color:V('text-muted'), fontSize:10 }}>Conf: {(signal.regime_confidence*100).toFixed(0)}%</span>}
          {signal.regime_age > 0 && <span style={{ color:V('text-muted'), fontSize:10 }}>Age: {signal.regime_age} bars</span>}
          {signal.playbook && <Badge label={signal.playbook} color={signal.playbook === 'TREND' ? V('accent') : V('purple')} />}
        </div>
      )}

      {(isEntry || showActiveEntry || (sig === 'HOLD' && signal.entry > 0)) && (() => {
        const d = showActiveEntry ? (lastEntry || {}) : signal
        const labelPrefix = (sig === 'HOLD' && signal.entry > 0) ? 'Possible ' : ''
        return (
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:8, marginTop:12 }}>
          {showActiveEntry && <div style={{ gridColumn:'1/-1', color:V('text-muted'), fontSize:9, textTransform:'uppercase', fontWeight:500 }}>Active {lastEntry.signal} entry:</div>}
          {(sig === 'HOLD' && signal.entry > 0) && <div style={{ gridColumn:'1/-1', color:V('text-muted'), fontSize:9, textTransform:'uppercase', fontWeight:500 }}>Candidate Setup:</div>}
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 12px' }}>
            <div style={{ color:V('text-muted'), fontSize:10 }}>{labelPrefix}Entry</div>
            <div style={{ color:V('text-primary'), fontSize:15, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.entry)}</div>
          </div>
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 12px' }}>
            <div style={{ color:V('text-muted'), fontSize:10 }}>{labelPrefix}Stop Loss</div>
            <div style={{ color:V('red'), fontSize:15, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.sl)}</div>
          </div>
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 12px' }}>
            <div style={{ color:V('text-muted'), fontSize:10 }}>{labelPrefix}T1 / T2</div>
            <div style={{ color:V('green'), fontSize:13, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.target1)} / {fmt(d.target2)}</div>
          </div>
        </div>
      )})()}

      {(isEntry || (sig === 'HOLD' && signal.entry > 0)) && signal.risk_pts > 0 && (
        <div style={{ display:'flex', gap:6, marginTop:8, flexWrap:'wrap' }}>
          <Badge label={`Risk: ${signal.risk_pts.toFixed(0)} pts`} color={V('red')} />
          {signal.rr_t1 > 0 && <Badge label={`RR: ${signal.rr_t1}x`} color={V('cyan')} />}
          {signal.entry_quality > 0 && <Badge label={`Quality: ${(signal.entry_quality*100).toFixed(0)}%`} color={signal.entry_quality > 0.5 ? V('green') : V('yellow')} />}
        </div>
      )}

      <div style={{ display:'flex', gap:6, marginTop:8, flexWrap:'wrap' }}>
        {!isRegime && signal.ml_prob != null && signal.ml_prob > 0 && <Badge label={`ML: ${(signal.ml_prob*100).toFixed(0)}%`} color={signal.ml_prob > 0.6 ? V('green') : V('yellow')} />}
        {signal.weighted_score > 0 && <Badge label={`Score: ${(signal.weighted_score*100).toFixed(0)}%`} color={V('accent')} />}
        {signal.atr_5m > 0 && <Badge label={`ATR: ${signal.atr_5m.toFixed(0)}`} color={V('purple')} />}
        {signal.macro_bias && signal.macro_bias !== 'NEUTRAL' && <Badge label={`Macro: ${signal.macro_bias}`} color={signal.macro_bias==='BULLISH'||signal.macro_bias==='LONG'?V('green'):V('red')} />}
        {!isRegime && signal.adx_1h > 0 && <Badge label={`ADX: ${signal.adx_1h}`} color={V('purple')} />}
      </div>

      {signal.reasons && signal.reasons.length > 0 && (
        <details style={{ marginTop:8 }}>
          <summary style={{ color:V('text-muted'), fontSize:10, cursor:'pointer', fontWeight:500 }}>Strategy Reasons ({signal.reasons.length})</summary>
          <ul style={{ margin:'4px 0 0', paddingLeft:14 }}>
            {signal.reasons.slice(0,6).map((r,i) => <li key={i} style={{ color:V('text-muted'), fontSize:10, marginTop:1 }}>{r}</li>)}
          </ul>
        </details>
      )}

      {signal.veto_reasons && signal.veto_reasons.length > 0 && (
        <div style={{ marginTop:6, background:V('red-bg'), border:`1px solid color-mix(in srgb, ${V('red')} 30%, transparent)`, borderRadius:V('radius-sm'), padding:'4px 8px' }}>
          <div style={{ color:V('red'), fontSize:10, fontWeight:600 }}>Vetoed:</div>
          {signal.veto_reasons.map((r,i) => <div key={i} style={{ color:V('red'), fontSize:10, opacity:0.8 }}>{r}</div>)}
        </div>
      )}

      {connected && isEntry && !position && (
        <div style={{ display:'flex', gap:8, marginTop:12 }}>
          <StyledButton onClick={() => onManualTrade(sig)} variant={sig==='LONG' ? 'success' : 'danger'} style={{ flex:1, padding:'10px 0', fontSize:13 }}>
            {sig==='LONG' ? '▲ Execute LONG' : '▼ Execute SHORT'}
          </StyledButton>
        </div>
      )}
    </Card>
  )
}

// ── Compact Signal Panel ────────────────────────────────────────────────────
function CompactSignalPanel({ signal, strategyLabel, ltp, lastEntry }) {
  const m = window.innerWidth < 768
  if (!signal || !signal.signal) return null
  const sig = signal.signal
  const isRegime = LIVE_STRATEGY_IDS.includes(signal.strategy) && !NO_REGIME_STRATEGY_IDS.has(signal.strategy)
  const isEntry = sig === 'LONG' || sig === 'SHORT'
  const isHold  = sig === 'HOLD'
  const showActiveEntry = isHold && lastEntry && (lastEntry.signal === 'LONG' || lastEntry.signal === 'SHORT')
  const displaySig = showActiveEntry ? lastEntry.signal : sig
  const sigColor = displaySig==='LONG'||displaySig==='LONG_EXIT' ? V('green') : displaySig==='SHORT'||displaySig==='SHORT_EXIT' ? V('red') : V('yellow')
  const sigIcon  = displaySig==='LONG' ? '📈' : displaySig==='SHORT' ? '📉' : (displaySig||'').includes('EXIT') ? '🚪' : '⏸'
  const regimeColor = r => r?.startsWith('TRENDING_UP') ? V('green') : r?.startsWith('TRENDING_DOWN') ? V('red') : r === 'TRANSITION' ? V('yellow') : V('text-muted')
  const ruleScore = signal.weighted_score ? (signal.weighted_score * 100).toFixed(0) : null

  return (
    <Card style={{ borderLeft:`3px solid ${sigColor}`, padding:14, height:'100%', boxSizing:'border-box', display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase', letterSpacing:1, marginBottom:3, fontWeight:500 }}>{strategyLabel}</div>
          <div style={{ color:sigColor, fontSize:20, fontWeight:800 }}>{sigIcon} {showActiveEntry ? `${lastEntry.signal} (active)` : sig}</div>
          {signal.time && <div style={{ color:V('text-muted'), fontSize:9, marginTop:2 }}>{toISTSignalTime(signal.time)}</div>}
        </div>
        <div style={{ textAlign:'right' }}>
          <div style={{ color:V('text-primary'), fontSize:16, fontFamily:"'JetBrains Mono', monospace", fontWeight:700 }}>{fmt(ltp || signal.close || signal.entry)}</div>
          <div style={{ color:V('text-muted'), fontSize:9 }}>LTP</div>
        </div>
      </div>

      {sig === 'HOLD' && signal.reason && (
        <div style={{ color:V('text-muted'), fontSize:11, marginTop:8, lineHeight:'1.4' }}>{signal.reason}</div>
      )}

      {isRegime && signal.regime && (
        <div style={{ display:'flex', gap:5, marginTop:8, flexWrap:'wrap', alignItems:'center' }}>
          <Badge label={signal.regime === 'TRENDING_UP' ? '▲ TRENDING UP' : signal.regime === 'TRENDING_DOWN' ? '▼ TRENDING DOWN' : signal.regime === 'TRANSITION' ? '↔ TRANSITION' : '◆ SIDEWAYS'} color={regimeColor(signal.regime)} />
          {signal.regime_confidence > 0 && <span style={{ color:V('text-muted'), fontSize:9 }}>Conf: {(signal.regime_confidence*100).toFixed(0)}%</span>}
          {signal.regime_age > 0 && <span style={{ color:V('text-muted'), fontSize:9 }}>Age: {signal.regime_age} bars</span>}
          {signal.playbook && <Badge label={signal.playbook} color={signal.playbook === 'TREND' ? V('accent') : V('purple')} />}
        </div>
      )}

      {(isEntry || showActiveEntry || (sig === 'HOLD' && signal.entry > 0)) && (() => {
        const d = showActiveEntry ? lastEntry : signal
        const labelPrefix = (sig === 'HOLD' && signal.entry > 0) ? 'Possible ' : ''
        return (
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:6, marginTop:10 }}>
          {showActiveEntry && <div style={{ gridColumn:'1/-1', color:V('text-muted'), fontSize:9, textTransform:'uppercase' }}>Active {lastEntry.signal} entry:</div>}
          {(sig === 'HOLD' && signal.entry > 0) && <div style={{ gridColumn:'1/-1', color:V('text-muted'), fontSize:9, textTransform:'uppercase' }}>Candidate Setup:</div>}
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
            <div style={{ color:V('text-muted'), fontSize:9 }}>{labelPrefix}Entry</div>
            <div style={{ color:V('text-primary'), fontSize:13, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.entry)}</div>
          </div>
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
            <div style={{ color:V('text-muted'), fontSize:9 }}>{labelPrefix}Stop Loss</div>
            <div style={{ color:V('red'), fontSize:13, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.sl)}</div>
          </div>
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
            <div style={{ color:V('text-muted'), fontSize:9 }}>{labelPrefix}T1 / T2</div>
            <div style={{ color:V('green'), fontSize:11, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmt(d.target1)} / {fmt(d.target2)}</div>
          </div>
        </div>
      )})()}

      {(isEntry || (sig === 'HOLD' && signal.entry > 0)) && (signal.risk_pts > 0 || signal.rr_t1 > 0) && (
        <div style={{ display:'flex', gap:5, marginTop:6, flexWrap:'wrap' }}>
          {signal.risk_pts > 0 && <Badge label={`Risk: ${signal.risk_pts.toFixed(0)} pts`} color={V('red')} />}
          {signal.rr_t1 > 0 && <Badge label={`RR: ${signal.rr_t1}x`} color={V('cyan')} />}
          {signal.entry_quality > 0 && <Badge label={`Quality: ${(signal.entry_quality*100).toFixed(0)}%`} color={signal.entry_quality > 0.5 ? V('green') : V('yellow')} />}
        </div>
      )}

      <div style={{ display:'flex', gap:5, marginTop:8, flexWrap:'wrap' }}>
        {!isRegime && signal.ml_prob != null && <Badge label={`ML: ${(signal.ml_prob*100).toFixed(0)}%`} color={signal.ml_prob > 0.6 ? V('green') : V('yellow')} />}
        {!isRegime && ruleScore != null && <Badge label={`Rule: ${ruleScore}%`} color={Number(ruleScore) > 60 ? V('green') : V('yellow')} />}
        {signal.atr_5m > 0 && <Badge label={`ATR: ${signal.atr_5m.toFixed(0)}`} color={V('purple')} />}
        {signal.macro_bias && signal.macro_bias !== 'NEUTRAL' && <Badge label={`Macro: ${signal.macro_bias}`} color={signal.macro_bias==='BULLISH'||signal.macro_bias==='LONG'?V('green'):V('red')} />}
        {!isRegime && signal.adx_1h > 0 && <Badge label={`ADX: ${signal.adx_1h}`} color={V('purple')} />}
        {isRegime && signal.weighted_score > 0 && <Badge label={`Score: ${(signal.weighted_score*100).toFixed(0)}%`} color={V('accent')} />}
      </div>

      {(() => {
        const reasons = (showActiveEntry && lastEntry?.reasons?.length) ? lastEntry.reasons : signal.reasons
        return reasons && reasons.length > 0 && (
          <details style={{ marginTop:6 }}>
            <summary style={{ color:V('text-muted'), fontSize:9, cursor:'pointer' }}>Strategy Reasons ({reasons.length})</summary>
            <ul style={{ margin:'3px 0 0', paddingLeft:14 }}>
              {reasons.slice(0,5).map((r,i) => <li key={i} style={{ color:V('text-muted'), fontSize:9, marginTop:1 }}>{r}</li>)}
            </ul>
          </details>
        )
      })()}

      {signal.veto_reasons && signal.veto_reasons.length > 0 && (
        <div style={{ marginTop:5, background:V('red-bg'), border:`1px solid color-mix(in srgb, ${V('red')} 30%, transparent)`, borderRadius:V('radius-sm'), padding:'3px 7px' }}>
          <div style={{ color:V('red'), fontSize:9, fontWeight:600 }}>Vetoed:</div>
          {signal.veto_reasons.slice(0,3).map((r,i) => <div key={i} style={{ color:V('red'), fontSize:9, opacity:0.8 }}>{r}</div>)}
        </div>
      )}
    </Card>
  )
}

// ── Position Panel ──────────────────────────────────────────────────────────
function PositionPanel({ state, title }) {
  const m = window.innerWidth < 768
  if (!state?.position) return (
    <Card>
      <div style={{ color:V('text-muted'), textAlign:'center', padding:'20px 0', fontSize:13 }}>No open auto-trade position</div>
    </Card>
  )
  const p = state.position
  const pnlColor = clr(p.current_pnl)
  return (
    <Card style={{ borderLeft:`3px solid ${p.direction==='LONG'?V('green'):V('red')}` }}>
      {title && <div style={{ color:V('yellow'), fontSize:10, textTransform:'uppercase', fontWeight:600, marginBottom:8 }}>{title}</div>}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <div>
          <Badge label={p.direction} color={p.direction==='LONG'?V('green'):V('red')} />
          <span style={{ color:V('text-primary'), marginLeft:8, fontWeight:600, fontSize:13 }}>{p.symbol}</span>
        </div>
        <div style={{ color:pnlColor, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", fontSize:16 }}>{fmtPnl(p.current_pnl)}</div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(3,1fr)', gap:6 }}>
        {[['Entry', p.entry_price, V('text-primary')], ['SL', p.sl, V('red')], ['T1', p.target1, V('green')], ['T2', p.target2, V('cyan')], ['Qty', p.qty, V('text-primary')], ['Mode', p.trade_mode, V('purple')]].map(([l,v,c])=>(
          <div key={l} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
            <div style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase' }}>{l}</div>
            <div style={{ color:c, fontSize:13, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{l === 'Mode' ? v : fmt(v)}</div>
          </div>
        ))}
      </div>
      {p.t1_hit && <div style={{ marginTop:8, color:V('green'), fontSize:11, fontWeight:500 }}>✓ T1 hit — SL moved to breakeven</div>}
    </Card>
  )
}

// ── Manual positions: your own Dhan trades (alerts only, the app never exits them) ──
function ManualPositionsPanel({ items }) {
  const m = window.innerWidth < 768
  const list = items || []
  const total = list.reduce((s, p) => s + (Number(p.unrealized) || 0), 0)
  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:13 }}>Manual positions</div>
          <div style={{ color:V('text-muted'), fontSize:10 }}>Your own Dhan trades — alerts only, never exited by the app</div>
        </div>
        {list.length > 0 && <div style={{ color:clr(total), fontWeight:700, fontFamily:"'JetBrains Mono', monospace", fontSize:16 }} title="Dhan's unrealised P&L, gross">{fmtPnl(total)}</div>}
      </div>
      {list.length === 0 ? (
        <div style={{ color:V('text-muted'), textAlign:'center', padding:'12px 0', fontSize:13 }}>No manual positions</div>
      ) : list.map(p => (
        <div key={p.symbol} style={{ borderLeft:`3px solid ${p.direction==='LONG'?V('green'):V('red')}`, background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px', marginBottom:6 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            <div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <Badge label={p.direction} color={p.direction==='LONG'?V('green'):V('red')} />
              <span style={{ color:V('text-primary'), fontWeight:600, fontSize:12 }}>{p.symbol}</span>
            </div>
            <span style={{ color:clr(p.unrealized), fontWeight:700, fontFamily:"'JetBrains Mono', monospace", fontSize:13 }}>{p.unrealized != null ? fmtPnl(p.unrealized) : '—'}</span>
          </div>
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:6, marginTop:6 }}>
            {[['Qty', p.qty], ['Entry', p.entry_price], ['Index SL', p.sl], ['Index T2', p.target2]].map(([l, v]) => (
              <div key={l}>
                <div style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase' }}>{l}</div>
                <div style={{ color:V('text-primary'), fontSize:12, fontFamily:"'JetBrains Mono', monospace" }}>{l === 'Qty' ? v : (v ? fmt(v) : '—')}</div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </Card>
  )
}

// ── Strategy Details page (08 Oct): description + backtest results on 4 instruments per strategy ──
function StrategyDetailsPage() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState({})
  const load = async () => {
    try { setData(await API.get('/api/strategy_details')); setErr('') } catch (e) { setErr(String(e?.message || e)) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!data?.refresh?.running) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [data?.refresh?.running])

  const refresh = async () => {
    if (!window.confirm('Refresh all strategy results to the latest data?\n\nRuns every strategy on BANKNIFTY, NIFTY, SENSEX and CRUDEOIL in the backtest worker (live trading is not affected). Takes about an hour; the current results stay until it finishes.')) return
    const r = await API.post('/api/strategy_details/refresh', {})
    if (r?.detail) window.alert(r.detail)
    else if (r?.error) window.alert(r.error)
    load()
  }

  if (!data) return <Card><div style={{ color:V('text-muted'), padding:20 }}>{err ? 'Could not load: ' + err : 'Loading…'}</div></Card>
  const partial = data.partial
  const snap = data.snapshot || partial
  const runs = {}
  ;(data.snapshot?.runs || []).forEach(r => { runs[r.strategy + '|' + r.instrument] = r })
  // results of a refresh in progress (or one that stopped part-way) replace older ones as they finish
  ;(partial?.runs || []).forEach(r => { runs[r.strategy + '|' + r.instrument] = { ...r, fresh: !!data.snapshot } })
  const rf = data.refresh || {}
  const fmtDate = s => s ? new Date(s).toLocaleString('en-IN', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false }) : '—'
  const num = (v, d=2) => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits:d })
  const groups = [['live', 'Live now'], ['backtest', 'Backtest only']]

  const card = s => {
    const isActive = s.id === data.active_strategy
    const shown = !!open[s.id]
    return (
      <Card key={s.id} style={{ marginBottom:14 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:10, flexWrap:'wrap' }}>
          <div style={{ minWidth:0 }}>
            <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>{s.name}</div>
            <div style={{ color:V('text-muted'), fontSize:11, marginTop:2 }}>{s.id}</div>
          </div>
          <div style={{ display:'flex', gap:6 }}>
            {isActive && <Badge label="ACTIVE" color={V('green')} />}
            <Badge label={s.group === 'live' ? 'LIVE' : 'BACKTEST ONLY'} color={s.group === 'live' ? V('blue') : V('text-muted')} />
          </div>
        </div>
        <div style={{ color:V('text-secondary'), fontSize:13, marginTop:8, lineHeight:1.5 }}>{s.summary}</div>
        <div onClick={() => setOpen({ ...open, [s.id]: !shown })} style={{ color:V('blue'), fontSize:12, marginTop:8, cursor:'pointer', userSelect:'none' }}>
          {shown ? '▾ Hide how it works' : '▸ How it works'}
        </div>
        {shown && (
          <div style={{ display:'grid', gap:6, marginTop:8, fontSize:12, color:V('text-secondary'), lineHeight:1.5 }}>
            {[['Entry', s.entry], ['Exit', s.exit], ['Risk', s.risk], ['Notes', s.notes]].filter(([, v]) => v).map(([l, v]) => (
              <div key={l}><span style={{ color:V('text-muted'), fontWeight:600 }}>{l}: </span>{v}</div>
            ))}
          </div>
        )}
        <div style={{ overflowX:'auto', marginTop:12 }}>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12, minWidth:620 }}>
            <thead>
              <tr style={{ color:V('text-muted'), textAlign:'right' }}>
                {['Instrument', 'Trades', 'Win %', 'Profit factor', 'Points', 'P&L (gross)', 'Max DD %', 'Expectancy'].map((h, i) => (
                  <th key={h} style={{ padding:'6px 8px', fontWeight:600, textAlign: i === 0 ? 'left' : 'right', borderBottom:`1px solid ${V('border')}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data.instruments || []).map(inst => {
                const r = runs[s.id + '|' + inst]
                const st = r?.stats
                return (
                  <tr key={inst} style={{ textAlign:'right', borderBottom:`1px solid ${V('border')}` }}>
                    <td style={{ padding:'6px 8px', textAlign:'left', color:V('text-primary'), fontWeight:600 }}>
                      {inst}{r?.fresh && <span style={{ color:V('green'), fontSize:9, marginLeft:6 }} title="From the refresh in progress">NEW</span>}
                    </td>
                    {!r ? <td colSpan={7} style={{ padding:'6px 8px', color:V('text-muted'), textAlign:'left' }}>Not run yet</td>
                      : r.error ? <td colSpan={7} style={{ padding:'6px 8px', color:V('yellow'), textAlign:'left' }}>{r.error}</td>
                      : <>
                        <td style={{ padding:'6px 8px' }}>{num(st.total_trades, 0)}</td>
                        <td style={{ padding:'6px 8px' }}>{num(st.win_rate_pct, 1)}</td>
                        <td style={{ padding:'6px 8px', color: st.profit_factor >= 1 ? V('green') : V('red') }}>{num(st.profit_factor)}</td>
                        <td style={{ padding:'6px 8px', color:clr(st.points) }}>{num(st.points, 0)}</td>
                        <td style={{ padding:'6px 8px', color:clr(st.total_pnl), fontWeight:600 }}>{fmtPnl(st.total_pnl)}</td>
                        <td style={{ padding:'6px 8px' }}>{num(st.max_drawdown_pct, 2)}</td>
                        <td style={{ padding:'6px 8px', color:clr(st.expectancy) }}>{fmtPnl(st.expectancy)}</td>
                      </>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    )
  }

  return (
    <div>
      <Card style={{ marginBottom:16 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, flexWrap:'wrap' }}>
          <div>
            <div style={{ color:V('text-primary'), fontWeight:700, fontSize:14 }}>
              {snap ? `Results ${snap.period?.from} to ${snap.period?.to}` : 'No results yet'}
            </div>
            <div style={{ color:V('text-muted'), fontSize:12, marginTop:3 }}>
              {data.snapshot ? `Last complete run ${fmtDate(data.snapshot.generated_at)} · ₹5L capital · 1 lot · carry-forward · gross P&L`
                : partial ? `Partial results (${partial.runs?.length || 0} of ${(data.strategies || []).length * (data.instruments || []).length}) · ₹5L capital · 1 lot · carry-forward · gross P&L`
                : 'Press "Refresh to latest" (after market hours) to run every strategy.'}
            </div>
            {partial && (
              <div style={{ color:V('yellow'), fontSize:11, marginTop:3 }}>
                {rf.running ? `Refreshing — ${rf.done || 0} of ${rf.total || '…'} done; results appear as they finish.`
                  : 'The last refresh stopped part-way; finished results are shown, the rest are from the last complete run.'}
              </div>
            )}
            {snap?.lot_sizes && <div style={{ color:V('text-muted'), fontSize:11, marginTop:3 }}>
              Lot sizes: {Object.entries(snap.lot_sizes).map(([k, v]) => `${k} ${v}`).join(' · ')}
            </div>}
          </div>
          <div style={{ textAlign:'right' }}>
            <StyledButton onClick={refresh} variant="primary" disabled={rf.running || !data.refresh_allowed}>
              <RefreshCw size={12}/> {rf.running ? 'Refreshing…' : 'Refresh to latest'}
            </StyledButton>
            <div style={{ color:V('text-muted'), fontSize:11, marginTop:4 }}>
              {rf.running ? `${rf.done || 0} / ${rf.total || '…'} · ${rf.current || ''}`
                : !data.refresh_allowed ? data.refresh_blocked_reason
                : rf.error ? <span style={{ color:V('yellow') }}>{rf.error}</span>
                : rf.message || ''}
            </div>
          </div>
        </div>
        {snap?.caveats?.length > 0 && (
          <ul style={{ margin:'10px 0 0 16px', padding:0, color:V('text-muted'), fontSize:11, lineHeight:1.6 }}>
            {snap.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        )}
      </Card>
      {groups.map(([g, title]) => (
        <div key={g} style={{ marginBottom:22 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:13, textTransform:'uppercase', letterSpacing:0.5, margin:'4px 0 10px' }}>
            {title} <span style={{ color:V('text-muted'), fontWeight:500 }}>({(data.strategies || []).filter(s => s.group === g).length})</span>
          </div>
          {(data.strategies || []).filter(s => s.group === g).map(card)}
        </div>
      ))}
    </div>
  )
}

// ── Dhan Trade Panel ────────────────────────────────────────────────────────
function DhanTradePanel({ position, tradeSignal, onExit, ltp, instrument }) {
  if (!position) return null
  const p = position
  const ts = tradeSignal || {}
  const dir = p.direction
  const dirColor = dir === 'LONG' ? V('green') : V('red')
  const pnlColor = clr(p.current_pnl)

  return (
    <Card style={{ borderLeft:`3px solid ${dirColor}` }}>
      <div style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase', marginBottom:6, fontWeight:500 }}>Dhan Trade</div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <span style={{ fontSize:18 }}>{dir === 'LONG' ? '📈' : '📉'}</span>
          <div>
            <div style={{ color:dirColor, fontSize:18, fontWeight:800 }}>IN TRADE — {dir}</div>
            <div style={{ color:V('text-muted'), fontSize:10 }}>{p.symbol} • {p.trade_mode}</div>
          </div>
        </div>
        <div style={{ textAlign:'right' }}>
          <div style={{ color:pnlColor, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", fontSize:18 }}>{fmtPnl(p.current_pnl)}</div>
          <div style={{ color:V('text-muted'), fontSize:10 }}>LTP: {fmt(ltp)}</div>
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6, marginBottom:6 }}>
        {[['Index Entry', p.index_entry_price || p.entry_price, V('text-primary')],
          ['Stop Loss', p.sl, V('red')],
          ['Target 1', p.target1, V('green')],
          ['Target 2', p.target2, V('cyan')]].map(([label, val, color]) => (
          <div key={label} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 14px' }}>
            <div style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase', marginBottom:3 }}>{label}</div>
            <div style={{ color, fontSize:15, fontFamily:"'JetBrains Mono', monospace", fontWeight:700 }}>{fmt(val)}</div>
          </div>
        ))}
      </div>

      {p.trade_mode === 'OPTIONS' && (
        <div style={{ background:V('bg-tertiary'), border:`1px solid color-mix(in srgb, ${V('purple')} 30%, transparent)`, borderRadius:V('radius-sm'), padding:'6px 10px', marginBottom:8, display:'flex', alignItems:'center', gap:12 }}>
          <span style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase' }}>Option</span>
          <span style={{ color:V('purple'), fontSize:11, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{p.symbol}</span>
          <span style={{ color:V('text-muted'), fontSize:9 }}>Entry Premium:</span>
          <span style={{ color:V('text-primary'), fontSize:11, fontFamily:"'JetBrains Mono', monospace" }}>₹{fmt(p.entry_price)}</span>
        </div>
      )}

      <div style={{ display:'flex', gap:6, marginBottom:8 }}>
        {[['Qty', p.qty, V('text-primary')], ['Mode', p.trade_mode, V('purple')]].map(([l,v,c]) => (
          <div key={l} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'5px 10px' }}>
            <span style={{ color:V('text-muted'), fontSize:9, textTransform:'uppercase', marginRight:6 }}>{l}</span>
            <span style={{ color:c, fontSize:12, fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{l === 'Mode' ? v : fmt(v)}</span>
          </div>
        ))}
        {p.t1_hit && <div style={{ background:V('green-bg'), borderRadius:V('radius-sm'), padding:'5px 10px', display:'flex', alignItems:'center', gap:4 }}><CheckCircle size={11} style={{color:V('green')}}/><span style={{ color:V('green'), fontSize:11 }}>T1 Hit — SL at BE</span></div>}
      </div>

      {ts.reasons && ts.reasons.length > 0 && (
        <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'6px 10px', marginBottom:8 }}>
          <div style={{ color:V('text-head'), fontSize:10, fontWeight:600, textTransform:'uppercase', marginBottom:3 }}>Entry Reasons</div>
          <ul style={{ margin:0, paddingLeft:14, display:'flex', flexDirection:'column', gap:1 }}>
            {ts.reasons.slice(0, 3).map((r, i) => <li key={i} style={{ color:V('text-muted'), fontSize:10 }}>{r}</li>)}
          </ul>
        </div>
      )}

      <StyledButton onClick={onExit} variant="danger" style={{ width:'100%', padding:'10px 0', fontSize:13 }}>
        ⬛ Close Position
      </StyledButton>
    </Card>
  )
}

// "Manual: +₹5,499 (2 open)" under Position P&L -- your own Dhan positions (Dhan's unrealised, gross)
const manualPnlLine = (list) => {
  const n = (list || []).length
  if (!n) return undefined
  const total = list.reduce((s, p) => s + (Number(p.unrealized) || 0), 0)
  return `Manual: ${fmtPnl(total)} (${n} open)`
}

// ── Day Stats ───────────────────────────────────────────────────────────────
function DayStats({ state, balance, livePnl, todayPnl, lotSize, manualPositions }) {
  const m = window.innerWidth < 768
  const d = state?.day_stats
  // Backend always keeps day_stats.gross_pnl in sync with today_pnl, so the
  // fallback used to read d?.gross_pnl when todayPnl was 0 -- but "0" is a
  // legitimate value (no P&L today), not "not loaded yet", and that fallback
  // could surface a stale/unfiltered day_stats snapshot instead. Use
  // todayPnl directly.
  const displayPnl = todayPnl
  return (
    <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
      <MetricBox label="Balance" value={`₹${fmt(balance)}`} color={V('accent')} />
      <MetricBox label="Position P&L" value={fmtPnl(livePnl)} color={clr(livePnl)} sub={manualPnlLine(manualPositions)} />
      <MetricBox label="Today's P&L" value={fmtPnl(displayPnl)} color={clr(displayPnl)} sub={`${d?.total_trades||0} trades`} />
      <MetricBox label="Win/Loss" value={`${d?.wins||0} / ${d?.losses||0}`} color={V('text-primary')} sub={lotSize ? `Lot: ${lotSize}` : (d?.total_trades > 0 ? `${((d?.wins/d?.total_trades)*100).toFixed(0)}% WR` : '—')} />
    </div>
  )
}

// ── Settings Modal ──────────────────────────────────────────────────────────
// ── Dhan Connection: view expiry, paste a new token (no .env editing / restart) ──
const _tokenWhen = iso => iso ? new Date(iso).toLocaleString('en-IN', { timeZone:'Asia/Kolkata', weekday:'short', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', hour12:false }) : ''
const _tokenLeft = m => m == null ? '' : m <= 0 ? 'expired' : m < 60 ? `${m} min left` : `${Math.floor(m/60)} h ${m%60} min left`
const _tokenColor = s => s === 'ok' ? V('green') : s === 'expiring' ? V('yellow') : V('red')

function DhanTokenRow({ st, onUpdated }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const update = async () => {
    if (!val.trim()) return
    setBusy(true); setMsg(null)
    try {
      const r = await fetch('/api/dhan/token', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ slot: st.slot, access_token: val.trim() }) })
      const d = await r.json()
      if (r.ok && d.success) { setVal(''); setMsg({ ok:true, text: 'Saved. ' + (d.notes || []).join(', ') + '.' }); onUpdated() }
      else setMsg({ ok:false, text: d.detail || 'Update failed' })
    } catch (e) { setMsg({ ok:false, text: 'Update failed: ' + e.message }) }
    finally { setBusy(false) }
  }
  const stateText = st.state === 'missing' ? 'Not set' : st.state === 'unreadable' ? 'Token unreadable'
    : st.state === 'expired' ? `Expired ${_tokenWhen(st.expires_at)}` : `Valid until ${_tokenWhen(st.expires_at)} (${_tokenLeft(st.minutes_left)})`
  return (
    <div style={{ padding:'10px 0', borderBottom:`1px solid ${V('border-light')}` }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:8, flexWrap:'wrap' }}>
        <div style={{ fontSize:13, fontWeight:600, color:V('text-primary') }}>{st.label} token</div>
        <div style={{ fontSize:12, fontWeight:600, color:_tokenColor(st.state) }}>{stateText}</div>
      </div>
      <div style={{ fontSize:11, color:V('text-muted'), margin:'4px 0 8px' }}>Client ID {st.client_id || '—'} · Token {st.token || '—'}</div>
      <div style={{ display:'flex', gap:8 }}>
        <input type="password" autoComplete="off" value={val} onChange={e=>setVal(e.target.value)} placeholder="Paste new access token"
          style={{ flex:1, background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'6px 10px', fontSize:12, outline:'none' }} />
        <StyledButton onClick={update} variant="primary" disabled={busy || !val.trim()} style={{ padding:'6px 14px', fontSize:12 }}>
          {busy ? 'Checking…' : 'Update'}
        </StyledButton>
      </div>
      {msg && <div style={{ fontSize:11, marginTop:6, color: msg.ok ? V('green') : V('red') }}>{msg.text}</div>}
    </div>
  )
}

function DhanTokenCard() {
  const [tokens, setTokens] = useState(null)
  const load = () => fetch('/api/dhan/tokens').then(r => r.json()).then(setTokens).catch(() => {})
  useEffect(() => { load(); const id = setInterval(load, 60000); return () => clearInterval(id) }, [])
  return (
    <Card>
      <div style={{ display:'flex', alignItems:'center', gap:8, borderBottom:`2px solid ${V('border')}`, paddingBottom:8, marginBottom:4 }}>
        <Shield size={16} style={{color:V('accent')}} />
        <span style={{ fontSize:14, fontWeight:700, color:V('text-primary') }}>Dhan Connection</span>
      </div>
      <div style={{ fontSize:11, color:V('text-muted'), margin:'4px 0 2px' }}>
        Dhan access tokens last about a day. Paste a new one here when it expires — it is checked with Dhan,
        saved, and the app reconnects without a restart. The token is never shown again (only its last 4 characters).
      </div>
      {tokens ? ['main', 'cas'].map(k => tokens[k] && <DhanTokenRow key={k} st={tokens[k]} onUpdated={load} />)
              : <div style={{ fontSize:12, color:V('text-muted'), padding:10 }}>Loading…</div>}
    </Card>
  )
}

// ── Configuration panel: Dhan client IDs + Telegram details (saved to .env) ──
const _cfgInput = { width:'100%', boxSizing:'border-box', background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'8px 12px', fontSize:12, outline:'none' }
const _cfgLabel = { color:V('text-secondary'), fontSize:12, marginBottom:4, fontWeight:500 }
async function _postJson(url, body) {
  const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: body ? JSON.stringify(body) : undefined })
  let d = {}; try { d = await r.json() } catch (_) {}
  return { ok: r.ok && d.success !== false, d }
}

function DhanClientSection({ slot, current, onSaved }) {
  const [client, setClient] = useState(current.client_id || '')
  const [tok, setTok] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const changed = client.trim() !== (current.client_id || '')
  const save = async () => {
    setBusy(true); setMsg(null)
    const { ok, d } = await _postJson('/api/config/dhan-client', { slot, client_id: client.trim(), access_token: tok.trim() })
    setBusy(false)
    if (ok) { setTok(''); setMsg({ ok:true, text:'Saved. ' + (d.notes || []).join(', ') + '.' }); onSaved() }
    else setMsg({ ok:false, text: d.detail || 'Save failed' })
  }
  return (
    <div style={{ padding:'10px 0', borderBottom:`1px solid ${V('border-light')}` }}>
      <div style={{ fontSize:13, fontWeight:600, color:V('text-primary'), marginBottom:8 }}>{current.label}</div>
      <div style={_cfgLabel}>Dhan client ID</div>
      <input value={client} onChange={e=>setClient(e.target.value)} placeholder="e.g. 1100012345" style={_cfgInput} />
      <div style={{ ..._cfgLabel, marginTop:8 }}>Access token for this client ID {changed ? '(required)' : ''}</div>
      <input type="password" autoComplete="off" value={tok} onChange={e=>setTok(e.target.value)} placeholder="A token belongs to one client ID — paste it with the new ID" style={_cfgInput} />
      <div style={{ display:'flex', justifyContent:'flex-end', marginTop:8 }}>
        <StyledButton onClick={save} variant="primary" disabled={busy || !client.trim() || !tok.trim()} style={{ padding:'6px 16px', fontSize:12 }}>
          {busy ? 'Checking…' : 'Save'}
        </StyledButton>
      </div>
      {msg && <div style={{ fontSize:11, marginTop:4, color: msg.ok ? V('green') : V('red') }}>{msg.text}</div>}
    </div>
  )
}

function TelegramSection({ current, onSaved }) {
  const [tok, setTok] = useState('')
  const [chat, setChat] = useState(current.chat_id || '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const chatChanged = chat.trim() !== (current.chat_id || '')
  const save = async () => {
    setBusy(true); setMsg(null)
    const body = {}
    if (tok.trim()) body.bot_token = tok.trim()
    if (chatChanged) body.chat_id = chat.trim()
    const { ok, d } = await _postJson('/api/config/telegram', body)
    setBusy(false)
    if (ok) { setTok(''); setMsg({ ok:true, text:'Saved — new details are used for the next alert.' }); onSaved() }
    else setMsg({ ok:false, text: d.detail || 'Save failed' })
  }
  const test = async () => {
    setBusy(true); setMsg(null)
    const { ok, d } = await _postJson('/api/config/telegram/test')
    setBusy(false)
    setMsg(ok ? { ok:true, text:'Test message sent — check Telegram.' } : { ok:false, text: d.detail || 'Test failed' })
  }
  return (
    <div style={{ padding:'10px 0' }}>
      <div style={_cfgLabel}>Bot token {current.bot_token ? `(current ${current.bot_token})` : '(not set)'}</div>
      <input type="password" autoComplete="off" value={tok} onChange={e=>setTok(e.target.value)} placeholder="Paste a new bot token to replace it (leave empty to keep)" style={_cfgInput} />
      <div style={{ ..._cfgLabel, marginTop:8 }}>Chat ID</div>
      <input value={chat} onChange={e=>setChat(e.target.value)} placeholder="e.g. 123456789" style={_cfgInput} />
      <div style={{ display:'flex', justifyContent:'flex-end', gap:8, marginTop:8 }}>
        <StyledButton onClick={test} variant="default" disabled={busy || !current.configured} style={{ padding:'6px 14px', fontSize:12 }}>Send test message</StyledButton>
        <StyledButton onClick={save} variant="primary" disabled={busy || (!tok.trim() && !chatChanged)} style={{ padding:'6px 16px', fontSize:12 }}>
          {busy ? 'Checking…' : 'Save'}
        </StyledButton>
      </div>
      {msg && <div style={{ fontSize:11, marginTop:4, color: msg.ok ? V('green') : V('red') }}>{msg.text}</div>}
    </div>
  )
}

function ConfigurationModal({ onClose }) {
  const [conf, setConf] = useState(null)
  const load = () => fetch('/api/config/credentials').then(r => r.json()).then(setConf).catch(() => {})
  useEffect(() => { load() }, [])
  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', backdropFilter:'blur(4px)', zIndex:100, display:'flex', alignItems:'center', justifyContent:'center', padding:16 }}>
      <div style={{ background:V('bg-secondary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-xl'), width:520, maxWidth:'100%', maxHeight:'90vh', overflowY:'auto', padding:24, boxShadow:V('shadow-lg') }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 }}>
          <h2 style={{ margin:0, color:V('text-primary'), fontSize:18, fontWeight:700 }}>Configuration</h2>
          <StyledButton onClick={onClose} variant="default" style={{ padding:'4px 12px', fontSize:12 }}>Close</StyledButton>
        </div>
        <p style={{ color:V('text-muted'), fontSize:12, margin:'0 0 12px' }}>
          Account details saved to the app's .env file — everything is checked before it is saved and used straight away.
        </p>
        {!conf ? <div style={{ fontSize:12, color:V('text-muted') }}>Loading…</div> : (<>
          <div style={{ fontSize:11, fontWeight:700, color:V('text-head'), textTransform:'uppercase', marginTop:6 }}>Dhan account</div>
          {['main', 'cas'].map(k => conf.dhan[k] && <DhanClientSection key={k + (conf.dhan[k].client_id || '')} slot={k} current={conf.dhan[k]} onSaved={load} />)}
          <div style={{ fontSize:11, fontWeight:700, color:V('text-head'), textTransform:'uppercase', marginTop:16 }}>Telegram alerts</div>
          <TelegramSection key={conf.telegram.chat_id + conf.telegram.bot_token} current={conf.telegram} onSaved={load} />
        </>)}
      </div>
    </div>
  )
}

function SettingsPanel({ onSaved }) {
  const [cfg, setCfg] = useState({})
  const [orig, setOrig] = useState({})   // values as loaded -- Save sends only what you changed
  const [showConfig, setShowConfig] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = () => API.get('/api/settings').then(c => { setCfg(c); setOrig(c) })
  useEffect(() => { load() }, [])

  const set = (k,v) => setCfg(p => ({...p, [k]:v}))

  // Save used to re-send EVERY setting as it was when the page opened -- including
  // auto_trade / instrument / strategy -- silently undoing changes made elsewhere
  // meanwhile (e.g. auto-trade switched off by the kill switch or on the Live page).
  const save = async () => {
    const changed = Object.fromEntries(Object.entries(cfg).filter(([k, v]) => v !== orig[k]))
    if (Object.keys(changed).length) await API.post('/api/settings', changed)
    await load()
    setSaved(true); setTimeout(() => { setSaved(false); onSaved(); }, 1200)
  }

  const Row = ({ label, children }) => (
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 0', borderBottom:`1px solid ${V('border-light')}` }}>
      <span style={{ color:V('text-secondary'), fontSize:12 }}>{label}</span>
      {children}
    </div>
  )
  const Sel = ({ val, opts, onChange }) => (
    // `o.v ?? o`, not `o.v || o`: an option whose value is 0 ("Current Expiry") used to
    // become "[object Object]", so it could never be selected again.
    <select value={val ?? ''} onChange={e=>onChange(e.target.value)} style={{
      background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'5px 10px', fontSize:12, outline:'none'
    }}>{opts.map(o=><option key={o.v ?? o} value={o.v ?? o}>{o.l ?? o}</option>)}</select>
  )
  const Num = ({ val, onChange, min, max, step=1 }) => (
    <input type="number" value={val||0} min={min} max={max} step={step} onChange={e=>onChange(Number(e.target.value))} style={{
      background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'5px 10px', fontSize:12, width:90, outline:'none'
    }} />
  )

  const SectionTitle = ({ label, icon }) => (
    <div style={{ display:'flex', alignItems:'center', gap:8, borderBottom:`2px solid ${V('border')}`, paddingBottom:8, marginBottom:12 }}>
      {icon}
      <span style={{ color:V('text-primary'), fontSize:13, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.05em' }}>{label}</span>
    </div>
  )

  return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'flex-end' }}>
        <StyledButton onClick={() => setShowConfig(true)} variant="primary" style={{ padding:'8px 18px', fontSize:13, fontWeight:700 }}>
          <Settings size={14} /> Configuration
        </StyledButton>
      </div>
      {showConfig && <ConfigurationModal onClose={() => { setShowConfig(false); load() }} />}
      <DhanTokenCard />
      {/* 2-column grid layout */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 }}>
        {/* Left Column */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
          {/* Strategy & Instrument */}
          <Card>
            <SectionTitle label="Strategy & Instrument" icon={<Cpu size={16} style={{color:V('accent')}} />} />
            <Row label="Strategy">
              <Sel val={cfg.strategy} opts={LIVE_STRATEGY_OPTIONS} onChange={v=>set('strategy',v)} />
            </Row>
            <Row label="Instrument">
              <Sel val={cfg.instrument} opts={['NIFTY','BANKNIFTY','SENSEX','CRUDEOIL']} onChange={v=>set('instrument',v)} />
            </Row>
            <Row label="Position Hold Mode">
              <Sel val={cfg.position_hold_mode} opts={[{v:'CARRY_FORWARD',l:'Carry Forward (hold overnight)'},{v:'INTRADAY',l:'Intraday (exit by close)'}]} onChange={v=>set('position_hold_mode',v)} />
            </Row>
            <Row label="Trade Mode">
              <Sel val={cfg.trade_mode} opts={['INDEX','OPTIONS']} onChange={v=>set('trade_mode',v)} />
            </Row>
            {cfg.trade_mode==='INDEX' && (
              <Row label="Index Expiry">
                <Sel val={cfg.index_expiry} opts={[{v:0,l:'Current Month'},{v:1,l:'Next Month'},{v:2,l:'Far Month'}]} onChange={v=>set('index_expiry',Number(v))} />
              </Row>
            )}
            {cfg.trade_mode==='OPTIONS' && (<>
              <Row label="Options Expiry">
                <Sel val={cfg.options_expiry} opts={[{v:0,l:'Current Expiry'},{v:1,l:'Next Expiry'}]} onChange={v=>set('options_expiry',Number(v))} />
              </Row>
              <Row label="Strike Type">
                <Sel val={cfg.strike_type} opts={['ATM','ITM','OTM']} onChange={v=>set('strike_type',v)} />
              </Row>
              <Row label="Strike Offset (ITM-/OTM+)">
                <Num val={cfg.strike_offset} onChange={v=>set('strike_offset',v)} min={-10} max={10} />
              </Row>
            </>)}
            <Row label="Lot Multiplier">
              <Num val={cfg.lot_multiplier} onChange={v=>set('lot_multiplier',v)} min={1} max={50} />
            </Row>
          </Card>

          {/* Risk Management & Auto Trading */}
          <Card>
            <SectionTitle label="Risk & Auto Execution" icon={<Shield size={16} style={{color:V('red')}} />} />
            <Row label="Max Daily Loss (₹)">
              <Num val={cfg.max_daily_loss} onChange={v=>set('max_daily_loss',v)} min={1000} max={100000} step={1000} />
            </Row>
            <Row label="Max Daily Profit (₹)">
              <Num val={cfg.max_daily_profit} onChange={v=>set('max_daily_profit',v)} min={1000} max={200000} step={1000} />
            </Row>
            <Row label="Auto Square-Off (mins before close, Intraday mode only)">
              <Num val={cfg.auto_square_off_minutes} onChange={v=>set('auto_square_off_minutes',v)} min={1} max={60} />
            </Row>
            <Row label="Kill Switch">
              <Sel val={cfg.auto_kill_switch ? 'true' : 'false'} opts={[{v:'false',l:'Off'},{v:'true',l:'On'}]} onChange={v=>set('auto_kill_switch',v==='true')} />
            </Row>
            {cfg.auto_kill_switch && (
              <Row label="Max Consecutive Failures">
                <Num val={cfg.auto_kill_switch_max_failures} onChange={v=>set('auto_kill_switch_max_failures',v)} min={1} max={10} />
              </Row>
            )}
          </Card>

          {/* Telegram Alerts */}
          <Card>
            <SectionTitle label="Telegram Alerts" icon={<Send size={16} style={{color:V('cyan')}} />} />
            {/* Bot token / chat ID are read only from .env (never saved from here) --
                editable boxes here used to look like they saved but were discarded. */}
            <Row label="Bot Token & Chat ID">
              <span style={{ fontSize:12, fontWeight:600, color: cfg.telegram_configured ? V('green') : V('yellow') }}>
                {cfg.telegram_configured ? '✓ Configured in .env' : 'Not set — add them to .env and restart'}
              </span>
            </Row>
          </Card>
        </div>

        {/* Right Column */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
          {/* Strategy Specific Settings */}
          <Card>
            <SectionTitle label="Positions Imported from Dhan" icon={<Target size={16} style={{color:V('yellow')}} />} />
            <div style={{ fontSize:11, color:V('text-muted'), margin:'8px 0 4px', lineHeight:1.5 }}>
              SL / T1 / T2 levels set on positions the app picks up from your Dhan account (e.g. manual trades).
              The live strategies use their own levels, not these.
            </div>
            <Row label="SL Multiplier (ATR×)">
              <Num val={cfg.atr_sl_mult} onChange={v=>set('atr_sl_mult',v)} min={0.5} max={3} step={0.1} />
            </Row>
            <Row label="T1 Multiplier (ATR×)">
              <Num val={cfg.atr_t1_mult} onChange={v=>set('atr_t1_mult',v)} min={1} max={5} step={0.1} />
            </Row>
            <Row label="T2 Multiplier (ATR×)">
              <Num val={cfg.atr_t2_mult} onChange={v=>set('atr_t2_mult',v)} min={2} max={8} step={0.1} />
            </Row>

          </Card>

          {/* Capital Protection & System Tuning */}
          <Card>
            <SectionTitle label="System & Capital Protection" icon={<Settings size={16} style={{color:V('accent')}} />} />
            <Row label="Starting Capital (₹)">
              <Num val={cfg.starting_capital} onChange={v=>set('starting_capital',v)} min={10000} max={10000000} step={10000} />
            </Row>
            <div style={{ fontSize:10, color:V('text-muted'), margin:'-4px 0 6px' }}>
              Changing this restarts the drawdown tracker (equity and peak reset to the new value, block flag cleared).
            </div>
            <Row label="Data Stale Threshold (min)">
              <Num val={cfg.data_stale_threshold_min} onChange={v=>set('data_stale_threshold_min',v)} min={5} max={30} />
            </Row>
            <Row label="Chart Timeframe">
              <Sel val={cfg.chart_timeframe} opts={[{v:'1',l:'1 Min'},{v:'5',l:'5 Min'},{v:'15',l:'15 Min'},{v:'25',l:'25 Min'},{v:'60',l:'1 Hour'},{v:'DAY',l:'Daily'}]} onChange={v=>set('chart_timeframe',v)} />
            </Row>
          </Card>
        </div>
      </div>

      {/* Save Button */}
      <div style={{ display:'flex', justifyContent:'flex-end', marginTop:8 }}>
        <StyledButton onClick={save} variant={saved ? 'success' : 'primary'} style={{ padding:'12px 40px', fontSize:14, fontWeight:700 }}>
          {saved ? '✓ Settings Saved Successfully!' : 'Save System Settings'}
        </StyledButton>
      </div>
    </div>
  )
}

// ── Backtest Panel ──────────────────────────────────────────────────────────
function BacktestPanel({ connected, selectedStrategy, onStrategyChange, onSelectStrategy }) {
  const m = window.innerWidth < 768
  const [form, setForm] = useState({ instrument:'BANKNIFTY', from_date:'', to_date:'', initial_capital:500000, lot_multiplier:1, strategy: selectedStrategy || 'regime_trend_range', hold_mode:'INTRADAY' })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [availableStrategies, setAvailableStrategies] = useState([
    { v:'regime_trend_range', l:'Regime Trend/Range Optimized' },
    { v:'regime_trend_v2', l:'Regime Trend V2 — Selective (optimized)' },
    { v:'regime_trend_v2b', l:'Regime Trend V2-B — Balanced' },
    { v:'multi_agent', l:'Multi-Agent V3 Kernel' },
    { v:'donchian_5m_swing', l:'Donchian 5m Swing (overnight)' },
    { v:'donchian_5m_intraday', l:'Donchian 5m Intraday' }
  ])

  useEffect(() => {
    if (selectedStrategy) {
      setForm(p => ({ ...p, strategy: selectedStrategy }))
    }
  }, [selectedStrategy])

  const fetchStrats = async () => {
    try {
      const resp = await fetch('/api/backtest/strategies')
      const r = await resp.json()
      if (r && r.strategies) {
        setAvailableStrategies(r.strategies.map(s => ({ v: s.id, l: s.label })))
      }
    } catch (e) {}
  }

  useEffect(() => {
    fetchStrats()
  }, [])

  const deleteCustomStrat = async (stratId) => {
    if (!window.confirm(`Are you sure you want to delete custom strategy '${stratId}'?`)) return
    try {
      const resp = await fetch(`/api/research/strategy/${stratId}`, { method: 'DELETE' })
      const r = await resp.json()
      if (r.success) {
        setForm(p => ({ ...p, strategy: 'regime_trend_range' }))
        fetchStrats()
      } else {
        alert(r.detail || r.error || 'Failed to delete strategy')
      }
    } catch (e) {
      alert('Delete request failed: ' + e.message)
    }
  }

  const run = async () => {
    if (!connected) { setErr('Connect to broker first'); return }
    setLoading(true); setErr('')
    try {
      const resp = await fetch('/api/backtest', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(form) })
      const r = await resp.json()
      if (!resp.ok) setErr(r.detail || r.error || `Server error ${resp.status}`)
      else if (r.error) setErr(r.error)
      else setResult(r)
    } catch(e) { setErr('Backtest failed: ' + (e.message || e)) }
    finally { setLoading(false) }
  }

  const downloadCSV = () => {
    if (!result?.trades || result.trades.length === 0) return
    const headers = ['Entry Time', 'Exit Time', 'Direction', 'Entry Price', 'Exit Price', 'P&L', 'Exit Reason']
    const csvRows = [headers.join(',')]
    for (const t of result.trades) {
      csvRows.push([
        t.entry_time, t.exit_time, t.direction, t.entry_price, t.exit_price, t.pnl,
        `"${(t.exit_reason || '').replace(/"/g, '""')}"`
      ].join(','))
    }
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.setAttribute("href", url)
    link.setAttribute("download", `trades_${form.instrument}_backtest.csv`)
    link.style.visibility = 'hidden'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const inputStyle = {
    background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`,
    borderRadius:V('radius-sm'), padding:'8px 10px', width:'100%', fontSize:12,
  }

  const isCustomStrategy = (form.strategy || '').startsWith('custom_')
  // Strategies running in live trading are locked: no delete from here (backend refuses too)
  const isLiveStrategy = LIVE_STRATEGY_IDS.includes(form.strategy)

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <Card>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Backtest Configuration</div>
          {isCustomStrategy && (
            <div style={{ display:'flex', gap:8 }}>
              <StyledButton onClick={() => onSelectStrategy && onSelectStrategy(form.strategy)} variant="secondary" style={{ padding:'4px 10px', fontSize:11 }}>
                ✏️ Edit in Research Studio
              </StyledButton>
              {isLiveStrategy ? (
                <span style={{ color:V('text-muted'), fontSize:11, alignSelf:'center' }}>🔒 Live strategy — locked</span>
              ) : (
                <StyledButton onClick={() => deleteCustomStrat(form.strategy)} variant="danger" style={{ padding:'4px 10px', fontSize:11 }}>
                  🗑️ Delete Strategy
                </StyledButton>
              )}
            </div>
          )}
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
          {[
            ['Strategy', <select value={form.strategy} onChange={e=>{ const v = e.target.value; setForm(p=>({...p,strategy:v})); onStrategyChange && onStrategyChange(v) }} style={inputStyle}>
              {availableStrategies.map(o=><option key={o.v} value={o.v}>{o.l}</option>)}
            </select>],
            ['Instrument', <select value={form.instrument} onChange={e=>setForm(p=>({...p,instrument:e.target.value}))} style={inputStyle}>
              {['NIFTY','BANKNIFTY','SENSEX','CRUDEOIL'].map(i=><option key={i}>{i}</option>)}
            </select>],
            ['From Date', <input type="date" value={form.from_date} onChange={e=>setForm(p=>({...p,from_date:e.target.value}))} style={inputStyle} />],
            ['To Date', <input type="date" value={form.to_date} onChange={e=>setForm(p=>({...p,to_date:e.target.value}))} style={inputStyle} />],
            ['Capital (₹)', <input type="number" value={form.initial_capital} onChange={e=>setForm(p=>({...p,initial_capital:Number(e.target.value)}))} style={inputStyle} />],
            ['Position Holding', <select value={form.hold_mode} onChange={e=>setForm(p=>({...p,hold_mode:e.target.value}))} style={inputStyle}>
              <option value="INTRADAY">Intraday (close by EOD time)</option>
              <option value="CARRY_FORWARD">Carry Forward (allow overnight)</option>
            </select>],
          ].map(([label, input]) => (
            <div key={label}>
              <div style={{ color:V('text-muted'), fontSize:11, marginBottom:4, fontWeight:500 }}>{label}</div>
              {input}
            </div>
          ))}
        </div>
        {err && <div style={{ color:V('red'), fontSize:12, marginTop:8, fontWeight:500 }}>{err}</div>}
        <StyledButton onClick={run} disabled={loading} variant="primary" style={{ width:'100%', marginTop:14, padding:'12px 0', fontSize:13 }}>
          {loading ? '⏳ Running...' : '▶ Run Backtest'}
        </StyledButton>
      </Card>

      {result?.stats && (
        <Card>
          <div style={{ color:V('text-primary'), fontWeight:700, marginBottom:6, fontSize:15 }}>Backtest Results</div>
          {/* What data the backtest actually ran on (and any history shortfall) */}
          {result.data_range && result.data_range['5'] && (
            <div style={{ color:V('text-muted'), fontSize:11, marginBottom:4 }}>
              Data used: 5-min {result.data_range['5'].from} → {result.data_range['5'].to}
              {result.data_range['1D'] ? ` · daily ${result.data_range['1D'].from} → ${result.data_range['1D'].to}` : ''}
            </div>
          )}
          {(result.data_notes || []).map((n, i) => (
            <div key={i} style={{ color:V('yellow'), fontSize:11, marginBottom:4 }}>⚠ {n}</div>
          ))}
          <div style={{ marginBottom:8 }} />
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8, marginBottom:14 }}>
            <MetricBox label="Total Trades" value={result.stats.total_trades} />
            <MetricBox label="Win Rate" value={`${result.stats.win_rate_pct}%`} color={V('green')} />
            <MetricBox label="Profit Factor" value={result.stats.profit_factor} color={result.stats.profit_factor>1?V('green'):V('red')} />
            <MetricBox label="Total P&L" value={fmtPnl(result.stats.total_pnl)} color={clr(result.stats.total_pnl)} />
            <MetricBox label="Avg Win" value={fmtPnl(result.stats.avg_win)} color={V('green')} />
            <MetricBox label="Avg Loss" value={fmtPnl(result.stats.avg_loss)} color={V('red')} />
            <MetricBox label="Max Drawdown" value={`${result.stats.max_drawdown_pct}%`} color={V('red')} />
            <MetricBox label="Expectancy" value={fmtPnl(result.stats.expectancy)} color={clr(result.stats.expectancy)} />
          </div>
          
          {result.equity_curve && result.equity_curve.length > 0 && (
            <div style={{ marginBottom: 18, border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm'), padding: 12, background: V('bg-tertiary') }}>
              <div style={{ color: V('text-muted'), fontSize: 11, fontWeight: 500, marginBottom: 8 }}>Cumulative P&L Curve</div>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={[
                  { entry_time: 'Start', equity: form.initial_capital },
                  ...result.equity_curve
                ]} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={V('green')} stopOpacity={0.25}/>
                      <stop offset="95%" stopColor={V('green')} stopOpacity={0.0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={V('border-light')} opacity={0.3} />
                  <XAxis dataKey="entry_time" tick={{ fill: V('text-muted'), fontSize: 9 }} stroke={V('border-light')} />
                  <YAxis 
                    tick={{ fill: V('text-muted'), fontSize: 9 }} 
                    stroke={V('border-light')}
                    domain={['dataMin - 10000', 'dataMax + 10000']}
                    tickFormatter={v => `₹${(v/1000).toFixed(0)}k`} 
                  />
                  <ReTooltip 
                    contentStyle={{ background: V('bg-secondary'), border: `1px solid ${V('border')}`, borderRadius: V('radius-sm'), fontSize: 11 }}
                    labelStyle={{ color: V('text-muted') }}
                    itemStyle={{ color: V('text-primary') }}
                    formatter={(v) => [`₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`, 'Equity']}
                  />
                  <Area type="monotone" dataKey="equity" stroke={V('green')} strokeWidth={2} fillOpacity={1} fill="url(#colorEquity)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
          
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
            <span style={{ color:V('text-muted'), fontSize:11, fontWeight:500 }}>All Trades ({result.trades.length})</span>
            <StyledButton onClick={downloadCSV} variant="primary" style={{ padding:'4px 12px', fontSize:11 }}>
              📥 Download CSV
            </StyledButton>
          </div>
          
          <div style={{ overflowX:'auto', maxHeight:'300px', overflowY:'auto', border:`1px solid ${V('border-light')}`, borderRadius:V('radius-sm') }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead style={{ position:'sticky', top:0, background:V('bg-secondary'), zIndex:1 }}>
                <tr>{['Time','Dir','Entry','Exit','P&L','Reason'].map(h=>(
                  <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), borderBottom:`1px solid ${V('border')}`, fontWeight:600 }}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>
                {result.trades.map((t,i) => (
                  <tr key={i} style={{ borderBottom:`1px solid ${V('border-light')}`, background: i%2===0 ? 'transparent' : V('bg-tertiary') }}>
                    <td style={{ padding:'6px 10px', color:V('text-muted') }}>{t.entry_time?.slice(5,16)}</td>
                    <td style={{ padding:'6px 10px', color: t.direction==='LONG'?V('green'):V('red'), fontWeight:600 }}>{t.direction}</td>
                    <td style={{ padding:'6px 10px', color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>{fmt(t.entry_price)}</td>
                    <td style={{ padding:'6px 10px', color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>{fmt(t.exit_price)}</td>
                    <td style={{ padding:'6px 10px', color:clr(t.pnl), fontFamily:"'JetBrains Mono', monospace", fontWeight:600 }}>{fmtPnl(t.pnl)}</td>
                    <td style={{ padding:'6px 10px', color:V('text-muted'), fontSize:10 }}>{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Connect Modal ───────────────────────────────────────────────────────────
function ConnectModal({ onClose, onConnected }) {
  const [cc, setCc]   = useState('')
  const [tok, setTok] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  const connect = async () => {
    setLoading(true); setMsg('')
    const r = await API.post('/api/connect', { client_code: cc, access_token: tok })
    setMsg(r.message)
    if (r.success) { setTimeout(() => { onConnected(); onClose() }, 800) }
    setLoading(false)
  }

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', backdropFilter:'blur(4px)', zIndex:100, display:'flex', alignItems:'center', justifyContent:'center' }}>
      <div style={{ background:V('bg-secondary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-xl'), width:440, padding:28, boxShadow:V('shadow-lg') }}>
        <h2 style={{ margin:'0 0 6px', color:V('text-primary'), fontSize:18, fontWeight:700 }}>Connect to Dhan</h2>
        <p style={{ color:V('text-muted'), fontSize:12, margin:'0 0 20px' }}>Access token refreshes daily — update every morning before trading.</p>
        {[['Dhan Client Code', cc, setCc, 'CLIENT123456'],
          ['Access Token', tok, setTok, 'eyJ...']].map(([label, val, setter, ph]) => (
          <div key={label} style={{ marginBottom:14 }}>
            <div style={{ color:V('text-secondary'), fontSize:12, marginBottom:4, fontWeight:500 }}>{label}</div>
            <input value={val} onChange={e=>setter(e.target.value)} placeholder={ph}
              style={{ width:'100%', background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'10px 14px', fontSize:13, boxSizing:'border-box' }} />
          </div>
        ))}
        {msg && <div style={{ color: msg.includes('✓')||msg.includes('Connected') ? V('green') : V('red'), fontSize:12, marginBottom:12, fontWeight:500 }}>{msg}</div>}
        <div style={{ display:'flex', gap:8 }}>
          <StyledButton onClick={connect} disabled={loading||!cc||!tok} variant="primary" style={{ flex:1, padding:'12px 0' }}>
            {loading ? 'Connecting...' : 'Connect'}
          </StyledButton>
          <StyledButton onClick={onClose} style={{ padding:'12px 20px' }}>Cancel</StyledButton>
        </div>
      </div>
    </div>
  )
}

// Helper to extract clean YYYY-MM-DD from any date string / timestamp
const extractISODate = (e) => {
  if (!e) return ''
  // Try dedicated date fields first
  let raw = e.entry_date || e.date || e.trade_date || e.createTime || e.updateTime || e.exchangeTime || ''
  
  // If no date field, use timestamp fields only if they contain date delimiters or start with '20'
  if (!raw && typeof e.entry_time === 'string' && (e.entry_time.includes('-') || e.entry_time.includes('/') || e.entry_time.includes('T') || e.entry_time.startsWith('20'))) {
    raw = e.entry_time
  }
  if (!raw && typeof e.time === 'string' && (e.time.includes('-') || e.time.includes('/') || e.time.includes('T') || e.time.startsWith('20'))) {
    raw = e.time
  }
  if (!raw && typeof e.created_at === 'string') raw = e.created_at
  if (!raw && typeof e.timestamp === 'string') raw = e.timestamp
  if (!raw) return ''

  const str = String(raw).trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(str)) return str.slice(0, 10)
  const dmY = str.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})/)
  if (dmY) return `${dmY[3]}-${dmY[2].padStart(2, '0')}-${dmY[1].padStart(2, '0')}`
  try {
    const d = new Date(str)
    if (!isNaN(d.getTime())) {
      const year = d.getFullYear()
      const month = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      return `${year}-${month}-${day}`
    }
  } catch {}
  return ''
}


// ── Signal Journal Panel ────────────────────────────────────────────────────
// Strategy Signals Log -- every strategy signal with its theoretical result (index points).
// Statuses: OPEN, WIN, LOSS, FLAT (break-even), DAY_END (still open next morning, closed at
// 09:15 with no exit price -- not a result), VOID (bad data: no/stale exit, holiday signal).
// Only WIN / LOSS / FLAT count towards win rate and P&L.
const _SJ_RESULT = new Set(['WIN', 'LOSS', 'FLAT'])
// Names for strategies that are no longer live but still appear in older log entries
const _SJ_OLD_LABELS = {
  multi_agent: 'Multi-Agent Optimized',
  regime_trend_range: 'Regime T/R Optimized',
  custom_halftrend_hull_standalone: 'HalfTrend + Hull (retired)',
  regime_trend_v2: 'Regime Trend V2 — Selective',
  regime_trend_v2b: 'Regime Trend V2-B — Balanced',
  donchian_5m_swing: 'Donchian 5m Swing',
  donchian_5m_intraday: 'Donchian 5m Intraday',
}
const _sjStrategyLabel = s => LIVE_STRATEGY_LABELS[s] || _SJ_OLD_LABELS[s] || s

function SignalJournalPanel({ entries, onRefresh }) {
  const m = window.innerWidth < 768
  const [draftStrategy, setDraftStrategy] = React.useState('ALL')
  const [draftInstrument, setDraftInstrument] = React.useState('ALL')
  const [draftFromDate, setDraftFromDate] = React.useState('')
  const [draftToDate, setDraftToDate] = React.useState('')

  // Applied filters state (only updated on Submit)
  const [appliedFilters, setAppliedFilters] = React.useState({
    strategy: 'ALL',
    instrument: 'ALL',
    fromDate: '',
    toDate: ''
  })
  const [dateError, setDateError] = React.useState('')

  // Filter choices come from the log itself (no hard-coded lists of old strategies)
  const strategyOptions = React.useMemo(() => [...new Set(entries.map(e => e.strategy).filter(Boolean))].sort((a, b) => _sjStrategyLabel(a).localeCompare(_sjStrategyLabel(b))), [entries])
  const instrumentOptions = React.useMemo(() => [...new Set(entries.map(e => e.instrument).filter(Boolean))].sort(), [entries])

  const handleSubmitFilters = (e) => {
    if (e) e.preventDefault()
    if (draftFromDate && draftToDate && draftFromDate > draftToDate) {
      setDateError('The From date is after the To date.')
      return
    }
    setDateError('')
    setAppliedFilters({ strategy: draftStrategy, instrument: draftInstrument, fromDate: draftFromDate, toDate: draftToDate })
    if (onRefresh) onRefresh()
  }

  const filteredEntries = entries.filter(e => {
    if (appliedFilters.strategy !== 'ALL' && e.strategy !== appliedFilters.strategy) return false
    if (appliedFilters.instrument !== 'ALL' && e.instrument !== appliedFilters.instrument) return false
    const dateStr = extractISODate(e)
    if (dateStr && dateStr.length === 10) {
      if (appliedFilters.fromDate && dateStr < appliedFilters.fromDate) return false
      if (appliedFilters.toDate && dateStr > appliedFilters.toDate) return false
    }
    return true
  })

  const results  = filteredEntries.filter(e => _SJ_RESULT.has(e.status))
  const wins     = results.filter(e => e.status === 'WIN').length
  const losses   = results.filter(e => e.status === 'LOSS').length
  const flats    = results.filter(e => e.status === 'FLAT').length
  const dayEnd   = filteredEntries.filter(e => e.status === 'DAY_END').length
  const voided   = filteredEntries.filter(e => e.status === 'VOID').length
  const totalPts = results.reduce((s, e) => s + (e.pnl_pts || 0), 0)
  const totalInr = results.reduce((s, e) => s + (e.pnl_inr || 0), 0)
  const winRate  = results.length > 0 ? (wins / results.length * 100).toFixed(0) : '—'

  const statusColor = s => s === 'WIN' ? V('green') : s === 'LOSS' ? V('red') : s === 'OPEN' ? V('yellow') : V('text-muted')
  const statusLabel = s => ({ WIN:'✓ WIN', LOSS:'✗ LOSS', OPEN:'● OPEN', FLAT:'= FLAT', DAY_END:'Day-end close', VOID:'Void' }[s] || s)

  const handleDownloadCSV = () => {
    const headers = ['Date', 'Strategy', 'Instrument', 'Direction', 'Entry Price', 'SL', 'Target 1', 'Target 2', 'Regime', 'Score', 'Exit Time', 'Exit Price', 'P&L pts', 'P&L INR (index pts x lot)', 'Exit Reason', 'Status', 'Note']
    const rows = filteredEntries.map(e => [
      e.entry_time,
      _sjStrategyLabel(e.strategy),
      e.instrument,
      e.direction,
      e.entry_price,
      e.sl,
      e.target1,
      e.target2,
      e.regime || '',
      e.weighted_score || e.ml_prob || '',
      e.exit_time || '',
      e.exit_price ?? '',
      e.pnl_pts ?? '',
      e.pnl_inr ?? '',
      e.exit_reason || '',
      e.status,
      e.void_reason || ''
    ])

    const csvContent = [headers.join(','), ...rows.map(r => r.map(val => `"${String(val).replace(/"/g, '""')}"`).join(','))].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.setAttribute("href", url)
    link.setAttribute("download", `strategy_signals_log_${new Date().toISOString().split('T')[0]}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const selStyle = {
    background: V('bg-input'),
    color: V('text-primary'),
    border: `1px solid ${V('border')}`,
    borderRadius: V('radius-sm'),
    padding: '4px 8px',
    fontSize: 11,
    outline: 'none'
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(5,1fr)', gap:8 }}>
        <MetricBox label="Total Signals" value={filteredEntries.length} color={V('accent')} sub={(dayEnd || voided) ? `${dayEnd} day-end · ${voided} void — not counted` : undefined} />
        <MetricBox label="Win Rate" value={`${winRate}%`} color={V('green')} sub={`${wins}W / ${losses}L${flats ? ` / ${flats} flat` : ''}`} />
        <MetricBox label="Total P&L (pts)" value={totalPts >= 0 ? `+${totalPts.toFixed(0)}` : totalPts.toFixed(0)} color={clr(totalPts)} sub="index points" />
        <MetricBox label="Theoretical P&L (₹)" value={fmtPnl(totalInr)} color={clr(totalInr)} sub="index pts × lot size, not option P&L" />
        <MetricBox label="Open" value={filteredEntries.filter(e=>e.status==='OPEN').length} color={V('yellow')} />
      </div>

      <Card>
        <form onSubmit={handleSubmitFilters} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12, flexWrap:'wrap', gap:8 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Strategy Signals Log</div>

          <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>Strategy:</span>
              <select value={draftStrategy} onChange={e => setDraftStrategy(e.target.value)} style={selStyle}>
                <option value="ALL">All Strategies</option>
                {strategyOptions.map(s => <option key={s} value={s}>{_sjStrategyLabel(s)}</option>)}
              </select>
            </div>

            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>Instrument:</span>
              <select value={draftInstrument} onChange={e => setDraftInstrument(e.target.value)} style={selStyle}>
                <option value="ALL">All Instruments</option>
                {instrumentOptions.map(i => <option key={i} value={i}>{i}</option>)}
              </select>
            </div>

            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>From:</span>
              <input type="date" value={draftFromDate} onChange={e => setDraftFromDate(e.target.value)} style={{ ...selStyle, width:'120px' }} />
            </div>
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>To:</span>
              <input type="date" value={draftToDate} onChange={e => setDraftToDate(e.target.value)} style={{ ...selStyle, width:'120px' }} />
            </div>

            <StyledButton type="submit" variant="primary" style={{ padding:'4px 14px', fontSize:11, fontWeight:700, display:'flex', alignItems:'center', gap:4 }}>
              <Filter size={12}/> Submit
            </StyledButton>

            <StyledButton type="button" onClick={handleDownloadCSV} variant="default" style={{ padding:'4px 12px', fontSize:11 }}>Download CSV</StyledButton>
            <StyledButton type="button" onClick={() => onRefresh && onRefresh()} variant="default" style={{ padding:'4px 12px', fontSize:11 }}>↺ Refresh</StyledButton>
            <StyledButton type="button" onClick={async () => {
              if (!window.confirm('Clear the signal log?\n\nClosed entries are removed (this cannot be undone). Open entries are kept — Live Trading uses them for strategy positions and exit alerts.')) return
              await fetch('/api/signal_journal', { method:'DELETE' })
              onRefresh && onRefresh()
            }} variant="danger" style={{ padding:'4px 12px', fontSize:11 }}><Trash2 size={11}/> Clear Logs</StyledButton>
          </div>
        </form>
        {dateError && <div style={{ color:V('red'), fontSize:12, marginBottom:8 }}>{dateError}</div>}

        {filteredEntries.length === 0 ? (
          <div style={{ color:V('text-muted'), textAlign:'center', padding:40, fontSize:13 }}>
            No strategy signals found for the selected filters.
          </div>
        ) : (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr>{['Date','Strategy','Instrument','Dir','Entry','SL','T1','T2','Regime','Score','Exit','P&L pts','P&L ₹*','Reason','Status'].map(h=>(
                  <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), borderBottom:`1px solid ${V('border')}`, whiteSpace:'nowrap', fontWeight:600 }}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>{filteredEntries.map((e, i) => {
                const noResult = e.status === 'DAY_END' || e.status === 'VOID'
                return (
                <tr key={i} title={e.void_reason || (e.status === 'DAY_END' ? 'Still open at the next morning; closed at 09:15 with no exit price — not counted' : undefined)}
                    style={{ borderBottom:`1px solid ${V('border-light')}`, background: i%2===0 ? 'transparent' : V('bg-tertiary'), opacity: noResult ? 0.55 : 1 }}>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), whiteSpace:'nowrap', fontSize:10 }}>{toISTDateTime(e.entry_time)}</td>
                  <td style={{ padding:'6px 8px', color:V('text-primary'), fontWeight:500 }}>{_sjStrategyLabel(e.strategy)}</td>
                  <td style={{ padding:'6px 8px', color:V('text-primary'), fontWeight:500 }}>{e.instrument}</td>
                  <td style={{ padding:'6px 8px', color:e.direction==='LONG'?V('green'):V('red'), fontWeight:700 }}>{e.direction}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{fmt(e.entry_price)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('red') }}>{fmt(e.sl)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('green') }}>{fmt(e.target1)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('cyan') }}>{fmt(e.target2)}</td>
                  <td style={{ padding:'6px 8px', fontSize:10, color: e.regime?.startsWith('TRENDING_UP')?V('green'):e.regime?.startsWith('TRENDING_DOWN')?V('red'):V('text-muted') }}>{e.regime ? e.regime.replace('TRENDING_','T_') : '—'}</td>
                  <td style={{ padding:'6px 8px', color:V('accent'), fontSize:10 }}>{e.weighted_score!=null?(e.weighted_score*100).toFixed(0)+'%':e.ml_prob!=null?(e.ml_prob*100).toFixed(0)+'%':'—'}</td>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), fontSize:10 }}>{e.exit_time ? toISTDateTime(e.exit_time) : '—'}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", fontWeight:700, color: noResult ? V('text-muted') : clr(e.pnl_pts) }}>
                    {noResult || e.pnl_pts == null ? '—' : (e.pnl_pts >= 0 ? `+${e.pnl_pts}` : e.pnl_pts)}
                  </td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", fontWeight:700, color: noResult ? V('text-muted') : clr(e.pnl_inr) }}>
                    {noResult || e.pnl_inr == null ? '—' : fmtPnl(e.pnl_inr)}
                  </td>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), fontSize:10 }}>{e.status === 'VOID' ? (e.void_reason || 'Void') : (e.exit_reason || '—')}</td>
                  <td style={{ padding:'6px 8px', color:statusColor(e.status), fontWeight:600, fontSize:10, whiteSpace:'nowrap' }}>{statusLabel(e.status)}</td>
                </tr>
              )})}</tbody>
            </table>
            <div style={{ fontSize:10, color:V('text-muted'), marginTop:8 }}>
              * Theoretical: index points × lot size — what the index move was worth, not the option's P&L. Day-end closes and void rows are shown faded and not counted.
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── CAS Spike Scanner Page ──────────────────────────────────────────────────
// Two independent modes, each its own section: Mode A ("CAS Window",
// 15:05-15:28 IST, stock options only) and Mode B ("Undercurrent", all day,
// stocks + indices). See STRATEGY_REGISTRY.md / the CAS scanner plan for the
// full research behind both.
const _FLOW_SHORT = {
  long_buildup: 'Long Buildup',
  short_buildup: 'Short Buildup',
  short_covering: 'Short Covering',
  long_unwinding: 'Long Unwinding',
  flat: 'Flat',
}

// Detail-column text for a Mode B candidate -- accumulated Vol/OI over the
// persistence window (not an instantaneous snapshot, per the 2026-08-26
// redesign), or the OI-concentration-trend magnitude for that trigger type.
function _casDetail(c) {
  if (c.trigger === 'oi_concentration_trend') {
    return `${c.concentration_trend_pct > 0 ? '+' : ''}${c.concentration_trend_pct} pts`
  }
  const ratio = c.zero_baseline_oi ? '∞' : `${c.accumulation_ratio}x`
  const mins = c.minutes_tracked != null ? ` over ${c.minutes_tracked}m` : ''
  const cluster = c.cluster_neighbors ? ` · ${c.cluster_neighbors} nbr` : ''
  const flow = c.flow ? ` · ${_FLOW_SHORT[c.flow] || c.flow}` : ''
  return `${ratio}${mins}${cluster}${flow}`
}

// Scanner health line for the CAS page: without it, "Nothing flagged" looked the same
// whether the market was quiet, closed, or the scanner had stopped / its token expired.
const _casTime = iso => iso ? new Date(iso).toLocaleString('en-IN', { timeZone:'Asia/Kolkata', weekday:'short', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', hour12:false }) : null

function CasStatusBar({ st }) {
  if (!st) return null
  const tokenBad = st.token && ['expired', 'missing', 'unreadable'].includes(st.token.state)
  let tone = 'green', head = 'Scanning'
  if (!st.enabled) { tone = 'muted'; head = 'Scanner switched off in Settings' }
  else if (tokenBad) { tone = 'red'; head = `CAS Dhan token ${st.token.state} — update it in Settings → Dhan Connection` }
  else if (!st.running || !st.connected) { tone = 'red'; head = 'Scanner not running (no Dhan connection)' }
  else if (!st.market_open) { tone = 'yellow'; head = `Paused — ${st.paused_reason || 'market closed'}` }
  const color = tone === 'green' ? V('green') : tone === 'red' ? V('red') : tone === 'yellow' ? V('yellow') : V('text-muted')
  const parts = []
  if (st.last_sweep_at) parts.push(`Stocks last swept ${_casTime(st.last_sweep_at)} (${st.last_sweep_stocks} stocks, ${st.last_sweep_failures} failed, took ${Math.round((st.last_sweep_seconds || 0) / 60)} min)`)
  else parts.push('No stock sweep yet since the app started')
  if (st.last_index_sweep_at) parts.push(`indices last swept ${_casTime(st.last_index_sweep_at)}`)
  return (
    <div style={{ border:`1px solid color-mix(in srgb, ${color} 35%, transparent)`, background:`color-mix(in srgb, ${color} 8%, transparent)`, borderRadius:V('radius-md'), padding:'10px 14px', display:'flex', flexDirection:'column', gap:3 }}>
      <div style={{ color, fontWeight:700, fontSize:13 }}>● {head}</div>
      <div style={{ color:V('text-muted'), fontSize:11 }}>{parts.join(' · ')}. Lists below are from the last sweep.</div>
    </div>
  )
}

// PAPER strategy (Mode C): "14:45 expiry squeeze" -- forward test, never places orders (cas_paper_squeeze.py)
function CasPaperSection() {
  const [d, setD] = useState(null)
  useEffect(() => {
    const load = () => API.get('/api/cas_paper').then(setD).catch(() => {})
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])
  if (!d) return null
  const s = d.stats || {}
  const th = { textAlign:'left', padding:'6px 8px', color:V('text-muted'), fontSize:11, borderBottom:`1px solid ${V('border')}`, whiteSpace:'nowrap' }
  const td = { padding:'6px 8px', fontSize:12, borderBottom:`1px solid ${V('border-light')}`, whiteSpace:'nowrap' }
  const statusColor = r => r.status === 'OPEN' ? V('yellow') : r.status === 'NO_TRADE' ? V('text-muted') : (r.return_pct > 0 ? V('green') : V('red'))
  const reasonLabel = { STOP:'stop hit', TRAIL:'trailing stop', STAGNANT:'15:15, under +8%', TIME:'15:25 exit' }
  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:8 }}>
        <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Paper strategy — 14:45 expiry squeeze</div>
        <Badge label={d.enabled ? 'PAPER · no orders' : 'OFF'} color={d.enabled ? V('accent') : V('text-muted')} />
      </div>
      <div style={{ color:V('text-muted'), fontSize:12, margin:'6px 0 12px', lineHeight:1.5 }}>
        NIFTY · BANKNIFTY · SENSEX, on each index's expiry day. At 14:45, if the day's trend and the last 15 minutes agree,
        buy the 1-strike in-the-money option at 14:46 (skip under ₹8). No stop until +20% (then entry +7.5%); from +35% trail 15% below the peak;
        exit 15:15 if still under +8%, otherwise by 15:25. Paper P&L after 3% slippage each side, 1 lot.
        Backtest: Jan 2025–Oct 2026 +6.9%/trade, but unseen Jun–Dec 2024 −0.9% — this is a forward test, not a proven edge.
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(5, minmax(0,1fr))', gap:8, marginBottom:12 }}>
        <MetricBox label="Paper trades" value={s.trades ?? 0} />
        <MetricBox label="Win rate" value={s.trades ? `${Math.round(s.wins / s.trades * 100)}%` : '—'} sub={s.trades ? `${s.wins}W / ${s.trades - s.wins}L` : undefined} />
        <MetricBox label="Avg return" value={s.avg_return_pct != null ? `${s.avg_return_pct > 0 ? '+' : ''}${s.avg_return_pct}%` : '—'} color={clr(s.avg_return_pct)} sub="per trade, after slippage" />
        <MetricBox label="Paper P&L" value={fmtPnl(s.rupees || 0)} color={clr(s.rupees)} sub="1 lot each" />
        <MetricBox label="No-trade days" value={s.no_trade_days ?? 0} sub="signal didn't agree" />
      </div>
      {(d.trades || []).length === 0 ? (
        <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:16 }}>
          Nothing yet — it runs at 14:46 on each index's expiry day (Telegram alerts on entry, break-even lock and exit).
        </div>
      ) : (
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>{['Date','Index','Result','Contract','Entry','Stop / peak','Exit','Return','₹ (1 lot)'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>{d.trades.map((r, i) => (
              <tr key={i}>
                <td style={td}>{r.date}</td>
                <td style={td}>{r.index}</td>
                <td style={{ ...td, color: statusColor(r), fontWeight:600 }}>{r.status === 'NO_TRADE' ? 'No trade' : r.status === 'OPEN' ? 'Open' : (r.return_pct > 0 ? 'Win' : 'Loss')}</td>
                <td style={td}>{r.status === 'NO_TRADE' ? <span style={{ color:V('text-muted') }}>{r.reason}</span> : `${r.strike} ${r.side} (1 ITM, lot ${r.lot})`}</td>
                <td style={td}>{r.entry_price != null ? `${r.entry_time?.slice(0,5)} @ ₹${r.entry_price}` : '—'}</td>
                <td style={td}>{r.status === 'OPEN' ? `${r.stop ? `₹${Number(r.stop).toFixed(1)}` : 'none yet'} / ₹${r.peak}` : (r.peak ? `peak ₹${r.peak}` : '—')}</td>
                <td style={td}>{r.exit_price != null ? `${r.exit_time?.slice(0,5)} @ ₹${r.exit_price} (${reasonLabel[r.exit_reason] || r.exit_reason})` : '—'}</td>
                <td style={{ ...td, color: clr(r.return_pct), fontWeight:600 }}>{r.return_pct != null ? `${r.return_pct > 0 ? '+' : ''}${r.return_pct}%` : '—'}</td>
                <td style={{ ...td, color: clr(r.rupees) }}>{r.rupees != null ? fmtPnl(r.rupees) : '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function CasAlertsPage({ alertsA, alertsB, alertsC, atRisk, undercurrent, heatmap }) {
  const [subTab, setSubTab] = useState('alerts')
  const m = window.innerWidth < 768
  const [casStatus, setCasStatus] = useState(null)
  useEffect(() => {
    const load = () => API.get('/api/cas_status').then(setCasStatus).catch(() => {})
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])

  const pillStyle = (active) => ({
    padding: '6px 16px', borderRadius: 20, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: active ? 'color-mix(in srgb, #6366f1 18%, transparent)' : 'transparent',
    color: active ? '#6366f1' : V('text-muted'),
    border: `1px solid ${active ? 'color-mix(in srgb, #6366f1 35%, transparent)' : V('border')}`,
  })

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <CasStatusBar st={casStatus} />
      <CasPaperSection />
      <div style={{ display:'flex', gap:8 }}>
        <div style={pillStyle(subTab === 'alerts')} onClick={() => setSubTab('alerts')}>Alerts</div>
        <div style={pillStyle(subTab === 'heatmap')} onClick={() => setSubTab('heatmap')}>Heatmap</div>
      </div>
      {subTab === 'heatmap' ? <CasHeatmapTab heatmap={heatmap} /> : <CasAlertsTab alertsA={alertsA} alertsB={alertsB} alertsC={alertsC} atRisk={atRisk} undercurrent={undercurrent} />}
    </div>
  )
}

function CasAlertsTab({ alertsA, alertsB, alertsC, atRisk, undercurrent }) {
  const m = window.innerWidth < 768
  const nowIst = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const t = nowIst.getHours() * 60 + nowIst.getMinutes()
  const windowActive = t >= (15 * 60 + 5) && t <= (15 * 60 + 28)

  const thStyle = { textAlign:'left', padding:'8px 10px', color:V('text-muted'), fontSize:11, textTransform:'uppercase', letterSpacing:'0.05em', borderBottom:`1px solid ${V('border')}` }
  const tdStyle = { padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:20 }}>
      {/* ── Mode A: CAS Window ── */}
      <Card>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:14, flexWrap:'wrap', gap:10 }}>
          <div>
            <div style={{ fontSize:16, fontWeight:700, color:V('text-primary') }}>CAS Window</div>
            <div style={{ fontSize:12, color:V('text-muted') }}>Stock options only · near-worthless + expiring soon · reactive alerting only 15:05-15:28 IST</div>
          </div>
          <span style={{
            padding:'4px 12px', borderRadius:20, fontSize:11, fontWeight:600,
            background: windowActive ? 'color-mix(in srgb, #22c55e 18%, transparent)' : V('bg-tertiary'),
            color: windowActive ? '#22c55e' : V('text-muted'),
            border: `1px solid ${windowActive ? 'color-mix(in srgb, #22c55e 35%, transparent)' : V('border')}`,
          }}>{windowActive ? '● Active now' : 'Inactive (visibility only)'}</span>
        </div>

        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(3,1fr)', gap:10, marginBottom:16 }}>
          <MetricBox label="Fired Alerts Today" value={alertsA.length} color={alertsA.length ? '#ef4444' : V('text-primary')} />
          <MetricBox label="At-Risk Shortlist" value={atRisk.length} sub="refreshed after each stock sweep (~15 min)" />
          <MetricBox label="Top Risk Score" value={atRisk[0] ? atRisk[0].score.toFixed(0) : '—'} sub={atRisk[0] ? `${atRisk[0].symbol} ${atRisk[0].strike}${atRisk[0].option_type}` : ''} />
        </div>

        {alertsA.length > 0 && (
          <div style={{ overflowX:'auto', marginBottom:16 }}>
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead><tr>
                {['Time','Symbol','Strike/Type','Expiry','Premium Before → Peak','% Spike'].map(h => <th key={h} style={thStyle}>{h}</th>)}
              </tr></thead>
              <tbody>
                {alertsA.slice(0, 50).map((a, i) => (
                  <tr key={i}>
                    <td style={tdStyle}>{a.time}</td>
                    <td style={tdStyle}>{a.symbol}</td>
                    <td style={tdStyle}>{a.strike}{a.option_type}</td>
                    <td style={tdStyle}>{a.expiry}</td>
                    <td style={tdStyle}>₹{a.premium_before?.toFixed(2)} → ₹{a.premium_peak?.toFixed(2)}</td>
                    <td style={{...tdStyle, color:'#ef4444', fontWeight:600}}>+{a.pct_move}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ fontSize:12, color:V('text-muted'), marginBottom:8, fontWeight:600 }}>At-Risk Shortlist (structural — visible before anything fires)</div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              {['Symbol','Strike/Type','Expiry (days)','Premium','% OTM / OI Share'].map(h => <th key={h} style={thStyle}>{h}</th>)}
            </tr></thead>
            <tbody>
              {atRisk.length === 0 && <tr><td colSpan={5} style={{...tdStyle, color:V('text-muted'), textAlign:'center'}}>Nothing at risk right now</td></tr>}
              {atRisk.slice(0, 30).map((c, i) => (
                <tr key={i}>
                  <td style={tdStyle}>{c.symbol}</td>
                  <td style={tdStyle}>{c.strike}{c.option_type}</td>
                  <td style={tdStyle}>{c.expiry} ({c.days_to_expiry}d)</td>
                  <td style={tdStyle}>₹{c.premium?.toFixed(2)}</td>
                  <td style={tdStyle}>{c.near_atm ? 'Near-ATM' : `${(Math.abs(c.strike - c.spot)/c.spot*100).toFixed(1)}% away`} · OI {c.oi_share_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ── Mode C: Index Burst ── */}
      <Card>
        <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), marginBottom:2 }}>Index Burst</div>
        <div style={{ fontSize:12, color:V('text-muted'), marginBottom:14 }}>NIFTY/BANKNIFTY/SENSEX only · same Vol/OI accumulation signal as Undercurrent, but a much shorter confirm window (minutes, not 30) · shown here only — Telegram off since 3 Oct (a replay showed buying its alerts lost money)</div>

        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(1,1fr)', gap:10, marginBottom:16 }}>
          <MetricBox label="Fired Alerts Today" value={alertsC.length} color={alertsC.length ? '#f59e0b' : V('text-primary')} />
        </div>

        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              {['Time','Symbol','Strike/Type','Trigger','Detail','Bias','Confidence'].map(h => <th key={h} style={thStyle}>{h}</th>)}
            </tr></thead>
            <tbody>
              {alertsC.length === 0 && <tr><td colSpan={7} style={{...tdStyle, color:V('text-muted'), textAlign:'center'}}>No index bursts fired today</td></tr>}
              {alertsC.slice(0, 50).map((a, i) => (
                <tr key={i}>
                  <td style={tdStyle}>{a.time}</td>
                  <td style={tdStyle}>{a.symbol}</td>
                  <td style={tdStyle}>{a.strike ? `${a.strike}${a.option_type}` : '—'}</td>
                  <td style={tdStyle}>{a.trigger === 'oi_concentration_trend' ? 'OI buildup' : 'Vol/OI accumulation'}</td>
                  <td style={tdStyle}>{_casDetail(a)}</td>
                  <td style={{...tdStyle, color: a.bias === 'bullish' ? '#22c55e' : a.bias === 'bearish' ? '#ef4444' : V('text-muted'), fontWeight:600}}>{a.bias ? a.bias[0].toUpperCase()+a.bias.slice(1) : '—'}</td>
                  <td style={tdStyle}>{a.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ── Mode B: Undercurrent ── */}
      <Card>
        <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), marginBottom:2 }}>Undercurrent</div>
        <div style={{ fontSize:12, color:V('text-muted'), marginBottom:14 }}>Fired alerts: stocks only (indices have their own Index Burst alerting above) · currently-flagged list below still spans stocks + indices · Volume/OI ratio, building OI concentration, IV-skew · runs all day, not CAS-specific</div>

        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(2,1fr)', gap:10, marginBottom:16 }}>
          <MetricBox label="Fired Alerts Today" value={alertsB.length} color={alertsB.length ? '#f59e0b' : V('text-primary')} />
          <MetricBox label="Currently Flagged" value={undercurrent.length} sub="refreshed after each stock sweep (~15 min)" />
        </div>

        {alertsB.length > 0 && (
          <div style={{ overflowX:'auto', marginBottom:16 }}>
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead><tr>
                {['Time','Symbol','Strike/Type','Trigger','Detail','Bias','Confidence'].map(h => <th key={h} style={thStyle}>{h}</th>)}
              </tr></thead>
              <tbody>
                {alertsB.slice(0, 50).map((a, i) => (
                  <tr key={i}>
                    <td style={tdStyle}>{a.time}</td>
                    <td style={tdStyle}>{a.symbol}</td>
                    <td style={tdStyle}>{a.strike ? `${a.strike}${a.option_type}` : '—'}</td>
                    <td style={tdStyle}>{a.trigger === 'oi_concentration_trend' ? 'OI buildup' : 'Vol/OI accumulation'}</td>
                    <td style={tdStyle}>{_casDetail(a)}</td>
                    <td style={{...tdStyle, color: a.bias === 'bullish' ? '#22c55e' : a.bias === 'bearish' ? '#ef4444' : V('text-muted'), fontWeight:600}}>{a.bias ? a.bias[0].toUpperCase()+a.bias.slice(1) : '—'}</td>
                    <td style={tdStyle}>{a.confidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ fontSize:12, color:V('text-muted'), marginBottom:8, fontWeight:600 }}>Currently Flagged</div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              {['Symbol','Strike/Type','Trigger','Detail','Bias','Confidence'].map(h => <th key={h} style={thStyle}>{h}</th>)}
            </tr></thead>
            <tbody>
              {undercurrent.length === 0 && <tr><td colSpan={6} style={{...tdStyle, color:V('text-muted'), textAlign:'center'}}>Nothing flagged right now</td></tr>}
              {undercurrent.slice(0, 30).map((c, i) => (
                <tr key={i}>
                  <td style={tdStyle}>{c.symbol}</td>
                  <td style={tdStyle}>{c.strike ? `${c.strike}${c.option_type}` : '—'}</td>
                  <td style={tdStyle}>{c.trigger === 'oi_concentration_trend' ? 'OI buildup' : 'Vol/OI accumulation'}</td>
                  <td style={tdStyle}>{_casDetail(c)}</td>
                  <td style={{...tdStyle, color: c.bias === 'bullish' ? '#22c55e' : c.bias === 'bearish' ? '#ef4444' : V('text-muted'), fontWeight:600}}>{c.bias ? c.bias[0].toUpperCase()+c.bias.slice(1) : '—'}</td>
                  <td style={tdStyle}>{c.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center', padding:'4px 0' }}>
        Awareness only — no automated trading action is taken from either mode.
      </div>
    </div>
  )
}

// Per-underlying heatmap over the FULL F&O universe (not just currently-flagged
// names), with streak continuity across sweeps -- a state view, complementing
// the point-in-time Telegram alert stream so it's clear what's still active
// right now vs what fired once and stopped.
function CasHeatmapTab({ heatmap }) {
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const m = window.innerWidth < 768

  const list = heatmap || []
  const filtered = filter
    ? list.filter(h => h.symbol.toUpperCase().includes(filter.toUpperCase()))
    : list
  // Active cells first (longest-running streak first), then inactive alphabetically.
  const sorted = [...filtered].sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1
    if (a.active) return b.streak_minutes - a.streak_minutes
    return a.symbol.localeCompare(b.symbol)
  })

  const cellColor = (h) => {
    if (!h.active) return { bg: V('bg-tertiary'), fg: V('text-muted'), border: V('border') }
    if (h.dominant_bias === 'bullish') {
      const k = Math.min(Math.abs(h.net_score) / 3, 1)
      return { bg: `color-mix(in srgb, #22c55e ${10 + k * 35}%, transparent)`, fg: '#22c55e', border: 'color-mix(in srgb, #22c55e 45%, transparent)' }
    }
    if (h.dominant_bias === 'bearish') {
      const k = Math.min(Math.abs(h.net_score) / 3, 1)
      return { bg: `color-mix(in srgb, #ef4444 ${10 + k * 35}%, transparent)`, fg: '#ef4444', border: 'color-mix(in srgb, #ef4444 45%, transparent)' }
    }
    return { bg: 'color-mix(in srgb, #f59e0b 20%, transparent)', fg: '#f59e0b', border: 'color-mix(in srgb, #f59e0b 45%, transparent)' }
  }

  const activeCount = list.filter(h => h.active).length
  const bullCount = list.filter(h => h.dominant_bias === 'bullish').length
  const bearCount = list.filter(h => h.dominant_bias === 'bearish').length

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <Card>
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(3,1fr)', gap:10, marginBottom:16 }}>
          <MetricBox label="Currently Active" value={activeCount} sub={`of ${list.length} F&O underlyings`} />
          <MetricBox label="Bullish Lean" value={bullCount} color={bullCount ? '#22c55e' : V('text-primary')} />
          <MetricBox label="Bearish Lean" value={bearCount} color={bearCount ? '#ef4444' : V('text-primary')} />
        </div>

        <input
          type="text" placeholder="Filter by symbol..." value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{
            width:'100%', padding:'8px 12px', marginBottom:14, borderRadius:8, fontSize:13,
            background:V('bg-tertiary'), border:`1px solid ${V('border')}`, color:V('text-primary'),
          }}
        />

        <div style={{
          display:'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${m ? 90 : 110}px, 1fr))`, gap:6,
          maxHeight: 560, overflowY: 'auto', paddingRight: 4,
        }}>
          {sorted.map(h => {
            const c = cellColor(h)
            return (
              <div
                key={h.symbol}
                onClick={() => setSelected(h)}
                title={h.active ? `${h.symbol}: ${h.dominant_bias}, ${h.streak_minutes}m` : h.symbol}
                style={{
                  background: c.bg, color: c.fg, border: `1px solid ${c.border}`,
                  borderRadius: 6, padding: '8px 6px', cursor: 'pointer', textAlign: 'center',
                  display:'flex', flexDirection:'column', gap: 2,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{h.symbol}</div>
                {h.active && <div style={{ fontSize: 10, opacity: 0.85 }}>{h.streak_minutes}m</div>}
              </div>
            )
          })}
          {sorted.length === 0 && (
            <div style={{ gridColumn: '1 / -1', textAlign:'center', color:V('text-muted'), padding: 20 }}>
              {list.length === 0 ? 'Waiting for the first sweep...' : 'No symbols match that filter'}
            </div>
          )}
        </div>
      </Card>

      {selected && (
        <Card>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:10 }}>
            <div style={{ fontSize:15, fontWeight:700, color:V('text-primary') }}>{selected.symbol}</div>
            <div style={{ cursor:'pointer', color:V('text-muted'), fontSize:13 }} onClick={() => setSelected(null)}>Close ✕</div>
          </div>
          {selected.active ? (
            <>
              <div style={{ fontSize:13, color:V('text-muted'), marginBottom:12 }}>
                Dominant bias: <span style={{ color: selected.dominant_bias === 'bullish' ? '#22c55e' : selected.dominant_bias === 'bearish' ? '#ef4444' : '#f59e0b', fontWeight:600 }}>
                  {selected.dominant_bias[0].toUpperCase() + selected.dominant_bias.slice(1)}
                </span> · continuous for {selected.streak_minutes}m ({selected.sweep_count} sweeps) · {selected.bullish_count} bullish leg{selected.bullish_count===1?'':'s'}, {selected.bearish_count} bearish leg{selected.bearish_count===1?'':'s'}
              </div>
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead><tr>
                    {['Strike/Type','Trigger','Detail','Bias','Confidence'].map(hd => (
                      <th key={hd} style={{ textAlign:'left', padding:'8px 10px', color:V('text-muted'), fontSize:11, textTransform:'uppercase', letterSpacing:'0.05em', borderBottom:`1px solid ${V('border')}` }}>{hd}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {selected.strikes.map((s, i) => (
                      <tr key={i}>
                        <td style={{ padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }}>{s.strike ? `${s.strike}${s.option_type}` : '—'}</td>
                        <td style={{ padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }}>{s.trigger === 'oi_concentration_trend' ? 'OI buildup' : 'Vol/OI accumulation'}</td>
                        <td style={{ padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }}>{_casDetail(s)}</td>
                        <td style={{ padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13, color: s.bias === 'bullish' ? '#22c55e' : s.bias === 'bearish' ? '#ef4444' : V('text-muted'), fontWeight:600 }}>{s.bias ? s.bias[0].toUpperCase()+s.bias.slice(1) : '—'}</td>
                        <td style={{ padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }}>{s.confidence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div style={{ fontSize:13, color:V('text-muted') }}>Nothing flagged for {selected.symbol} right now.</div>
          )}
        </Card>
      )}

      <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center', padding:'4px 0' }}>
        Updates every ~5 min · streak resets on a bias flip or a gap in flagging · Awareness only — no automated trading action is taken.
      </div>
    </div>
  )
}

// ── Investment Analysis Page ────────────────────────────────────────────────
// Separate from live options trading: its own backend (bse_client.py +
// tickertape_client.py + investment_api.py), fully self-contained state
// (search-driven, not a polling dashboard), no shared state with the rest
// of the app. Company Analysis and IPO Review are functional; Scanner is
// a placeholder until its own backend exists.
function InvestmentAnalysisPage() {
  const [subTab, setSubTab] = useState('company')

  const pillStyle = (active) => ({
    padding: '6px 16px', borderRadius: 20, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: active ? 'color-mix(in srgb, #6366f1 18%, transparent)' : 'transparent',
    color: active ? '#6366f1' : V('text-muted'),
    border: `1px solid ${active ? 'color-mix(in srgb, #6366f1 35%, transparent)' : V('border')}`,
  })

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
        <div style={pillStyle(subTab === 'company')} onClick={() => setSubTab('company')}>Company Analysis</div>
        <div style={pillStyle(subTab === 'scanner')} onClick={() => setSubTab('scanner')}>Stock Scanner</div>
        <div style={pillStyle(subTab === 'ipo')} onClick={() => setSubTab('ipo')}>IPO Review</div>
      </div>
      {subTab === 'company' && <CompanyAnalysisTab />}
      {subTab === 'scanner' && (
        <InvestmentComingSoon title="Stock Scanner"
          note="Ranks the full BSE-listed universe on value, quality, momentum, growth and technical factors, with an AI-reasoned shortlist on top. Backend not built yet." />
      )}
      {subTab === 'ipo' && <IpoReviewTab />}
    </div>
  )
}

function InvestmentComingSoon({ title, note }) {
  return (
    <Card>
      <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), marginBottom:8 }}>{title}</div>
      <div style={{ fontSize:13, color:V('text-muted') }}>{note}</div>
    </Card>
  )
}

// ── Company Analysis: "should I invest now, wait, or at what price?" ───────
// Renders investment_valuation's deterministic model (always available) plus
// the AI debate (bull vs bear with rebuttals, then a judge) when it ran.
const STANCE_META = {
  accumulate_now:          { label: 'Attractive — accumulate now',                          color: '#22c55e' },
  start_small_and_stagger: { label: 'Reasonable — start small, add on dips',                color: '#84cc16' },
  wait_for_better_price:   { label: 'Fairly priced, thin margin of safety — wait for a dip', color: '#f59e0b' },
  avoid_for_now:           { label: 'Expensive on these assumptions — stay away for now',   color: '#ef4444' },
  no_valuation_call:       { label: 'Low-reliability company — no valuation call',           color: '#ef4444' },
}
const EDGE_META = { bull: { label: 'Bull', color: '#22c55e' }, bear: { label: 'Bear', color: '#ef4444' }, even: { label: 'Even', color: '#94a3b8' } }
const DEBATE_TH = { textAlign:'left', padding:'7px 10px', color:V('text-muted'), fontSize:10, textTransform:'uppercase', letterSpacing:'0.05em', borderBottom:`1px solid ${V('border')}`, whiteSpace:'nowrap' }
const DEBATE_TD = { padding:'7px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:12, verticalAlign:'top' }

function Chip({ color, children }) {
  return (
    <span style={{ padding:'3px 10px', borderRadius:20, fontSize:11, fontWeight:600, whiteSpace:'nowrap',
      background:`color-mix(in srgb, ${color} 16%, transparent)`, color, border:`1px solid color-mix(in srgb, ${color} 35%, transparent)` }}>
      {children}
    </span>
  )
}

function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom:8 }}>
      <div style={{ fontSize:13, fontWeight:700, color:V('text-primary') }}>{children}</div>
      {sub && <div style={{ fontSize:11, color:V('text-muted'), marginTop:2, lineHeight:1.45 }}>{sub}</div>}
    </div>
  )
}

function Panel({ children, tint, style }) {
  const bg = tint ? `color-mix(in srgb, ${tint} 7%, transparent)` : V('bg-tertiary')
  const bd = tint ? `1px solid color-mix(in srgb, ${tint} 25%, transparent)` : `1px solid ${V('border-light')}`
  return <div style={{ background:bg, border:bd, borderRadius:V('radius-md'), padding:14, ...style }}>{children}</div>
}

const fmtRs = (v) => v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
const fmtPct = (v, sign) => v == null ? '—' : `${sign && v > 0 ? '+' : ''}${v}%`
const retColor = (v) => v == null ? V('text-muted') : v >= 12 ? '#22c55e' : v >= 8 ? '#f59e0b' : '#ef4444'

function ValuationModelPanel({ vm, m }) {
  if (!vm) return null
  if (!vm.available) {
    return <Panel><div style={{ fontSize:12, color:V('text-muted') }}>The price-vs-value model could not be built: {vm.reason}</div></Panel>
  }
  const rel = vm.reliability
  if (vm.levels_withheld) {
    return (
      <Panel tint="#ef4444">
        <div style={{ fontSize:12, fontWeight:700, color:'#ef4444', marginBottom:4 }}>Price levels and return projections are not shown</div>
        <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.5, marginBottom:6 }}>
          This company's data is too unreliable to support them — a "fair entry price" built on it would look authoritative and not be. Reasons:
        </div>
        <ul style={{ margin:0, paddingLeft:18, fontSize:12, color:V('text-primary'), lineHeight:1.55 }}>
          {(rel?.reasons || []).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      </Panel>
    )
  }
  const sc = vm.scenarios
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      {rel && rel.level !== 'high' && (
        <Panel tint={rel.level === 'low' ? '#ef4444' : '#f59e0b'}>
          <div style={{ fontSize:12, fontWeight:700, color: rel.level === 'low' ? '#ef4444' : '#f59e0b', marginBottom:4 }}>
            Model reliability: {rel.level}{vm.stance_capped_for_low_reliability ? ' — stance capped, this model cannot support a buy call for this company' : ''}
          </div>
          <ul style={{ margin:0, paddingLeft:18, fontSize:12, color:V('text-primary'), lineHeight:1.5 }}>
            {rel.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Panel>
      )}

      <FairValuePanel fv={vm.fair_value} price={vm.current_price} m={m} />

      <ReverseDcfPanel rd={vm.reverse_dcf} m={m} />

      <div>
        <SectionTitle sub={`What you would earn per year over ${vm.horizon_years} years if you bought at today's price (${fmtRs(vm.current_price)}), including the ${vm.dividend_yield_pct ?? 0}% dividend yield. Growth and exit P/E are haircuts of the company's own history — see assumptions below.`}>
          5-year return scenarios at today's price
        </SectionTitle>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              <th style={DEBATE_TH}></th><th style={DEBATE_TH}>EPS growth / yr</th><th style={DEBATE_TH}>Exit P/E</th>
              <th style={DEBATE_TH}>EPS in 5 yrs</th><th style={DEBATE_TH}>Implied price in 5 yrs</th><th style={DEBATE_TH}>Return / yr</th>
            </tr></thead>
            <tbody>
              {[['bear', 'Bear', '#ef4444'], ['base', 'Base', '#f59e0b'], ['bull', 'Bull', '#22c55e']].map(([k, name, c]) => (
                <tr key={k}>
                  <td style={{ ...DEBATE_TD, fontWeight:700, color:c }}>{name}</td>
                  <td style={DEBATE_TD}>{sc[k].eps_growth_pct}%</td>
                  <td style={DEBATE_TD}>{sc[k].exit_pe}×</td>
                  <td style={DEBATE_TD}>₹{sc[k].eps_in_5y}</td>
                  <td style={DEBATE_TD}>{fmtRs(sc[k].value_in_5y)}</td>
                  <td style={{ ...DEBATE_TD, fontWeight:700, color:retColor(sc[k].total_return_pct_pa) }}>{fmtPct(sc[k].total_return_pct_pa, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <SectionTitle sub="The same maths run backwards: the price at which the BASE case would earn each target return. If today's price is already below a level, that level is already met.">
          Entry price ladder — what price should I pay?
        </SectionTitle>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              <th style={DEBATE_TH}>Level</th><th style={DEBATE_TH}>Price</th><th style={DEBATE_TH}>vs today</th>
              <th style={DEBATE_TH}>Bear</th><th style={DEBATE_TH}>Base</th><th style={DEBATE_TH}>Bull</th>
            </tr></thead>
            <tbody>
              {vm.entry_ladder.map((l, i) => {
                const met = l.price >= vm.current_price
                return (
                  <tr key={i}>
                    <td style={DEBATE_TD}>{l.name}{met && <span style={{ marginLeft:6, fontSize:10, color:'#22c55e', fontWeight:600 }}>✓ today's price already qualifies</span>}</td>
                    <td style={{ ...DEBATE_TD, fontWeight:700 }}>{fmtRs(l.price)}</td>
                    <td style={{ ...DEBATE_TD, color: l.vs_current_pct >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(l.vs_current_pct, true)}</td>
                    {['bear', 'base', 'bull'].map(k => (
                      <td key={k} style={{ ...DEBATE_TD, color:retColor(l.return_if_bought_here_pct_pa[k]) }}>{fmtPct(l.return_if_bought_here_pct_pa[k], true)}/yr</td>
                    ))}
                  </tr>
                )
              })}
              {vm.bear_case_safe_price && (
                <tr>
                  <td style={DEBATE_TD}>Defensive floor <span style={{ color:V('text-muted') }}>(even the bear case earns ~6%/yr)</span></td>
                  <td style={{ ...DEBATE_TD, fontWeight:700 }}>{fmtRs(vm.bear_case_safe_price.price)}</td>
                  <td style={{ ...DEBATE_TD, color: vm.bear_case_safe_price.price >= vm.current_price ? '#22c55e' : '#ef4444' }}>
                    {fmtPct(Math.round((vm.bear_case_safe_price.price - vm.current_price) / vm.current_price * 1000) / 10, true)}
                  </td>
                  <td style={DEBATE_TD} colSpan={3}></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {rel?.level === 'low' ? (
        <Panel>
          <div style={{ fontSize:12, color:V('text-muted'), lineHeight:1.5 }}>
            Staged-entry prices are not shown: this company's earnings history is too erratic for the model to support a specific entry price. The ladder above is indicative only.
          </div>
        </Panel>
      ) : (
      <div>
        <SectionTitle sub="Split the money you plan to invest across these steps rather than betting on one day's price. Later steps only trigger if the price actually falls to that level.">
          Suggested staged entry
        </SectionTitle>
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : `repeat(${vm.tranche_plan.length}, 1fr)`, gap:10 }}>
          {vm.tranche_plan.map((t, i) => (
            <MetricBox key={i} label={`Step ${i + 1}${t.share_pct ? ` — ${t.share_pct}% of planned money` : ''}`}
              value={t.share_pct === 0 ? 'Wait' : fmtRs(t.price)} sub={t.when} color={t.share_pct === 0 ? V('text-muted') : undefined} />
          ))}
        </div>
      </div>
      )}

      <WaitingEvidencePanel we={vm.waiting_evidence} m={m} />

      <TrackRecordPanel m={m} />

      <details>
        <summary style={{ cursor:'pointer', fontSize:12, fontWeight:600, color:'#6366f1' }}>Assumptions behind these numbers</summary>
        <ul style={{ margin:'8px 0 0', paddingLeft:18, fontSize:11, color:V('text-muted'), lineHeight:1.6 }}>
          <li>Trailing EPS ₹{vm.eps_ttm}, today's P/E {vm.current_pe}×; industry P/E {vm.industry_pe ?? 'n/a'}×; the stock's own historical median P/E {vm.own_history_median_pe ?? 'n/a'}×; exit-multiple anchor {vm.anchor_pe}×.</li>
          <li>Growth basis: {vm.growth_basis.historical_eps_growth_pct}% ({vm.growth_basis.source}){vm.growth_basis.faded_toward_long_run_pct ? `, pulled ${vm.growth_basis.faded_toward_long_run_pct}% of the way toward a long-run ${vm.growth_basis.long_run_growth_pct}% → ${vm.growth_basis.growth_used_before_haircut_pct}% before the haircut` : ''}.</li>
          {vm.assumptions.map((a, i) => <li key={i}>{a}</li>)}
          {vm.stance_downgraded_for?.length > 0 && <li>Baseline stance was lowered one step for: {vm.stance_downgraded_for.join(', ')}.</li>}
          <li>This is a structured way to compare price with value using visible assumptions — not a forecast.</li>
        </ul>
      </details>
    </div>
  )
}

// ── Evidence check: every number the AI wrote is checked against the data it was given ──
// Click a number to see where it came from; red = could not be traced. The check verifies
// numbers only — it cannot verify judgement, or whether the data provider was right.
const EvidenceContext = React.createContext(null)

const EVIDENCE_STYLE = {
  traced:     { color: '#22c55e', label: 'Traced to the data' },
  value_only: { color: '#f59e0b', label: 'Matches a data value by number only' },
  computed:   { color: '#22c55e', label: 'Calculated from the data' },
  threshold:  { color: '#94a3b8', label: 'A level the AI suggests watching' },
  unverified: { color: '#ef4444', label: 'Could not be traced' },
}

function EvidencePopover({ it }) {
  const st = it.status === 'traced' && it.quality === 'value_only' ? 'value_only' : it.status
  const meta = EVIDENCE_STYLE[st] || EVIDENCE_STYLE.unverified
  return (
    <span onClick={e => e.stopPropagation()} style={{ position:'absolute', zIndex:60, left:0, top:'1.5em', minWidth:230, maxWidth:340, padding:'8px 10px', borderRadius:8,
      background:V('bg-secondary'), border:`1px solid ${V('border')}`, boxShadow:V('shadow-md'), fontSize:11, lineHeight:1.5, fontWeight:400, fontStyle:'normal', textAlign:'left', color:V('text-primary'), whiteSpace:'normal' }}>
      <b style={{ color:meta.color }}>{meta.label}</b>
      {it.status === 'traced' && (it.sources || []).map((src, i) => (
        <div key={i} style={{ marginTop:3 }}>{src.label} = <b>{src.value}</b><div style={{ color:V('text-muted'), fontSize:10 }}>{src.path}</div></div>
      ))}
      {it.status === 'traced' && it.quality === 'value_only' && <div style={{ marginTop:4, color:V('text-muted') }}>The label is not clear from the sentence — check it means the same thing.</div>}
      {it.status === 'computed' && <div style={{ marginTop:3 }}>{it.formula}</div>}
      {it.status === 'threshold' && <div style={{ marginTop:3, color:V('text-muted') }}>The AI proposing a level to watch, not a claim about the data.</div>}
      {it.status === 'unverified' && <div style={{ marginTop:3 }}>This number is not in, and does not follow from, the data the AI was given. Treat it with caution.</div>}
    </span>
  )
}

function Ev({ path, text }) {
  const ctx = React.useContext(EvidenceContext)
  const [open, setOpen] = useState(null)
  const items = ctx?.byPath?.[path]
  if (!text || !items || !items.length) return <>{text}</>
  const nodes = []
  let pos = 0
  items.forEach((it, i) => {
    if (it.start < pos || it.end > text.length) return
    nodes.push(text.slice(pos, it.start))
    const st = it.status === 'traced' && it.quality === 'value_only' ? 'value_only' : it.status
    const color = (EVIDENCE_STYLE[st] || EVIDENCE_STYLE.unverified).color
    const bad = it.status === 'unverified'
    nodes.push(
      <span key={i} onClick={e => { e.stopPropagation(); setOpen(open === i ? null : i) }}
        style={{ position:'relative', cursor:'pointer', borderBottom: bad ? `2px solid ${color}` : `1px dotted ${color}`,
                 background: bad ? 'color-mix(in srgb, #ef4444 14%, transparent)' : 'transparent' }}>
        {text.slice(it.start, it.end)}
        {open === i && <EvidencePopover it={it} />}
      </span>
    )
    pos = it.end
  })
  nodes.push(text.slice(pos))
  return <>{nodes}</>
}

function EvidenceBadge({ check }) {
  if (!check) return null
  const checked = check.total - check.threshold
  const strong = check.traced + check.computed
  const un = check.unverified
  const color = un === 0 ? '#22c55e' : un <= 3 ? '#f59e0b' : '#ef4444'
  const flagged = (check.items || []).filter(it => it.status === 'unverified' || (it.status === 'traced' && it.quality === 'value_only'))
  return (
    <details style={{ margin:'0 0 12px' }}>
      <summary style={{ cursor:'pointer', fontSize:12, fontWeight:600, color }}>
        {un === 0 ? '✓' : '⚠'} Evidence check: {strong} of {checked} figures traced to the data the AI was given
        {check.value_only ? ` · ${check.value_only} match a data value by number only` : ''}{un ? ` · ${un} could not be traced (underlined red below)` : ''}
      </summary>
      <div style={{ marginTop:8, padding:'10px 12px', borderRadius:V('radius-md'), background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, fontSize:12, lineHeight:1.55, color:V('text-primary') }}>
        <div><b>Click any number</b> in the analysis to see where it came from. {check.scope}</div>
        {flagged.length > 0 && (
          <div style={{ marginTop:8 }}>
            <b>Worth a second look:</b>
            <ul style={{ margin:'4px 0 0', paddingLeft:18 }}>
              {flagged.slice(0, 12).map((it, i) => (
                <li key={i} style={{ color: it.status === 'unverified' ? '#ef4444' : V('text-primary') }}>
                  <b>{it.figure}</b> — {it.status === 'unverified' ? 'not found in the data' : 'matches a data value by number only'}: <span style={{ color:V('text-muted') }}>“…{it.context}…”</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  )
}

const FV_AGREEMENT = {
  strong:   { label: 'Methods agree',          color: '#22c55e' },
  moderate: { label: 'Methods partly agree',   color: '#f59e0b' },
  weak:     { label: 'Methods disagree widely', color: '#ef4444' },
  limited:  { label: 'Only two methods',       color: '#94a3b8' },
}
const FV_POSITION = { above: { label: 'Price is above fair value', color: '#ef4444' }, inside: { label: 'Price is inside fair value', color: '#f59e0b' }, below: { label: 'Price is below fair value', color: '#22c55e' } }

// What is it worth? Four independent methods drawn on one price axis, with today's price marked.
function FairValuePanel({ fv, price, m }) {
  if (!fv || !fv.available) return null
  const ag = FV_AGREEMENT[fv.agreement] || FV_AGREEMENT.limited
  const pos = FV_POSITION[fv.price_position] || FV_POSITION.inside
  const lo = Math.min(price, ...fv.methods.map(x => x.low)) * 0.94
  const hi = Math.max(price, ...fv.methods.map(x => x.high)) * 1.04
  const at = v => `${((v - lo) / (hi - lo)) * 100}%`
  const verdictColor = { above: '#ef4444', inside: '#f59e0b', below: '#22c55e' }
  return (
    <div>
      <SectionTitle sub="One method is one opinion. Here the stock is valued four independent ways; when they land in the same place that means something, and when they scatter the honest answer is a wide range.">
        What is it worth? <Chip color={ag.color}>{ag.label}</Chip>
      </SectionTitle>
      <Panel tint={pos.color}>
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:12 }}><b style={{ color:pos.color }}>{pos.label}.</b> {fv.text}</div>
        <div style={{ position:'relative', margin:'0 4px' }}>
          <div style={{ position:'absolute', top:0, bottom:0, left:at(fv.zone_low), width:`calc(${at(fv.zone_high)} - ${at(fv.zone_low)})`, background:'color-mix(in srgb, #3b82f6 12%, transparent)', borderLeft:'1px dashed #3b82f6', borderRight:'1px dashed #3b82f6' }} />
          <div style={{ position:'absolute', top:0, bottom:0, left:at(price), borderLeft:'2px solid #ef4444', zIndex:2 }}>
            <span style={{ position:'absolute', top:-16, left:-30, fontSize:10, fontWeight:700, color:'#ef4444', whiteSpace:'nowrap' }}>Price {fmtRs(price)}</span>
          </div>
          {fv.methods.map(x => (
            <div key={x.key} style={{ position:'relative', display:'grid', gridTemplateColumns: m ? '1fr' : '150px 1fr', alignItems:'center', gap:m ? 2 : 8, padding:'7px 0' }}>
              <div style={{ fontSize:12, fontWeight:600, color:V('text-primary') }}>{x.label}</div>
              <div style={{ position:'relative', height:22 }}>
                <div style={{ position:'absolute', top:8, height:6, left:at(x.low), width:`calc(${at(x.high)} - ${at(x.low)})`, borderRadius:3, background:verdictColor[x.price_vs_zone], opacity:0.55 }} />
                <div style={{ position:'absolute', top:3, height:16, left:at(x.mid), borderLeft:`2px solid ${V('text-primary')}` }} />
                <span style={{ position:'absolute', top:-2, left:at(x.low), fontSize:9, color:V('text-muted'), transform:'translateX(-100%)', paddingRight:3, whiteSpace:'nowrap' }}>{fmtRs(x.low)}</span>
                <span style={{ position:'absolute', top:-2, left:at(x.high), fontSize:9, color:V('text-muted'), paddingLeft:3, whiteSpace:'nowrap' }}>{fmtRs(x.high)}</span>
              </div>
            </div>
          ))}
        </div>
        <div style={{ fontSize:11, color:V('text-muted'), marginTop:6 }}>Bar = each method's fair range, tick = its middle; green = price below the range (cheap on that method), amber = inside, red = above. Blue band = the consensus zone ({fmtRs(fv.zone_low)} to {fmtRs(fv.zone_high)}).</div>
        <details style={{ marginTop:8 }}>
          <summary style={{ cursor:'pointer', fontSize:12, color:V('text-secondary') }}>How each method works</summary>
          <ul style={{ margin:'6px 0 0', paddingLeft:18, fontSize:12, lineHeight:1.55, color:V('text-primary') }}>
            {fv.methods.map(x => <li key={x.key}><b>{x.label}:</b> {x.basis}</li>)}
          </ul>
        </details>
        <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5, marginTop:8 }}>{fv.caution}</div>
      </Panel>
    </div>
  )
}

const RDCF_META = {
  demanding:   { label: 'Demanding',              color: '#ef4444' },
  in_line:     { label: 'In line with its record', color: '#f59e0b' },
  undemanding: { label: 'Undemanding',            color: '#22c55e' },
  unknown:     { label: 'No record to compare',   color: '#94a3b8' },
}

// Reverse DCF: no forecast at all — solve for the growth today's price already requires.
// Evidence for "should I wait?": how often this stock's own history dipped, and whether waiting paid.
function WaitingEvidencePanel({ we, m }) {
  if (!we || !we.available) return null
  const pctCell = v => (v == null ? '—' : `${v}%`)
  return (
    <div>
      <SectionTitle sub="Advice to 'wait for a dip' is only useful if such a dip tends to happen, and only worth it if waiting doesn't cost more than it saves. This measures both on this stock's own price history.">
        Does waiting for a lower price pay?
      </SectionTitle>
      <Panel>
        {we.verdict_label && <div style={{ fontSize:13, fontWeight:700, marginBottom:6, color: we.verdict === 'favours_waiting' ? '#22c55e' : we.verdict === 'against_waiting' ? '#ef4444' : '#f59e0b' }}>{we.verdict_label}</div>}
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:10 }}>{we.text}</div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse', minWidth: m ? 620 : 0 }}>
            <thead>
              <tr>
                <th style={DEBATE_TH}>If you wait for…</th>
                <th style={DEBATE_TH}>Reached within 3 / 6 / 12 months</th>
                <th style={DEBATE_TH}>Typical wait</th>
                <th style={DEBATE_TH}>Waiting beat buying now</th>
                <th style={DEBATE_TH}>2-yr return: now vs waiting</th>
              </tr>
            </thead>
            <tbody>
              {we.rows.map((r, i) => {
                const w = r.wait_vs_buy_now_2y
                return (
                  <tr key={i}>
                    <td style={DEBATE_TD}><b>{fmtRs(r.price)}</b> <span style={{ color:V('text-muted') }}>({r.drop_pct}% lower)</span><div style={{ fontSize:10, color:V('text-muted') }}>{r.label}</div></td>
                    <td style={DEBATE_TD}>{pctCell(r.history_touch_pct['3m'])} / {pctCell(r.history_touch_pct['6m'])} / <b>{pctCell(r.history_touch_pct['12m'])}</b></td>
                    <td style={DEBATE_TD}>{r.median_weeks_to_reach_when_it_did != null ? `${r.median_weeks_to_reach_when_it_did} weeks` : '—'}</td>
                    <td style={DEBATE_TD}>{w ? <b style={{ color: w.waiting_beat_buying_now_pct >= 50 ? '#22c55e' : '#ef4444' }}>{w.waiting_beat_buying_now_pct}%</b> : '—'}{w && <div style={{ fontSize:10, color:V('text-muted') }}>order filled {w.order_filled_pct}%</div>}</td>
                    <td style={DEBATE_TD}>{w ? `${w.avg_price_return_buy_now_pct}% vs ${w.avg_price_return_wait_pct}%` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5, marginTop:8 }}>
          How it is measured: for every starting week in {we.history_years} years of prices, did the price fall to the level within 3, 6 or 12 months? "Waiting" = leave a buy order at that level for up to a year, buy anyway at the one-year mark if it never filled, and compare the 2-year price return with buying on the starting week. {we.caution}
        </div>
      </Panel>
    </div>
  )
}

const STANCE_NAMES = { accumulate_now: 'Accumulate now', start_small_and_stagger: 'Start small, add on dips', wait_for_better_price: 'Wait for a dip', avoid_for_now: 'Avoid for now' }
const FV_POS_NAMES = { below: 'Priced below the fair-value zone', inside: 'Priced inside it', above: 'Priced above it' }

// How well has this method actually worked? Loaded on demand from the offline back-test.
function TrackRecordPanel({ m }) {
  const [tr, setTr] = useState(null)
  const [loading, setLoading] = useState(false)
  const load = () => {
    if (tr || loading) return
    setLoading(true)
    fetch('/api/investment/model-track-record').then(r => r.json()).then(setTr).catch(() => setTr({ available: false })).finally(() => setLoading(false))
  }
  const h = tr?.available ? tr.horizons?.['3y'] : null
  const table = (title, rows, names) => (
    <div style={{ marginTop:10, overflowX:'auto' }}>
      <div style={{ fontSize:11, fontWeight:700, color:V('text-secondary'), marginBottom:4 }}>{title}</div>
      <table style={{ width:'100%', borderCollapse:'collapse' }}>
        <thead><tr><th style={DEBATE_TH}>Group</th><th style={DEBATE_TH}>Cases</th><th style={DEBATE_TH}>Avg return a year</th><th style={DEBATE_TH}>Beat the typical stock</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.bucket}><td style={DEBATE_TD}>{names[r.bucket] || r.bucket}</td><td style={DEBATE_TD}>{r.n}</td><td style={DEBATE_TD}>{r.mean_return_pct}%</td>
              <td style={DEBATE_TD}><b style={{ color: r.beat_median_stock_pct >= 55 ? '#22c55e' : r.beat_median_stock_pct <= 45 ? '#ef4444' : V('text-primary') }}>{r.beat_median_stock_pct}%</b></td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  return (
    <details onToggle={e => { if (e.target.open) load() }}>
      <summary style={{ cursor:'pointer', fontSize:12, fontWeight:600, color:'#6366f1' }}>How well has this method worked in the past? (back-test)</summary>
      <div style={{ marginTop:8 }}>
        {loading && <div style={{ fontSize:12, color:V('text-muted') }}>Loading…</div>}
        {tr && !tr.available && <div style={{ fontSize:12, color:V('text-muted') }}>The back-test has not been run yet.</div>}
        {h && (
          <Panel>
            <div style={{ fontSize:12, color:V('text-muted'), marginBottom:8 }}>
              The same model was re-run on {tr.universe.stocks} large Indian companies at {tr.as_of_dates[0]} … {tr.as_of_dates[1]}, using only what was knowable on each date, and compared with what the stocks actually returned afterwards ({h.observations} cases at the 3-year horizon).
            </div>
            <ul style={{ margin:0, paddingLeft:18, fontSize:13, lineHeight:1.6, color:V('text-primary') }}>
              {(tr.findings || []).map((f, i) => <li key={i} style={{ marginBottom:6 }}>{f}</li>)}
            </ul>
            {table('3-year outcomes by the model’s stance', h.by_stance, STANCE_NAMES)}
            {table('3-year outcomes by where the price sat against the fair-value zone', h.by_fair_value_position, FV_POS_NAMES)}
            <div style={{ marginTop:10, fontSize:11, color:V('text-muted'), lineHeight:1.55 }}>
              <b>Limits of this test:</b>
              <ul style={{ margin:'4px 0 0', paddingLeft:18 }}>{(tr.limits || []).map((l, i) => <li key={i}>{l}</li>)}</ul>
              <div style={{ marginTop:4 }}>Not yet validated: whether "the methods agree" predicts anything (the small samples were inconsistent), and the reverse-DCF "demanding / undemanding" label beyond the same cheap-versus-dear signal. Run {tr.generated_at?.slice(0, 10)}.</div>
            </div>
          </Panel>
        )}
      </div>
    </details>
  )
}

function ReverseDcfPanel({ rd, m }) {
  if (!rd) return null
  const meta = RDCF_META[rd.level] || RDCF_META.unknown
  return (
    <div>
      <SectionTitle sub="Instead of forecasting, this works backwards from today's price: what earnings growth does the price already require? You decide whether that is plausible.">
        What today's price already assumes <Chip color={meta.color}>{meta.label}</Chip>
      </SectionTitle>
      <Panel tint={meta.color}>
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(3,1fr)', gap:10, marginBottom:10 }}>
          <MetricBox label="Growth the price needs" value={`${rd.needed_growth_pct}%/yr`} sub={`to earn ${rd.target_pct}% a year over 5 years`} color={meta.color} />
          <MetricBox label="The company achieved" value={rd.historical_growth_pct != null ? `${rd.historical_growth_pct}%/yr` : '—'} sub={rd.historical_source || 'no usable record'} />
          <MetricBox label="Our base case assumes" value={`${rd.model_base_growth_pct}%/yr`} sub="after our fade and haircut" />
        </div>
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:10 }}>{rd.text}</div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr><th style={DEBATE_TH}>Growth needed a year, if…</th><th style={DEBATE_TH}>to earn 9%</th><th style={DEBATE_TH}>to earn 12%</th><th style={DEBATE_TH}>to earn 15%</th></tr></thead>
            <tbody>
              {(rd.grid || []).map((g, i) => (
                <tr key={i}>
                  <td style={DEBATE_TD}>{g.label}</td>
                  {['9', '12', '15'].map(t => <td key={t} style={{ ...DEBATE_TD, fontWeight:600 }}>{g.needed_growth_pct[t]}%</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5, marginTop:8 }}>{rd.caution}</div>
      </Panel>
    </div>
  )
}

function DebateSide({ side, data, m }) {
  const isBull = side === 'bull'
  const c = isBull ? '#22c55e' : '#ef4444'
  const o = data.opening, r = data.rebuttal
  return (
    <Panel tint={c} style={{ display:'flex', flexDirection:'column', gap:10 }}>
      <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:c, fontWeight:700 }}>{isBull ? 'Bull case — for buying now' : 'Bear case — against buying now'}</div>
      <div style={{ fontSize:13, fontWeight:600, color:V('text-primary'), lineHeight:1.45 }}><Ev path={`${side}.opening.thesis`} text={o.thesis} /></div>
      {(o.arguments || []).map((a, i) => (
        <div key={i} style={{ borderLeft:`3px solid ${c}`, paddingLeft:10 }}>
          <div style={{ fontSize:12, fontWeight:700, color:V('text-primary') }}>{i + 1}. <Ev path={`${side}.opening.arguments[${i}].headline`} text={a.headline} /></div>
          <div style={{ fontSize:11, color:V('text-muted'), marginTop:3, lineHeight:1.5 }}><b>Evidence:</b> <Ev path={`${side}.opening.arguments[${i}].evidence`} text={a.evidence} /></div>
          <div style={{ fontSize:12, color:V('text-primary'), marginTop:3, lineHeight:1.5 }}><Ev path={`${side}.opening.arguments[${i}].why_it_matters`} text={a.why_it_matters} /></div>
        </div>
      ))}
      <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.5 }}><b>What must be true:</b> <Ev path={`${side}.opening.what_must_be_true`} text={o.what_must_be_true} /></div>
      <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.5 }}><b>Own weak spot:</b> <Ev path={`${side}.opening.own_weak_spot`} text={o.own_weak_spot} /></div>
      <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.5 }}><b>Would be proven wrong if:</b> <Ev path={`${side}.opening.invalidation`} text={o.invalidation} /></div>

      <div style={{ borderTop:`1px dashed ${V('border')}`, paddingTop:10, display:'flex', flexDirection:'column', gap:8 }}>
        <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), fontWeight:600 }}>Rebuttal to the {isBull ? 'bear' : 'bull'}</div>
        {(r?.rebuttals || []).map((x, i) => (
          <div key={i} style={{ fontSize:12, lineHeight:1.5 }}>
            <div style={{ color:V('text-muted'), fontStyle:'italic' }}>“<Ev path={`${side}.rebuttal.rebuttals[${i}].their_point`} text={x.their_point} />”</div>
            <div style={{ color:V('text-primary'), marginTop:3 }}><Ev path={`${side}.rebuttal.rebuttals[${i}].your_response`} text={x.your_response} /></div>
            <div style={{ color:V('text-muted'), marginTop:3 }}><b>Concedes:</b> <Ev path={`${side}.rebuttal.rebuttals[${i}].concession`} text={x.concession} /></div>
          </div>
        ))}
        {r?.updated_position && <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.5 }}><b>After the debate:</b> <Ev path={`${side}.rebuttal.updated_position`} text={r.updated_position} /></div>}
      </div>
    </Panel>
  )
}

// Shown at the very top of a company page the moment it loads, so a company whose
// data cannot support a valuation is never presented like a well-covered one.
const DQ_META = {
  low:            { color: '#ef4444', icon: '⚠' },
  medium:         { color: '#f59e0b', icon: '⚠' },
  unavailable:    { color: '#94a3b8', icon: 'ℹ' },
  not_applicable: { color: '#94a3b8', icon: 'ℹ' },
}

function DataQualityBanner({ dq }) {
  if (!dq) return null
  const meta = DQ_META[dq.level] || DQ_META.unavailable
  const reasons = (dq.reasons || []).filter(Boolean)
  return (
    <div style={{ marginBottom:12, padding:'10px 14px', borderRadius:V('radius-md'),
      background:`color-mix(in srgb, ${meta.color} 9%, transparent)`, border:`1px solid color-mix(in srgb, ${meta.color} 35%, transparent)` }}>
      <div style={{ fontSize:13, fontWeight:700, color:meta.color }}>{meta.icon} {dq.headline}</div>
      {reasons.length > 0 && (
        <ul style={{ margin:'6px 0 0', paddingLeft:18, fontSize:12, color:V('text-primary'), lineHeight:1.55 }}>
          {reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {dq.level === 'low' && (
        <div style={{ marginTop:6, fontSize:11, color:V('text-muted'), lineHeight:1.5 }}>
          Price targets, entry levels and return projections are not shown for this company. The ratios, results and shareholding below are still factual.
        </div>
      )}
    </div>
  )
}

function DataFreshness({ fresh, quoteAsOf, compact }) {
  const t = quoteAsOf ? new Date(quoteAsOf).toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit' }) : null
  const parts = []
  const backup = fresh?.statements_source === 'yahoo'
  if (fresh?.financials_through) {
    parts.push(`Financials through ${fresh.financials_through}${fresh.status === 'in_sync' ? ` (BSE and ${backup ? 'Yahoo Finance' : 'Tickertape'} agree)` : ''}`)
    if (backup) parts.push('backup data source: Yahoo Finance, about 4 fiscal years, not cross-checked against filings')
  } else parts.push('Multi-year financials not available from our data providers')
  if (t) parts.push(`price as of ${t}`)
  return (
    <div style={{ marginBottom: compact ? 0 : 12 }}>
      <div style={{ fontSize:11, color:V('text-muted') }}>{parts.join(' · ')}</div>
      {(fresh?.status === 'tickertape_behind' || fresh?.status === 'bse_behind') && (
        <div style={{ marginTop:6, padding:'8px 12px', borderRadius:V('radius-md'), fontSize:12, lineHeight:1.5, color:'#f59e0b',
          background:'color-mix(in srgb, #f59e0b 9%, transparent)', border:'1px solid color-mix(in srgb, #f59e0b 30%, transparent)' }}>
          ⚠ {fresh.message}
        </div>
      )}
    </div>
  )
}

const READ_LABELS = { business_quality: 'Business quality', growth_and_earnings: 'Growth & earnings', valuation: 'Valuation',
                      sentiment_and_flows: 'Sentiment & investor flows', risks_and_unknowns: 'Risks & unknowns' }

// Shows exactly how a price in the verdict (e.g. "Rs 989") was reached, so it is a
// traceable calculation rather than an assertion. All numbers come from the
// deterministic model (investment_valuation.py), never from the AI.
function PriceReasoning({ vm }) {
  const d = vm?.price_derivation
  if (!d) return null
  const st = d.steps
  const key = d.key_level_index != null ? d.levels[d.key_level_index] : null
  const near = d.nearby_prices || []
  const refIdx = near.findIndex(n => n.is_reference)
  const lower = refIdx > 0 ? near[refIdx - 1] : null
  const upper = refIdx >= 0 ? near.slice(refIdx + 1).find(n => !n.is_today) : null
  const title = key
    ? `Why ${fmtRs(key.price)}${lower ? `, and not ${fmtRs(lower.price)}` : ''}${upper ? ` or ${fmtRs(upper.price)}` : ''}?`
    : 'How the price levels were calculated'
  const sens = d.assumption_sensitivity
  const step = { fontSize:12, color:V('text-primary'), lineHeight:1.55, marginBottom:6 }
  return (
    <details open style={{ marginTop:12, paddingTop:10, borderTop:`1px dashed ${V('border')}` }}>
      <summary style={{ cursor:'pointer', fontSize:13, fontWeight:700, color:V('text-primary') }}>{title}</summary>
      <div style={{ marginTop:8 }}>
        <div style={step}><b>1. Start with what the company earns today:</b> ₹{st.eps_ttm} profit per share over the last 12 months.</div>
        <div style={step}><b>2. Grow it for {st.horizon_years} years:</b> {st.growth_text} That takes earnings to about <b>₹{st.eps_in_5y}</b> per share.</div>
        <div style={step}><b>3. Apply the P/E the market is assumed to pay then:</b> {st.exit_pe_text} So ₹{st.eps_in_5y} × {st.exit_pe} ≈ <b>{fmtRs(st.value_in_5y)}</b> expected price in {st.horizon_years} years.</div>
        <div style={step}><b>4. Work backwards to the price to pay today</b> for each return we want. Dividends ({st.dividend_yield_pct}% a year) cover part of the target, so the share price itself only needs to grow by the rest:</div>
        <div style={{ overflowX:'auto', marginBottom:8 }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              <th style={DEBATE_TH}>Level</th><th style={DEBATE_TH}>Target / yr</th><th style={DEBATE_TH}>Price must grow / yr</th><th style={DEBATE_TH}>Calculation</th><th style={DEBATE_TH}>Price today</th>
            </tr></thead>
            <tbody>
              {d.levels.map((l, i) => (
                <tr key={i} style={i === d.key_level_index ? { background:V('bg-tertiary') } : {}}>
                  <td style={{ ...DEBATE_TD, fontWeight: i === d.key_level_index ? 700 : 400 }}>{l.name}</td>
                  <td style={DEBATE_TD}>{l.target_pct}%</td>
                  <td style={DEBATE_TD}>{l.required_price_growth_pct}%</td>
                  <td style={DEBATE_TD}>{fmtRs(l.value_in_5y)} ÷ {l.growth_factor}{l.scenario === 'bear' ? ' (bear-case value)' : ''}</td>
                  <td style={{ ...DEBATE_TD, fontWeight:700 }}>{fmtRs(l.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={step}><b>5. Why these targets:</b> {d.why_targets}</div>

        {near.length > 0 && (
          <>
            <div style={{ ...step, marginTop:10 }}>
              <b>What if you pay a different price?</b>{key ? ` ${fmtRs(key.price)} is simply the highest price that still meets the ${key.target_pct}% minimum — it is not a forecast that the stock will fall there.` : ''}
            </div>
            <div style={{ overflowX:'auto', marginBottom:8 }}>
              <table style={{ width:'100%', borderCollapse:'collapse' }}>
                <thead><tr><th style={DEBATE_TH}>If you buy at</th><th style={DEBATE_TH}>Base case earns / yr</th><th style={DEBATE_TH}>Bear case earns / yr</th><th style={DEBATE_TH}></th></tr></thead>
                <tbody>
                  {near.map((n, i) => (
                    <tr key={i} style={n.is_reference ? { background:V('bg-tertiary') } : {}}>
                      <td style={{ ...DEBATE_TD, fontWeight:700 }}>{fmtRs(n.price)}</td>
                      <td style={{ ...DEBATE_TD, color:retColor(n.base_return_pct_pa), fontWeight:600 }}>{fmtPct(n.base_return_pct_pa, true)}</td>
                      <td style={{ ...DEBATE_TD, color:retColor(n.bear_return_pct_pa) }}>{fmtPct(n.bear_return_pct_pa, true)}</td>
                      <td style={{ ...DEBATE_TD, color:V('text-muted'), fontSize:11 }}>{n.is_today ? 'today' : n.is_reference ? (key ? 'the level in the verdict' : 'defensive floor') : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5, marginBottom:6 }}>
              A lower price means a bigger cushion and a higher return if the assumptions hold — but the stock may never fall that far. A higher price means a smaller return for the same risk.
            </div>
          </>
        )}

        {sens && (sens.bear || sens.bull) && (
          <div style={step}>
            <b>How much does this depend on the assumptions?</b> The price that earns {sens.target_pct}% a year would be{' '}
            <b>{fmtRs(sens.bear)}</b> if the bear-case assumptions came true, <b>{fmtRs(sens.base)}</b> on the base case, and <b>{fmtRs(sens.bull)}</b> on the bull case.
            So the number is only as reliable as the growth and P/E assumptions in steps 2 and 3.
          </div>
        )}
      </div>
    </details>
  )
}

function DeepDebateResult({ d, m }) {
  if (!d) return null
  const vm = d.valuation_model
  if (d.ai_skipped) {
    return (
      <Panel>
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:6 }}>{d.reason}</div>
        <div style={{ fontSize:12, color:V('text-muted'), lineHeight:1.5 }}>
          The AI debate was skipped because no meaningful company analysis is possible here. Everything above is unaffected.
        </div>
      </Panel>
    )
  }
  if (!d.available) {
    return (
      <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
        <Panel>
          <div style={{ fontSize:13, color:V('text-muted') }}>
            The AI debate isn't available right now{d.reason?.includes('429') || d.reason?.toLowerCase().includes('rate limit') ? ' — the AI provider is rate-limited, try again in a minute' : ''}.
            The numeric price-vs-value model below doesn't depend on it.
          </div>
        </Panel>
        {vm?.available && (
          <>
            <Panel tint={STANCE_META[vm.baseline_stance]?.color}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Model's baseline stance (no AI)</div>
              <Chip color={STANCE_META[vm.baseline_stance]?.color || '#94a3b8'}>{STANCE_META[vm.baseline_stance]?.label}</Chip>
            </Panel>
            <ValuationModelPanel vm={vm} m={m} />
          </>
        )}
      </div>
    )
  }

  const evByPath = {}
  ;(d.evidence_check?.items || []).forEach(it => { (evByPath[it.path] = evByPath[it.path] || []).push(it) })
  Object.values(evByPath).forEach(list => list.sort((x, y) => x.start - y.start))
  const j = d.judge || {}
  const stanceMeta = STANCE_META[j.final_stance] || { label: j.final_stance, color: '#94a3b8' }
  const winner = j.who_argued_better === 'bull' ? EDGE_META.bull : j.who_argued_better === 'bear' ? EDGE_META.bear : EDGE_META.even
  return (
    <EvidenceContext.Provider value={{ byPath: evByPath }}>
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <Panel tint={stanceMeta.color}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:8, marginBottom:8 }}>
          <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:stanceMeta.color, fontWeight:700 }}>{d.low_reliability ? 'Verdict — low-reliability company' : 'Verdict — should I invest?'}</div>
          <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
            <Chip color={stanceMeta.color}>{stanceMeta.label}</Chip>
            <Chip color="#94a3b8">{j.confidence} confidence</Chip>
          </div>
        </div>
        <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), lineHeight:1.4, marginBottom:10 }}><Ev path="judge.headline" text={j.headline} /></div>
        <EvidenceBadge check={d.evidence_check} />
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:8 }}><Ev path="judge.stance_reasoning" text={j.stance_reasoning} /></div>
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55, marginBottom:8 }}><b>Today, tomorrow or wait?</b> <Ev path="judge.today_vs_wait" text={j.today_vs_wait} /></div>
        <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.55 }}><b>How to use the price levels:</b> <Ev path="judge.how_to_use_price_levels" text={j.how_to_use_price_levels} /></div>
        {!d.low_reliability && <PriceReasoning vm={vm} />}
        {j.stance_adjusted && (
          <div style={{ fontSize:11, color:V('text-muted'), marginTop:8 }}>
            The judge moved the numeric baseline (“{STANCE_META[vm?.baseline_stance]?.label}”) by one step based on evidence beyond the model.
          </div>
        )}
        {j.confidence_reason && <div style={{ fontSize:11, color:V('text-muted'), marginTop:6, fontStyle:'italic' }}><Ev path="judge.confidence_reason" text={j.confidence_reason} /></div>}
      </Panel>

      <ValuationModelPanel vm={vm} m={m} />

      <div>
        <SectionTitle sub="Both sides get the same format: four evidence-backed arguments, an admitted weak spot, and a rebuttal after reading the other side.">
          The debate
        </SectionTitle>
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr', gap:10 }}>
          <DebateSide side="bull" data={d.bull} m={m} />
          <DebateSide side="bear" data={d.bear} m={m} />
        </div>
      </div>

      <div>
        <SectionTitle>Judge's scorecard <Chip color={winner.color}>{j.who_argued_better === 'balanced' ? 'Argued about equally' : `${winner.label} argued better`}</Chip></SectionTitle>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr><th style={DEBATE_TH}>Issue</th><th style={DEBATE_TH}>Edge</th><th style={DEBATE_TH}>Why</th></tr></thead>
            <tbody>
              {(j.scorecard || []).map((s, i) => {
                const e = EDGE_META[s.edge] || EDGE_META.even
                return (
                  <tr key={i}>
                    <td style={{ ...DEBATE_TD, fontWeight:600, textTransform:'capitalize' }}>{s.issue}</td>
                    <td style={DEBATE_TD}><Chip color={e.color}>{e.label}</Chip></td>
                    <td style={{ ...DEBATE_TD, lineHeight:1.5 }}><Ev path={`judge.scorecard[${i}].why`} text={s.why} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {j.what_would_change_my_mind?.length > 0 && (
        <Panel>
          <SectionTitle>What would change this view — check back on these</SectionTitle>
          <ul style={{ margin:0, paddingLeft:18, fontSize:12, color:V('text-primary'), lineHeight:1.6 }}>
            {j.what_would_change_my_mind.map((x, i) => <li key={i}><Ev path={`judge.what_would_change_my_mind[${i}]`} text={x} /></li>)}
          </ul>
        </Panel>
      )}

      <details>
        <summary style={{ cursor:'pointer', fontSize:12, fontWeight:600, color:'#6366f1' }}>Analyst reads that fed the debate</summary>
        <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(2,1fr)', gap:10, marginTop:10 }}>
          {Object.entries(READ_LABELS).map(([k, label]) => (
            <Panel key={k}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>{label}</div>
              <div style={{ fontSize:12, color:V('text-primary'), lineHeight:1.55 }}><Ev path={`reads.${k}`} text={d.reads?.[k]} /></div>
            </Panel>
          ))}
        </div>
      </details>

      {d.data_freshness && <DataFreshness fresh={d.data_freshness} compact />}
      <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center' }}>{d.disclaimer} · AI model: {d.model_name}</div>
    </div>
    </EvidenceContext.Provider>
  )
}

// One company in the name-search dropdown / "did you mean" list. Shows enough
// detail (legal name, BSE code, ISIN, market cap, rename note) for the user to
// be sure it is the right company; the analysis only loads for the symbol clicked.
function SuggestionRow({ s, active, onPick, onHover }) {
  return (
    <div onMouseDown={e => { e.preventDefault(); onPick(s) }} onMouseEnter={onHover}
      style={{ padding:'9px 12px', cursor:'pointer', background: active ? V('bg-tertiary') : 'transparent', borderBottom:`1px solid ${V('border-light')}` }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', gap:10 }}>
        <span style={{ fontSize:13, fontWeight:600, color:V('text-primary') }}>{s.name}</span>
        <span style={{ fontSize:11, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:'#6366f1', whiteSpace:'nowrap' }}>{s.symbol}</span>
      </div>
      <div style={{ fontSize:11, color:V('text-muted'), marginTop:2, lineHeight:1.4 }}>
        {s.legal_name ? `${s.legal_name} · ` : ''}BSE {s.bse_code} · ISIN {s.isin || '—'}{s.mktcap_cr ? ` · ₹${s.mktcap_cr.toLocaleString('en-IN')} cr market cap` : ''}
      </div>
      {s.note && <div style={{ fontSize:11, color:'#f59e0b', marginTop:2 }}>{s.note}</div>}
    </div>
  )
}

function CompanyAnalysisTab() {
  const [query, setQuery] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [debateData, setDebateData] = useState(null)
  const [debateLoading, setDebateLoading] = useState(false)
  const [showGuide, setShowGuide] = useState(true)
  const [suggestions, setSuggestions] = useState([])
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [didYouMean, setDidYouMean] = useState(null)   // null = not applicable, [] = nothing matched
  const suggestSeq = useRef(0)
  const m = window.innerWidth < 768

  // Live name/symbol suggestions as the user types (debounced; stale responses ignored).
  useEffect(() => {
    const q = query.trim()
    const seq = ++suggestSeq.current
    if (q.length < 2) { setSuggestions([]); return }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`/api/investment/search?q=${encodeURIComponent(q)}&limit=8`)
        const j = await r.json()
        if (seq === suggestSeq.current) { setSuggestions(j.results || []); setActiveIdx(-1) }
      } catch { if (seq === suggestSeq.current) setSuggestions([]) }
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  const pick = (sug) => { setQuery(sug.symbol); setSuggestOpen(false); setDidYouMean(null); runSearch(sug.symbol) }

  const runSearch = async (sym) => {
    const s = (sym || '').trim()
    if (!s) return
    setLoading(true); setError(null); setDebateData(null); setDidYouMean(null); setSuggestOpen(false)
    try {
      const r = await fetch(`/api/investment/equity/${encodeURIComponent(s.toUpperCase())}/full`)
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        if (r.status === 404) {
          // Not a BSE symbol: offer the closest companies by name instead of a dead end.
          try {
            const sr = await fetch(`/api/investment/search?q=${encodeURIComponent(s)}&limit=6`)
            setDidYouMean((await sr.json()).results || [])
          } catch { setDidYouMean([]) }
        }
        throw new Error(body.detail || `Request failed (${r.status})`)
      }
      setData(await r.json())
    } catch (e) {
      setError(e.message || 'Failed to load')
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  const onSearchKey = (e) => {
    if (e.key === 'ArrowDown' && suggestions.length) { e.preventDefault(); setSuggestOpen(true); setActiveIdx(i => (i + 1) % suggestions.length) }
    else if (e.key === 'ArrowUp' && suggestions.length) { e.preventDefault(); setActiveIdx(i => (i <= 0 ? suggestions.length - 1 : i - 1)) }
    else if (e.key === 'Escape') setSuggestOpen(false)
    else if (e.key === 'Enter') {
      if (suggestOpen && activeIdx >= 0 && suggestions[activeIdx]) pick(suggestions[activeIdx])
      else runSearch(query)
    }
  }

  const runDebate = async () => {
    if (!data?.overview?.symbol) return
    setDebateLoading(true)
    try {
      const r = await fetch(`/api/investment/equity/${encodeURIComponent(data.overview.symbol)}/debate`)
      setDebateData(await r.json())
    } catch (e) {
      setDebateData({ available: false, reason: e.message || 'Request failed' })
    } finally {
      setDebateLoading(false)
    }
  }

  const thStyle = { textAlign:'left', padding:'8px 10px', color:V('text-muted'), fontSize:11, textTransform:'uppercase', letterSpacing:'0.05em', borderBottom:`1px solid ${V('border')}` }
  const tdStyle = { padding:'8px 10px', borderBottom:`1px solid ${V('border-light')}`, fontSize:13 }

  const ov = data?.overview
  const fin = data?.financials
  const sh = data?.shareholding
  const peers = data?.peers?.peers || []
  const piotroski = fin?.piotroski_f_score
  const altman = fin?.altman_z_score
  const zoneColor = altman?.zone === 'safe' ? '#22c55e' : altman?.zone === 'distress' ? '#ef4444' : '#eab308'
  const chg = ov ? (ov.quote?.ltp - ov.quote?.prev_close) : null

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <Card>
        <div style={{ display:'flex', gap:10, flexWrap:'wrap' }}>
          <div style={{ flex:1, minWidth:220, position:'relative' }}>
            <input
              type="text" placeholder="Search by company name or BSE symbol (e.g. Reliance, TCS, Larsen & Toubro)..." value={query}
              onChange={e => { setQuery(e.target.value); setSuggestOpen(true) }}
              onFocus={() => setSuggestOpen(true)}
              onBlur={() => setSuggestOpen(false)}
              onKeyDown={onSearchKey}
              style={{
                width:'100%', boxSizing:'border-box', padding:'10px 14px', borderRadius:8, fontSize:14,
                background:V('bg-tertiary'), border:`1px solid ${V('border')}`, color:V('text-primary'),
              }}
            />
            {suggestOpen && suggestions.length > 0 && (
              <div style={{ position:'absolute', top:'calc(100% + 4px)', left:0, right:0, zIndex:30, maxHeight:380, overflowY:'auto',
                background:V('bg-secondary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-md'), boxShadow:V('shadow-md') }}>
                {suggestions.map((sug, i) => (
                  <SuggestionRow key={sug.symbol} s={sug} active={i === activeIdx} onPick={pick} onHover={() => setActiveIdx(i)} />
                ))}
              </div>
            )}
          </div>
          <StyledButton onClick={() => runSearch(query)} disabled={loading || !query.trim()}>
            {loading ? 'Loading…' : 'Analyze'}
          </StyledButton>
        </div>
        {error && <div style={{ marginTop:10, color:'#ef4444', fontSize:13 }}>{error}</div>}
        {didYouMean && (
          <div style={{ marginTop:10 }}>
            {didYouMean.length > 0 ? (
              <>
                <div style={{ fontSize:12, fontWeight:600, color:V('text-primary'), marginBottom:6 }}>Did you mean one of these? Click the company you want:</div>
                <div style={{ border:`1px solid ${V('border')}`, borderRadius:V('radius-md'), overflow:'hidden' }}>
                  {didYouMean.map(sug => <SuggestionRow key={sug.symbol} s={sug} onPick={pick} onHover={() => {}} />)}
                </div>
              </>
            ) : (
              <div style={{ fontSize:12, color:V('text-muted') }}>No listed company matches that. Try the company name or its BSE symbol.</div>
            )}
          </div>
        )}
      </Card>

      {!data && !loading && !error && (
        <div style={{ fontSize:13, color:V('text-muted'), textAlign:'center', padding:'24px 0' }}>
          Search any BSE-listed company symbol to see ratios, fundamentals, quality scores, shareholding, and peer comparison.
        </div>
      )}

      {ov && (
        <>
          <Card>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', flexWrap:'wrap', gap:10, marginBottom:14 }}>
              <div>
                <div style={{ fontSize:18, fontWeight:700, color:V('text-primary') }}>
                  {ov.name} <span style={{ color:V('text-muted'), fontWeight:500, fontSize:13 }}>({ov.symbol})</span>
                </div>
                <div style={{ fontSize:12, color:V('text-muted'), marginTop:2 }}>
                  {ov.classification?.sector} · {ov.classification?.industry} · {ov.classification?.group}
                </div>
                <div style={{ fontSize:11, color:V('text-muted'), marginTop:2 }}>ISIN {ov.isin}</div>
              </div>
              <div style={{ textAlign:'right' }}>
                <div style={{ fontSize:22, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>
                  ₹{ov.quote?.ltp?.toFixed(2)}
                </div>
                <div style={{ fontSize:12, color: chg >= 0 ? '#22c55e' : '#ef4444' }}>
                  {chg >= 0 ? '+' : ''}{chg?.toFixed(2)} ({((chg / ov.quote?.prev_close) * 100)?.toFixed(2)}%)
                </div>
              </div>
            </div>
            <DataQualityBanner dq={data?.data_quality} />
            <DataFreshness fresh={fin?.data_freshness} quoteAsOf={ov.quote_as_of} />
            <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(6,1fr)', gap:10 }}>
              <MetricBox label="P/E" value={ov.ratios?.pe ?? '—'} sub="Years of profit you're paying for" />
              <MetricBox label="P/B" value={ov.ratios?.pb ?? '—'} sub="Price vs. net worth per share" />
              <MetricBox label="ROE %" value={ov.ratios?.roe ?? '—'} sub="Profit per ₹ of shareholder equity" />
              <MetricBox label="EPS" value={ov.ratios?.eps ?? '—'} sub="Profit earned per share" />
              <MetricBox label="OPM %" value={ov.ratios?.opm ?? '—'} sub="Operating profit as % of revenue" />
              <MetricBox label="NPM %" value={ov.ratios?.npm ?? '—'} sub="Net profit as % of revenue" />
            </div>

            <div style={{ marginTop:14 }}>
              <button onClick={() => setShowGuide(v => !v)}
                style={{ background:'none', border:'none', padding:0, cursor:'pointer', fontSize:12, fontWeight:600, color:'#6366f1' }}>
                {showGuide ? '▾ Hide' : '▸ Show'} how to read these numbers
              </button>
              {showGuide && (
                <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(2,1fr)', gap:10, marginTop:10 }}>
                  {ratioGuide(ov.ratios, peers, ov.symbol).map(g => (
                    <div key={g.label} style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderLeft:`3px solid ${g.tone}`, borderRadius:V('radius-md'), padding:12 }}>
                      <div style={{ fontSize:12, fontWeight:700, color:V('text-primary'), marginBottom:4 }}>{g.label}</div>
                      <div style={{ fontSize:12, color:g.tone, fontWeight:600, marginBottom:6, lineHeight:1.45 }}>For this company: {g.reading}</div>
                      <div style={{ fontSize:11, color:V('text-primary'), lineHeight:1.5, marginBottom:4 }}>{g.what}</div>
                      <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5, marginBottom:4 }}><b>Acceptable range:</b> {g.range}</div>
                      <div style={{ fontSize:11, color:V('text-muted'), lineHeight:1.5 }}><b>Lower vs higher:</b> {g.lowHigh}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:14 }}>Quality & Risk Scores</div>
            <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr', gap:14 }}>
              <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:16 }}>
                <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.08em', color:V('text-muted'), marginBottom:8 }}>Piotroski F-Score</div>
                {piotroski?.complete ? (
                  <>
                    <div style={{ fontSize:28, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>
                      {piotroski.score}<span style={{ fontSize:16, color:V('text-muted') }}> / 9</span>
                    </div>
                    <div style={{ fontSize:11, color:V('text-muted'), marginTop:4 }}>{piotroski.compared_years?.join(' → ')}</div>
                    <div style={{ fontSize:12, color:V('text-muted'), marginTop:8, lineHeight:1.5 }}>
                      {piotroski.score >= 8 ? 'Very strong financial health across profitability, leverage, and efficiency.' :
                       piotroski.score >= 6 ? 'Reasonably healthy fundamentals, with some weaker checks.' :
                       piotroski.score >= 3 ? 'Mixed signals across the 9 profitability/leverage/efficiency checks.' :
                       'Weak on most of the 9 checks — worth a closer look at the financials.'}
                    </div>
                    {piotroski.score < 9 && piotroskiFailedChecks(piotroski.checks).length > 0 && (
                      <div style={{ fontSize:11, color:V('text-muted'), marginTop:6, lineHeight:1.5 }}>
                        Weaker on: {piotroskiFailedChecks(piotroski.checks).join(', ')}.
                      </div>
                    )}
                  </>
                ) : <div style={{ fontSize:13, color:V('text-muted') }}>Not enough history{piotroski?.reason ? ` (${piotroski.reason})` : ''}</div>}
              </div>
              <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:16 }}>
                <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.08em', color:V('text-muted'), marginBottom:8 }}>Altman Z-Score</div>
                {altman?.z_score != null ? (
                  <>
                    <div style={{ display:'flex', alignItems:'baseline', gap:10 }}>
                      <div style={{ fontSize:28, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{altman.z_score.toFixed(2)}</div>
                      <span style={{
                        padding:'3px 10px', borderRadius:20, fontSize:11, fontWeight:600,
                        background:`color-mix(in srgb, ${zoneColor} 18%, transparent)`, color:zoneColor,
                        border:`1px solid color-mix(in srgb, ${zoneColor} 35%, transparent)`,
                      }}>{altman.zone}</span>
                    </div>
                    <div style={{ fontSize:11, color:V('text-muted'), marginTop:4 }}>{altman.fiscal_year}</div>
                    <div style={{ fontSize:12, color:V('text-muted'), marginTop:8, lineHeight:1.5 }}>
                      {altman.zone === 'safe' ? 'Estimates bankruptcy/distress risk — "safe" means low near-term financial-distress risk by this model.' :
                       altman.zone === 'grey' ? 'Estimates bankruptcy/distress risk — "grey" means some caution warranted, not a clear pass or fail.' :
                       'Estimates bankruptcy/distress risk — "distress" is the zone historically associated with companies that later ran into financial trouble.'}
                    </div>
                    {/financ|bank|insurance|nbfc/i.test(`${ov.classification?.sector} ${ov.classification?.industry}`) && (
                      <div style={{ fontSize:11, color:'#f59e0b', marginTop:6, lineHeight:1.5 }}>
                        Note: this score was designed for manufacturers. Banks and financial companies are leveraged by nature, so it often reads "distress" for healthy lenders — treat it with caution here.
                      </div>
                    )}
                    {altman.zone !== 'safe' && altmanWeakPoints(altman.components).length > 0 && (
                      <div style={{ fontSize:11, color:V('text-muted'), marginTop:6, lineHeight:1.5 }}>
                        Driven mainly by: {altmanWeakPoints(altman.components).join(', ')}.
                      </div>
                    )}
                  </>
                ) : <div style={{ fontSize:13, color:V('text-muted') }}>{altman?.reason || 'Not available'}</div>}
              </div>
            </div>
          </Card>

          {fin?.recent_results_bse && (
            <Card>
              <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:14 }}>
                Recent Results ({fin.recent_results_bse.currency_unit})
              </div>
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead><tr>
                    <th style={thStyle}>Metric</th>
                    {fin.recent_results_bse.periods?.map(p => <th key={p} style={thStyle}>{p}</th>)}
                  </tr></thead>
                  <tbody>
                    {fin.recent_results_bse.results_cr?.map((row, i) => (
                      <tr key={i}>
                        <td style={{...tdStyle, fontWeight:600}}>{row.title}</td>
                        {row.values.map((v, j) => <td key={j} style={tdStyle}>{v}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {sh?.bse_latest?.holding_pct_by_category && (
            <Card>
              <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:4 }}>Shareholding Pattern</div>
              <div style={{ fontSize:12, color:V('text-muted'), marginBottom:14 }}>{sh.bse_latest.latest_quarter}</div>
              <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
                <MetricBox label="Promoter" value={`${(sh.bse_latest.holding_pct_by_category.ShareholdingOfPromoterAndPromoterGroup ?? 0).toFixed(2)}%`} color="#6366f1" />
                <MetricBox label="Public" value={`${(sh.bse_latest.holding_pct_by_category.PublicShareholding ?? 0).toFixed(2)}%`} />
              </div>
            </Card>
          )}

          {peers.length > 0 && (
            <Card>
              <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:14 }}>Peer Comparison</div>
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead><tr>
                    {['Name','LTP','Revenue','PAT','OPM %','NPM %','RONW %','EPS','P/E'].map(h => <th key={h} style={thStyle}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {peers.map((p, i) => (
                      <tr key={i} style={p.Name === ov.symbol ? { background: V('bg-tertiary') } : {}}>
                        <td style={{...tdStyle, fontWeight: p.Name === ov.symbol ? 700 : 400}}>{p.Name}</td>
                        <td style={tdStyle}>₹{p.LTP}</td>
                        <td style={tdStyle}>{p.Revenue}</td>
                        <td style={tdStyle}>{p.PAT}</td>
                        <td style={tdStyle}>{p.OPM}</td>
                        <td style={tdStyle}>{p.NPM}</td>
                        <td style={tdStyle}>{p.RONW}</td>
                        <td style={tdStyle}>{p.EPS}</td>
                        <td style={tdStyle}>{p.PE}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <Card>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:10, marginBottom: debateData ? 14 : 0 }}>
              <div>
                <div style={{ fontSize:15, fontWeight:700, color:V('text-primary') }}>Should I invest? — AI Investment Debate</div>
                <div style={{ fontSize:12, color:V('text-muted'), maxWidth:640, lineHeight:1.5 }}>
                  Answers three questions: is today's price good, should I wait, and at what price should I buy. A 5-year return model, the stock's own P/E history, shareholding trends and recent announcements feed a bull-vs-bear debate with rebuttals, judged by a senior AI model. On-demand — takes 30 to 60 seconds.
                </div>
              </div>
              <StyledButton onClick={runDebate} disabled={debateLoading}>
                {debateLoading ? 'Debating… (30-60s)' : (debateData ? 'Re-run' : 'Get AI Analysis')}
              </StyledButton>
            </div>
            {debateLoading && (
              <div style={{ fontSize:12, color:V('text-muted'), marginTop:12 }}>
                Running four rounds: analyst reads → bull and bear openings → rebuttals → judge's verdict…
              </div>
            )}

            <DeepDebateResult d={debateData} m={m} />
          </Card>

          <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center', padding:'4px 0' }}>
            Data from BSE and Tickertape.
          </div>
        </>
      )}
    </div>
  )
}

function IpoReviewTab() {
  const [ipos, setIpos] = useState(null)
  const [listError, setListError] = useState(null)
  const [selected, setSelected] = useState(null) // ipo_no of the one being analyzed
  const m = window.innerWidth < 768

  useEffect(() => {
    fetch('/api/investment/ipo/list')
      .then(r => r.json())
      .then(d => setIpos(d.ipos || []))
      .catch(e => setListError(e.message || 'Failed to load IPO list'))
  }, [])

  if (selected) {
    return <IpoAnalysisView ipoNo={selected} onBack={() => setSelected(null)} />
  }

  const equityOffers = ipos?.filter(i => i.is_equity_offer) || []
  const otherActions = ipos?.filter(i => !i.is_equity_offer) || []

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ fontSize:12, color:V('text-muted') }}>
        Everything currently open for bidding on BSE. Only IPOs/FPOs get RHP-derived fundamentals, quality scores, and an AI verdict — other corporate actions below use a different disclosure document and don't have that analysis available.
      </div>
      {listError && <Card><div style={{ color:'#ef4444', fontSize:13 }}>{listError}</div></Card>}
      {!ipos && !listError && <div style={{ fontSize:13, color:V('text-muted'), textAlign:'center', padding:'24px 0' }}>Loading IPOs…</div>}
      {ipos?.length === 0 && <Card><div style={{ fontSize:13, color:V('text-muted') }}>No open issues found.</div></Card>}

      {equityOffers.length > 0 && (
        <>
          <div style={{ fontSize:14, fontWeight:700, color:V('text-primary'), marginTop:4 }}>IPOs &amp; FPOs</div>
          <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(2,1fr)', gap:10 }}>
            {equityOffers.map(ipo => <IpoListCard key={ipo.ipo_no} ipo={ipo} onClick={() => setSelected(ipo.ipo_no)} />)}
          </div>
        </>
      )}

      {otherActions.length > 0 && (
        <>
          <div style={{ fontSize:14, fontWeight:700, color:V('text-primary'), marginTop:10 }}>Other Corporate Actions</div>
          <div style={{ fontSize:11, color:V('text-muted'), marginBottom:2 }}>
            Rights Issues, Buybacks, Offers to Buy, and Debt Issues — not fresh equity offerings, so no RHP/fundamentals analysis applies here.
          </div>
          <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(2,1fr)', gap:10 }}>
            {otherActions.map(ipo => <IpoListCard key={ipo.ipo_no} ipo={ipo} onClick={() => setSelected(ipo.ipo_no)} dimmed />)}
          </div>
        </>
      )}

      <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center' }}>
        GMP/subscription sentiment from investorgain.com — unofficial, community-sourced, informational only.
      </div>
    </div>
  )
}

function IpoListCard({ ipo, onClick, dimmed }) {
  const gmpColor = (v) => v == null ? V('text-muted') : v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : V('text-muted')
  return (
    <div onClick={onClick}
      style={{
        cursor:'pointer', background:V('bg-secondary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-md'),
        padding:14, display:'flex', flexDirection:'column', gap:8, opacity: dimmed ? 0.85 : 1,
      }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8 }}>
        <div style={{ fontSize:14, fontWeight:700, color:V('text-primary') }}>{ipo.name}</div>
        <span style={{ padding:'2px 8px', borderRadius:12, fontSize:10, fontWeight:600, background:V('bg-tertiary'), color:V('text-muted'), border:`1px solid ${V('border')}`, whiteSpace:'nowrap' }}>
          {ipo.platform}
        </span>
      </div>
      <div style={{ fontSize:12, color:V('text-muted') }}>
        <span style={{ fontWeight:600, color: ipo.is_equity_offer ? '#6366f1' : V('text-muted') }}>{ipo.type_full}</span>
        {' · '}₹{ipo.price_band} · {ipo.start_date?.slice(0,10)} → {ipo.end_date?.slice(0,10)}
      </div>
      <div style={{ display:'flex', gap:14, fontSize:12 }}>
        <div>
          <span style={{ color:V('text-muted') }}>GMP: </span>
          <span style={{ color: gmpColor(ipo.sentiment?.gmp?.gmp_value), fontWeight:600 }}>
            {ipo.sentiment?.gmp?.gmp_value != null ? `₹${ipo.sentiment.gmp.gmp_value} (${ipo.sentiment.gmp.gmp_percent}%)` : '—'}
          </span>
        </div>
        <div>
          <span style={{ color:V('text-muted') }}>Sub: </span>
          <span style={{ color:V('text-primary'), fontWeight:600 }}>{ipo.sentiment?.subscription?.total_x != null ? `${ipo.sentiment.subscription.total_x}x` : '—'}</span>
        </div>
      </div>
    </div>
  )
}

function IpoAnalysisView({ ipoNo, onBack }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const m = window.innerWidth < 768

  useEffect(() => {
    setData(null); setError(null)
    fetch(`/api/investment/ipo/${ipoNo}/analysis`)
      .then(async r => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail || `Request failed (${r.status})`)
        }
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message || 'Failed to load'))
  }, [ipoNo])

  const mech = data?.mechanics
  const sent = data?.sentiment
  const rhp = data?.rhp
  const piotroski = data?.piotroski_f_score
  const altman = data?.altman_zprime_score
  const trend = data?.yoy_trend
  const investorInterest = data?.investor_interest
  const verdict = data?.verdict
  const zoneColor = altman?.zone === 'safe' ? '#22c55e' : altman?.zone === 'distress' ? '#ef4444' : '#eab308'
  const callColor = (v) => v === 'apply' || v === 'invest' ? '#22c55e' : v === 'avoid' ? '#ef4444' : '#eab308'
  const trendColor = (d) => d === 'up' ? '#22c55e' : d === 'down' ? '#ef4444' : V('text-muted')
  const trendArrow = (d) => d === 'up' ? '↑' : d === 'down' ? '↓' : '→'
  const priceColor = (p) => p === 'attractive' ? '#22c55e' : p === 'aggressive' ? '#ef4444' : '#eab308'
  const interestColor = (i) => i === 'high' ? '#22c55e' : i === 'low' ? '#ef4444' : '#eab308'
  // BSE's price band is free text ("1700.00-1785.00|/A discount of Rs 170/- ... Employee Reservation portion|"):
  // show the band itself and keep the rest as a footnote. The backend tidies it too; this covers an older backend.
  const bandParts = String(mech?.price_band ?? '').split('|').map(x => x.trim().replace(/^\/+|\/+$/g, '')).filter(Boolean)
  const bandNums = (bandParts[0] || '').match(/\d[\d,]*\.?\d*/g) || []
  const inr = (x) => `₹${Number(x.replace(/,/g, '')).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const bandDisplay = mech?.price_band_display ?? (bandNums.length >= 2 ? `${inr(bandNums[0])} – ${inr(bandNums[bandNums.length - 1])}` : (bandParts[0] ?? '—'))
  const bandNote = mech?.price_band_note ?? (bandParts.slice(1).join(' ') || null)

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div onClick={onBack} style={{ cursor:'pointer', fontSize:13, color:V('text-muted'), fontWeight:600 }}>← Back to IPO list</div>

      {error && <Card><div style={{ color:'#ef4444', fontSize:13 }}>{error}</div></Card>}
      {!data && !error && (
        <div style={{ fontSize:13, color:V('text-muted'), textAlign:'center', padding:'24px 0' }}>
          Downloading and analyzing the RHP — can take up to a minute for an IPO not seen before…
        </div>
      )}

      {mech && (
        <Card>
          <div style={{ fontSize:18, fontWeight:700, color:V('text-primary'), marginBottom:10 }}>{mech.scrip_name}</div>
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
            <MetricBox label="Price Band" value={bandDisplay} />
            <MetricBox label="Lot Size" value={mech.market_lot ?? '—'} />
            <MetricBox label="Issue Size" value={mech.issue_size_shares ? Number(mech.issue_size_shares).toLocaleString() : '—'} />
            <MetricBox label="Period" value={mech.issue_period ?? '—'} />
          </div>
          {bandNote && <div style={{ marginTop:8, fontSize:11, color:V('text-muted'), lineHeight:1.5 }}>Note on the price band: {bandNote}</div>}
        </Card>
      )}

      {mech && rhp?.errors?.length > 0 && !rhp?.summary_financials && (
        <Card style={{ border:`1px solid color-mix(in srgb, #eab308 35%, ${V('border')})`, background:'color-mix(in srgb, #eab308 6%, transparent)' }}>
          <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>
            <strong>No fundamentals analysis available for this issue.</strong> {rhp.errors[0]}.
          </div>
        </Card>
      )}

      {verdict?.available && (
        <Card style={{ border:`1px solid color-mix(in srgb, ${callColor(verdict.listing_gain_view)} 35%, ${V('border')})` }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', flexWrap:'wrap', gap:10, marginBottom:12 }}>
            <div style={{ fontSize:15, fontWeight:700, color:V('text-primary') }}>IPO Verdict</div>
            {verdict.star_rating != null && (
              <div style={{ fontSize:16, letterSpacing:2 }}>
                {'★'.repeat(verdict.star_rating)}<span style={{ color:V('border') }}>{'★'.repeat(Math.max(0, 5 - verdict.star_rating))}</span>
              </div>
            )}
          </div>
          {verdict.business_summary && (
            <div style={{ fontSize:13, color:V('text-muted'), lineHeight:1.5, marginBottom:14 }}>{verdict.business_summary}</div>
          )}
          <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr', gap:10, marginBottom:14 }}>
            <div style={{
              padding:14, borderRadius:V('radius-md'),
              background:`color-mix(in srgb, ${callColor(verdict.listing_gain_view)} 10%, transparent)`,
              border:`1px solid color-mix(in srgb, ${callColor(verdict.listing_gain_view)} 30%, transparent)`,
            }}>
              <div style={{ fontSize:10, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Listing Gain Call</div>
              <div style={{ display:'flex', alignItems:'baseline', gap:8, flexWrap:'wrap' }}>
                <div style={{ fontSize:18, fontWeight:800, color:callColor(verdict.listing_gain_view), textTransform:'uppercase' }}>{verdict.listing_gain_view}</div>
                {verdict.listing_gain_view === 'apply' && sent?.gmp?.gmp_percent != null && (
                  <div style={{ fontSize:13, fontWeight:700, color:callColor(verdict.listing_gain_view) }}>
                    ~{sent.gmp.gmp_percent}%+ expected
                  </div>
                )}
              </div>
              {verdict.listing_gain_reason && <div style={{ fontSize:12, color:V('text-primary'), marginTop:6, lineHeight:1.5 }}>{verdict.listing_gain_reason}</div>}
            </div>
            <div style={{
              padding:14, borderRadius:V('radius-md'),
              background:`color-mix(in srgb, ${callColor(verdict.long_term_view)} 10%, transparent)`,
              border:`1px solid color-mix(in srgb, ${callColor(verdict.long_term_view)} 30%, transparent)`,
            }}>
              <div style={{ fontSize:10, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Long-Term Call</div>
              <div style={{ fontSize:18, fontWeight:800, color:callColor(verdict.long_term_view), textTransform:'uppercase' }}>{verdict.long_term_view}</div>
              {verdict.long_term_reason && <div style={{ fontSize:12, color:V('text-primary'), marginTop:6, lineHeight:1.5 }}>{verdict.long_term_reason}</div>}
            </div>
          </div>
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:'10px 12px' }}>
              <div style={{ fontSize:10, textTransform:'uppercase', color:V('text-muted'), marginBottom:4 }}>Revenue YoY</div>
              <div style={{ fontSize:14, fontWeight:700, color: trendColor(trend?.revenue_direction) }}>
                {trend?.revenue_yoy_pct != null ? `${trendArrow(trend.revenue_direction)} ${Math.abs(trend.revenue_yoy_pct)}%` : '—'}
              </div>
            </div>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:'10px 12px' }}>
              <div style={{ fontSize:10, textTransform:'uppercase', color:V('text-muted'), marginBottom:4 }}>PAT YoY</div>
              <div style={{ fontSize:14, fontWeight:700, color: trendColor(trend?.net_profit_direction) }}>
                {trend?.net_profit_yoy_pct != null ? `${trendArrow(trend.net_profit_direction)} ${Math.abs(trend.net_profit_yoy_pct)}%` : '—'}
              </div>
            </div>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:'10px 12px' }}>
              <div style={{ fontSize:10, textTransform:'uppercase', color:V('text-muted'), marginBottom:4 }}>Issue Price</div>
              <div style={{ fontSize:14, fontWeight:700, color: priceColor(verdict.issue_price_assessment), textTransform:'capitalize' }}>
                {verdict.issue_price_assessment || '—'}
              </div>
            </div>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:'10px 12px' }}>
              <div style={{ fontSize:10, textTransform:'uppercase', color:V('text-muted'), marginBottom:4 }}>Investor Interest</div>
              <div style={{ fontSize:14, fontWeight:700, color: interestColor(investorInterest), textTransform:'capitalize' }}>
                {investorInterest || '—'}
              </div>
            </div>
          </div>
        </Card>
      )}

      {verdict && !verdict.available && rhp?.summary_financials && (
        <Card>
          <div style={{ fontSize:13, color:V('text-muted'), background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:14 }}>
            AI analysis isn't available right now{verdict.reason?.toLowerCase().includes('rate limit') ? ' — the AI provider is rate-limited, try again in a minute' : ''}. The numbers above are unaffected either way.
          </div>
        </Card>
      )}

      {sent && (sent.gmp || sent.subscription) && (
        <Card>
          <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:4 }}>Market Sentiment</div>
          <div style={{ fontSize:11, color:V('text-muted'), marginBottom:14 }}>{sent.source}</div>
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(3,1fr)', gap:10, marginBottom: sent.subscription ? 14 : 0 }}>
            <MetricBox label="GMP" value={sent.gmp?.gmp_value != null ? `₹${sent.gmp.gmp_value}` : '—'} color={sent.gmp?.gmp_value > 0 ? '#22c55e' : sent.gmp?.gmp_value < 0 ? '#ef4444' : undefined} />
            <MetricBox label="GMP %" value={sent.gmp?.gmp_percent != null ? `${sent.gmp.gmp_percent}%` : '—'} />
            <MetricBox label="Total Sub" value={sent.subscription?.total_x != null ? `${sent.subscription.total_x}x` : '—'} />
          </div>
          {sent.subscription && (
            <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
              <MetricBox label="QIB" value={sent.subscription.qib_x != null ? `${sent.subscription.qib_x}x` : '—'} />
              <MetricBox label="NII" value={sent.subscription.nii_x != null ? `${sent.subscription.nii_x}x` : '—'} />
              <MetricBox label="RII" value={sent.subscription.rii_x != null ? `${sent.subscription.rii_x}x` : '—'} />
              <MetricBox label="BHNI" value={sent.subscription.bhni_x != null ? `${sent.subscription.bhni_x}x` : '—'} />
            </div>
          )}
        </Card>
      )}

      {rhp?.summary_financials && (
        <Card>
          <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:14 }}>Quality Scores (from RHP restated financials)</div>
          <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr', gap:14 }}>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:16 }}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.08em', color:V('text-muted'), marginBottom:8 }}>Piotroski F-Score</div>
              {piotroski?.complete ? (
                <>
                  <div style={{ fontSize:28, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>
                    {piotroski.score}<span style={{ fontSize:16, color:V('text-muted') }}> / 9</span>
                  </div>
                  <div style={{ fontSize:12, color:V('text-muted'), marginTop:8, lineHeight:1.5 }}>
                    {piotroski.score >= 8 ? 'Very strong financial health across profitability, leverage, and efficiency.' :
                     piotroski.score >= 6 ? 'Reasonably healthy fundamentals, with some weaker checks.' :
                     piotroski.score >= 3 ? 'Mixed signals — several of the 9 profitability/leverage/efficiency checks fail. Common for young, fast-scaling companies, not necessarily a red flag on its own.' :
                     'Weak on most of the 9 checks — worth reading the fundamentals section closely.'}
                  </div>
                  {piotroski.score < 9 && piotroskiFailedChecks(piotroski.checks).length > 0 && (
                    <div style={{ fontSize:11, color:V('text-muted'), marginTop:6, lineHeight:1.5 }}>
                      Weaker on: {piotroskiFailedChecks(piotroski.checks).join(', ')} — from the RHP's restated financials.
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize:13, color:V('text-muted') }}>{piotroski?.reason || 'Not available'}</div>}
            </div>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:16 }}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.08em', color:V('text-muted'), marginBottom:8 }}>Altman Z'-Score (private-company variant)</div>
              {altman?.z_prime_score != null ? (
                <>
                  <div style={{ display:'flex', alignItems:'baseline', gap:10 }}>
                    <div style={{ fontSize:28, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{altman.z_prime_score.toFixed(2)}</div>
                    <span style={{ padding:'3px 10px', borderRadius:20, fontSize:11, fontWeight:600, background:`color-mix(in srgb, ${zoneColor} 18%, transparent)`, color:zoneColor, border:`1px solid color-mix(in srgb, ${zoneColor} 35%, transparent)` }}>{altman.zone}</span>
                  </div>
                  <div style={{ fontSize:12, color:V('text-muted'), marginTop:8, lineHeight:1.5 }}>
                    {altman.zone === 'safe' ? 'Estimates bankruptcy/distress risk — "safe" means low near-term financial-distress risk by this model.' :
                     altman.zone === 'grey' ? 'Estimates bankruptcy/distress risk — "grey" means some caution warranted, not a clear pass or fail.' :
                     'Estimates bankruptcy/distress risk — "distress" is the zone historically associated with companies that later ran into financial trouble; worth weighing carefully.'}
                  </div>
                  {altman.zone !== 'safe' && altmanWeakPoints(altman.components).length > 0 && (
                    <div style={{ fontSize:11, color:V('text-muted'), marginTop:6, lineHeight:1.5 }}>
                      Driven mainly by: {altmanWeakPoints(altman.components).join(', ')} — from the RHP's restated financials.
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize:13, color:V('text-muted') }}>{altman?.reason || 'Not available'}</div>}
            </div>
          </div>
        </Card>
      )}

      {rhp?.objects_of_offer?.length > 0 && (
        <Card>
          <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:10 }}>Objects of the Offer</div>
          <ul style={{ margin:0, paddingLeft:18, display:'flex', flexDirection:'column', gap:6 }}>
            {rhp.objects_of_offer.map((o, i) => <li key={i} style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{o}</li>)}
          </ul>
        </Card>
      )}

      {rhp?.risk_factors?.length > 0 && (
        <Card>
          <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), marginBottom:4 }}>Top Risk Factors (from RHP)</div>
          <div style={{ fontSize:11, color:V('text-muted'), marginBottom:10 }}>As disclosed by the company, most material first per SEBI ordering</div>
          <ul style={{ margin:0, paddingLeft:18, display:'flex', flexDirection:'column', gap:8 }}>
            {rhp.risk_factors.slice(0, 12).map((r) => (
              <li key={r.number} style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{r.heading}</li>
            ))}
          </ul>
        </Card>
      )}

      {verdict?.available && (
        <Card>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:10, marginBottom:14 }}>
            <div style={{ fontSize:15, fontWeight:700, color:V('text-primary') }}>Detailed AI Analysis</div>
            <div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <span style={{ padding:'3px 10px', borderRadius:20, fontSize:11, fontWeight:600, background:V('bg-tertiary'), color:V('text-muted'), border:`1px solid ${V('border')}` }}>
                {verdict.confidence} confidence
              </span>
              {verdict.confidence_score != null && (
                <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                  <div style={{ width:60, height:6, borderRadius:3, background:V('bg-tertiary'), overflow:'hidden' }}>
                    <div style={{ width:`${verdict.confidence_score}%`, height:'100%', background:'#6366f1' }} />
                  </div>
                  <span style={{ fontSize:12, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>
                    {verdict.confidence_score}%
                  </span>
                </div>
              )}
            </div>
          </div>
          <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
            {verdict.key_reasons?.length > 0 && (
              <ul style={{ margin:0, paddingLeft:18, display:'flex', flexDirection:'column', gap:4 }}>
                {verdict.key_reasons.map((r, i) => <li key={i} style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{r}</li>)}
              </ul>
            )}

            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:14 }}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Fundamentals</div>
              <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{verdict.fundamental_read}</div>
            </div>
            <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:14 }}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Sentiment</div>
              <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{verdict.sentiment_read}</div>
            </div>
            {verdict.issue_price_reason && (
              <div style={{ background:V('bg-tertiary'), border:`1px solid ${V('border-light')}`, borderRadius:V('radius-md'), padding:14 }}>
                <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:V('text-muted'), marginBottom:6 }}>Issue Pricing</div>
                <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{verdict.issue_price_reason}</div>
              </div>
            )}
            <div style={{ background:'color-mix(in srgb, #6366f1 8%, transparent)', border:'1px solid color-mix(in srgb, #6366f1 25%, transparent)', borderRadius:V('radius-md'), padding:14 }}>
              <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:'0.06em', color:'#6366f1', marginBottom:6, fontWeight:600 }}>How the two views were weighed</div>
              <div style={{ fontSize:13, color:V('text-primary'), lineHeight:1.5 }}>{verdict.reasoning}</div>
            </div>

            <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center' }}>{verdict.disclaimer}</div>
          </div>
        </Card>
      )}

      {rhp?.errors?.length > 0 && rhp?.summary_financials && (
        <div style={{ fontSize:11, color:V('text-muted'), textAlign:'center' }}>
          Note: {rhp.errors.join('; ')} — figures shown are whatever was available.
        </div>
      )}
    </div>
  )
}

// ── Trading Journal Panel ───────────────────────────────────────────────────
function JournalPanel({ journal, info = {}, onRefresh, fromDate, toDate, onFromDateChange, onToDateChange }) {
  const m = window.innerWidth < 768
  const [selectedTrade, setSelectedTrade] = useState(null)
  const [draftFrom, setDraftFrom] = useState(fromDate || '')
  const [draftTo, setDraftTo] = useState(toDate || '')
  const [appliedFrom, setAppliedFrom] = useState(fromDate || '')
  const [appliedTo, setAppliedTo] = useState(toDate || '')

  useEffect(() => {
    if (fromDate) {
      setDraftFrom(fromDate)
      setAppliedFrom(fromDate)
    }
    if (toDate) {
      setDraftTo(toDate)
      setAppliedTo(toDate)
    }
  }, [fromDate, toDate])

  const [dateError, setDateError] = useState('')
  const handleSubmitDates = (e) => {
    if (e) e.preventDefault()
    if (draftFrom && draftTo && draftFrom > draftTo) {
      setDateError('The From date is after the To date.')
      return
    }
    setDateError('')
    setAppliedFrom(draftFrom)
    setAppliedTo(draftTo)
    if (onFromDateChange) onFromDateChange(draftFrom)
    if (onToDateChange) onToDateChange(draftTo)
    if (onRefresh) onRefresh(draftFrom, draftTo)
  }



  const filteredJournal = journal


  const closedTrades = filteredJournal.filter(t => t.status === 'CLOSED')
  const totalTrades = closedTrades.length
  const wins = closedTrades.filter(t => t.pnl > 0).length
  const losses = totalTrades - wins
  const expired = filteredJournal.filter(t => t.status === 'EXPIRED').length
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0.0
  const grossProfit = closedTrades.filter(t => t.pnl > 0).reduce((acc, t) => acc + t.pnl, 0.0)
  const grossLoss = Math.abs(closedTrades.filter(t => t.pnl <= 0).reduce((acc, t) => acc + t.pnl, 0.0))
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? 99.9 : 0.0)
  const netPnl = closedTrades.reduce((acc, t) => acc + t.pnl, 0.0)

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Journal Trades" value={totalTrades} sub={`Wins: ${wins} | Losses: ${losses}${expired ? ` | ${expired} expired (not counted)` : ''}`} />
        <MetricBox label="Win Rate" value={`${winRate.toFixed(1)}%`} color={winRate >= 50 ? V('green') : V('yellow')} />
        <MetricBox label="Profit Factor" value={profitFactor.toFixed(2)} color={profitFactor >= 1.0 ? V('green') : V('red')} />
        <MetricBox label="Gross P&L" value={fmtPnl(netPnl)} color={clr(netPnl)} sub="before charges" />
      </div>

      <Card>
        <form onSubmit={handleSubmitDates} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12, flexWrap:'wrap', gap:8 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Trading Journal Log</div>
          
          <div style={{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>From:</span>
              <input 
                type="date" 
                value={draftFrom} 
                onChange={e => setDraftFrom(e.target.value)} 
                style={{
                  background: V('bg-input'),
                  color: V('text-primary'),
                  border: `1px solid ${V('border')}`,
                  borderRadius: V('radius-sm'),
                  padding: '4px 8px',
                  fontSize: 11,
                  width: '120px',
                  outline: 'none'
                }}
              />
            </div>

            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>To:</span>
              <input 
                type="date" 
                value={draftTo} 
                onChange={e => setDraftTo(e.target.value)} 
                style={{
                  background: V('bg-input'),
                  color: V('text-primary'),
                  border: `1px solid ${V('border')}`,
                  borderRadius: V('radius-sm'),
                  padding: '4px 8px',
                  fontSize: 11,
                  width: '120px',
                  outline: 'none'
                }}
              />
            </div>

            <StyledButton type="submit" variant="primary" style={{ padding:'4px 14px', fontSize:11, fontWeight:700, display:'flex', alignItems:'center', gap:4 }}>
              <Filter size={12}/> Submit
            </StyledButton>
            
            <StyledButton type="button" onClick={() => onRefresh(draftFrom, draftTo)} variant="default" style={{ padding:'4px 12px', fontSize:11 }}>
              <RefreshCw size={11}/> Refresh
            </StyledButton>
          </div>
        </form>

        {(dateError || info.error) && (
          <div style={{ color:V('red'), fontSize:12, marginBottom:8 }}>{dateError || info.error}</div>
        )}
        {filteredJournal.length === 0 ? (
          <div style={{ color:V('text-muted'), textAlign:'center', padding:40, fontSize:13 }}>
            {info.status === 'disconnected' ? 'Dhan is not connected, so broker trades cannot be loaded.'
              : (!info.status && !info.error) ? 'Loading trades from Dhan… (the first load reads ~4 months of history and can take ~10 s)'
              : 'No broker-executed trades found on Dhan for the selected date range.'}
          </div>
        ) : (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr style={{ borderBottom:`1px solid ${V('border')}` }}>
                  {['Entry', 'Status', 'Type', 'Instrument', 'Symbol/Strike', 'Entry px', 'Exit', 'Gross P&L', 'Exit Reason', 'Model Logic'].map(h => (
                    <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), fontWeight:600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredJournal.slice().reverse().map((t, idx) => {

                  const strikeStr = t.option_strike ? `${t.option_strike} ${t.option_type || ''}` : '—'
                  const pnlVal = t.pnl != null ? t.pnl : 0.0
                  return (<React.Fragment key={idx}>
                    <tr style={{ borderBottom:`1px solid ${V('border-light')}`, verticalAlign:'middle', background: idx%2===0 ? 'transparent' : V('bg-tertiary') }}>
                      <td style={{ padding:'8px 10px', color:V('text-muted'), whiteSpace:'nowrap' }}>
                        <div>{t.entry_date}</div>
                        <div style={{ fontSize:10 }}>{t.entry_time}</div>
                      </td>
                      <td style={{ padding:'8px 10px' }}>
                        <Badge label={t.status} color={t.status === 'OPEN' ? V('yellow') : V('text-muted')} />
                      </td>
                      <td style={{ padding:'8px 10px', color: t.direction === 'LONG' ? V('green') : V('red'), fontWeight:700 }}>
                        {t.direction}
                      </td>
                      <td style={{ padding:'8px 10px', color:V('text-primary') }}>
                        <div>{t.instrument}</div>
                        <div style={{ fontSize:9, color:V('text-muted') }}>{t.trade_mode}</div>
                      </td>
                      <td style={{ padding:'8px 10px', color:V('text-primary') }}>
                        <div style={{ fontWeight:500 }}>{t.symbol?.replace(t.instrument + ' ', '')}</div>
                        {t.option_strike && <div style={{ fontSize:10, color:V('purple') }}>{strikeStr}</div>}
                      </td>
                      <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>₹{fmt(t.entry_price)}</td>
                      <td style={{ padding:'8px 10px', color:V('text-primary'), whiteSpace:'nowrap' }}>
                        <div style={{ fontFamily:"'JetBrains Mono', monospace" }}>{t.exit_price != null ? `₹${fmt(t.exit_price)}` : '—'}</div>
                        {t.exit_date && <div style={{ fontSize:10, color:V('text-muted') }}>{t.exit_date}{t.exit_time ? ` ${t.exit_time}` : ''}</div>}
                      </td>
                      <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color: clr(pnlVal), fontWeight:700 }}
                          title={t.status === 'EXPIRED' ? 'Held to expiry: settled by the exchange, which is not in Dhan trade history -- P&L unknown here' : undefined}>
                        {t.status === 'CLOSED' ? fmtPnl(pnlVal) : '—'}
                      </td>
                      <td style={{ padding:'8px 10px', color:V('text-muted') }}>{t.exit_reason || '—'}</td>
                      <td style={{ padding:'8px 10px' }}>
                        <StyledButton
                          onClick={() => setSelectedTrade(selectedTrade === t.trade_id ? null : t.trade_id)}
                          variant="primary" style={{ padding:'3px 8px', fontSize:10 }}
                        >
                          {selectedTrade === t.trade_id ? 'Hide' : 'Reasons'}
                        </StyledButton>
                      </td>
                    </tr>
                    {selectedTrade === t.trade_id && (
                      <tr style={{ background:V('bg-tertiary') }}>
                        <td colSpan={10} style={{ padding:'12px 16px', borderBottom:`1px solid ${V('border')}` }}>
                          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
                            <div style={{ display:'flex', gap:24, flexWrap:'wrap', borderBottom:`1px solid ${V('border-light')}`, paddingBottom:8 }}>
                              {t.weighted_score != null && <div><span style={{ color:V('text-muted') }}>Consensus Score:</span> <strong style={{ color:V('accent') }}>{t.weighted_score.toFixed(2)} (Score: {Math.round(t.weighted_score * 15)}/15)</strong></div>}
                              {t.ml_prob != null && <div><span style={{ color:V('text-muted') }}>ML Probability:</span> <strong style={{ color:V('green') }}>{(t.ml_prob*100).toFixed(0)}%</strong></div>}
                              {t.macro_bias && <div><span style={{ color:V('text-muted') }}>Macro Bias:</span> <strong style={{ color: t.macro_bias === 'LONG' ? V('green') : V('red') }}>{t.macro_bias}</strong></div>}
                              {t.h1_trend && <div><span style={{ color:V('text-muted') }}>1H Trend:</span> <strong style={{ color: t.h1_trend === 'LONG' ? V('green') : V('red') }}>{t.h1_trend}</strong></div>}
                            </div>
                            <div>
                              <div style={{ color:V('text-head'), fontWeight:600, fontSize:11, marginBottom:4 }}>Agent Decision Reasons:</div>
                              {t.reasons && t.reasons.length > 0 ? (
                                <ul style={{ margin:0, paddingLeft:16, color:V('text-muted'), display:'flex', flexDirection:'column', gap:2 }}>
                                  {t.reasons.map((r, rIdx) => (
                                    <li key={rIdx} style={{ fontSize:11 }}>{r}</li>
                                  ))}
                                </ul>
                              ) : (
                                <div style={{ color:V('text-muted'), fontSize:11, fontStyle:'italic' }}>No detailed reasons recorded.</div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>)
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Capital Protection Card ─────────────────────────────────────────────────
function CapitalProtectionCard() {
  const [cap, setCap] = useState(null)
  const [dh, setDh] = useState(null)

  useEffect(() => {
    const fetch_ = () => {
      API.get('/api/capital').then(c => setCap(c)).catch(()=>{})
      API.get('/api/data-health').then(d => setDh(d)).catch(()=>{})
    }
    fetch_()
    const iv = setInterval(fetch_, 15000)
    return () => clearInterval(iv)
  }, [])

  if (!cap) return <Card><div style={{color:V('text-muted'), fontSize:11, padding:8}}>Loading capital data...</div></Card>

  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ color:V('yellow'), fontSize:11, textTransform:'uppercase', letterSpacing:'0.1em', fontWeight:600 }}>Capital Protection</div>
        {dh && (
          <Badge label={dh.is_stale ? 'DATA STALE' : 'DATA OK'} color={dh.is_stale ? V('red') : V('green')} />
        )}
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, fontSize:12 }}>
        <div><span style={{color:V('text-muted')}}>Equity:</span> <span style={{color:V('text-primary'), fontWeight:600}}>Rs.{fmt(cap.current_equity)}</span></div>
        <div><span style={{color:V('text-muted')}}>Peak:</span> <span style={{color:V('text-primary'), fontWeight:600}}>Rs.{fmt(cap.peak_equity)}</span></div>
        <div><span style={{color:V('text-muted')}}>Drawdown from peak:</span> <span style={{color: cap.current_drawdown > 0 ? V('red') : V('green'), fontWeight:600}}>Rs.{fmt(cap.current_drawdown)} ({cap.current_drawdown_pct?.toFixed(1)}%)</span></div>
        <div><span style={{color:V('text-muted')}}>Limit ({cap.max_drawdown_pct}%):</span> <span style={{color:V('text-primary'), fontWeight:600}}>Rs.{fmt(cap.drawdown_limit)}</span></div>
        <div><span style={{color:V('text-muted')}}>Today:</span> <span style={{color:clr(cap.today_pnl), fontWeight:600}}>{fmtPnl(cap.today_pnl)}</span></div>
        <div><span style={{color:V('text-muted')}}>Auto-trade entries:</span> <span style={{color: cap.drawdown_breached ? V('red') : V('green'), fontWeight:600}}>{cap.drawdown_breached ? 'BLOCKED (limit hit)' : 'allowed'}</span></div>
      </div>
      <div style={{ marginTop:8, height:5, background:V('bg-tertiary'), borderRadius:3, overflow:'hidden' }}>
        <div style={{ height:'100%', width:`${cap.drawdown_limit > 0 ? Math.min(100, (cap.current_drawdown / cap.drawdown_limit) * 100) : 0}%`, background: cap.current_drawdown_pct > cap.max_drawdown_pct * 0.7 ? '#ef4444' : cap.current_drawdown_pct > cap.max_drawdown_pct * 0.4 ? '#f59e0b' : '#10b981', borderRadius:3, transition:'width 0.3s' }}/>
      </div>
    </Card>
  )
}

// ── System Health Card ──────────────────────────────────────────────────────
function SystemHealthCard() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const fetch_ = () => API.get('/api/system-health').then(h => setHealth(h)).catch(()=>{})
    fetch_()
    const iv = setInterval(fetch_, 15000)
    return () => clearInterval(iv)
  }, [])

  if (!health) return <Card><div style={{color:V('text-muted'), fontSize:11, padding:8}}>Loading system health...</div></Card>

  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ color:V('cyan'), fontSize:11, textTransform:'uppercase', letterSpacing:'0.1em', fontWeight:600 }}>System Health</div>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background: health.app_state === 'RUNNING' ? V('green') : V('red') }}/>
          <span style={{ fontSize:11, color: health.app_state === 'RUNNING' ? V('green') : V('red'), fontWeight:600 }}>{health.app_state}</span>
        </div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, fontSize:12 }}>
        <div><span style={{color:V('text-muted')}}>Heartbeat:</span> <span style={{color: health.heartbeat_ok ? V('green') : V('red'), fontWeight:600}}>{health.heartbeat_ok ? 'OK' : 'STALE'}</span></div>
        <div><span style={{color:V('text-muted')}}>Watchdog:</span> <span style={{color: health.watchdog_active ? V('green') : V('yellow'), fontWeight:600}}>{health.watchdog_active ? 'Active' : 'Off'}</span></div>
        <div><span style={{color:V('text-muted')}}>PID:</span> <span style={{color:V('text-primary')}}>{health.pid}</span></div>
        <div><span style={{color:V('text-muted')}}>Uptime:</span> <span style={{color:V('text-primary')}}>{health.uptime_seconds ? (health.uptime_seconds > 3600 ? `${Math.floor(health.uptime_seconds/3600)}h ${Math.floor((health.uptime_seconds%3600)/60)}m` : `${Math.floor(health.uptime_seconds/60)}m`) : '—'}</span></div>
      </div>
      {health.heartbeat_age_seconds != null && (
        <div style={{ marginTop:4, fontSize:10, color:V('text-muted') }}>Last heartbeat: {health.heartbeat_age_seconds < 60 ? `${Math.round(health.heartbeat_age_seconds)}s ago` : `${Math.round(health.heartbeat_age_seconds/60)}m ago`}</div>
      )}
    </Card>
  )
}

// ── Phase 2 Detail Component ────────────────────────────────────────────────
function Phase2Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase2/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase2')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase2', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 2 exploratory analysis')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase2' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase2' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run Exploratory Analysis
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading analysis results...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {/* Regime Distribution */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📊 Regime Distribution (Train Set)</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {Object.entries(data.regime_distribution || {}).map(([regime, pct]) => (
                <div key={regime} style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm') }}>
                  <div style={{ fontSize: 10, color: V('text-muted'), textTransform: 'capitalize' }}>
                    {regime.replace(/_/g, ' ')}
                  </div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: V('text-primary'), marginTop: 2 }}>{pct}%</div>
                </div>
              ))}
            </div>
          </div>

          {/* Lead-Lag Results */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>⏱️ Lead-Lag Study (Nifty vs BankNifty returns)</div>
            <div style={{ overflowX: 'auto', maxHeight: 150, border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Lag (min)</th>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Correlation</th>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Direction</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.lead_lag || []).map((row, idx) => (
                    <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                      <td style={{ padding: '4px 8px', color: V('text-primary') }}>{row.lag_minutes} m</td>
                      <td style={{ padding: '4px 8px', color: Math.abs(row.correlation) > 0.15 ? V('cyan') : V('text-muted'), fontWeight: 600 }}>{row.correlation}</td>
                      <td style={{ padding: '4px 8px', color: V('text-muted'), fontSize: 10 }}>{row.leads}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Correlation Matrix */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>🧮 Static Correlation Matrix (Constituents)</div>
            <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
                <thead>
                  <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                    <th style={{ padding: '4px 6px', color: V('text-muted') }}>Asset</th>
                    {data.correlation_matrix?.columns.map(col => (
                      <th key={col} style={{ padding: '4px 6px', color: V('text-muted'), textAlign: 'center' }}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.correlation_matrix?.columns.map((rowSymbol, rowIdx) => (
                    <tr key={rowSymbol} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                      <td style={{ padding: '4px 6px', color: V('text-primary'), fontWeight: 600 }}>{rowSymbol}</td>
                      {data.correlation_matrix.data[rowIdx].map((val, colIdx) => (
                        <td 
                          key={colIdx} 
                          style={{ 
                            padding: '4px 6px', 
                            textAlign: 'center', 
                            background: val === 1 ? 'rgba(34, 197, 94, 0.15)' : (val > 0.6 ? 'rgba(59, 130, 246, 0.15)' : 'transparent'),
                            color: val > 0.6 ? V('text-primary') : V('text-muted'),
                            fontWeight: val > 0.6 ? 600 : 400
                          }}
                        >
                          {val}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Phase 3 Detail Component ────────────────────────────────────────────────
function Phase3Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase3/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase3')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase3', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 3 PCA')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase3' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase3' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run PCA Analysis
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading PCA results...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {/* Explained Variance */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📈 Explained Variance Ratio</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(data.explained_variance || []).slice(0, 5).map((val, idx) => (
                <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
                  <span style={{ color: V('text-muted'), width: 30 }}>PC{idx+1}</span>
                  <div style={{ flex: 1, background: V('bg-primary'), height: 6, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ width: `${val * 100}%`, height: '100%', background: V('accent'), borderRadius: 3 }} />
                  </div>
                  <span style={{ color: V('text-primary'), fontWeight: 600, width: 45, textAlign: 'right' }}>{(val * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </div>

          {/* PC1 & PC2 Loadings */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📐 Component Loadings (Weights)</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {['PC1', 'PC2'].map(pcName => (
                <div key={pcName} style={{ background: V('bg-primary'), borderRadius: V('radius-sm'), padding: 8 }}>
                  <div style={{ fontSize: 10, color: V('accent'), fontWeight: 600, marginBottom: 4 }}>{pcName} (Market/Sector Factor)</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 150, overflowY: 'auto' }}>
                    {Object.entries(data.loadings?.[pcName] || {}).map(([asset, weight]) => (
                      <div key={asset} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, padding: '2px 0' }}>
                        <span style={{ color: V('text-muted'), fontFamily: "'JetBrains Mono', monospace" }}>{asset}</span>
                        <span style={{ color: weight >= 0 ? '#22c55e' : V('red'), fontWeight: 600 }}>{weight >= 0 ? '+' : ''}{weight}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Rolling PC1 Stability */}
          {data.rolling_stability?.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>🔄 Rolling PC1 Loading Stability (Cosine Similarity)</div>
              <div style={{ maxHeight: 120, overflowY: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Time</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Stability</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>PC1 Var %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rolling_stability.slice(-10).map((row, idx) => (
                      <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>{row.timestamp.split(' ')[0]}</td>
                        <td style={{ padding: '4px 8px', color: row.pc1_stability > 0.8 ? '#22c55e' : V('yellow'), fontWeight: 600 }}>
                          {(row.pc1_stability * 100).toFixed(1)}%
                        </td>
                        <td style={{ padding: '4px 8px', color: V('text-primary'), fontFamily: "'JetBrains Mono', monospace" }}>
                          {(row.explained_variance_pc1 * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phase 4 Detail Component ────────────────────────────────────────────────
function Phase4Detail({ actionLoading, setActionLoading, fetchAll }) {
  const m = window.innerWidth < 768
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase4/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase4')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase4', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 4 RMT')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase4' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase4' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run RMT Denoising
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading RMT results...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {/* Theoretical Bounds */}
          <div style={{ display: 'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap: 8 }}>
            <MetricBox label="Noise Cutoff (λ+)" value={data.lambda_plus} color={V('red')} />
            <MetricBox label="Noise Floor (λ-)" value={data.lambda_minus} color={V('green')} />
            <MetricBox label="Significant Factors" value={data.retained_factors} color={V('cyan')} />
          </div>

          {/* Eigenvalue Table */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>🎲 Empirical Eigenvalues vs Marchenko-Pastur Threshold</div>
            <div style={{ overflowX: 'auto', maxHeight: 150, border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                <thead>
                  <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Index</th>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Eigenvalue</th>
                    <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.empirical_eigenvalues || []).map((val, idx) => {
                    const isSig = val > data.lambda_plus
                    return (
                      <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>#{idx + 1}</td>
                        <td style={{ padding: '4px 8px', color: isSig ? V('cyan') : V('text-primary'), fontWeight: isSig ? 600 : 400 }}>{val}</td>
                        <td style={{ padding: '4px 8px' }}>
                          <span style={{
                            fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 4,
                            background: isSig ? 'rgba(6, 182, 212, 0.15)' : 'rgba(100, 116, 139, 0.12)',
                            color: isSig ? V('cyan') : V('text-muted'),
                          }}>
                            {isSig ? 'SIGNAL' : 'NOISE'}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Regime Factor Stats */}
          {data.regime_factor_stats && Object.keys(data.regime_factor_stats).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📊 Avg Significant Factors by Regime</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {Object.entries(data.regime_factor_stats).map(([reg, stat]) => (
                  <div key={reg} style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm'), display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 10, color: V('text-muted'), textTransform: 'capitalize' }}>{reg.replace(/_/g, ' ')}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), fontFamily: "'JetBrains Mono', monospace" }}>{stat.avg_factors} ({stat.samples} w)</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phase 5 Detail Component ────────────────────────────────────────────────
function Phase5Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase5/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase5')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase5', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 5 residuals')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase5' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase5' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run Residuals & Cointegration
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading residual results...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {/* Regime Residual Stats */}
          {data.regime_residual_stats && Object.keys(data.regime_residual_stats).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📊 Residual Mean Reversion Speed by Regime</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {Object.entries(data.regime_residual_stats).map(([reg, stat]) => (
                  <div key={reg} style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm'), display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 10, color: V('text-muted'), textTransform: 'capitalize' }}>{reg.replace(/_/g, ' ')}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: V('cyan'), fontFamily: "'JetBrains Mono', monospace" }}>t½: {stat.avg_half_life_minutes}m</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Pairs Cointegration Table */}
          {data.pairs_cointegration && data.pairs_cointegration.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>🔗 Top BankNifty Pairs Cointegration (Engle-Granger)</div>
              <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Pair</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>t-Stat</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>p-Value</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Beta</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Spread t½</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.pairs_cointegration.map((row, idx) => (
                      <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 8px', color: V('text-primary'), fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{row.pair}</td>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>{row.coint_t_stat}</td>
                        <td style={{ padding: '4px 8px', color: row.is_significant_5pct ? '#22c55e' : V('text-muted'), fontWeight: row.is_significant_5pct ? 600 : 400 }}>{row.p_value}</td>
                        <td style={{ padding: '4px 8px', color: V('text-primary'), fontFamily: "'JetBrains Mono', monospace" }}>{row.beta}</td>
                        <td style={{ padding: '4px 8px', color: V('cyan'), fontWeight: 600 }}>{row.half_life_minutes !== 'N/A' ? `${row.half_life_minutes}m` : 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Stock Residual Stats Table */}
          {data.stock_residual_stats && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📉 Idiosyncratic Residual Mean Reversion (Per Stock)</div>
              <div style={{ overflowX: 'auto', maxHeight: 200, border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Stock</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>t½ (min)</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Beta</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>p-Value</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Autocorr (Lag 1)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.stock_residual_stats).map(([sym, stat]) => (
                      <tr key={sym} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 8px', color: V('text-primary'), fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{sym}</td>
                        <td style={{ padding: '4px 8px', color: V('cyan'), fontWeight: 600 }}>{stat.half_life_minutes !== 'N/A' ? `${stat.half_life_minutes}m` : 'N/A'}</td>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>{stat.beta}</td>
                        <td style={{ padding: '4px 8px', color: stat.p_value < 0.05 ? '#22c55e' : V('text-muted') }}>{stat.p_value}</td>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>{stat.autocorr_1}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phase 6 Detail Component ────────────────────────────────────────────────
function Phase6Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase6/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase6')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase6', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 6 baseline strategy')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  const formatCurrency = (val) => {
    if (val === undefined || val === null) return '—'
    const sign = val >= 0 ? '+' : ''
    return `${sign}₹${Math.round(val).toLocaleString()}`
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase6' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase6' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run Baseline Strategy
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading baseline results...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
          {/* Key Metrics Columns */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {/* Train Metrics */}
            <div style={{ background: V('bg-tertiary'), padding: 10, borderRadius: V('radius-md'), border: `1px solid ${V('border')}` }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: V('text-primary'), borderBottom: `1px solid ${V('border-light')}`, paddingBottom: 4, marginBottom: 6 }}>
                🚂 Train Split (Baseline)
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 11 }}>
                <div><span style={{ color: V('text-muted') }}>Total Return:</span> <strong style={{ color: data.train_metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444' }}>{formatCurrency(data.train_metrics.total_return_inr)} ({data.train_metrics.total_return_pct}%)</strong></div>
                <div><span style={{ color: V('text-muted') }}>Sharpe Ratio:</span> <strong>{data.train_metrics.sharpe}</strong></div>
                <div><span style={{ color: V('text-muted') }}>Win Rate:</span> <strong>{data.train_metrics.win_rate}%</strong></div>
                <div><span style={{ color: V('text-muted') }}>Profit Factor:</span> <strong>{data.train_metrics.profit_factor}</strong></div>
                <div><span style={{ color: V('text-muted') }}>Max Drawdown:</span> <strong style={{ color: '#ef4444' }}>{data.train_metrics.max_drawdown_pct}%</strong></div>
                <div><span style={{ color: V('text-muted') }}>Trades Count:</span> <strong>{data.train_metrics.trades_count}</strong></div>
              </div>
            </div>

            {/* Validation Metrics */}
            <div style={{ background: V('bg-tertiary'), padding: 10, borderRadius: V('radius-md'), border: `1px solid ${V('border')}` }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: V('text-primary'), borderBottom: `1px solid ${V('border-light')}`, paddingBottom: 4, marginBottom: 6 }}>
                🧪 Validation Split (Baseline)
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 11 }}>
                <div><span style={{ color: V('text-muted') }}>Total Return:</span> <strong style={{ color: data.validation_metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444' }}>{formatCurrency(data.validation_metrics.total_return_inr)} ({data.validation_metrics.total_return_pct}%)</strong></div>
                <div><span style={{ color: V('text-muted') }}>Sharpe Ratio:</span> <strong>{data.validation_metrics.sharpe}</strong></div>
                <div><span style={{ color: V('text-muted') }}>Win Rate:</span> <strong>{data.validation_metrics.win_rate}%</strong></div>
                <div><span style={{ color: V('text-muted') }}>Profit Factor:</span> <strong>{data.validation_metrics.profit_factor}</strong></div>
                <div><span style={{ color: V('text-muted') }}>Max Drawdown:</span> <strong style={{ color: '#ef4444' }}>{data.validation_metrics.max_drawdown_pct}%</strong></div>
                <div><span style={{ color: V('text-muted') }}>Trades Count:</span> <strong>{data.validation_metrics.trades_count}</strong></div>
              </div>
            </div>
          </div>

          {/* Regime metrics */}
          {data.regime_metrics && Object.keys(data.regime_metrics).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📊 Baseline Performance by Regime (Validation)</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
                {Object.entries(data.regime_metrics).map(([reg, stat]) => (
                  <div key={reg} style={{ background: V('bg-primary'), padding: '8px 10px', borderRadius: V('radius-sm'), border: `1px solid ${V('border-light')}` }}>
                    <div style={{ fontSize: 10, color: V('text-muted'), textTransform: 'capitalize', fontWeight: 600, marginBottom: 4 }}>{reg.replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 11, color: V('text-primary'), display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <div>Return: <strong style={{ color: stat.total_return_inr >= 0 ? '#22c55e' : '#ef4444' }}>{formatCurrency(stat.total_return_inr)}</strong></div>
                      <div>Sharpe: <strong>{stat.sharpe}</strong></div>
                      <div>Trades: <strong>{stat.trades_count}</strong></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Sample Trades */}
          {data.sample_trades && data.sample_trades.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📋 Validation Sample Trades Log (Top 10)</div>
              <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Time</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Symbol</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Dir</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Entry Px</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Exit Px</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Net PnL</th>
                      <th style={{ textAlign: 'left', padding: '4px 8px', color: V('text-muted') }}>Regime</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sample_trades.slice(0, 10).map((trade, idx) => (
                      <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 8px', color: V('text-muted'), whiteSpace: 'nowrap' }}>{trade.entry_time.split('T')[0]} {trade.entry_time.split('T')[1]?.slice(0, 5)}</td>
                        <td style={{ padding: '4px 8px', color: V('text-primary'), fontWeight: 600 }}>{trade.symbol}</td>
                        <td style={{ padding: '4px 8px', color: trade.direction === 'LONG' ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{trade.direction}</td>
                        <td style={{ padding: '4px 8px', fontFamily: "'JetBrains Mono', monospace" }}>₹{Math.round(trade.entry_price).toLocaleString()}</td>
                        <td style={{ padding: '4px 8px', fontFamily: "'JetBrains Mono', monospace" }}>₹{Math.round(trade.exit_price).toLocaleString()}</td>
                        <td style={{ padding: '4px 8px', color: trade.net_pnl >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(trade.net_pnl)}</td>
                        <td style={{ padding: '4px 8px', color: V('text-muted') }}>{trade.regime}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phase 7 Detail Component ────────────────────────────────────────────────
function Phase7Detail({ actionLoading, setActionLoading, fetchAll }) {
  const m = window.innerWidth < 768
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase7/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase7')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase7', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 7 feature engineering')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase7' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase7' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Build Features & Splits
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Loading feature preview...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          {/* Data Set Sizes Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: m ? '1fr' : 'repeat(3, 1fr)', gap: 8 }}>
            <div style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm'), border: `1px solid ${V('border-light')}` }}>
              <div style={{ fontSize: 9, color: V('text-muted') }}>TRAINING SPLIT ROWS</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: V('text-primary'), marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>{data.train_rows?.toLocaleString() || 0}</div>
            </div>
            <div style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm'), border: `1px solid ${V('border-light')}` }}>
              <div style={{ fontSize: 9, color: V('text-muted') }}>VALIDATION SPLIT ROWS</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: V('text-primary'), marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>{data.val_rows?.toLocaleString() || 0}</div>
            </div>
            <div style={{ background: V('bg-primary'), padding: '6px 10px', borderRadius: V('radius-sm'), border: `1px solid ${V('border-light')}` }}>
              <div style={{ fontSize: 9, color: V('text-muted') }}>TEST SPLIT ROWS (FROZEN)</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: V('cyan'), marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>{data.test_rows?.toLocaleString() || 0}</div>
            </div>
          </div>

          {/* Columns List */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 4 }}>🛠️ Engineered Features ({data.columns?.filter(c => c.startsWith('feat_')).length} columns)</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, background: V('bg-tertiary'), padding: 8, borderRadius: V('radius-sm'), border: `1px solid ${V('border')}` }}>
              {data.columns?.filter(c => c.startsWith('feat_')).map(col => (
                <span key={col} style={{ fontSize: 9, padding: '2px 6px', background: V('bg-primary'), border: `1px solid ${V('border-light')}`, borderRadius: 4, color: V('text-primary'), fontFamily: "'JetBrains Mono', monospace" }}>{col.replace('feat_', '')}</span>
              ))}
            </div>
          </div>

          {/* Feature Matrix Preview */}
          {data.preview && data.preview.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📋 Feature Table Preview (Top 5 Rows)</div>
              <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>Timestamp</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>Symbol</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>Close</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>RSI</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>ADX</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>PC1</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>Residual Z</th>
                      <th style={{ textAlign: 'left', padding: '4px 6px', color: V('text-muted') }}>Target</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.preview.map((row, idx) => (
                      <tr key={idx} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '4px 6px', color: V('text-muted'), whiteSpace: 'nowrap' }}>{row.timestamp?.split('T')[0]} {row.timestamp?.split('T')[1]?.slice(0, 5)}</td>
                        <td style={{ padding: '4px 6px', color: V('text-primary'), fontWeight: 600 }}>{row.symbol}</td>
                        <td style={{ padding: '4px 6px', fontFamily: "'JetBrains Mono', monospace" }}>{Number(row.close).toFixed(2)}</td>
                        <td style={{ padding: '4px 6px', fontFamily: "'JetBrains Mono', monospace" }}>{Number(row.feat_rsi_14).toFixed(1)}</td>
                        <td style={{ padding: '4px 6px', fontFamily: "'JetBrains Mono', monospace" }}>{Number(row.feat_adx_14).toFixed(1)}</td>
                        <td style={{ padding: '4px 6px', fontFamily: "'JetBrains Mono', monospace", color: row.feat_pc1 >= 0 ? '#22c55e' : '#ef4444' }}>{Number(row.feat_pc1).toFixed(2)}</td>
                        <td style={{ padding: '4px 6px', fontFamily: "'JetBrains Mono', monospace", color: row.feat_residual_zscore >= 0 ? '#22c55e' : '#ef4444' }}>{Number(row.feat_residual_zscore).toFixed(2)}</td>
                        <td style={{ padding: '4px 6px', color: row.target_binary_5 === 1 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{row.target_binary_5}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phase 8 Detail Component ────────────────────────────────────────────────
function Phase8Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase8/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase8')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase8', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 8 machine learning')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  const formatCurrency = (val) => {
    if (val === undefined || val === null || isNaN(val)) return '—'
    const sign = val >= 0 ? '+' : ''
    return `${sign}₹${Math.round(val).toLocaleString()}`
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase8' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase8' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Train & Compare Models
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Running ML training and backtest simulations...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
          {/* Recommendation Go/No-Go Card */}
          <div
            style={{
              background: data.go_decision.recommendation === 'GO' ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)',
              border: `1px solid ${data.go_decision.recommendation === 'GO' ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
              padding: '12px 16px',
              borderRadius: V('radius-md'),
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span
                style={{
                  padding: '2px 8px',
                  borderRadius: 12,
                  fontSize: 11,
                  fontWeight: 800,
                  color: '#ffffff',
                  background: data.go_decision.recommendation === 'GO' ? '#22c55e' : '#ef4444',
                }}
              >
                {data.go_decision.recommendation} Decision
              </span>
              <span style={{ fontSize: 12, fontWeight: 700, color: V('text-primary') }}>
                Factor research go/no-go recommendation
              </span>
            </div>
            <p style={{ margin: 0, fontSize: 11, color: V('text-muted'), lineHeight: 1.4 }}>
              {data.go_decision.rationale}
            </p>
          </div>

          {/* Model Comparison Table */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📈 Models Performance Comparison (Validation Set)</div>
            <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Metric</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Model A (Traditional)</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Model B (Factors + Trad)</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Baseline (Phase 6)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Features Used</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_a.features_used}</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600, color: V('accent') }}>{data.model_b.features_used}</td>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>1 (Residual Z-Score)</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Return (₹)</td>
                    <td style={{ padding: '6px 10px', color: data.model_a.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.model_a.metrics.total_return_inr)}</td>
                    <td style={{ padding: '6px 10px', color: data.model_b.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.model_b.metrics.total_return_inr)}</td>
                    <td style={{ padding: '6px 10px', color: data.baseline.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.baseline.metrics.total_return_inr)}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Return (%)</td>
                    <td style={{ padding: '6px 10px', color: data.model_a.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{data.model_a.metrics.total_return_pct}%</td>
                    <td style={{ padding: '6px 10px', color: data.model_b.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{data.model_b.metrics.total_return_pct}%</td>
                    <td style={{ padding: '6px 10px', color: data.baseline.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{data.baseline.metrics.total_return_pct}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Sharpe Ratio</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_a.metrics.sharpe}</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600, color: V('cyan') }}>{data.model_b.metrics.sharpe}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.sharpe}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Win Rate</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.win_rate}%</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_b.metrics.win_rate}%</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.win_rate}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Profit Factor</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.profit_factor}</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_b.metrics.profit_factor}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.profit_factor}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Max Drawdown</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444' }}>{data.model_a.metrics.max_drawdown_pct}%</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444' }}>{data.model_b.metrics.max_drawdown_pct}%</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444' }}>{data.baseline.metrics.max_drawdown_pct}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Trades Count</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.trades_count}</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_b.metrics.trades_count}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.trades_count}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Bootstrap Sharpe (Mean ± Std)</td>
                    <td style={{ padding: '6px 10px', fontFamily: "'JetBrains Mono', monospace" }}>{data.model_a.bootstrap.sharpe_mean} ± {data.model_a.bootstrap.sharpe_std}</td>
                    <td style={{ padding: '6px 10px', fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>{data.model_b.bootstrap.sharpe_mean} ± {data.model_b.bootstrap.sharpe_std}</td>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>—</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Bootstrap Win Rate (Mean ± Std)</td>
                    <td style={{ padding: '6px 10px', fontFamily: "'JetBrains Mono', monospace" }}>{data.model_a.bootstrap.win_rate_mean}% ± {data.model_a.bootstrap.win_rate_std}%</td>
                    <td style={{ padding: '6px 10px', fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>{data.model_b.bootstrap.win_rate_mean}% ± {data.model_b.bootstrap.win_rate_std}%</td>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>—</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Phase9Detail({ actionLoading, setActionLoading, fetchAll }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await API.get('/api/research/phase9/data').catch(() => null)
      if (res && !res.error) {
        setData(res)
      } else if (res && res.error) {
        setError(res.error)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchResults()
  }, [])

  const handleRun = async () => {
    setActionLoading('phase9')
    setError(null)
    try {
      const res = await API.post('/api/research/run/phase9', {}).catch(() => null)
      if (res && res.success) {
        setData(res.data)
        await fetchAll()
      } else {
        setError(res?.error || 'Failed to run Phase 9 walk-forward backtest')
      }
    } catch (e) {
      setError(e.message)
    }
    setActionLoading(null)
  }

  const formatCurrency = (val) => {
    if (val === undefined || val === null || isNaN(val)) return '—'
    const sign = val >= 0 ? '+' : ''
    return `${sign}₹${Math.round(val).toLocaleString()}`
  }

  return (
    <div style={{ marginTop: 12, borderTop: `1px solid ${V('border-light')}`, paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
        <StyledButton
          onClick={handleRun}
          variant="primary"
          disabled={actionLoading === 'phase9' || loading}
          style={{ padding: '6px 12px', fontSize: 11 }}
        >
          {actionLoading === 'phase9' ? <RefreshCw size={11} className="spin" /> : <Play size={11} />}
          Run Walk-Forward Backtest
        </StyledButton>
        <StyledButton onClick={fetchResults} variant="default" style={{ padding: '6px 12px', fontSize: 11 }}>
          <RefreshCw size={11} /> Load Data
        </StyledButton>
      </div>

      {loading && <div style={{ color: V('text-muted'), fontSize: 11 }}>Running walk-forward simulations on out-of-sample test set (2025-10-01 to 2026-06-30)...</div>}
      {error && <div style={{ color: V('red'), fontSize: 11, marginBottom: 10 }}>⚠ {error}</div>}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
          {/* Recommendation Card */}
          <div
            style={{
              background: data.go_decision.recommendation === 'GO' ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)',
              border: `1px solid ${data.go_decision.recommendation === 'GO' ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
              padding: '12px 16px',
              borderRadius: V('radius-md'),
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span
                style={{
                  padding: '2px 8px',
                  borderRadius: 12,
                  fontSize: 11,
                  fontWeight: 800,
                  color: '#ffffff',
                  background: data.go_decision.recommendation === 'GO' ? '#22c55e' : '#ef4444',
                }}
              >
                {data.go_decision.recommendation} Decision
              </span>
              <span style={{ fontSize: 12, fontWeight: 700, color: V('text-primary') }}>
                Walk-Forward Backtesting Verdict
              </span>
            </div>
            <p style={{ margin: 0, fontSize: 11, color: V('text-muted'), lineHeight: 1.4 }}>
              {data.go_decision.rationale}
            </p>
          </div>

          {/* Success Criteria List */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>✔️ Success Criteria Checklist</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {Object.entries(data.success_criteria).map(([key, criterion]) => (
                <div
                  key={key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '8px 12px',
                    background: V('bg-secondary'),
                    border: `1px solid ${V('border-light')}`,
                    borderRadius: V('radius-sm'),
                  }}
                >
                  {criterion.status ? (
                    <CheckCircle size={14} style={{ color: '#22c55e', flexShrink: 0 }} />
                  ) : (
                    <XCircle size={14} style={{ color: '#ef4444', flexShrink: 0 }} />
                  )}
                  <span style={{ fontSize: 11, color: V('text-primary') }}>{criterion.description}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Out-of-Sample Performance Table */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📈 Out-of-Sample Performance (Test Split)</div>
            <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Metric</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Model A (Traditional)</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Model B (Factors + Trad)</th>
                    <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Baseline (Z-Score)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Return (₹)</td>
                    <td style={{ padding: '6px 10px', color: data.model_a.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.model_a.metrics.total_return_inr)}</td>
                    <td style={{ padding: '6px 10px', color: data.model_b.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.model_b.metrics.total_return_inr)}</td>
                    <td style={{ padding: '6px 10px', color: data.baseline.metrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{formatCurrency(data.baseline.metrics.total_return_inr)}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Return (%)</td>
                    <td style={{ padding: '6px 10px', color: data.model_a.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{data.model_a.metrics.total_return_pct}%</td>
                    <td style={{ padding: '6px 10px', color: data.model_b.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{data.model_b.metrics.total_return_pct}%</td>
                    <td style={{ padding: '6px 10px', color: data.baseline.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{data.baseline.metrics.total_return_pct}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Sharpe Ratio</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_a.metrics.sharpe}</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600, color: V('cyan') }}>{data.model_b.metrics.sharpe}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.sharpe}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Win Rate</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.win_rate}%</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_b.metrics.win_rate}%</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.win_rate}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Profit Factor</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.profit_factor}</td>
                    <td style={{ padding: '6px 10px', fontWeight: 600 }}>{data.model_b.metrics.profit_factor}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.profit_factor}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Max Drawdown</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444' }}>{data.model_a.metrics.max_drawdown_pct}%</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444', fontWeight: 600 }}>{data.model_b.metrics.max_drawdown_pct}%</td>
                    <td style={{ padding: '6px 10px', color: '#ef4444' }}>{data.baseline.metrics.max_drawdown_pct}%</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Trades Count</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.trades_count}</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_b.metrics.trades_count}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.trades_count}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Avg Bars Held</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_a.metrics.avg_bars_held}</td>
                    <td style={{ padding: '6px 10px' }}>{data.model_b.metrics.avg_bars_held}</td>
                    <td style={{ padding: '6px 10px' }}>{data.baseline.metrics.avg_bars_held}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Fees (₹)</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.model_a.metrics.total_fees)}</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.model_b.metrics.total_fees)}</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.baseline.metrics.total_fees)}</td>
                  </tr>
                  <tr style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>Total Slippage (₹)</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.model_a.metrics.total_slippage)}</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.model_b.metrics.total_slippage)}</td>
                    <td style={{ padding: '6px 10px' }}>{formatCurrency(data.baseline.metrics.total_slippage)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Model B Regime Performance Breakdown */}
          {data.model_b.regime_metrics && Object.keys(data.model_b.regime_metrics).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: V('text-primary'), marginBottom: 6 }}>📊 Model B Performance Breakdown by Regime</div>
              <div style={{ overflowX: 'auto', border: `1px solid ${V('border-light')}`, borderRadius: V('radius-sm') }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                  <thead>
                    <tr style={{ background: V('bg-primary'), borderBottom: `1px solid ${V('border')}` }}>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Regime</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Trades</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Win Rate</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Profit Factor</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Sharpe</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px', color: V('text-muted') }}>Net Return (₹)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.model_b.regime_metrics).map(([regName, regMetrics]) => (
                      <tr key={regName} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                        <td style={{ padding: '6px 10px', fontWeight: 600, color: V('text-primary') }}>
                          {regName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                        </td>
                        <td style={{ padding: '6px 10px' }}>{regMetrics.trades_count}</td>
                        <td style={{ padding: '6px 10px' }}>{regMetrics.win_rate}%</td>
                        <td style={{ padding: '6px 10px' }}>{regMetrics.profit_factor}</td>
                        <td style={{ padding: '6px 10px', fontWeight: 600, color: regMetrics.sharpe >= 0 ? V('green') : V('red') }}>
                          {regMetrics.sharpe}
                        </td>
                        <td style={{ padding: '6px 10px', fontWeight: 700, color: regMetrics.total_return_inr >= 0 ? '#22c55e' : '#ef4444', fontFamily: "'JetBrains Mono', monospace" }}>
                          {formatCurrency(regMetrics.total_return_inr)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Research Pipeline Panel ─────────────────────────────────────────────────
function ResearchPanel() {
  const m = window.innerWidth < 768
  const [pipelineStatus, setPipelineStatus] = useState(null)
  const [pullProgress, setPullProgress] = useState(null)
  const [inventory, setInventory] = useState(null)
  const [qualityReport, setQualityReport] = useState(null)
  const [notes, setNotes] = useState('')
  const [notesEditing, setNotesEditing] = useState(false)
  const [notesDraft, setNotesDraft] = useState('')
  const [expandedPhase, setExpandedPhase] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(null)

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, invRes, notesRes] = await Promise.all([
        API.get('/api/research/status').catch(() => null),
        API.get('/api/research/inventory').catch(() => null),
        API.get('/api/research/notes').catch(() => null),
      ])
      if (statusRes && !statusRes.error) setPipelineStatus(statusRes)
      if (invRes) setInventory(invRes)
      if (notesRes) {
        setNotes(notesRes.content || '')
        setNotesDraft(notesRes.content || '')
      }
    } catch (e) {
      console.error('Failed to fetch research data:', e)
    }
    setLoading(false)
  }, [])

  // Poll pull progress when active
  useEffect(() => {
    fetchAll()
    const id = setInterval(async () => {
      const prog = await API.get('/api/research/phase1/status').catch(() => null)
      if (prog && prog.status !== 'not_started') {
        setPullProgress(prog)
        if (prog.status === 'done' || prog.status === 'error') {
          fetchAll() // Refresh everything when done
        }
      }
    }, 3000)
    return () => clearInterval(id)
  }, [fetchAll])

  const handleScaffold = async () => {
    setActionLoading('scaffold')
    const r = await API.post('/api/research/scaffold', {}).catch(() => null)
    if (r) await fetchAll()
    setActionLoading(null)
  }

  const handleRunPhase1 = async () => {
    setActionLoading('phase1')
    const r = await API.post('/api/research/run/phase1', {}).catch(() => null)
    if (r?.error) alert(r.error)
    setActionLoading(null)
  }

  const handleSaveNotes = async () => {
    await API.post('/api/research/notes', { content: notesDraft }).catch(() => null)
    setNotes(notesDraft)
    setNotesEditing(false)
  }

  const handleFetchQuality = async () => {
    const r = await API.get('/api/research/phase1/quality').catch(() => null)
    if (r) setQualityReport(r)
  }

  const phases = pipelineStatus?.phases || {}

  // Phase metadata for display
  const PHASE_META = {
    phase_0:  { icon: '🏗️', color: '#8b5cf6', goal: 'Reusable, testable repo skeleton' },
    phase_1:  { icon: '📊', color: '#3b82f6', goal: 'Clean, corporate-action-aware dataset', blocking: true },
    phase_2:  { icon: '🔍', color: '#10b981', goal: 'Lead-lag, regime tagging, exploration' },
    phase_3:  { icon: '📐', color: '#f59e0b', goal: 'PCA on Nifty 50 & BankNifty constituents' },
    phase_4:  { icon: '🎲', color: '#ef4444', goal: 'Marchenko-Pastur noise filtering' },
    phase_5:  { icon: '📉', color: '#ec4899', goal: 'Residuals & cointegration analysis' },
    phase_6:  { icon: '💰', color: '#14b8a6', goal: 'Costs, slippage & baseline strategy' },
    phase_7:  { icon: '🔧', color: '#6366f1', goal: 'Traditional + factor feature engineering' },
    phase_8:  { icon: '🤖', color: '#a855f7', goal: 'ML comparison: trad vs factor features' },
    phase_9:  { icon: '📈', color: '#22c55e', goal: 'Walk-forward backtest (test set unlocked)' },
    phase_10: { icon: '🔴', color: '#f43f5e', goal: 'Paper trading with live DhanHQ feed' },
  }

  const getStatusBadge = (status) => {
    const map = {
      'not_started': { label: 'Not Started', bg: V('bg-tertiary'), color: V('text-muted'), border: V('border-light') },
      'in_progress': { label: 'In Progress', bg: 'color-mix(in srgb, #f59e0b 12%, transparent)', color: '#f59e0b', border: 'color-mix(in srgb, #f59e0b 30%, transparent)' },
      'complete':    { label: 'Complete',     bg: 'color-mix(in srgb, #22c55e 12%, transparent)', color: '#22c55e', border: 'color-mix(in srgb, #22c55e 30%, transparent)' },
    }
    const s = map[status] || map['not_started']
    return (
      <span style={{
        background: s.bg, color: s.color, border: `1px solid ${s.border}`,
        borderRadius: 20, padding: '2px 10px', fontSize: 10, fontWeight: 600,
      }}>{s.label}</span>
    )
  }

  if (loading) return <Card><div style={{color:V('text-muted'), textAlign:'center', padding:40}}>Loading research pipeline...</div></Card>

  // Pull progress bar
  const PullProgressBar = () => {
    if (!pullProgress || pullProgress.status === 'not_started') return null
    const pct = pullProgress.total > 0 ? Math.round((pullProgress.symbols_done / pullProgress.total) * 100) : 0
    const statusLabel = {
      'starting': 'Initializing...',
      'pulling': `Pulling data: ${pullProgress.current || ''}`,
      'complete': 'Data pull complete — running checks...',
      'checking_corporate_actions': 'Checking corporate actions...',
      'generating_quality_report': 'Generating quality report...',
      'done': 'Phase 1 Complete ✓',
      'error': `Error: ${pullProgress.error_message || 'Unknown'}`,
    }[pullProgress.status] || pullProgress.status

    return (
      <div style={{
        background: V('bg-tertiary'), borderRadius: V('radius-md'), padding: 16,
        border: `1px solid ${pullProgress.status === 'error' ? V('red') : pullProgress.status === 'done' ? 'color-mix(in srgb, #22c55e 30%, transparent)' : 'color-mix(in srgb, #3b82f6 30%, transparent)'}`,
        marginBottom: 14,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ color: V('text-primary'), fontSize: 13, fontWeight: 600 }}>
            📡 Phase 1 — Data Acquisition
          </span>
          <span style={{ color: V('text-muted'), fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
            {pullProgress.symbols_done}/{pullProgress.total} symbols
          </span>
        </div>
        <div style={{ background: V('bg-primary'), borderRadius: 6, height: 8, overflow: 'hidden', marginBottom: 6 }}>
          <div style={{
            height: '100%', borderRadius: 6, transition: 'width 0.5s ease',
            width: `${pct}%`,
            background: pullProgress.status === 'done' ? '#22c55e' : pullProgress.status === 'error' ? V('red') : 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
          }} />
        </div>
        <div style={{ color: V('text-muted'), fontSize: 11 }}>{statusLabel}</div>
        {pullProgress.errors?.length > 0 && (
          <div style={{ color: V('red'), fontSize: 11, marginTop: 6 }}>
            ⚠ Failed: {pullProgress.errors.join(', ')}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* ── Action Buttons ── */}
      <Card style={{ padding: 14 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <StyledButton
            onClick={handleScaffold}
            variant="default"
            disabled={actionLoading === 'scaffold' || phases.phase_0?.status === 'complete'}
            style={{ padding: '8px 16px', fontSize: 12 }}
          >
            {actionLoading === 'scaffold' ? <RefreshCw size={12} className="spin" /> : <Target size={12} />}
            {phases.phase_0?.status === 'complete' ? '✓ Scaffolded' : 'Scaffold Phase 0'}
          </StyledButton>
          <StyledButton
            onClick={handleRunPhase1}
            variant="primary"
            disabled={actionLoading === 'phase1' || (pullProgress && !['not_started','done','error'].includes(pullProgress.status))}
            style={{ padding: '8px 16px', fontSize: 12 }}
          >
            {actionLoading === 'phase1' ? <RefreshCw size={12} className="spin" /> : <Activity size={12} />}
            Run Phase 1 — Pull Data
          </StyledButton>
          <StyledButton
            onClick={handleFetchQuality}
            variant="default"
            style={{ padding: '8px 16px', fontSize: 12 }}
          >
            <Shield size={12} /> View Data Quality
          </StyledButton>
          <StyledButton onClick={fetchAll} variant="default" style={{ padding: '8px 16px', fontSize: 12 }}>
            <RefreshCw size={12} /> Refresh
          </StyledButton>
        </div>
      </Card>

      {/* ── Pull Progress ── */}
      <PullProgressBar />

      {/* ── Pipeline Stepper ── */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <Target size={16} style={{ color: V('accent') }} />
          <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 15 }}>Research Pipeline</span>
          <Badge label="11 Phases" color={V('accent')} />
          {pipelineStatus?.last_updated && (
            <span style={{ color: V('text-muted'), fontSize: 10, marginLeft: 'auto' }}>
              Updated: {new Date(pipelineStatus.last_updated).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {Object.entries(phases).map(([phaseId, phase]) => {
            const meta = PHASE_META[phaseId] || { icon: '📋', color: '#888', goal: '' }
            const isExpanded = expandedPhase === phaseId
            const steps = phase.steps || {}
            const stepsDone = Object.values(steps).filter(Boolean).length
            const stepsTotal = Object.keys(steps).length
            const pct = stepsTotal > 0 ? Math.round((stepsDone / stepsTotal) * 100) : 0

            return (
              <div key={phaseId} style={{ borderRadius: V('radius-md'), overflow: 'hidden' }}>
                {/* Phase header */}
                <button
                  onClick={() => setExpandedPhase(isExpanded ? null : phaseId)}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px', background: isExpanded ? V('bg-tertiary') : 'transparent',
                    border: 'none', borderRadius: V('radius-md'), cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => { if (!isExpanded) e.currentTarget.style.background = V('bg-tertiary') }}
                  onMouseLeave={e => { if (!isExpanded) e.currentTarget.style.background = 'transparent' }}
                >
                  <span style={{ fontSize: 18 }}>{meta.icon}</span>
                  <div style={{ flex: 1, textAlign: 'left' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ color: V('text-primary'), fontSize: 13, fontWeight: 600 }}>
                        {phaseId.replace('phase_', 'Phase ')} — {phase.name}
                      </span>
                      {getStatusBadge(phase.status)}
                      {phase.blocking && <span style={{ color: V('red'), fontSize: 9, fontWeight: 700 }}>BLOCKING</span>}
                    </div>
                    <div style={{ color: V('text-muted'), fontSize: 11, marginTop: 2 }}>{meta.goal}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {stepsTotal > 0 && (
                      <span style={{ color: V('text-muted'), fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
                        {stepsDone}/{stepsTotal}
                      </span>
                    )}
                    {/* Mini progress bar */}
                    {stepsTotal > 0 && (
                      <div style={{ width: 60, height: 4, background: V('bg-primary'), borderRadius: 2 }}>
                        <div style={{
                          height: '100%', borderRadius: 2, transition: 'width 0.3s',
                          width: `${pct}%`,
                          background: phase.status === 'complete' ? '#22c55e' : meta.color,
                        }} />
                      </div>
                    )}
                    <ChevronDown size={14} style={{
                      color: V('text-muted'), transition: 'transform 0.2s',
                      transform: isExpanded ? 'rotate(180deg)' : 'rotate(0)',
                    }} />
                  </div>
                </button>

                {/* Expanded steps */}
                {isExpanded && (
                  <div style={{
                    padding: '6px 14px 14px 46px',
                    background: V('bg-tertiary'), borderBottomLeftRadius: V('radius-md'),
                    borderBottomRightRadius: V('radius-md'),
                  }}>
                    <div style={{ marginBottom: 10 }}>
                      {Object.entries(steps).map(([stepId, done]) => (
                        <div key={stepId} style={{
                          display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
                          color: done ? '#22c55e' : V('text-muted'), fontSize: 12,
                        }}>
                          {done ? <CheckCircle size={13} /> : <XCircle size={13} style={{ opacity: 0.4 }} />}
                          <span style={{ fontWeight: done ? 500 : 400 }}>
                            {stepId.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                          </span>
                        </div>
                      ))}
                    </div>

                    {phaseId === 'phase_2' && (
                      <Phase2Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_3' && (
                      <Phase3Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_4' && (
                      <Phase4Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_5' && (
                      <Phase5Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_6' && (
                      <Phase6Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_7' && (
                      <Phase7Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_8' && (
                      <Phase8Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                    {phaseId === 'phase_9' && (
                      <Phase9Detail
                        actionLoading={actionLoading}
                        setActionLoading={setActionLoading}
                        fetchAll={fetchAll}
                      />
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </Card>

      {/* ── Data Inventory ── */}
      {inventory && inventory.files && inventory.files.length > 0 && (
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <BarChart2 size={16} style={{ color: '#3b82f6' }} />
            <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 14 }}>Data Inventory</span>
            <Badge label={`${inventory.files.length} files`} color="#3b82f6" />
            <span style={{ color: V('text-muted'), fontSize: 11, marginLeft: 'auto' }}>
              Total: {inventory.total_size_mb} MB
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${V('border')}` }}>
                  {['Symbol', 'Timeframe', 'Size'].map(h => (
                    <th key={h} style={{
                      textAlign: 'left', padding: '6px 10px', color: V('text-muted'),
                      fontSize: 10, textTransform: 'uppercase', fontWeight: 600,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {inventory.files.map((f, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '6px 10px', color: V('text-primary'), fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{f.symbol}</td>
                    <td style={{ padding: '6px 10px', color: V('text-muted') }}>{f.timeframe}</td>
                    <td style={{ padding: '6px 10px', color: V('text-muted'), fontFamily: "'JetBrains Mono', monospace" }}>{f.size_kb} KB</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── Data Quality Report ── */}
      {qualityReport && !qualityReport.error && (
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Shield size={16} style={{ color: qualityReport.summary?.concerns?.length > 0 ? '#f59e0b' : '#22c55e' }} />
            <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 14 }}>Data Quality Report</span>
            {qualityReport.summary?.concerns?.length > 0 && (
              <Badge label={`${qualityReport.summary.concerns.length} concerns`} color="#f59e0b" />
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
            <MetricBox label="Files Analyzed" value={qualityReport.summary?.total_files || 0} />
            <MetricBox label="Total Rows" value={(qualityReport.summary?.total_rows || 0).toLocaleString()} />
            <MetricBox
              label="Concerns"
              value={qualityReport.summary?.symbols_with_concerns || 0}
              color={qualityReport.summary?.symbols_with_concerns > 0 ? V('yellow') : V('green')}
            />
          </div>

          {qualityReport.summary?.concerns?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {qualityReport.summary.concerns.map((c, i) => (
                <div key={i} style={{
                  background: 'color-mix(in srgb, #f59e0b 8%, transparent)',
                  border: '1px solid color-mix(in srgb, #f59e0b 20%, transparent)',
                  borderRadius: V('radius-sm'), padding: '6px 10px',
                  color: '#f59e0b', fontSize: 11,
                }}>
                  ⚠ {c}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* ── Corporate Action Flags ── */}
      {pullProgress?.corporate_actions?.total_flags > 0 && (
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <AlertCircle size={16} style={{ color: V('red') }} />
            <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 14 }}>Corporate Action Flags</span>
            <Badge label={`${pullProgress.corporate_actions.total_flags} flags`} color={V('red')} />
          </div>
          <div style={{ fontSize: 11, color: V('text-muted'), marginBottom: 8 }}>
            These overnight jumps exceed 15% threshold and may be unadjusted splits/bonuses.
            Verify before trusting downstream analysis.
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${V('border')}` }}>
                  {['Symbol', 'Date', 'Prev Close', 'Next Open', 'Jump %', 'Likely Action'].map(h => (
                    <th key={h} style={{
                      textAlign: 'left', padding: '5px 8px', color: V('text-muted'),
                      fontSize: 9, textTransform: 'uppercase', fontWeight: 600,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(pullProgress.corporate_actions.flags || []).map((f, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${V('border-light')}` }}>
                    <td style={{ padding: '5px 8px', color: V('text-primary'), fontWeight: 600 }}>{f.symbol}</td>
                    <td style={{ padding: '5px 8px', color: V('text-muted') }}>{f.date}</td>
                    <td style={{ padding: '5px 8px', color: V('text-muted'), fontFamily: "'JetBrains Mono', monospace" }}>₹{f.prev_close}</td>
                    <td style={{ padding: '5px 8px', color: V('text-muted'), fontFamily: "'JetBrains Mono', monospace" }}>₹{f.next_open}</td>
                    <td style={{ padding: '5px 8px', color: Math.abs(f.overnight_return_pct) > 30 ? V('red') : '#f59e0b', fontWeight: 600 }}>
                      {f.overnight_return_pct > 0 ? '+' : ''}{f.overnight_return_pct}%
                    </td>
                    <td style={{ padding: '5px 8px', color: V('text-muted'), fontSize: 10 }}>{f.likely_action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── Research Notes ── */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <BookOpen size={16} style={{ color: V('accent') }} />
          <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 14 }}>Research Notes</span>
          {!notesEditing ? (
            <StyledButton onClick={() => setNotesEditing(true)} variant="default" style={{ marginLeft: 'auto', padding: '4px 10px', fontSize: 11 }}>
              Edit
            </StyledButton>
          ) : (
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <StyledButton onClick={handleSaveNotes} variant="primary" style={{ padding: '4px 10px', fontSize: 11 }}>Save</StyledButton>
              <StyledButton onClick={() => { setNotesEditing(false); setNotesDraft(notes) }} variant="default" style={{ padding: '4px 10px', fontSize: 11 }}>Cancel</StyledButton>
            </div>
          )}
        </div>
        {notesEditing ? (
          <textarea
            value={notesDraft}
            onChange={e => setNotesDraft(e.target.value)}
            style={{
              width: '100%', minHeight: 200, background: V('bg-tertiary'),
              color: V('text-primary'), border: `1px solid ${V('border')}`,
              borderRadius: V('radius-md'), padding: 12, fontSize: 12,
              fontFamily: "'JetBrains Mono', monospace", resize: 'vertical', outline: 'none',
            }}
          />
        ) : (
          <pre style={{
            color: V('text-secondary'), fontSize: 12, whiteSpace: 'pre-wrap',
            fontFamily: "'JetBrains Mono', monospace", lineHeight: 1.6,
            maxHeight: 300, overflow: 'auto', margin: 0,
            background: V('bg-tertiary'), borderRadius: V('radius-md'), padding: 12,
          }}>
            {notes || 'No research notes yet. Click Edit to add notes.'}
          </pre>
        )}
      </Card>

      {/* ── Ground Rules (collapsible) ── */}
      <Card>
        <button
          onClick={() => setExpandedPhase(expandedPhase === 'rules' ? null : 'rules')}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, width: '100%',
            background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
          }}
        >
          <Shield size={16} style={{ color: '#f59e0b' }} />
          <span style={{ color: V('text-primary'), fontWeight: 700, fontSize: 14 }}>Ground Rules & Success Criteria</span>
          <ChevronDown size={14} style={{
            color: V('text-muted'), marginLeft: 'auto', transition: 'transform 0.2s',
            transform: expandedPhase === 'rules' ? 'rotate(180deg)' : 'rotate(0)',
          }} />
        </button>
        {expandedPhase === 'rules' && (
          <div style={{ marginTop: 12, fontSize: 12, color: V('text-secondary'), lineHeight: 1.7 }}>
            <div style={{ fontWeight: 600, color: '#f59e0b', marginBottom: 6 }}>Ground Rules:</div>
            <ul style={{ paddingLeft: 20, margin: '0 0 12px 0' }}>
              <li>No phase is "done" until its Deliverable checklist is committed</li>
              <li>Never touch the test window until Phase 9</li>
              <li>Every backtest must include costs and slippage</li>
              <li>Corporate actions are BLOCKING — Phase 1 must pass first</li>
              <li>Log data source, API endpoint, and pull timestamp for every dataset</li>
            </ul>
            <div style={{ fontWeight: 600, color: '#22c55e', marginBottom: 6 }}>Success Criteria (test set only):</div>
            <ul style={{ paddingLeft: 20, margin: 0 }}>
              <li>Win rate improves over Phase 6 baseline</li>
              <li>Profit factor improves</li>
              <li>Max drawdown does not worsen materially</li>
              <li>Robustness holds across all four regimes</li>
            </ul>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Performance Analytics Panel ─────────────────────────────────────────────

function PerformancePanel({ theme }) {
  const m = window.innerWidth < 768
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  const fetchPerf = useCallback(async () => {
    setLoading(true)
    try {
      const res = await API.get('/api/performance')
      setData(res)
    } catch (e) {
      console.error('Failed to fetch performance:', e)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchPerf()
    const id = setInterval(fetchPerf, 30000)
    return () => clearInterval(id)
  }, [fetchPerf])

  const downloadCSV = async (endpoint, filename) => {
    try {
      const resp = await fetch(endpoint)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename; a.click()
      URL.revokeObjectURL(url)
    } catch(e) { alert('Download failed: ' + e.message) }
  }

  if (loading) return <Card><div style={{color:V('text-muted'), textAlign:'center', padding:40}}>Loading performance data...</div></Card>
  if (!data || !data.summary) return <Card><div style={{color:V('text-muted'), textAlign:'center', padding:40}}>No performance data available yet. Complete some trades first.</div></Card>

  const s = data.summary
  const ec = data.equity_curve || []
  const daily = data.daily_breakdown || []
  const drift = data.drift_alerts || []
  const exitReasons = data.exit_reasons || {}

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {drift.length > 0 && (
        <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
          {drift.map((a, i) => (
            <div key={i} style={{
              background: a.severity === 'CRITICAL' ? V('red-bg') : V('yellow-bg'),
              border: `1px solid color-mix(in srgb, ${a.severity === 'CRITICAL' ? V('red') : V('yellow')} 30%, transparent)`,
              borderRadius:V('radius-md'), padding:'12px 16px', display:'flex', alignItems:'center', gap:10
            }}>
              <AlertCircle size={14} style={{ color: a.severity === 'CRITICAL' ? V('red') : V('yellow'), flexShrink:0 }} />
              <span style={{ color: a.severity === 'CRITICAL' ? V('red') : V('yellow'), fontSize:12, fontWeight:600 }}>{a.message}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
        <CapitalProtectionCard />
        <SystemHealthCard />
      </div>

      <div style={{ fontSize:12, color:V('text-muted') }}>
        {s.source === 'local'
          ? "Dhan isn't connected — showing the app's own trade journal instead of broker trades."
          : `Real Dhan trades closed in the last ${s.period_days || 30} days · all instruments${s.instruments?.length ? ` (${s.instruments.join(', ')})` : ''} · gross P&L, before charges · dated by exit.`}
      </div>

      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Total Trades" value={s.total_trades} sub={`W:${s.wins} | L:${s.losses}`} />
        <MetricBox label="Win Rate" value={`${s.win_rate}%`} color={s.win_rate >= 50 ? V('green') : V('yellow')} />
        <MetricBox label="Profit Factor" value={s.profit_factor} color={s.profit_factor >= 1.0 ? V('green') : V('red')} />
        <MetricBox label="Gross P&L" value={fmtPnl(s.net_pnl)} color={clr(s.net_pnl)} sub="before charges" />
      </div>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Avg Win" value={fmtPnl(s.avg_win)} color={V('green')} />
        <MetricBox label="Avg Loss" value={fmtPnl(s.avg_loss)} color={V('red')} />
        {s.total_trades >= 20
          ? <MetricBox label="Sharpe Ratio" value={s.sharpe_ratio} color={s.sharpe_ratio >= 1 ? V('green') : s.sharpe_ratio >= 0 ? V('yellow') : V('red')} />
          : <MetricBox label="Sharpe Ratio" value="—" color={V('text-muted')} sub={`needs 20+ trades (${s.total_trades} so far)`} />}
        <MetricBox label="Max Drawdown" value={fmtPnl(-s.max_drawdown)} color={V('red')} sub={`${s.max_drawdown_pct}% of capital`} />
      </div>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Expectancy" value={fmtPnl(s.expectancy)} color={clr(s.expectancy)} sub="per trade" />
        <MetricBox label="Consec Wins" value={s.max_consec_wins} color={V('green')} />
        <MetricBox label="Consec Losses" value={s.max_consec_losses} color={V('red')} />
        <MetricBox label="Current Equity" value={`₹${fmt(s.current_equity)}`} color={clr(s.current_equity - s.starting_capital)} />
      </div>

      {ec.length > 0 && (
        <Card>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
            <div>
              <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Cumulative P&L</div>
              <div style={{ color:V('text-muted'), fontSize:11 }}>Gross, trade by trade, in order of exit — not account equity</div>
            </div>
            <StyledButton onClick={fetchPerf} variant="primary" style={{ padding:'4px 12px', fontSize:11 }}>
              <RefreshCw size={11}/> Refresh
            </StyledButton>
          </div>
          <div style={{ height:280 }}>
            {(() => {
              const colors = theme === 'dark' ? {
                border: '#1e2235',
                text: '#636882',
                bg: '#111525',
                textPrimary: '#e2e5f0'
              } : {
                border: '#e2e5ed',
                text: '#8b90a0',
                bg: '#ffffff',
                textPrimary: '#1a1d26'
              }
              return (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={ec}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#4f6ef7" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#4f6ef7" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.border} />
                    <XAxis dataKey="trade_num" tick={{fill:colors.text, fontSize:10}} stroke={colors.border} label={{value:'Trade #', fill:colors.text, fontSize:10, position:'insideBottom', offset:-5}} />
                    <YAxis tick={{fill:colors.text, fontSize:10}} stroke={colors.border} tickFormatter={v => `₹${(v/1000).toFixed(0)}k`} />
                    <ReTooltip
                      contentStyle={{ background:colors.bg, border:`1px solid ${colors.border}`, borderRadius:8, fontSize:11 }}
                      labelStyle={{ color:colors.textPrimary }}
                      formatter={(v) => [fmtPnl(v), 'Cumulative P&L']}
                      labelFormatter={(l, payload) => {
                        const p = payload && payload[0] && payload[0].payload
                        return p ? `Trade #${l} · exit ${p.date} ${p.time || ''} · ${fmtPnl(p.pnl)} · ${p.symbol || ''}` : `Trade #${l}`
                      }}
                    />
                    <Area type="monotone" dataKey="cumulative_pnl" stroke="#4f6ef7" fill="url(#eqGrad)" strokeWidth={2} dot={true} />
                  </AreaChart>
                </ResponsiveContainer>
              )
            })()}
          </div>
        </Card>
      )}

      {Object.keys(exitReasons).length > 0 && (
        <Card>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15, marginBottom:12 }}>Exit Reason Breakdown</div>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr style={{ borderBottom:`1px solid ${V('border')}` }}>
                  {['Exit Reason', 'Count', 'Win Rate', 'Total PnL'].map(h => (
                    <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), fontWeight:600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(exitReasons).map(([reason, stats]) => (
                  <tr key={reason} style={{ borderBottom:`1px solid ${V('border-light')}` }}>
                    <td style={{ padding:'8px 10px', color:V('text-primary'), fontWeight:600 }}>{reason}</td>
                    <td style={{ padding:'8px 10px', color:V('text-muted') }}>{stats.count}</td>
                    <td style={{ padding:'8px 10px', color: stats.win_rate >= 50 ? V('green') : V('yellow') }}>{stats.win_rate}%</td>
                    <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color:clr(stats.total_pnl), fontWeight:700 }}>{fmtPnl(stats.total_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {daily.length > 0 && (
        <Card>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15, marginBottom:12 }}>Daily Breakdown</div>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr style={{ borderBottom:`1px solid ${V('border')}` }}>
                  {['Date', 'Trades', 'Wins', 'Losses', 'Win Rate', 'PnL'].map(h => (
                    <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), fontWeight:600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {daily.slice().reverse().map((d, i) => (
                  <tr key={i} style={{ borderBottom:`1px solid ${V('border-light')}`, background: i%2===0 ? 'transparent' : V('bg-tertiary') }}>
                    <td style={{ padding:'8px 10px', color:V('text-muted') }}>{d.date}</td>
                    <td style={{ padding:'8px 10px', color:V('text-primary') }}>{d.trades}</td>
                    <td style={{ padding:'8px 10px', color:V('green') }}>{d.wins}</td>
                    <td style={{ padding:'8px 10px', color:V('red') }}>{d.losses}</td>
                    <td style={{ padding:'8px 10px', color: d.win_rate >= 50 ? V('green') : V('yellow') }}>{d.win_rate}%</td>
                    <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color:clr(d.pnl), fontWeight:700 }}>{fmtPnl(d.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div style={{ display:'flex', gap:8 }}>
        <StyledButton onClick={() => downloadCSV('/api/download/performance-log', 'performance_log.csv')} variant="primary" style={{ flex:1, padding:'12px 16px' }}>
          <BarChart2 size={14}/> Download Performance Log
        </StyledButton>
        <StyledButton onClick={() => downloadCSV('/api/download/slippage-log', 'slippage_log.csv')} variant="purple" style={{ flex:1, padding:'12px 16px' }}>
          <TrendingDown size={14}/> Download Slippage Log
        </StyledButton>
      </div>
    </div>
  )
}

// ── Dashboard Page ──────────────────────────────────────────────────────────
// Capital-drawdown flag: while it's on, the backend refuses every auto-trade entry
// (capital_tracker.can_trade). It used to be invisible in the UI -- it sat stuck on
// from 2026-06-25 with 0% drawdown. Shown on Dashboard and Auto Trade with a reset.
function CapitalBreachNotice({ capitalState }) {
  const [busy, setBusy] = useState(false)
  const [hidden, setHidden] = useState(false)
  if (!capitalState?.drawdown_breached || hidden) return null
  const reset = async () => {
    if (!window.confirm('Reset the capital drawdown flag?\n\nAuto-trade entries are refused while it is on. ' +
      `Current drawdown: ${(capitalState.current_drawdown_pct ?? 0).toFixed(1)}% (limit ${capitalState.max_drawdown_pct}%).`)) return
    setBusy(true)
    try {
      const r = await API.post('/api/capital/reset-breach')
      if (r && r.success) setHidden(true)
      else alert('Reset failed')
    } catch (e) { alert('Reset failed: ' + e.message) }
    finally { setBusy(false) }
  }
  return (
    <div style={{ background:V('red-bg'), border:`1px solid color-mix(in srgb, ${V('red')} 35%, transparent)`,
                  borderRadius:V('radius'), padding:'10px 14px', display:'flex', alignItems:'center', gap:12, flexWrap:'wrap' }}>
      <div style={{ flex:1, minWidth:240, color:V('red'), fontSize:12, fontWeight:600, lineHeight:1.5 }}>
        ⛔ Auto-trade entries are BLOCKED — the capital drawdown flag is on (since {capitalState.last_updated}).
        Current drawdown {(capitalState.current_drawdown_pct ?? 0).toFixed(1)}% of a {capitalState.max_drawdown_pct}% limit.
        Every new auto-trade entry is refused until the flag is reset.
      </div>
      <StyledButton onClick={reset} variant="danger" disabled={busy} style={{ padding:'6px 14px', fontSize:12 }}>
        {busy ? 'Resetting…' : 'Reset flag'}
      </StyledButton>
    </div>
  )
}

function DashboardPage({ connected, appRunning, autoTrade, toggleAutoTrade, balance, livePnl, todayPnl, lotSize, tradeState, signal, strategy, instrument, ltp, capitalState, dataHealth, toggleAppRunning, allSignals, lastEntries, telegramConfigured, refreshSettings, maxDailyLoss, maxDailyProfit, manualPositions }) {
  const m = window.innerWidth < 768
  const d = tradeState?.day_stats
  // Backend always keeps day_stats.gross_pnl in sync with today_pnl, so the
  // fallback used to read d?.gross_pnl when todayPnl was 0 -- but "0" is a
  // legitimate value (no P&L today), not "not loaded yet", and that fallback
  // could surface a stale/unfiltered day_stats snapshot instead. Use
  // todayPnl directly.
  const displayPnl = todayPnl

  const strategyLabels = { ...LIVE_STRATEGY_LABELS, multi_agent: 'Multi-Agent Optimized', regime_trend_range: 'Regime T/R Optimized', regime_trend_v2: 'Regime Trend V2', regime_trend_v2b: 'Regime Trend V2-B', donchian_5m_swing: 'Donchian 5m Swing', donchian_5m_intraday: 'Donchian 5m Intraday' }
  const [sparkData, setSparkData] = React.useState({ closes: [], pct_change: 0.0 })
  const [logs, setLogs] = React.useState([])
  const [isClosing, setIsClosing] = React.useState(false)
  const [editingLimit, setEditingLimit] = React.useState(false)
  const [tempLimit, setTempLimit] = React.useState(maxDailyLoss || 1000.0)

  React.useEffect(() => {
    if (maxDailyLoss != null) {
      setTempLimit(maxDailyLoss)
    }
  }, [maxDailyLoss])

  const saveLimit = async () => {
    const val = parseFloat(tempLimit)
    if (isNaN(val) || val <= 0) {
      alert("Please enter a valid positive limit value.")
      return
    }
    await API.post('/api/settings', { max_daily_loss: val })
    setEditingLimit(false)
    refreshSettings()
  }

  // Fetch Sparkline Index data
  React.useEffect(() => {
    const fetchSpark = async () => {
      const data = await API.get('/api/sparkline').catch(() => null)
      if (data) setSparkData(data)
    }
    fetchSpark()
    const id = setInterval(fetchSpark, 30000)
    return () => clearInterval(id)
  }, [instrument])

  // Fetch Activity Logs
  React.useEffect(() => {
    const fetchLogs = async () => {
      const data = await API.get('/api/activity_logs').catch(() => null)
      if (data && data.logs) setLogs(data.logs)
    }
    fetchLogs()
    const id = setInterval(fetchLogs, 3000)
    return () => clearInterval(id)
  }, [])

  // Emergency exit handler
  const handlePanicExit = async () => {
    if (!window.confirm("CRITICAL WARNING: Force-close the active position at the market price?\n\nThis will send an immediate market order to exit all lots.")) return
    setIsClosing(true)
    try {
      const res = await API.post('/api/trade/manual', { action: 'EXIT' })
      if (res && res.success) {
        alert("Exit order placed successfully.")
      } else {
        alert(`Exit failed: ${res?.error || 'Unknown error'}`)
      }
    } catch (err) {
      alert(`Network error during exit: ${err.message}`)
    } finally {
      setIsClosing(false)
      refreshSettings()
    }
  }

  // Toggle Auto-trade state -- same confirmation as the Live / Auto Trade pages
  // (this button used to switch real auto-trading on with no confirmation).
  const handleToggleAutoTrade = async () => {
    await toggleAutoTrade()
    refreshSettings()
  }

  // Handle setting updates
  const handleInstrumentChange = async (val) => {
    await API.post('/api/settings', { instrument: val })
    refreshSettings()
  }

  const handleStrategyChange = async (val) => {
    await API.post('/api/settings', { strategy: val })
    refreshSettings()
  }

  // Render SVG Sparkline
  const renderSparkline = () => {
    const closes = sparkData.closes || []
    if (closes.length < 2) {
      return (
        <div style={{ fontSize:11, color:V('text-muted'), fontStyle:'italic', marginLeft:'auto' }}>
          No trend data available
        </div>
      )
    }
    const minVal = Math.min(...closes)
    const maxVal = Math.max(...closes)
    const range = maxVal - minVal
    const points = closes.map((val, idx) => {
      const x = (idx / (closes.length - 1)) * 120
      const y = range > 0 ? 30 - ((val - minVal) / range) * 22 : 15
      return `${x},${y}`
    }).join(' ')
    const isUp = sparkData.pct_change >= 0
    return (
      <div style={{ display:'flex', alignItems:'center', gap:10, marginLeft:'auto' }}>
        <div style={{ textAlign:'right' }}>
          <div style={{ fontSize:12, fontWeight:700, color: isUp ? V('green') : V('red') }}>
            {isUp ? '▲' : '▼'} {Math.abs(sparkData.pct_change).toFixed(2)}%
          </div>
          <div style={{ fontSize:9, color:V('text-muted') }}>20D Trend</div>
        </div>
        <svg width="120" height="35" style={{ overflow:'visible' }}>
          <polyline
            fill="none"
            stroke={isUp ? V('green') : V('red')}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            points={points}
          />
        </svg>
      </div>
    )
  }

  const renderSignalCard = (sig, label) => (
    <Card>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:500, marginBottom:8 }}>{label}</div>
      {sig ? (
        <div>
          <div style={{ fontSize:22, fontWeight:800, color: sig.signal==='LONG'||sig.signal==='LONG_EXIT' ? V('green') : sig.signal==='SHORT'||sig.signal==='SHORT_EXIT' ? V('red') : V('yellow') }}>
            {sig.signal==='LONG' ? '📈' : sig.signal==='SHORT' ? '📉' : sig.signal?.includes('EXIT') ? '🚪' : '⏸'} {sig.signal}
          </div>
          <div style={{ color:V('text-muted'), fontSize:11, marginTop:4 }}>
            {sig.time ? toISTSignalTime(sig.time) : ''}
          </div>
          <div style={{ display:'flex', gap:6, marginTop:8, flexWrap:'wrap' }}>
            {sig.ml_prob > 0 && <Badge label={`ML: ${(sig.ml_prob*100).toFixed(0)}%`} color={sig.ml_prob > 0.6 ? V('green') : V('yellow')} />}
            {sig.weighted_score > 0 && <Badge label={`Score: ${(sig.weighted_score*100).toFixed(0)}%`} color={V('accent')} />}
            {sig.regime && <Badge label={sig.regime.replace('TRENDING_','T_')} color={sig.regime?.startsWith('TRENDING_UP')?V('green'):sig.regime?.startsWith('TRENDING_DOWN')?V('red'):V('yellow')} />}
          </div>
        </div>
      ) : (
        <div style={{ color:V('text-muted'), fontSize:13 }}>No signal yet</div>
      )}
    </Card>
  )

  const maSignal = allSignals?.custom_option_b_ram_rf || (strategy === 'custom_option_b_ram_rf' ? signal : null)
  const rtrSignal = allSignals?.custom_regime_v1_trend_range_final || (strategy === 'custom_regime_v1_trend_range_final' ? signal : null)
  const alphaComboSignal = allSignals?.custom_alpha_combo_cusum125 || (strategy === 'custom_alpha_combo_cusum125' ? signal : null)
  const timeGatedSignal = allSignals?.custom_time_gated_alpha_combo || (strategy === 'custom_time_gated_alpha_combo' ? signal : null)

  // Risk Budget Horizontal Progress Calculation
  const lossLimit = maxDailyLoss || 1000.0
  const profitLimit = maxDailyProfit || 3000.0
  const displayPnlVal = displayPnl || 0.0
  
  let dotPosPct = 50
  if (displayPnlVal > 0) {
    const normPnl = Math.min(profitLimit, displayPnlVal)
    dotPosPct = 50 + (normPnl / profitLimit) * 50
  } else if (displayPnlVal < 0) {
    const normPnl = Math.max(-lossLimit, displayPnlVal)
    dotPosPct = 50 + (normPnl / lossLimit) * 50
  }

  return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <CapitalBreachNotice capitalState={capitalState} />
      {/* Welcome header — title only */}
      <div style={{
        background:`linear-gradient(135deg, ${V('accent')} 0%, ${V('purple')} 100%)`,
        borderRadius:V('radius-lg'), padding:'18px 24px', color:'#fff',
      }}>
        <h2 style={{ margin:0, fontSize:19, fontWeight:700 }}>Autonomous Algo Trading Platform</h2>
      </div>

      {/* Top Config Row: Instrument, Strategy, Daily Risk Budget */}
      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1.2fr 1fr 1.2fr', gap:10 }}>
        {/* Instrument Selector + Sparkline */}
        <Card style={{ padding:14, display:'flex', alignItems:'center', gap:12 }}>
          <div>
            <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500, textTransform:'uppercase' }}>Instrument</div>
            <select
              value={instrument}
              onChange={e => handleInstrumentChange(e.target.value)}
              style={{
                background:'transparent', color:V('text-primary'), border:'none', fontSize:17, fontWeight:800, marginTop:4, outline:'none', cursor:'pointer', padding:0
              }}
            >
              <option value="BANKNIFTY" style={{background:V('bg-primary')}}>BANKNIFTY</option>
              <option value="NIFTY" style={{background:V('bg-primary')}}>NIFTY</option>
              <option value="FINNIFTY" style={{background:V('bg-primary')}}>FINNIFTY</option>
              <option value="MIDCPNIFTY" style={{background:V('bg-primary')}}>MIDCPNIFTY</option>
              <option value="CRUDEOIL" style={{background:V('bg-primary')}}>CRUDEOIL</option>
            </select>
          </div>
          {renderSparkline()}
        </Card>

        {/* Strategy Selector */}
        <Card style={{ padding:14 }}>
          <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500, textTransform:'uppercase' }}>Strategy</div>
          <select
            value={strategy}
            onChange={e => handleStrategyChange(e.target.value)}
            style={{
              background:'transparent', color:V('text-primary'), border:'none', fontSize:17, fontWeight:800, marginTop:4, outline:'none', cursor:'pointer', padding:0, width:'100%'
            }}
          >
            {LIVE_STRATEGY_OPTIONS.map(o => (
              <option key={o.v} value={o.v} style={{background:V('bg-primary')}}>{o.l}</option>
            ))}
          </select>
        </Card>

        {/* Bidirectional Risk Budget Panel */}
        <Card style={{ padding:14, display:'flex', flexDirection:'column', justifyContent:'center' }}>
          <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:V('text-muted'), fontWeight:500, textTransform:'uppercase', marginBottom:6 }}>
            <span>Daily Risk Control</span>
            <span style={{ color: displayPnl >= 0 ? V('green') : V('red'), fontWeight:700 }}>
              {displayPnl >= 0 ? '+' : ''}₹{displayPnl?.toFixed(2) || '0.00'}
            </span>
          </div>
          
          {/* Bidirectional progress bar container */}
          <div style={{ position:'relative', height:8, background:V('border'), borderRadius:4, margin:'4px 0 8px 0' }}>
            {/* Center zero line mark */}
            <div style={{ position:'absolute', left:'50%', top:0, bottom:0, width:2, background:V('text-muted'), zIndex:2 }} />
            
            {/* Colored fill bar */}
            {displayPnl > 0 ? (
              <div style={{ position:'absolute', left:'50%', right:`${100 - dotPosPct}%`, top:0, bottom:0, background:V('green'), borderRadius:'0 4px 4px 0' }} />
            ) : displayPnl < 0 ? (
              <div style={{ position:'absolute', left:`${dotPosPct}%`, right:'50%', top:0, bottom:0, background:V('red'), borderRadius:'4px 0 0 4px' }} />
            ) : null}

            {/* Pointer pin */}
            <div style={{
              position:'absolute', left:`${dotPosPct}%`, top:'50%', transform:'translate(-50%, -50%)',
              width:12, height:12, borderRadius:'50%', background: displayPnl >= 0 ? V('green') : V('red'),
              border:`2px solid ${V('bg-primary')}`, boxShadow:'0 1px 3px rgba(0,0,0,0.3)', zIndex:3
            }} />
          </div>

          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:9, color:V('text-muted') }}>
            {editingLimit ? (
              <div style={{ display:'flex', alignItems:'center', gap:4 }}>
                <span style={{ fontSize:9 }}>Limit:</span>
                <input
                  type="number"
                  value={tempLimit}
                  onChange={e => setTempLimit(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') saveLimit()
                    if (e.key === 'Escape') setEditingLimit(false)
                  }}
                  style={{
                    background: V('bg-input'),
                    color: V('text-primary'),
                    border: `1px solid ${V('border')}`,
                    borderRadius: V('radius-sm'),
                    padding: '2px 4px',
                    fontSize: 10,
                    width: '60px',
                    outline: 'none'
                  }}
                  autoFocus
                />
                <button onClick={saveLimit} style={{ background:'none', border:'none', color:V('green'), cursor:'pointer', fontSize:11, padding:0, fontWeight:'bold' }}>✓</button>
                <button onClick={() => setEditingLimit(false)} style={{ background:'none', border:'none', color:V('red'), cursor:'pointer', fontSize:11, padding:0, fontWeight:'bold' }}>✗</button>
              </div>
            ) : (
              <div 
                onClick={() => setEditingLimit(true)}
                style={{ cursor:'pointer', display:'flex', alignItems:'center', gap:4 }}
                title="Click to edit Daily Risk Limit"
              >
                <span>Max Loss: -₹{lossLimit.toFixed(0)}</span>
                <span style={{ fontSize:8, opacity:0.7 }}>✏️</span>
              </div>
            )}
            <span>Target: +₹{(maxDailyProfit || 3000.0).toFixed(0)}</span>
          </div>
        </Card>
      </div>

      {/* Status indicators — 4 columns: Broker, Engine (clickable), Telegram, Auto Trade (clickable toggle) */}
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
        <Card style={{ display:'flex', alignItems:'center', gap:14, padding:12 }}>
          <div style={{ width:36, height:36, borderRadius:8, background: connected ? V('green-bg') : V('red-bg'), display:'flex', alignItems:'center', justifyContent:'center' }}>
            {connected ? <Wifi size={16} style={{color:V('green')}}/> : <WifiOff size={16} style={{color:V('red')}}/>}
          </div>
          <div>
            <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500 }}>Broker</div>
            <div style={{ fontSize:13, fontWeight:700, color: connected ? V('green') : V('red') }}>{connected ? 'Connected' : 'Disconnected'}</div>
          </div>
        </Card>

        {/* Engine Start/Stop */}
        <Card
          onClick={toggleAppRunning}
          style={{ display:'flex', alignItems:'center', gap:14, padding:12, cursor:'pointer', transition:'box-shadow 0.15s' }}
          onMouseEnter={e => e.currentTarget.style.boxShadow = `0 0 0 2px ${appRunning ? 'var(--red)' : 'var(--green)'}40`}
          onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
        >
          <div style={{ width:36, height:36, borderRadius:8, background: appRunning ? V('green-bg') : V('red-bg'), display:'flex', alignItems:'center', justifyContent:'center' }}>
            {appRunning ? <Play size={16} style={{color:V('green')}}/> : <Square size={16} style={{color:V('red')}}/>}
          </div>
          <div>
            <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500 }}>Engine</div>
            <div style={{ fontSize:13, fontWeight:700, color: appRunning ? V('green') : V('red') }}>{appRunning ? '● Running' : '■ Stopped'}</div>
            <div style={{ fontSize:9, color:V('text-muted') }}>Click to {appRunning ? 'stop' : 'start'}</div>
          </div>
        </Card>

        {/* Telegram status */}
        <Card style={{ display:'flex', alignItems:'center', gap:14, padding:12 }}>
          <div style={{ width:36, height:36, borderRadius:8, background: telegramConfigured ? V('cyan-bg') : V('bg-tertiary'), display:'flex', alignItems:'center', justifyContent:'center' }}>
            <Send size={16} style={{color: telegramConfigured ? V('cyan') : V('text-muted')}}/>
          </div>
          <div>
            <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500 }}>Telegram</div>
            <div style={{ fontSize:13, fontWeight:700, color: telegramConfigured ? V('cyan') : V('text-muted') }}>{telegramConfigured ? 'Connected' : 'Not Set'}</div>
          </div>
        </Card>

        {/* Auto Trade Toggle */}
        <Card 
          onClick={handleToggleAutoTrade}
          style={{ display:'flex', alignItems:'center', gap:14, padding:12, cursor:'pointer', transition:'box-shadow 0.15s' }}
          onMouseEnter={e => e.currentTarget.style.boxShadow = `0 0 0 2px ${autoTrade ? 'var(--red)' : 'var(--green)'}40`}
          onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
        >
          <div style={{ width:36, height:36, borderRadius:8, background: autoTrade ? V('green-bg') : V('bg-tertiary'), display:'flex', alignItems:'center', justifyContent:'center' }}>
            <Zap size={16} style={{color: autoTrade ? V('green') : V('text-muted')}}/>
          </div>
          <div>
            <div style={{ fontSize:10, color:V('text-muted'), fontWeight:500 }}>Auto Trade</div>
            <div style={{ fontSize:13, fontWeight:700, color: autoTrade ? V('green') : V('text-muted') }}>{autoTrade ? 'Active' : 'Inactive'}</div>
            <div style={{ fontSize:9, color:V('text-muted') }}>Click to {autoTrade ? 'deactivate' : 'activate'}</div>
          </div>
        </Card>
      </div>

      {/* Key metrics */}
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
        <MetricBox label="Balance" value={`₹${fmt(balance)}`} color={V('accent')} />
        <MetricBox label="Position P&L" value={fmtPnl(livePnl)} color={clr(livePnl)} sub={manualPnlLine(manualPositions)} />
        <MetricBox label="Today's P&L" value={fmtPnl(displayPnl)} color={clr(displayPnl)} sub={`${d?.total_trades||0} trades`} />
        <MetricBox label="Win / Loss" value={`${d?.wins||0} / ${d?.losses||0}`} color={V('text-primary')} sub={lotSize ? `Lot: ${lotSize}` : (d?.total_trades > 0 ? `${((d?.wins/d?.total_trades)*100).toFixed(0)}% WR` : '—')} />
      </div>

      {/* Four live-strategy signal tiles, responsive wrap */}
      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : 'repeat(auto-fit, minmax(220px, 1fr))', gap:10, marginBottom:10 }}>
        {renderSignalCard(alphaComboSignal, 'Alpha Combo Signal')}
        {renderSignalCard(timeGatedSignal, 'Time-Gated Alpha Combo Signal')}
        {renderSignalCard(maSignal, 'Option B (Ram > RF) Signal')}
        {renderSignalCard(rtrSignal, 'Regime T/R V1 Final Signal')}
      </div>

      {/* Active Position */}
      <div style={{ display:'grid', gridTemplateColumns: '1fr', gap:10 }}>
        {/* Position card with Panic Exit Button */}
        <Card style={{ display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
          <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:500, marginBottom:8 }}>Active Position</div>
          {tradeState?.position ? (() => {
            const p = tradeState.position
            return (
              <div style={{ flexGrow:1, display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
                <div>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                    <div style={{ fontSize:19, fontWeight:800, color: p.direction==='LONG' ? V('green') : V('red') }}>
                      {p.direction==='LONG' ? '📈' : '📉'} {p.direction}
                    </div>
                    <div style={{ fontFamily:"'JetBrains Mono', monospace", fontWeight:700, fontSize:18, color:clr(p.current_pnl) }}>{fmtPnl(p.current_pnl)}</div>
                  </div>
                  <div style={{ color:V('text-muted'), fontSize:11, marginTop:4 }}>{p.symbol} • LTP: {fmt(ltp)}</div>
                  <div style={{ display:'flex', gap:6, marginTop:8, flexWrap:'wrap' }}>
                    <Badge label={`Entry: ${fmt(p.entry_price)}`} color={V('accent')} />
                    <Badge label={`SL: ${fmt(p.sl)}`} color={V('red')} />
                    {p.t1_hit && <Badge label="T1 Hit" color={V('green')} />}
                  </div>
                </div>

                <StyledButton 
                  onClick={handlePanicExit} 
                  disabled={isClosing}
                  variant="danger" 
                  style={{ width:'100%', padding:'8px 10px', fontSize:11, fontWeight:700, marginTop:12, display:'flex', alignItems:'center', justifyContent:'center', gap:6 }}
                >
                  {isClosing ? 'Closing Position...' : '⚠️ EMERGENCY PANIC EXIT'}
                </StyledButton>
              </div>
            )
          })() : (
            <div style={{ color:V('text-muted'), fontSize:13, display:'flex', alignItems:'center', height:'100%', paddingTop:20 }}>No open position</div>
          )}
        </Card>
      </div>

      {/* Live Engine Console widget */}
      <Card style={{ padding:14 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <Cpu size={14} style={{ color:V('accent') }} />
            <span style={{ fontSize:11, color:V('text-primary'), fontWeight:700, textTransform:'uppercase' }}>Live Engine Console Feed</span>
          </div>
          <span style={{ fontSize:9, color:V('green'), fontWeight:600 }}>● Ticking</span>
        </div>
        <div style={{
          background:'#080a10', border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'),
          padding:'8px 12px', height:105, overflowY:'auto', fontFamily:"'JetBrains Mono', monospace", fontSize:10, color:'#38bdf8',
          display:'flex', flexDirection:'column-reverse', gap:4
        }}>
          {logs.length === 0 ? (
            <div style={{ color:V('text-muted') }}>Initializing socket feed...</div>
          ) : (
            [...logs].reverse().map((lg, idx) => (
              <div key={idx} style={{ color: lg.includes('failed') || lg.includes('Breached') ? '#f87171' : lg.includes('Signal') || lg.includes('Restored') ? '#4ade80' : '#38bdf8' }}>{lg}</div>
            ))
          )}
        </div>
      </Card>

      {/* Data health warning */}
      {dataHealth?.is_stale && (
        <div style={{
          background:V('yellow-bg'), border:`1px solid color-mix(in srgb, ${V('yellow')} 30%, transparent)`,
          borderRadius:V('radius-md'), padding:'10px 14px', display:'flex', alignItems:'center', gap:10
        }}>
          <AlertCircle size={15} style={{color:V('yellow'), flexShrink:0}} />
          <span style={{ color:V('yellow'), fontSize:11, fontWeight:600 }}>
            Data Stale: Last candle {Math.round((dataHealth.staleness_seconds||0)/60)} min ago. Signal generation paused.
          </span>
        </div>
      )}
    </div>
  )
}

// ── Auto Trade Monitor Page ─────────────────────────────────────────────────
function AutoTradeMonitorPage({ autoTrade, toggleAutoTrade, journal, allSignals, capitalState, tradeState, strategy, signal }) {
  const m = window.innerWidth < 768
  const strategyLabels = { ...LIVE_STRATEGY_LABELS, multi_agent: 'Multi-Agent Optimized', regime_trend_range: 'Regime T/R Optimized', regime_trend_v2: 'Regime Trend V2', regime_trend_v2b: 'Regime Trend V2-B', donchian_5m_swing: 'Donchian 5m Swing', donchian_5m_intraday: 'Donchian 5m Intraday' }

  // ── Inactive state ──
  if (!autoTrade) {
    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', minHeight:'60vh', gap:20 }}>
        <div style={{ width:'100%' }}><CapitalBreachNotice capitalState={capitalState} /></div>
        <div style={{ width:80, height:80, borderRadius:'50%', background:V('red-bg'), display:'flex', alignItems:'center', justifyContent:'center' }}>
          <Zap size={36} style={{ color:V('red') }} />
        </div>
        <h2 style={{ margin:0, fontSize:22, fontWeight:700, color:V('text-primary') }}>Auto Trade is Inactive</h2>
        <p style={{ margin:0, fontSize:14, color:V('text-muted'), maxWidth:400, textAlign:'center' }}>
          Auto trading is currently disabled. Activate it to see live trade timeline, capital utilization, equity curve, and confidence metrics.
        </p>
        <StyledButton onClick={toggleAutoTrade} variant="primary" style={{ padding:'10px 28px', fontSize:14, fontWeight:700 }}>
          <Zap size={16} /> Activate Auto Trade
        </StyledButton>
      </div>
    )
  }

  // ── Compute confidence score ──
  // NOTE: maSignal now comes from Option B (custom_option_b_ram_rf, replaced
  // HalfTrend+Hull here 2026-10-01), which has no regime classifier -- its
  // weighted_score/regime/regime_confidence are always 0/unset, so this
  // score's "Weighted score" and "Regime clarity" components will read 0 for
  // it. Agreement scoring (direction only) still works normally.
  const maSignal = allSignals?.custom_option_b_ram_rf
  const rtrSignal = allSignals?.custom_regime_v1_trend_range_final
  const activeSignal = signal || maSignal || rtrSignal

  // Strategy agreement: do both strategies agree on direction?
  let agreementScore = 0
  let agreementLabel = 'No Data'
  if (maSignal?.signal && rtrSignal?.signal) {
    const maDir = maSignal.signal === 'LONG' || maSignal.signal === 'LONG_EXIT' ? 'BULL' : maSignal.signal === 'SHORT' || maSignal.signal === 'SHORT_EXIT' ? 'BEAR' : 'NEUTRAL'
    const rtrDir = rtrSignal.signal === 'LONG' || rtrSignal.signal === 'LONG_EXIT' ? 'BULL' : rtrSignal.signal === 'SHORT' || rtrSignal.signal === 'SHORT_EXIT' ? 'BEAR' : 'NEUTRAL'
    if (maDir === rtrDir && maDir !== 'NEUTRAL') { agreementScore = 40; agreementLabel = 'Aligned' }
    else if (maDir === 'NEUTRAL' || rtrDir === 'NEUTRAL') { agreementScore = 20; agreementLabel = 'Partial' }
    else { agreementScore = 0; agreementLabel = 'Conflicting' }
  } else if (maSignal?.signal || rtrSignal?.signal) {
    agreementScore = 15; agreementLabel = 'Single Strategy'
  }

  // Weighted score contribution (0-30 points)
  const ws = activeSignal?.weighted_score || 0
  const weightedScorePoints = Math.min(30, Math.round(ws * 30))

  // Regime clarity (0-15 points)
  const regime = rtrSignal?.regime || activeSignal?.regime
  const regimeConf = rtrSignal?.regime_confidence || activeSignal?.regime_confidence || 0
  let regimePoints = 0
  if (regime && regime !== 'TRANSITION' && regime !== 'SIDEWAYS') {
    regimePoints = Math.min(15, Math.round(regimeConf * 15))
  }

  // Win rate contribution (0-15 points)
  const closedTrades = (journal || []).filter(t => t.status === 'CLOSED')
  const totalTrades = closedTrades.length
  const wins = closedTrades.filter(t => t.pnl > 0).length
  const winRate = totalTrades > 0 ? wins / totalTrades : 0
  const winRatePoints = totalTrades >= 3 ? Math.min(15, Math.round(winRate * 15)) : 0

  const confidenceScore = agreementScore + weightedScorePoints + regimePoints + winRatePoints
  const confidenceColor = confidenceScore >= 70 ? V('green') : confidenceScore >= 40 ? V('yellow') : V('red')
  const confidenceLabel = confidenceScore >= 70 ? 'High' : confidenceScore >= 40 ? 'Moderate' : 'Low'

  // ── Trade timeline data ──
  const todayTrades = closedTrades.filter(t => {
    if (!t.entry_date) return false
    const today = new Date().toISOString().slice(0, 10)
    return t.entry_date === today || t.entry_date?.includes(today.replace(/-/g, '/'))
  })

  // ── P&L curve data (trade-by-trade cumulative) ──
  let cumPnl = 0
  const pnlCurveData = [{ trade: 0, pnl: 0, label: 'Start' }]
  todayTrades.forEach((t, i) => {
    cumPnl += (t.pnl || 0)
    pnlCurveData.push({ trade: i + 1, pnl: Math.round(cumPnl * 100) / 100, label: `T${i + 1}: ${t.direction}` })
  })

  // ── Capital utilization ──
  const cap = capitalState
  const ddPct = cap ? (cap.is_profit ? 0 : Math.min(100, (cap.current_drawdown / cap.drawdown_limit) * 100)) : 0
  const d = tradeState?.day_stats

  // ── Trade heatmap (by hour) ──
  const heatmapData = Array.from({ length: 7 }, (_, i) => ({ hour: 9 + i, label: `${9 + i}:00`, wins: 0, losses: 0, pnl: 0 }))
  closedTrades.forEach(t => {
    if (!t.entry_time) return
    const hourMatch = t.entry_time.match(/(\d{1,2}):/)
    if (hourMatch) {
      const h = parseInt(hourMatch[1])
      const idx = h - 9
      if (idx >= 0 && idx < 7) {
        if (t.pnl > 0) heatmapData[idx].wins++
        else heatmapData[idx].losses++
        heatmapData[idx].pnl += (t.pnl || 0)
      }
    }
  })

  return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <CapitalBreachNotice capitalState={capitalState} />

      {/* Top row: Confidence Score + Strategy Agreement + Capital Utilization */}
      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:12 }}>

        {/* Confidence Score */}
        <Card style={{ padding:20, textAlign:'center' }}>
          <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Trade Confidence</div>
          <div style={{ position:'relative', width:100, height:100, margin:'0 auto' }}>
            <svg viewBox="0 0 36 36" style={{ width:100, height:100, transform:'rotate(-90deg)' }}>
              <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none" stroke={V('border')} strokeWidth="3" />
              <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none" stroke={confidenceColor} strokeWidth="3"
                strokeDasharray={`${confidenceScore}, 100`}
                strokeLinecap="round" />
            </svg>
            <div style={{ position:'absolute', top:'50%', left:'50%', transform:'translate(-50%,-50%)', textAlign:'center' }}>
              <div style={{ fontSize:24, fontWeight:800, color:confidenceColor, fontFamily:"'JetBrains Mono', monospace" }}>{confidenceScore}</div>
              <div style={{ fontSize:9, color:V('text-muted') }}>/100</div>
            </div>
          </div>
          <div style={{ fontSize:14, fontWeight:700, color:confidenceColor, marginTop:8 }}>{confidenceLabel} Confidence</div>
          <div style={{ display:'flex', flexDirection:'column', gap:4, marginTop:12, fontSize:11 }}>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <span style={{ color:V('text-muted') }}>Strategy Align</span>
              <span style={{ color:V('text-primary'), fontWeight:600 }}>{agreementScore}/40 ({agreementLabel})</span>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <span style={{ color:V('text-muted') }}>Score</span>
              <span style={{ color:V('text-primary'), fontWeight:600 }}>{weightedScorePoints}/30</span>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <span style={{ color:V('text-muted') }}>Regime Clarity</span>
              <span style={{ color:V('text-primary'), fontWeight:600 }}>{regimePoints}/15</span>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <span style={{ color:V('text-muted') }}>Win Rate</span>
              <span style={{ color:V('text-primary'), fontWeight:600 }}>{winRatePoints}/15{totalTrades < 3 ? ' (need 3+ trades)' : ''}</span>
            </div>
          </div>
        </Card>

        {/* Strategy Agreement */}
        <Card style={{ padding:20 }}>
          <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Strategy Agreement</div>
          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            {LIVE_STRATEGY_IDS.map(sid => {
              const s = allSignals?.[sid]
              const dir = s?.signal
              const dirColor = dir === 'LONG' || dir === 'LONG_EXIT' ? V('green') : dir === 'SHORT' || dir === 'SHORT_EXIT' ? V('red') : V('yellow')
              return (
                <div key={sid} style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                  <div style={{ fontSize:11, color:V('text-muted'), fontWeight:500, marginBottom:6 }}>{strategyLabels[sid]}</div>
                  <div style={{ fontSize:18, fontWeight:800, color:dirColor }}>
                    {dir === 'LONG' ? '📈' : dir === 'SHORT' ? '📉' : dir?.includes('EXIT') ? '🚪' : '⏸'} {dir || 'N/A'}
                  </div>
                  {s?.regime && <div style={{ fontSize:10, color:V('text-muted'), marginTop:4 }}>Regime: {s.regime.replace('TRENDING_', 'T_')}</div>}
                  {s?.weighted_score > 0 && <div style={{ fontSize:10, color:V('accent'), marginTop:2 }}>Score: {(s.weighted_score * 100).toFixed(0)}%</div>}
                </div>
              )
            })}
            <div style={{
              textAlign:'center', padding:'8px 12px', borderRadius:V('radius-sm'), fontWeight:700, fontSize:13,
              background: agreementLabel === 'Aligned' ? V('green-bg') : agreementLabel === 'Conflicting' ? V('red-bg') : V('yellow-bg'),
              color: agreementLabel === 'Aligned' ? V('green') : agreementLabel === 'Conflicting' ? V('red') : V('yellow'),
            }}>
              {agreementLabel === 'Aligned' ? '✓ Strategies Aligned' : agreementLabel === 'Conflicting' ? '✗ Strategies Conflicting' : '~ ' + agreementLabel}
            </div>
          </div>
        </Card>

        {/* Capital Utilization */}
        <Card style={{ padding:20 }}>
          <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Capital Utilization</div>
          {cap ? (
            <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
                <div>
                  <div style={{ fontSize:10, color:V('text-muted') }}>Current Equity</div>
                  <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>₹{fmt(cap.current_equity)}</div>
                </div>
                <div>
                  <div style={{ fontSize:10, color:V('text-muted') }}>Peak Equity</div>
                  <div style={{ fontSize:16, fontWeight:700, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>₹{fmt(cap.peak_equity)}</div>
                </div>
              </div>
              {cap.is_profit ? (
                <div style={{ background:V('green-bg'), borderRadius:V('radius-sm'), padding:10, textAlign:'center' }}>
                  <div style={{ fontSize:10, color:V('text-muted') }}>Today's Profit</div>
                  <div style={{ fontSize:20, fontWeight:800, color:V('green'), fontFamily:"'JetBrains Mono', monospace" }}>+₹{fmt(cap.today_pnl)}</div>
                </div>
              ) : (
                <div>
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, marginBottom:4 }}>
                    <span style={{ color:V('text-muted') }}>Drawdown</span>
                    <span style={{ color:V('red'), fontWeight:600 }}>₹{fmt(cap.current_drawdown)} ({cap.current_drawdown_pct?.toFixed(1)}%)</span>
                  </div>
                  <div style={{ height:10, background:V('bg-tertiary'), borderRadius:5, overflow:'hidden' }}>
                    <div style={{ height:'100%', width:`${ddPct}%`, background: ddPct > 70 ? '#ef4444' : ddPct > 40 ? '#f59e0b' : '#10b981', borderRadius:5, transition:'width 0.5s' }}/>
                  </div>
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:V('text-muted'), marginTop:4 }}>
                    <span>0%</span>
                    <span>Limit: ₹{fmt(cap.drawdown_limit)}</span>
                    <span>100%</span>
                  </div>
                </div>
              )}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
                <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:8, textAlign:'center' }}>
                  <div style={{ fontSize:10, color:V('text-muted') }}>Trades Today</div>
                  <div style={{ fontSize:18, fontWeight:700, color:V('text-primary') }}>{d?.total_trades || 0}</div>
                </div>
                <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:8, textAlign:'center' }}>
                  <div style={{ fontSize:10, color:V('text-muted') }}>Win Rate</div>
                  <div style={{ fontSize:18, fontWeight:700, color: winRate >= 0.5 ? V('green') : V('yellow') }}>{totalTrades > 0 ? `${(winRate * 100).toFixed(0)}%` : '—'}</div>
                </div>
              </div>
            </div>
          ) : (
            <div style={{ color:V('text-muted'), fontSize:13 }}>Loading capital data...</div>
          )}
        </Card>
      </div>

      {/* Trade Timeline */}
      <Card>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Today's Trade Timeline</div>
        {todayTrades.length === 0 ? (
          <div style={{ color:V('text-muted'), fontSize:13, textAlign:'center', padding:30 }}>No trades today yet. Trades will appear here as they are executed.</div>
        ) : (
          <div style={{ display:'flex', alignItems:'center', gap:0, overflowX:'auto', padding:'10px 0' }}>
            {todayTrades.map((t, i) => {
              const isWin = t.pnl > 0
              return (
                <React.Fragment key={i}>
                  {i > 0 && <div style={{ width:30, height:2, background:V('border'), flexShrink:0 }} />}
                  <div style={{
                    minWidth:120, padding:10, borderRadius:V('radius-md'), flexShrink:0,
                    background: isWin ? V('green-bg') : V('red-bg'),
                    border: `1px solid ${isWin ? V('green') : V('red')}30`,
                  }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 }}>
                      <Badge label={t.direction} color={t.direction === 'LONG' ? V('green') : V('red')} />
                      <span style={{ fontSize:10, color:V('text-muted') }}>T{i + 1}</span>
                    </div>
                    <div style={{ fontFamily:"'JetBrains Mono', monospace", fontWeight:700, fontSize:14, color: clr(t.pnl) }}>
                      {fmtPnl(t.pnl)}
                    </div>
                    <div style={{ fontSize:9, color:V('text-muted'), marginTop:2 }}>{t.entry_time || ''} • {t.exit_reason || ''}</div>
                  </div>
                </React.Fragment>
              )
            })}
          </div>
        )}
      </Card>

      {/* Rolling P&L Curve */}
      <Card>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Equity Curve (Trade-by-Trade)</div>
        {pnlCurveData.length <= 1 ? (
          <div style={{ color:V('text-muted'), fontSize:13, textAlign:'center', padding:30 }}>Curve will populate as trades close today.</div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={pnlCurveData}>
              <CartesianGrid strokeDasharray="3 3" stroke={V('border')} />
              <XAxis dataKey="label" tick={{ fontSize:10, fill:V('text-muted') }} />
              <YAxis tick={{ fontSize:10, fill:V('text-muted') }} tickFormatter={v => `₹${v}`} />
              <ReTooltip
                contentStyle={{ background:V('bg-secondary'), border:`1px solid ${V('border')}`, borderRadius:8, fontSize:12 }}
                formatter={(val) => [`₹${val}`, 'Cumulative P&L']}
              />
              <defs>
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={cumPnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity={0.3}/>
                  <stop offset="95%" stopColor={cumPnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity={0}/>
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="pnl" stroke={cumPnl >= 0 ? '#10b981' : '#ef4444'} fill="url(#pnlGrad)" strokeWidth={2} dot={{ r:4, fill:V('bg-secondary'), stroke: cumPnl >= 0 ? '#10b981' : '#ef4444', strokeWidth:2 }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* Trade Heatmap */}
      <Card>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:12 }}>Performance by Hour (All Time)</div>
        {closedTrades.length === 0 ? (
          <div style={{ color:V('text-muted'), fontSize:13, textAlign:'center', padding:30 }}>Not enough trade data to show heatmap.</div>
        ) : (
          <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(4, 1fr)' : 'repeat(7, 1fr)', gap:6 }}>
            {heatmapData.map(h => {
              const total = h.wins + h.losses
              const wr = total > 0 ? h.wins / total : 0
              const intensity = total > 0 ? Math.min(1, total / 5) : 0
              const cellBg = total === 0 ? V('bg-tertiary') : wr >= 0.6 ? `rgba(16,185,129,${0.15 + intensity * 0.25})` : wr >= 0.4 ? `rgba(245,158,11,${0.15 + intensity * 0.25})` : `rgba(239,68,68,${0.15 + intensity * 0.25})`
              const cellColor = total === 0 ? V('text-muted') : wr >= 0.6 ? V('green') : wr >= 0.4 ? V('yellow') : V('red')
              return (
                <div key={h.hour} style={{ background:cellBg, borderRadius:V('radius-sm'), padding:10, textAlign:'center' }}>
                  <div style={{ fontSize:12, fontWeight:700, color:V('text-primary') }}>{h.label}</div>
                  <div style={{ fontSize:18, fontWeight:800, color:cellColor, fontFamily:"'JetBrains Mono', monospace", margin:'4px 0' }}>
                    {total > 0 ? `${Math.round(wr * 100)}%` : '—'}
                  </div>
                  <div style={{ fontSize:9, color:V('text-muted') }}>{h.wins}W / {h.losses}L</div>
                  <div style={{ fontSize:10, fontWeight:600, color:clr(h.pnl), fontFamily:"'JetBrains Mono', monospace" }}>
                    {total > 0 ? fmtPnl(h.pnl) : ''}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* Stop button at bottom */}
      <div style={{ textAlign:'center', padding:'8px 0' }}>
        <StyledButton onClick={toggleAutoTrade} variant="danger" style={{ padding:'8px 24px', fontSize:13 }}>
          <Square size={14} /> Stop Auto Trade
        </StyledButton>
      </div>
    </div>
  )
}

// ── Market Context Page ─────────────────────────────────────────────────────
function MarketContextPage() {
  const m = window.innerWidth < 768
  const [ctx, setCtx] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)

  // OI Analysis independent state
  const [oiData, setOiData] = useState(null)
  const [oiInstrument, setOiInstrument] = useState('BANKNIFTY')
  const [oiExpiry, setOiExpiry] = useState('')  // '' = nearest
  const [oiExpiries, setOiExpiries] = useState([])
  const [oiLoading, setOiLoading] = useState(false)
  const [oiLastRefresh, setOiLastRefresh] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const data = await API.get('/api/market-context')
      setCtx(data)
      setError(null)
    } catch (e) {
      setError('Failed to fetch market context')
    }
    setLoading(false)
  }, [])

  const handleRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const data = await API.post('/api/market-context/refresh')
      setCtx(data)
      setError(null)
    } catch (e) {
      setError('Refresh failed')
    }
    setRefreshing(false)
  }, [])

  // Fetch OI analysis independently
  const fetchOiAnalysis = useCallback(async (inst, exp) => {
    setOiLoading(true)
    try {
      const params = new URLSearchParams({ instrument: inst || oiInstrument })
      if (exp) params.append('expiry', exp)
      const data = await API.get(`/api/oi-analysis?${params}`)
      setOiData(data)
      setOiLastRefresh(new Date())
      // Update available expiries from response
      if (data.available_expiries) setOiExpiries(data.available_expiries)
    } catch (e) {
      console.error('OI fetch failed:', e)
    }
    setOiLoading(false)
  }, [oiInstrument])

  // Fetch expiry list when instrument changes
  const handleOiInstrumentChange = useCallback(async (newInst) => {
    setOiInstrument(newInst)
    setOiExpiry('')  // reset to nearest
    setOiExpiries([])
    try {
      const expData = await API.get(`/api/oi-expiry-list?instrument=${newInst}`)
      if (expData.expiries) setOiExpiries(expData.expiries)
    } catch (e) { /* ignore */ }
    fetchOiAnalysis(newInst, '')
  }, [fetchOiAnalysis])

  // Handle expiry change
  const handleOiExpiryChange = useCallback((newExp) => {
    setOiExpiry(newExp)
    fetchOiAnalysis(oiInstrument, newExp)
  }, [oiInstrument, fetchOiAnalysis]) // sync-fix

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 60000)
    return () => clearInterval(iv)
  }, [fetchData])

  // OI auto-refresh every 1 minute
  useEffect(() => {
    fetchOiAnalysis(oiInstrument, oiExpiry)
    const oiIv = setInterval(() => fetchOiAnalysis(oiInstrument, oiExpiry), 60000)
    return () => clearInterval(oiIv)
  }, [oiInstrument, oiExpiry, fetchOiAnalysis])

  const biasColors = {
    bullish: V('green'), mildly_bullish: V('green'),
    bearish: V('red'), mildly_bearish: V('red'),
    neutral: V('yellow')
  }
  const biasLabels = {
    bullish: 'Bullish', mildly_bullish: 'Mildly Bullish',
    bearish: 'Bearish', mildly_bearish: 'Mildly Bearish',
    neutral: 'Neutral'
  }

  if (loading && !ctx) {
    return (
      <div className="fade-in" style={{ textAlign:'center', padding:60 }}>
        <RefreshCw size={24} className="spin" style={{ color:V('accent'), marginBottom:12 }} />
        <div style={{ color:V('text-muted'), fontSize:13 }}>Loading global market data...</div>
        <div style={{ color:V('text-muted'), fontSize:11, marginTop:6 }}>First load may take 10-15 seconds (fetching from Yahoo Finance + Dhan)</div>
      </div>
    )
  }

  const markets = ctx?.global_markets?.markets || []
  const bias = ctx?.global_bias || {}
  const sentiment = ctx?.news_sentiment || {}
  const fearGreed = ctx?.fear_greed || {}
  const vix = ctx?.india_vix || {}
  const expiry = ctx?.expiry_today || {}
  const composite = ctx?.composite_score || {}
  const correlation = ctx?.nifty_bn_correlation || {}
  const oi = oiData || ctx?.oi_analysis || {}

  // Group markets by region
  const usMarkets = markets.filter(m => m.region === 'US')
  const commodityMarkets = markets.filter(m => m.region === 'Commodities')
  const asiaMarkets = markets.filter(m => m.region === 'Asia')
  const europeMarkets = markets.filter(m => m.region === 'Europe' && m.symbol !== '^FTSE')
  const ukMarket = markets.filter(m => m.symbol === '^FTSE')
  const indiaMarkets = markets.filter(m => m.region === 'India')

  const renderMarketRow = (m) => (
    <div key={m.symbol} style={{
      display:'flex', justifyContent:'space-between', alignItems:'center',
      padding:'10px 0', borderBottom:`1px solid ${V('border-light')}`
    }}>
      <div>
        <div style={{ fontSize:13, fontWeight:600, color:V('text-primary') }}>{m.name}</div>
        <div style={{ fontSize:10, color:V('text-muted') }}>
          {m.symbol} {m.type === 'futures' ? 'Futures' : 'Index'}
          {m.source && <span style={{ marginLeft:4, fontSize:8, color:V('accent'), fontWeight:600 }}>• {m.source}</span>}
        </div>
      </div>
      <div style={{ textAlign:'right' }}>
        {m.status === 'ok' ? (
          <>
            <div style={{ fontSize:15, fontWeight:700, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>
              {m.unit === 'USD' ? '$' : ''}{m.price?.toLocaleString()}{m.unit === 'USD' ? '' : ''}
            </div>
            <div style={{ fontSize:12, fontWeight:600, color: m.change >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
              {m.change >= 0 ? '+' : ''}{m.change} ({m.change >= 0 ? '+' : ''}{m.change_pct}%)
            </div>
          </>
        ) : (
          <div style={{ fontSize:11, color:V('text-muted') }}>Unavailable</div>
        )}
      </div>
    </div>
  )

  const renderRegionSection = (title, regionMarkets, emoji) => (
    <div style={{ marginBottom:4 }}>
      <div style={{ fontSize:11, fontWeight:600, color:V('text-head'), textTransform:'uppercase', letterSpacing:'0.08em', marginBottom:6 }}>
        {emoji} {title}
      </div>
      {regionMarkets.length > 0 ? regionMarkets.map(renderMarketRow) : (
        <div style={{ color:V('text-muted'), fontSize:11, padding:8 }}>No data</div>
      )}
    </div>
  )

  // Fear & Greed gauge color
  const fgScore = fearGreed.score
  const fgColor = fgScore >= 55 ? V('green') : fgScore >= 45 ? V('yellow') : fgScore != null ? V('red') : V('text-muted')

  // VIX color
  const vixColor = vix.level === 'very_low' || vix.level === 'low' ? V('green')
    : vix.level === 'moderate' ? V('yellow')
    : vix.level === 'elevated' || vix.level === 'high' ? V('orange')
    : vix.level === 'extreme' ? V('red') : V('text-muted')

  // Composite score color
  const compColor = (composite.composite_score||0) >= 20 ? V('green')
    : (composite.composite_score||0) >= -20 ? V('yellow') : V('red')

  return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:14 }}>

      {/* ═══ Top Bar: Refresh All + Composite Master Score ═══ */}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div style={{ display:'flex', alignItems:'center', gap:14 }}>
          <div style={{ fontSize:11, color:V('text-muted') }}>
            {ctx?.global_markets?.timestamp ? `Updated: ${new Date(ctx.global_markets.timestamp).toLocaleTimeString()}` : ''}
          </div>
          {composite.composite_score != null && (
            <div style={{ display:'flex', alignItems:'center', gap:6, background:V('bg-tertiary'), padding:'4px 10px', borderRadius:V('radius-md') }}>
              <Target size={12} style={{ color: compColor }} />
              <span style={{ fontSize:11, color:V('text-muted') }}>Master Score:</span>
              <span style={{ fontSize:14, fontWeight:800, color: compColor, fontFamily:"'JetBrains Mono', monospace" }}>
                {composite.composite_score > 0 ? '+' : ''}{composite.composite_score}
              </span>
              <span style={{ fontSize:10, fontWeight:600, color: compColor }}>{composite.label}</span>
            </div>
          )}
        </div>
        <StyledButton onClick={handleRefresh} variant="primary" style={{ padding:'5px 14px', fontSize:11 }} disabled={refreshing}>
          <RefreshCw size={12} className={refreshing ? 'spin' : ''} /> {refreshing ? 'Refreshing All...' : 'Refresh All'}
        </StyledButton>
      </div>

      {error && (
        <div style={{ background:V('red-bg'), padding:'10px 14px', borderRadius:V('radius-md'), color:V('red'), fontSize:12, fontWeight:600 }}>
          {error}
        </div>
      )}

      {/* ═══ Tile A: Global Markets ═══ */}
      <Card>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14 }}>
          <div style={{ color:V('accent'), fontSize:13, fontWeight:700 }}>Global Markets Overview</div>
          <Badge label={`${markets.filter(m=>m.status==='ok'&&m.change>=0).length}/${markets.filter(m=>m.status==='ok').length} Green`}
            color={markets.filter(m=>m.status==='ok'&&m.change>=0).length > markets.filter(m=>m.status==='ok').length/2 ? V('green') : V('red')} />
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:20 }}>
          <div>
            {renderRegionSection('India', indiaMarkets, '🇮🇳')}
            {renderRegionSection('US Futures', usMarkets, '🇺🇸')}
            {renderRegionSection('Europe', europeMarkets, '🇪🇺')}
          </div>
          <div>
            {renderRegionSection('Asia', asiaMarkets, '🌏')}
            {renderRegionSection('Commodities (USD)', commodityMarkets, '🛢️')}
            {renderRegionSection('Europe (UK)', ukMarket, '🇬🇧')}
          </div>
        </div>
      </Card>

      {/* ═══ Row: Global Bias + VIX & Expiry ═══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>

        {/* Left: Global Analysis */}
        <Card>
          <div style={{ color:V('purple'), fontSize:13, fontWeight:700, marginBottom:12 }}>Global Market Analysis</div>
          <div style={{ textAlign:'center', marginBottom:12 }}>
            <div style={{ fontSize:36, fontWeight:800, color: biasColors[bias.bias] || V('text-muted'), fontFamily:"'JetBrains Mono', monospace" }}>
              {bias.score != null ? `${bias.score > 0 ? '+' : ''}${bias.score}` : '—'}
            </div>
            <div style={{ fontSize:15, fontWeight:700, color: biasColors[bias.bias] || V('text-muted'), marginTop:2 }}>
              {biasLabels[bias.bias] || 'Loading...'}
            </div>
            <div style={{ height:5, background:V('bg-tertiary'), borderRadius:3, overflow:'hidden', margin:'10px 0 0', position:'relative' }}>
              <div style={{ position:'absolute', left:'50%', top:0, height:'100%', width:2, background:V('text-muted'), opacity:0.3 }} />
              <div style={{
                height:'100%', width:`${Math.abs(bias.score||0)/2}%`,
                marginLeft: (bias.score||0) >= 0 ? '50%' : `${50 - Math.abs(bias.score||0)/2}%`,
                background: (bias.score||0) >= 0 ? V('green') : V('red'),
                borderRadius:3, transition:'all 0.5s'
              }} />
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:8, color:V('text-muted'), marginTop:2 }}>
              <span>Bearish -100</span><span>Neutral</span><span>Bullish +100</span>
            </div>
          </div>
          {bias.details && (
            <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:6, marginBottom:10 }}>
              {[
                { label:'US', val: bias.details.us_avg },
                { label:'Asia', val: bias.details.asia_avg },
                { label:'Europe', val: bias.details.europe_avg },
              ].map(r => (
                <div key={r.label} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:8, textAlign:'center' }}>
                  <div style={{ fontSize:10, color:V('text-muted') }}>{r.label}</div>
                  <div style={{ fontSize:14, fontWeight:700, color: (r.val||0) >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                    {r.val != null ? `${r.val >= 0 ? '+' : ''}${r.val}%` : '—'}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div style={{ borderTop:`1px solid ${V('border-light')}`, paddingTop:8 }}>
            {(bias.rationale || []).map((r, i) => (
              <div key={i} style={{ fontSize:11, color:V('text-secondary'), lineHeight:1.6, paddingLeft:8, borderLeft:`2px solid ${V('border')}`, marginBottom:3 }}>
                {r}
              </div>
            ))}
          </div>
        </Card>

        {/* Right: India VIX + Expiry Today stacked */}
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {/* India VIX */}
          <Card>
            <div style={{ color:V('orange'), fontSize:13, fontWeight:700, marginBottom:10, display:'flex', alignItems:'center', gap:6 }}>
              <Activity size={14} /> India VIX
            </div>
            {vix.value != null ? (
              <>
                <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:10 }}>
                  <div style={{ fontSize:42, fontWeight:800, color: vixColor, fontFamily:"'JetBrains Mono', monospace" }}>
                    {vix.value}
                  </div>
                  <div>
                    <Badge label={vix.level === 'very_low' ? 'Very Low' : vix.level === 'low' ? 'Low' : vix.level === 'moderate' ? 'Moderate' : vix.level === 'elevated' ? 'Elevated' : vix.level === 'high' ? 'High' : vix.level === 'extreme' ? 'Extreme' : '—'}
                      color={vixColor} />
                    <div style={{ fontSize:11, color:V('text-secondary'), lineHeight:1.5, marginTop:6 }}>
                      {vix.interpretation}
                    </div>
                  </div>
                </div>
                <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(5, 1fr)', gap:2, fontSize:8 }}>
                  {[
                    { label:'<12', color:V('green'), text:'Calm' },
                    { label:'12-20', color:V('yellow'), text:'Normal' },
                    { label:'20-25', color:V('orange'), text:'Caution' },
                    { label:'25-30', color:V('red'), text:'High' },
                    { label:'>30', color:V('red'), text:'Extreme' },
                  ].map(s => {
                    const active = vix.value && (
                      (s.label === '<12' && vix.value < 12) ||
                      (s.label === '12-20' && vix.value >= 12 && vix.value < 20) ||
                      (s.label === '20-25' && vix.value >= 20 && vix.value < 25) ||
                      (s.label === '25-30' && vix.value >= 25 && vix.value < 30) ||
                      (s.label === '>30' && vix.value >= 30)
                    )
                    return (
                      <div key={s.label} style={{ textAlign:'center', padding:3,
                        background: active ? V('bg-tertiary') : 'transparent', borderRadius:3,
                        border: active ? `1px solid ${s.color}` : `1px solid ${V('border-light')}`
                      }}>
                        <div style={{ color: s.color, fontWeight:600 }}>{s.label}</div>
                        <div style={{ color:V('text-muted') }}>{s.text}</div>
                      </div>
                    )
                  })}
                </div>
              </>
            ) : (
              <div style={{ textAlign:'center', padding:16, color:V('text-muted'), fontSize:11 }}>
                {vix.error === 'Dhan not connected' ? 'Connect to Dhan to view India VIX (yfinance fallback loading...)' : 'Fetching VIX data...'}
              </div>
            )}
          </Card>

          {/* Expiry Today */}
          <Card>
            <div style={{ color:V('cyan'), fontSize:13, fontWeight:700, marginBottom:10, display:'flex', alignItems:'center', gap:6 }}>
              <Calendar size={14} /> Expiry Today
            </div>
            {expiry.has_expiry ? (
              <div>
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:10 }}>
                  <AlertCircle size={18} style={{ color:V('orange') }} />
                  <span style={{ fontSize:13, fontWeight:700, color:V('orange') }}>Expiry Day!</span>
                </div>
                {(expiry.expiries || []).map((exp, i) => (
                  <div key={i} style={{
                    background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:10, marginBottom:6,
                    border:`1px solid ${exp.type === 'Monthly' ? V('orange') : V('border')}`,
                    display:'flex', justifyContent:'space-between', alignItems:'center'
                  }}>
                    <div>
                      <div style={{ fontSize:14, fontWeight:800, color:V('text-primary') }}>{exp.instrument}</div>
                      <div style={{ fontSize:10, color:V('text-muted'), marginTop:2 }}>Expect higher volatility, rapid theta decay</div>
                    </div>
                    <Badge label={exp.type} color={exp.type === 'Monthly' ? V('orange') : V('accent')} />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display:'flex', alignItems:'center', gap:12, padding:'8px 0' }}>
                <CheckCircle size={20} style={{ color:V('green') }} />
                <div>
                  <div style={{ fontSize:13, fontWeight:600, color:V('green') }}>No Expiry Today</div>
                  <div style={{ fontSize:10, color:V('text-muted') }}>
                    {expiry.today || 'Normal trading day'}
                    {expiry.error === 'Dhan not connected' && ' • Connect to Dhan for live expiry data'}
                  </div>
                </div>
              </div>
            )}
          </Card>

          {/* Nifty / BankNifty Correlation */}
          <Card>
            <div style={{ color:V('purple'), fontSize:13, fontWeight:700, marginBottom:10, display:'flex', alignItems:'center', gap:6 }}>
              <TrendingUp size={14} /> Nifty / BankNifty Correlation
            </div>
            {correlation.nifty?.price ? (
              <>
                {/* Index comparison row */}
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:10 }}>
                  {[
                    { label: 'NIFTY 50', data: correlation.nifty },
                    { label: 'BANKNIFTY', data: correlation.banknifty },
                  ].map(idx => (
                    <div key={idx.label} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:10 }}>
                      <div style={{ fontSize:10, color:V('text-muted'), fontWeight:600, marginBottom:4 }}>{idx.label}</div>
                      <div style={{ fontSize:16, fontWeight:800, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>
                        {idx.data.price?.toLocaleString()}
                      </div>
                      {idx.data.change_pct != null && (
                        <div style={{ fontSize:12, fontWeight:600, color: idx.data.change_pct >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                          {idx.data.change >= 0 ? '+' : ''}{idx.data.change} ({idx.data.change_pct >= 0 ? '+' : ''}{idx.data.change_pct}%)
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* Direction badge */}
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
                  <Badge
                    label={correlation.direction === 'aligned' ? 'Aligned' : 'Divergent'}
                    color={correlation.direction === 'aligned' ? V('green') : V('orange')}
                  />
                  {correlation.beta != null && (
                    <span style={{ fontSize:10, color:V('text-muted'), fontFamily:"'JetBrains Mono', monospace" }}>
                      β = {correlation.beta}
                    </span>
                  )}
                </div>

                {/* Alignment text */}
                {correlation.alignment && (
                  <div style={{ fontSize:11, fontWeight:600, color: correlation.direction === 'aligned' ? V('green') : V('orange'), marginBottom:8 }}>
                    {correlation.alignment}
                  </div>
                )}

                {/* Analysis points */}
                {(correlation.analysis || []).map((a, i) => (
                  <div key={i} style={{ fontSize:10, color:V('text-secondary'), lineHeight:1.6, paddingLeft:8, borderLeft:`2px solid ${V('border')}`, marginBottom:3 }}>
                    {a}
                  </div>
                ))}

                {/* OI Cross-Analysis */}
                {correlation.oi_cross?.status === 'ok' && (
                  <div style={{ marginTop:10, borderTop:`1px solid ${V('border')}`, paddingTop:10 }}>
                    <div style={{ fontSize:10, fontWeight:600, color:V('orange'), textTransform:'uppercase', marginBottom:8 }}>
                      OI Cross-Analysis
                    </div>
                    <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6, marginBottom:8 }}>
                      {[
                        { label:'NIFTY', pcr: correlation.oi_cross.nifty_pcr, verdict: correlation.oi_cross.nifty_verdict, mp: correlation.oi_cross.nifty_max_pain, range: correlation.oi_cross.nifty_oi_range, iv: correlation.oi_cross.nifty_iv },
                        { label:'BANKNIFTY', pcr: correlation.oi_cross.banknifty_pcr, verdict: correlation.oi_cross.banknifty_verdict, mp: correlation.oi_cross.banknifty_max_pain, range: correlation.oi_cross.banknifty_oi_range, iv: correlation.oi_cross.banknifty_iv },
                      ].map(idx => (
                        <div key={idx.label} style={{ background:V('bg-tertiary'), borderRadius:4, padding:8 }}>
                          <div style={{ fontSize:9, fontWeight:700, color:V('text-muted'), marginBottom:4 }}>{idx.label}</div>
                          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                            <span style={{ fontSize:10, color:V('text-secondary') }}>PCR:</span>
                            <span style={{ fontSize:12, fontWeight:700, fontFamily:"'JetBrains Mono', monospace",
                              color: idx.pcr >= 1 ? V('green') : idx.pcr < 0.7 ? V('red') : V('yellow')
                            }}>{idx.pcr}</span>
                          </div>
                          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:2 }}>
                            <span style={{ fontSize:9, color:V('text-muted') }}>Verdict:</span>
                            <span style={{ fontSize:9, fontWeight:600,
                              color: idx.verdict?.includes('Bullish') ? V('green') : idx.verdict?.includes('Bearish') ? V('red') : V('yellow')
                            }}>{idx.verdict}</span>
                          </div>
                          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:2 }}>
                            <span style={{ fontSize:9, color:V('text-muted') }}>Max Pain:</span>
                            <span style={{ fontSize:9, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{idx.mp?.toLocaleString()}</span>
                          </div>
                          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:2 }}>
                            <span style={{ fontSize:9, color:V('text-muted') }}>ATM IV:</span>
                            <span style={{ fontSize:9, fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{idx.iv}%</span>
                          </div>
                          <div style={{ fontSize:8, color:V('text-muted'), marginTop:3 }}>Range: {idx.range}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ fontSize:10, lineHeight:1.5, padding:'6px 8px', borderRadius:4,
                      background: correlation.oi_cross.oi_alignment === 'aligned' ? V('green')+'15' : correlation.oi_cross.oi_alignment === 'divergent' ? V('red')+'15' : V('yellow')+'15',
                      color: correlation.oi_cross.oi_alignment === 'aligned' ? V('green') : correlation.oi_cross.oi_alignment === 'divergent' ? V('red') : V('yellow'),
                      fontWeight:600
                    }}>
                      {correlation.oi_cross.oi_alignment_text}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div style={{ textAlign:'center', padding:12, color:V('text-muted'), fontSize:11 }}>
                {correlation.analysis?.[0] || 'Loading correlation data...'}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* ═══ Row: Composite Master Score + Sentiment ═══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>

        {/* Composite Master Score */}
        <Card style={{ borderTop:`3px solid ${compColor}` }}>
          <div style={{ color: compColor, fontSize:13, fontWeight:700, marginBottom:14, display:'flex', alignItems:'center', gap:6 }}>
            <Target size={14} /> Composite Market Score
          </div>

          <div style={{ textAlign:'center', marginBottom:16 }}>
            <div style={{ fontSize:48, fontWeight:900, color: compColor, fontFamily:"'JetBrains Mono', monospace" }}>
              {composite.composite_score != null ? `${composite.composite_score > 0 ? '+' : ''}${composite.composite_score}` : '—'}
            </div>
            <div style={{ fontSize:18, fontWeight:700, color: compColor, marginTop:4 }}>
              {composite.label || 'N/A'}
            </div>
            <div style={{ height:6, background:V('bg-tertiary'), borderRadius:3, overflow:'hidden', margin:'12px 0 0', position:'relative' }}>
              <div style={{ position:'absolute', left:'50%', top:0, height:'100%', width:2, background:V('text-muted'), opacity:0.3 }} />
              <div style={{
                height:'100%', width:`${Math.abs(composite.composite_score||0)/2}%`,
                marginLeft: (composite.composite_score||0) >= 0 ? '50%' : `${50 - Math.abs(composite.composite_score||0)/2}%`,
                background: compColor,
                borderRadius:3, transition:'all 0.5s'
              }} />
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:8, color:V('text-muted'), marginTop:2 }}>
              <span>Strong Bearish -100</span><span>Neutral</span><span>Strong Bullish +100</span>
            </div>
          </div>

          {/* Component breakdown */}
          {composite.components && (
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              {Object.entries(composite.components).map(([key, comp]) => (
                <div key={key} style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:8 }}>
                  <div style={{ fontSize:9, color:V('text-muted'), textTransform:'uppercase' }}>{comp.label}</div>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', marginTop:2 }}>
                    <span style={{ fontSize:16, fontWeight:700, color: comp.score >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                      {comp.score > 0 ? '+' : ''}{comp.score}
                    </span>
                    <span style={{ fontSize:9, color:V('text-muted') }}>{comp.weight}% wt</span>
                  </div>
                  {comp.raw != null && (
                    <div style={{ fontSize:8, color:V('text-muted'), marginTop:1 }}>Raw: {comp.raw}</div>
                  )}
                </div>
              ))}
            </div>
          )}

          {composite.explanation && (
            <div style={{ fontSize:8, color:V('text-muted'), fontFamily:"'JetBrains Mono', monospace", background:V('bg-input'), padding:'4px 8px', borderRadius:4, marginTop:10 }}>
              {composite.explanation}
            </div>
          )}
        </Card>

        {/* Tile C: Sentiment */}
        <Card>
          <div style={{ color:V('cyan'), fontSize:13, fontWeight:700, marginBottom:14 }}>Market Sentiment</div>

          {/* Fear & Greed */}
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:14, marginBottom:14 }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
              <div style={{ fontSize:11, fontWeight:600, color:V('text-head'), textTransform:'uppercase' }}>CNN Fear & Greed Index</div>
              <span style={{ fontSize:9, color:V('text-muted'), fontStyle:'italic' }}>~15 min cache</span>
            </div>
            <div style={{ display:'flex', alignItems:'center', gap:16 }}>
              <div style={{ position:'relative', width:70, height:70, flexShrink:0 }}>
                <svg viewBox="0 0 36 36" style={{ width:70, height:70, transform:'rotate(-90deg)' }}>
                  <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                    fill="none" stroke={V('border')} strokeWidth="3" />
                  <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                    fill="none" stroke={fgColor} strokeWidth="3"
                    strokeDasharray={`${fgScore || 0}, 100`} strokeLinecap="round" />
                </svg>
                <div style={{ position:'absolute', top:'50%', left:'50%', transform:'translate(-50%,-50%)', textAlign:'center' }}>
                  <div style={{ fontSize:18, fontWeight:800, color:fgColor, fontFamily:"'JetBrains Mono', monospace" }}>{fgScore ?? '—'}</div>
                </div>
              </div>
              <div>
                <div style={{ fontSize:16, fontWeight:700, color:fgColor }}>{fearGreed.label || 'N/A'}</div>
                {fearGreed.previous_close != null && (
                  <div style={{ fontSize:10, color:V('text-muted'), marginTop:2 }}>Previous: {fearGreed.previous_close}</div>
                )}
              </div>
            </div>
          </div>

          {/* News Sentiment Score */}
          <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:14, marginBottom:14 }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
              <div style={{ fontSize:11, fontWeight:600, color:V('text-head'), textTransform:'uppercase' }}>News Sentiment (Weighted)</div>
              <Badge label={sentiment.overall === 'bullish' ? 'Bullish' : sentiment.overall === 'bearish' ? 'Bearish' : 'Neutral'}
                color={sentiment.overall === 'bullish' ? V('green') : sentiment.overall === 'bearish' ? V('red') : V('yellow')} />
            </div>
            <div style={{ display:'flex', gap:16, alignItems:'center', marginBottom:6 }}>
              <div>
                <span style={{ fontSize:10, color:V('text-muted') }}>Score: </span>
                <span style={{ fontSize:14, fontWeight:700, color: (sentiment.score||0) > 0 ? V('green') : (sentiment.score||0) < 0 ? V('red') : V('yellow'), fontFamily:"'JetBrains Mono', monospace" }}>
                  {sentiment.score ?? 0}
                </span>
              </div>
              <div>
                <span style={{ fontSize:10, color:V('green') }}>Bull: {sentiment.bull_count || 0}</span>
                <span style={{ fontSize:10, color:V('text-muted'), margin:'0 6px' }}>|</span>
                <span style={{ fontSize:10, color:V('red') }}>Bear: {sentiment.bear_count || 0}</span>
              </div>
            </div>
            {sentiment.calculation && (
              <div style={{ fontSize:8, color:V('text-muted'), fontFamily:"'JetBrains Mono', monospace", background:V('bg-input'), padding:'4px 8px', borderRadius:4, marginBottom:4 }}>
                {sentiment.calculation}
              </div>
            )}
            {sentiment.method && (
              <div style={{ fontSize:8, color:V('text-muted'), fontStyle:'italic' }}>
                {sentiment.method}
              </div>
            )}
            <div style={{ fontSize:9, color:V('text-muted'), marginTop:4 }}>
              {sentiment.total_headlines ? `${sentiment.total_headlines} headlines scanned, ${sentiment.scored_headlines} with keywords` : ''}
              {sentiment.skipped_old_headlines ? ` · ${sentiment.skipped_old_headlines} older than 3 days left out` : ''}
            </div>
          </div>

          {/* News Headlines */}
          <div>
            <div style={{ fontSize:10, fontWeight:600, color:V('text-head'), textTransform:'uppercase', marginBottom:8 }}>Top Headlines (by impact)</div>
            <div style={{ maxHeight:200, overflowY:'auto' }}>
              {(sentiment.headlines || []).slice(0, 12).map((h, i) => (
                <div key={i} style={{
                  padding:'5px 0', borderBottom:`1px solid ${V('border-light')}`,
                  display:'flex', gap:8, alignItems:'flex-start'
                }}>
                  <div style={{
                    width:6, height:6, borderRadius:'50%', marginTop:4, flexShrink:0,
                    background: h.sentiment === 'bullish' ? V('green') : h.sentiment === 'bearish' ? V('red') : V('text-muted')
                  }} />
                  <div style={{ flex:1 }}>
                    <div style={{ fontSize:11, color:V('text-primary'), lineHeight:1.4 }}>{h.title}</div>
                    <div style={{ fontSize:9, color:V('text-muted'), marginTop:1 }}>{h.source}
                      {h.pub_date && !isNaN(new Date(h.pub_date)) && (
                        <span style={{ marginLeft:6 }}>· {toISTDateTime(h.pub_date)}</span>
                      )}
                      {h.score != null && h.score !== 0 && (
                        <span style={{ marginLeft:6, fontWeight:600, color: h.score > 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                          {h.score > 0 ? '+' : ''}{h.score}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {(!sentiment.headlines || sentiment.headlines.length === 0) && (
                <div style={{ color:V('text-muted'), fontSize:11, padding:12, textAlign:'center' }}>No headlines available</div>
              )}
            </div>
          </div>
        </Card>
      </div>

      {/* ═══ Open Interest Analysis ═══ */}
      <Card style={{ borderTop:`3px solid ${V('orange')}` }}>
        {/* Header: Title + Selectors + Refresh + Verdict */}
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14, flexWrap:'wrap', gap:8 }}>
          <div style={{ color:V('orange'), fontSize:13, fontWeight:700, display:'flex', alignItems:'center', gap:6 }}>
            <BarChart2 size={14} /> Open Interest Analysis
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            {/* Instrument selector */}
            <select value={oiInstrument} onChange={e => handleOiInstrumentChange(e.target.value)}
              style={{ background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:4, padding:'4px 8px', fontSize:11, fontWeight:600, cursor:'pointer', outline:'none' }}>
              {(oi.available_instruments || [
                {value:'BANKNIFTY',label:'BANKNIFTY'},{value:'NIFTY',label:'NIFTY 50'},
                {value:'FINNIFTY',label:'FINNIFTY'},{value:'MIDCPNIFTY',label:'MIDCPNIFTY'},
                {value:'SENSEX',label:'SENSEX'},{value:'BANKEX',label:'BANKEX'}
              ]).map(inst => (
                <option key={inst.value} value={inst.value}>{inst.label}</option>
              ))}
            </select>

            {/* Expiry selector */}
            <select value={oiExpiry} onChange={e => handleOiExpiryChange(e.target.value)}
              style={{ background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:4, padding:'4px 8px', fontSize:11, cursor:'pointer', outline:'none', maxWidth:140 }}>
              <option value="">Nearest Expiry</option>
              {(oiExpiries.length ? oiExpiries : oi.available_expiries || []).map(exp => (
                <option key={exp} value={exp}>{exp}</option>
              ))}
            </select>

            {/* Refresh button */}
            <button onClick={() => fetchOiAnalysis(oiInstrument, oiExpiry)}
              disabled={oiLoading}
              style={{ background:'transparent', border:`1px solid ${V('orange')}`, color:V('orange'), borderRadius:4, padding:'4px 8px', fontSize:10, cursor:'pointer', display:'flex', alignItems:'center', gap:4, opacity: oiLoading ? 0.5 : 1 }}>
              <RefreshCw size={12} className={oiLoading ? 'spin' : ''} />
              {oiLoading ? 'Loading...' : 'Refresh OI'}
            </button>

            {oi.oi_verdict && <Badge label={oi.verdict_text || 'N/A'} color={
              oi.oi_verdict?.includes('bullish') ? V('green') : oi.oi_verdict?.includes('bearish') ? V('red') : V('yellow')
            } />}
          </div>
        </div>

        {/* Last refresh + auto-refresh info */}
        {oiLastRefresh && (
          <div style={{ fontSize:9, color:V('text-muted'), marginBottom:10, display:'flex', gap:12 }}>
            <span>Last updated: {oiLastRefresh.toLocaleTimeString()}</span>
            <span>Exp: {oi.expiry || '—'}</span>
            <span>Auto-refresh: 5 min</span>
          </div>
        )}

        {oi.status === 'ok' ? (
          <>
            {/* Data quality warning for BSE indices */}
            {oi.data_quality && oi.data_quality !== 'good' && (
              <div style={{ background:V('yellow')+'15', border:`1px solid ${V('yellow')}40`, borderRadius:V('radius-sm'), padding:'6px 12px', marginBottom:10, fontSize:10, color:V('yellow'), display:'flex', alignItems:'center', gap:6 }}>
                <AlertCircle size={12} />
                {oi.data_quality === 'poor'
                  ? `${oi.instrument} option chain has limited IV/premium data from Dhan — PCR, Max Pain, Support/Resistance and OI data are accurate. Straddle and IV Skew may be unavailable.`
                  : `Some data fields for ${oi.instrument} may be incomplete.`}
              </div>
            )}

            {/* Row 1: PCR + Max Pain + ATM Straddle + IV Skew */}
            <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : '1fr 1fr 1fr 1fr', gap:10, marginBottom:14 }}>
              {/* PCR — with rationale */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:9, color:V('text-muted'), textTransform:'uppercase', marginBottom:6 }}>Put-Call Ratio (OI)</div>
                <div style={{ fontSize:28, fontWeight:800, fontFamily:"'JetBrains Mono', monospace", color:
                  oi.pcr_signal?.includes('bullish') ? V('green') : oi.pcr_signal?.includes('bearish') ? V('red') : V('yellow')
                }}>{oi.pcr}</div>
                <div style={{ fontSize:9, color:V('text-muted'), marginTop:4 }}>Vol PCR: {oi.pcr_volume}</div>
                <div style={{ fontSize:9, color:V('text-secondary'), marginTop:4, lineHeight:1.4 }}>{oi.pcr_interpretation}</div>
                <div style={{ fontSize:8, color:V('text-muted'), marginTop:6, lineHeight:1.5, borderTop:`1px solid ${V('border-light')}`, paddingTop:6 }}>
                  <strong style={{ color:V('green') }}>What this means:</strong> PCR = Total Put OI / Total Call OI.
                  Above 1.0 means more puts are being written (sellers expect support) — <em>bullish</em>.
                  Below 0.7 means heavy call writing (sellers cap upside) — <em>bearish</em>.
                  Extreme readings {'>'} 1.5 or {'<'} 0.5 often signal reversals.
                </div>
              </div>

              {/* Max Pain — with rationale */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:9, color:V('text-muted'), textTransform:'uppercase', marginBottom:6 }}>Max Pain</div>
                <div style={{ fontSize:28, fontWeight:800, fontFamily:"'JetBrains Mono', monospace", color:V('text-head') }}>
                  {oi.max_pain?.toLocaleString()}
                </div>
                <div style={{ fontSize:10, color:V('text-muted'), marginTop:4 }}>
                  Spot: <span style={{ fontWeight:600, color:V('text-primary') }}>{oi.spot_price?.toLocaleString()}</span>
                </div>
                <div style={{ fontSize:10, marginTop:4, color: oi.max_pain_distance > 0 ? V('red') : oi.max_pain_distance < 0 ? V('green') : V('yellow'), fontFamily:"'JetBrains Mono', monospace" }}>
                  {oi.max_pain_distance > 0 ? '+' : ''}{oi.max_pain_distance?.toFixed(0)} pts ({oi.max_pain_pct}%)
                </div>
                <div style={{ fontSize:8, color:V('text-muted'), marginTop:6, lineHeight:1.5, borderTop:`1px solid ${V('border-light')}`, paddingTop:6 }}>
                  <strong style={{ color:V('text-head') }}>What this means:</strong> Max Pain is the strike where option buyers lose the most money.
                  Price tends to gravitate toward this level near expiry (option sellers defend it).
                  {oi.max_pain_distance > 0
                    ? ` Spot is ${Math.abs(oi.max_pain_distance)?.toFixed(0)} pts ABOVE Max Pain — expect downward pull.`
                    : oi.max_pain_distance < 0
                    ? ` Spot is ${Math.abs(oi.max_pain_distance)?.toFixed(0)} pts BELOW Max Pain — upward gravitational pull likely.`
                    : ' Spot is at Max Pain — expect range-bound movement.'}
                </div>
              </div>

              {/* ATM Straddle — with rationale */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:9, color:V('text-muted'), textTransform:'uppercase', marginBottom:6 }}>ATM Straddle ({oi.atm_strike})</div>
                {oi.atm_straddle > 0 ? (
                  <>
                    <div style={{ fontSize:28, fontWeight:800, fontFamily:"'JetBrains Mono', monospace", color:V('cyan') }}>
                      {oi.atm_straddle}
                    </div>
                    <div style={{ fontSize:10, color:V('text-muted'), marginTop:4 }}>ATM IV: {oi.atm_iv > 0 ? `${oi.atm_iv}%` : 'N/A'}</div>
                    <div style={{ fontSize:10, color:V('text-secondary'), marginTop:4, fontFamily:"'JetBrains Mono', monospace" }}>
                      Range: {oi.expected_range?.low?.toLocaleString()} — {oi.expected_range?.high?.toLocaleString()}
                    </div>
                    <div style={{ fontSize:8, color:V('text-muted'), marginTop:6, lineHeight:1.5, borderTop:`1px solid ${V('border-light')}`, paddingTop:6 }}>
                      <strong style={{ color:V('cyan') }}>What this means:</strong> The ATM straddle price ({oi.atm_straddle}) is what the market expects {oi.instrument} to move by this expiry.
                      If you think the move will be <em>less</em>, sell the straddle. If <em>more</em>, buy it.
                      The range is the 1-SD expected band — price stays within ~68% of the time.
                    </div>
                  </>
                ) : (
                  <div style={{ color:V('text-muted'), fontSize:11, padding:'8px 0' }}>
                    Straddle data unavailable for {oi.instrument}
                    <div style={{ fontSize:9, marginTop:4 }}>BSE indices may have limited IV/premium data from Dhan API</div>
                  </div>
                )}
              </div>

              {/* IV Skew — with rationale */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:9, color:V('text-muted'), textTransform:'uppercase', marginBottom:6 }}>IV Skew (Put - Call)</div>
                {(oi.avg_put_iv > 0 || oi.avg_call_iv > 0) ? (
                  <>
                    <div style={{ fontSize:28, fontWeight:800, fontFamily:"'JetBrains Mono', monospace", color:
                      oi.iv_skew > 3 ? V('red') : oi.iv_skew < -3 ? V('green') : V('yellow')
                    }}>
                      {oi.iv_skew > 0 ? '+' : ''}{oi.iv_skew}%
                    </div>
                    <div style={{ fontSize:9, color:V('text-muted'), marginTop:4 }}>Put IV: {oi.avg_put_iv}% | Call IV: {oi.avg_call_iv}%</div>
                    <div style={{ fontSize:9, color:V('text-secondary'), marginTop:4, lineHeight:1.4 }}>{oi.iv_skew_text}</div>
                    <div style={{ fontSize:8, color:V('text-muted'), marginTop:6, lineHeight:1.5, borderTop:`1px solid ${V('border-light')}`, paddingTop:6 }}>
                      <strong style={{ color:V('orange') }}>What this means:</strong> IV Skew compares OTM Put IV vs OTM Call IV.
                      Positive skew = puts are more expensive (fear of downside).
                      Negative skew = calls are costlier (breakout expectation).
                      {Math.abs(oi.iv_skew) <= 3 ? ' Current skew is balanced — no strong fear or greed in options pricing.' :
                       oi.iv_skew > 3 ? ' Traders are paying a premium for downside protection — cautious sentiment.' :
                       ' Unusual call demand — market may be anticipating an upside move.'}
                    </div>
                  </>
                ) : (
                  <div style={{ color:V('text-muted'), fontSize:11, padding:'8px 0' }}>
                    IV data unavailable for {oi.instrument}
                    <div style={{ fontSize:9, marginTop:4 }}>BSE indices may have limited IV data from Dhan API</div>
                  </div>
                )}
              </div>
            </div>

            {/* Row 2: Support/Resistance + OI Change + Signals */}
            <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:10, marginBottom:14 }}>
              {/* Support Levels */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:10, fontWeight:600, color:V('green'), textTransform:'uppercase', marginBottom:8 }}>
                  OI Support Levels (Put OI)
                </div>
                {(oi.supports || []).map((s, i) => (
                  <div key={i} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', borderBottom:`1px solid ${V('border-light')}` }}>
                    <div>
                      <span style={{ fontSize:14, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('green') }}>
                        {s.strike?.toLocaleString()}
                      </span>
                      {s.strength === 'strong' && <span style={{ fontSize:8, background:V('green'), color:'#000', padding:'1px 4px', borderRadius:3, marginLeft:4 }}>STRONG</span>}
                    </div>
                    <div style={{ textAlign:'right' }}>
                      <div style={{ fontSize:10, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>
                        {(s.put_oi/1000).toFixed(0)}K
                      </div>
                      <div style={{ fontSize:9, color: s.put_oi_change >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                        {s.put_oi_change >= 0 ? '+' : ''}{(s.put_oi_change/1000).toFixed(1)}K chg
                      </div>
                    </div>
                  </div>
                ))}
                {(!oi.supports || oi.supports.length === 0) && <div style={{ fontSize:10, color:V('text-muted') }}>No data</div>}
              </div>

              {/* Resistance Levels */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:10, fontWeight:600, color:V('red'), textTransform:'uppercase', marginBottom:8 }}>
                  OI Resistance Levels (Call OI)
                </div>
                {(oi.resistances || []).map((r, i) => (
                  <div key={i} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', borderBottom:`1px solid ${V('border-light')}` }}>
                    <div>
                      <span style={{ fontSize:14, fontWeight:700, fontFamily:"'JetBrains Mono', monospace", color:V('red') }}>
                        {r.strike?.toLocaleString()}
                      </span>
                      {r.strength === 'strong' && <span style={{ fontSize:8, background:V('red'), color:'#fff', padding:'1px 4px', borderRadius:3, marginLeft:4 }}>STRONG</span>}
                    </div>
                    <div style={{ textAlign:'right' }}>
                      <div style={{ fontSize:10, color:V('text-primary'), fontFamily:"'JetBrains Mono', monospace" }}>
                        {(r.call_oi/1000).toFixed(0)}K
                      </div>
                      <div style={{ fontSize:9, color: r.call_oi_change >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                        {r.call_oi_change >= 0 ? '+' : ''}{(r.call_oi_change/1000).toFixed(1)}K chg
                      </div>
                    </div>
                  </div>
                ))}
                {(!oi.resistances || oi.resistances.length === 0) && <div style={{ fontSize:10, color:V('text-muted') }}>No data</div>}
              </div>

              {/* OI Change & Signals */}
              <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-md'), padding:12 }}>
                <div style={{ fontSize:10, fontWeight:600, color:V('orange'), textTransform:'uppercase', marginBottom:8 }}>
                  OI Change & Signals
                </div>
                <div style={{ fontSize:10, color:V('text-secondary'), lineHeight:1.5, marginBottom:8 }}>
                  {oi.oi_change_text}
                </div>
                <div style={{ display:'flex', gap:8, marginBottom:8 }}>
                  <div style={{ flex:1, padding:6, background:V('bg-card'), borderRadius:4 }}>
                    <div style={{ fontSize:8, color:V('text-muted') }}>Call OI Chg</div>
                    <div style={{ fontSize:13, fontWeight:700, color: (oi.total_call_oi_change||0) >= 0 ? V('red') : V('green'), fontFamily:"'JetBrains Mono', monospace" }}>
                      {oi.total_call_oi_change >= 0 ? '+' : ''}{((oi.total_call_oi_change||0)/1000).toFixed(0)}K
                    </div>
                  </div>
                  <div style={{ flex:1, padding:6, background:V('bg-card'), borderRadius:4 }}>
                    <div style={{ fontSize:8, color:V('text-muted') }}>Put OI Chg</div>
                    <div style={{ fontSize:13, fontWeight:700, color: (oi.total_put_oi_change||0) >= 0 ? V('green') : V('red'), fontFamily:"'JetBrains Mono', monospace" }}>
                      {oi.total_put_oi_change >= 0 ? '+' : ''}{((oi.total_put_oi_change||0)/1000).toFixed(0)}K
                    </div>
                  </div>
                </div>

                {/* Trading Signals */}
                {(oi.signals || []).map((sig, i) => (
                  <div key={i} style={{ fontSize:9, color:V('text-secondary'), lineHeight:1.5, paddingLeft:8, borderLeft:`2px solid ${V('orange')}`, marginBottom:4 }}>
                    {sig}
                  </div>
                ))}

                {/* OI Range */}
                <div style={{ marginTop:8, padding:6, background:V('bg-card'), borderRadius:4, textAlign:'center' }}>
                  <div style={{ fontSize:8, color:V('text-muted'), textTransform:'uppercase' }}>OI-Based Range</div>
                  <div style={{ fontSize:13, fontWeight:700, color:V('text-head'), fontFamily:"'JetBrains Mono', monospace" }}>
                    {oi.oi_range}
                  </div>
                </div>
              </div>
            </div>

            {/* Row 3: Strike Table (near ATM) */}
            {oi.strikes_table && oi.strikes_table.length > 0 && (
              <div>
                <div style={{ fontSize:10, fontWeight:600, color:V('text-head'), textTransform:'uppercase', marginBottom:8 }}>
                  Option Chain — Near ATM Strikes
                </div>
                <div style={{ overflowX:'auto' }}>
                  <table style={{ width:'100%', fontSize:10, borderCollapse:'collapse', fontFamily:"'JetBrains Mono', monospace" }}>
                    <thead>
                      <tr style={{ borderBottom:`2px solid ${V('border')}` }}>
                        <th style={{ padding:'6px 4px', textAlign:'right', color:V('text-muted'), fontSize:8, fontWeight:600 }}>OI</th>
                        <th style={{ padding:'6px 4px', textAlign:'right', color:V('text-muted'), fontSize:8, fontWeight:600 }}>OI Chg</th>
                        <th style={{ padding:'6px 4px', textAlign:'right', color:V('text-muted'), fontSize:8, fontWeight:600 }}>Vol</th>
                        <th style={{ padding:'6px 4px', textAlign:'right', color:V('text-muted'), fontSize:8, fontWeight:600 }}>IV</th>
                        <th style={{ padding:'6px 4px', textAlign:'right', color:V('text-muted'), fontSize:8, fontWeight:600 }}>CE LTP</th>
                        <th style={{ padding:'6px 8px', textAlign:'center', color:V('orange'), fontSize:9, fontWeight:700, background:V('bg-tertiary') }}>STRIKE</th>
                        <th style={{ padding:'6px 4px', textAlign:'left', color:V('text-muted'), fontSize:8, fontWeight:600 }}>PE LTP</th>
                        <th style={{ padding:'6px 4px', textAlign:'left', color:V('text-muted'), fontSize:8, fontWeight:600 }}>IV</th>
                        <th style={{ padding:'6px 4px', textAlign:'left', color:V('text-muted'), fontSize:8, fontWeight:600 }}>Vol</th>
                        <th style={{ padding:'6px 4px', textAlign:'left', color:V('text-muted'), fontSize:8, fontWeight:600 }}>OI Chg</th>
                        <th style={{ padding:'6px 4px', textAlign:'left', color:V('text-muted'), fontSize:8, fontWeight:600 }}>OI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {oi.strikes_table.map((row, i) => {
                        const isATM = row.is_atm
                        const rowBg = isATM ? V('orange')+'15' : i%2===0 ? 'transparent' : V('bg-tertiary')+'40'
                        return (
                          <tr key={i} style={{ background:rowBg, borderBottom:`1px solid ${V('border-light')}` }}>
                            <td style={{ padding:'5px 4px', textAlign:'right', color:V('text-primary') }}>{(row.ce_oi/1000).toFixed(1)}K</td>
                            <td style={{ padding:'5px 4px', textAlign:'right', color: row.ce_oi_chg>=0 ? V('red') : V('green') }}>
                              {row.ce_oi_chg>=0?'+':''}{(row.ce_oi_chg/1000).toFixed(1)}K
                            </td>
                            <td style={{ padding:'5px 4px', textAlign:'right', color:V('text-secondary') }}>{(row.ce_vol/1000).toFixed(0)}K</td>
                            <td style={{ padding:'5px 4px', textAlign:'right', color:V('text-secondary') }}>{row.ce_iv}%</td>
                            <td style={{ padding:'5px 4px', textAlign:'right', color:V('text-primary'), fontWeight:600 }}>{row.ce_ltp}</td>
                            <td style={{ padding:'5px 8px', textAlign:'center', fontWeight:800, color: isATM ? V('orange') : V('text-head'), fontSize: isATM ? 11 : 10, background:V('bg-tertiary') }}>
                              {row.strike?.toLocaleString()}{isATM ? ' ATM' : ''}
                            </td>
                            <td style={{ padding:'5px 4px', textAlign:'left', color:V('text-primary'), fontWeight:600 }}>{row.pe_ltp}</td>
                            <td style={{ padding:'5px 4px', textAlign:'left', color:V('text-secondary') }}>{row.pe_iv}%</td>
                            <td style={{ padding:'5px 4px', textAlign:'left', color:V('text-secondary') }}>{(row.pe_vol/1000).toFixed(0)}K</td>
                            <td style={{ padding:'5px 4px', textAlign:'left', color: row.pe_oi_chg>=0 ? V('green') : V('red') }}>
                              {row.pe_oi_chg>=0?'+':''}{(row.pe_oi_chg/1000).toFixed(1)}K
                            </td>
                            <td style={{ padding:'5px 4px', textAlign:'left', color:V('text-primary') }}>{(row.pe_oi/1000).toFixed(1)}K</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* OI Totals footer */}
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:10, padding:'8px 12px', background:V('bg-tertiary'), borderRadius:V('radius-sm') }}>
              <div style={{ fontSize:9, color:V('text-muted') }}>
                Total Call OI: <span style={{ fontWeight:700, color:V('red') }}>{((oi.total_call_oi||0)/100000).toFixed(2)}L</span>
                <span style={{ margin:'0 8px', color:V('border') }}>|</span>
                Total Put OI: <span style={{ fontWeight:700, color:V('green') }}>{((oi.total_put_oi||0)/100000).toFixed(2)}L</span>
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                <span style={{ fontSize:9, color:V('text-muted') }}>OI Verdict:</span>
                <span style={{ fontSize:12, fontWeight:800, color:
                  oi.oi_verdict?.includes('bullish') ? V('green') : oi.oi_verdict?.includes('bearish') ? V('red') : V('yellow'),
                  fontFamily:"'JetBrains Mono', monospace"
                }}>
                  {oi.verdict_text}
                </span>
                <span style={{ fontSize:9, color:V('text-muted') }}>
                  ({oi.bullish_points}B / {oi.bearish_points}Be)
                </span>
              </div>
            </div>
          </>
        ) : (
          <div style={{ textAlign:'center', padding:24, color:V('text-muted') }}>
            <div style={{ fontSize:12, marginBottom:4 }}>
              {oi.status === 'unavailable' ? 'OI data unavailable' : 'Loading OI analysis...'}
            </div>
            {oi.error && <div style={{ fontSize:10, color:V('red') }}>{oi.error}</div>}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Options Awareness Tiles for Live Trading ────────────────────────────────

function StrikeSelectorPanel({ data, direction, onDirectionChange, loading }) {
  if (!data || data.status !== 'ok') return (
    <Card style={{ minHeight:120 }}>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:8, letterSpacing:1 }}>Strike Selector</div>
      <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:20 }}>
        {loading ? 'Loading...' : 'Connect to Dhan to view strike recommendations'}
      </div>
    </Card>
  )
  const rawStrikes = data.recommended_strikes || []
  const targetType = direction === 'LONG' ? 'CE' : 'PE'
  const strikes = rawStrikes.filter(s => s.type === targetType)
  const spot = data.spot_price || 0
  const atm = data.atm_strike || 0
  const dte = data.dte
  const expiry = data.expiry || ''
  const urgency = data.expiry_urgency || 'normal'
  const urgencyColor = urgency === 'expiry_day' ? V('red') : urgency === 'critical' ? V('red') : urgency === 'high' ? V('yellow') : V('green')

  // Show 7 strikes centered on ATM
  const display = strikes.slice(0, 7)

  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, letterSpacing:1 }}>Strike Selector</div>
          <div style={{ display:'flex', gap:2 }}>
            {['LONG','SHORT'].map(d => (
              <button key={d} onClick={() => onDirectionChange(d)} style={{
                background: direction === d ? (d === 'LONG' ? V('green') : V('red')) : V('bg-tertiary'),
                color: direction === d ? '#fff' : V('text-muted'),
                border:'none', borderRadius:V('radius-sm'), padding:'3px 10px', fontSize:10, fontWeight:600, cursor:'pointer'
              }}>{d === 'LONG' ? 'CE (Buy)' : 'PE (Buy)'}</button>
            ))}
          </div>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <span style={{ color:V('text-muted'), fontSize:10 }}>Exp: {expiry}</span>
          {dte != null && <Badge label={`${dte}d`} color={urgencyColor} />}
        </div>
      </div>

      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
          <thead>
            <tr style={{ borderBottom:`1px solid ${V('border')}` }}>
              <th style={{ padding:'4px 6px', textAlign:'left', color:V('text-muted'), fontWeight:500, fontSize:10 }}>Strike</th>
              <th style={{ padding:'4px 6px', textAlign:'right', color:V('text-muted'), fontWeight:500, fontSize:10 }}>LTP</th>
              <th style={{ padding:'4px 6px', textAlign:'right', color:V('text-muted'), fontWeight:500, fontSize:10 }}>Delta</th>
              <th style={{ padding:'4px 6px', textAlign:'right', color:V('text-muted'), fontWeight:500, fontSize:10 }}>Theta/hr</th>
              <th style={{ padding:'4px 6px', textAlign:'right', color:V('text-muted'), fontWeight:500, fontSize:10 }}>IV%</th>
              <th style={{ padding:'4px 6px', textAlign:'right', color:V('text-muted'), fontWeight:500, fontSize:10 }}>B/E</th>
              <th style={{ padding:'4px 6px', textAlign:'left', color:V('text-muted'), fontWeight:500, fontSize:10 }}>Tag</th>
            </tr>
          </thead>
          <tbody>
            {display.map((s, i) => {
              const isAtm = s.is_atm
              const rowBg = isAtm ? `color-mix(in srgb, ${V('accent')} 8%, transparent)` : 'transparent'
              return (
                <tr key={i} style={{ background:rowBg, borderBottom:`1px solid color-mix(in srgb, ${V('border')} 50%, transparent)` }}>
                  <td style={{ padding:'5px 6px', fontFamily:"'JetBrains Mono',monospace", fontWeight: isAtm ? 700 : 400, color: isAtm ? V('accent') : V('text-primary') }}>
                    {s.strike} <span style={{ fontSize:9, color:V('text-muted') }}>{s.moneyness}</span>
                  </td>
                  <td style={{ padding:'5px 6px', textAlign:'right', fontFamily:"'JetBrains Mono',monospace", color:V('text-primary'), fontWeight:600 }}>
                    {s.ltp > 0 ? s.ltp.toFixed(1) : '—'}
                  </td>
                  <td style={{ padding:'5px 6px', textAlign:'right', fontFamily:"'JetBrains Mono',monospace", color: s.delta >= 0.5 ? V('green') : s.delta >= 0.3 ? V('yellow') : V('text-muted') }}>
                    {s.delta.toFixed(2)}
                  </td>
                  <td style={{ padding:'5px 6px', textAlign:'right', fontFamily:"'JetBrains Mono',monospace", color:V('red'), fontSize:10 }}>
                    -{s.theta_per_hour.toFixed(1)}
                  </td>
                  <td style={{ padding:'5px 6px', textAlign:'right', fontFamily:"'JetBrains Mono',monospace", color:V('text-secondary') }}>
                    {s.iv > 0 ? s.iv.toFixed(1) : '—'}
                  </td>
                  <td style={{ padding:'5px 6px', textAlign:'right', fontFamily:"'JetBrains Mono',monospace", color:V('text-secondary'), fontSize:10 }}>
                    {s.breakeven > 0 ? fmt(s.breakeven) : '—'}
                  </td>
                  <td style={{ padding:'5px 6px' }}>
                    {s.tags && s.tags.map((t, j) => <span key={j} style={{
                      background: t.includes('ATM') ? `color-mix(in srgb, ${V('accent')} 15%, transparent)` :
                                  t.includes('Safe') ? `color-mix(in srgb, ${V('green')} 15%, transparent)` :
                                  t.includes('Value') ? `color-mix(in srgb, ${V('yellow')} 15%, transparent)` :
                                  `color-mix(in srgb, ${V('purple')} 15%, transparent)`,
                      color: t.includes('ATM') ? V('accent') : t.includes('Safe') ? V('green') : t.includes('Value') ? V('yellow') : V('purple'),
                      fontSize:9, padding:'1px 5px', borderRadius:3, fontWeight:500
                    }}>{t}</span>)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display:'flex', gap:12, marginTop:8, fontSize:10, color:V('text-muted'), flexWrap:'wrap' }}>
        <span>Spot: <b style={{ color:V('text-primary') }}>{fmt(spot)}</b></span>
        <span>ATM: <b style={{ color:V('accent') }}>{atm}</b></span>
        <span style={{ fontStyle:'italic' }}>
          {direction === 'LONG' ? 'Showing CE strikes — delta 0.50+ = safer, 0.30-0.45 = cheaper with more risk' : 'Showing PE strikes — delta 0.50+ = safer, 0.30-0.45 = cheaper with more risk'}
        </span>
      </div>
    </Card>
  )
}

// Helper to find best strike for CE or PE
function findBestStrike(strikes, type, dte, ivLevel) {
  const dirStrikes = strikes.filter(s => s.type === type)
  let bestStrike = null
  let bestScore = -Infinity

  for (const s of dirStrikes) {
    let score = 0
    const d = s.delta

    // 1. General delta preferences
    if (d >= 0.45 && d <= 0.60) score += 5
    else if (d >= 0.35 && d < 0.45) score += 3
    else if (d > 0.60 && d <= 0.70) score += 3
    else if (d >= 0.25 && d < 0.35) score += 1
    else if (d > 0.70) score += 1

    // 2. Tag-based preference
    if (s.tags) {
      if (s.tags.includes('Safe ITM')) score += 4
      if (s.tags.includes('ATM pick')) score += 3
      if (s.tags.includes('Value OTM')) score += 1
    }

    // 3. Near Expiry Safety Analysis (DTE <= 1)
    if (dte != null && dte <= 1) {
      if (s.moneyness === 'ITM' || s.tags?.includes('Safe ITM')) {
        score += 8  // Boost Safe ITM near expiry to guard against theta decay
      } else if (s.moneyness === 'OTM') {
        score -= 10 // Heavily penalize OTM due to extreme decay risk
      }
    }

    // 4. Low IV Context Gearing
    if (ivLevel === 'low') {
      if (s.moneyness === 'ITM') score += 4
    }

    // 5. Theta efficiency (theta per hour relative to premium ltp)
    if (s.ltp > 0 && s.theta_per_hour > 0) {
      const thetaRatio = s.theta_per_hour / s.ltp
      if (thetaRatio < 0.03) score += 3
      else if (thetaRatio > 0.06) score -= 4
    }

    // 6. Liquidity (Open Interest)
    if (s.oi > 100000) score += 1

    if (score > bestScore) {
      bestScore = score
      bestStrike = s
    }
  }

  // Fallback to ATM if no good match
  if (!bestStrike && dirStrikes.length > 0) {
    bestStrike = dirStrikes.find(s => s.is_atm) || dirStrikes[0]
  }

  return bestStrike
}

// ── Optimized Strike Recommendation Panel ───────────────────────────────────
function OptimizedStrikePanel({ data, loading }) {
  if (!data || data.status !== 'ok') return (
    <Card style={{ minHeight: 80, borderLeft: `3px solid ${V('accent')}` }}>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:6, letterSpacing:1, display:'flex', alignItems:'center', gap:6 }}>
        <span style={{ fontSize:14 }}>🎯</span> Optimized Strike
      </div>
      <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:12 }}>
        {loading ? 'Analyzing options chain...' : (data?.error || 'Connect to Dhan to view strike recommendation')}
      </div>
    </Card>
  )

  const oi = data.oi_context || {}
  const iv = data.iv_context || {}
  const strikes = data.recommended_strikes || []
  const spot = data.spot_price || 0
  const dte = data.dte

  // ── Step 1: Calculate direction scores from OI analysis ──
  let dirScore = 0 // positive = bullish (CE), negative = bearish (PE)
  let bullishScore = 0
  let bearishScore = 0
  const reasons = []

  // PCR signal
  const pcr = oi.pcr_signal || ''
  if (pcr === 'strongly_bullish') { dirScore += 3; bullishScore += 3; reasons.push('strongly bullish PCR') }
  else if (pcr === 'bullish') { dirScore += 2; bullishScore += 2; reasons.push('bullish PCR (put support)') }
  else if (pcr === 'strongly_bearish') { dirScore -= 3; bearishScore += 3; reasons.push('strongly bearish PCR') }
  else if (pcr === 'bearish') { dirScore -= 2; bearishScore += 2; reasons.push('bearish PCR (call resistance)') }

  // OI verdict
  const verdict = (oi.oi_verdict || '').toLowerCase()
  if (verdict.includes('strongly') && verdict.includes('bullish')) { dirScore += 3; bullishScore += 3; reasons.push('strongly bullish OI verdict') }
  else if (verdict.includes('bullish')) { dirScore += 2; bullishScore += 2; reasons.push('bullish OI verdict') }
  else if (verdict.includes('strongly') && verdict.includes('bearish')) { dirScore -= 3; bearishScore += 3; reasons.push('strongly bearish OI verdict') }
  else if (verdict.includes('bearish')) { dirScore -= 2; bearishScore += 2; reasons.push('bearish OI verdict') }

  // IV skew: positive skew = puts expensive = bearish; negative = calls expensive = bullish
  if (oi.iv_skew != null) {
    if (oi.iv_skew > 3) { dirScore -= 1; bearishScore += 1 }
    else if (oi.iv_skew < -3) { dirScore += 1; bullishScore += 1 }
  }

  // Max pain: if spot is above max pain, gravity pulls down (bearish); below = bullish
  if (oi.max_pain_distance != null) {
    if (oi.max_pain_distance > 200) { dirScore -= 1; bearishScore += 1 }
    else if (oi.max_pain_distance < -200) { dirScore += 1; bullishScore += 1 }
  }

  // Support/resistance proximity
  const immSup = typeof oi.immediate_support === 'object' ? oi.immediate_support?.strike : oi.immediate_support
  const immRes = typeof oi.immediate_resistance === 'object' ? oi.immediate_resistance?.strike : oi.immediate_resistance
  if (immSup && immRes && spot > 0) {
    const distToSup = spot - immSup
    const distToRes = immRes - spot
    if (distToSup < distToRes * 0.5) { dirScore += 1; bullishScore += 1 }
    else if (distToRes < distToSup * 0.5) { dirScore -= 1; bearishScore += 1 }
  }

  // Overall Consensus View
  const isBullish = dirScore > 0
  const isBearish = dirScore < 0
  const verdictLabel = isBullish ? 'BULLISH' : isBearish ? 'BEARISH' : 'NEUTRAL'
  const verdictColor = isBullish ? V('green') : isBearish ? V('red') : V('yellow')

  // ── Step 2: Extract best strikes for CE & PE ──
  const bestCe = findBestStrike(strikes, 'CE', dte, iv.iv_level)
  const bestPe = findBestStrike(strikes, 'PE', dte, iv.iv_level)

  // Confidence scores
  const ceConfidenceVal = Math.min(10, Math.max(1, bullishScore * 2))
  const peConfidenceVal = Math.min(10, Math.max(1, bearishScore * 2))

  const getConfidenceLabel = (v) => v >= 7 ? 'High' : v >= 4 ? 'Moderate' : 'Low'
  const getConfidenceColor = (v) => v >= 7 ? V('green') : v >= 4 ? V('yellow') : V('text-muted')

  const ceConfidence = getConfidenceLabel(ceConfidenceVal)
  const ceConfidenceColor = getConfidenceColor(ceConfidenceVal)

  const peConfidence = getConfidenceLabel(peConfidenceVal)
  const peConfidenceColor = getConfidenceColor(peConfidenceVal)

  // Rationale text builders
  const getRationaleText = (bestOption, type) => {
    if (!bestOption) return 'No strikes available'
    const rationale = []
    if (bestOption.tags && bestOption.tags.length > 0) {
      rationale.push(bestOption.tags[0])
    }
    if (iv.iv_level === 'low') rationale.push('IV low (cheap)')
    else if (iv.iv_level === 'high') rationale.push('IV high (expensive)')

    if (dte != null && dte <= 1) {
      rationale.push(bestOption.moneyness === 'ITM' ? 'ITM (decay protected)' : 'OTM (high decay)')
    }
    return rationale.join(' • ')
  }

  const ceRationaleText = getRationaleText(bestCe, 'CE')
  const peRationaleText = getRationaleText(bestPe, 'PE')

  if (!bestCe && !bestPe) {
    return (
      <Card style={{ minHeight: 80, borderLeft: `3px solid ${V('accent')}` }}>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:6, letterSpacing:1, display:'flex', alignItems:'center', gap:6 }}>
          <span style={{ fontSize:14 }}>🎯</span> Optimized Strike
        </div>
        <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:12 }}>
          No options strikes data available in data feed.
        </div>
      </Card>
    )
  }

  return (
    <Card style={{ borderLeft: `3px solid ${verdictColor}`, background: `color-mix(in srgb, ${verdictColor} 3%, ${V('bg-card')})`, padding: 14, height:'100%', boxSizing:'border-box', display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
      {/* Overall View Header */}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', borderBottom:`1px solid ${V('border')}`, paddingBottom:8, marginBottom:12 }}>
        <div style={{ color:V('text-muted'), fontSize:10, textTransform:'uppercase', fontWeight:600, letterSpacing:1, display:'flex', alignItems:'center', gap:5 }}>
          <span style={{ fontSize:13 }}>🎯</span> Overall Option Analysis View
        </div>
        <span style={{
          background: `color-mix(in srgb, ${verdictColor} 15%, transparent)`,
          color: verdictColor, fontSize:10, padding:'3px 8px', borderRadius:4, fontWeight:700, textTransform:'uppercase', letterSpacing:1
        }}>
          {verdictLabel} Consensus
        </span>
      </div>

      {/* Columns Grid */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 }}>
        {/* Left Column: Long / CE Side */}
        <div style={{ borderRight:`1px solid ${V('border')}`, paddingRight:12 }}>
          <div style={{ color:V('text-muted'), fontSize:10, textTransform:'uppercase', fontWeight:600, marginBottom:8, display:'flex', justifyContent:'space-between' }}>
            <span>Long / CE Side</span>
            {bestCe && <span style={{ fontSize:8, opacity:0.8, color:V('green') }}>{bestCe.moneyness}</span>}
          </div>
          {bestCe ? (
            <>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', marginBottom:4 }}>
                <span style={{ color:V('green'), fontSize:20, fontFamily:"'JetBrains Mono',monospace", fontWeight:800 }}>
                  {bestCe.strike} CE
                </span>
                <span style={{ color:V('text-primary'), fontSize:14, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
                  ₹{bestCe.ltp > 0 ? bestCe.ltp.toFixed(1) : '—'}
                </span>
              </div>
              <div style={{ display:'flex', gap:6, fontSize:9, color:V('text-muted'), marginBottom:8 }}>
                <span>Δ {bestCe.delta.toFixed(2)}</span>
                <span>•</span>
                <span>B/E {bestCe.breakeven.toFixed(0)}</span>
              </div>
              <div style={{ fontSize:10, color:V('text-secondary'), background:V('bg-tertiary'), padding:'4px 6px', borderRadius:3, minHeight:32, display:'flex', alignItems:'center', marginBottom:8, lineHeight:'1.3' }}>
                {ceRationaleText}
              </div>
              <div style={{ fontSize:10, color:V('text-muted') }}>
                Confidence: <b style={{ color:ceConfidenceColor }}>{ceConfidence}</b> ({ceConfidenceVal}/10)
              </div>
            </>
          ) : (
            <div style={{ color:V('text-muted'), fontSize:11, padding:'10px 0' }}>No CE recommendations</div>
          )}
        </div>

        {/* Right Column: Short / PE Side */}
        <div style={{ paddingLeft:4 }}>
          <div style={{ color:V('text-muted'), fontSize:10, textTransform:'uppercase', fontWeight:600, marginBottom:8, display:'flex', justifyContent:'space-between' }}>
            <span>Short / PE Side</span>
            {bestPe && <span style={{ fontSize:8, opacity:0.8, color:V('red') }}>{bestPe.moneyness}</span>}
          </div>
          {bestPe ? (
            <>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', marginBottom:4 }}>
                <span style={{ color:V('red'), fontSize:20, fontFamily:"'JetBrains Mono',monospace", fontWeight:800 }}>
                  {bestPe.strike} PE
                </span>
                <span style={{ color:V('text-primary'), fontSize:14, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
                  ₹{bestPe.ltp > 0 ? bestPe.ltp.toFixed(1) : '—'}
                </span>
              </div>
              <div style={{ display:'flex', gap:6, fontSize:9, color:V('text-muted'), marginBottom:8 }}>
                <span>Δ {bestPe.delta.toFixed(2)}</span>
                <span>•</span>
                <span>B/E {bestPe.breakeven.toFixed(0)}</span>
              </div>
              <div style={{ fontSize:10, color:V('text-secondary'), background:V('bg-tertiary'), padding:'4px 6px', borderRadius:3, minHeight:32, display:'flex', alignItems:'center', marginBottom:8, lineHeight:'1.3' }}>
                {peRationaleText}
              </div>
              <div style={{ fontSize:10, color:V('text-muted') }}>
                Confidence: <b style={{ color:peConfidenceColor }}>{peConfidence}</b> ({peConfidenceVal}/10)
              </div>
            </>
          ) : (
            <div style={{ color:V('text-muted'), fontSize:11, padding:'10px 0' }}>No PE recommendations</div>
          )}
        </div>
      </div>
    </Card>
  )
}

function GreeksDecayDashboard({ data, loading }) {
  if (!data || data.status !== 'ok') return (
    <Card style={{ minHeight:120 }}>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:8, letterSpacing:1 }}>Greeks & Decay</div>
      <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:20 }}>
        {loading ? 'Loading...' : (data?.error || 'Connect to Dhan to view Greeks')}
      </div>
    </Card>
  )

  const iv = data.iv_context || {}
  const theta = data.theta_context || {}
  const em = data.expected_move || {}
  const atm = data.atm_greeks || {}
  const dte = data.dte
  const urgency = data.expiry_urgency || 'normal'

  const ivColor = iv.iv_level === 'high' ? V('red') : iv.iv_level === 'low' ? V('green') : V('yellow')
  const dteColor = urgency === 'expiry_day' || urgency === 'critical' ? V('red') : urgency === 'high' ? V('yellow') : V('green')

  return (
    <Card>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:10, letterSpacing:1 }}>Greeks & Decay Dashboard</div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
        {/* IV Context */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:3 }}>ATM IV</div>
          <div style={{ display:'flex', alignItems:'baseline', gap:6 }}>
            <span style={{ color:ivColor, fontSize:18, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>{iv.atm_iv > 0 ? iv.atm_iv.toFixed(1) + '%' : '—'}</span>
            <span style={{ color:ivColor, fontSize:9, fontWeight:500, textTransform:'uppercase' }}>{iv.iv_level}</span>
          </div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:3, lineHeight:'1.3' }}>{iv.iv_label || ''}</div>
        </div>

        {/* Days to Expiry */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:3 }}>Days to Expiry</div>
          <div style={{ color:dteColor, fontSize:18, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
            {dte != null ? dte : '—'}
          </div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:3 }}>
            {urgency === 'expiry_day' ? 'EXPIRY DAY — max theta decay' :
             urgency === 'critical' ? 'Tomorrow expiry — rapid decay' :
             urgency === 'high' ? 'Decay accelerating' :
             'Normal decay regime'}
          </div>
        </div>

        {/* Expected Move */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:3 }}>Expected Move</div>
          {em.points ? (
            <>
              <div style={{ color:V('accent'), fontSize:16, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
                ±{em.points} <span style={{ fontSize:10, color:V('text-muted') }}>({em.pct}%)</span>
              </div>
              <div style={{ color:V('text-muted'), fontSize:9, marginTop:3 }}>
                Range: {fmt(em.range_low)} – {fmt(em.range_high)}
              </div>
            </>
          ) : <div style={{ color:V('text-muted'), fontSize:12 }}>—</div>}
        </div>

        {/* ATM Theta Decay */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:3 }}>ATM Theta Decay</div>
          {atm.theta ? (
            <>
              <div style={{ display:'flex', alignItems:'baseline', gap:6 }}>
                <span style={{ color:V('red'), fontSize:16, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
                  ₹{Math.abs(atm.theta).toFixed(1)}<span style={{ fontSize:10 }}>/day</span>
                </span>
              </div>
              <div style={{ color:V('text-muted'), fontSize:9, marginTop:3 }}>
                ₹{theta.atm_hourly_decay || 0}/hr • {theta.pct_of_premium || 0}% of premium
              </div>
            </>
          ) : <div style={{ color:V('text-muted'), fontSize:12 }}>—</div>}
        </div>
      </div>

      {/* ATM Greeks row */}
      {atm.delta > 0 && (
        <div style={{ display:'flex', gap:8, marginTop:8, flexWrap:'wrap' }}>
          <Badge label={`Δ ${atm.delta.toFixed(2)}`} color={V('accent')} />
          <Badge label={`Γ ${atm.gamma.toFixed(4)}`} color={V('purple')} />
          <Badge label={`V ${atm.vega.toFixed(1)}`} color={V('cyan')} />
          <Badge label={`Premium ₹${atm.premium?.toFixed(1) || '—'}`} color={V('text-secondary')} />
        </div>
      )}

      {theta.decay_warning && (
        <div style={{ marginTop:6, fontSize:10, color: theta.decay_warning.includes('Rapid') ? V('red') : theta.decay_warning.includes('High') ? V('yellow') : V('green'), fontWeight:500 }}>
          {theta.decay_warning}
        </div>
      )}
    </Card>
  )
}

function PositionGreeksPanel({ data, tradeState, loading }) {
  const m = window.innerWidth < 768
  const hasPosition = tradeState?.position && !tradeState.position.order_id?.startsWith('PAPER_')
  const optData = data?.status === 'ok' ? data : null

  if (!hasPosition) {
    return (
      <Card style={{ minHeight:100 }}>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:8, letterSpacing:1 }}>Position Greeks</div>
        <div style={{ textAlign:'center', padding:'12px 0' }}>
          <div style={{ color:V('text-muted'), fontSize:12 }}>No active position</div>
          <div style={{ color:V('text-muted'), fontSize:10, marginTop:4 }}>Greeks will appear here when you enter a trade</div>
        </div>
      </Card>
    )
  }

  const pos = tradeState.position
  const lots = pos.quantity || 1
  const entryPrice = pos.entry_price || 0
  const direction = pos.direction || 'LONG'
  const isCall = direction === 'LONG'

  // Find the closest strike to match position
  const strikes = optData?.recommended_strikes || []
  const atm = optData?.atm_greeks || {}
  const spot = optData?.spot_price || 0

  // Use ATM Greeks as proxy (best we can do without knowing exact strike)
  const delta = atm.delta || 0.5
  const theta = atm.theta || 0
  const gamma = atm.gamma || 0
  const vega = atm.vega || 0
  const thetaPerHour = atm.theta_per_hour || 0

  // Position-level Greeks (scaled by lots)
  const posDelta = delta * lots
  const posTheta = theta * lots
  const posGamma = gamma * lots

  // P&L per index point move
  const pnlPerPoint = delta * lots

  // Theta cost per hour
  const thetaCostHour = Math.abs(thetaPerHour) * lots

  // Breakeven considering theta (hours for theta to eat 1 point of delta profit)
  const hoursToEatOnePoint = delta > 0 && thetaPerHour > 0 ? (1 / (Math.abs(thetaPerHour) / (delta || 1))).toFixed(1) : '—'

  return (
    <Card style={{ borderLeft:`3px solid ${isCall ? V('green') : V('red')}` }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, letterSpacing:1 }}>Position Greeks</div>
        <Badge label={`${direction} × ${lots}`} color={isCall ? V('green') : V('red')} />
      </div>

      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1fr', gap:8 }}>
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10 }}>P&L / Index Pt</div>
          <div style={{ color:V('green'), fontSize:15, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
            ₹{pnlPerPoint.toFixed(1)}
          </div>
          <div style={{ color:V('text-muted'), fontSize:9 }}>per 1pt move</div>
        </div>

        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10 }}>Theta Cost</div>
          <div style={{ color:V('red'), fontSize:15, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
            -₹{thetaCostHour.toFixed(1)}<span style={{ fontSize:9 }}>/hr</span>
          </div>
          <div style={{ color:V('text-muted'), fontSize:9 }}>time decay</div>
        </div>

        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10 }}>Pos Delta</div>
          <div style={{ color:V('accent'), fontSize:15, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
            {posDelta.toFixed(2)}
          </div>
          <div style={{ color:V('text-muted'), fontSize:9 }}>exposure</div>
        </div>
      </div>

      <div style={{ display:'flex', gap:6, marginTop:8, flexWrap:'wrap' }}>
        <Badge label={`Pos Γ ${posGamma.toFixed(4)}`} color={V('purple')} />
        <Badge label={`Pos Θ ${posTheta.toFixed(1)}/day`} color={V('red')} />
        {entryPrice > 0 && <Badge label={`Entry ₹${entryPrice.toFixed(1)}`} color={V('text-secondary')} />}
      </div>

      <div style={{ marginTop:8, padding:'6px 10px', background:`color-mix(in srgb, ${V('yellow')} 8%, transparent)`, borderRadius:V('radius-sm'), fontSize:10, color:V('text-secondary'), lineHeight:'1.4' }}>
        Each hour costs you ₹{thetaCostHour.toFixed(1)} in theta. Index must move ~{hoursToEatOnePoint} pts/hr in your favor just to offset decay.
      </div>
    </Card>
  )
}

function LiveOiAnalysisPanel({ data, loading }) {
  const m = window.innerWidth < 768
  if (!data || data.status !== 'ok') return (
    <Card style={{ minHeight:120 }}>
      <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, marginBottom:8, letterSpacing:1 }}>OI Analysis</div>
      <div style={{ color:V('text-muted'), fontSize:12, textAlign:'center', padding:20 }}>
        {loading ? 'Loading...' : 'Connect to Dhan to view OI data'}
      </div>
    </Card>
  )

  const oi = data.oi_context || {}
  const iv = data.iv_context || {}
  const spot = data.spot_price || 0
  const pcrColor = oi.pcr_signal === 'bullish' ? V('green') : oi.pcr_signal === 'bearish' ? V('red') : V('yellow')
  const verdictColor = (oi.oi_verdict || '').includes('bullish') ? V('green') : (oi.oi_verdict || '').includes('bearish') ? V('red') : V('yellow')

  const supports = oi.supports || []
  const resistances = oi.resistances || []
  const signals = oi.signals || []

  return (
    <Card>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
        <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, letterSpacing:1 }}>OI Analysis</div>
        <Badge label={oi.verdict_text || 'N/A'} color={verdictColor} />
      </div>

      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : '1fr 1fr 1fr 1fr', gap:8 }}>
        {/* PCR */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:2 }}>PCR</div>
          <div style={{ color:pcrColor, fontSize:16, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>{oi.pcr || '—'}</div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:2, textTransform:'capitalize' }}>{oi.pcr_signal || ''}</div>
        </div>

        {/* Max Pain */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:2 }}>Max Pain</div>
          <div style={{ color:V('text-primary'), fontSize:16, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>{oi.max_pain || '—'}</div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:2 }}>
            {oi.max_pain_distance ? `${oi.max_pain_distance > 0 ? '+' : ''}${oi.max_pain_distance.toFixed(0)} pts` : ''}
          </div>
        </div>

        {/* IV Skew */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:2 }}>IV Skew</div>
          <div style={{ color: oi.iv_skew > 3 ? V('red') : oi.iv_skew < -3 ? V('green') : V('text-primary'), fontSize:16, fontFamily:"'JetBrains Mono',monospace", fontWeight:700 }}>
            {oi.iv_skew != null ? (oi.iv_skew > 0 ? '+' : '') + oi.iv_skew.toFixed(1) : '—'}
          </div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:2 }}>Put-Call IV diff</div>
        </div>

        {/* OI Range */}
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'8px 10px' }}>
          <div style={{ color:V('text-muted'), fontSize:10, marginBottom:2 }}>OI Range</div>
          <div style={{ color:V('text-primary'), fontSize:12, fontFamily:"'JetBrains Mono',monospace", fontWeight:600 }}>
            {oi.immediate_support ? fmt(typeof oi.immediate_support === 'object' ? oi.immediate_support.strike : oi.immediate_support) : '—'} – {oi.immediate_resistance ? fmt(typeof oi.immediate_resistance === 'object' ? oi.immediate_resistance.strike : oi.immediate_resistance) : '—'}
          </div>
          <div style={{ color:V('text-muted'), fontSize:9, marginTop:2 }}>Support – Resistance</div>
        </div>
      </div>

      {/* Support / Resistance levels */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginTop:8 }}>
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
          <div style={{ color:V('green'), fontSize:10, fontWeight:600, marginBottom:3 }}>OI Support Levels</div>
          <div style={{ display:'flex', gap:4, flexWrap:'wrap' }}>
            {supports.length > 0 ? supports.slice(0, 4).map((s, i) => (
              <span key={i} style={{ background:`color-mix(in srgb, ${V('green')} 12%, transparent)`, color:V('green'), fontSize:10, padding:'1px 5px', borderRadius:3, fontFamily:"'JetBrains Mono',monospace" }}>{s?.strike || s}</span>
            )) : <span style={{ color:V('text-muted'), fontSize:10 }}>—</span>}
          </div>
        </div>
        <div style={{ background:V('bg-tertiary'), borderRadius:V('radius-sm'), padding:'6px 10px' }}>
          <div style={{ color:V('red'), fontSize:10, fontWeight:600, marginBottom:3 }}>OI Resistance Levels</div>
          <div style={{ display:'flex', gap:4, flexWrap:'wrap' }}>
            {resistances.length > 0 ? resistances.slice(0, 4).map((r, i) => (
              <span key={i} style={{ background:`color-mix(in srgb, ${V('red')} 12%, transparent)`, color:V('red'), fontSize:10, padding:'1px 5px', borderRadius:3, fontFamily:"'JetBrains Mono',monospace" }}>{r?.strike || r}</span>
            )) : <span style={{ color:V('text-muted'), fontSize:10 }}>—</span>}
          </div>
        </div>
      </div>

      {/* OI Signals */}
      {signals.length > 0 && (
        <div style={{ marginTop:8, display:'flex', gap:4, flexWrap:'wrap' }}>
          {signals.map((sig, i) => {
            const sc = sig.toLowerCase().includes('bullish') || sig.toLowerCase().includes('call') ? V('green') :
                       sig.toLowerCase().includes('bearish') || sig.toLowerCase().includes('put') ? V('red') : V('yellow')
            return <span key={i} style={{ background:`color-mix(in srgb, ${sc} 10%, transparent)`, color:sc, fontSize:9, padding:'2px 6px', borderRadius:3, fontWeight:500 }}>{sig}</span>
          })}
        </div>
      )}

      <div style={{ marginTop:6, fontSize:10, color:V('text-muted'), lineHeight:'1.4' }}>
        {oi.iv_skew_text || ''}
      </div>
    </Card>
  )
}


// ── Main App ────────────────────────────────────────────────────────────────
function MainApp() {
  const { theme, toggle: toggleTheme } = useTheme()
  const [tab,         setTab]         = useState('dashboard')
  const [connected,   setConnected]   = useState(false)
  const [signal,      setSignal]      = useState(null)
  const [tradeState,  setTradeState]  = useState(null)
  const [balance,     setBalance]     = useState(0)
  const [livePnl,     setLivePnl]     = useState(0)
  const [todayPnl,    setTodayPnl]    = useState(0)
  const [lotSize,     setLotSize]     = useState(0)
  const [ltp,         setLtp]         = useState(null)
  const [instrument,  setInstrument]  = useState('BANKNIFTY')
  const [strategy,    setStrategy]    = useState(LIVE_STRATEGY_OPTIONS[0].v)
  const [researchLoadReq, setResearchLoadReq] = useState(null)
  // Backtest page's own strategy selection -- never the Live page's `strategy`
  // (Research Studio's "Backtest" button used to overwrite the live page's state).
  const [btStrategy, setBtStrategy] = useState(null)
  const [chartTf,     setChartTf]     = useState('5')
  const [sigHistory,  setSigHistory]  = useState([])
  const [chartSignals, setChartSignals] = useState([])
  // Live signals received today (signal_event). The chart's own reload (/api/chart_signals)
  // recomputes on a different data window than the live processor and could drop a signal
  // that was really sent (marker appeared, then vanished) -- these are merged back in.
  const liveChartSignalsRef = useRef({ key: '', day: '', list: [] })
  const [chartSignalsLoading, setChartSignalsLoading] = useState(false)
  const [allSignals,  setAllSignals]  = useState({})
  const [lastEntries, setLastEntries] = useState({})
  const [manualPositions, setManualPositions] = useState([])
  const [sigJournal,  setSigJournal]  = useState([])
  const [casAlertsA,  setCasAlertsA]  = useState([])  // Mode A (CAS Window) fired alerts
  const [casAlertsB,  setCasAlertsB]  = useState([])  // Mode B (Undercurrent, stock-sourced) fired alerts
  const [casAlertsC,  setCasAlertsC]  = useState([])  // Mode C (Index Burst) fired alerts
  const [casAtRisk,   setCasAtRisk]   = useState([])  // Mode A live shortlist
  const [casUndercurrent, setCasUndercurrent] = useState([])  // Mode B live flagged list
  const [casHeatmap, setCasHeatmap] = useState([])  // Mode B per-underlying heatmap, full F&O universe
  const [autoTrade,   setAutoTrade]   = useState(false)
  const [showSettings,setShowSettings]= useState(false)
  const [showConnect, setShowConnect] = useState(false)
  const [refreshChart,setRefreshChart]= useState(0)
  const [journal,     setJournal]     = useState([])
  const [journalInfo, setJournalInfo] = useState({})   // {status, error} from /api/journal
  const [activeTradeSignal, setActiveTradeSignal] = useState(null)
  const [appRunning,    setAppRunning]    = useState(true)
  const [capitalState, setCapitalState] = useState(null)
  const [dataHealth, setDataHealth] = useState(null)
  const [priceFrozen, setPriceFrozen] = useState(null)   // {price, since, minutes} while no live price
  const [market, setMarket] = useState(null)
  const [brokerAuthError, setBrokerAuthError] = useState(null)
  const [dhanTokens, setDhanTokens] = useState(null)
  const [telegramConfigured, setTelegramConfigured] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const isMobile = useIsMobile()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [maxDailyLoss, setMaxDailyLoss] = useState(0)
  const [maxDailyProfit, setMaxDailyProfit] = useState(0)
  const [optCtx, setOptCtx] = useState(null)
  const [optCtxLoading, setOptCtxLoading] = useState(false)
  const [optCtxDir, setOptCtxDir] = useState('LONG')
  const [optCtxExpiry, setOptCtxExpiry] = useState('')
  const [optCtxExpiries, setOptCtxExpiries] = useState([])
  const wsRef = useRef(null)
  const instrumentRef = useRef('BANKNIFTY')
  const strategyRef = useRef(LIVE_STRATEGY_OPTIONS[0].v)
  const fetchChartSignalsRef = useRef(null) // populated later, used by WS handler for instant chart refresh

  useEffect(() => { instrumentRef.current = instrument }, [instrument])
  useEffect(() => { strategyRef.current = strategy }, [strategy])

  const [journalFromDate, setJournalFromDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().split('T')[0]
  })
  const [journalToDate, setJournalToDate] = useState(() => {
    return new Date().toISOString().split('T')[0]
  })

  const fetchJournal = useCallback(async (overrideFromDate, overrideToDate) => {
    const fDate = overrideFromDate || journalFromDate
    const tDate = overrideToDate || journalToDate
    const res = await API.get(`/api/journal?from_date=${fDate}&to_date=${tDate}`).catch(()=>null)
    if (res && res.journal) {
      setJournal(res.journal)
      setJournalInfo({ status: res.status, error: res.error })
    } else {
      setJournalInfo({ error: 'Could not load broker trades from the server.' })
    }
  }, [journalFromDate, journalToDate])

  // WebSocket connection
  useEffect(() => {
    let delay = 1000
    let timerId = null
    let pingInterval = null

    const connect = () => {
      try {
        const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const ws = new WebSocket(`${wsProto}//${location.host}/ws`)
        
        ws.onopen = () => {
          delay = 1000 // Reset backoff on successful open
          if (pingInterval) clearInterval(pingInterval)
          pingInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              try { ws.send('ping') } catch(_) {}
            }
          }, 15000) // Send ping every 15s to keep TCP socket active
        }
        
        ws.onmessage = e => {
          if (e.data === 'pong') return
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'pong' || msg.type === 'heartbeat') return
            if (msg.type === 'candle_update') {
              if (!msg.instrument || msg.instrument === instrumentRef.current) {
                window.dispatchEvent(new CustomEvent('candle_update', { detail: msg.data }))
              }
            }
            if (msg.type === 'signal') {
              if (!msg.data?.instrument || msg.data.instrument === instrumentRef.current)
                setSignal(msg.data)
            }
            if (msg.type === 'state')        setTradeState(msg.data)
            if (msg.type === 'trade_opened') { setRefreshChart(r=>r+1) }
            if (msg.type === 'trade_closed') { setRefreshChart(r=>r+1) }
            if (msg.type === 'signal_event') {
              if ((!msg.data?.strategy || msg.data.strategy === strategyRef.current) &&
                  (!msg.data?.instrument || msg.data.instrument === instrumentRef.current)) {
                const store = liveChartSignalsRef.current
                const key = `${strategyRef.current}|${instrumentRef.current}`
                const day = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
                if (store.key !== key || store.day !== day) { store.key = key; store.day = day; store.list = [] }
                store.list.push(msg.data)
                setChartSignals(prev => [...(prev || []), msg.data])
                setRefreshChart(r => r + 1)
              }
            }
            if (msg.type === 'chart_signals_updated') {
              // Backend just invalidated its chart-signals cache (new entry/exit) —
              // refetch immediately instead of waiting for the next poll tick, so
              // the chart doesn't lag behind real trading activity.
              if (!msg.data?.strategy || msg.data.strategy === strategyRef.current) {
                fetchChartSignalsRef.current && fetchChartSignalsRef.current()
              }
            }
            if (msg.type === 'cas_alert') {
              const a = msg.data
              if (a.mode === 'cas_window') setCasAlertsA(prev => [a, ...prev])
              else if (a.mode === 'undercurrent') setCasAlertsB(prev => [a, ...prev])
              else if (a.mode === 'index_burst') setCasAlertsC(prev => [a, ...prev])
            }
            if (msg.type === 'cas_at_risk_update') { setCasAtRisk(msg.data || []) }
            if (msg.type === 'cas_undercurrent_update') { setCasUndercurrent(msg.data || []) }
            if (msg.type === 'cas_heatmap_update') { setCasHeatmap(msg.data || []) }
            if (msg.type === 'init') {
              setConnected(msg.data.connected)
              if (msg.data.signal && (!msg.data.signal.instrument || msg.data.signal.instrument === instrumentRef.current))
                setSignal(msg.data.signal)
              if (msg.data.state)  setTradeState(msg.data.state)
            }
          } catch (e) {}
        }
        ws.onclose = () => {
          if (pingInterval) { clearInterval(pingInterval); pingInterval = null; }
          timerId = setTimeout(() => {
            delay = Math.min(delay * 1.5, 5000) // Cap reconnect backoff at 5s max
            connect()
          }, delay)
        }
        wsRef.current = ws
      } catch (err) {
        if (pingInterval) { clearInterval(pingInterval); pingInterval = null; }
        console.error("WebSocket connection failed:", err)
        timerId = setTimeout(() => {
          delay = Math.min(delay * 1.5, 5000)
          connect()
        }, delay)
      }
    }
    connect()
    return () => {
      if (pingInterval) clearInterval(pingInterval)
      if (timerId) clearTimeout(timerId)
      wsRef.current?.close()
    }
  }, [])

  const refreshSettings = useCallback(async () => {
    const cfg = await API.get('/api/settings').catch(()=>null)
    if (cfg) {
      setInstrument(cfg.instrument)
      setChartTf(cfg.chart_timeframe)
      if (cfg.strategy) setStrategy(cfg.strategy)
      setTelegramConfigured(!!cfg.telegram_configured)
    }
  }, [])

  useEffect(() => {
    refreshSettings()
  }, [refreshSettings])

  // Poll status & history every 3 seconds
  useEffect(() => {
    const poll = async () => {
      const s = await API.get('/api/status').catch(()=>null)
      if (s) {
        setConnected(s.connected)
        setBalance(s.balance || 0)
        setLivePnl(s.live_pnl || 0)
        setTodayPnl(s.today_pnl || 0)
        setLotSize(s.lot_size || 0)
        if (s.ltp != null) setLtp(s.ltp)
        if (s.trade_state) setTradeState(s.trade_state)
        setManualPositions(s.manual_positions || [])
        if (s.capital_state) setCapitalState(s.capital_state)
        if (s.data_health) setDataHealth(s.data_health)
        setPriceFrozen(s.price_frozen || null)
        if (s.market) setMarket(s.market)
        setBrokerAuthError(s.broker_auth_error || null)
        setDhanTokens(s.dhan_tokens || null)
        if (s.app_running != null) setAppRunning(s.app_running)
        if (s.max_daily_loss != null) setMaxDailyLoss(s.max_daily_loss)
        if (s.max_daily_profit != null) setMaxDailyProfit(s.max_daily_profit)
        if (s.last_signal && (!s.last_signal.instrument || s.last_signal.instrument === instrumentRef.current)) {
          setSignal(s.last_signal)
        }
        if (s.active_entries) {
          setLastEntries(s.active_entries)
        }
        if (s.active_trade_signal) setActiveTradeSignal(s.active_trade_signal)
        else setActiveTradeSignal(null)
        setAutoTrade(s.auto_trade || false)
      }
      const h = await API.get('/api/signal_history?instrument=' + instrumentRef.current).catch(()=>null)
      if (h) {
        setSigHistory(h.signals || [])
      }
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => clearInterval(id)
  }, [])

  // Poll dual strategy signals every 5 seconds
  useEffect(() => {
    const fetchAll = async () => {
      const all = await API.get('/api/signals_all').catch(()=>null)
      if (all && all.signals) {
        setAllSignals(all.signals)
        if (all.active_entries) {
          setLastEntries(all.active_entries)
        }
        const active = all.signals[all.active_strategy]
        if (active && (!active.instrument || active.instrument === instrumentRef.current)) {
          setSignal(active)
        }
      }
    }
    const timeout = setTimeout(fetchAll, 5000)
    const id = setInterval(fetchAll, 15000)
    return () => { clearInterval(id); clearTimeout(timeout) }
  }, [])

  // Fetch backtest-generated chart signals for the selected strategy
  // This gives TradingView-like overlay: signals across all available history
  const fetchChartSignals = React.useCallback(async () => {
    setChartSignalsLoading(true)
    try {
      const r = await API.get(`/api/chart_signals?strategy=${strategy}&instrument=${instrument}&days=10`)
      if (r && r.signals) {
        // Keep today's live signals the reload doesn't contain (same 5-min bar + signal type)
        const store = liveChartSignalsRef.current
        const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
        const bar = s => String(s?.time || '').replace('T', ' ').slice(0, 16)
        let merged = r.signals
        if (store.key === `${strategy}|${instrument}` && store.day === today && store.list.length) {
          const have = new Set(r.signals.map(s => `${bar(s)}|${s.signal}`))
          const missing = store.list.filter(s => !have.has(`${bar(s)}|${s.signal}`))
          if (missing.length) merged = [...r.signals, ...missing].sort((a, b) => bar(a).localeCompare(bar(b)))
        }
        setChartSignals(merged)
        setRefreshChart(c => c + 1)
      }
    } catch(e) { console.error('fetchChartSignals error:', e) }
    finally { setChartSignalsLoading(false) }
  }, [strategy, instrument])

  // Keep ref in sync so WebSocket handler (created earlier) can call fetchChartSignals
  useEffect(() => { fetchChartSignalsRef.current = fetchChartSignals }, [fetchChartSignals])

  useEffect(() => {
    if (tab === 'live') {
      fetchChartSignals()
      // Safety-net poll only — real-time updates come instantly via the
      // 'chart_signals_updated' WebSocket push above. This just guards against
      // a missed/dropped WS message.
      const id = setInterval(fetchChartSignals, 10000)
      return () => clearInterval(id)
    }
  }, [strategy, instrument, tab, fetchChartSignals])

  // Poll journal if active tab is journal or sig_journal
  useEffect(() => {
    if (tab === 'journal' || tab === 'auto_monitor') {
      fetchJournal()
      const id = setInterval(fetchJournal, 5000)
      return () => clearInterval(id)
    }
    if (tab === 'sig_journal') {
      const fetchSJ = async () => {
        const r = await API.get('/api/signal_journal').catch(()=>null)
        if (r) setSigJournal(r.entries || [])
      }
      fetchSJ()
      const id = setInterval(fetchSJ, 5000)
      return () => clearInterval(id)
    }
    if (tab === 'cas') {
      // Fallback poll -- covers a missed WS message or the page being opened
      // after alerts already fired today. Real-time updates come instantly
      // via the 'cas_alert'/'cas_at_risk_update'/'cas_undercurrent_update'
      // WebSocket pushes above.
      const fetchCas = async () => {
        const [alerts, atRisk, undercurrent, heatmap] = await Promise.all([
          API.get('/api/cas_alerts').catch(()=>null),
          API.get('/api/cas_at_risk').catch(()=>null),
          API.get('/api/cas_undercurrent').catch(()=>null),
          API.get('/api/cas_heatmap').catch(()=>null),
        ])
        if (alerts) {
          setCasAlertsA(alerts.alerts_cas_window || [])
          // alerts_undercurrent holds both stock-sourced (Mode B) and
          // index-sourced (Mode C) alerts -- backend tags each with "mode"
          // so they can be split back apart here.
          const bAll = alerts.alerts_undercurrent || []
          setCasAlertsB(bAll.filter(a => a.mode !== 'index_burst'))
          setCasAlertsC(bAll.filter(a => a.mode === 'index_burst'))
        }
        if (atRisk) setCasAtRisk(atRisk.at_risk || [])
        if (undercurrent) setCasUndercurrent(undercurrent.undercurrent || [])
        if (heatmap) setCasHeatmap(heatmap.heatmap || [])
      }
      fetchCas()
      const id = setInterval(fetchCas, 10000)
      return () => clearInterval(id)
    }
  }, [tab, fetchJournal])


  // Options context fetch for Live Trading

  const fetchOptionsContext = useCallback(async (inst, dir, exp) => {
    setOptCtxLoading(true)
    try {
      const activeInst = inst || instrument
      const activeDir = dir || optCtxDir
      const activeExp = exp !== undefined ? exp : optCtxExpiry
      
      let url = `/api/options-context?instrument=${activeInst}&direction=${activeDir}`
      if (activeExp && activeExp !== 'nearest') {
        url += `&expiry=${activeExp}`
      }
      
      const r = await API.get(url)
      if (r) setOptCtx(r)
    } catch(e) { console.error('Options context fetch failed:', e) }
    setOptCtxLoading(false)
  }, [instrument, optCtxDir, optCtxExpiry])

  // Handle live options context expiry change
  const handleOptCtxExpiryChange = useCallback(async (newExp) => {
    setOptCtxExpiry(newExp)
    await fetchOptionsContext(instrument, optCtxDir, newExp)
  }, [instrument, optCtxDir, fetchOptionsContext])

  // Load available expiries list when instrument changes
  useEffect(() => {
    let active = true
    const loadExpiries = async () => {
      try {
        setOptCtxExpiry('nearest') // Reset selected expiry to nearest
        setOptCtxExpiries([])
        const expData = await API.get(`/api/oi-expiry-list?instrument=${instrument}`)
        if (active && expData && expData.expiries) {
          setOptCtxExpiries(expData.expiries)
        }
      } catch (e) {
        console.error('Failed to load live expiries:', e)
      }
    }
    if (tab === 'live' && connected) {
      loadExpiries()
    }
    return () => { active = false }
  }, [instrument, tab, connected])

  // Auto-fetch options context when on live tab, every 3 minutes
  useEffect(() => {
    if (tab === 'live' && connected) {
      fetchOptionsContext()
      const id = setInterval(() => fetchOptionsContext(), 180000)
      return () => clearInterval(id)
    }
  }, [tab, connected, instrument, optCtxDir, optCtxExpiry, fetchOptionsContext])
  const refreshSignal = async () => {
    const all = await API.get('/api/signals_all').catch(()=>null)
    if (all && all.signals) {
      setAllSignals(all.signals)
      if (all.active_entries) {
        setLastEntries(all.active_entries)
      }
      const active = all.signals[all.active_strategy]
      if (active && (!active.instrument || active.instrument === instrumentRef.current)) {
        setSignal(active); setRefreshChart(r=>r+1)
      }
    } else {
      const s = await API.get('/api/signal').catch(()=>null)
      if (s && (!s.instrument || s.instrument === instrumentRef.current)) {
        setSignal(s); setRefreshChart(r=>r+1)
      }
    }
    const h = await API.get('/api/signal_history?instrument=' + instrumentRef.current).catch(()=>null)
    if (h) {
      setSigHistory(h.signals || [])
    }
  }

  const toggleAppRunning = async () => {
    const newVal = !appRunning
    if (!newVal) {
      const confirmed = window.confirm("STOP the application?\n\nThis will pause all data fetching, signal generation, and polling.\nOpen positions will NOT be closed.")
      if (!confirmed) return
    }
    await API.post(newVal ? '/api/app/start' : '/api/app/stop')
    setAppRunning(newVal)
  }

  const toggleAutoTrade = async () => {
    const newVal = !autoTrade
    if (newVal) {
      const confirmed = window.confirm(
        'Enable AUTO TRADING?\n\n' +
        'This will place REAL orders on Dhan using NRML (carry forward).\n' +
        'Strategy: ' + strategy + '\n' +
        'Instrument: ' + instrument + '\n\n' +
        'Make sure your Dhan account has sufficient margin.\n' +
        (capitalState?.drawdown_breached
          ? '\nWARNING: the capital drawdown flag is ON -- every entry will be REFUSED until you reset it (Dashboard / Auto Trade page).\n\n'
          : '') +
        'Click OK to enable.'
      )
      if (!confirmed) return
    }
    await API.post('/api/settings', { auto_trade: newVal })
    setAutoTrade(newVal)
  }

  // D3 (08 Oct): Dhan's own kill switch, pressed by the user only -- nothing triggers it automatically.
  const [killSwitch, setKillSwitch] = useState(null)   // {ok, active, raw}
  const refreshKillSwitch = async () => {
    try { setKillSwitch(await API.get('/api/dhan/kill_switch')) } catch { /* status only */ }
  }
  useEffect(() => { refreshKillSwitch() }, [])
  const activateKillSwitch = async () => {
    const confirmed = window.confirm(
      'ACTIVATE DHAN KILL SWITCH?\n\n' +
      'Dhan will BLOCK ALL TRADING on your account for the rest of today:\n' +
      '- the app cannot place or exit orders\n' +
      '- you cannot trade manually on Dhan either\n\n' +
      'Open positions stay open. The app\'s auto-trade will be switched OFF.\n\n' +
      'Click OK to activate.'
    )
    if (!confirmed) return
    try {
      const r = await API.post('/api/dhan/kill_switch/activate', { confirm: 'ACTIVATE' })
      if (r?.status) setKillSwitch(r.status)
      if (r?.success) { setAutoTrade(false); window.alert('Dhan kill switch is ACTIVE. Trading is blocked for today.') }
      else window.alert('Kill switch NOT activated: ' + (r?.error || 'unknown error') + '\nActivate it in the Dhan app if needed.')
    } catch (e) {
      window.alert('Kill switch request failed: ' + (e?.message || e) + '\nActivate it in the Dhan app if needed.')
    }
  }

  const handleManualTrade = async (action) => {
    const r = await API.post('/api/trade/manual', { action })
    if (!r.success) alert(r.error || 'Trade failed')
    else { setRefreshChart(r=>r+1); refreshSignal() }
  }

  const handleTestTelegram = async () => {
    const r = await API.post('/api/telegram/test').catch(() => ({ success: false, detail: 'Failed to connect to server' }))
    if (!r.success) {
      alert(r.detail || 'Failed to send test Telegram alert. Make sure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env')
    } else {
      alert('✓ Test Telegram alert dispatched successfully!')
    }
  }

  const sideW = sidebarCollapsed ? 64 : 240

  const pageTitles = {
    dashboard: ['Dashboard', 'Overview of your trading system'],
    auto_monitor: ['Auto Trade Monitor', 'Live trade tracking, confidence, and capital utilization'],
    market_ctx: ['Global Cues & Sentiment', 'Live global markets, bias analysis, and news sentiment'],
    live: ['Live Trading', `${instrument} • ${chartTf === 'DAY' ? 'Daily' : chartTf + 'm'} • ${LIVE_STRATEGY_LABELS[strategy] || strategy}`],
    research_studio: ['Strategy Research Studio', 'Ingest PineScript v5+, AI prompts, or custom Python — Transpile, Validate & Move to Backtesting'],
    backtest: ['Backtest', 'Run historical backtests on your strategies'],
    strategy_details: ['Strategy Details', 'What each strategy does and how it performed on BANKNIFTY, NIFTY, SENSEX and CRUDEOIL'],
    sig_journal: ['Strategy Signals Log', 'Strategy signal history and theoretical P&L'],
    journal: ['Broker Journal', 'Actual broker execution log'],
    performance: ['Performance', 'Analytics, equity curve, and system health'],
    research: ['Research', 'Factor research pipeline — PCA, RMT, lead-lag analysis'],
    settings: ['Settings', 'Configure system settings, strategy parameters, risk thresholds, and API keys'],
    cas: ['CAS', 'CAS-window spike detection (15:05-15:28, stock options) and all-day undercurrent scanning (stocks + indices)'],
    investment: ['Investment Analysis', 'Company fundamentals, quality scores, and peer comparison — separate from live options trading'],
  }

  return (
    <div style={{ background:V('bg-primary'), minHeight:'100vh', color:V('text-primary'), fontFamily:"'Inter', system-ui, sans-serif" }}>
      <Sidebar
        tab={tab} setTab={setTab}
        theme={theme} toggleTheme={toggleTheme}
        onSettings={() => { setTab('settings'); if (isMobile) setMobileMenuOpen(false) }}
        onConnect={() => connected ? null : setShowConnect(true)}
        connected={connected}
        collapsed={sidebarCollapsed}
        setCollapsed={setSidebarCollapsed}
        isMobile={isMobile}
        mobileOpen={mobileMenuOpen}
        setMobileOpen={setMobileMenuOpen}
      />

      {isMobile && <MobileTopBar onMenuClick={() => setMobileMenuOpen(true)} />}

      {/* Main content */}
      <div style={{ marginLeft: isMobile ? 0 : sideW, transition:'margin-left 0.25s ease', minHeight:'100vh', paddingTop: isMobile ? 52 : 0 }}>
        {/* Auto-trade banner */}
        {autoTrade && (
          <div style={{
            background:V('red-bg'), borderBottom:`1px solid color-mix(in srgb, ${V('red')} 30%, transparent)`,
            padding:'8px 24px', display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:12
          }}>
            <div style={{ display:'flex', gap:16, alignItems:'center' }}>
              <span style={{ color:V('red'), fontWeight:700 }}>AUTO TRADING ACTIVE</span>
              <span style={{ color:V('text-muted') }}>Strategy: <span style={{color:V('text-primary'), fontWeight:500}}>{LIVE_STRATEGY_LABELS[strategy] || strategy}</span></span>
              <span style={{ color:V('text-muted') }}>Order: <span style={{color:V('text-primary'), fontWeight:500}}>NRML</span></span>
              {tradeState?.position ? (
                <span style={{ color: tradeState.position.direction === 'LONG' ? V('green') : V('red'), fontWeight:600 }}>
                  Position: {tradeState.position.direction} {tradeState.position.symbol} x{tradeState.position.qty}
                </span>
              ) : <span style={{ color:V('text-muted') }}>No open position</span>}
            </div>
            <StyledButton onClick={toggleAutoTrade} variant="danger" style={{ padding:'3px 12px', fontSize:10 }}>
              STOP AUTO
            </StyledButton>
          </div>
        )}

        {/* Dhan token expiring (< 2 h) or expired -- update it in Settings */}
        {dhanTokens && Object.values(dhanTokens).filter(x => x.state === 'expiring' || x.state === 'expired').map(x => (
          <div key={x.label} style={{
            background: x.state === 'expired' ? V('red-bg') : V('yellow-bg'),
            borderBottom:`1px solid color-mix(in srgb, ${x.state === 'expired' ? V('red') : V('yellow')} 30%, transparent)`,
            padding:'8px 24px', fontSize:12, fontWeight:700, color: x.state === 'expired' ? V('red') : V('yellow'),
            display:'flex', alignItems:'center', gap:12, flexWrap:'wrap'
          }}>
            <span>{x.state === 'expired' ? '⚠' : '⏰'} Dhan {x.label} token {x.state === 'expired'
              ? `EXPIRED ${_tokenWhen(x.expires_at)}`
              : `expires ${_tokenWhen(x.expires_at)} (${_tokenLeft(x.minutes_left)})`} — paste a new one in Settings → Dhan Connection.</span>
            {tab !== 'settings' && <button onClick={() => setTab('settings')} style={{ background:'none', border:`1px solid currentColor`, color:'inherit', borderRadius:6, padding:'2px 10px', fontSize:11, fontWeight:700, cursor:'pointer' }}>Update now</button>}
          </div>
        ))}

        {/* Dhan rejecting the access token: no fresh data, no orders */}
        {brokerAuthError && (
          <div style={{
            background:V('red-bg'), borderBottom:`1px solid color-mix(in srgb, ${V('red')} 30%, transparent)`,
            padding:'8px 24px', fontSize:12, color:V('red'), fontWeight:700
          }}>
            ⚠ DHAN TOKEN EXPIRED (since {brokerAuthError.since}) — no fresh data and no orders. Update the token in .env and restart the app.
          </div>
        )}

        {/* Market closed: everything on the page is last-known data -- say from when */}
        {['live', 'dashboard', 'market_ctx', 'auto_monitor'].includes(tab) && market && market.open === false && (
          <div style={{
            background:V('bg-tertiary'), borderBottom:`1px solid ${V('border')}`,
            padding:'8px 24px', fontSize:12, color:V('text-secondary'), fontWeight:600
          }}>
            ⏸ {market.label}
          </div>
        )}

        {/* Price freeze: feed and quote both down -- price held at the last real one, checks paused */}
        {priceFrozen && (
          <div style={{
            background:V('red-bg'), borderBottom:`1px solid color-mix(in srgb, ${V('red')} 30%, transparent)`,
            padding:'8px 24px', fontSize:12, color:V('red'), fontWeight:600
          }}>
            PRICE FROZEN: no live price since {new Date(priceFrozen.since).toLocaleTimeString('en-IN', { hour12:false })} (last {fmt(priceFrozen.price)}, {priceFrozen.minutes} min). SL / target / strategy checks paused until prices return; open trades unchanged.
          </div>
        )}

        {/* Data stale warning */}
        {dataHealth?.is_stale && tab !== 'dashboard' && (
          <div style={{
            background:V('yellow-bg'), borderBottom:`1px solid color-mix(in srgb, ${V('yellow')} 30%, transparent)`,
            padding:'8px 24px', fontSize:12, color:V('yellow'), fontWeight:600
          }}>
            DATA STALE: Last candle {Math.round((dataHealth.staleness_seconds||0)/60)} min ago. Signal generation paused.
          </div>
        )}

        {/* Page content */}
        <div style={{ padding: isMobile ? '16px 12px' : '24px 28px', maxWidth:1400 }}>
          {/* Page header */}
          {tab !== 'live' && (
            <PageHeader title={pageTitles[tab]?.[0] || ''} subtitle={pageTitles[tab]?.[1] || ''} />
          )}

          {/* Live Trading has its own header with controls */}
          {tab === 'live' && (
            <PageHeader title="Live Trading" subtitle={`${instrument} • ${chartTf === 'DAY' ? 'Daily' : chartTf + ' Min'} • ${LIVE_STRATEGY_LABELS[strategy] || strategy}`}>
              <StyledSelect value={instrument} onChange={async (e) => {
                const val = e.target.value
                setInstrument(val)
                setSignal(null)
                setLastEntries({})
                setSigHistory([])
                await API.post('/api/settings', { instrument: val })
                setRefreshChart(r => r + 1)
                refreshSignal()
              }} options={['NIFTY','BANKNIFTY','SENSEX','CRUDEOIL'].map(i => ({v:i,l:i}))} />

              <StyledSelect value={chartTf} onChange={async (e) => {
                const val = e.target.value
                setChartTf(val)
                await API.post('/api/settings', { chart_timeframe: val })
                setRefreshChart(r => r + 1)
                refreshSignal()
              }} options={[{v:'1',l:'1m'},{v:'5',l:'5m'},{v:'15',l:'15m'},{v:'25',l:'25m'},{v:'60',l:'1H'},{v:'DAY',l:'Day'}]} />

              <StyledSelect value={strategy} onChange={async (e) => {
                const val = e.target.value
                setStrategy(val)
                setSignal(null)
                setSigHistory([])
                setLastEntries({})
                await API.post('/api/settings', { strategy: val })
                setRefreshChart(r => r + 1)
                refreshSignal()
              }} options={LIVE_STRATEGY_OPTIONS} style={{border:`1px solid ${V('accent')}`}} />

              <StyledSelect
                value={optCtxExpiry}
                onChange={(e) => handleOptCtxExpiryChange(e.target.value)}
                options={[
                  {v: 'nearest', l: 'Nearest Expiry'},
                  ...optCtxExpiries.map(exp => ({v: exp, l: exp}))
                ]}
                style={{ border: `1px solid ${V('border')}` }}
              />

              {connected && (
                <StyledButton onClick={() => { refreshSignal(); fetchChartSignals() }} variant="primary">
                  <RefreshCw size={12}/> Refresh
                </StyledButton>
              )}

              <StyledButton onClick={async () => {
                if (!window.confirm('Clear all chart signal markers and signal history?')) return
                await fetch('/api/signal_history', { method:'DELETE' })
                setSigHistory([])
                liveChartSignalsRef.current = { key: '', day: '', list: [] }
                setChartSignals([])
                setRefreshChart(r => r + 1)
              }} variant="danger">
                <Trash2 size={12}/> Clear Signals
              </StyledButton>

              <StyledButton onClick={handleTestTelegram} variant="purple">
                <Send size={12}/> Telegram
              </StyledButton>

              <StyledButton onClick={toggleAppRunning} variant={appRunning ? 'success' : 'danger'}>
                {appRunning ? <><Play size={12}/> Running</> : <><Square size={12}/> Stopped</>}
              </StyledButton>

              <StyledButton onClick={toggleAutoTrade} variant={autoTrade ? 'success' : 'default'}>
                {autoTrade ? <><Zap size={12}/> Auto ON</> : <><Square size={12}/> Auto OFF</>}
              </StyledButton>

              {killSwitch?.active
                ? <StyledButton variant="danger" disabled title="Dhan blocks all trading on the account for the rest of today">
                    <Square size={12}/> Kill switch ACTIVE
                  </StyledButton>
                : <StyledButton onClick={activateKillSwitch} variant="danger" title="Dhan's own kill switch: blocks ALL trading on the account for the rest of today">
                    <Square size={12}/> Dhan Kill Switch
                  </StyledButton>}
            </PageHeader>
          )}

          {/* Page bodies */}
          {tab === 'dashboard' && (
            <DashboardPage
              connected={connected} appRunning={appRunning} autoTrade={autoTrade} toggleAutoTrade={toggleAutoTrade}
              balance={balance} livePnl={livePnl} todayPnl={todayPnl} lotSize={lotSize}
              tradeState={tradeState} signal={signal} strategy={strategy}
              instrument={instrument} ltp={ltp} capitalState={capitalState} dataHealth={dataHealth}
              toggleAppRunning={toggleAppRunning} allSignals={allSignals} lastEntries={lastEntries}
              telegramConfigured={telegramConfigured} refreshSettings={refreshSettings} manualPositions={manualPositions}
              maxDailyLoss={maxDailyLoss} maxDailyProfit={maxDailyProfit}
            />
          )}

          {tab === 'auto_monitor' && (
            <AutoTradeMonitorPage
              autoTrade={autoTrade} toggleAutoTrade={toggleAutoTrade}
              journal={journal} allSignals={allSignals} capitalState={capitalState}
              tradeState={tradeState} strategy={strategy} signal={signal}
            />
          )}

          {tab === 'market_ctx' && <MarketContextPage />}

          {tab === 'live' && (
            <div style={{ display:'flex', flexDirection:'column', gap:14 }}>

              {/* ══════════ SECTION 2: METRICS ══════════ */}
              <DayStats state={tradeState} balance={balance} livePnl={livePnl} todayPnl={todayPnl} lotSize={lotSize} manualPositions={manualPositions} />

              {/* ══════════ SECTION 3: CHART + STRATEGY SIGNALS ══════════ */}
              <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
                {/* Top: Full-Width Chart */}
                <div style={{ width:'100%' }}>
                  <LiveChart refreshChart={refreshChart} instrument={instrument} timeframe={chartTf} signals={chartSignals} strategy={strategy} theme={theme} loading={chartSignalsLoading} />
                </div>

                {/* Bottom: 3-Column Grid for Signals & Option Analysis */}
                <div style={{ display:'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1.2fr', gap: isMobile ? 10 : 14 }}>
                  {/* Col 1: Active Strategy Signal */}
                  <ErrorBoundary>
                    <SignalPanel signal={signal} onManualTrade={handleManualTrade} position={tradeState?.position} connected={connected} ltp={ltp} lastEntry={lastEntries[strategy]} />
                  </ErrorBoundary>

                  {/* Col 2: Secondary Strategy Signal */}
                  {(() => {
                    const otherSignals = Object.entries(allSignals || {}).filter(([k]) => LIVE_STRATEGY_IDS.includes(k) && k !== strategy)
                    if (otherSignals.length > 0) {
                      return otherSignals.map(([k, sig]) => (
                        <ErrorBoundary key={k}>
                          <CompactSignalPanel signal={sig} ltp={ltp} lastEntry={lastEntries[k]} strategyLabel={LIVE_STRATEGY_LABELS[k] || k} />
                        </ErrorBoundary>
                      ))
                    } else {
                      return (
                        <Card style={{ opacity: 0.6, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 120 }}>
                          <div style={{ color: V('text-muted'), fontSize: 11, textTransform: 'uppercase', fontWeight: 600 }}>Secondary Strategy Offline</div>
                        </Card>
                      )
                    }
                  })()}

                  {/* Col 3: Options Analysis View */}
                  <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
                    <ErrorBoundary>
                      <OptimizedStrikePanel data={optCtx} loading={optCtxLoading} />
                    </ErrorBoundary>

                    {!connected && (
                      <Card style={{ textAlign:'center', padding:24 }}>
                        <WifiOff size={24} style={{color:V('yellow'), marginBottom:8}} />
                        <div style={{ color:V('yellow'), fontSize:13, marginBottom:10, fontWeight:600 }}>Not connected to Dhan</div>
                        <StyledButton onClick={()=>setShowConnect(true)} variant="primary" style={{ margin:'0 auto' }}>
                          Connect Now
                        </StyledButton>
                      </Card>
                    )}
                  </div>
                </div>
              </div>

              {/* ══════════ SECTION 4: LIVE POSITION ══════════ */}
              <div style={{ borderTop:`1px solid ${V('border')}`, paddingTop:14 }}>
                <div style={{ color:V('text-muted'), fontSize:11, textTransform:'uppercase', fontWeight:600, letterSpacing:1, marginBottom:10, display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{ fontSize:13 }}>📊</span> Auto-trade position
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
                  <ErrorBoundary>
                    {tradeState?.position && !tradeState.position.order_id?.startsWith('PAPER_') ? (
                      <DhanTradePanel
                        position={tradeState.position}
                        tradeSignal={activeTradeSignal}
                        onExit={() => handleManualTrade('EXIT')}
                        ltp={ltp}
                        instrument={instrument}
                      />
                    ) : (
                      // "Live Position" must reflect broker-confirmed reality only, never a
                      // paper/simulated position — matches the filter PositionGreeksPanel
                      // already applies just below, so the two panels can't disagree again.
                      <PositionPanel
                        state={{ ...tradeState, position: tradeState?.position?.order_id?.startsWith('PAPER_') ? tradeState.position : null }}
                        title="Paper — no real order" />
                    )}
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <PositionGreeksPanel data={optCtx} tradeState={tradeState} loading={optCtxLoading} />
                  </ErrorBoundary>
                </div>
                <div style={{ marginTop:14 }}>
                  <ErrorBoundary>
                    <ManualPositionsPanel items={manualPositions} />
                  </ErrorBoundary>
                </div>
              </div>

              {/* ══════════ SECTION 5: OPTIONS AWARENESS & ANALYSIS ══════════ */}
              <div style={{ borderTop:`1px solid ${V('border')}`, paddingTop:14 }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
                  <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                    <Target size={16} style={{ color:V('accent') }} />
                    <span style={{ color:V('text-primary'), fontSize:14, fontWeight:700 }}>Options Awareness & Analysis</span>
                    <span style={{ color:V('text-muted'), fontSize:11 }}>
                      {optCtx?.instrument || instrument} • {optCtx?.expiry || ''}
                    </span>
                  </div>
                  <div style={{ display:'flex', gap:6, alignItems:'center' }}>
                    {optCtx?.as_of && (
                      <span style={{ color: optCtx.stale ? V('yellow') : V('text-muted'), fontSize:10, fontWeight: optCtx.stale ? 600 : 400 }}
                            title={optCtx.stale ? `Latest refresh failed: ${optCtx.refresh_error || ''}` : undefined}>
                        {optCtx.stale ? '⚠ Latest refresh failed — showing data from ' : 'Updated '}
                        {new Date(optCtx.as_of).toLocaleString('en-IN', { timeZone:'Asia/Kolkata', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false })}
                      </span>
                    )}
                    {optCtx?.data_quality === 'poor' && (
                      <span style={{ color:V('yellow'), fontSize:10, fontWeight:500 }}>⚠ Limited data quality</span>
                    )}
                    <StyledButton onClick={() => fetchOptionsContext()} variant="default" style={{ padding:'4px 10px', fontSize:11 }}>
                      <RefreshCw size={11} /> Refresh
                    </StyledButton>
                  </div>
                </div>

                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
                  {/* Row 1: Strike Selector + Greeks Dashboard */}
                  <ErrorBoundary>
                    <StrikeSelectorPanel
                      data={optCtx}
                      direction={optCtxDir}
                      onDirectionChange={(d) => { setOptCtxDir(d); fetchOptionsContext(instrument, d) }}
                      loading={optCtxLoading}
                    />
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <GreeksDecayDashboard data={optCtx} loading={optCtxLoading} />
                  </ErrorBoundary>

                  {/* Row 2: OI Analysis (full width) */}
                  <div style={{ gridColumn:'1 / -1' }}>
                    <ErrorBoundary>
                      <LiveOiAnalysisPanel data={optCtx} loading={optCtxLoading} />
                    </ErrorBoundary>
                  </div>
                </div>
              </div>
            </div>
          )}

          {tab === 'research_studio' && (
            <ResearchStudio API={API} onNavigateToBacktest={(sId) => { setBtStrategy(sId); setTab('backtest'); }} loadRequest={researchLoadReq} />
          )}

          {tab === 'backtest' && <BacktestPanel connected={connected} selectedStrategy={btStrategy} onStrategyChange={setBtStrategy} onSelectStrategy={(sId) => { setResearchLoadReq({ id: sId, ts: Date.now() }); setTab('research_studio'); }} />}

          {tab === 'strategy_details' && (
            <ErrorBoundary>
              <StrategyDetailsPage />
            </ErrorBoundary>
          )}

          {tab === 'sig_journal' && (
            <ErrorBoundary>
              <SignalJournalPanel entries={sigJournal || []} onRefresh={async () => {
                const r = await API.get('/api/signal_journal').catch(()=>null)
                if (r && r.entries) setSigJournal(r.entries)
              }} />
            </ErrorBoundary>
          )}

          {tab === 'cas' && (
            <ErrorBoundary>
              <CasAlertsPage alertsA={casAlertsA} alertsB={casAlertsB} alertsC={casAlertsC} atRisk={casAtRisk} undercurrent={casUndercurrent} heatmap={casHeatmap} />
            </ErrorBoundary>
          )}

          {tab === 'investment' && (
            <ErrorBoundary>
              <InvestmentAnalysisPage />
            </ErrorBoundary>
          )}


          {tab === 'journal' && (
            <JournalPanel 
              journal={journal} info={journalInfo} 
              onRefresh={fetchJournal} 
              fromDate={journalFromDate}
                         toDate={journalToDate}
              onFromDateChange={setJournalFromDate}
              onToDateChange={setJournalToDate}
            />
          )}

          {tab === 'performance' && <PerformancePanel theme={theme} />}
          {tab === 'settings' && <SettingsPanel onSaved={()=>{setRefreshChart(r=>r+1); refreshSettings()}} />}
        </div>
      </div>

      {showConnect  && <ConnectModal  onClose={()=>setShowConnect(false)}  onConnected={()=>setConnected(true)} />}
    </div>
  )
}


// ── Login gate (2026-10-03) ─────────────────────────────────────────────────
// The backend refuses every /api call and the WebSocket without a session cookie (auth.py).
// Any /api response "401 Not logged in" (e.g. the 12-hour session ran out) brings the login
// screen back -- caught at fetch level so every call in the app is covered.
if (typeof window !== 'undefined' && !window.__authFetchWrapped) {
  window.__authFetchWrapped = true
  const _fetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const res = await _fetch(input, init)
    const url = typeof input === 'string' ? input : (input?.url || '')
    if (res.status === 401 && url.includes('/api/') && !url.includes('/api/auth/')) {
      window.dispatchEvent(new Event('auth-required'))
    }
    return res
  }
}

function LoginPage({ onLoggedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      const r = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'},
                                                 body: JSON.stringify({ username, password }) })
      const d = await r.json().catch(() => ({}))
      if (r.ok) { onLoggedIn(d.username) }
      else { setError(d.detail || 'Login failed'); setPassword('') }
    } catch (_) { setError('Cannot reach the server.') }
    setBusy(false)
  }
  const input = { width:'100%', boxSizing:'border-box', background:V('bg-input'), color:V('text-primary'),
                  border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'10px 12px', fontSize:14, outline:'none' }
  return (
    <div style={{ minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center', background:V('bg-primary'), padding:16 }}>
      <form onSubmit={submit} style={{ width:360, maxWidth:'100%', background:V('bg-secondary'), border:`1px solid ${V('border')}`,
                                       borderRadius:V('radius-xl'), padding:28, boxShadow:V('shadow-lg'), display:'flex', flexDirection:'column', gap:14 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <div style={{ width:36, height:36, borderRadius:10, background:V('accent-bg'), display:'flex', alignItems:'center', justifyContent:'center' }}>
            <Lock size={18} style={{ color:V('accent') }} />
          </div>
          <div>
            <div style={{ color:V('text-primary'), fontWeight:700, fontSize:17 }}>AlgoTrader Pro</div>
            <div style={{ color:V('text-muted'), fontSize:12 }}>Sign in to continue</div>
          </div>
        </div>
        <div>
          <div style={{ color:V('text-secondary'), fontSize:12, marginBottom:4 }}>Username</div>
          <input autoFocus autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} style={input} />
        </div>
        <div>
          <div style={{ color:V('text-secondary'), fontSize:12, marginBottom:4 }}>Password</div>
          <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} style={input} />
        </div>
        {error && <div style={{ color:V('red'), fontSize:12 }}>{error}</div>}
        <StyledButton variant="primary" disabled={busy || !username || !password} style={{ padding:'10px 14px', fontSize:14, fontWeight:700 }}>
          {busy ? 'Signing in…' : 'Sign in'}
        </StyledButton>
        <div style={{ color:V('text-muted'), fontSize:11, textAlign:'center' }}>Session lasts 12 hours</div>
      </form>
    </div>
  )
}

export default function App() {
  const [auth, setAuth] = useState({ checked:false, user:null })
  useEffect(() => {
    fetch('/api/auth/me').then(r => r.ok ? r.json() : null).then(d => setAuth({ checked:true, user: d?.username || null }))
      .catch(() => setAuth({ checked:true, user:null }))
    const onAuthRequired = () => setAuth({ checked:true, user:null })
    window.addEventListener('auth-required', onAuthRequired)
    return () => window.removeEventListener('auth-required', onAuthRequired)
  }, [])
  if (!auth.checked) return <div style={{ minHeight:'100vh', background:V('bg-primary') }} />
  if (!auth.user) return <LoginPage onLoggedIn={(user) => setAuth({ checked:true, user })} />
  return <MainApp />
}
