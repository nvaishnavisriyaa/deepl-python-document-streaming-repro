"""Apply the output file guard to a deepl-python checkout.

Usage:
    python apply_output_guard.py <checkout>

``translate_document_from_filepath`` writes straight to the output path, which
truncates whatever is already there before any translation work starts, and
then deletes it if the translation fails. This translates into a temporary file
beside the output path and moves it into place once the translation succeeds.

Moving it into place rather than refusing to overwrite keeps the current
behaviour for callers who do pass an existing path on purpose. The Node, PHP
and .NET clients refuse instead, which would be a behaviour change here.
"""

import sys

OLD = """        with open(input_path, "rb") as in_file:
            with open(output_path, "wb") as out_file:
                try:
                    return self.translate_document(
                        in_file,
                        out_file,
                        target_lang=target_lang,
                        source_lang=source_lang,
                        formality=formality,
                        glossary=glossary,
                        glossary_ids=glossary_ids,
                        style_rule=style_rule,
                        translation_memory=translation_memory,
                        translation_memory_threshold=(
                            translation_memory_threshold
                        ),
                        output_format=output_format,
                        timeout_s=timeout_s,
                        extra_body_parameters=extra_body_parameters,
                    )
                except Exception as e:
                    out_file.close()
                    os.unlink(output_path)
                    raise e
"""

NEW = """        # Translate into a temporary file beside the output path and move it
        # into place once the translation has succeeded. Writing straight to
        # output_path truncates anything already there before any work starts,
        # and the failure path below then deletes it, so a failed call used to
        # destroy an existing file at that path.
        directory = os.path.dirname(os.path.abspath(output_path))
        with open(input_path, "rb") as in_file:
            descriptor, partial_path = tempfile.mkstemp(
                dir=directory, suffix=out_ext
            )
            try:
                with os.fdopen(descriptor, "wb") as out_file:
                    status = self.translate_document(
                        in_file,
                        out_file,
                        target_lang=target_lang,
                        source_lang=source_lang,
                        formality=formality,
                        glossary=glossary,
                        glossary_ids=glossary_ids,
                        style_rule=style_rule,
                        translation_memory=translation_memory,
                        translation_memory_threshold=(
                            translation_memory_threshold
                        ),
                        output_format=output_format,
                        timeout_s=timeout_s,
                        extra_body_parameters=extra_body_parameters,
                    )
                os.replace(partial_path, output_path)
                return status
            except Exception as e:
                try:
                    os.unlink(partial_path)
                except OSError:
                    pass
                raise e
"""

IMPORT_OLD = """import pathlib
import requests  # type: ignore
import time
"""

IMPORT_NEW = """import pathlib
import requests  # type: ignore
import tempfile
import time
"""

EDITS = [(OLD, NEW), (IMPORT_OLD, IMPORT_NEW)]


def main() -> int:
    path = f"{sys.argv[1]}/deepl/translator.py"
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    for index, (old, new) in enumerate(EDITS, start=1):
        count = text.count(old)
        if count != 1:
            print(f"edit {index}: matched {count} times, expected 1")
            return 1
        text = text.replace(old, new)

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"applied the output file guard to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
