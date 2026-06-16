// Live time-series charts fed by the rolling metrics history.
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const axis = { stroke: '#475569', fontSize: 11 }
const grid = '#1e293b'

function Panel({ title, children }) {
  return (
    <div className="chart-panel">
      <div className="chart-title">{title}</div>
      <ResponsiveContainer width="100%" height={160}>
        {children}
      </ResponsiveContainer>
    </div>
  )
}

export default function Charts({ history }) {
  return (
    <div className="charts">
      <Panel title="Throughput (rps)">
        <LineChart data={history}>
          <CartesianGrid stroke={grid} />
          <XAxis dataKey="t" {...axis} minTickGap={40} />
          <YAxis {...axis} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} />
          <Line type="monotone" dataKey="offered_rps" stroke="#6366f1" dot={false} name="offered" />
          <Line type="monotone" dataKey="served_rps" stroke="#22c55e" dot={false} name="served" />
        </LineChart>
      </Panel>

      <Panel title="Avg CPU (%)">
        <AreaChart data={history}>
          <CartesianGrid stroke={grid} />
          <XAxis dataKey="t" {...axis} minTickGap={40} />
          <YAxis {...axis} domain={[0, 100]} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} />
          <Area type="monotone" dataKey="avg_cpu" stroke="#f59e0b" fill="#f59e0b33" name="cpu%" />
        </AreaChart>
      </Panel>

      <Panel title="Latency p95 (ms)">
        <LineChart data={history}>
          <CartesianGrid stroke={grid} />
          <XAxis dataKey="t" {...axis} minTickGap={40} />
          <YAxis {...axis} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} />
          <Line type="monotone" dataKey="p95" stroke="#ef4444" dot={false} name="p95 ms" />
        </LineChart>
      </Panel>

      <Panel title="Instances & DB connections">
        <LineChart data={history}>
          <CartesianGrid stroke={grid} />
          <XAxis dataKey="t" {...axis} minTickGap={40} />
          <YAxis {...axis} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} />
          <Line type="stepAfter" dataKey="instances" stroke="#0ea5e9" dot={false} name="instances" />
          <Line type="monotone" dataKey="db_conn" stroke="#38bdf8" dot={false} name="db conns" />
        </LineChart>
      </Panel>
    </div>
  )
}
