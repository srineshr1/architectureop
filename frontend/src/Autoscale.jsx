// Auto-scaling control: toggle + thresholds + last action readout.
import { api } from './api'

export default function Autoscale({ snapshot }) {
  const a = snapshot?.autoscale || {}
  const on = !!a.enabled

  const toggle = () => api.setAutoscale({ enabled: !on }).catch(() => {})
  const setField = (k) => (e) =>
    api.setAutoscale({ [k]: Number(e.target.value) }).catch(() => {})

  return (
    <div className="panel autoscale">
      <div className="panel-title">Auto-scaling</div>

      <div className="control-row">
        <label>Status</label>
        <button className={`cache-toggle ${on ? 'on-green' : ''}`} onClick={toggle}>
          {on ? 'ENABLED' : 'DISABLED'}
        </button>
      </div>

      <div className="as-grid">
        <label>min<input type="number" min="0" max="8" defaultValue={a.min ?? 1} onBlur={setField('min')} /></label>
        <label>max<input type="number" min="1" max="8" defaultValue={a.max ?? 6} onBlur={setField('max')} /></label>
        <label>CPU high %<input type="number" min="10" max="100" defaultValue={a.cpu_high ?? 50} onBlur={setField('cpu_high')} /></label>
        <label>CPU low %<input type="number" min="0" max="90" defaultValue={a.cpu_low ?? 12} onBlur={setField('cpu_low')} /></label>
        <label>p95 high ms<input type="number" min="10" max="5000" defaultValue={a.p95_high ?? 120} onBlur={setField('p95_high')} /></label>
        <label>p95 low ms<input type="number" min="1" max="2000" defaultValue={a.p95_low ?? 25} onBlur={setField('p95_low')} /></label>
      </div>

      <div className="live-readout">scales on CPU <b>or</b> latency · {a.last_reason || 'idle'}</div>
    </div>
  )
}
