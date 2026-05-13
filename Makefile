.PHONY: help test test-fast dashboard deploy clean install

PYTHON ?= python3
DASHBOARD_DIR := src/clickmem/dashboard

help:
	@echo "ClickMem dev targets:"
	@echo "  make install       - pip install -e .[dev]"
	@echo "  make test          - run full test suite (pytest -x)"
	@echo "  make test-fast     - run tests excluding slow/integration"
	@echo "  make dashboard     - build the React dashboard into dist/"
	@echo "  make deploy        - rsync to mini.local (LAN dev box)"
	@echo "  make clean         - remove build artefacts and caches"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -x

test-fast:
	$(PYTHON) -m pytest -x -m "not slow and not integration"

dashboard:
	@if [ ! -d "$(DASHBOARD_DIR)" ]; then \
		echo "Dashboard sources not present yet (Phase 7)."; \
		exit 0; \
	fi
	cd $(DASHBOARD_DIR) && pnpm install && pnpm build

deploy:
	rsync -avz --delete \
	    --exclude '.git' --exclude '.venv' --exclude 'dist' \
	    --exclude '__pycache__' --exclude '.pytest_cache' \
	    ./ mini.local:~/clickmem/

clean:
	rm -rf build dist *.egg-info .pytest_cache .hypothesis
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
