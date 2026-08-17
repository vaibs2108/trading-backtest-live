import React, { useState, useEffect, useRef } from 'react'
import { 
  Code2, Sparkles, CheckCircle2, AlertTriangle, Play, Save, Trash2, 
  ArrowRight, FileCode, Layers, Cpu, ShieldCheck, RefreshCw,
  UploadCloud, Clipboard, FileText, Check, Copy, Terminal, Plus, Download
} from 'lucide-react'

// CSS Variable design token helper matching the main application design system
const V = (name) => `var(--${name})`

export default function ResearchStudio({ API, onNavigateToBacktest }) {
  const [activeTab, setActiveTab] = useState('pinescript') // 'pinescript', 'ai_prompt', 'python_editor', 'custom_list'
  const [pinescriptCode, setPinescriptCode] = useState('')
  const [pythonCode, setPythonCode] = useState('')
  const [aiPrompt, setAiPrompt] = useState('')
  const [convertMode, setConvertMode] = useState('ai') // 'ai' (OpenAI Assistant) or 'local' (Deterministic Parser)
  const [strategyId, setStrategyId] = useState('')
  
  const [loading, setLoading] = useState(false)
  const [templates, setTemplates] = useState([])
  const [selectedTemplate, setSelectedTemplate] = useState('')
  
  const [validation, setValidation] = useState(null)
  const [verificationReport, setVerificationReport] = useState(null)
  const [customStrategies, setCustomStrategies] = useState([])
  const [statusMsg, setStatusMsg] = useState({ type: '', text: '' })
  const [isDragging, setIsDragging] = useState(false)
  const [copied, setCopied] = useState(false)

  const fileInputRef = useRef(null)

  // Fetch templates & custom strategies on mount
  useEffect(() => {
    fetchTemplates()
    fetchCustomStrategies()
  }, [])

  const fetchTemplates = async () => {
    try {
      const res = await API.get('/api/research/templates').catch(() => null)
      if (res && res.templates && res.templates.length > 0) {
        setTemplates(res.templates)
        setSelectedTemplate(res.templates[0].id)
        setPinescriptCode(res.templates[0].pinescript)
      }
    } catch (e) {
      console.error("Failed to load templates", e)
    }
  }

  const fetchCustomStrategies = async () => {
    try {
      const res = await API.get('/api/research/list').catch(() => null)
      if (res && res.strategies) {
        setCustomStrategies(res.strategies)
      }
    } catch (e) {
      console.error("Failed to load custom strategies", e)
    }
  }

  const handleSelectTemplate = (tId) => {
    setSelectedTemplate(tId)
    const found = templates.find(t => t.id === tId)
    if (found) {
      setPinescriptCode(found.pinescript)
      setPythonCode('')
      setValidation(null)
      setVerificationReport(null)
    }
  }

  // File Upload Handler (.txt, .pine, .ps, .py)
  const handleFileUpload = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    readAndSetFile(file)
  }

  // Drag & Drop Handlers
  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer.files?.[0]
    if (file) {
      readAndSetFile(file)
    }
  }

  const readAndSetFile = (file) => {
    const reader = new FileReader()
    reader.onload = (event) => {
      const content = event.target?.result || ''
      setPinescriptCode(content)
      setStatusMsg({ type: 'success', text: `Loaded '${file.name}' (${(file.size / 1024).toFixed(1)} KB)` })
    }
    reader.readAsText(file)
  }

  // Paste Clipboard Handler
  const handlePasteClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setPinescriptCode(text)
        setStatusMsg({ type: 'info', text: 'Pasted content from clipboard.' })
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: 'Clipboard read permission denied. Please paste directly into the text area.' })
    }
  }

  const handleConvert = async (targetMode = null) => {
    if (!pinescriptCode.trim()) {
      setStatusMsg({ type: 'error', text: 'Please upload, paste, or select a PineScript code sample first.' })
      return
    }
    const modeToUse = typeof targetMode === 'string' ? targetMode : convertMode
    if (typeof targetMode === 'string') setConvertMode(targetMode)

    setLoading(true)
    setStatusMsg({ type: '', text: '' })
    try {
      const res = await API.post('/api/research/convert', {
        pinescript: pinescriptCode,
        mode: modeToUse,
        strategy_id: strategyId.trim()
      })
      if (res && res.python_code) {
        setPythonCode(res.python_code)
        setValidation(res.validation)
        if (res.mode === 'local' && (res.is_complex || res.warning)) {
          setStatusMsg({
            type: 'warning',
            text: res.warning || '⚠️ Complex PineScript strategy detected! Local transpiler simplified these equations. Click "AI Transpiler" above for 100% mathematical fidelity.'
          })
        } else {
          setStatusMsg({
            type: 'success',
            text: `Converted successfully using ${res.mode === 'ai' ? 'OpenAI Assistant (100% Math Fidelity)' : 'Local PineScript Transpiler'}!`
          })
        }
      } else {
        const errDetail = typeof res?.detail === 'string'
          ? res.detail
          : (Array.isArray(res?.detail) ? res.detail[0]?.msg : (res?.error || 'Conversion returned no python code.'))
        setStatusMsg({ type: 'error', text: `Conversion failed: ${errDetail}` })
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: `Conversion failed: ${e.message || 'Server error'}` })
    } finally {
      setLoading(false)
    }
  }

  const handleGeneratePrompt = async () => {
    if (!aiPrompt.trim()) {
      setStatusMsg({ type: 'error', text: 'Please describe your strategy rules in the prompt box.' })
      return
    }
    setLoading(true)
    setStatusMsg({ type: '', text: '' })
    try {
      const res = await API.post('/api/research/generate-prompt', { prompt: aiPrompt })
      if (res && res.python_code) {
        setPythonCode(res.python_code)
        setValidation(res.validation)
        setStatusMsg({ type: 'success', text: 'AI Strategy generated successfully!' })
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: e.response?.data?.detail || 'AI Generation failed. Check OPENAI_API_KEY / MODEL_NAME in .env' })
    } finally {
      setLoading(false)
    }
  }

  const handleDryRun = async () => {
    if (!pythonCode.trim()) {
      setStatusMsg({ type: 'error', text: 'Convert or generate Python code before running verification.' })
      return
    }
    setLoading(true)
    setStatusMsg({ type: '', text: '' })
    try {
      const res = await API.post('/api/research/dry-run', { python_code: pythonCode })
      if (res && res.verification_table) {
        setVerificationReport(res)
        setStatusMsg({ type: 'success', text: 'Signal verification report generated! Review table below.' })
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: e.response?.data?.detail || 'Dry run verification failed.' })
    } finally {
      setLoading(false)
    }
  }

  const handleSaveAndMove = async () => {
    if (!pythonCode.trim()) {
      setStatusMsg({ type: 'error', text: 'No Python code ready to save.' })
      return
    }
    const cleanId = strategyId.trim() || `custom_strategy_${Date.now().toString().slice(-6)}`
    setLoading(true)
    setStatusMsg({ type: '', text: '' })
    try {
      const res = await API.post('/api/research/save', {
        strategy_id: cleanId,
        python_code: pythonCode
      })
      if (res && res.success) {
        setStatusMsg({ type: 'success', text: `Strategy '${res.strategy_id}' saved and promoted to Backtest dropdown!` })
        fetchCustomStrategies()
        if (onNavigateToBacktest) {
          setTimeout(() => onNavigateToBacktest(res.strategy_id), 1000)
        }
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: e.response?.data?.detail || 'Failed to save strategy.' })
    } finally {
      setLoading(false)
    }
  }

  const handleLoadCustom = async (sId) => {
    setLoading(true)
    try {
      const res = await API.get(`/api/research/strategy/${sId}`).catch(() => null)
      if (res && res.code) {
        setStrategyId(res.strategy_id)
        setPythonCode(res.code)
        setActiveTab('pinescript')
        setStatusMsg({ type: 'info', text: `Loaded strategy '${sId}' for editing.` })
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: 'Could not load strategy file.' })
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteCustom = async (sId) => {
    if (!window.confirm(`Are you sure you want to delete custom strategy '${sId}'?`)) return
    setLoading(true)
    try {
      const res = await API.delete(`/api/research/strategy/${sId}`).catch(() => null)
      if (res && res.success) {
        setStatusMsg({ type: 'success', text: `Deleted custom strategy '${sId}'.` })
        fetchCustomStrategies()
      }
    } catch (e) {
      setStatusMsg({ type: 'error', text: 'Delete failed.' })
    } finally {
      setLoading(false)
    }
  }

  const copyPythonCode = () => {
    navigator.clipboard.writeText(pythonCode)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, color: V('text-primary') }}>
      
      {/* Hidden File Input for .pine / .txt / .ps upload */}
      <input
        type="file"
        ref={fileInputRef}
        accept=".txt,.pine,.ps,.py"
        style={{ display: 'none' }}
        onChange={handleFileUpload}
      />

      {/* Top Sub-Header & Navigation Tabs */}
      <div style={{
        background: V('bg-card'),
        border: `1px solid ${V('border')}`,
        borderRadius: 12,
        padding: '12px 18px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 12
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: 'linear-gradient(135deg, #4f6ef7, #8b5cf6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', boxShadow: '0 4px 12px rgba(79, 110, 247, 0.3)'
          }}>
            <Code2 size={20} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 700, color: V('text-primary') }}>Strategy Sandbox & Transpiler</span>
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 12,
                background: 'color-mix(in srgb, #10b981 15%, transparent)',
                color: V('green'), border: '1px solid color-mix(in srgb, #10b981 30%, transparent)'
              }}>
                LIVE KERNEL COMPILER
              </span>
            </div>
            <span style={{ fontSize: 12, color: V('text-muted') }}>
              Ingest PineScript v5+, AI prompts, or custom Python — Transpile, validate, & promote directly to Backtesting.
            </span>
          </div>
        </div>

        {/* Tab Selector Buttons */}
        <div style={{
          display: 'flex', gap: 6, background: V('bg-primary'),
          padding: 4, borderRadius: 8, border: `1px solid ${V('border')}`
        }}>
          {[
            { id: 'pinescript', label: 'PineScript Transpiler', icon: <FileCode size={14} /> },
            { id: 'ai_prompt', label: 'AI Strategy Assistant', icon: <Sparkles size={14} style={{ color: '#f59e0b' }} /> },
            { id: 'custom_list', label: `Custom Strategies (${customStrategies.length})`, icon: <Layers size={14} /> },
          ].map((tab) => {
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  background: active ? V('accent') : 'transparent',
                  color: active ? '#fff' : V('text-muted'),
                  border: 'none',
                  borderRadius: 6,
                  padding: '6px 14px',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  transition: 'all 0.2s ease',
                  boxShadow: active ? '0 2px 8px rgba(79, 110, 247, 0.4)' : 'none'
                }}
              >
                {tab.icon}
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Notification / Status Message */}
      {statusMsg.text && (
        <div style={{
          padding: '10px 16px',
          borderRadius: 8,
          fontSize: 13,
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          background: statusMsg.type === 'error'
            ? 'color-mix(in srgb, #f43f5e 15%, transparent)'
            : statusMsg.type === 'warning'
            ? 'color-mix(in srgb, #f59e0b 15%, transparent)'
            : statusMsg.type === 'success'
            ? 'color-mix(in srgb, #10b981 15%, transparent)'
            : 'color-mix(in srgb, #4f6ef7 15%, transparent)',
          border: `1px solid ${
            statusMsg.type === 'error' ? '#f43f5e' : statusMsg.type === 'warning' ? '#f59e0b' : statusMsg.type === 'success' ? '#10b981' : '#4f6ef7'
          }`,
          color: statusMsg.type === 'error' ? '#f43f5e' : statusMsg.type === 'warning' ? '#f59e0b' : statusMsg.type === 'success' ? '#10b981' : V('text-primary')
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {statusMsg.type === 'error' || statusMsg.type === 'warning' ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
            <span>{statusMsg.text}</span>
          </div>

          {statusMsg.type === 'warning' && (
            <button
              onClick={() => handleConvert('ai')}
              style={{
                background: '#f59e0b',
                color: '#000',
                border: 'none',
                padding: '4px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 700,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                whiteSpace: 'nowrap'
              }}
            >
              <Sparkles size={12} /> Convert with AI Now
            </button>
          )}
        </div>
      )}

      {/* ════════════════ TAB 1: PINESCRIPT TRANSPILER ════════════════ */}
      {activeTab === 'pinescript' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          
          {/* Left Column: PineScript Ingestion & Controls */}
          <div style={{
            background: V('bg-card'),
            border: `1px solid ${V('border')}`,
            borderRadius: 12,
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            height: 640
          }}>
            {/* Header Toolbar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${V('border')}`, paddingBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <FileCode size={18} style={{ color: V('accent') }} />
                <span style={{ fontWeight: 700, fontSize: 14 }}>PineScript Source Code (v5+)</span>
              </div>

              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  style={{
                    background: V('bg-primary'),
                    border: `1px solid ${V('border')}`,
                    color: V('text-primary'),
                    padding: '4px 10px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5
                  }}
                >
                  <UploadCloud size={13} style={{ color: V('accent') }} /> Upload .pine/.txt
                </button>
                <button
                  onClick={handlePasteClipboard}
                  style={{
                    background: V('bg-primary'),
                    border: `1px solid ${V('border')}`,
                    color: V('text-primary'),
                    padding: '4px 10px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5
                  }}
                >
                  <Clipboard size={13} /> Paste
                </button>
              </div>
            </div>

            {/* Template Presets Bar */}
            {templates.length > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: V('text-muted') }}>
                <span>Quick Samples:</span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {templates.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => handleSelectTemplate(t.id)}
                      style={{
                        background: selectedTemplate === t.id ? 'color-mix(in srgb, #4f6ef7 20%, transparent)' : V('bg-primary'),
                        border: `1px solid ${selectedTemplate === t.id ? V('accent') : V('border')}`,
                        color: selectedTemplate === t.id ? V('accent') : V('text-primary'),
                        padding: '3px 8px',
                        borderRadius: 4,
                        fontSize: 11,
                        cursor: 'pointer',
                        fontWeight: 600
                      }}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Code Editor Dropzone Container */}
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                position: 'relative',
                border: isDragging ? `2px dashed ${V('accent')}` : `1px solid ${V('border')}`,
                borderRadius: 8,
                background: isDragging ? 'color-mix(in srgb, #4f6ef7 10%, transparent)' : V('bg-primary'),
                overflow: 'hidden',
                transition: 'all 0.2s ease'
              }}
            >
              {isDragging && (
                <div style={{
                  position: 'absolute', inset: 0, zIndex: 10,
                  background: 'rgba(79, 110, 247, 0.85)',
                  display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center',
                  color: '#fff', fontWeight: 700, gap: 10
                }}>
                  <UploadCloud size={36} />
                  <span>Drop your .pine or .txt strategy file here!</span>
                </div>
              )}

              <textarea
                value={pinescriptCode}
                onChange={(e) => setPinescriptCode(e.target.value)}
                placeholder="// Paste or upload your PineScript v5+ strategy code here...&#10;// Example:&#10;//@version=5&#10;strategy('My Custom Strategy', overlay=true)&#10;fastEma = ta.ema(close, 9)&#10;slowEma = ta.ema(close, 21)&#10;if (ta.crossover(fastEma, slowEma))&#10;    strategy.entry('Long', strategy.long)"
                style={{
                  width: '100%',
                  height: '100%',
                  background: 'transparent',
                  color: '#e2e8f0',
                  border: 'none',
                  outline: 'none',
                  padding: 12,
                  fontFamily: "'Consolas', 'Fira Code', 'Courier New', monospace",
                  fontSize: 12,
                  lineHeight: 1.6,
                  resize: 'none',
                  tabSize: 4
                }}
              />
            </div>

            {/* Transpile Controls Footer */}
            <div style={{ display: 'flex', alignItems: 'center', justifyBetween: 'space-between', gap: 10, paddingTop: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
                <span style={{ fontSize: 11, color: V('text-muted'), fontWeight: 600 }}>Strategy ID:</span>
                <input
                  type="text"
                  value={strategyId}
                  onChange={(e) => setStrategyId(e.target.value)}
                  placeholder="e.g. custom_my_strategy (or leave blank for auto-unique ID)"
                  style={{
                    background: V('bg-primary'),
                    border: `1px solid ${V('border')}`,
                    color: V('text-primary'),
                    borderRadius: 6,
                    padding: '6px 10px',
                    fontSize: 11,
                    fontFamily: 'monospace',
                    width: 220
                  }}
                />

                <div style={{ display: 'flex', alignItems: 'center', gap: 3, background: V('bg-primary'), padding: '3px', borderRadius: 6, border: `1px solid ${V('border')}` }}>
                  <button
                    type="button"
                    onClick={() => setConvertMode('ai')}
                    title="Convert using OpenAI GPT-4 (Full mathematical fidelity)"
                    style={{
                      background: convertMode === 'ai' ? V('accent') : 'transparent',
                      color: convertMode === 'ai' ? '#fff' : V('text-muted'),
                      border: 'none',
                      borderRadius: 4,
                      padding: '4px 8px',
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4
                    }}
                  >
                    <Sparkles size={12} /> AI Transpiler
                  </button>
                  <button
                    type="button"
                    onClick={() => setConvertMode('local')}
                    title="Convert using offline rule-based parser"
                    style={{
                      background: convertMode === 'local' ? V('border') : 'transparent',
                      color: convertMode === 'local' ? V('text-primary') : V('text-muted'),
                      border: 'none',
                      borderRadius: 4,
                      padding: '4px 8px',
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: 'pointer'
                    }}
                  >
                    Local Rules
                  </button>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  onClick={() => handleConvert()}
                  disabled={loading}
                  style={{
                    background: 'linear-gradient(135deg, #4f6ef7, #6366f1)',
                    border: 'none',
                    color: '#fff',
                    padding: '8px 18px',
                    borderRadius: 6,
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    boxShadow: '0 4px 12px rgba(79, 110, 247, 0.3)',
                    opacity: loading ? 0.6 : 1
                  }}
                >
                  {loading ? <RefreshCw size={14} className="spin" /> : <Cpu size={14} />}
                  Transpile to Python Kernel
                </button>
              </div>
            </div>
          </div>

          {/* Right Column: Transpiled Python Output */}
          <div style={{
            background: V('bg-card'),
            border: `1px solid ${V('border')}`,
            borderRadius: 12,
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            height: 640
          }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${V('border')}`, paddingBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Code2 size={18} style={{ color: V('green') }} />
                <span style={{ fontWeight: 700, fontSize: 14 }}>Standardized Python StrategyKernel</span>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {validation && (
                  <span style={{
                    fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 12,
                    background: validation.valid ? 'color-mix(in srgb, #10b981 15%, transparent)' : 'color-mix(in srgb, #f43f5e 15%, transparent)',
                    color: validation.valid ? V('green') : V('red'),
                    border: `1px solid ${validation.valid ? '#10b981' : '#f43f5e'}`
                  }}>
                    {validation.valid ? '✓ AST VALID' : '⚠ SYNTAX ERROR'}
                  </span>
                )}
                {pythonCode && (
                  <button
                    onClick={copyPythonCode}
                    style={{
                      background: V('bg-primary'),
                      border: `1px solid ${V('border')}`,
                      color: V('text-primary'),
                      padding: '4px 10px',
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4
                    }}
                  >
                    {copied ? <Check size={12} style={{ color: V('green') }} /> : <Copy size={12} />}
                    {copied ? 'Copied!' : 'Copy Code'}
                  </button>
                )}
              </div>
            </div>

            {/* Code Output Textarea */}
            <div style={{
              flex: 1,
              border: `1px solid ${V('border')}`,
              borderRadius: 8,
              background: V('bg-primary'),
              overflow: 'hidden'
            }}>
              <textarea
                value={pythonCode}
                onChange={(e) => setPythonCode(e.target.value)}
                placeholder="# Transpiled Python StrategyKernel will appear here automatically after transpilation..."
                style={{
                  width: '100%',
                  height: '100%',
                  background: 'transparent',
                  color: '#38bdf8',
                  border: 'none',
                  outline: 'none',
                  padding: 12,
                  fontFamily: "'Consolas', 'Fira Code', 'Courier New', monospace",
                  fontSize: 12,
                  lineHeight: 1.6,
                  resize: 'none',
                  tabSize: 4
                }}
              />
            </div>

            {/* Action Footer */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, paddingTop: 4 }}>
              <button
                onClick={handleDryRun}
                disabled={loading || !pythonCode}
                style={{
                  background: V('bg-primary'),
                  border: `1px solid ${V('border')}`,
                  color: V('text-primary'),
                  padding: '8px 14px',
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  opacity: (loading || !pythonCode) ? 0.5 : 1
                }}
              >
                <Play size={14} style={{ color: '#8b5cf6' }} />
                Verify Signals (Dry Run)
              </button>

              <button
                onClick={handleSaveAndMove}
                disabled={loading || !pythonCode}
                style={{
                  background: 'linear-gradient(135deg, #10b981, #059669)',
                  border: 'none',
                  color: '#fff',
                  padding: '8px 18px',
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  boxShadow: '0 4px 12px rgba(16, 185, 129, 0.3)',
                  opacity: (loading || !pythonCode) ? 0.5 : 1
                }}
              >
                <Save size={14} />
                Save & Move to Backtesting
                <ArrowRight size={14} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ════════════════ TAB 2: AI PROMPT ASSISTANT ════════════════ */}
      {activeTab === 'ai_prompt' && (
        <div style={{
          background: V('bg-card'),
          border: `1px solid ${V('border')}`,
          borderRadius: 12,
          padding: 20,
          display: 'flex',
          flexDirection: 'column',
          gap: 16
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, borderBottom: `1px solid ${V('border')}`, paddingBottom: 14 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: 'color-mix(in srgb, #f59e0b 20%, transparent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: '#f59e0b', border: '1px solid color-mix(in srgb, #f59e0b 30%, transparent)'
            }}>
              <Sparkles size={20} />
            </div>
            <div>
              <span style={{ fontSize: 16, fontWeight: 700, display: 'block' }}>Natural Language AI Strategy Creator</span>
              <span style={{ fontSize: 12, color: V('text-muted') }}>
                Type your strategy idea in plain English. OpenAI will build a complete, ready-to-backtest StrategyKernel module.
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 12, fontWeight: 700, color: V('text-primary') }}>Describe your Trading Rules & Indicators:</label>
            <textarea
              rows={5}
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              placeholder="e.g. Create a 5-minute BankNifty strategy. Enter LONG when EMA 9 crosses above EMA 21 and RSI(14) > 55. Stop Loss is 1.5 ATR and Target is 3 ATR. Exit on opposite crossover."
              style={{
                width: '100%',
                background: V('bg-primary'),
                border: `1px solid ${V('border')}`,
                color: V('text-primary'),
                borderRadius: 8,
                padding: 12,
                fontSize: 13,
                outline: 'none',
                resize: 'vertical'
              }}
            />
          </div>

          <div style={{ display: 'flex', justifyBetween: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: 8, fontSize: 11, color: V('text-muted') }}>
              <span>Sample Prompts:</span>
              <button
                onClick={() => setAiPrompt("BankNifty 5m Donchian Breakout: Buy when close breaks 20-bar high, Short when close breaks 20-bar low. ATR 1.5 Stop Loss.")}
                style={{ background: V('bg-primary'), border: `1px solid ${V('border')}`, color: V('text-primary'), padding: '2px 8px', borderRadius: 4, cursor: 'pointer', fontSize: 11 }}
              >
                Donchian 20-bar Breakout
              </button>
              <button
                onClick={() => setAiPrompt("RSI Mean Reversion: Enter LONG when RSI(14) < 30 and MACD histogram turns positive. Target 2.0 ATR.")}
                style={{ background: V('bg-primary'), border: `1px solid ${V('border')}`, color: V('text-primary'), padding: '2px 8px', borderRadius: 4, cursor: 'pointer', fontSize: 11 }}
              >
                RSI Oversold + MACD
              </button>
            </div>

            <button
              onClick={handleGeneratePrompt}
              disabled={loading}
              style={{
                background: 'linear-gradient(135deg, #f59e0b, #d97706)',
                border: 'none',
                color: '#fff',
                padding: '10px 20px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 700,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                boxShadow: '0 4px 12px rgba(245, 158, 11, 0.3)',
                opacity: loading ? 0.6 : 1
              }}
            >
              {loading ? <RefreshCw size={14} className="spin" /> : <Sparkles size={14} />}
              Generate Strategy Kernel with AI
            </button>
          </div>

          {pythonCode && (
            <div style={{ border: `1px solid ${V('border')}`, borderRadius: 8, padding: 14, background: V('bg-primary'), display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', justifyBetween: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 700, fontSize: 13, color: V('green'), display: 'flex', alignItems: 'center', gap: 6 }}>
                  <CheckCircle2 size={16} /> Generated Python StrategyKernel Code
                </span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={handleDryRun} style={{ background: V('bg-card'), border: `1px solid ${V('border')}`, color: V('text-primary'), padding: '4px 10px', borderRadius: 4, fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                    <Play size={12} /> Dry Run
                  </button>
                  <button onClick={handleSaveAndMove} style={{ background: V('accent'), color: '#fff', border: 'none', padding: '4px 12px', borderRadius: 4, fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                    <Save size={12} /> Save to Backtest
                  </button>
                </div>
              </div>
              <textarea
                value={pythonCode}
                onChange={(e) => setPythonCode(e.target.value)}
                style={{
                  width: '100%', height: 320, background: 'transparent', color: '#38bdf8', border: 'none', outline: 'none',
                  fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* ════════════════ TAB 3: MY CUSTOM STRATEGIES LIST ════════════════ */}
      {activeTab === 'custom_list' && (
        <div style={{
          background: V('bg-card'),
          border: `1px solid ${V('border')}`,
          borderRadius: 12,
          padding: 20,
          display: 'flex',
          flexDirection: 'column',
          gap: 16
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${V('border')}`, paddingBottom: 14 }}>
            <div>
              <span style={{ fontSize: 16, fontWeight: 700, display: 'block' }}>My Custom Strategies in Backtest Staging</span>
              <span style={{ fontSize: 12, color: V('text-muted') }}>
                Saved strategies registered in backend/strategies/custom/ ready for multi-timeframe backtesting.
              </span>
            </div>
            <button
              onClick={fetchCustomStrategies}
              style={{
                background: V('bg-primary'),
                border: `1px solid ${V('border')}`,
                color: V('text-primary'),
                padding: '6px 12px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6
              }}
            >
              <RefreshCw size={14} /> Refresh List
            </button>
          </div>

          {customStrategies.length === 0 ? (
            <div style={{
              padding: 40,
              textAlign: 'center',
              border: `1px dashed ${V('border')}`,
              borderRadius: 12,
              color: V('text-muted'),
              fontSize: 13
            }}>
              No custom strategies saved yet. Upload a .pine / .txt file or use the AI Assistant tab to create your first strategy!
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
              {customStrategies.map((s) => (
                <div
                  key={s.strategy_id}
                  style={{
                    background: V('bg-primary'),
                    border: `1px solid ${V('border')}`,
                    borderRadius: 10,
                    padding: 14,
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'space-between',
                    gap: 12
                  }}
                >
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                      <span style={{ fontWeight: 700, fontSize: 14, color: V('text-primary') }}>{s.display_name}</span>
                      <span style={{
                        fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                        background: 'color-mix(in srgb, #4f6ef7 15%, transparent)',
                        color: V('accent'), border: '1px solid color-mix(in srgb, #4f6ef7 30%, transparent)'
                      }}>
                        STAGING
                      </span>
                    </div>
                    <span style={{ fontSize: 11, fontFamily: 'monospace', color: V('text-muted') }}>{s.strategy_id}</span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 8, borderTop: `1px solid ${V('border')}` }}>
                    <button
                      onClick={() => onNavigateToBacktest && onNavigateToBacktest(s.strategy_id)}
                      style={{
                        background: 'linear-gradient(135deg, #10b981, #059669)',
                        color: '#fff',
                        border: 'none',
                        padding: '5px 10px',
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 700,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4
                      }}
                    >
                      <Play size={12} /> Run Backtest
                    </button>

                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        onClick={() => handleLoadCustom(s.strategy_id)}
                        style={{ background: 'transparent', border: 'none', color: V('accent'), cursor: 'pointer', fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 3 }}
                      >
                        <Code2 size={12} /> Edit
                      </button>
                      <button
                        onClick={() => handleDeleteCustom(s.strategy_id)}
                        style={{ background: 'transparent', border: 'none', color: V('red'), cursor: 'pointer', fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 3 }}
                      >
                        <Trash2 size={12} /> Delete
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ════════════════ SIGNAL ALIGNMENT VERIFICATION REPORT ════════════════ */}
      {verificationReport && (
        <div style={{
          background: V('bg-card'),
          border: `1px solid ${V('border')}`,
          borderRadius: 12,
          padding: 18,
          display: 'flex',
          flexDirection: 'column',
          gap: 14
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${V('border')}`, paddingBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{
                width: 32, height: 32, borderRadius: 6,
                background: 'color-mix(in srgb, #8b5cf6 20%, transparent)',
                color: '#8b5cf6', display: 'flex', alignItems: 'center', justifyContent: 'center'
              }}>
                <ShieldCheck size={18} />
              </div>
              <div>
                <span style={{ fontWeight: 700, fontSize: 14, display: 'block' }}>Signal Alignment Verification Report</span>
                <span style={{ fontSize: 11, color: V('text-muted') }}>
                  Simulated dry-run test on 100-bar sample OHLCV. Signal triggers and metrics verified.
                </span>
              </div>
            </div>

            {verificationReport.metrics && (
              <div style={{ display: 'flex', gap: 14, fontSize: 12, fontWeight: 600 }}>
                <span>Trades: <strong>{verificationReport.metrics.total_trades}</strong></span>
                <span style={{ color: V('green') }}>Win Rate: <strong>{verificationReport.metrics.win_rate}%</strong></span>
                <span style={{ color: V('accent') }}>Total PnL: <strong>₹{verificationReport.metrics.total_pnl?.toLocaleString()}</strong></span>
              </div>
            )}
          </div>

          <div style={{ overflowX: 'auto', border: `1px solid ${V('border')}`, borderRadius: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, textAlign: 'left' }}>
              <thead>
                <tr style={{ background: V('bg-primary'), color: V('text-muted'), borderBottom: `1px solid ${V('border')}` }}>
                  <th style={{ padding: '8px 12px' }}>#</th>
                  <th style={{ padding: '8px 12px' }}>Direction</th>
                  <th style={{ padding: '8px 12px' }}>Entry Time</th>
                  <th style={{ padding: '8px 12px' }}>Entry Price</th>
                  <th style={{ padding: '8px 12px' }}>Exit Time</th>
                  <th style={{ padding: '8px 12px' }}>Exit Price</th>
                  <th style={{ padding: '8px 12px' }}>Exit Reason</th>
                  <th style={{ padding: '8px 12px' }}>PnL (₹)</th>
                </tr>
              </thead>
              <tbody>
                {verificationReport.verification_table.map((t) => (
                  <tr key={t.trade_num} style={{ borderBottom: `1px solid ${V('border')}` }}>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{t.trade_num}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{
                        padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                        background: t.direction === 'LONG' ? 'color-mix(in srgb, #10b981 15%, transparent)' : 'color-mix(in srgb, #f43f5e 15%, transparent)',
                        color: t.direction === 'LONG' ? V('green') : V('red')
                      }}>
                        {t.direction}
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>
                      {typeof t.entry_time === 'number' ? `Bar #${t.entry_time}` : String(t.entry_time || '-').substring(0, 16).replace('T', ' ')}
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>₹{t.entry_price}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>
                      {typeof t.exit_time === 'number' ? `Bar #${t.exit_time}` : String(t.exit_time || '-').substring(0, 16).replace('T', ' ')}
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>₹{t.exit_price}</td>
                    <td style={{ padding: '8px 12px', fontWeight: 600 }}>{t.exit_reason}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontWeight: 700, color: t.pnl >= 0 ? V('green') : V('red') }}>
                      {t.pnl >= 0 ? `+₹${t.pnl}` : `-₹${Math.abs(t.pnl)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
