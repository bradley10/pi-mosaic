.PHONY: develop format run sim test lint clean deploy

develop:
	uv sync --extra dev --extra test

format:
	uv run ruff format .

lint:
	uv run ruff check
	uv run ruff format --check .
	uv run pyright

test:
	uv run pytest

run:
	uv run python -m controller

sim:
	uv run python -m controller simulate

clean:
	rm -rf .venv *.egg-info .pytest_cache .coverage .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

deploy:
	./deploy/deploy.sh