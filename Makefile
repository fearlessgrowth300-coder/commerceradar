.PHONY: up down logs migrate test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker web

migrate:
	docker compose exec api alembic upgrade head

# Runs against the separate <db>_test database, never dev data.
test:
	docker compose exec api pytest -q
