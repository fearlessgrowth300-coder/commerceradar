.PHONY: up down logs migrate deploy test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker web

migrate:
	docker compose exec api alembic upgrade head

# Runs against the separate <db>_test database, never dev data.
# Update a running server: pull, migrate, restart app processes. The web dev server's file
# watcher can miss routes after a branch switch, so web is restarted too.
deploy:
	git pull --ff-only
	docker compose exec -T api alembic upgrade head
	docker compose restart api worker beat web

test:
	docker compose exec api pytest -q
