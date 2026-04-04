import { useState, useEffect } from 'react'
import { FolderGit2, Trash2, Clock, Code2, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

export default function ReposPage() {
  const [repos, setRepos] = useState([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const fetchRepos = () => {
    setLoading(true)
    fetch('/api/repositories')
      .then(r => r.json())
      .then(data => { setRepos(data.repositories || []); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { fetchRepos() }, [])

  const handleDelete = async (repoName) => {
    if (!confirm(`Delete repository "${repoName}"?`)) return
    await fetch(`/api/repositories/${encodeURIComponent(repoName)}`, { method: 'DELETE' })
    fetchRepos()
  }

  if (loading) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 40, height: 40 }} />
        <h3 style={{ marginTop: 16 }}>Loading repositories...</h3>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Indexed Repositories</h1>
          <p className="page-subtitle">Manage your indexed codebases. Click a repo to start chatting about it.</p>
        </div>
        <button className="btn btn-outline" onClick={fetchRepos}>
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {repos.length === 0 ? (
        <div className="empty-state">
          <FolderGit2 className="empty-icon" size={64} />
          <h3>No repositories indexed</h3>
          <p>Go to the Index page and provide a GitHub URL or local path to get started.</p>
        </div>
      ) : (
        <div className="card">
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Elements</th>
                  <th>Lines</th>
                  <th>Languages</th>
                  <th>Indexing Time</th>
                  <th>Indexed At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {repos.map(repo => (
                  <tr key={repo.repo_name}>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <FolderGit2 size={16} style={{ color: 'var(--accent-blue)' }} />
                        <strong style={{ color: 'var(--text-primary)', cursor: 'pointer' }}
                          onClick={() => navigate(`/chat`)}
                        >
                          {repo.repo_name}
                        </strong>
                        {repo.is_incremental && <span className="badge badge-green">Incremental</span>}
                      </div>
                    </td>
                    <td>
                      <span style={{ color: 'var(--text-primary)' }}>{repo.total_elements}</span>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                        {repo.functions}f · {repo.methods}m · {repo.classes}c
                      </div>
                    </td>
                    <td>{(repo.total_lines || 0).toLocaleString()}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {Object.keys(repo.languages || {}).map(lang => (
                          <span key={lang} className="badge badge-purple">{lang}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <Clock size={14} style={{ color: 'var(--text-muted)' }} />
                        {repo.indexing_time}s
                      </div>
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {repo.indexed_at ? new Date(repo.indexed_at).toLocaleString() : '—'}
                    </td>
                    <td>
                      <button
                        className="btn btn-danger"
                        style={{ padding: '6px 12px', fontSize: 12 }}
                        onClick={() => handleDelete(repo.repo_name)}
                      >
                        <Trash2 size={14} /> Delete
                      </button>
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
