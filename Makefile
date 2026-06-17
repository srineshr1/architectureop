# ReadIssue overload-simulation lab — common operations.
# The worker image is built separately because the control plane spawns worker
# containers dynamically (they are not long-running compose services).

.PHONY: build up down clean seed test logs ps help

help:
	@echo "ReadIssue lab targets:"
	@echo "  make build   - build worker image + all compose images"
	@echo "  make up      - build, start the stack, and seed the database"
	@echo "  make down    - stop the stack (keeps the DB volume)"
	@echo "  make clean   - stop the stack, remove managed worker containers + volumes"
	@echo "  make seed    - (re)run the database seeder"
	@echo "  make test    - run the Python test suite"
	@echo "  make logs    - tail control-plane logs"
	@echo "  make ps      - show stack + managed worker containers"
	@echo ""
	@echo "Dashboard:        http://localhost:3000"
	@echo "Control plane API: http://localhost:8000/api/health"
	@echo "Traefik dashboard: http://localhost:8081"

build:
	docker build -t readissue-worker:latest ./worker
	docker compose build

up: build
	docker compose up -d postgres replica redis traefik pgbouncer
	docker compose run --rm seeder
	docker compose run --rm -e POSTGRES_HOST=replica seeder
	docker compose up -d control-plane frontend
	@echo ""
	@echo "ReadIssue is up → open http://localhost:3000"

seed:
	docker compose run --rm seeder
	docker compose run --rm -e POSTGRES_HOST=replica seeder

down:
	docker compose down

# Full teardown: stop stack, remove dynamically-spawned workers, drop volumes.
clean:
	-docker rm -f $$(docker ps -aq --filter label=readissue.managed=true) 2>/dev/null || true
	docker compose down -v
	@echo "Lab torn down (managed workers removed, volumes dropped)."

test:
	. .venv/bin/activate && python -m pytest tests/ -q

logs:
	docker compose logs -f control-plane

ps:
	@docker compose ps
	@echo "--- managed workers ---"
	@docker ps --filter label=readissue.managed=true --format '{{.Names}}\t{{.Status}}'
