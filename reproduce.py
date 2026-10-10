"""Measure all four source states and print the tables from the README.

Usage:
    python reproduce.py <path to a deepl-python checkout> [sizes in MB]

The checkout has to be a git working tree with nothing uncommitted under
deepl/, because this script edits those files to switch between source states
and restores them with ``git checkout`` afterwards.

The four states:

    shipped    as released
    chunk      the download chunk size raised to 8192
    buffer     the .text read on the streamed response dropped
    both       the patch in patch/document-streaming.patch

Each measurement runs in its own process against a local stand-in for the API,
so no API key and no network access are involved. ``buffer`` is the slow one:
it restores real streaming while leaving the one byte chunk size in place, so
give it time.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VARIANTS = ["shipped", "chunk", "buffer", "both"]


def run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, cwd=HERE
    )
    if result.returncode != 0:
        sys.exit(f"failed: {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def restore(checkout: str) -> None:
    subprocess.run(
        ["git", "checkout", "--", "deepl/"],
        cwd=checkout,
        capture_output=True,
        check=True,
    )


def measure(checkout: str, variant: str, sizes, mode: str):
    restore(checkout)
    run(os.path.join(HERE, "apply_variant.py"), checkout, variant)
    rows = []
    for mb in sizes:
        rows.append(
            run(
                os.path.join(HERE, "measure.py"),
                checkout,
                variant,
                str(mb),
                mode,
            )
        )
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    checkout = os.path.abspath(sys.argv[1])
    sizes = [float(x) for x in sys.argv[2:]] or [1, 4, 10, 20]

    dirty = subprocess.run(
        ["git", "status", "--porcelain", "deepl/"],
        cwd=checkout,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        sys.exit(
            f"{checkout} has uncommitted changes under deepl/. This script "
            "edits those files and restores them with git checkout, so it "
            "would throw that work away:\n" + dirty
        )

    try:
        print("=" * 72)
        print("Time for one document translation, end to end")
        print("=" * 72)
        for variant in VARIANTS:
            for row in measure(checkout, variant, sizes, "time"):
                print(row)
            print()

        print("=" * 72)
        print("Peak memory in the client process")
        print("=" * 72)
        for variant in ["shipped", "both"]:
            for row in measure(checkout, variant, sizes, "mem"):
                print(row)
            print()
    finally:
        restore(checkout)
        print(f"restored {checkout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
