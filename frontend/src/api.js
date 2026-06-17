// REST + WebSocket client for the ReadIssue control plane.
import { useEffect, useRef, useState } from 'react'

async function jpost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${url} -> ${r.status}`)
  return r.json()
}

export const api = {
  listInstances: () => fetch('/api/instances').then((r) => r.json()),
  addInstance: () => jpost('/api/instances'),
  removeInstance: (id) =>
    fetch(`/api/instances/${id}`, { method: 'DELETE' }).then((r) => r.json()),
  setLoad: (target_rps, mode) => jpost('/api/load', { target_rps, mode }),
  stopLoad: () => jpost('/api/load/stop'),
  spike: (peak_rps, duration_s, ramp_s) =>
    jpost('/api/load/spike', { peak_rps, duration_s, ramp_s }),
  setCache: (enabled, flush) => jpost('/api/cache', { enabled, flush }),
  setAutoscale: (cfg) => jpost('/api/autoscale', cfg),
  scenarioSlow: (rps) => jpost('/api/scenario/slow', { rps }),
  scenarioCrash: () => jpost('/api/scenario/crash'),
  scenarioStampede: (peak_rps, duration_s) =>
    jpost('/api/scenario/stampede', { peak_rps, duration_s }),
  setIndex: (enabled) => jpost('/api/optimizations/index', { enabled }),
  setPgbouncer: (enabled) => jpost('/api/optimizations/pgbouncer', { enabled }),
  setReplicas: (enabled) => jpost('/api/optimizations/replicas', { enabled }),
  setRateLimit: (enabled, max_in_flight) =>
    jpost('/api/optimizations/rate_limit', { enabled, max_in_flight }),
}

// Keep a rolling history of snapshots for the time-series charts.
const MAX_HISTORY = 60

export function useMetrics() {
  const [snapshot, setSnapshot] = useState(null)
  const [history, setHistory] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)

  useEffect(() => {
    let stop = false
    let retry

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${window.location.host}/ws/metrics`)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!stop) retry = setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => {
        const snap = JSON.parse(ev.data)
        if (!snap || !snap.ts) return
        setSnapshot(snap)
        setHistory((h) => {
          const sys = snap.system || {}
          const load = snap.load || {}
          const point = {
            t: new Date(snap.ts * 1000).toLocaleTimeString(),
            offered_rps: load.target_rps ?? 0,
            served_rps: sys.total_rps ?? 0,
            avg_cpu: sys.avg_cpu_pct ?? 0,
            p95: sys.max_latency_p95_ms ?? 0,
            instances: sys.instance_count ?? 0,
            db_conn: snap.db?.connections ?? 0,
          }
          return [...h, point].slice(-MAX_HISTORY)
        })
      }
    }
    connect()
    return () => {
      stop = true
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [])

  return { snapshot, history, connected }
}
