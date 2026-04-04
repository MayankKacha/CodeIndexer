import { useState, useRef, useEffect } from 'react'
import { Upload, CheckCircle2, AlertCircle, Loader2, GitBranch } from 'lucide-react'

export default function IndexPage() {
  const [url, setUrl] = useState('')
  const [generateDescriptions, setGenerateDescriptions] = useState(true)
  const [indexWithCGC, setIndexWithCGC] = useState(false)
  const [isIndexing, setIsIndexing] = useState(false)
  const [events, setEvents] = useState([])
  const [finalStats, setFinalStats] = useState(null)
  const logRef = useRef(null)

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [events])

  const handleIndex = async () => {
    if (!url.trim()) return
    setIsIndexing(true)
    setEvents([])
    setFinalStats(null)

    try {
      const response = await fetch('/api/index', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: url.trim(),
          generate_descriptions: generateDescriptions,
          index_with_cgc: indexWithCGC
        }),
      })

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              setEvents(prev => [...prev, event])

              if (event.step === 'done') {
                setFinalStats(event.data)
                setIsIndexing(false)
              } else if (event.step === 'error') {
                setIsIndexing(false)
              }
            } catch (e) {
              // skip malformed
            }
          }
        }
      }
    } catch (err) {
      setEvents(prev => [...prev, { step: 'error', message: `Connection error: ${err.message}` }])
      setIsIndexing(false)
    }
  }

  const getIcon = (step) => {
    if (step === 'done' || step === 'complete' || step === 'cgc_done') return <CheckCircle2 size={16} className="progress-icon done" />
    if (step === 'error') return <AlertCircle size={16} className="progress-icon error" />
    if (step === 'skip') return <CheckCircle2 size={16} className="progress-icon done" />
    return <Loader2 size={16} className="progress-icon working" />
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Index a Repository</h1>
        <p className="page-subtitle">
          Provide a GitHub URL or local path to index a codebase. The system will parse, embed, and store the code for intelligent search.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="input-group">
          <div style={{ position: 'relative', flex: 1 }}>
            <GitBranch size={18} style={{ position: 'absolute', left: 14, top: 13, color: 'var(--text-muted)' }} />
            <input
              type="text"
              className="input"
              placeholder="https://github.com/user/repo  or  /path/to/local/directory"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !isIndexing && handleIndex()}
              disabled={isIndexing}
              style={{ paddingLeft: 42 }}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={handleIndex}
            disabled={isIndexing || !url.trim()}
            style={{ minWidth: 100 }}
          >
            {isIndexing && !indexWithCGC ? <><Loader2 size={16} className="spinner-inline" /> Indexing...</> : <><Upload size={16} /> Index</>}
          </button>
          <button
            className="btn"
            onClick={() => {
              setIndexWithCGC(true);
              handleIndex();
            }}
            disabled={isIndexing || !url.trim()}
            style={{ 
              minWidth: 160, 
              borderColor: 'var(--accent-purple)', 
              color: 'var(--accent-purple)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: 'rgba(139, 92, 246, 0.1)'
            }}
          >
            {isIndexing && indexWithCGC ? <><Loader2 size={16} className="spinner-inline" /> Benchmarking...</> : <><CheckCircle2 size={16} /> Benchmark with CGC</>}
          </button>
        </div>

        <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 10 }}>
          <label className="toggle-switch" style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none' }}>
            <input
              type="checkbox"
              checked={generateDescriptions}
              onChange={(e) => setGenerateDescriptions(e.target.checked)}
              disabled={isIndexing}
              style={{
                width: 18,
                height: 18,
                accentColor: 'var(--accent-blue)',
                cursor: 'pointer'
              }}
            />
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Generate LLM code descriptions (increases indexing time and token usage)
            </span>
          </label>
          <label className="toggle-switch" style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none', marginLeft: 20 }}>
            <input
              type="checkbox"
              checked={indexWithCGC}
              onChange={(e) => setIndexWithCGC(e.target.checked)}
              disabled={isIndexing}
              style={{
                width: 18,
                height: 18,
                accentColor: 'var(--accent-purple)',
                cursor: 'pointer'
              }}
            />
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Also index with CodeGraphContext (Comparison)
            </span>
          </label>
        </div>
      </div>

      {events.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div style={{ display: 'grid', gridTemplateColumns: indexWithCGC ? '1fr 1fr' : '1fr', gap: 20 }}>
            <div>
              <h4 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>
                CodeIndexer Pipeline
              </h4>
              <div className="progress-log" ref={logRef}>
                {events.filter(ev => !ev.step.startsWith('cgc_')).map((ev, i) => (
                  <div key={i} className="progress-item">
                    {getIcon(ev.step)}
                    <span className="progress-text">{ev.message}</span>
                  </div>
                ))}
              </div>
            </div>
            
            {indexWithCGC && (
              <div>
                <h4 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--accent-purple)', marginBottom: 12 }}>
                  CodeGraphContext Stage
                </h4>
                <div className="progress-log">
                  {events.filter(ev => ev.step.startsWith('cgc_')).map((ev, i) => (
                    <div key={i} className="progress-item">
                      {getIcon(ev.step)}
                      <span className="progress-text">{ev.message}</span>
                    </div>
                  ))}
                  {events.filter(ev => !ev.step.startsWith('cgc_')).some(ev => ev.step === 'done') && 
                   !events.some(ev => ev.step.startsWith('cgc_')) && (
                    <div className="progress-item">
                      <Loader2 size={16} className="progress-icon working" />
                      <span className="progress-text">Waiting for parser...</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {finalStats && (
        <div className="card-grid">
          <div className="stat-card">
            <span className="stat-label">Total Elements</span>
            <span className="stat-value">{finalStats.total_elements || 0}</span>
            <span className="stat-detail">
              {finalStats.functions || 0} functions · {finalStats.methods || 0} methods · {finalStats.classes || 0} classes
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Indexing Time</span>
            <span className="stat-value">
              {finalStats.cgc_indexing_time_seconds 
                ? <span style={{fontSize: '0.8em'}}>CI: {finalStats.indexing_time_seconds?.toFixed(1) || 0}s | CGC: {finalStats.cgc_indexing_time_seconds?.toFixed(1)}s</span>
                : `${finalStats.indexing_time_seconds?.toFixed(1) || 0}s`
              }
            </span>
            <span className="stat-detail">
              Parse: {finalStats.parse_time_seconds || 0}s · Embed: {finalStats.embedding_time_seconds || 0}s
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Lines of Code</span>
            <span className="stat-value">{(finalStats.total_lines || 0).toLocaleString()}</span>
            <span className="stat-detail">
              {Object.entries(finalStats.languages || {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Graph</span>
            <span className="stat-value">{finalStats.graph_nodes || 0}</span>
            <span className="stat-detail">
              {finalStats.graph_nodes || 0} nodes · {finalStats.graph_relationships || 0} relationships
            </span>
          </div>
        </div>
      )}
      {finalStats && finalStats.cgc_indexing_time_seconds && (
        <div className="card" style={{ marginTop: 24, padding: '24px' }}>
          <h3 className="card-title" style={{ marginBottom: 20 }}>📊 Indexing Time Comparison</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '40px' }}>
            {/* CodeIndexer Bar */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontWeight: 600, color: 'var(--accent-blue)' }}>CodeIndexer</span>
                <span style={{ color: 'var(--text-secondary)' }}>{finalStats.indexing_time_seconds?.toFixed(1)}s</span>
              </div>
              <div style={{ height: 12, background: 'rgba(255,255,255,0.1)', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ 
                  height: '100%', 
                  background: 'var(--accent-blue)', 
                  width: `${Math.min(100, (finalStats.indexing_time_seconds / Math.max(finalStats.indexing_time_seconds, finalStats.cgc_indexing_time_seconds)) * 100)}%`,
                  transition: 'width 1s ease-out'
                }}></div>
              </div>
            </div>
            
            {/* CGC Bar */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontWeight: 600, color: 'var(--accent-purple)' }}>CodeGraphContext</span>
                <span style={{ color: 'var(--text-secondary)' }}>{finalStats.cgc_indexing_time_seconds?.toFixed(1)}s</span>
              </div>
              <div style={{ height: 12, background: 'rgba(255,255,255,0.1)', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ 
                  height: '100%', 
                  background: 'var(--accent-purple)', 
                  width: `${Math.min(100, (finalStats.cgc_indexing_time_seconds / Math.max(finalStats.indexing_time_seconds, finalStats.cgc_indexing_time_seconds)) * 100)}%`,
                  transition: 'width 1s ease-out'
                }}></div>
              </div>
            </div>
          </div>
          <p style={{ marginTop: 20, fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
            Note: CodeIndexer performs rich file parsing, dependency analysis, and builds a vector space (AI models).
            CodeGraphContext builds a symbolic Relationship Graph (Graph DB).
          </p>
        </div>
      )}
    </div>
  )
}
