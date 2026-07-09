.DEFAULT_GOAL := help

PACKAGE_NAME = recorder
UNAME_S := $(shell uname -s)

.PHONY: pre-install
pre-install:
ifeq ($(OS), Windows_NT)
	pip install uv
else ifeq ($(UNAME_S), Darwin)
	brew install uv
else
	curl -LsSf https://astral.sh/uv/install.sh | sh
endif

.PHONY: install
install: pre-install
	uv venv --python 3.14
	uv sync

.PHONY: install-global
install-global:
	uv pip install -e .

.PHONY: update
update:
	uv lock --upgrade
	uv sync

.PHONY: run
run:
	uv run $(PACKAGE_NAME) run $(filter-out $@,$(MAKECMDGOALS))

.PHONY: format
format:
	uv run ruff format

.PHONY: lint
lint:
	uv run pre-commit run --all-files

.PHONY: clean
clean:
	git clean -Xdf

.PHONY: help
help:
	@echo Available targets:
	@echo ""
	@echo "Installation & Dependencies:"
	@echo "   pre-install          Install uv based on the OS"
	@echo "   install              Install dependencies using uv"
	@echo "   install-global       Install $(PACKAGE_NAME) globally (editable mode)"
	@echo "   update               Update dependencies using uv"
	@echo ""
	@echo "Application:"
	@echo "   run                  Run $(PACKAGE_NAME) with the specified arguments"
	@echo ""
	@echo "Development:"
	@echo "   format               Format the project using ruff"
	@echo "   lint                 Run ruff via pre-commit"
	@echo "   clean                Clean the project directory"
	@echo ""
	@echo "   help                 Show this help message"

%:
	@:
