import { useState, useEffect } from 'react'
import { GitCompareArrows, Search, Clock, Cpu, FileJson, AlertCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function ComparePage() {
  const [query, setQuery] = useState('')
  const [repos, setRepos] = useState([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/repositories')
      .then(r => r.json())
      .then(data => setRepos(data.repositories || []))
      .catch(() => {})
  }, [])

  const handleCompare = async () => {
    if (!query.trim()) return

    setIsLoading(true)
    setError(null)

    try {
      const res = await fetch('/api/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: query.trim(),
          repo_name: selectedRepo,
          top_k: 5
        })
      })

      if (!res.ok) {
        throw new Error(`API error: ${res.statusText}`)
      }

      const data = await res.json()
      setResults(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  // Helper to determine winner (lower time is better, lower tokens is better)
  const getWinner = (metric) => {
    if (!results || !results.codegraphcontext.available) return 'ci'
    
    const ci = results.codeindexer
    const cgc = results.codegraphcontext
    
    if (metric === 'time') {
      return ci.retrieval_time_ms <= cgc.retrieval_time_ms ? 'ci' : 'cgc'
    } else if (metric === 'tokens') {
      const ciTokens = ci.compressed_token_breakdown.total_tokens
      const cgcTokens = cgc.token_breakdown.total_tokens
      return ciTokens <= cgcTokens ? 'ci' : 'cgc'
    }
    return null
  }

  return (
    <div className="compare-page">
      <div className="page-header">
        <h1 className="page-title">Retrieval Comparison</h1>
        <p className="page-subtitle">Compare CodeIndexer and CodeGraphContext retrieval performance side-by-side.</p>
      </div>

      <div className="card search-card">
        <div className="input-group">
          {repos.length > 0 && (
            <select
              className="input repo-select"
              value={selectedRepo}
              onChange={(e) => setSelectedRepo(e.target.value)}
              style={{ maxWidth: '200px' }}
            >
              <option value="">All Repositories</option>
              {repos.map(r => (
                <option key={r.repo_name} value={r.repo_name}>{r.repo_name}</option>
              ))}
            </select>
          )}
          <div style={{ position: 'relative', flex: 1 }}>
            <Search className="search-icon" size={18} style={{ position: 'absolute', left: '16px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="text"
              className="input"
              placeholder="What do you want to find in the codebase?"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCompare()}
              style={{ paddingLeft: '44px' }}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={handleCompare}
            disabled={isLoading || !query.trim()}
          >
            {isLoading ? <div className="spinner"></div> : <><GitCompareArrows size={18} /> Compare</>}
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner card" style={{ marginTop: '24px', borderColor: 'var(--accent-red)', background: 'rgba(239, 68, 68, 0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--accent-red)' }}>
            <AlertCircle size={20} />
            <span style={{ fontWeight: 500 }}>Failed to run comparison: {error}</span>
          </div>
        </div>
      )}

      {results && (
        <div className="comparison-content">
          <div className="metrics-section">
            <h3 className="section-title">Performance Metrics</h3>
            <div className="metrics-grid">
              {/* Timing Card */}
              <div className="metric-card">
                <div className="metric-header">
                  <Clock size={16} /> Retrieval Time
                </div>
                <div className="metric-comparison">
                  <div className={`metric-side ${getWinner('time') === 'ci' ? 'winner' : ''}`}>
                    <div className="metric-label">CodeIndexer</div>
                    <div className="metric-value">{results.codeindexer.retrieval_time_ms} ms</div>
                  </div>
                  <div className="metric-divider">vs</div>
                  <div className={`metric-side ${!results.codegraphcontext.available ? 'unavailable' : getWinner('time') === 'cgc' ? 'winner' : ''}`}>
                    <div className="metric-label">CodeGraphContext</div>
                    <div className="metric-value">
                      {results.codegraphcontext.available ? `${results.codegraphcontext.retrieval_time_ms} ms` : 'N/A'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Tokens Card */}
              <div className="metric-card">
                <div className="metric-header">
                  <Cpu size={16} /> LLM Input Tokens
                </div>
                <div className="metric-comparison">
                  <div className={`metric-side ${getWinner('tokens') === 'ci' ? 'winner' : ''}`}>
                    <div className="metric-label">CodeIndexer {results.codeindexer.compression ? '(Compressed)' : ''}</div>
                    <div className="metric-value">{results.codeindexer.compressed_token_breakdown.total_tokens.toLocaleString()}</div>
                  </div>
                  <div className="metric-divider">vs</div>
                  <div className={`metric-side ${!results.codegraphcontext.available ? 'unavailable' : getWinner('tokens') === 'cgc' ? 'winner' : ''}`}>
                    <div className="metric-label">CodeGraphContext (Raw)</div>
                    <div className="metric-value">
                      {results.codegraphcontext.available ? results.codegraphcontext.token_breakdown.total_tokens.toLocaleString() : 'N/A'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Results Count Card */}
              <div className="metric-card">
                <div className="metric-header">
                  <FileJson size={16} /> Top-K Results
                </div>
                <div className="metric-comparison">
                  <div className="metric-side">
                    <div className="metric-label">CodeIndexer</div>
                    <div className="metric-value">{results.codeindexer.result_count}</div>
                  </div>
                  <div className="metric-divider">vs</div>
                  <div className={`metric-side ${!results.codegraphcontext.available ? 'unavailable' : ''}`}>
                    <div className="metric-label">CodeGraphContext</div>
                    <div className="metric-value">
                      {results.codegraphcontext.available ? results.codegraphcontext.result_count : 'N/A'}
                    </div>
                  </div>
                </div>
              </div>
            </div>
            
            {/* Token Breakdown Visual */}
            <div className="card token-breakdown-card">
              <h4 style={{ marginBottom: '16px', fontSize: '14px', color: 'var(--text-secondary)' }}>Token Consumption Analysis</h4>
              <div className="token-bars">
                <div className="token-bar-row">
                  <div className="token-bar-label">CodeIndexer</div>
                  <div className="token-bar-track">
                    {/* System Prompt */}
                    <div 
                      className="token-bar-segment system" 
                      style={{ width: `${(results.codeindexer.compressed_token_breakdown.system_prompt_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                      title={`System Prompt: ${results.codeindexer.compressed_token_breakdown.system_prompt_tokens}`}
                    ></div>
                    {/* Context */}
                    <div 
                      className="token-bar-segment context" 
                      style={{ width: `${(results.codeindexer.compressed_token_breakdown.context_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                      title={`Context: ${results.codeindexer.compressed_token_breakdown.context_tokens}`}
                    ></div>
                    {/* Query */}
                     <div 
                      className="token-bar-segment query" 
                      style={{ width: `${(results.codeindexer.compressed_token_breakdown.query_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                      title={`Query: ${results.codeindexer.compressed_token_breakdown.query_tokens}`}
                    ></div>
                  </div>
                  <div className="token-bar-value">{results.codeindexer.compressed_token_breakdown.total_tokens}</div>
                </div>
                
                {results.codegraphcontext.available && (
                  <div className="token-bar-row">
                    <div className="token-bar-label">CodeGraphContext</div>
                    <div className="token-bar-track">
                      {/* System Prompt */}
                      <div 
                        className="token-bar-segment system" 
                        style={{ width: `${(results.codegraphcontext.token_breakdown.system_prompt_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                        title={`System Prompt: ${results.codegraphcontext.token_breakdown.system_prompt_tokens}`}
                      ></div>
                      {/* Context */}
                      <div 
                        className="token-bar-segment context cgc" 
                        style={{ width: `${(results.codegraphcontext.token_breakdown.context_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                        title={`Context: ${results.codegraphcontext.token_breakdown.context_tokens}`}
                      ></div>
                      {/* Query */}
                       <div 
                        className="token-bar-segment query" 
                        style={{ width: `${(results.codegraphcontext.token_breakdown.query_tokens / Math.max(results.codeindexer.compressed_token_breakdown.total_tokens, results.codegraphcontext.token_breakdown.total_tokens)) * 100}%` }}
                        title={`Query: ${results.codegraphcontext.token_breakdown.query_tokens}`}
                      ></div>
                    </div>
                    <div className="token-bar-value">{results.codegraphcontext.token_breakdown.total_tokens}</div>
                  </div>
                )}
              </div>
              <div className="token-legend">
                <span className="legend-item"><span className="legend-color system"></span> System</span>
                <span className="legend-item"><span className="legend-color context"></span> CI Context</span>
                <span className="legend-item"><span className="legend-color context cgc"></span> CGC Context</span>
                <span className="legend-item"><span className="legend-color query"></span> User Query</span>
              </div>
            </div>
          </div>

          <div className="documents-section">
            <h3 className="section-title" style={{ marginTop: '32px' }}>Retrieved Documents</h3>
            <div className="documents-columns">
              
              {/* CodeIndexer Column */}
              <div className="document-column">
                <div className="column-header ci-header">
                  <div className="column-title">CodeIndexer</div>
                  <div className="badge badge-blue">{results.codeindexer.result_count} items</div>
                </div>
                <div className="results-list">
                  {results.codeindexer.results.map((item, idx) => (
                    <div key={`ci-${idx}`} className="result-card">
                      <div className="result-card-header">
                        <span className="result-name">{item.name}</span>
                        <span className="result-type">{item.element_type}</span>
                      </div>
                      <div className="result-path">{item.file_path} • Lines {item.start_line}-{item.end_line}</div>
                      <div className="result-code-container">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {`\`\`\`${item.language}\n${item.code || '// No code snippet returned'}\n\`\`\``}
                        </ReactMarkdown>
                      </div>
                      <div className="result-footer">
                        RRF Score: {item.rrf_score.toFixed(4)}
                        {item.rerank_score > 0 && ` • Rerank: ${item.rerank_score.toFixed(4)}`}
                      </div>
                    </div>
                  ))}
                  {results.codeindexer.result_count === 0 && (
                     <div className="empty-state-small">No results found</div>
                  )}
                </div>
              </div>

              {/* CodeGraphContext Column */}
              <div className="document-column">
                <div className="column-header cgc-header">
                  <div className="column-title">CodeGraphContext</div>
                  {results.codegraphcontext.available && (
                     <div className="badge badge-purple">{results.codegraphcontext.result_count} items</div>
                  )}
                </div>
                <div className="results-list">
                  {!results.codegraphcontext.available ? (
                    <div className="empty-state-small error">
                      {results.codegraphcontext.error || "CodeGraphContext is not available."}
                    </div>
                  ) : results.codegraphcontext.result_count === 0 ? (
                    <div className="empty-state-small">No results found</div>
                  ) : (
                    results.codegraphcontext.results.map((item, idx) => (
                      <div key={`cgc-${idx}`} className="result-card">
                        <div className="result-card-header">
                          <span className="result-name">{item.name}</span>
                          <span className="result-type">{item.element_type}</span>
                        </div>
                        <div className="result-path">{item.file_path} • Line {item.start_line}</div>
                        <div className="result-code-container">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {`\`\`\`${item.language}\n${item.code || '// No code snippet returned'}\n\`\`\``}
                          </ReactMarkdown>
                        </div>
                        {item.description && (
                          <div className="result-description">
                            {item.description.substring(0, 150)}{item.description.length > 150 ? '...' : ''}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>

            </div>
          </div>
        </div>
      )}
    </div>
  )
}
