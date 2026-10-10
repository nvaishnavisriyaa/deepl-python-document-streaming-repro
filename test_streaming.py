"""The defects as tests.

Point ``DEEPL_CHECKOUT`` at a deepl-python checkout and run pytest. Against the
shipped source the four defect tests fail. With the patch applied all of them
pass.

    pip install -r requirements.txt
    git clone https://github.com/DeepL/deepl-python
    DEEPL_CHECKOUT=./deepl-python pytest test_streaming.py -v

The tests after the defect tests are guards. They pin the behaviour the fix has
to leave alone, so they pass either way.

No API key and no network access are needed. ``fake_api.py`` stands in for the
DeepL document endpoints on localhost.
"""

import inspect
import logging
import os
import subprocess
import sys
import tracemalloc

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.environ.get("DEEPL_CHECKOUT")

MB = 1024 * 1024


def _load_deepl():
    if not CHECKOUT:
        pytest.skip("set DEEPL_CHECKOUT to a deepl-python checkout")
    path = os.path.abspath(CHECKOUT)
    if path not in sys.path:
        sys.path.insert(0, path)
    import deepl

    return deepl


class FakeApi:
    """The DeepL document endpoints, in their own process.

    Separate process so that a memory measurement in here sees only this
    process's allocations, not the bytes the server side had to build.
    """

    def __init__(
        self, size_bytes: int, error_status: int = 0, error_on: str = "result"
    ):
        self.process = subprocess.Popen(
            [
                sys.executable,
                os.path.join(HERE, "fake_api.py"),
                str(size_bytes),
                str(error_status),
                error_on,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        self.port = int(self.process.stdout.readline().strip())

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.process.kill()
        self.process.wait()


@pytest.fixture
def api():
    created = []

    def make(size_bytes: int, **kwargs) -> FakeApi:
        server = FakeApi(size_bytes, **kwargs)
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


def _translate(deepl_module, server, tmp_path, size_bytes):
    source = tmp_path / "in.txt"
    source.write_bytes(b"x" * 1024)
    target = tmp_path / "out.txt"
    translator = deepl_module.Translator("test-key", server_url=server.url)
    translator.translate_document_from_filepath(
        str(source), str(target), target_lang="FR"
    )
    assert target.stat().st_size == size_bytes
    return target


# ---------------------------------------------------------------------------
# The defects.
# ---------------------------------------------------------------------------


def test_download_chunk_size_default_is_usable():
    """The shipped default of 1 reads the document one byte per iteration."""
    deepl = _load_deepl()
    default = (
        inspect.signature(deepl.Translator.translate_document_download)
        .parameters["chunk_size"]
        .default
    )
    assert default >= 4096, (
        f"translate_document_download defaults to chunk_size={default}. Its "
        "only caller, translate_document, does not override it, so every "
        "document is read one byte at a time."
    )


def test_tm_export_chunk_size_default_is_usable():
    """The same default on the translation memory export path."""
    deepl = _load_deepl()
    default = (
        inspect.signature(deepl.DeepLClient.download_translation_memory_export)
        .parameters["chunk_size"]
        .default
    )
    assert default >= 4096, (
        f"download_translation_memory_export defaults to chunk_size={default}. "
        "export_translation_memory_to_filepath passes 8192 explicitly, so only "
        "a caller using the public method directly gets the slow path."
    )


def test_peak_memory_does_not_scale_with_document_size(api, tmp_path):
    """Streaming should hold a chunk, not the whole document.

    _api_call reads .text on the streamed response before the caller gets to
    stream it, which downloads and decodes the entire body.
    """
    deepl = _load_deepl()
    size = 8 * MB
    server = api(size)

    tracemalloc.start()
    try:
        _translate(deepl, server, tmp_path, size)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < size, (
        f"peak allocation was {peak / MB:.1f}MB for an {size / MB:.0f}MB "
        "document, so the body is being buffered rather than streamed."
    )


def test_streamed_body_is_not_formatted_into_a_log_line(api, tmp_path):
    """The buffered body ends up in a log string that is then discarded.

    util._get_log_text builds its message eagerly and there is no level check,
    so the whole document is formatted into a string on every download even
    when debug logging is switched off.
    """
    deepl = _load_deepl()
    size = 4 * MB
    server = api(size)

    longest = 0

    class Recorder(logging.Handler):
        def emit(self, record):
            nonlocal longest
            longest = max(longest, len(record.getMessage()))

    logger = logging.getLogger("deepl")
    handler = Recorder()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        _translate(deepl, server, tmp_path, size)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert longest < size / 2, (
        f"a log line of {longest / MB:.1f}MB was built for an "
        f"{size / MB:.0f}MB document."
    )


# ---------------------------------------------------------------------------
# Guards on what the fix must not change.
# ---------------------------------------------------------------------------


def test_downloaded_bytes_are_unchanged(api, tmp_path):
    """Whatever the chunk size, the file on disk has to match the body."""
    deepl = _load_deepl()
    size = 3 * MB + 7  # deliberately not a multiple of any chunk size
    server = api(size)
    target = _translate(deepl, server, tmp_path, size)

    unit = b"Bonjour, le monde ! " * 100_000
    expected = (unit * (size // len(unit) + 1))[:size]
    assert target.read_bytes() == expected


def test_failed_download_still_raises_with_the_status_code(api, tmp_path):
    """A failed download still reports the status it failed with.

    The download path passes the literal string "<file>" to _raise_for_status
    rather than the response, so the streamed body never reaches the error
    message. That is why dropping the .text read cannot change this.
    """
    deepl = _load_deepl()
    server = api(1024, error_status=418)

    source = tmp_path / "in.txt"
    source.write_bytes(b"x" * 1024)
    translator = deepl.Translator("test-key", server_url=server.url)

    with pytest.raises(deepl.DocumentTranslationException) as caught:
        translator.translate_document_from_filepath(
            str(source), str(tmp_path / "out.txt"), target_lang="FR"
        )
    assert "418" in str(caught.value)


def test_error_body_is_still_reported_on_a_normal_call(api, tmp_path):
    """A non-streamed call still carries the response body in the error.

    _api_call has two branches. Only the streamed one is being changed, and
    this pins that the other one still reports what the server said.
    """
    deepl = _load_deepl()
    server = api(1024, error_status=418, error_on="status")

    source = tmp_path / "in.txt"
    source.write_bytes(b"x" * 1024)
    translator = deepl.Translator("test-key", server_url=server.url)

    with pytest.raises(deepl.DocumentTranslationException) as caught:
        translator.translate_document_from_filepath(
            str(source), str(tmp_path / "out.txt"), target_lang="FR"
        )
    assert "the body of the error response" in str(caught.value)


def test_translation_still_reports_billed_characters(api, tmp_path):
    """The status object comes back intact."""
    deepl = _load_deepl()
    size = 1 * MB
    server = api(size)

    source = tmp_path / "in.txt"
    source.write_bytes(b"x" * 1024)
    translator = deepl.Translator("test-key", server_url=server.url)
    status = translator.translate_document_from_filepath(
        str(source), str(tmp_path / "out.txt"), target_lang="FR"
    )
    assert status.billed_characters == 1
    assert status.done
