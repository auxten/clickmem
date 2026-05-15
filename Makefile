.PHONY: help test test-fast dashboard deploy clean install audit-install audit-install-tier1 audit-install-tier2 audit-install-clean

PYTHON ?= python3
DASHBOARD_DIR := src/clickmem/dashboard

help:
	@echo "ClickMem dev targets:"
	@echo "  make install                 - pip install -e .[dev]"
	@echo "  make test                    - run full test suite (pytest -x)"
	@echo "  make test-fast               - run tests excluding slow/integration"
	@echo "  make dashboard               - build the React dashboard into dist/"
	@echo "  make deploy                  - rsync to mini.local (LAN dev box)"
	@echo "  make clean                   - remove build artefacts and caches"
	@echo "  make audit-install           - run the one-shot install-experience audit"
	@echo "  make audit-install-tier1     - audit Tier 1 (install blockers) only"
	@echo "  make audit-install-tier2     - audit Tier 2 (UX defects) only"
	@echo "  make audit-install-clean     - delete /tmp sandboxes + audit-results/"

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

audit-install:
	$(PYTHON) scripts/audit_install.py all

audit-install-tier1:
	$(PYTHON) scripts/audit_install.py tier1

audit-install-tier2:
	$(PYTHON) scripts/audit_install.py tier2

audit-install-clean:
	rm -rf /tmp/clickmem-audit-* audit-results/
