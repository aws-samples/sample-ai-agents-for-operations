# DART Agent Makefile
# Dataset Audit & Readiness for Training

.DEFAULT_GOAL := help
.PHONY: help setup test test-unit test-integration test-all lint typecheck security-scan \
        build docker-build docker-run deploy deploy-staging deploy-prod clean

# ── Configuration ──────────────────────────────────────────────────────────────
PYTHON      := python3
VENV        := .venv
PIP         := $(VENV)/bin/pip
PYTEST      := $(VENV)/bin/pytest
RUFF        := $(VENV)/bin/ruff
MYPY        := $(VENV)/bin/mypy
PIP_AUDIT   := $(VENV)/bin/pip-audit
SRC_DIR     := src/dart-agent
TEST_DIR    := tests
IMAGE_NAME  := dart-agent
ENV         ?= dev

## help: Show this help message
help:
	@echo "DART Agent — Dataset Audit & Readiness for Training"
	@echo ""
	@grep -E '^##' Makefile | sed 's/## /  /'

## setup: Create virtual environment and install dependencies
setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip==24.3.1
	$(PIP) install -r requirements.txt -r requirements-dev.txt
	@echo "✅ Virtual environment ready. Activate with: source $(VENV)/bin/activate"

## test-unit: Run unit tests with coverage
test-unit:
	$(PYTEST) $(TEST_DIR)/unit/ -v \
		--cov=$(SRC_DIR) \
		--cov-report=term-missing \
		--cov-fail-under=80 \
		-p no:warnings

## test-integration: Run integration tests
test-integration:
	$(PYTEST) $(TEST_DIR)/integration/ -v -p no:warnings

## test-all: Run all tests with full coverage report
test-all:
	$(PYTEST) $(TEST_DIR)/ -v \
		--cov=$(SRC_DIR) \
		--cov-report=term-missing \
		--cov-report=html:coverage_html \
		--cov-fail-under=80

## lint: Run ruff linter
lint:
	$(RUFF) check $(SRC_DIR)/ $(TEST_DIR)/
	@echo "✅ Lint passed"

## lint-fix: Auto-fix lint issues
lint-fix:
	$(RUFF) check --fix $(SRC_DIR)/ $(TEST_DIR)/

## typecheck: Run mypy type checking
typecheck:
	$(MYPY) $(SRC_DIR)/ --ignore-missing-imports

## security-scan: Run pip-audit for dependency vulnerabilities
security-scan:
	$(PIP_AUDIT) -r requirements.txt
	@echo "✅ Security scan passed"

## docker-build: Build Docker image locally
docker-build:
	docker build -t $(IMAGE_NAME):$(ENV) \
		--build-arg ENVIRONMENT=$(ENV) \
		-f Dockerfile .
	@echo "✅ Docker image built: $(IMAGE_NAME):$(ENV)"

## docker-run: Run agent locally in Docker (interactive)
docker-run:
	docker run --rm -it \
		-e AWS_REGION=$(AWS_REGION) \
		-e OUTPUT_BUCKET=$(OUTPUT_BUCKET) \
		-e AUDIT_TABLE=$(AUDIT_TABLE) \
		-e AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) \
		-e AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
		-e AWS_SESSION_TOKEN=$(AWS_SESSION_TOKEN) \
		$(IMAGE_NAME):$(ENV) "$(MESSAGE)"

## deploy-staging: Deploy to staging environment
deploy-staging:
	cd infra && \
	npm install && \
	npx cdk deploy DartAgent-staging \
		--context env=staging \
		--require-approval never

## deploy-prod: Deploy to production environment (requires manual approval)
deploy-prod:
	cd infra && \
	npm install && \
	npx cdk deploy DartAgent-prod \
		--context env=prod \
		--require-approval broadening

## clean: Remove build artifacts and virtual environment
clean:
	rm -rf $(VENV)
	rm -rf coverage_html .coverage
	rm -rf **/__pycache__ **/*.pyc
	rm -rf .ruff_cache .mypy_cache
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Cleaned"

## run: Run agent locally (CLI mode)
run:
	cd $(SRC_DIR) && $(PYTHON) agent.py "$(MESSAGE)"
