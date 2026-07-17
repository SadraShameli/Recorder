.DEFAULT_GOAL := help

PACKAGE_NAME = recorder
SERVICE_NAME = recorder.service
SERVICE_NAME_TELEGRAM = recorder-telegram.service
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
ifeq ($(UNAME_S), Linux)
	uv venv --python 3.13 --system-site-packages
else
	uv venv --python 3.13
endif
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

.PHONY: telegram
telegram:
	uv run $(PACKAGE_NAME) telegram $(filter-out $@,$(MAKECMDGOALS))

.PHONY: pull
pull:
	git pull
	uv sync

.PHONY: deploy
deploy: pull restart-all

.PHONY: status
status:
	systemctl status $(SERVICE_NAME) $(SERVICE_NAME_TELEGRAM) --no-pager

.PHONY: logs
logs:
	journalctl -u $(SERVICE_NAME) --no-pager -n 50

.PHONY: logs-telegram
logs-telegram:
	journalctl -u $(SERVICE_NAME_TELEGRAM) --no-pager -n 50

.PHONY: restart
restart:
	sudo systemctl restart $(SERVICE_NAME)

.PHONY: restart-telegram
restart-telegram:
	sudo systemctl restart $(SERVICE_NAME_TELEGRAM)

.PHONY: restart-all
restart-all: restart restart-telegram

.PHONY: install-service
install-service:
	sudo .venv/bin/$(PACKAGE_NAME) install-service

.PHONY: uninstall-service
uninstall-service:
	sudo .venv/bin/$(PACKAGE_NAME) uninstall-service

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
	@echo "   telegram             Run the Telegram bot with the specified arguments"
	@echo ""
	@echo "Deployment (Raspberry Pi):"
	@echo "   pull                 Pull latest code and sync dependencies"
	@echo "   deploy               Pull latest code and restart both services"
	@echo "   status               Show status of both systemd services"
	@echo "   logs                 Show recent recorder.service logs"
	@echo "   logs-telegram        Show recent recorder-telegram.service logs"
	@echo "   restart              Restart recorder.service"
	@echo "   restart-telegram     Restart recorder-telegram.service"
	@echo "   restart-all          Restart both services"
	@echo "   install-service      Install both services via systemd"
	@echo "   uninstall-service    Remove both services from systemd"
	@echo ""
	@echo "Development:"
	@echo "   format               Format the project using ruff"
	@echo "   lint                 Run ruff via pre-commit"
	@echo "   clean                Clean the project directory"
	@echo ""
	@echo "   help                 Show this help message"

%:
	@:
