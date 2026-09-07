import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'
import { careerhubConsentAdapter } from './consentAdapter'
import { installGlobalConsentApi, initConsentGate, startTagScanMonitor } from './consentGate'

// R2-10: gate non-essential tags and expose consent state before React even
// mounts, so a tag placed anywhere in index.html is covered from first paint.
installGlobalConsentApi(careerhubConsentAdapter, ['necessary', 'functional', 'analytics', 'advertising'])
initConsentGate(careerhubConsentAdapter)
// K-48: keep checking for tags/pixels/iframes/beacons that bypassed the gate
// for as long as the page is open, not just once at load.
startTagScanMonitor(careerhubConsentAdapter)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
