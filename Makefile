.PHONY: all build

all: dev

clean:
	rm -rf ./build

setup:
	@curl -LsSf https://astral.sh/uv/install.sh | sh
	@uv venv
	@uv lock

# use the --clear-cache command as of Flet 0.25.0 to clear the build/flutter project.
# to speed up rebuilds then as of Flet 0.25.0 the build/flutter project is not re-created on every build.
build-macos: version
	@uv run flet build \
		--compile-app \
		--compile-packages \
		macos

dev: version
	@export FLET_DEBUG=1; export WALLET_ENVIRONMENT=development; uv run flet run main.py

fmt:
	@uv tool run ruff check --select I --fix
	@uv tool run ruff format

# used by ci
check:
	uv tool run ruff check --select I
	uv tool run ruff format --check

sign: build-macos
	./sign.sh

version:
	@uv run python generate_version.py
