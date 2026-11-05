.PHONY: run test lint docker-run docker-test

run:
	uv run --extra llm uvicorn trade_reconciliation.api:app --host 0.0.0.0 --port 8000 --workers 1

test:
	uv run --extra dev pytest -q

lint:
	uv run --extra dev ruff check src tests

docker-run:
	docker compose up --build

docker-test:
	docker compose run --build --rm reconciliation pytest -v
