import { useState, useEffect } from 'react'
import { BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { Clock, Code2, FileCode, Layers, TrendingUp } from 'lucide-react'

const COLORS = ['#3b82f6', '#8b5cf6', '#22c55e', '#f97316', '#06b6d4', '#ef4444', '#ec4899', '#eab308']

export default function DashboardPage() {
  const [analytics, setAnalytics] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/analytics')
      .then(r => r.json())
      .then(data => { setAnalytics(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 40, height: 40 }} />
        <h3 style={{ marginTop: 16 }}>Loading analytics...</h3>
      </div>
    )
  }

  if (!analytics || analytics.total_repositories === 0) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Analytics Dashboard</h1>
          <p className="page-subtitle">Performance metrics, indexing time breakdown, and codebase statistics.</p>
        </div>
        <div className="empty-state">
          <Layers className="empty-icon" size={64} />
          <h3>No data yet</h3>
          <p>Index a repository first to see analytics here.</p>
        </div>
      </div>
    )
  }

  // Prepare chart data
  const languageData = Object.entries(analytics.languages || {}).map(([name, value]) => ({ name, value }))

  const repoTimingData = (analytics.repositories || []).map(r => ({
    name: r.repo_name,
    Parse: r.parse_time || 0,
    Embed: r.embedding_time || 0,
    Graph: r.graph_time || 0,
    Vector: r.vector_time || 0,
  }))

  const repoElementsData = (analytics.repositories || []).map(r => ({
    name: r.repo_name,
    Functions: r.functions || 0,
    Methods: r.methods || 0,
    Classes: r.classes || 0,
  }))

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Analytics Dashboard</h1>
        <p className="page-subtitle">Performance metrics, indexing time breakdown, and codebase statistics.</p>
      </div>

      {/* Summary stats */}
      <div className="card-grid" style={{ marginBottom: 28 }}>
        <div className="stat-card">
          <span className="stat-label">Repositories</span>
          <span className="stat-value">{analytics.total_repositories}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total Elements</span>
          <span className="stat-value">{analytics.total_elements.toLocaleString()}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Lines of Code</span>
          <span className="stat-value">{analytics.total_lines.toLocaleString()}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total Indexing Time</span>
          <span className="stat-value">{analytics.total_indexing_time}s</span>
        </div>
      </div>

      {/* Charts */}
      <div className="chart-grid">
        {/* Language Distribution */}
        {languageData.length > 0 && (
          <div className="chart-card">
            <h3 className="chart-title">Language Distribution</h3>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={languageData}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={100}
                  paddingAngle={3}
                  dataKey="value"
                  label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
                >
                  {languageData.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: '#1a1f35', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 8, color: '#f0f4ff' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Indexing Time Breakdown */}
        {repoTimingData.length > 0 && (
          <div className="chart-card">
            <h3 className="chart-title">Indexing Time Breakdown (seconds)</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={repoTimingData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#94a3b8', fontSize: 12 }} width={120} />
                <Tooltip contentStyle={{ background: '#1a1f35', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 8, color: '#f0f4ff' }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="Parse" stackId="a" fill="#3b82f6" radius={[0, 0, 0, 0]} />
                <Bar dataKey="Embed" stackId="a" fill="#8b5cf6" />
                <Bar dataKey="Graph" stackId="a" fill="#22c55e" />
                <Bar dataKey="Vector" stackId="a" fill="#f97316" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Code Elements by Repo */}
        {repoElementsData.length > 0 && (
          <div className="chart-card">
            <h3 className="chart-title">Code Elements by Repository</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={repoElementsData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                <XAxis dataKey="name" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <Tooltip contentStyle={{ background: '#1a1f35', border: '1px solid rgba(148,163,184,0.12)', borderRadius: 8, color: '#f0f4ff' }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="Functions" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Methods" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Classes" fill="#22c55e" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Comparison Card */}
        <div className="chart-card">
          <h3 className="chart-title">CodeIndexer vs Traditional Retrieval</h3>
          <div style={{ padding: '20px 0' }}>
            <table style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Traditional</th>
                  <th>CodeIndexer</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Search Method</td>
                  <td><span className="badge badge-orange">Keyword Only</span></td>
                  <td><span className="badge badge-blue">Hybrid (BM25 + Vector + Graph)</span></td>
                </tr>
                <tr>
                  <td>Re-ranking</td>
                  <td><span className="badge badge-orange">None</span></td>
                  <td><span className="badge badge-green">Cross-Encoder Neural</span></td>
                </tr>
                <tr>
                  <td>Context Compression</td>
                  <td><span className="badge badge-orange">None</span></td>
                  <td><span className="badge badge-green">LLM-powered (80%+ reduction)</span></td>
                </tr>
                <tr>
                  <td>Code Understanding</td>
                  <td><span className="badge badge-orange">Text Match</span></td>
                  <td><span className="badge badge-purple">AST + Graph + Semantic</span></td>
                </tr>
                <tr>
                  <td>Incremental Update</td>
                  <td><span className="badge badge-orange">Full Re-index</span></td>
                  <td><span className="badge badge-green">File Hash Diffing</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
