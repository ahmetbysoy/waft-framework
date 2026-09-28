# WAFT — common tasks.  Usage: make <target>
.DEFAULT_GOAL := help
PY ?= python3

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Install python deps + Chromium (with system libs)
	$(PY) -m pip install -r requirements.txt
	$(PY) -m playwright install --with-deps chromium

install-dev:  ## Install dev deps too
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m playwright install --with-deps chromium

sample-data:  ## Generate examples/data/* (Excel, JSON, proxies, .env.example)
	$(PY) examples/make_sample_data.py

demo-site:  ## Run the bundled demo website on :8080
	$(PY) examples/demo_site.py --port 8080 --quiet

demo:  ## 10 isolated contexts against the demo site (proxy-less, Excel data)
	$(PY) -m waft --data examples/data/targets.xlsx --contexts 10 --concurrency 10 --proxy-mode off

demo-json:  ## JSON data source demo (wizard steps, load test, API discovery, CAPTCHA)
	$(PY) -m waft --data examples/data/targets.json --contexts 3 --concurrency 3 --proxy-mode off

demo-headful:  ## Same as demo but with visible windows
	$(PY) -m waft --data examples/data/targets.xlsx --contexts 2 --concurrency 2 --proxy-mode off --headful --slow-mo 120

dry-run:  ## Show the execution plan without launching a browser
	$(PY) -m waft --data examples/data/targets.xlsx --contexts 10 --proxy-mode off --dry-run

lint:  ## Ruff lint
	ruff check waft/ examples/ tests/

test:  ## Unit tests (fast, no browser)
	$(PY) -m pytest tests/ -q -m "not integration"

test-all:  ## Unit + integration tests (needs Chromium)
	$(PY) -m pytest tests/ -q

clean:  ## Remove artifacts and caches
	rm -rf artifacts .pytest_cache .ruff_cache **/__pycache__

.PHONY: help install install-dev sample-data demo-site demo demo-json demo-headful dry-run lint test test-all clean
