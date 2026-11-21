---
icon: lucide/code
---

# Contributing

## Set up

```sh
git clone https://github.com/maxludden/mirrormax.git
cd mirrormax
uv sync --all-groups
```

## Checks

```sh
uv run pytest                 # unit tests; no real displays or launchd needed
uv run pytest -m macos        # also exercise the real kqueue watcher (macOS only)
uv run mypy                   # strict, over src and tests
uv run ruff check
uv run ruff format --check
```

If you change the UI on purpose, accept the new snapshots:

```sh
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_ui_cli.py
```

## Documentation

The site is built with [Zensical](https://zensical.org/) from the Markdown in `docs/`, configured by `zensical.toml`.

```sh
uv run --group docs zensical serve          # live preview at http://localhost:8000
uv run --group docs zensical build --clean  # build into site/
```

### The CLI reference is generated

`docs/reference/cli.md` is generated from the Typer app, so it always matches `mirrormax --help`. After changing a command, an option or a help string, regenerate it:

```sh
uv run python scripts/gen_cli_reference.py
```

A test (`tests/test_docs.py`) and the docs workflow both fail if the committed page is out of date. To check without writing:

```sh
uv run python scripts/gen_cli_reference.py --check
```

### Publishing

The **Documentation** workflow (`.github/workflows/docs.yml`) builds the site on every push to `main` and deploys it to GitHub Pages. Pull requests build the site too, without deploying, so broken docs are caught before merge.

## Continuous integration

The **CI** workflow (`.github/workflows/ci.yml`) runs ruff, mypy and pytest on macOS for every push and pull request, on Python 3.12 and 3.13.
