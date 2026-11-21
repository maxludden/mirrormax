---
icon: lucide/rocket
---

# Releasing

Releases go to [PyPI](https://pypi.org/project/mirrormax/) automatically when you publish a GitHub Release. The docs site deploys separately, on every push to `main`.

## One-time setup

### GitHub Pages

1. Push the repository to `github.com/maxludden/mirrormax`.
2. Open **Settings → Pages → Build and deployment** and set **Source** to **GitHub Actions**.

The next push to `main` publishes the site at <https://maxludden.github.io/mirrormax/>.

### PyPI trusted publishing

The release workflow uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored in GitHub.

1. Sign in to PyPI and open **Your account → Publishing → Add a new pending publisher**.
2. Fill in:

    | Field | Value |
    |---|---|
    | PyPI project name | `mirrormax` |
    | Owner | `maxludden` |
    | Repository name | `mirrormax` |
    | Workflow name | `release.yml` |
    | Environment name | `pypi` |

3. In GitHub, open **Settings → Environments**, create an environment called `pypi`, and (recommended) add yourself as a required reviewer so every upload waits for your approval.

To rehearse on TestPyPI, repeat both steps on [test.pypi.org](https://test.pypi.org/) with the environment name `testpypi`.

## Cutting a release

1. **Bump the version** in both places (a test fails if they disagree):

    ```sh
    uv version --bump patch          # or minor / major; updates pyproject.toml and uv.lock
    ```

    then set `__version__` in `src/mirrormax/__init__.py` to match.

2. **Update the [changelog](../changelog.md)**: move the *Unreleased* entries under the new version and date.

3. **Check everything locally:**

    ```sh
    uv run pytest && uv run mypy && uv run ruff check && uv run ruff format --check
    uv run python scripts/gen_cli_reference.py --check
    uv build
    ```

4. **Commit, tag and push:**

    ```sh
    git commit -am "Release v0.2.0"
    git tag v0.2.0
    git push && git push --tags
    ```

5. **Publish a GitHub Release** for the tag:

    ```sh
    gh release create v0.2.0 --generate-notes
    ```

The **Release** workflow then:

1. runs the full CI suite on macOS,
2. checks that the tag (without the `v`), `pyproject.toml` and `mirrormax.__version__` all match,
3. builds the sdist and wheel with `uv build` and smoke-tests the wheel with `mirrormax --version`,
4. waits for approval on the `pypi` environment (if you added a reviewer), then uploads to PyPI.

## Rehearsing on TestPyPI

Run the workflow by hand from the **Actions** tab (**Release → Run workflow**) and choose `testpypi`. It runs CI, builds and uploads to TestPyPI without needing a release. Note that TestPyPI, like PyPI, refuses to upload the same version twice.
