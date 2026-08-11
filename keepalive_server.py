"""Безопасный HTTP handler для health check сервиса."""
import http.server
import socketserver


class ReusableTCPServer(socketserver.TCPServer):
    """TCP сервер с возможностью переиспользования адреса."""

    allow_reuse_address = True


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """Отвечает только на health-check пути и не обращается к файлам."""

    server_version = "Sumday77Health"
    sys_version = ""
    health_paths = frozenset({"/", "/health"})

    def do_GET(self):
        if self.path not in self.health_paths:
            self._send_text_response(404, b"Not Found\n")
            return

        self._send_text_response(200, b"OK\n")

    def _send_text_response(self, status_code: int, body: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format, *args):
        pass
