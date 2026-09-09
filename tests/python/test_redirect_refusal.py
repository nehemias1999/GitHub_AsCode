"""The token must never reach the second host.

`GitHubAsCode.Http.psm1` sets `MaximumRedirection = 0`, and the reason is the most
carefully reasoned security decision in that module: an Authorization header forwarded
to whatever host a 30x names is a credential handed to a third party, with no error and
no log line.

`urllib.request` follows redirects by default and re-sends the headers set on the
request. A naive port therefore inverts that decision silently, which is why ADR 0006
names it as the one class of bug the port introduces.

An assertion about a configuration value would not prove anything here, so this file
runs two real servers on the loopback interface. The first redirects to the second; the
second records every request it receives, headers included. Two tests:

  - the transport refuses, and the second server is never contacted at all
  - the default urllib opener *does* forward the credential, which is what makes the
    guard load bearing rather than decorative

The second test is the planted failure. Without it, the first proves only that
something raised.
"""

import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from github_as_code import http as transport

TOKEN = "EXAMPLE-token-that-must-not-travel"


class _Recorder(BaseHTTPRequestHandler):
    """The second host. Records what it was sent, and answers 200."""

    received: list[dict[str, str]] = []

    def do_GET(self):  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        type(self).received.append(
            {"path": self.path, "authorization": self.headers.get("Authorization", "")}
        )
        body = b'{"reached": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence. The default handler writes every request to stderr, which would bury
        # the suite's own output in noise from a test that is meant to be quiet.
        pass


def _redirector(target: str) -> type[BaseHTTPRequestHandler]:
    """The first host. Answers 302 pointing at the second."""

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    return Redirector


class _Server:
    """One HTTPServer on an ephemeral loopback port, running in a thread."""

    def __init__(self, handler: type[BaseHTTPRequestHandler]):
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ARedirectNeverCarriesTheCredential(unittest.TestCase):
    def setUp(self):
        _Recorder.received = []
        self.second = _Server(_Recorder)
        self.first = _Server(_redirector(self.second.base_url + "/leaked"))
        self.addCleanup(self.first.close)
        self.addCleanup(self.second.close)

    def test_refuses_the_redirect_instead_of_following_it(self):
        with self.assertRaises(transport.TransportError) as caught:
            transport.read_only_request(
                base_url=self.first.base_url,
                path="user",
                headers={"Authorization": f"Bearer {TOKEN}"},
                maximum_retry_count=1,
            )

        message = str(caught.exception)
        self.assertIn("redirected", message)
        self.assertIn("disclose the credential", message)

    def test_does_not_contact_the_second_host_at_all(self):
        # The assertion that matters. Refusing to return the body would not be enough:
        # the disclosure happens when the request is SENT, so the proof has to be that
        # the second server saw nothing, not that the caller got an error.
        with self.assertRaises(transport.TransportError):
            transport.read_only_request(
                base_url=self.first.base_url,
                path="user",
                headers={"Authorization": f"Bearer {TOKEN}"},
                maximum_retry_count=1,
            )

        self.assertEqual([], _Recorder.received)

    def test_the_token_appears_nowhere_in_the_refusal(self):
        # The message names the URL that was requested, and a base URL is one edit away
        # from carrying userinfo. Nothing in this path should be able to print a token.
        with self.assertRaises(transport.TransportError) as caught:
            transport.read_only_request(
                base_url=self.first.base_url,
                path="user",
                headers={"Authorization": f"Bearer {TOKEN}"},
                maximum_retry_count=1,
            )

        self.assertNotIn(TOKEN, str(caught.exception))


class TheDefaultOpenerWouldHaveLeakedIt(unittest.TestCase):
    """The planted failure, so the guard above is measured rather than asserted."""

    def setUp(self):
        _Recorder.received = []
        self.second = _Server(_Recorder)
        self.first = _Server(_redirector(self.second.base_url + "/leaked"))
        self.addCleanup(self.first.close)
        self.addCleanup(self.second.close)

    def test_urllib_forwards_the_authorization_header_across_the_redirect(self):
        # Run with the stock opener, which is what a naive port would use. If this ever
        # stops holding - because a future Python strips the header on a cross-host
        # redirect the way some clients do - the test fails, and that is the right
        # outcome: it means the hazard this module guards against has changed shape and
        # the reasoning above needs rereading rather than trusting.
        request = urllib.request.Request(
            self.first.base_url + "/user",
            headers={"Authorization": f"Bearer {TOKEN}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
        except urllib.error.URLError as error:  # pragma: no cover - not the point of the test
            self.fail(f"The stock opener could not complete the redirect: {error}")

        self.assertEqual(1, len(_Recorder.received), "The second host was not reached at all.")
        self.assertIn(
            TOKEN,
            _Recorder.received[0]["authorization"],
            "urllib no longer forwards Authorization across hosts. Re-read the reasoning "
            "in src/github_as_code/http.py before relaxing anything.",
        )


if __name__ == "__main__":
    unittest.main()
