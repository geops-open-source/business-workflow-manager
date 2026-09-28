all: format typecheck test

# Format code
format:
    uv run ruff check --fix
    uv run ruff format

# Run tests
test:
    uv run pytest

# Run typechecker
typecheck:
    uv run pyright

# Build documentation
build-docs:
    uv run make -C docs html

# Run pytest on code changes (accepts additional commandline flags for pytest)
[positional-arguments]
watch *args:
    watchexec \
        --watch=src \
        --watch=tests \
        --exts=py \
        --clear \
        --shell=none \
        --wrap-process=none \
        -- uv run pytest --exitfirst --failed-first --new-first "$@"

