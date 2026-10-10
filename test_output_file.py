"""A separate, smaller finding: a failed translation destroys the output file.

``translate_document_from_filepath`` opens the output path for writing before
any translation work starts, and deletes it if anything fails. Point it at a
file that already exists and a failure takes that file with it.

This is kept apart from ``test_streaming.py`` because it is a different fix,
offered separately in ``patch/output-file-guard.patch``.

    DEEPL_CHECKOUT=./deepl-python pytest test_output_file.py -v
"""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.environ.get("DEEPL_CHECKOUT")

EXISTING = b"work the user already had on disk"


def _load_deepl():
    if not CHECKOUT:
        pytest.skip("set DEEPL_CHECKOUT to a deepl-python checkout")
    path = os.path.abspath(CHECKOUT)
    if path not in sys.path:
        sys.path.insert(0, path)
    import deepl

    return deepl


@pytest.fixture
def failing_api():
    """A stand-in that fails the download, after the upload has succeeded."""
    process = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_api.py"), "1024", "418", "result"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    port = int(process.stdout.readline().strip())
    yield f"http://127.0.0.1:{port}"
    process.kill()
    process.wait()


def test_failed_translation_leaves_an_existing_output_file_alone(failing_api, tmp_path):
    deepl = _load_deepl()

    source = tmp_path / "in.txt"
    source.write_bytes(b"x" * 1024)
    target = tmp_path / "out.txt"
    target.write_bytes(EXISTING)

    translator = deepl.Translator("test-key", server_url=failing_api)
    with pytest.raises(deepl.DocumentTranslationException):
        translator.translate_document_from_filepath(
            str(source), str(target), target_lang="FR"
        )

    assert target.exists(), (
        "the translation failed and took an existing file at the output path "
        "with it. The file is opened with mode 'wb' before any work starts, "
        "then os.unlink'd when the translation raises."
    )
    assert (
        target.read_bytes() == EXISTING
    ), "the file at the output path survived but its contents were replaced."
