import http.client
import threading
import unittest

from keepalive_server import HealthCheckHandler, ReusableTCPServer


def _request(path: str, method: str = "GET") -> tuple[int, bytes]:
    server = ReusableTCPServer(("127.0.0.1", 0), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = None

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        if connection is not None:
            connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class HealthCheckHandlerTests(unittest.TestCase):
    def test_head_checks_return_status_without_body(self):
        for path, expected_status in (("/", 200), ("/health", 200), ("/.env", 404)):
            with self.subTest(path=path):
                status, body = _request(path, "HEAD")
                self.assertEqual(status, expected_status)
                self.assertEqual(body, b"")

    def test_health_paths_return_ok(self):
        for path in ("/", "/health"):
            with self.subTest(path=path):
                status, body = _request(path)

                self.assertEqual(status, 200)
                self.assertEqual(body, b"OK\n")

    def test_does_not_serve_project_files_or_directories(self):
        blocked_paths = (
            "/logs/bot.log",
            "/fitness_bot.db",
            "/.env",
            "/main.py",
            "/database/",
            "/../main.py",
            "/%2e%2e/main.py",
        )

        for path in blocked_paths:
            with self.subTest(path=path):
                status, body = _request(path)

                self.assertEqual(status, 404)
                self.assertEqual(body, b"Not Found\n")


if __name__ == "__main__":
    unittest.main()
