"""A stand-in for the DeepL document API, run as its own process.

Usage:
    python fake_api.py <size in bytes> [error status] [result|status]

Prints the port it is listening on as the first line of stdout, then serves
until it is killed. It answers the three endpoints the document translation
flow uses:

    POST /v2/document              upload, hands back a document id and key
    POST /v2/document/{id}         status, answers "done" on the first poll
    POST /v2/document/{id}/result  the translated bytes

Answering "done" straight away keeps the client's five second polling sleep out
of any measurement. This runs in its own process so that a memory measurement
in the client sees only the client's own allocations, not the bytes the server
side had to build.

With an error status, the named endpoint answers that status with a short body
instead. ``result`` is the streamed endpoint and ``status`` is a normal one,
which is the difference the error tests turn on.

No API key and no network access are involved.
"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Plausible document bytes. Text rather than random noise, since a real
# document decodes like text.
PAYLOAD = b"Bonjour, le monde ! " * 100_000

# Deliberately not JSON. If it parsed, the phrase would reach the exception
# through the "message" field rather than through the response body, and the
# test that uses it would pass for the wrong reason.
ERROR_BODY = b"the body of the error response"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    body = b""
    error_status = 0
    error_on = "result"

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self) -> None:
        self._send(self.error_status, "text/plain", ERROR_BODY)

    def do_POST(self):
        # Drain the upload so the client is never left blocked on a write.
        remaining = int(self.headers.get("Content-Length") or 0)
        while remaining > 0:
            remaining -= len(self.rfile.read(min(65536, remaining)))

        path = self.path.rstrip("/")
        if path.endswith("/result"):
            if self.error_status and self.error_on == "result":
                self._fail()
            else:
                self._send(200, "application/octet-stream", self.body)
        elif path == "/v2/document":
            self._send(
                200,
                "application/json",
                b'{"document_id": "docid", "document_key": "dockey"}',
            )
        else:
            if self.error_status and self.error_on == "status":
                self._fail()
            else:
                self._send(
                    200,
                    "application/json",
                    b'{"document_id": "docid", "status": "done",'
                    b' "billed_characters": 1}',
                )

    def log_message(self, *args):
        pass


def main() -> int:
    size = int(sys.argv[1])
    Handler.error_status = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    Handler.error_on = sys.argv[3] if len(sys.argv) > 3 else "result"
    Handler.body = (PAYLOAD * (size // len(PAYLOAD) + 1))[:size]

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
