# Document translation in deepl-python buffers the whole file, then writes it one byte at a time

Reproduction, measurements and a patch for two defects in the document
translation path of
[DeepLcom/deepl-python](https://github.com/DeepLcom/deepl-python).

Checked against **v1.32.0**, and against `main` at `7257b99`.

Both defects are in `deepl/translator.py`. They sit on either side of the same
`stream=True` download, and they hide each other:

1. `_api_call` reads `.text` on the streamed response, which downloads and
   decodes the entire document before the caller can stream any of it. The
   only thing that value is used for is a debug log line that is normally
   thrown away.
2. `translate_document_download` defaults to `chunk_size=1`, and the only
   caller does not override it, so the document is written one byte per loop
   iteration.

**Fixing either one on its own is a trap.** Defect 1 is what makes defect 2
survivable, because once the body is already in memory the one byte loop is
reading memory rather than a socket. Repair the streaming and leave the chunk
size alone, and a 20 MB document goes from 8 seconds to over 3 minutes.

## The measurements

A single document translation, end to end, through the public
`translate_document_from_filepath`:

| document | as shipped | chunk size fixed | streaming fixed | both fixed |
| --- | --- | --- | --- | --- |
| 1 MB | 0.412s | 0.141s | 7.395s | 0.142s |
| 4 MB | 1.081s | 0.150s | 36.448s | 0.141s |
| 10 MB | 3.012s | 0.193s | 97.644s | 0.170s |
| 20 MB | 8.028s | 0.278s | 191.723s | 0.171s |

Peak memory in the client process, for the same translations:

| document | as shipped | both fixed |
| --- | --- | --- |
| 1 MB | 4.1MB, 4.1x the document | 0.1MB |
| 4 MB | 16.1MB, 4.0x the document | 0.1MB |
| 10 MB | 40.1MB, 4.0x the document | 0.1MB |
| 20 MB | 80.1MB, 4.0x the document | 0.1MB |

Three things to read out of those tables.

- As shipped, a 20 MB document takes **47 times** longer than it needs to.
- As shipped, peak memory is **4x the document size** and grows with it. With
  the patch it is flat at 0.1MB, which is what `stream=True` was for.
- The `streaming fixed` column is the trap. Fixing the buffering alone makes a
  20 MB document **24 times slower than it is today**.

Absolute times depend on the machine. The ratios and the memory shape do not.

## Reproducing

No API key and no network access. `fake_api.py` answers the three document
endpoints on localhost, so the only thing under measurement is the client.

```bash
pip install -r requirements.txt
git clone https://github.com/DeepLcom/deepl-python
python reproduce.py ./deepl-python            # both tables above
```

The defects as tests:

```bash
DEEPL_CHECKOUT=./deepl-python pytest test_streaming.py -v
# 4 failed, 4 passed   against the shipped source

cd deepl-python && git apply ../patch/document-streaming.patch && cd ..
DEEPL_CHECKOUT=./deepl-python pytest test_streaming.py -v
# 8 passed
```

`reproduce.py` edits `deepl/` to switch between source states and restores it
with `git checkout` afterwards, so it refuses to run if that directory has
uncommitted work in it.

## Defect 1: the streamed body is read into memory for a discarded log line

`deepl/translator.py:163`, in `_api_call`:

```python
json = None
if isinstance(content, str):
    content_str = content
    try:
        json = json_module.loads(content)
    except json_module.JSONDecodeError:
        pass
else:
    content_str = content.text          # <- the whole document, decoded

util.log_info("DeepL API response", url=url, status_code=status_code)
util.log_debug("Response details", content=content_str)
```

`content` is a `requests.Response` only when `stream=True`, and the one place
in the library that streams through `_api_call` is the document download at
line 1063. So for every document translation, that `else` branch downloads the
whole body and decodes it to `str` before `translate_document_download` gets to
stream anything.

`content_str` has exactly one consumer, the `log_debug` two lines below.

That log call does not short circuit. From `deepl/util.py`:

```python
def _get_log_text(message, **kwargs):
    return (
        message
        + " "
        + " ".join(f"{key}={value}" for key, value in sorted(kwargs.items()))
    )


def log_debug(message, **kwargs):
    text = _get_log_text(message, **kwargs)
    logger.debug(text)
```

The message is built before `logger.debug` is called and there is no level
check, so a second full copy of the document is formatted into a string on
every download, whether or not debug logging is switched on. That is where the
4x comes from: the raw body, the decoded `str`, and the log line.

A detail worth noting, because it is what makes the fix safe: on the download
path the error branch never looks at this value either.
`translate_document_download` passes the literal string `"<file>"` to
`_raise_for_status`, not the response, so no error message loses anything when
the `.text` read goes away. `test_failed_download_still_raises_with_the_status_code`
and `test_error_body_is_still_reported_on_a_normal_call` pin both halves of
that.

## Defect 2: the download loop runs one byte at a time

`deepl/translator.py:1037`:

```python
def translate_document_download(
    self,
    handle: DocumentHandle,
    output_file: Union[TextIO, BinaryIO, Any, None] = None,
    chunk_size: int = 1,                      # <- the default
) -> Optional[requests.Response]:
    ...
    if output_file:
        chunks = response.iter_content(chunk_size=chunk_size)
        for chunk in chunks:
            output_file.write(chunk)
```

There is one call site, `deepl/translator.py:834`, inside
`translate_document`, and it does not override the default:

```python
status = self.translate_document_wait_until_done(handle, timeout_s)
if status.ok:
    self.translate_document_download(handle, output_document)
```

`DeepLClient` subclasses `Translator` and overrides none of these methods, so
the recommended entry point gets the same default. Every user of
`translate_document` and `translate_document_from_filepath` is on this path.

### The same codebase already uses 8192

`deepl/deepl_client.py:1299` declares the same `chunk_size: int = 1` default on
`download_translation_memory_export`, but its caller overrides it, at
`deepl/deepl_client.py:1415`:

```python
with open(output_path, "wb") as output_file:
    self.download_translation_memory_export(
        job, output_file, chunk_size=8192
    )
```

The translation memory export path also avoids defect 1. It goes through
`_asset_call`, which hands the response back without touching `.text`, and
`_raise_for_asset_status` uses `""` rather than the body for a streamed
response.

So the newer code gets both of these right and the older document path gets
both wrong. That is the clearest sign this is an oversight rather than a
decision.

The patch raises the translation memory default to 8192 as well, since a caller
using that public method directly still gets 1 today.

## How the other DeepL clients do it

| client | how the download is written | guards the output file |
| --- | --- | --- |
| deepl-python | hand written loop, `chunk_size=1` | no, `open(path, "wb")` truncates |
| deepl-node 1.28.1 | pipes the response into a write stream | yes, `createWriteStream(..., {flags: "wx"})` |
| deepl-php | hands the path to curl | yes, `file_exists` then throw |
| deepl-dotnet | `response.Content.CopyToAsync(outputFile)` | yes, `FileMode.CreateNew` |

Python is the only one of the four that iterates the body itself, and the only
one that iterates it a byte at a time.

## The patch

`patch/document-streaming.patch` applies to v1.32.0 and to `main` at `7257b99`:

```bash
git apply patch/document-streaming.patch
```

Six insertions, three deletions, across two files. Three of the insertions are
a comment.

```diff
--- a/deepl/translator.py
+++ b/deepl/translator.py
@@ -160,7 +160,10 @@ class Translator:
             except json_module.JSONDecodeError:
                 pass
         else:
-            content_str = content.text
+            # Streamed response. Reading .text here would download and decode
+            # the whole body before the caller gets to stream it, which
+            # defeats the streaming that was asked for.
+            content_str = "<streamed response>"
 
         util.log_info("DeepL API response", url=url, status_code=status_code)
         util.log_debug("Response details", content=content_str)
@@ -1038,7 +1041,7 @@ class Translator:
         self,
         handle: DocumentHandle,
         output_file: Union[TextIO, BinaryIO, Any, None] = None,
-        chunk_size: int = 1,
+        chunk_size: int = 8192,
     ) -> Optional[requests.Response]:
```

plus the same one line change to the translation memory default in
`deepl/deepl_client.py`.

`black --check`, `flake8` and `mypy` all pass on the patched files under the
repo's own settings, line length 79. `black` does report
`deepl/http_client.py` as needing reformatting, but it does that on an
untouched v1.32.0 checkout too, so it is not from this change.

Nothing in the existing test suite pins either default. The only tests that
mention `chunk_size` pass 128 explicitly:

```python
for chunk in response.iter_content(chunk_size=128):
```

### Why a placeholder rather than deleting the log call

Keeping the `log_debug` call means the download still logs a line at debug
level, so anyone relying on that log for request tracing keeps it. Only the
body is left out. A different choice would be to make the logging lazy, by
moving the formatting behind `logger.isEnabledFor(logging.DEBUG)`. That would
help every other call in the library as well, but it is a wider change than
this one, and for a multi-megabyte document the body does not belong in a log
line at any level.

## Also found: a failed translation destroys an existing output file

Separate and smaller, in `translate_document_from_filepath`, at
`deepl/translator.py:725`:

```python
with open(input_path, "rb") as in_file:
    with open(output_path, "wb") as out_file:   # truncates before any work
        try:
            return self.translate_document(...)
        except Exception as e:
            out_file.close()
            os.unlink(output_path)              # and deletes it
            raise e
```

Point it at a path that already holds a file, have the translation fail, and
that file is gone. `test_output_file.py` shows this against the shipped source.

`patch/output-file-guard.patch` translates into a temporary file beside the
output path and moves it into place with `os.replace` once the translation has
succeeded. It is offered separately because it is a different fix, and because
the alternative that the Node, PHP and .NET clients use, refusing to overwrite
at all, would be a behaviour change for anyone passing an existing path on
purpose. Moving the finished file into place keeps that working and still never
destroys the target.

Both patches apply cleanly together, in either order.

## What is in here

| File | What it is |
| --- | --- |
| `reproduce.py` | runs all four source states and prints both tables |
| `measure.py` | one translation, timed or measured for peak memory |
| `fake_api.py` | the three DeepL document endpoints, on localhost, in their own process |
| `apply_variant.py` | switches a checkout between the four source states |
| `test_streaming.py` | the two defects as tests, with guards on what must not change |
| `test_output_file.py` | the output file finding as a test |
| `apply_output_guard.py` | applies the output file fix to a checkout |
| `patch/document-streaming.patch` | the fix, `git apply` ready |
| `patch/output-file-guard.patch` | the output file fix, separately |
| `RESULTS.txt` | the output of `reproduce.py` on a fresh v1.32.0 checkout |

## On trusting the numbers

Nothing here reimplements any part of the library. `measure.py` puts the
checkout on `sys.path`, imports `deepl`, and calls the public
`translate_document_from_filepath`, so whatever source state the checkout is in
is what gets measured. It also reads the `chunk_size` default straight off the
function with `inspect.signature` and prints it on every row, which is why the
`chunk_size=` column in the tables is evidence rather than a label.

Two measurement details that matter:

- **Timing and memory are measured in separate runs.** `tracemalloc` traces
  every allocation, and `chunk_size=1` makes millions of them, so leaving it on
  inflates the timings by roughly an order of magnitude.
- **The stand-in API runs in its own process.** It has to build the document
  bytes to serve them, and in a single process those allocations land in the
  same `tracemalloc` total and swamp the number being measured.

The stand-in answers `"done"` on the first status poll, which keeps the
library's own five second polling sleep out of the timings. It serves over real
HTTP on localhost, so the transport is real but the latency is near zero. On a
real connection the download time is added to both columns, so the gap stays
the same while the ratio shrinks. The memory figures are unaffected by that.

## Licence

MIT, the same as deepl-python. The patch files contain short excerpts of
deepl-python source, which is MIT licensed, copyright DeepL SE.
