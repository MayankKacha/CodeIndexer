import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User, MessageSquare } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function ChatPage() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [repos, setRepos] = useState([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const messagesEndRef = useRef(null)

  useEffect(() => {
    fetch('/api/repositories')
      .then(r => r.json())
      .then(data => setRepos(data.repositories || []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return

    const userMsg = { role: 'user', content: input.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setIsStreaming(true)

    // Add an empty AI message to stream into
    const aiMsgIndex = messages.length + 1
    setMessages(prev => [...prev, { role: 'ai', content: '' }])

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: userMsg.content, repo_name: selectedRepo }),
      })

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        accumulated += decoder.decode(value, { stream: true })
        setMessages(prev => {
          const updated = [...prev]
          updated[aiMsgIndex] = { role: 'ai', content: accumulated }
          return updated
        })
      }
    } catch (err) {
      setMessages(prev => {
        const updated = [...prev]
        updated[aiMsgIndex] = { role: 'ai', content: `Error: ${err.message}` }
        return updated
      })
    }

    setIsStreaming(false)
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Ask Your Codebase</h1>
        <p className="page-subtitle">Ask questions about your indexed repositories and get AI-powered answers.</p>
      </div>

      {repos.length > 0 && (
        <div className="repo-selector">
          <select
            value={selectedRepo}
            onChange={(e) => setSelectedRepo(e.target.value)}
          >
            <option value="">All Repositories</option>
            {repos.map(r => (
              <option key={r.repo_name} value={r.repo_name}>{r.repo_name}</option>
            ))}
          </select>
        </div>
      )}

      <div className="chat-container">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="empty-state">
              <MessageSquare className="empty-icon" size={64} />
              <h3>Start a conversation</h3>
              <p>Ask a question about your indexed codebase. For example: "How does the search pipeline work?" or "What does the CodeEncoder class do?"</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`chat-message ${msg.role}`}>
              <div className={`chat-avatar ${msg.role === 'ai' ? 'ai' : 'user'}`}>
                {msg.role === 'ai' ? <Bot size={18} /> : <User size={18} />}
              </div>
              <div className={`chat-bubble ${msg.role}`}>
                {msg.role === 'ai' ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content || '...'}</ReactMarkdown>
                ) : (
                  <p>{msg.content}</p>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-area">
          <div className="chat-input-row">
            <input
              type="text"
              className="input"
              placeholder="Ask a question about your codebase..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              disabled={isStreaming}
            />
            <button
              className="btn btn-primary"
              onClick={handleSend}
              disabled={isStreaming || !input.trim()}
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
