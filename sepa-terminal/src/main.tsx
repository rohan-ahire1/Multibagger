import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/globals.css'

function App() {
  return (
    <div className="min-h-screen bg-bg-primary text-text-primary flex items-center justify-center">
      <h1 className="text-xl font-medium">SEPA Terminal v2</h1>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
