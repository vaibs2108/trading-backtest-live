import React, { useState, useEffect, useRef, useCallback } from 'react'
import { createChart, CrosshairMode } from 'lightweight-charts'
import {
  Activity, Settings, BarChart2, TrendingUp, TrendingDown, Shield,
  Wifi, WifiOff, RefreshCw, Play, Square, AlertCircle,
  ChevronDown, CheckCircle, XCircle, Clock, Zap, BookOpen, Send, Cpu,
  Moon, Sun, LayoutDashboard, Globe, ChevronLeft, ChevronRight, Menu,
  Calendar, Target, Trash2
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as ReTooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'

// ── API helpers ──────────────────────────────────────────────────────────────
const API = {
  get:  (url)       => fetch(url).then(r => r.json()),
  post: (url, body) => fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) }).then(r => r.json()),
}

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
const fmtPnl = n => n == null ? '—' : (n>=0?'+':'') + '₹' + Math.abs(n).toLocaleString('en-IN',{maximumFractionDigits:0})
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

// ── Sidebar ──────────────────────────────────────────────────────────────────
function Sidebar({ tab, setTab, theme, toggleTheme, onSettings, onConnect, connected, collapsed, setCollapsed, isMobile, mobileOpen, setMobileOpen }) {
  const navItems = [
    { id:'dashboard',    icon:<LayoutDashboard size={18}/>, label:'Dashboard' },
    { id:'market_ctx',   icon:<Globe size={18}/>,           label:'Market Context' },
    { id:'live',         icon:<Activity size={18}/>,        label:'Live Trading' },
    { id:'auto_monitor', icon:<Zap size={18}/>,             label:'Auto Trade' },
    { id:'backtest',     icon:<BarChart2 size={18}/>,       label:'Backtest' },
    { id:'sig_journal',  icon:<TrendingUp size={18}/>,      label:'Strategy Signals Log' },
    { id:'journal',      icon:<BookOpen size={18}/>,        label:'Broker Journal' },
    { id:'performance',  icon:<BarChart2 size={18}/>,       label:'Performance' },
    { id:'research',     icon:<Target size={18}/>,          label:'Research' },
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
          const p = s.strategy === 'regime_trend_range' ? 'R' : (s.strategy === 'broker_sync' ? 'B' : 'M')
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
    loadData()
    const id = setInterval(loadData, 5000)
    return () => clearInterval(id)
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
          {externalLoading && <span style={{ color:V('text-muted'), fontSize:11 }}>Loading signals...</span>}
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
  const isRegime = signal.strategy === 'regime_trend_range'
  const showActiveEntry = isHold && lastEntry && (lastEntry.signal === 'LONG' || lastEntry.signal === 'SHORT')
  const displaySig = showActiveEntry ? lastEntry.signal : sig
  const sigColor = displaySig==='LONG'||displaySig==='LONG_EXIT' ? V('green') : displaySig==='SHORT'||displaySig==='SHORT_EXIT' ? V('red') : V('yellow')
  const sigIcon  = displaySig==='LONG' ? '📈' : displaySig==='SHORT' ? '📉' : (displaySig||'').includes('EXIT') ? '🚪' : '⏸'
  const regimeColor = r => r?.startsWith('TRENDING_UP') ? V('green') : r?.startsWith('TRENDING_DOWN') ? V('red') : r === 'TRANSITION' ? V('yellow') : V('text-muted')

  return (
    <Card style={{ borderLeft:`3px solid ${sigColor}`, height:'100%', boxSizing:'border-box', display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ color:V('text-muted'), fontSize:11, marginBottom:4, fontWeight:500 }}>{signal.strategy === 'regime_trend_range' ? 'Regime T/R Optimized' : signal.strategy === 'multi_agent' ? 'Multi-Agent Optimized' : 'Strategy Signal'}</div>
          <div style={{ color:sigColor, fontSize:24, fontWeight:800 }}>{sigIcon} {showActiveEntry ? `${lastEntry.signal} (active)` : sig}</div>
          {signal.time && <div style={{ color:V('text-muted'), fontSize:10, marginTop:2 }}>{toIST(signal.time)}</div>}
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
  const isRegime = signal.strategy === 'regime_trend_range'
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
          {signal.time && <div style={{ color:V('text-muted'), fontSize:9, marginTop:2 }}>{toIST(signal.time)}</div>}
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
function PositionPanel({ state }) {
  const m = window.innerWidth < 768
  if (!state?.position) return (
    <Card>
      <div style={{ color:V('text-muted'), textAlign:'center', padding:'20px 0', fontSize:13 }}>No open position</div>
    </Card>
  )
  const p = state.position
  const pnlColor = clr(p.current_pnl)
  return (
    <Card style={{ borderLeft:`3px solid ${p.direction==='LONG'?V('green'):V('red')}` }}>
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

// ── Day Stats ───────────────────────────────────────────────────────────────
function DayStats({ state, balance, livePnl, todayPnl, lotSize }) {
  const m = window.innerWidth < 768
  const d = state?.day_stats
  const displayPnl = todayPnl != null && todayPnl !== 0 ? todayPnl : d?.gross_pnl
  return (
    <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:10 }}>
      <MetricBox label="Balance" value={`₹${fmt(balance)}`} color={V('accent')} />
      <MetricBox label="Live P&L" value={fmtPnl(livePnl)} color={clr(livePnl)} />
      <MetricBox label="Today's P&L" value={fmtPnl(displayPnl)} color={clr(displayPnl)} sub={`${d?.total_trades||0} trades`} />
      <MetricBox label="Win/Loss" value={`${d?.wins||0} / ${d?.losses||0}`} color={V('text-primary')} sub={lotSize ? `Lot: ${lotSize}` : (d?.total_trades > 0 ? `${((d?.wins/d?.total_trades)*100).toFixed(0)}% WR` : '—')} />
    </div>
  )
}

// ── Settings Modal ──────────────────────────────────────────────────────────
function SettingsPanel({ onSaved }) {
  const [cfg, setCfg] = useState({})
  const [saved, setSaved] = useState(false)

  useEffect(() => { API.get('/api/settings').then(setCfg) }, [])

  const set = (k,v) => setCfg(p => ({...p, [k]:v}))

  const save = async () => {
    await API.post('/api/settings', cfg)
    setSaved(true); setTimeout(() => { setSaved(false); onSaved(); }, 1200)
  }

  const Row = ({ label, children }) => (
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 0', borderBottom:`1px solid ${V('border-light')}` }}>
      <span style={{ color:V('text-secondary'), fontSize:12 }}>{label}</span>
      {children}
    </div>
  )
  const Sel = ({ val, opts, onChange }) => (
    <select value={val||''} onChange={e=>onChange(e.target.value)} style={{
      background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'5px 10px', fontSize:12, outline:'none'
    }}>{opts.map(o=><option key={o.v||o} value={o.v||o}>{o.l||o}</option>)}</select>
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
      {/* 2-column grid layout */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 }}>
        {/* Left Column */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
          {/* Strategy & Instrument */}
          <Card>
            <SectionTitle label="Strategy & Instrument" icon={<Cpu size={16} style={{color:V('accent')}} />} />
            <Row label="Strategy">
              <Sel val={cfg.strategy} opts={[{v:'multi_agent',l:'Multi-Agent Optimized (6 agents + ML)'},{v:'regime_trend_range',l:'Regime Trend/Range Optimized'}]} onChange={v=>set('strategy',v)} />
            </Row>
            <Row label="Instrument">
              <Sel val={cfg.instrument} opts={['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','CRUDEOIL']} onChange={v=>set('instrument',v)} />
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
            <Row label="Auto Square-Off (mins before close)">
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
            <Row label="Bot Token">
              <input value={cfg.telegram_bot_token||''} onChange={e=>set('telegram_bot_token',e.target.value)} placeholder="Bot Token" style={{
                background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'5px 10px', fontSize:12, width:240, outline:'none'
              }} />
            </Row>
            <Row label="Chat ID">
              <input value={cfg.telegram_chat_id||''} onChange={e=>set('telegram_chat_id',e.target.value)} placeholder="Chat ID" style={{
                background:V('bg-input'), color:V('text-primary'), border:`1px solid ${V('border')}`, borderRadius:V('radius-sm'), padding:'5px 10px', fontSize:12, width:240, outline:'none'
              }} />
            </Row>
          </Card>
        </div>

        {/* Right Column */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
          {/* Strategy Specific Settings */}
          <Card>
            <SectionTitle label="Strategy Parameters" icon={<Target size={16} style={{color:V('yellow')}} />} />
            <div style={{ fontSize:11, color:V('text-muted'), fontWeight:600, margin:'8px 0 4px', textTransform:'uppercase' }}>Multi-Agent Strategy</div>
            <Row label="ML Threshold">
              <Num val={cfg.ml_threshold} onChange={v=>set('ml_threshold',v)} min={0.3} max={0.9} step={0.01} />
            </Row>
            <Row label="SL Multiplier (ATR×)">
              <Num val={cfg.atr_sl_mult} onChange={v=>set('atr_sl_mult',v)} min={0.5} max={3} step={0.1} />
            </Row>
            <Row label="T1 Multiplier (ATR×)">
              <Num val={cfg.atr_t1_mult} onChange={v=>set('atr_t1_mult',v)} min={1} max={5} step={0.1} />
            </Row>
            <Row label="T2 Multiplier (ATR×)">
              <Num val={cfg.atr_t2_mult} onChange={v=>set('atr_t2_mult',v)} min={2} max={8} step={0.1} />
            </Row>

            <div style={{ fontSize:11, color:V('text-muted'), fontWeight:600, margin:'16px 0 4px', textTransform:'uppercase' }}>Regime Strategy</div>
            <Row label="Trail Multiplier (ATR×)">
              <Num val={cfg.regime_trail_mult} onChange={v=>set('regime_trail_mult',v)} min={0.5} max={4.0} step={0.1} />
            </Row>
            <Row label="Trail Activation (ATR×)">
              <Num val={cfg.regime_trail_activation} onChange={v=>set('regime_trail_activation',v)} min={0.0} max={2.0} step={0.1} />
            </Row>
            <Row label="BE Trigger (ATR×)">
              <Num val={cfg.regime_be_trigger} onChange={v=>set('regime_be_trigger',v)} min={0.0} max={2.0} step={0.1} />
            </Row>
            <Row label="BE Buffer (ATR×)">
              <Num val={cfg.regime_be_buffer} onChange={v=>set('regime_be_buffer',v)} min={0.0} max={2.0} step={0.1} />
            </Row>
          </Card>

          {/* Capital Protection & System Tuning */}
          <Card>
            <SectionTitle label="System & Capital Protection" icon={<Settings size={16} style={{color:V('accent')}} />} />
            <Row label="Starting Capital (₹)">
              <Num val={cfg.starting_capital} onChange={v=>set('starting_capital',v)} min={10000} max={10000000} step={10000} />
            </Row>
            <Row label="Data Stale Threshold (min)">
              <Num val={cfg.data_stale_threshold_min} onChange={v=>set('data_stale_threshold_min',v)} min={5} max={30} />
            </Row>
            <Row label="Chart Timeframe">
              <Sel val={cfg.chart_timeframe} opts={[{v:'1',l:'1 Min'},{v:'5',l:'5 Min'},{v:'15',l:'15 Min'},{v:'25',l:'25 Min'},{v:'60',l:'1 Hour'},{v:'DAY',l:'Daily'}]} onChange={v=>set('chart_timeframe',v)} />
            </Row>
            <Row label="Min Orchestrator Score">
              <Num val={cfg.min_orchestrator_score} onChange={v=>set('min_orchestrator_score',v)} min={0.3} max={0.9} step={0.05} />
            </Row>
            <Row label="Chart Patterns">
              <Sel val={cfg.chart_patterns_enabled ? 'true' : 'false'} opts={[{v:'true',l:'Enabled'},{v:'false',l:'Disabled'}]} onChange={v=>set('chart_patterns_enabled',v==='true')} />
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
function BacktestPanel({ connected }) {
  const m = window.innerWidth < 768
  const [form, setForm] = useState({ instrument:'BANKNIFTY', from_date:'', to_date:'', initial_capital:500000, lot_multiplier:1, strategy:'multi_agent' })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const run = async () => {
    if (!connected) { setErr('Connect to broker first'); return }
    setLoading(true); setErr('')
    try {
      const r = await API.post('/api/backtest', form)
      if (r.error) setErr(r.error)
      else setResult(r)
    } catch(e) { setErr('Backtest failed') }
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

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <Card>
        <div style={{ color:V('text-primary'), fontWeight:700, marginBottom:14, fontSize:15 }}>Backtest Configuration</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
          {[
            ['Strategy', <select value={form.strategy} onChange={e=>setForm(p=>({...p,strategy:e.target.value}))} style={inputStyle}>
              {[{v:'multi_agent',l:'Multi-Agent Optimized'},{v:'regime_trend_range',l:'Regime Trend/Range Optimized'},{v:'kalman_vix',l:'Kalman VIX Regime-Switching'},{v:'supertrendy',l:'SuperTrendy (Adaptive ST)'}].map(o=><option key={o.v} value={o.v}>{o.l}</option>)}
            </select>],
            ['Instrument', <select value={form.instrument} onChange={e=>setForm(p=>({...p,instrument:e.target.value}))} style={inputStyle}>
              {['NIFTY','BANKNIFTY','SENSEX','CRUDEOIL'].map(i=><option key={i}>{i}</option>)}
            </select>],
            ['From Date', <input type="date" value={form.from_date} onChange={e=>setForm(p=>({...p,from_date:e.target.value}))} style={inputStyle} />],
            ['To Date', <input type="date" value={form.to_date} onChange={e=>setForm(p=>({...p,to_date:e.target.value}))} style={inputStyle} />],
            ['Capital (₹)', <input type="number" value={form.initial_capital} onChange={e=>setForm(p=>({...p,initial_capital:Number(e.target.value)}))} style={inputStyle} />],
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
          <div style={{ color:V('text-primary'), fontWeight:700, marginBottom:14, fontSize:15 }}>Backtest Results</div>
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

// ── Signal Journal Panel ────────────────────────────────────────────────────
function SignalJournalPanel({ entries, onRefresh }) {
  const m = window.innerWidth < 768
  const [strategyFilter, setStrategyFilter] = React.useState('ALL')
  const [instrumentFilter, setInstrumentFilter] = React.useState('ALL')
  const [fromDate, setFromDate] = React.useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 30)
    return d.toISOString().split('T')[0]
  })
  const [toDate, setToDate] = React.useState(() => {
    return new Date().toISOString().split('T')[0]
  })

  // Apply filters
  const filteredEntries = entries.filter(e => {
    if (strategyFilter !== 'ALL' && e.strategy !== strategyFilter) return false
    if (instrumentFilter !== 'ALL' && e.instrument !== instrumentFilter) return false
    if (e.entry_time) {
      const dateStr = e.entry_time.split('T')[0]
      if (fromDate && dateStr < fromDate) return false
      if (toDate && dateStr > toDate) return false
    }
    return true
  })

  const closed  = filteredEntries.filter(e => e.status !== 'OPEN')
  const wins    = closed.filter(e => e.status === 'WIN').length
  const losses  = closed.filter(e => e.status === 'LOSS').length
  const totalPts = closed.reduce((s, e) => s + (e.pnl_pts || 0), 0)
  const totalInr = closed.reduce((s, e) => s + (e.pnl_inr || 0), 0)
  const winRate  = closed.length > 0 ? (wins / closed.length * 100).toFixed(0) : '—'

  const statusColor = s => s === 'WIN' ? V('green') : s === 'LOSS' ? V('red') : V('yellow')
  const statusLabel = s => s === 'WIN' ? '✓ WIN' : s === 'LOSS' ? '✗ LOSS' : '● OPEN'

  const handleDownloadCSV = () => {
    const headers = ['Date', 'Strategy', 'Instrument', 'Direction', 'Entry Price', 'SL', 'Target 1', 'Target 2', 'Regime', 'Score', 'Exit Time', 'Exit Price', 'P&L pts', 'P&L INR', 'Exit Reason', 'Status']
    const rows = filteredEntries.map(e => [
      e.entry_time,
      e.strategy === 'multi_agent' ? 'Multi-Agent Optimized' : e.strategy === 'regime_trend_range' ? 'Regime T/R Optimized' : e.strategy,
      e.instrument,
      e.direction,
      e.entry_price,
      e.sl,
      e.target1,
      e.target2,
      e.regime || '',
      e.weighted_score || e.ml_prob || '',
      e.exit_time || '',
      e.exit_price || '',
      e.pnl_pts || '',
      e.pnl_inr || '',
      e.exit_reason || '',
      e.status
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

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(5,1fr)', gap:8 }}>
        <MetricBox label="Total Signals" value={filteredEntries.length} color={V('accent')} />
        <MetricBox label="Win Rate" value={`${winRate}%`} color={V('green')} sub={`${wins}W / ${losses}L`} />
        <MetricBox label="Total P&L (pts)" value={totalPts >= 0 ? `+${totalPts.toFixed(0)}` : totalPts.toFixed(0)} color={clr(totalPts)} />
        <MetricBox label="Total P&L (₹)" value={fmtPnl(totalInr)} color={clr(totalInr)} />
        <MetricBox label="Open" value={filteredEntries.filter(e=>e.status==='OPEN').length} color={V('yellow')} />
      </div>

      <Card>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12, flexWrap:'wrap', gap:8 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Strategy Signals Log</div>
          
          <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>Strategy:</span>
              <select 
                value={strategyFilter} 
                onChange={e => setStrategyFilter(e.target.value)} 
                style={{
                  background: V('bg-input'),
                  color: V('text-primary'),
                  border: `1px solid ${V('border')}`,
                  borderRadius: V('radius-sm'),
                  padding: '4px 8px',
                  fontSize: 11,
                  outline: 'none'
                }}
              >
                <option value="ALL">All Strategies</option>
                <option value="multi_agent">Multi-Agent Optimized</option>
                <option value="regime_trend_range">Regime T/R Optimized</option>
              </select>
            </div>

            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>Instrument:</span>
              <select 
                value={instrumentFilter} 
                onChange={e => setInstrumentFilter(e.target.value)} 
                style={{
                  background: V('bg-input'),
                  color: V('text-primary'),
                  border: `1px solid ${V('border')}`,
                  borderRadius: V('radius-sm'),
                  padding: '4px 8px',
                  fontSize: 11,
                  outline: 'none'
                }}
              >
                <option value="ALL">All Instruments</option>
                <option value="BANKNIFTY">BANKNIFTY</option>
                <option value="NIFTY">NIFTY</option>
                <option value="FINNIFTY">FINNIFTY</option>
                <option value="MIDCPNIFTY">MIDCPNIFTY</option>
                <option value="SENSEX">SENSEX</option>
                <option value="CRUDEOIL">CRUDEOIL</option>
              </select>
            </div>

            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>From:</span>
              <input 
                type="date" 
                value={fromDate} 
                onChange={e => setFromDate(e.target.value)} 
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
                value={toDate} 
                onChange={e => setToDate(e.target.value)} 
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

            <StyledButton onClick={handleDownloadCSV} variant="primary" style={{ padding:'4px 12px', fontSize:11 }}>Download CSV</StyledButton>
            <StyledButton onClick={onRefresh} variant="primary" style={{ padding:'4px 12px', fontSize:11 }}>↺ Refresh</StyledButton>
            <StyledButton onClick={async () => {
              if (!window.confirm('Clear all signal journal entries? This cannot be undone.')) return
              await fetch('/api/signal_journal', { method:'DELETE' })
              onRefresh()
            }} variant="danger" style={{ padding:'4px 12px', fontSize:11 }}><Trash2 size={11}/> Clear Logs</StyledButton>
          </div>
        </div>

        {filteredEntries.length === 0 ? (
          <div style={{ color:V('text-muted'), textAlign:'center', padding:40, fontSize:13 }}>
            No strategy signals found for the selected filters.
          </div>
        ) : (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr>{['Date','Strategy','Instrument','Dir','Entry','SL','T1','T2','Regime','Score','Exit','P&L pts','P&L ₹','Reason','Status'].map(h=>(
                  <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), borderBottom:`1px solid ${V('border')}`, whiteSpace:'nowrap', fontWeight:600 }}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>{filteredEntries.map((e, i) => (
                <tr key={i} style={{ borderBottom:`1px solid ${V('border-light')}`, background: i%2===0 ? 'transparent' : V('bg-tertiary') }}>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), whiteSpace:'nowrap', fontSize:10 }}>{toISTDateTime(e.entry_time)}</td>
                  <td style={{ padding:'6px 8px', color:V('text-primary'), fontWeight:500 }}>
                    {e.strategy === 'multi_agent' ? 'Multi-Agent Optimized' : e.strategy === 'regime_trend_range' ? 'Regime T/R Optimized' : e.strategy}
                  </td>
                  <td style={{ padding:'6px 8px', color:V('text-primary'), fontWeight:500 }}>{e.instrument}</td>
                  <td style={{ padding:'6px 8px', color:e.direction==='LONG'?V('green'):V('red'), fontWeight:700 }}>{e.direction}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{fmt(e.entry_price)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('red') }}>{fmt(e.sl)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('green') }}>{fmt(e.target1)}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", color:V('cyan') }}>{fmt(e.target2)}</td>
                  <td style={{ padding:'6px 8px', fontSize:10, color: e.regime?.startsWith('TRENDING_UP')?V('green'):e.regime?.startsWith('TRENDING_DOWN')?V('red'):V('text-muted') }}>{e.regime ? e.regime.replace('TRENDING_','T_') : '—'}</td>
                  <td style={{ padding:'6px 8px', color:V('accent'), fontSize:10 }}>{e.weighted_score!=null?(e.weighted_score*100).toFixed(0)+'%':e.ml_prob!=null?(e.ml_prob*100).toFixed(0)+'%':'—'}</td>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), fontSize:10 }}>{e.exit_time ? toISTDateTime(e.exit_time) : '—'}</td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", fontWeight:700, color:clr(e.pnl_pts) }}>
                    {e.pnl_pts != null ? (e.pnl_pts >= 0 ? `+${e.pnl_pts}` : e.pnl_pts) : '—'}
                  </td>
                  <td style={{ padding:'6px 8px', fontFamily:"'JetBrains Mono', monospace", fontWeight:700, color:clr(e.pnl_inr) }}>
                    {e.pnl_inr != null ? fmtPnl(e.pnl_inr) : '—'}
                  </td>
                  <td style={{ padding:'6px 8px', color:V('text-muted'), fontSize:10 }}>{e.exit_reason || '—'}</td>
                  <td style={{ padding:'6px 8px', color:statusColor(e.status), fontWeight:600, fontSize:10 }}>{statusLabel(e.status)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Trading Journal Panel ───────────────────────────────────────────────────
function JournalPanel({ journal, onRefresh, fromDate, toDate, onFromDateChange, onToDateChange }) {
  const m = window.innerWidth < 768
  const [selectedTrade, setSelectedTrade] = useState(null)
  const closedTrades = journal.filter(t => t.status === 'CLOSED')
  const totalTrades = closedTrades.length
  const wins = closedTrades.filter(t => t.pnl > 0).length
  const losses = totalTrades - wins
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0.0
  const grossProfit = closedTrades.filter(t => t.pnl > 0).reduce((acc, t) => acc + t.pnl, 0.0)
  const grossLoss = Math.abs(closedTrades.filter(t => t.pnl <= 0).reduce((acc, t) => acc + t.pnl, 0.0))
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? 99.9 : 0.0)
  const netPnl = closedTrades.reduce((acc, t) => acc + t.pnl, 0.0)

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Journal Trades" value={totalTrades} sub={`Wins: ${wins} | Losses: ${losses}`} />
        <MetricBox label="Win Rate" value={`${winRate.toFixed(1)}%`} color={winRate >= 50 ? V('green') : V('yellow')} />
        <MetricBox label="Profit Factor" value={profitFactor.toFixed(2)} color={profitFactor >= 1.0 ? V('green') : V('red')} />
        <MetricBox label="Net PnL" value={fmtPnl(netPnl)} color={clr(netPnl)} />
      </div>

      <Card>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12, flexWrap:'wrap', gap:8 }}>
          <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Trading Journal Log</div>
          
          <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:11, color:V('text-muted') }}>From:</span>
              <input 
                type="date" 
                value={fromDate} 
                onChange={e => onFromDateChange(e.target.value)} 
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
                value={toDate} 
                onChange={e => onToDateChange(e.target.value)} 
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
            
            <StyledButton onClick={() => onRefresh()} variant="primary" style={{ padding:'4px 12px', fontSize:11 }}>
              <RefreshCw size={11}/> Refresh
            </StyledButton>
          </div>
        </div>

        {journal.length === 0 ? (
          <div style={{ color:V('text-muted'), textAlign:'center', padding:40, fontSize:13 }}>
            No broker-executed trades found on Dhan for the selected date range.<br/>
            <span style={{ fontSize:11, color:V('text-muted'), marginTop:4, display:'inline-block' }}>
              Ensure your Dhan account is connected under configuration.
            </span>
          </div>
        ) : (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr style={{ borderBottom:`1px solid ${V('border')}` }}>
                  {['Date/Time', 'Status', 'Type', 'Instrument', 'Symbol/Strike', 'Entry px', 'Exit px', 'PnL', 'Exit Reason', 'Model Logic'].map(h => (
                    <th key={h} style={{ padding:'8px 10px', textAlign:'left', color:V('text-muted'), fontWeight:600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {journal.slice().reverse().map((t, idx) => {
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
                      <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color:V('text-primary') }}>{t.exit_price != null ? `₹${fmt(t.exit_price)}` : '—'}</td>
                      <td style={{ padding:'8px 10px', fontFamily:"'JetBrains Mono', monospace", color: clr(pnlVal), fontWeight:700 }}>
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
        {cap.is_profit ? (
          <div><span style={{color:V('text-muted')}}>Profit Today:</span> <span style={{color:V('green'), fontWeight:600}}>+Rs.{fmt(cap.today_pnl)}</span></div>
        ) : (
          <div><span style={{color:V('text-muted')}}>Drawdown:</span> <span style={{color:V('red'), fontWeight:600}}>Rs.{fmt(cap.current_drawdown)} ({cap.current_drawdown_pct?.toFixed(1)}%)</span></div>
        )}
        <div><span style={{color:V('text-muted')}}>Limit (10%):</span> <span style={{color:V('text-primary'), fontWeight:600}}>Rs.{fmt(cap.drawdown_limit)}</span></div>
      </div>
      <div style={{ marginTop:8, height:5, background:V('bg-tertiary'), borderRadius:3, overflow:'hidden' }}>
        <div style={{ height:'100%', width:`${cap.is_profit ? 0 : Math.min(100, (cap.current_drawdown / cap.drawdown_limit) * 100)}%`, background: cap.current_drawdown_pct > 7 ? '#ef4444' : cap.current_drawdown_pct > 4 ? '#f59e0b' : '#10b981', borderRadius:3, transition:'width 0.3s' }}/>
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

      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Total Trades" value={s.total_trades} sub={`W:${s.wins} | L:${s.losses}`} />
        <MetricBox label="Win Rate" value={`${s.win_rate}%`} color={s.win_rate >= 50 ? V('green') : V('yellow')} />
        <MetricBox label="Profit Factor" value={s.profit_factor} color={s.profit_factor >= 1.0 ? V('green') : V('red')} />
        <MetricBox label="Net PnL" value={fmtPnl(s.net_pnl)} color={clr(s.net_pnl)} />
      </div>
      <div style={{ display:'grid', gridTemplateColumns: m ? 'repeat(2,1fr)' : 'repeat(4,1fr)', gap:8 }}>
        <MetricBox label="Avg Win" value={fmtPnl(s.avg_win)} color={V('green')} />
        <MetricBox label="Avg Loss" value={fmtPnl(s.avg_loss)} color={V('red')} />
        <MetricBox label="Sharpe Ratio" value={s.sharpe_ratio} color={s.sharpe_ratio >= 1 ? V('green') : s.sharpe_ratio >= 0 ? V('yellow') : V('red')} />
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
            <div style={{ color:V('text-primary'), fontWeight:700, fontSize:15 }}>Equity Curve</div>
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
                      formatter={(v, name) => [name === 'equity' ? `₹${fmt(v)}` : `₹${fmt(v)}`, name === 'equity' ? 'Equity' : 'Cumul. PnL']}
                      labelFormatter={l => `Trade #${l}`}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#4f6ef7" fill="url(#eqGrad)" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="cumulative_pnl" stroke="#10b981" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
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
function DashboardPage({ connected, appRunning, autoTrade, balance, livePnl, todayPnl, lotSize, tradeState, signal, strategy, instrument, ltp, capitalState, dataHealth, toggleAppRunning, allSignals, lastEntries, telegramConfigured, refreshSettings, maxDailyLoss, maxDailyProfit }) {
  const m = window.innerWidth < 768
  const d = tradeState?.day_stats
  const displayPnl = todayPnl != null && todayPnl !== 0 ? todayPnl : d?.gross_pnl

  const strategyLabels = { multi_agent: 'Multi-Agent Optimized', regime_trend_range: 'Regime T/R Optimized' }
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

  // Toggle Auto-trade state
  const handleToggleAutoTrade = async () => {
    await API.post('/api/settings', { auto_trade: !autoTrade })
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
            {sig.time ? toIST(sig.time) : ''}
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

  const maSignal = allSignals?.multi_agent || (strategy === 'multi_agent' ? signal : null)
  const rtrSignal = allSignals?.regime_trend_range || (strategy === 'regime_trend_range' ? signal : null)

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
            <option value="multi_agent" style={{background:V('bg-primary')}}>Multi-Agent Optimized</option>
            <option value="regime_trend_range" style={{background:V('bg-primary')}}>Regime T/R Optimized</option>
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
        <MetricBox label="Live P&L" value={fmtPnl(livePnl)} color={clr(livePnl)} />
        <MetricBox label="Today's P&L" value={fmtPnl(displayPnl)} color={clr(displayPnl)} sub={`${d?.total_trades||0} trades`} />
        <MetricBox label="Win / Loss" value={`${d?.wins||0} / ${d?.losses||0}`} color={V('text-primary')} sub={lotSize ? `Lot: ${lotSize}` : (d?.total_trades > 0 ? `${((d?.wins/d?.total_trades)*100).toFixed(0)}% WR` : '—')} />
      </div>

      {/* Dual strategy signals + Active Position — 3 columns */}
      <div style={{ display:'grid', gridTemplateColumns: m ? '1fr' : '1fr 1fr 1.2fr', gap:10 }}>
        {renderSignalCard(maSignal, 'Multi-Agent Signal')}
        {renderSignalCard(rtrSignal, 'Regime T/R Signal')}

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
  const strategyLabels = { multi_agent: 'Multi-Agent Optimized', regime_trend_range: 'Regime T/R Optimized' }

  // ── Inactive state ──
  if (!autoTrade) {
    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', minHeight:'60vh', gap:20 }}>
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
  const maSignal = allSignals?.multi_agent
  const rtrSignal = allSignals?.regime_trend_range
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
            {['multi_agent', 'regime_trend_range'].map(sid => {
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
        {loading ? 'Analyzing options chain...' : 'Connect to Dhan to view strike recommendation'}
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
        {loading ? 'Loading...' : 'Connect to Dhan to view Greeks'}
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
export default function App() {
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
  const [strategy,    setStrategy]    = useState('multi_agent')
  const [chartTf,     setChartTf]     = useState('5')
  const [sigHistory,  setSigHistory]  = useState([])
  const [chartSignals, setChartSignals] = useState([])
  const [chartSignalsLoading, setChartSignalsLoading] = useState(false)
  const [allSignals,  setAllSignals]  = useState({})
  const [lastEntries, setLastEntries] = useState({})
  const [sigJournal,  setSigJournal]  = useState([])
  const [autoTrade,   setAutoTrade]   = useState(false)
  const [showSettings,setShowSettings]= useState(false)
  const [showConnect, setShowConnect] = useState(false)
  const [refreshChart,setRefreshChart]= useState(0)
  const [journal,     setJournal]     = useState([])
  const [activeTradeSignal, setActiveTradeSignal] = useState(null)
  const [appRunning,    setAppRunning]    = useState(true)
  const [capitalState, setCapitalState] = useState(null)
  const [dataHealth, setDataHealth] = useState(null)
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

  useEffect(() => { instrumentRef.current = instrument }, [instrument])

  const [journalFromDate, setJournalFromDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().split('T')[0]
  })
  const [journalToDate, setJournalToDate] = useState(() => {
    return new Date().toISOString().split('T')[0]
  })

  const fetchJournal = useCallback(async () => {
    const res = await API.get(`/api/journal?from_date=${journalFromDate}&to_date=${journalToDate}`).catch(()=>null)
    if (res && res.journal) {
      setJournal(res.journal)
    }
  }, [journalFromDate, journalToDate])

  // WebSocket connection
  useEffect(() => {
    let delay = 1000
    let timerId = null

    const connect = () => {
      try {
        const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const ws = new WebSocket(`${wsProto}//${location.host}/ws`)
        
        ws.onopen = () => {
          delay = 1000 // Reset on successful open
        }
        
        ws.onmessage = e => {
          const msg = JSON.parse(e.data)
          if (msg.type === 'signal') {
            if (!msg.data?.instrument || msg.data.instrument === instrumentRef.current)
              setSignal(msg.data)
          }
          if (msg.type === 'state')        setTradeState(msg.data)
          if (msg.type === 'trade_opened') setRefreshChart(r=>r+1)
          if (msg.type === 'trade_closed') setRefreshChart(r=>r+1)
          if (msg.type === 'init') {
            setConnected(msg.data.connected)
            if (msg.data.signal && (!msg.data.signal.instrument || msg.data.signal.instrument === instrumentRef.current))
              setSignal(msg.data.signal)
            if (msg.data.state)  setTradeState(msg.data.state)
          }
        }
        ws.onclose = () => {
          timerId = setTimeout(() => {
            delay = Math.min(delay * 2, 30000) // Double the backoff up to 30s
            connect()
          }, delay)
        }
        wsRef.current = ws
      } catch (err) {
        console.error("WebSocket connection failed:", err)
        timerId = setTimeout(() => {
          delay = Math.min(delay * 2, 30000)
          connect()
        }, delay)
      }
    }
    connect()
    return () => {
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
      setTelegramConfigured(!!(cfg.telegram_bot_token && cfg.telegram_chat_id))
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
        if (s.capital_state) setCapitalState(s.capital_state)
        if (s.data_health) setDataHealth(s.data_health)
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
      const r = await API.get(`/api/chart_signals?strategy=${strategy}&days=10`)
      if (r && r.signals) {
        setChartSignals(r.signals)
        setRefreshChart(c => c + 1)
      }
    } catch(e) { console.error('fetchChartSignals error:', e) }
    finally { setChartSignalsLoading(false) }
  }, [strategy])

  useEffect(() => {
    if (tab === 'live') {
      fetchChartSignals()
      const id = setInterval(fetchChartSignals, 90000) // auto-refresh every 90s
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
        'Click OK to enable.'
      )
      if (!confirmed) return
    }
    await API.post('/api/settings', { auto_trade: newVal })
    setAutoTrade(newVal)
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
    live: ['Live Trading', `${instrument} • ${chartTf === 'DAY' ? 'Daily' : chartTf + 'm'} • ${strategy === 'multi_agent' ? 'Multi-Agent' : 'Regime T/R'}`],
    backtest: ['Backtest', 'Run historical backtests on your strategies'],
    sig_journal: ['Strategy Signals Log', 'Strategy signal history and theoretical P&L'],
    journal: ['Broker Journal', 'Actual broker execution log'],
    performance: ['Performance', 'Analytics, equity curve, and system health'],
    research: ['Research', 'Factor research pipeline — PCA, RMT, lead-lag analysis'],
    settings: ['Settings', 'Configure system settings, strategy parameters, risk thresholds, and API keys'],
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
              <span style={{ color:V('text-muted') }}>Strategy: <span style={{color:V('text-primary'), fontWeight:500}}>{strategy === 'multi_agent' ? 'Multi-Agent Optimized' : 'Regime T/R Optimized'}</span></span>
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
            <PageHeader title="Live Trading" subtitle={`${instrument} • ${chartTf === 'DAY' ? 'Daily' : chartTf + ' Min'} • ${strategy === 'multi_agent' ? 'Multi-Agent Optimized' : 'Regime T/R Optimized'}`}>
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
              }} options={[{v:'multi_agent',l:'Multi-Agent Optimized'},{v:'regime_trend_range',l:'Regime T/R Optimized'}]} style={{border:`1px solid ${V('accent')}`}} />

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
            </PageHeader>
          )}

          {/* Page bodies */}
          {tab === 'dashboard' && (
            <DashboardPage
              connected={connected} appRunning={appRunning} autoTrade={autoTrade}
              balance={balance} livePnl={livePnl} todayPnl={todayPnl} lotSize={lotSize}
              tradeState={tradeState} signal={signal} strategy={strategy}
              instrument={instrument} ltp={ltp} capitalState={capitalState} dataHealth={dataHealth}
              toggleAppRunning={toggleAppRunning} allSignals={allSignals} lastEntries={lastEntries}
              telegramConfigured={telegramConfigured} refreshSettings={refreshSettings}
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
              <DayStats state={tradeState} balance={balance} livePnl={livePnl} todayPnl={todayPnl} lotSize={lotSize} />

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
                    const otherSignals = Object.entries(allSignals || {}).filter(([k]) => k !== strategy)
                    if (otherSignals.length > 0) {
                      return otherSignals.map(([k, sig]) => (
                        <ErrorBoundary key={k}>
                          <CompactSignalPanel signal={sig} ltp={ltp} lastEntry={lastEntries[k]} strategyLabel={{"multi_agent":"Multi-Agent Optimized","regime_trend_range":"Regime T/R Optimized"}[k] || k} />
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
                  <span style={{ fontSize:13 }}>📊</span> Live Position
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
                      <PositionPanel state={tradeState} />
                    )}
                  </ErrorBoundary>
                  <ErrorBoundary>
                    <PositionGreeksPanel data={optCtx} tradeState={tradeState} loading={optCtxLoading} />
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

          {tab === 'backtest' && <BacktestPanel connected={connected} />}

          {tab === 'sig_journal' && (
            <SignalJournalPanel entries={sigJournal} onRefresh={async () => {
              const r = await API.get('/api/signal_journal').catch(()=>null)
              if (r) setSigJournal(r.entries || [])
            }} />
          )}

          {tab === 'journal' && (
            <JournalPanel 
              journal={journal} 
              onRefresh={fetchJournal} 
              fromDate={journalFromDate}
              toDate={journalToDate}
              onFromDateChange={setJournalFromDate}
              onToDateChange={setJournalToDate}
            />
          )}

          {tab === 'performance' && <PerformancePanel theme={theme} />}
          {tab === 'research' && <ResearchPanel />}
          {tab === 'settings' && <SettingsPanel onSaved={()=>{setRefreshChart(r=>r+1); refreshSettings()}} />}
        </div>
      </div>

      {showConnect  && <ConnectModal  onClose={()=>setShowConnect(false)}  onConnected={()=>setConnected(true)} />}
    </div>
  )
}
