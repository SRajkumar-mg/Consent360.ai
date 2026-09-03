import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { CookieBanner } from '../components/CookieBanner'

interface PlatformState {
  showConsentModal?: boolean
  customerName?: string
  customerId?: number
  created?: boolean
}

interface Challenge {
  id: number
  title: string
  difficulty: 'Easy' | 'Medium' | 'Hard'
  acceptance: number
  tags: string[]
  solved: boolean
}

const NAV_ITEMS = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
    ),
  },
  {
    id: 'problems',
    label: 'Problems',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
      </svg>
    ),
  },
  {
    id: 'contests',
    label: 'Contests',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" /><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
        <path d="M4 22h16" /><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
        <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" /><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" />
      </svg>
    ),
  },
  {
    id: 'discuss',
    label: 'Discuss',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    id: 'submissions',
    label: 'Submissions',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
      </svg>
    ),
  },
]

const STATS = [
  { label: 'Problems Solved', value: '128' },
  { label: 'Current Streak', value: '7 days' },
  { label: 'Contest Rating', value: '1642' },
  { label: 'Global Rank', value: '#12,403' },
]

const CHALLENGES: Challenge[] = [
  { id: 1, title: 'Two Sum', difficulty: 'Easy', acceptance: 54.2, tags: ['Array', 'Hash Table'], solved: true },
  { id: 2, title: 'Longest Substring Without Repeating Characters', difficulty: 'Medium', acceptance: 34.5, tags: ['String', 'Sliding Window'], solved: false },
  { id: 3, title: 'Median of Two Sorted Arrays', difficulty: 'Hard', acceptance: 35.1, tags: ['Array', 'Binary Search'], solved: false },
  { id: 4, title: 'Valid Parentheses', difficulty: 'Easy', acceptance: 40.8, tags: ['String', 'Stack'], solved: true },
  { id: 5, title: 'Merge k Sorted Lists', difficulty: 'Hard', acceptance: 38.2, tags: ['Linked List', 'Heap'], solved: false },
  { id: 6, title: 'Binary Tree Level Order Traversal', difficulty: 'Medium', acceptance: 62.4, tags: ['Tree', 'BFS'], solved: false },
]

export function PlatformPage() {
  const location = useLocation()
  const state = (location.state ?? null) as PlatformState | null
  const [activeNav, setActiveNav] = useState('dashboard')
  const [consentOpen, setConsentOpen] = useState(
    () => !!state?.showConsentModal && typeof state?.customerId === 'number',
  )

  return (
    <div className="platform-page">
      <header className="platform-header">
        <div className="brand">
          <span className="brand-mark">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="16 18 22 12 16 6" />
              <polyline points="8 6 2 12 8 18" />
            </svg>
          </span>
          <div>
            <div className="brand-title">Codex</div>
            <div className="brand-sub">Code. Compete. Collaborate.</div>
          </div>
        </div>
        <div className="header-right">
          <span className="header-avatar">{(state?.customerName || 'C').charAt(0).toUpperCase()}</span>
          <span className="header-name">{state?.customerName || 'Coder'}</span>
          <Link to="/" className="signout-btn">Sign out</Link>
        </div>
      </header>

      <div className="platform-layout">
        <aside className="platform-sidebar">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`sidebar-item${activeNav === item.id ? ' active' : ''}`}
              onClick={() => setActiveNav(item.id)}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
          <div className="sidebar-footer">v0.1.0</div>
        </aside>

        <main className="platform-main">
          <div className="welcome-row">
            <div>
              <h1>Welcome back, {state?.customerName || 'Coder'}</h1>
              <p>Here is your progress for today. Keep the streak alive!</p>
            </div>
          </div>

          <div className="stat-strip">
            {STATS.map((s) => (
              <div className="stat-pill" key={s.label}>
                <b>{s.value}</b>
                <span>{s.label}</span>
              </div>
            ))}
          </div>

          <section className="daily-card">
            <div className="daily-info">
              <span className="daily-kicker">Daily Challenge · Aug 21</span>
              <h2>Two Sum</h2>
              <p>
                Given an array of integers <code>nums</code> and an integer <code>target</code>, return indices of
                the two numbers such that they add up to <code>target</code>.
              </p>
              <div className="daily-meta">
                <span className="diff-badge diff-easy">Easy</span>
                <span className="accept-rate">54.2% acceptance</span>
              </div>
              <button className="btn btn-primary daily-solve">Solve Challenge</button>
            </div>
            <div className="daily-code">
              <div className="code-titlebar">
                <span className="dot dot-red" /><span className="dot dot-amber" /><span className="dot dot-green" />
                <span className="code-filename">solution.py</span>
              </div>
              <div className="code-block">
                <div><span className="code-kw">def</span> <span className="code-fn">two_sum</span><span className="code-pl">(nums, target):</span></div>
                <div><span className="code-pl">    seen = {'{}'}</span></div>
                <div><span className="code-pl">    </span><span className="code-kw">for</span><span className="code-pl"> i, n </span><span className="code-kw">in</span><span className="code-pl"> </span><span className="code-fn">enumerate</span><span className="code-pl">(nums):</span></div>
                <div><span className="code-pl">        </span><span className="code-kw">if</span><span className="code-pl"> target - n </span><span className="code-kw">in</span><span className="code-pl"> seen:</span></div>
                <div><span className="code-pl">            </span><span className="code-kw">return</span><span className="code-pl"> [seen[target - n], i]</span></div>
                <div><span className="code-pl">        seen[n] = i</span></div>
                <div><span className="code-cm">    # O(n) time, O(n) space</span></div>
              </div>
            </div>
          </section>

          <div className="section-head">
            <h2>Recommended for you</h2>
            <span className="muted-note">Based on your recent activity</span>
          </div>

          <div className="cards">
            {CHALLENGES.map((c) => (
              <article className="card challenge-card" key={c.id}>
                <div className="challenge-top">
                  <span className={`diff-badge diff-${c.difficulty.toLowerCase()}`}>{c.difficulty}</span>
                  {c.solved && <span className="solved-check" title="Solved">✓ Solved</span>}
                </div>
                <h3 className="challenge-title">{c.title}</h3>
                <div className="tag-row">
                  {c.tags.map((t) => (
                    <span className="tag" key={t}>{t}</span>
                  ))}
                </div>
                <div className="challenge-bottom">
                  <span className="accept-rate">{c.acceptance}% acceptance</span>
                  <button className="btn btn-ghost btn-sm">Solve</button>
                </div>
              </article>
            ))}
          </div>
        </main>
      </div>

      {consentOpen && state?.customerId != null && (
        <CookieBanner customerId={state.customerId} onClose={() => setConsentOpen(false)} />
      )}
    </div>
  )
}
