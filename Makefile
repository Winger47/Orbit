.PHONY: lint format typecheck test check dev

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

dev:
	uv run uvicorn orbit.main:app --reload
