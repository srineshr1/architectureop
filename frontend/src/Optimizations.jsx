// Read-path optimization toggles (index, pooling, replicas, load shedding).
import { api } from './api'

const ITEMS = [
  { key: 'index', label: 'DB index on stock', desc: 'fixes the slow seq-scan query', fn: api.setIndex },
  { key: 'pgbouncer', label: 'PgBouncer pooling', desc: 'bounds Postgres connections', fn: api.setPgbouncer },
  { key: 'replicas', label: 'Read replicas', desc: 'spread reads across DB copies', fn: api.setReplicas },
  { key: 'rate_limit', label: 'Load shedding', desc: 'shed excess load (429) under overload', fn: api.setRateLimit },
]

export default function Optimizations({ snapshot }) {
  const opt = snapshot?.optimizations || {}

  return (
    <div className="panel optimizations">
      <div className="panel-title">Optimizations</div>
      <ul className="opt-list">
        {ITEMS.map((it) => {
          const on = !!opt[it.key]
          return (
            <li key={it.key}>
              <div className="opt-text">
                <span className="opt-label">{it.label}</span>
                <span className="opt-desc">{it.desc}</span>
              </div>
              <button
                className={`opt-toggle ${on ? 'on' : ''}`}
                onClick={() => it.fn(!on).catch(() => {})}
              >
                {on ? 'ON' : 'OFF'}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
