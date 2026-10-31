PYTHON ?= python3.14
.PHONY: test compile build smoke

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

compile:
	$(PYTHON) -m compileall -q src scripts tests

build:
	$(PYTHON) scripts/build.py

smoke: build
	$(PYTHON) dist/local-agentic-triage.pyz --help
