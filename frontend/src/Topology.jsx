// Topology: visualises the request path LoadGen -> LB -> Instances -> DB/Cache.
import './Topology.css'

function cpuColor(cpu) {
  if (cpu >= 80) return '#ef4444'
  if (cpu >= 50) return '#f59e0b'
  if (cpu >= 20) return '#eab308'
  return '#22c55e'
}

function InstanceNode({ inst }) {
  const cpu = inst.cpu_pct ?? 0
  const unhealthy = inst.health && inst.health !== 'healthy'
  return (
    <div className={`node instance ${unhealthy ? 'unhealthy' : ''}`}>
      <div className="node-title">{inst.worker_id}</div>
      <div className="bar">
        <div
          className="bar-fill"
          style={{ width: `${Math.min(100, cpu)}%`, background: cpuColor(cpu) }}
        />
      </div>
      <div className="node-stats">
        <span>{cpu.toFixed(0)}% cpu</span>
        <span>{(inst.rps ?? 0).toFixed(0)} rps</span>
      </div>
      <div className="node-stats sub">
        <span>{(inst.latency_p95_ms ?? 0).toFixed(1)}ms p95</span>
        <span>{inst.mem_mb ?? 0}MB</span>
      </div>
      {inst.cache_hit_ratio > 0 && (
        <div className="node-stats sub">
          <span>cache {(inst.cache_hit_ratio * 100).toFixed(0)}%</span>
        </div>
      )}
    </div>
  )
}

export default function Topology({ snapshot, cacheEnabled }) {
  const instances = snapshot?.instances || []
  const sys = snapshot?.system || {}
  const db = snapshot?.db || {}
  const load = snapshot?.load || {}

  return (
    <div className="topology">
      <div className="tier">
        <div className="node source">
          <div className="node-title">Load Generator</div>
          <div className="node-stats"><span>{(load.target_rps ?? 0).toFixed(0)} rps offered</span></div>
          <div className="node-stats sub"><span>{load.mode || 'fast'} mode</span></div>
        </div>
      </div>

      <div className="arrow">▼</div>

      <div className="tier">
        <div className="node lb">
          <div className="node-title">Load Balancer</div>
          <div className="node-stats"><span>Traefik</span></div>
          <div className="node-stats sub"><span>{instances.length} targets</span></div>
        </div>
      </div>

      <div className="arrow">▼</div>

      <div className="tier instances-tier">
        {instances.length === 0 && (
          <div className="node empty">No instances — add one →</div>
        )}
        {instances.map((i) => (
          <InstanceNode key={i.worker_id} inst={i} />
        ))}
      </div>

      <div className="arrow">▼</div>

      <div className="tier datastores">
        <div className="node db">
          <div className="node-title">PostgreSQL</div>
          <div className="node-stats"><span>{db.connections ?? '–'} conns</span></div>
          <div className="node-stats sub"><span>{db.active_queries ?? '–'} active</span></div>
        </div>
        <div className={`node cache ${cacheEnabled ? 'on' : 'off'}`}>
          <div className="node-title">Redis Cache</div>
          <div className="node-stats"><span>{cacheEnabled ? 'enabled' : 'disabled'}</span></div>
        </div>
      </div>
    </div>
  )
}
