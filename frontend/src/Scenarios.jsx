// Scenario injectors: slow queries, instance crash, cache stampede.
import { useState } from 'react'
import { api } from './api'

export default function Scenarios() {
  const [msg, setMsg] = useState(null)

  async function run(label, fn) {
    setMsg(`running: ${label}…`)
    try {
      const r = await fn()
      setMsg(`${label}: ${JSON.stringify(r)}`)
    } catch (e) {
      setMsg(`${label} failed: ${e.message || e}`)
    }
  }

  return (
    <div className="panel scenarios">
      <div className="panel-title">Scenarios</div>
      <div className="scenario-buttons">
        <button onClick={() => run('Slow queries', () => api.scenarioSlow(80))}>
          🐌 Slow Queries
          <span>expensive reads hammer the DB</span>
        </button>
        <button className="danger" onClick={() => run('Crash instance', api.scenarioCrash)}>
          💥 Crash Instance
          <span>hard-kill a worker; watch failover</span>
        </button>
        <button className="warn" onClick={() => run('Cache stampede', () => api.scenarioStampede(800, 10))}>
          🌊 Cache Stampede
          <span>flush cache + burst → DB surge</span>
        </button>
      </div>
      {msg && <div className="scenario-msg">{msg}</div>}
    </div>
  )
}
