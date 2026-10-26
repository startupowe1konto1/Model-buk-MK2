"""Local preview of the MK2 app: static files with UTF-8 declared (the published page gets its charset from the
artifact skeleton, a bare local server would not send one). Bound to 127.0.0.1 only."""
import functools
import http.server
import sys
from pathlib import Path

root = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        # /__test: the page with an in-memory window.claude double injected first, to exercise the editing paths
        # /__dev: the same with index_dev.html (a working copy that is never published)
        if self.path.split("?")[0] in ("/__test", "/__dev", "/__fake.js"):
            fake = Path("\\\\?\\" + str(Path(__file__).resolve().parent / "fake_claude.js"))  # long-path prefix
            if self.path.startswith("/__fake.js"):
                body, ctype = fake.read_bytes(), "text/javascript; charset=utf-8"
            else:
                name = "index_dev.html" if self.path.startswith("/__dev") else "index.html"
                page = (Path(root) / name).read_text(encoding="utf-8")
                head = ('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
                        '<script src="/__fake.js"></script>\n')   # what the publish skeleton adds, plus the db double
                body, ctype = (head + page).encode("utf-8"), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


Server(("127.0.0.1", port), functools.partial(Handler, directory=root)).serve_forever()
