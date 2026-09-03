import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getErrorMessage } from '../api'
import { CookieBanner } from '../components/CookieBanner'

interface LocationState {
  showConsentModal?: boolean
  customerName?: string
  customerId?: number
  created?: boolean
}

type Tab = 'dashboard' | 'my-courses' | 'browse' | 'certificates' | 'discussion'

interface Course {
  id: number
  title: string
  instructor: string
  category: string
  level: 'Beginner' | 'Intermediate' | 'Advanced'
  lessons: number
  duration: string
  rating: number
  learners: string
  color: string
  initials: string
  desc: string
  progress: number
}

const COURSES: Course[] = [
  { id: 1, title: 'React Foundations', instructor: 'Ananya Iyer', category: 'Web Development', level: 'Beginner', lessons: 42, duration: '12h 30m', rating: 4.8, learners: '24.5K', color: '#10b981', initials: 'RF', desc: 'Components, hooks, routing and state management — everything you need to build modern React apps.', progress: 68 },
  { id: 2, title: 'Python for Data Science', instructor: 'Rohit Verma', category: 'Data Science', level: 'Intermediate', lessons: 56, duration: '18h 45m', rating: 4.9, learners: '31.2K', color: '#f59e0b', initials: 'PY', desc: 'NumPy, pandas, matplotlib and real-world datasets — analyse data with confidence.', progress: 100 },
  { id: 3, title: 'UI/UX Design Essentials', instructor: 'Meera Nair', category: 'Design', level: 'Beginner', lessons: 35, duration: '9h 15m', rating: 4.7, learners: '18.9K', color: '#8b5cf6', initials: 'UX', desc: 'Design principles, wireframing, prototyping and usability testing for beautiful products.', progress: 24 },
  { id: 4, title: 'Machine Learning A-Z', instructor: 'Dr. Arjun Mehta', category: 'AI & ML', level: 'Advanced', lessons: 78, duration: '26h 10m', rating: 4.9, learners: '42.7K', color: '#ec4899', initials: 'ML', desc: 'From regression to deep learning — train, evaluate and deploy real ML models.', progress: 0 },
  { id: 5, title: 'Cloud & DevOps with AWS', instructor: 'Kavya Reddy', category: 'Cloud', level: 'Intermediate', lessons: 48, duration: '15h 20m', rating: 4.6, learners: '15.3K', color: '#38bdf8', initials: 'AW', desc: 'EC2, S3, Lambda, CI/CD pipelines and infrastructure as code on Amazon Web Services.', progress: 0 },
  { id: 6, title: 'Digital Marketing Mastery', instructor: 'Sanjay Gupta', category: 'Marketing', level: 'Beginner', lessons: 30, duration: '8h 40m', rating: 4.5, learners: '12.1K', color: '#fb7185', initials: 'DM', desc: 'SEO, social media, content strategy and analytics to grow any brand online.', progress: 0 },
]

interface Certificate {
  id: number
  course: string
  issued: string
  credential: string
  grade: string
}

const CERTIFICATES: Certificate[] = [
  { id: 1, course: 'Python for Data Science', issued: 'Jul 12, 2026', credential: 'SL-PYDS-2026-8841', grade: 'A+' },
  { id: 2, course: 'JavaScript Essentials', issued: 'Mar 03, 2026', credential: 'SL-JSES-2026-5127', grade: 'A' },
]

interface Thread {
  id: number
  author: string
  initials: string
  color: string
  course: string
  time: string
  title: string
  excerpt: string
  replies: number
  likes: number
}

const INITIAL_THREADS: Thread[] = [
  { id: 1, author: 'Priya Sharma', initials: 'PS', color: '#10b981', course: 'React Foundations', time: '2h ago', title: 'Best practices for managing complex form state?', excerpt: 'I am building a multi-step form and unsure whether to keep state in a parent component or use context…', replies: 12, likes: 34 },
  { id: 2, author: 'Arjun Patel', initials: 'AP', color: '#8b5cf6', course: 'Machine Learning A-Z', time: '5h ago', title: 'Week 4 assignment — gradient descent not converging', excerpt: 'My loss plateaus around 0.4 after 50 epochs. I have tried lowering the learning rate to 0.001…', replies: 8, likes: 21 },
  { id: 3, author: 'Sneha Reddy', initials: 'SR', color: '#f59e0b', course: 'UI/UX Design Essentials', time: '1d ago', title: 'Sharing my wireframes for the food delivery app capstone', excerpt: 'Would love feedback on the checkout flow. I went with a two-step process instead of a single page…', replies: 17, likes: 48 },
]

function Star() {
  return (
    <svg className="sl-star" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  )
}

function formatInitials(name: string): string {
  return name.split(' ').map((w) => w[0]).join('').toUpperCase().slice(0, 2)
}

const NAV_ITEMS: { id: Tab; label: string; icon: ReactNode }[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    id: 'my-courses',
    label: 'My Courses',
    icon: (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
      </svg>
    ),
  },
  {
    id: 'browse',
    label: 'Browse Courses',
    icon: (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
      </svg>
    ),
  },
  {
    id: 'certificates',
    label: 'Certificates',
    icon: (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="8" r="7" /><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88" />
      </svg>
    ),
  },
  {
    id: 'discussion',
    label: 'Discussion',
    icon: (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z" />
      </svg>
    ),
  },
]

function CourseCard({ course, enrolled, onEnroll }: { course: Course; enrolled: boolean; onEnroll: (id: number) => void }) {
  return (
    <article className="sl-course-card">
      <div className="sl-course-banner" style={{ background: `linear-gradient(135deg, ${course.color}33, ${course.color}11)` }}>
        <span className="sl-course-thumb" style={{ background: course.color }}>{course.initials}</span>
        <span className={`sl-badge lv-${course.level.toLowerCase()}`}>{course.level}</span>
      </div>
      <div className="sl-course-body">
        <div className="sl-course-cat">{course.category}</div>
        <h3 className="sl-course-title">{course.title}</h3>
        <div className="sl-course-instructor">by {course.instructor}</div>
        <div className="sl-course-meta">
          <span className="sl-meta-item"><Star /> {course.rating}</span>
          <span className="sl-meta-item">{course.lessons} lessons</span>
          <span className="sl-meta-item">{course.duration}</span>
          <span className="sl-meta-item">{course.learners} learners</span>
        </div>
        <p className="sl-course-desc">{course.desc}</p>
        <div className="sl-course-foot">
          {enrolled ? (
            <span className="sl-enrolled-note">✓ Enrolled{course.progress > 0 ? ` · ${course.progress}% complete` : ''}</span>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={() => onEnroll(course.id)}>Enroll now</button>
          )}
        </div>
      </div>
    </article>
  )
}

export function PlatformPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const state = (location.state ?? {}) as LocationState

  const userName = state.customerName || 'Learner'
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const [courses, setCourses] = useState<Course[]>(COURSES)
  const [enrolledIds, setEnrolledIds] = useState<number[]>([1, 2, 3])
  const [threads, setThreads] = useState<Thread[]>(INITIAL_THREADS)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [bannerCustomerId, setBannerCustomerId] = useState<number | null>(
    state.showConsentModal && state.customerId != null ? state.customerId : null,
  )

  useEffect(() => {
    if (!state.customerId) navigate('/', { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const firstName = userName.split(' ')[0]
  const q = query.trim().toLowerCase()
  const matchesQuery = (c: Course) =>
    !q || c.title.toLowerCase().includes(q) || c.instructor.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)

  const enrolledCourses = courses.filter((c) => enrolledIds.includes(c.id))
  const inProgressCourses = enrolledCourses.filter((c) => c.progress > 0 && c.progress < 100)
  const recommended = courses.filter((c) => !enrolledIds.includes(c.id) && matchesQuery(c))
  const hoursLearned = Math.round(
    enrolledCourses.reduce((acc, c) => acc + (c.progress / 100) * parseInt(c.duration, 10), 0),
  )

  const enroll = (id: number) => setEnrolledIds((prev) => (prev.includes(id) ? prev : [...prev, id]))

  const continueCourse = (id: number) =>
    setCourses((prev) => prev.map((c) => (c.id === id ? { ...c, progress: Math.min(100, c.progress + 10) } : c)))

  const postThread = (e: FormEvent) => {
    e.preventDefault()
    const title = draft.trim()
    if (!title) return
    setThreads((prev) => [
      {
        id: Date.now(),
        author: userName,
        initials: formatInitials(userName),
        color: '#10b981',
        course: 'General',
        time: 'just now',
        title,
        excerpt: '',
        replies: 0,
        likes: 0,
      },
      ...prev,
    ])
    setDraft('')
  }

  return (
    <div className="sl-page">
      {/* Top bar */}
      <nav className="sl-topbar">
        <div className="sl-topbar-inner">
          <div className="sl-brand">
            <div className="sl-brand-mark">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
                <path d="M6 12v5c0 2 2 3 6 3s6-1 6-3v-5" />
              </svg>
            </div>
            <span className="sl-brand-name">Skill<em>Learn</em></span>
            <div className="sl-search">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
              </svg>
              <input placeholder="Search courses, instructors…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          </div>
          <div className="sl-topbar-right">
            <div className="sl-avatar" title={userName}>{formatInitials(userName)}</div>
            <a className="sl-signout" href="/" title="Sign out">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </a>
          </div>
        </div>
      </nav>

      {/* Layout */}
      <div className="sl-layout">
        {/* Sidebar */}
        <aside className="sl-sidebar">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`sl-nav-item ${activeTab === item.id ? 'active' : ''}`}
              onClick={() => setActiveTab(item.id)}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
          <div className="sl-sidebar-foot">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2l8 4v6c0 5.25-3.4 9.25-8 10-4.6-.75-8-4.75-8-10V6l8-4z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
            Consent managed via Consent360
          </div>
        </aside>

        {/* Main */}
        <main className="sl-main">
          {activeTab === 'dashboard' && (
            <>
              <section className="sl-hero">
                <div className="sl-kicker">Learning dashboard</div>
                <h1>Welcome back, {firstName}</h1>
                <p>You are on a 12-day learning streak. Pick up where you left off or explore something new.</p>
                <div className="sl-hero-actions">
                  <button className="btn btn-primary" onClick={() => setActiveTab('my-courses')}>Continue learning</button>
                  <button className="btn btn-ghost" onClick={() => setActiveTab('browse')}>Browse catalog</button>
                </div>
              </section>

              <div className="sl-stats">
                <div className="sl-stat-card">
                  <span className="sl-stat-icon emerald">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" /></svg>
                  </span>
                  <div className="sl-stat-info"><span className="sl-stat-value">{inProgressCourses.length}</span><span className="sl-stat-label">In progress</span></div>
                </div>
                <div className="sl-stat-card">
                  <span className="sl-stat-icon sky">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
                  </span>
                  <div className="sl-stat-info"><span className="sl-stat-value">{hoursLearned}h</span><span className="sl-stat-label">Hours learned</span></div>
                </div>
                <div className="sl-stat-card">
                  <span className="sl-stat-icon amber">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="7" /><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88" /></svg>
                  </span>
                  <div className="sl-stat-info"><span className="sl-stat-value">{CERTIFICATES.length}</span><span className="sl-stat-label">Certificates</span></div>
                </div>
                <div className="sl-stat-card">
                  <span className="sl-stat-icon rose">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" /></svg>
                  </span>
                  <div className="sl-stat-info"><span className="sl-stat-value">12</span><span className="sl-stat-label">Day streak</span></div>
                </div>
              </div>

              {inProgressCourses.length > 0 && (
                <>
                  <div className="sl-section-head">
                    <h2 className="sl-section-title">Continue learning</h2>
                    <span className="sl-section-count">{inProgressCourses.length} active</span>
                  </div>
                  <div className="sl-my-list">
                    {inProgressCourses.map((c) => (
                      <div className="sl-my-course-row" key={c.id}>
                        <span className="sl-course-thumb sm" style={{ background: c.color }}>{c.initials}</span>
                        <div className="sl-my-course-info">
                          <div className="sl-my-course-title">{c.title}</div>
                          <div className="sl-my-course-sub">{c.lessons} lessons · {c.duration} · {c.instructor}</div>
                          <div className="sl-progress"><span style={{ width: `${c.progress}%` }} /></div>
                        </div>
                        <div className="sl-my-course-side">
                          <span className="sl-progress-num">{c.progress}%</span>
                          <button className="btn btn-primary btn-sm" onClick={() => continueCourse(c.id)}>Continue</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="sl-section-head">
                <h2 className="sl-section-title">Recommended for you</h2>
                <span className="sl-section-count">{recommended.length} courses</span>
              </div>
              <div className="sl-course-grid">
                {recommended.map((c) => (
                  <CourseCard key={c.id} course={c} enrolled={false} onEnroll={enroll} />
                ))}
                {recommended.length === 0 && (
                  <div className="sl-empty">
                    <h3>No matches found</h3>
                    <p>Try a different search term to discover more courses</p>
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === 'my-courses' && (
            <>
              <div className="sl-section-head">
                <h2 className="sl-section-title">My Courses</h2>
                <span className="sl-section-count">{enrolledCourses.length} enrolled</span>
              </div>
              {enrolledCourses.length === 0 ? (
                <div className="sl-empty">
                  <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
                    <path d="M6 12v5c0 2 2 3 6 3s6-1 6-3v-5" />
                  </svg>
                  <h3>No courses yet</h3>
                  <p>Enroll in a course to start your learning journey</p>
                  <button className="btn btn-primary btn-sm" onClick={() => setActiveTab('browse')}>Browse Courses</button>
                </div>
              ) : (
                <div className="sl-my-list">
                  {enrolledCourses.map((c) => (
                    <div className="sl-my-course-row" key={c.id}>
                      <span className="sl-course-thumb sm" style={{ background: c.color }}>{c.initials}</span>
                      <div className="sl-my-course-info">
                        <div className="sl-my-course-title">{c.title}</div>
                        <div className="sl-my-course-sub">{c.lessons} lessons · {c.duration} · {c.instructor}</div>
                        <div className="sl-progress"><span style={{ width: `${c.progress}%` }} /></div>
                      </div>
                      <div className="sl-my-course-side">
                        <span className="sl-progress-num">{c.progress}%</span>
                        {c.progress >= 100 ? (
                          <span className="sl-enrolled-note">✓ Completed</span>
                        ) : (
                          <button className="btn btn-primary btn-sm" onClick={() => continueCourse(c.id)}>Continue</button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {activeTab === 'browse' && (
            <>
              <div className="sl-section-head">
                <h2 className="sl-section-title">Browse Courses</h2>
                <span className="sl-section-count">{courses.filter(matchesQuery).length} of {courses.length} courses</span>
              </div>
              <div className="sl-course-grid">
                {courses.filter(matchesQuery).map((c) => (
                  <CourseCard key={c.id} course={c} enrolled={enrolledIds.includes(c.id)} onEnroll={enroll} />
                ))}
                {courses.filter(matchesQuery).length === 0 && (
                  <div className="sl-empty">
                    <h3>No matches found</h3>
                    <p>Try a different search term to discover more courses</p>
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === 'certificates' && (
            <>
              <div className="sl-section-head">
                <h2 className="sl-section-title">Certificates</h2>
                <span className="sl-section-count">{CERTIFICATES.length} earned</span>
              </div>
              <div className="sl-cert-grid">
                {CERTIFICATES.map((cert) => (
                  <div className="sl-cert-card" key={cert.id}>
                    <span className="sl-cert-icon">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="8" r="7" /><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88" />
                      </svg>
                    </span>
                    <div className="sl-cert-info">
                      <div className="sl-cert-name">{cert.course}</div>
                      <div className="sl-cert-meta">Issued {cert.issued} · ID {cert.credential}</div>
                    </div>
                    <span className="sl-cert-grade">{cert.grade}</span>
                  </div>
                ))}
              </div>
              <div className="sl-cert-note">
                Complete 100% of a course to earn a verified certificate with a unique credential ID.
              </div>
            </>
          )}

          {activeTab === 'discussion' && (
            <>
              <div className="sl-section-head">
                <h2 className="sl-section-title">Discussion</h2>
                <span className="sl-section-count">{threads.length} threads</span>
              </div>
              <form className="sl-composer" onSubmit={postThread}>
                <textarea
                  className="input"
                  placeholder={`Ask a question or share an insight, ${firstName}…`}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button className="btn btn-primary btn-sm" type="submit" disabled={!draft.trim()}>Post Thread</button>
              </form>
              <div className="sl-threads">
                {threads.map((t) => (
                  <article className="sl-thread" key={t.id}>
                    <span className="sl-thread-avatar" style={{ background: t.color }}>{t.initials}</span>
                    <div className="sl-thread-body">
                      <div className="sl-thread-top">
                        <span className="sl-thread-author">{t.author}</span>
                        <span className="sl-thread-time">{t.time}</span>
                        <span className="sl-tag">{t.course}</span>
                      </div>
                      <h3 className="sl-thread-title">{t.title}</h3>
                      {t.excerpt && <p className="sl-thread-excerpt">{t.excerpt}</p>}
                      <div className="sl-thread-meta">
                        <span>{t.replies} replies</span>
                        <span>·</span>
                        <span>{t.likes} likes</span>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
        </main>
      </div>

      {error && <div className="sl-error-toast">{error}</div>}

      {bannerCustomerId != null && (
        <CookieBanner customerId={bannerCustomerId} onClose={() => setBannerCustomerId(null)} />
      )}
    </div>
  )
}
