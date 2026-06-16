// Manual scaling: add/remove instances and kill specific ones.
import { useState } from 'react'
import { api } from './api'

export default function Scaling({ snapshot }) {
  const instances = snapshot?.instances || []
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function withBusy(fn) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const add = () => withBusy(() => api.addInstance())
  const removeOne = () => {
    const last = instances[instances.length - 1]
    if (last) return withBusy(() => api.removeInstance(last.worker_id))
  }
  const kill = (id) => withBusy(() => api.removeInstance(id))

  return (
    <div className="panel scaling">
      <div className="panel-title">Instances ({instances.length})</div>

      <div className="control-row buttons">
        <button className="ok" onClick={add} disabled={busy}>+ Add instance</button>
        <button onClick={removeOne} disabled={busy || instances.length === 0}>
          − Remove
        </button>
      </div>

      {error && <div className="scale-error">{error}</div>}

      <ul className="instance-list">
        {instances.length === 0 && <li className="muted">No instances running</li>}
        {instances.map((i) => {
          const unhealthy = i.health && i.health !== 'healthy'
          return (
            <li key={i.worker_id} className={unhealthy ? 'unhealthy' : ''}>
              <span className="dot" />
              <span className="iid">{i.worker_id}</span>
              <span className="icpu">{(i.cpu_pct ?? 0).toFixed(0)}%</span>
              <button className="kill" onClick={() => kill(i.worker_id)} disabled={busy}>
                ✕
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
