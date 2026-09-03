import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { integrationApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Spinner } from '../components/ui'
import { IconShield } from '../components/icons'

export function ContextLandingPage() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [customerName, setCustomerName] = useState('')

  useEffect(() => {
    if (!token) {
      setError('Missing context token')
      return
    }
    integrationApi
      .consumeContext(token)
      .then((r) => {
        setCustomerName(r.data.customer.name)
        setTimeout(() => navigate(`/customers/${r.data.customer.external_id}?context=${encodeURIComponent(token)}`, { replace: true }), 900)
      })
      .catch((e) => setError(getErrorMessage(e)))
  }, [token])

  if (error) {
    return (
      <div style={{ maxWidth: 480, margin: '80px auto' }}>
        <div className="card">
          <div className="card-body" style={{ textAlign: 'center', padding: 40 }}>
            <IconShield size={40} style={{ color: 'var(--danger)', margin: '0 auto 14px', display: 'block' }} />
            <h2>Unable to open consent dashboard</h2>
            <p className="text-secondary mt">{error}</p>
            <div className="mt">
              <Link to="/" className="btn btn-primary">Go to Dashboard</Link>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 480, margin: '80px auto' }}>
      <div className="card">
        <div className="card-body" style={{ textAlign: 'center', padding: 40 }}>
          <Spinner />
          <h2 className="mt">Opening consent dashboard</h2>
          <p className="text-secondary mt">
            Secure context verified for <b>{customerName || 'customer'}</b>.<br />
            Redirecting to the Customer Consent Dashboard…
          </p>
        </div>
      </div>
    </div>
  )
}
