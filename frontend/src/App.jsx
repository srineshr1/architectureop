import { useMetrics } from './api'
import Topology from './Topology'
import Charts from './Charts'
import Controls from './Controls'
import Scaling from './Scaling'
import Autoscale from './Autoscale'
import Scenarios from './Scenarios'
import './App.css'

function Stat({ label, value, unit, warn }) {
  return (
    <div className={`stat ${warn ? 'warn' : ''}`}>
      <div className="stat-value">{value}<span className="unit">{unit}</span></div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

export default function App() {
  const { snapshot, history, connected } = useMetrics()
  const sys = snapshot?.system || {}
  const load = snapshot?.load || {}

  const p95 = sys.max_latency_p95_ms ?? 0
  const cpu = sys.avg_cpu_pct ?? 0

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> ReadIssue
          <span className="subtitle">overload simulation lab</span>
        </div>
        <div className={`conn ${connected ? 'up' : 'down'}`}>
          {connected ? '● live' : '○ reconnecting'}
        </div>
      </header>

      <section className="stat-bar">
        <Stat label="Instances" value={sys.instance_count ?? 0} />
        <Stat label="Served RPS" value={(sys.total_rps ?? 0).toFixed(0)} />
        <Stat label="Offered RPS" value={(load.target_rps ?? 0).toFixed(0)} />
        <Stat label="Avg CPU" value={cpu.toFixed(0)} unit="%" warn={cpu >= 70} />
        <Stat label="p95 latency" value={p95.toFixed(1)} unit="ms" warn={p95 >= 100} />
        <Stat label="DB conns" value={snapshot?.db?.connections ?? '–'} />
        <Stat label="Errors" value={sys.total_errors ?? 0} warn={(sys.total_errors ?? 0) > 0} />
      </section>

      <main className="layout">
        <div className="left">
          <Controls snapshot={snapshot} />
          <Scaling snapshot={snapshot} />
          <Autoscale snapshot={snapshot} />
        </div>
        <div className="center panel">
          <div className="panel-title">Topology</div>
          <Topology snapshot={snapshot} cacheEnabled={snapshot?.cache?.enabled || false} />
          <Scenarios />
        </div>
      </main>

      <section className="panel charts-panel">
        <div className="panel-title">Live Metrics</div>
        <Charts history={history} />
      </section>
    </div>
  )
}
