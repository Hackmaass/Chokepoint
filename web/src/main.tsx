import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import Landing from './Landing'

// The landing page needs the theme too, and App only sets it once it mounts: dark unless the visitor chose otherwise.
try {
  const t = localStorage.getItem('theme') || 'dark'
  if (t !== 'system') document.documentElement.setAttribute('data-theme', t)
} catch {
  document.documentElement.setAttribute('data-theme', 'dark')
}

/** Two pages, hash-routed so the static host needs no rewrite rules: #/ is the landing page, #/app the working app. */
function Root() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  useEffect(() => { window.scrollTo(0, 0) }, [hash.startsWith('#/app')])
  return hash.startsWith('#/app') ? <App autoTour={hash === '#/app/tour'} /> : <Landing />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
