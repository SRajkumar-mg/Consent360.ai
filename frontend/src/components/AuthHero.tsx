import hero from '../assets/figma/login-hero.jpg'
import mark from '../assets/figma/consent-mark.png'
export function AuthHero() {
  return (
    <div className="auth-hero" style={{ backgroundImage: `url(${hero})` }}>
      <div className="auth-hero-overlay" />
      <div className="auth-hero-brand">
        <img src={mark} alt="" className="auth-hero-mark" />
        <div>
          <div className="auth-hero-title">Consent Management Platform</div>
          <div className="auth-hero-tag">Enterprise Consent &amp; Privacy Management</div>
        </div>
      </div>
      <div className="auth-hero-narrative">
        <h2 className="auth-hero-headline">Centralize consent.<br />Prove compliance.</h2>
        <p className="auth-hero-copy">Manage the full consent lifecycle for every customer — grant, deny, withdraw, renew, evaluate processing decisions and maintain a tamper-evident audit trail.</p>
        <div className="auth-hero-line" />
        <div className="auth-hero-tags"><span>✓ Consent Lifecycle</span><span>✓ Decision Engine</span><span>✓ Immutable Audit</span></div>
      </div>
    </div>
  )
}
