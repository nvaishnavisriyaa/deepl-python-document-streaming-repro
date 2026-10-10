"""Apply one source variant to a deepl-python checkout.

Usage:
    python apply_variant.py <checkout> shipped|buffer|chunk|both

    shipped   leave the checkout alone
    buffer    stop reading .text on a streamed response
    chunk     raise the translate_document_download default to 8192
    both      apply both

Each edit must match exactly once, so a drifted checkout fails loudly rather
than being measured in an unexpected state.
"""

import sys

# Reading .text on a streamed response buffers and decodes the entire body.
# Its only consumer is the log_debug call two lines down.
BUFFER_OLD = """        else:
            content_str = content.text

        util.log_info("DeepL API response", url=url, status_code=status_code)
        util.log_debug("Response details", content=content_str)
"""

BUFFER_NEW = """        else:
            # Streamed response. Reading .text here would download and decode
            # the whole body before the caller gets to stream it, which
            # defeats the streaming that was asked for.
            content_str = "<streamed response>"

        util.log_info("DeepL API response", url=url, status_code=status_code)
        util.log_debug("Response details", content=content_str)
"""

CHUNK_OLD = """        output_file: Union[TextIO, BinaryIO, Any, None] = None,
        chunk_size: int = 1,
"""

CHUNK_NEW = """        output_file: Union[TextIO, BinaryIO, Any, None] = None,
        chunk_size: int = 8192,
"""

# The same default on the translation memory export. Its one internal caller
# passes 8192 already, so only a caller using the public method directly is
# affected.
TM_OLD = """        output_file: Union[BinaryIO, Any, None] = None,
        chunk_size: int = 1,
"""

TM_NEW = """        output_file: Union[BinaryIO, Any, None] = None,
        chunk_size: int = 8192,
"""

EDITS = {
    "buffer": [("deepl/translator.py", BUFFER_OLD, BUFFER_NEW)],
    "chunk": [
        ("deepl/translator.py", CHUNK_OLD, CHUNK_NEW),
        ("deepl/deepl_client.py", TM_OLD, TM_NEW),
    ],
}
EDITS["both"] = EDITS["buffer"] + EDITS["chunk"]
EDITS["shipped"] = []


def main() -> int:
    checkout, variant = sys.argv[1], sys.argv[2]
    for relative, old, new in EDITS[variant]:
        path = f"{checkout}/{relative}"
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        count = text.count(old)
        if count != 1:
            print(f"{variant}: {relative} matched {count} times, expected 1")
            return 1
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace(old, new))
    print(f"variant: {variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
