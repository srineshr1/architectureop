// Load controls: RPS slider, fast/slow mode, traffic-spike, stop.
import { useState } from 'react'
import { api } from './api'

export default function Controls({ snapshot }) {
  const load = snapshot?.load || {}
  const cacheOn = snapshot?.cache?.enabled || false
  const [rps, setRps] = useState(0)
  const [mode, setMode] = useState('fast')
  const [busy, setBusy] = useState(false)

  async function apply(nextRps, nextMode) {
    setBusy(true)
    try {
      await api.setLoad(nextRps ?? rps, nextMode ?? mode)
    } finally {
      setBusy(false)
    }
  }

  function onSlider(e) {
    const v = Number(e.target.value)
    setRps(v)
    api.setLoad(v, mode).catch(() => {})
  }

  function onMode(m) {
    setMode(m)
    apply(rps, m)
  }

  async function onSpike() {
    await api.spike(Math.max(rps * 4, 800), 10, 2)
  }

  async function onStop() {
    setRps(0)
    await api.stopLoad()
  }

  return (
    <div className="panel controls">
      <div className="panel-title">Load Control</div>

      <div className="control-row">
        <label>Target RPS</label>
        <span className="value">{rps}</span>
      </div>
      <input
        type="range"
        min="0"
        max="2000"
        step="10"
        value={rps}
        onChange={onSlider}
        disabled={busy}
      />
      <div className="live-readout">
        offered {(load.target_rps ?? 0).toFixed(0)} · actual{' '}
        {(load.actual_rps ?? 0).toFixed(0)} · errors {load.errors_total ?? 0}
      </div>

      <div className="control-row mode">
        <button className={mode === 'fast' ? 'active' : ''} onClick={() => onMode('fast')}>
          Fast reads
        </button>
        <button className={mode === 'slow' ? 'active' : ''} onClick={() => onMode('slow')}>
          Slow queries
        </button>
      </div>

      <div className="control-row buttons">
        <button className="warn" onClick={onSpike}>⚡ Traffic Spike</button>
        <button className="danger" onClick={onStop}>■ Stop</button>
      </div>

      <div className="control-row">
        <label>Redis cache</label>
        <button
          className={`cache-toggle ${cacheOn ? 'on' : ''}`}
          onClick={() => api.setCache(!cacheOn, !cacheOn ? false : true)}
        >
          {cacheOn ? 'ON — click to disable' : 'OFF — click to enable'}
        </button>
      </div>
    </div>
  )
}
