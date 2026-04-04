import { Routes, Route, NavLink } from 'react-router-dom'
import { Database, MessageSquare, BarChart3, FolderGit2, Zap, GitCompareArrows } from 'lucide-react'
import IndexPage from './pages/IndexPage'
import ChatPage from './pages/ChatPage'
import DashboardPage from './pages/DashboardPage'
import ReposPage from './pages/ReposPage'
import ComparePage from './pages/ComparePage'

const navItems = [
  { to: '/', icon: Database, label: 'Index' },
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/compare', icon: GitCompareArrows, label: 'Compare' },
  { to: '/dashboard', icon: BarChart3, label: 'Dashboard' },
  { to: '/repos', icon: FolderGit2, label: 'Repositories' },
]

export default function App() {
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <a href="/" className="sidebar-logo">
            <span className="logo-icon"><Zap size={18} /></span>
            CodeIndexer
          </a>
        </div>
        <nav className="sidebar-nav">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              <Icon className="nav-icon" size={20} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          CodeIndexer v2.0 · Advanced Code Intelligence
        </div>
      </aside>

      <main className="main-content">
        <Routes>
          <Route path="/" element={<IndexPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/repos" element={<ReposPage />} />
        </Routes>
      </main>
    </div>
  )
}
