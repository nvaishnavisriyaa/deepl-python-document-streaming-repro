"""Time or measure the memory of one real document translation.

Imports deepl from the checkout given on the command line and calls the public
``translate_document_from_filepath``, so whatever source state that checkout is
in is what gets measured. The API stand-in runs as a separate process, so a
memory measurement sees only the client's own allocations.

Usage:
    python measure.py <checkout> <label> <size in MB> time|mem

tracemalloc traces every allocation and ``chunk_size=1`` makes millions of
them, so timing and memory have to be measured in separate runs.
"""

import os
import subprocess
import sys
import tempfile
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    checkout, label, mb, mode = (
        sys.argv[1],
        sys.argv[2],
        float(sys.argv[3]),
        sys.argv[4],
    )
    n = int(mb * 1024 * 1024)

    server = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_api.py"), str(n)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert server.stdout is not None
    port = int(server.stdout.readline().strip())

    sys.path.insert(0, os.path.abspath(checkout))
    import inspect

    import deepl

    default = (
        inspect.signature(deepl.Translator.translate_document_download)
        .parameters["chunk_size"]
        .default
    )

    translator = deepl.Translator("test-key", server_url=f"http://127.0.0.1:{port}")
    tmp = tempfile.mkdtemp(prefix="deepl-measure-")
    src, dst = os.path.join(tmp, "in.txt"), os.path.join(tmp, "out.txt")
    with open(src, "wb") as handle:
        handle.write(b"x" * 1024)

    try:
        if mode == "mem":
            tracemalloc.start()
        start = time.perf_counter()
        translator.translate_document_from_filepath(src, dst, target_lang="FR")
        elapsed = time.perf_counter() - start
        traced_peak = 0
        if mode == "mem":
            _, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        written = os.path.getsize(dst)
        assert written == n, f"expected {n} bytes, got {written}"

        if mode == "mem":
            print(
                f"{label:<10} {mb:>6.2f}MB  chunk_size={default:<5} "
                f"peak {traced_peak / (1024 * 1024):>7.1f}MB  "
                f"= {traced_peak / n:>4.1f}x the document"
            )
        else:
            print(f"{label:<10} {mb:>6.2f}MB  chunk_size={default:<5} {elapsed:>9.3f}s")
    finally:
        server.kill()
        server.wait()
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
