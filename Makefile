REMOTE       ?= mini.local
REMOTE_DIR   ?= ~/clickmem
PYTHON       ?= python3.13

RSYNC_EXCLUDES = --exclude '.venv' \
	--exclude '__pycache__' \
	--exclude '.hypothesis' \
	--exclude '.pytest_cache' \
	--exclude '*.egg-info' \
	--exclude '.git'

# ── Local targets ─────────────────────────────────────────────────────
.PHONY: test test-fast test-semantic setup

test:                         ## Run full test suite locally
	uv run pytest tests/ -v

test-fast:                    ## Run non-semantic tests locally
	uv run pytest tests/ -m "not semantic" -v

test-semantic:                ## Run semantic distance tests locally
	uv run pytest tests/ -m semantic -v

setup:                        ## Local install (same as ./setup.sh)
	./setup.sh

# ── Remote deploy + test ──────────────────────────────────────────────
.PHONY: deploy-test deploy-test-all deploy

deploy-test:                  ## rsync to REMOTE and run tests (excl. semantic)
	@echo "▸ Syncing to $(REMOTE):$(REMOTE_DIR) ..."
	rsync -az --delete $(RSYNC_EXCLUDES) . $(REMOTE):$(REMOTE_DIR)/
	@echo "▸ Installing deps + running tests on $(REMOTE) ..."
	ssh $(REMOTE) 'eval "$$(/opt/homebrew/bin/brew shellenv)" && \
		cd $(REMOTE_DIR) && \
		uv run --python $(PYTHON) --extra dev pytest tests/ -m "not semantic" -v 2>&1'

deploy-test-all:              ## rsync to REMOTE and run ALL tests (incl. semantic)
	@echo "▸ Syncing to $(REMOTE):$(REMOTE_DIR) ..."
	rsync -az --delete $(RSYNC_EXCLUDES) . $(REMOTE):$(REMOTE_DIR)/
	@echo "▸ Installing deps + running ALL tests on $(REMOTE) ..."
	ssh $(REMOTE) 'eval "$$(/opt/homebrew/bin/brew shellenv)" && \
		cd $(REMOTE_DIR) && \
		uv run --python $(PYTHON) --extra dev pytest tests/ -v 2>&1'

deploy:                       ## rsync to REMOTE and run full setup
	@echo "▸ Syncing to $(REMOTE):$(REMOTE_DIR) ..."
	rsync -az --delete $(RSYNC_EXCLUDES) . $(REMOTE):$(REMOTE_DIR)/
	@echo "▸ Running setup on $(REMOTE) ..."
	ssh $(REMOTE) 'eval "$$(/opt/homebrew/bin/brew shellenv)" && \
		cd $(REMOTE_DIR) && \
		./setup.sh 2>&1'

help:                         ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
