"""Allow `python -m clickmem` to behave like the `clickmem` console script."""

from clickmem.cli import app


if __name__ == "__main__":
    app()
