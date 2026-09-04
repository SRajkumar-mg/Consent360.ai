import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { IconX } from './icons'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

const SUGGESTIONS = [
  'How many active consents are there?',
  'What purposes require consent?',
  'Explain the consent lifecycle',
  'What does the DPDP Act say about consent?',
  'How many customers are expiring soon?',
  'What are the consent policies?',
]

export function Chatbot() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: 'assistant',
      content:
        'Hello! I\'m the Consent360 AI Assistant. I can help you understand consent data, purposes, policies, and the DPDP Act. What would you like to know?',
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const send = useCallback(
    async (text?: string) => {
      const msg = (text || input).trim()
      if (!msg || loading) return
      setInput('')
      const userMsg: ChatMsg = { role: 'user', content: msg }
      setMessages((prev) => [...prev, userMsg])
      setLoading(true)
      try {
        const history = [...messages, userMsg].map((m) => ({
          role: m.role,
          content: m.content,
        }))
        const { data } = await api.post<{ reply: string }>('/chatbot', {
          message: msg,
          history,
        })
        setMessages((prev) => [...prev, { role: 'assistant', content: data.reply }])
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: 'Sorry, something went wrong. Please try again.',
          },
        ])
      } finally {
        setLoading(false)
      }
    },
    [input, loading, messages],
  )

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      <button
        className="chatbot-fab"
        onClick={() => setOpen((o) => !o)}
        title="Consent360 AI Assistant"
      >
        {open ? <IconX size={22} /> : (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        )}
      </button>

      {open && (
        <div className="chatbot-panel">
          <div className="chatbot-header">
            <div className="chatbot-header-info">
              <div className="chatbot-avatar">CH</div>
              <div>
                <div className="chatbot-title">Consent360 Assistant</div>
                <div className="chatbot-subtitle">Ask about consents, policies & DPDP</div>
              </div>
            </div>
            <button className="icon-btn" onClick={() => setOpen(false)}>
              <IconX size={16} />
            </button>
          </div>

          <div className="chatbot-messages">
            {messages.map((m, i) => (
              <div key={i} className={`chatbot-msg ${m.role}`}>
                {m.role === 'assistant' && <div className="chatbot-msg-avatar">CH</div>}
                <div className="chatbot-bubble">
                  <div className="chatbot-msg-text" dangerouslySetInnerHTML={{ __html: formatReply(m.content) }} />
                </div>
              </div>
            ))}
            {loading && (
              <div className="chatbot-msg assistant">
                <div className="chatbot-msg-avatar">CH</div>
                <div className="chatbot-bubble">
                  <div className="chatbot-typing">
                    <span /><span /><span />
                  </div>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {messages.length <= 1 && (
            <div className="chatbot-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chatbot-suggestion" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}

          <div className="chatbot-input-row">
            <textarea
              ref={inputRef}
              className="chatbot-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask about consents, purposes, policies..."
              rows={1}
            />
            <button
              className="chatbot-send"
              onClick={() => send()}
              disabled={loading || !input.trim()}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </>
  )
}

function formatReply(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br/>')
}
