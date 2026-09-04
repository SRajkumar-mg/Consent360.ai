import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { consentApi, crmApi, getErrorMessage } from '../api'
import { CookieBanner } from '../components/CookieBanner'
import { COOKIE_CONSENT_KEY, CRM_LANG_KEY } from '../languages'

const AGE_VERIFIED_KEY = 'jobhub_age_verified'
import type { CrmCustomer } from '../types'

interface LocationState {
  showConsentModal?: boolean
  customerName?: string
  customerId?: number
  created?: boolean
}

interface CustomerPrefs {
  lang?: string
  categories?: Record<string, boolean>
}

const MOCK_JOBS = [
  { id: 1, title: 'Senior Frontend Engineer', company: 'TechCorp India', location: 'Bangalore, Karnataka', type: 'Full-time', salary: '₹18L - ₹28L', posted: '2d ago', applicants: 47, logo: 'TC', color: '#2563eb', desc: 'Join our team building next-generation web applications using React, TypeScript, and modern tooling.' },
  { id: 2, title: 'Product Manager', company: 'InnovateLabs', location: 'Mumbai, Maharashtra', type: 'Full-time', salary: '₹22L - ₹35L', posted: '1d ago', applicants: 32, logo: 'IL', color: '#7c3aed', desc: 'Lead product strategy for our B2B SaaS platform serving millions of users across India.' },
  { id: 3, title: 'Data Scientist', company: 'DataWave Analytics', location: 'Hyderabad, Telangana', type: 'Full-time', salary: '₹15L - ₹24L', posted: '5h ago', applicants: 18, logo: 'DW', color: '#0891b2', desc: 'Work with large-scale ML pipelines and predictive models for financial services clients.' },
  { id: 4, title: 'DevOps Engineer', company: 'CloudFirst Systems', location: 'Pune, Maharashtra', type: 'Full-time', salary: '₹14L - ₹22L', posted: '3d ago', applicants: 56, logo: 'CF', color: '#dc2626', desc: 'Manage and optimize our Kubernetes infrastructure across multiple cloud providers.' },
  { id: 5, title: 'UX Designer', company: 'PixelCraft Studio', location: 'Remote', type: 'Contract', salary: '₹12L - ₹18L', posted: '12h ago', applicants: 89, logo: 'PC', color: '#ea580c', desc: 'Design intuitive interfaces for mobile applications reaching 10M+ active users.' },
  { id: 6, title: 'Backend Engineer', company: 'ScaleStack', location: 'Chennai, Tamil Nadu', type: 'Full-time', salary: '₹16L - ₹26L', posted: '1d ago', applicants: 34, logo: 'SS', color: '#16a34a', desc: 'Build high-performance microservices handling 100K+ requests per second.' },
]

const TRENDING = [
  { tag: 'React.js', count: '2.4K jobs' },
  { tag: 'Python', count: '3.1K jobs' },
  { tag: 'Machine Learning', count: '1.8K jobs' },
  { tag: 'AWS', count: '2.9K jobs' },
  { tag: 'TypeScript', count: '1.5K jobs' },
]

const SUGGESTIONS = [
  { name: 'Priya Sharma', role: 'Software Engineer at Google', avatar: 'PS', color: '#2563eb' },
  { name: 'Arjun Patel', role: 'Product Manager at Microsoft', avatar: 'AP', color: '#7c3aed' },
  { name: 'Sneha Reddy', role: 'Data Scientist at Amazon', avatar: 'SR', color: '#ea580c' },
]

function formatInitials(name: string): string {
  return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
}

export function UsersPage() {
  const location = useLocation()
  const state = (location.state ?? {}) as LocationState
  const [users, setUsers] = useState<CrmCustomer[]>([])
  const [, setPrefs] = useState<Record<number, CustomerPrefs>>({})
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [bannerCustomerId, setBannerCustomerId] = useState<number | null>(state.customerId ?? null)
  const [activeTab, setActiveTab] = useState<'feed' | 'my-jobs' | 'network'>('feed')
  const [savedJobs, setSavedJobs] = useState<number[]>([])
  const [appliedJobs, setAppliedJobs] = useState<number[]>([])

  const userName = state.customerName || 'User'

  const load = async () => {
    try {
      const res = await crmApi.customers()
      setUsers(res.data)
      const results = await Promise.allSettled(
        res.data.map((u) => consentApi.getConsentPreferences(u.id))
      )
      const map: Record<number, CustomerPrefs> = {}
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          const p = (r.value.data?.preferences ?? {}) as CustomerPrefs
          if (p.categories && Object.keys(p.categories).length) map[res.data[i].id] = p
        }
      })
      setPrefs(map)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (localStorage.getItem(AGE_VERIFIED_KEY) !== 'true') {
      setError('Age verification required — please log in again and confirm you are 18 or older')
      setBannerCustomerId(null)
    }
  }, [])

  const persistPrefs = async (lang: string, categories: Record<string, boolean>) => {
    if (bannerCustomerId == null) return
    if (localStorage.getItem(AGE_VERIFIED_KEY) !== 'true') {
      setError('Age verification required — please log in again and confirm you are 18 or older')
      return
    }
    try {
      await consentApi.saveConsentPreferences(bannerCustomerId, { lang, categories })
      setPrefs((prev) => ({ ...prev, [bannerCustomerId]: { lang, categories } }))
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  const acceptAllCookies = async (lang: string) => {
    const categories = { necessary: true, functional: true, analytics: true, advertising: true }
    localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({ all: true, categories, at: Date.now() }))
    localStorage.setItem(CRM_LANG_KEY, lang)
    await persistPrefs(lang, categories)
    setBannerCustomerId(null)
  }

  const saveCookiePrefs = async (lang: string, categories: Record<string, boolean>) => {
    const cats = { ...categories, necessary: true }
    localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({ all: false, categories: cats, at: Date.now() }))
    localStorage.setItem(CRM_LANG_KEY, lang)
    await persistPrefs(lang, cats)
    setBannerCustomerId(null)
  }

  const toggleSaveJob = (id: number) => {
    setSavedJobs(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  const toggleApplyJob = (id: number) => {
    setAppliedJobs(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  return (
    <div className="jhub-page">
      {/* Top Navigation */}
      <nav className="jhub-nav">
        <div className="jhub-nav-inner">
          <div className="jhub-nav-brand">
            <div className="jhub-logo">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
                <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
              </svg>
            </div>
            <span className="jhub-brand-text">Consent360</span>
            <div className="jhub-search">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
              </svg>
              <input placeholder="Search jobs, people, companies..." value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
          <div className="jhub-nav-links">
            <button className={`jhub-nav-btn ${activeTab === 'feed' ? 'active' : ''}`} onClick={() => setActiveTab('feed')}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l1.5-5h15L21 9" /><path d="M3 9v11h18V9" /><path d="M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0" /></svg>
              <span>Jobs</span>
            </button>
            <button className={`jhub-nav-btn ${activeTab === 'network' ? 'active' : ''}`} onClick={() => setActiveTab('network')}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
              <span>Network</span>
            </button>
            <button className={`jhub-nav-btn ${activeTab === 'my-jobs' ? 'active' : ''}`} onClick={() => setActiveTab('my-jobs')}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
              <span>My Jobs</span>
            </button>
            <div className="jhub-nav-divider" />
            <div className="jhub-avatar-nav" title={userName}>
              {formatInitials(userName)}
            </div>
            <a className="jhub-signout" href="/" title="Sign out">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>
            </a>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <div className="jhub-layout">
        {/* Left Sidebar — Profile */}
        <aside className="jhub-sidebar-left">
          <div className="jhub-profile-card">
            <div className="jhub-profile-banner" />
            <div className="jhub-profile-avatar">{formatInitials(userName)}</div>
            <div className="jhub-profile-name">{userName}</div>
            <div className="jhub-profile-role">Job Seeker</div>
            <div className="jhub-profile-stats">
              <div className="jhub-stat"><span className="jhub-stat-num">{appliedJobs.length}</span><span>Applied</span></div>
              <div className="jhub-stat"><span className="jhub-stat-num">{savedJobs.length}</span><span>Saved</span></div>
              <div className="jhub-stat"><span className="jhub-stat-num">{users.length}</span><span>Connections</span></div>
            </div>
          </div>

          <div className="jhub-sidebar-card">
            <div className="jhub-sidebar-title">Recent Activity</div>
            <div className="jhub-activity-item">
              <div className="jhub-activity-dot" style={{ background: 'var(--success)' }} />
              <span>Profile viewed by 12 recruiters</span>
            </div>
            <div className="jhub-activity-item">
              <div className="jhub-activity-dot" style={{ background: 'var(--primary)' }} />
              <span>3 new job matches</span>
            </div>
            <div className="jhub-activity-item">
              <div className="jhub-activity-dot" style={{ background: 'var(--warning)' }} />
              <span>Application update</span>
            </div>
          </div>
        </aside>

        {/* Main Feed */}
        <main className="jhub-feed">
          {/* Tab Content */}
          {activeTab === 'feed' && (
            <>
              <div className="jhub-feed-header">
                <h2>Recommended for you</h2>
                <span className="jhub-feed-count">{MOCK_JOBS.length} jobs</span>
              </div>
              {MOCK_JOBS.map((job) => (
                <div className="jhub-job-card" key={job.id}>
                  <div className="jhub-job-top">
                    <div className="jhub-company-logo" style={{ background: job.color }}>{job.logo}</div>
                    <div className="jhub-job-info">
                      <div className="jhub-job-title">{job.title}</div>
                      <div className="jhub-job-company">{job.company}</div>
                      <div className="jhub-job-meta">
                        <span>{job.location}</span>
                        <span className="jhub-meta-dot" />
                        <span>{job.type}</span>
                        <span className="jhub-meta-dot" />
                        <span>{job.salary}</span>
                      </div>
                    </div>
                    <button
                      className={`jhub-save-btn ${savedJobs.includes(job.id) ? 'saved' : ''}`}
                      onClick={() => toggleSaveJob(job.id)}
                      title={savedJobs.includes(job.id) ? 'Unsave' : 'Save'}
                    >
                      <svg width="20" height="20" viewBox="0 0 24 24" fill={savedJobs.includes(job.id) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
                      </svg>
                    </button>
                  </div>
                  <p className="jhub-job-desc">{job.desc}</p>
                  <div className="jhub-job-bottom">
                    <span className="jhub-job-posted">{job.posted} · {job.applicants} applicants</span>
                    <button
                      className={`jhub-apply-btn ${appliedJobs.includes(job.id) ? 'applied' : ''}`}
                      onClick={() => toggleApplyJob(job.id)}
                    >
                      {appliedJobs.includes(job.id) ? '✓ Applied' : 'Easy Apply'}
                    </button>
                  </div>
                </div>
              ))}
            </>
          )}

          {activeTab === 'my-jobs' && (
            <>
              <div className="jhub-feed-header">
                <h2>My Jobs</h2>
              </div>
              {appliedJobs.length === 0 && savedJobs.length === 0 ? (
                <div className="jhub-empty-state">
                  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--muted)' }}>
                    <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
                    <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
                  </svg>
                  <h3>No jobs yet</h3>
                  <p>Start applying to jobs or save them for later</p>
                  <button className="jhub-apply-btn" onClick={() => setActiveTab('feed')}>Browse Jobs</button>
                </div>
              ) : (
                <>
                  {appliedJobs.length > 0 && (
                    <div className="jhub-myjobs-section">
                      <h3>Applied ({appliedJobs.length})</h3>
                      {MOCK_JOBS.filter(j => appliedJobs.includes(j.id)).map(job => (
                        <div className="jhub-job-card compact" key={job.id}>
                          <div className="jhub-job-top">
                            <div className="jhub-company-logo sm" style={{ background: job.color }}>{job.logo}</div>
                            <div className="jhub-job-info">
                              <div className="jhub-job-title">{job.title}</div>
                              <div className="jhub-job-company">{job.company}</div>
                            </div>
                            <span className="jhub-applied-badge">Applied</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {savedJobs.length > 0 && (
                    <div className="jhub-myjobs-section">
                      <h3>Saved ({savedJobs.length})</h3>
                      {MOCK_JOBS.filter(j => savedJobs.includes(j.id)).map(job => (
                        <div className="jhub-job-card compact" key={job.id}>
                          <div className="jhub-job-top">
                            <div className="jhub-company-logo sm" style={{ background: job.color }}>{job.logo}</div>
                            <div className="jhub-job-info">
                              <div className="jhub-job-title">{job.title}</div>
                              <div className="jhub-job-company">{job.company}</div>
                            </div>
                            <button className="jhub-apply-btn" onClick={() => toggleApplyJob(job.id)}>Easy Apply</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {activeTab === 'network' && (
            <>
              <div className="jhub-feed-header">
                <h2>People you may know</h2>
              </div>
              <div className="jhub-network-grid">
                {SUGGESTIONS.map((s, i) => (
                  <div className="jhub-network-card" key={i}>
                    <div className="jhub-network-avatar" style={{ background: s.color }}>{s.avatar}</div>
                    <div className="jhub-network-name">{s.name}</div>
                    <div className="jhub-network-role">{s.role}</div>
                    <button className="jhub-connect-btn">Connect</button>
                  </div>
                ))}
                {users.slice(0, 6).map((u) => (
                  <div className="jhub-network-card" key={u.id}>
                    <div className="jhub-network-avatar" style={{ background: ['#2563eb','#7c3aed','#ea580c','#16a34a','#dc2626','#0891b2'][u.id % 6] }}>
                      {formatInitials(u.name)}
                    </div>
                    <div className="jhub-network-name">{u.name}</div>
                    <div className="jhub-network-role">{u.email}</div>
                    <button className="jhub-connect-btn">Connect</button>
                  </div>
                ))}
              </div>
            </>
          )}
        </main>

        {/* Right Sidebar */}
        <aside className="jhub-sidebar-right">
          <div className="jhub-sidebar-card">
            <div className="jhub-sidebar-title">Trending Skills</div>
            {TRENDING.map((t, i) => (
              <div className="jhub-trending-item" key={i}>
                <span className="jhub-trending-tag">#{t.tag}</span>
                <span className="jhub-trending-count">{t.count}</span>
              </div>
            ))}
          </div>

          <div className="jhub-sidebar-card">
            <div className="jhub-sidebar-title">People Also Viewed</div>
            {SUGGESTIONS.map((s, i) => (
              <div className="jhub-pav-item" key={i}>
                <div className="jhub-pav-avatar" style={{ background: s.color }}>{s.avatar}</div>
                <div>
                  <div className="jhub-pav-name">{s.name}</div>
                  <div className="jhub-pav-role">{s.role}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="jhub-privacy-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2l8 4v6c0 5.25-3.4 9.25-8 10-4.6-.75-8-4.75-8-10V6l8-4z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
            Consent managed via Consent360
          </div>
        </aside>
      </div>

      {error && <div className="jhub-error-toast">{error}</div>}

      {bannerCustomerId != null && (
        <CookieBanner customerId={bannerCustomerId} onAcceptAll={acceptAllCookies} onSave={saveCookiePrefs} />
      )}
    </div>
  )
}
