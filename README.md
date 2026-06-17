# ReadIssue — AWS-style Overload Simulation Lab

A local, browser-based sandbox that runs **real** infrastructure so you can
deliberately create overload situations — saturated database, pegged CPU,
drowning instances — watch how the system reacts in real time, then apply real
fixes (add instances, add a load balancer, add Redis caching, auto-scale) and
verify recovery.

Everything is real: a real PostgreSQL database, a real Redis cache, real worker
instances as Docker containers spun up/down on demand, and a real Traefik load
balancer that auto-discovers them. A FastAPI control plane orchestrates it all
and streams live metrics to a React dashboard.

> This is a learning/experimentation tool meant to run **on your own machine**.
> See [Safety & limitations](#safety--limitations).

## Architecture

```
                  ┌──────────────────────────┐
   Browser  ─────▶│  React Dashboard (nginx)  │  http://localhost:3000
                  └─────────────┬────────────┘
                       /api, /ws │ (proxied)
                  ┌─────────────▼────────────┐
                  │   Control Plane (FastAPI) │  orchestration, metrics,
                  │   + load gen + autoscaler │  load gen, scenarios
                  └───┬──────────┬────────────┘
        Docker socket │          │ HTTP load
                      │          ▼
                      │   ┌──────────────┐     ┌──────────────┐
                      │   │ Traefik (LB) │────▶│  Worker 1..N │  (dynamic
                      │   └──────────────┘     │  read API)   │   containers)
   spawn/kill ◀───────┘                        └───┬──────┬───┘
   worker containers                               │      │
                                          ┌─────────▼─┐  ┌─▼────────┐
                                          │ PostgreSQL│  │  Redis   │
                                          └───────────┘  └──────────┘
```

- **Worker** (`worker/`): FastAPI read API over the `products` table. Two read
  modes — `fast` (indexed) and `slow` (deliberately expensive full scan). Tracks
  per-instance metrics; optional Redis cache-aside.
- **Control plane** (`control-plane/`): Docker-SDK orchestration, a metrics
  collector (Docker stats + worker metrics + Postgres activity) streamed over a
  WebSocket, a closed-loop load generator, a threshold auto-scaler, and scenario
  injectors.
- **Traefik**: auto-discovers worker containers by label and load-balances
  across them with zero config reloads.
- **Dashboard** (`frontend/`): topology view, live charts, RPS slider, scaling
  controls, cache + auto-scale toggles, and one-click scenarios.

## Quickstart (one command)

Requires Docker + Docker Compose and `make`.

```bash
make up
```

This builds the worker image, builds and starts the stack, seeds the database,
then brings up the control plane and dashboard.

Open the dashboard at **http://localhost:3000**.

Other endpoints:
- Control plane API: http://localhost:8000/api/health
- Traefik dashboard: http://localhost:8081

Tear everything down (removes dynamically-spawned workers and volumes):

```bash
make clean
```

`make help` lists all targets.

### Configuration

Edit `.env` to change the dataset size (`SEED_ROWS`), the worker cap
(`MAX_WORKERS`), credentials, and ports.

## Guided walkthroughs

Each of these takes ~1 minute in the dashboard.

### 1. Overload the database, then cache your way out
1. Start with 1–2 instances (use the **Instances** panel).
2. Click **🐌 Slow Queries** under **Scenarios** (or set the RPS slider to ~80
   and switch to *Slow queries* mode). Watch **p95 latency** jump and DB active
   queries climb.
3. Flip **Redis cache → ON** in *Load Control*. Watch latency collapse, the
   cache-hit ratio climb, and DB connections drop. You just fixed it.

### 2. Scale out under a traffic spike
1. Set a steady load (e.g. 200 RPS, fast mode).
2. Click **⚡ Traffic Spike**. Watch per-instance CPU rise.
3. Either click **+ Add instance** a few times, or enable **Auto-scaling** and
   watch the system add instances until latency recovers, then scale back down.

### 3. Survive an instance crash
1. Run 3 instances under load.
2. Click **💥 Crash Instance**. One worker is hard-killed; Traefik fails over to
   the survivors with no dropped traffic. Add it back (or let auto-scaling do it).

### 4. Trigger a cache stampede
1. Enable the cache and let it warm up.
2. Click **🌊 Cache Stampede**. The cache is flushed and a burst of traffic hits
   the cold cache at once — watch the brief surge of concurrent DB queries.

## Optimizations panel

Beyond the Redis cache and auto-scaling, the dashboard's **Optimizations** panel
toggles four real read-path optimizations live, each with a visible before/after:

- **DB index on `stock`** — the slow query does a full sequential scan over
  400k rows (~38ms p95 under load); enabling the index flips the plan to an
  index scan and p95 drops to ~3ms.
- **PgBouncer pooling** — routes workers through a transaction-pooling proxy.
  4 workers go from ~42 Postgres backends to ~10 while serving the same load.
- **Read replicas** — an independent read-only Postgres copy (the data is
  static after seeding). Flipping it on moves read load off the primary: in a
  slow-query test the primary's container CPU dropped from ~550% to ~1% while
  the replica took it on. Shows that read scale-out helps a DB-bound workload
  where adding app instances doesn't.
- **Load shedding** — worker admission control. Under overload it caps
  concurrent in-flight requests and returns `429` for the excess, keeping
  accepted-request latency bounded (e.g. ~18ms instead of hundreds of ms) while
  the shed counter climbs.

Together with the cache and autoscaler, these cover the full read path:
index → pooler → replicas → cache → autoscale → load-shed.

## Host development (live reload)

Run Postgres/Redis/Traefik in Docker but the control plane + dashboard on the
host for fast iteration:

```bash
# infra only
docker compose up -d postgres redis traefik
docker compose run --rm seeder
docker build -t readissue-worker:latest ./worker   # workers are spawned from this image

# control plane (host) — note the host-specific hosts/ports
python -m venv .venv && . .venv/bin/activate
pip install -r control-plane/requirements.txt
export COLLECTOR_PG_HOST=localhost INGRESS_URL=http://localhost:8080
set -a && . ./.env && set +a
uvicorn app.main:app --app-dir control-plane --host 127.0.0.1 --port 8000

# dashboard (host) — proxies /api and /ws to :8000
cd frontend && npm install && npm run dev   # http://localhost:5173
```

When the control plane runs on the host it reaches Postgres via `localhost`
(`COLLECTOR_PG_HOST=localhost`) and Traefik via `localhost:8080`
(`INGRESS_URL`). Inside the container it uses the `postgres`/`traefik` service
names. The host/port passed to *workers* is always the in-network service name,
so workers stay healthy either way.

## Tests

```bash
make test     # or: . .venv/bin/activate && python -m pytest tests/ -q
```

Unit tests cover the data generator, worker metrics + cache, the metrics
collector math, the load-generator pacing, the autoscaler decision logic, and
the orchestrator (mocked Docker). One Traefik integration test runs only when
the live stack is up.

## Safety & limitations

- **Localhost only.** The control plane has no authentication and mounts the
  Docker socket (effectively host-level privilege). Never expose it to a
  network.
- **Worker cap.** `MAX_WORKERS` (default 8) bounds how many containers can be
  spawned so the lab can't exhaust your machine.
- **Load generator ceiling.** The generator is a single Python process; it
  reliably sustains a few hundred RPS. Worker-side metrics (CPU, p95) are the
  source of truth for backend behaviour. To genuinely overload the backend, use
  *slow* mode and/or fewer instances rather than only cranking RPS.
- **Data is dummy.** The seeder generates synthetic product rows; nothing here
  is production data.

## Layout

```
control-plane/   FastAPI orchestrator, collector, loadgen, autoscaler, scenarios
worker/          containerized read API (fast/slow reads, cache-aside, metrics)
seeder/          idempotent dummy-data seeder
frontend/        React dashboard (Vite + Recharts), nginx for production
db/init/         Postgres schema
tests/           pytest suite
docker-compose.yml, Makefile, .env
```
# architectureop
