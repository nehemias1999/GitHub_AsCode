"""The GitHub-specific half of talking to the API.

The fixtures are the ones the Pester suite already uses, deliberately. They were built
for the shapes that matter - a repository with a hand-added topic, one with nothing at
all, a private one, an archived one, a Link header with a comma inside a query value -
and pointing both implementations at the same bytes is the cheapest form of the parity
the port is heading towards.
"""

import datetime
import json
import unittest

from github_as_code import http, rest
from support import REPO_ROOT

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TheStatusMapIsDataHandedToTheTransport(unittest.TestCase):
    def test_explains_that_a_github_404_is_ambiguous(self):
        # The single most consequential thing to get wrong: reading 404 as absence
        # produces a plan that says "create" for something that already exists.
        self.assertIn("cannot see it", rest.status_messages()[404])

    def test_separates_the_three_causes_that_share_a_403(self):
        message = rest.status_messages()[403]
        self.assertIn("permission", message)
        self.assertIn("rate limit", message)
        self.assertIn("secondary", message)

    def test_returns_a_copy_so_a_caller_cannot_edit_the_shared_map(self):
        borrowed = rest.status_messages()
        borrowed[404] = "wrong"
        self.assertNotEqual("wrong", rest.status_messages()[404])


class TheRateLimitBudgetIsReadFromHeaders(unittest.TestCase):
    def test_reads_the_budget_out_of_the_response_headers(self):
        state = rest.rate_limit_state(
            {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "4987"}
        )
        self.assertEqual(5000, state.limit)
        self.assertEqual(4987, state.remaining)

    def test_reads_the_reset_as_an_epoch_second_not_as_a_date(self):
        # Treating it as a date silently produces 1970 and a "reset 56 years ago" line
        # in a report.
        state = rest.rate_limit_state({"x-ratelimit-reset": "1767225600"})
        self.assertEqual(2026, state.reset_utc.year)

    def test_reports_an_absent_budget_as_unknown_rather_than_as_zero(self):
        # "unknown" and "none left" must not look alike to a caller deciding whether to
        # continue.
        state = rest.rate_limit_state({})
        self.assertIsNone(state.limit)
        self.assertIsNone(state.remaining)
        self.assertIsNone(state.reset_utc)

    def test_treats_an_unparsable_value_as_unknown(self):
        self.assertIsNone(rest.rate_limit_state({"x-ratelimit-limit": "many"}).limit)


class TheTokenShapeIsToldFromOneHeader(unittest.TestCase):
    def test_recognises_a_classic_token_by_the_scope_header_it_sends(self):
        shape = rest.token_shape({"x-oauth-scopes": "repo, read:org"})
        self.assertTrue(shape.is_classic)
        self.assertEqual(["repo", "read:org"], shape.scope)

    def test_recognises_a_fine_grained_token_by_the_absence_of_that_header(self):
        shape = rest.token_shape({"x-ratelimit-limit": "5000"})
        self.assertFalse(shape.is_classic)
        self.assertEqual([], shape.scope)

    def test_reads_the_expiry_header_github_actually_sends_trailing_utc_label_and_all(self):
        # "2026-12-31 23:59:59 UTC" - no single standard parse handles it.
        shape = rest.token_shape(
            {"github-authentication-token-expiration": "2026-12-31 23:59:59 UTC"},
            now=datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC),
        )
        self.assertEqual(2026, shape.expires_utc.year)
        self.assertEqual(30, shape.days_until_expiry)

    def test_reports_a_past_expiry_as_negative_rather_than_clamping_to_zero(self):
        # "expired three days ago" and "expires today" call for different actions.
        shape = rest.token_shape(
            {"github-authentication-token-expiration": "2020-01-01 00:00:00 UTC"},
            now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )
        self.assertLess(shape.days_until_expiry, 0)

    def test_treats_an_unparsable_expiry_as_absent(self):
        shape = rest.token_shape({"github-authentication-token-expiration": "soon"})
        self.assertIsNone(shape.expires_utc)
        self.assertIsNone(shape.days_until_expiry)


class TheNextPageIsFollowedAndNeverComputed(unittest.TestCase):
    def test_splits_an_absolute_next_page_url_into_the_path_and_query(self):
        target = rest.relative_target(
            "https://api.example.com/user/repos?per_page=100&page=2", "https://api.example.com"
        )
        self.assertEqual("user/repos", target.path)
        self.assertEqual({"per_page": "100", "page": "2"}, target.query)

    def test_refuses_to_follow_a_link_header_that_points_at_another_host(self):
        # A Link header is server-controlled input. Following it elsewhere would send
        # the Authorization header there - the same disclosure the redirect refusal
        # exists to prevent, arriving by a different route.
        with self.assertRaises(http.TransportError) as caught:
            rest.relative_target("https://elsewhere.example.net/x", "https://api.example.com")
        self.assertIn("Refusing to follow", str(caught.exception))

    def test_strips_the_base_path_so_an_enterprise_api_root_does_not_double_up(self):
        target = rest.relative_target(
            "https://ghe.example.com/api/v3/user/repos", "https://ghe.example.com/api/v3"
        )
        self.assertEqual("user/repos", target.path)

    def test_unescapes_a_query_value_rather_than_passing_the_escaped_form_through(self):
        # build_uri escapes on the way back out; passing the escaped form through would
        # double-encode every cursor.
        target = rest.relative_target(
            "https://api.example.com/x?q=a%20b", "https://api.example.com"
        )
        self.assertEqual("a b", target.query["q"])

    def test_rejects_a_next_page_that_is_not_an_absolute_url(self):
        with self.assertRaises(http.TransportError):
            rest.relative_target("/user/repos?page=2", "https://api.example.com")


class _FakeTransport:
    """Records every call and answers from a scripted list of pages.

    The body is handed back as TEXT, because that is what the real transport returns:
    decoding is the transport's job and parsing is this module's, and a fake that
    returned parsed objects would skip the one step where a proxy's HTML login page
    gets caught.
    """

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        content, link = self.pages.pop(0)
        headers = {"Link": link, "x-ratelimit-remaining": "4900"}
        body = content if isinstance(content, str) else json.dumps(content)
        return http.Response(content=body, headers=headers, status_code=200)


def a_context(**overrides) -> rest.GitHubContext:
    values = {
        "base_url": "https://api.example.com",
        "owner": "EXAMPLE-owner",
        "headers": {"Authorization": "Bearer EXAMPLE-token"},
        "token_environment_name": "GITHUB_TOKEN_READ",
        "page_size": 100,
        "maximum_page_count": 50,
    }
    values.update(overrides)
    return rest.GitHubContext(**values)


class PaginationStopsOnlyWhenTheHeaderStopsOffering(unittest.TestCase):
    def test_follows_every_page_the_link_header_offers(self):
        transport = _FakeTransport(
            [
                (fixture_text("repos.page1.json"), fixture_text("link-header-two-links.txt")),
                (fixture_text("repos.page2.json"), fixture_text("link-header-last-page.txt")),
            ]
        )
        result = rest.paged_result(a_context(), "user/repos", transport=transport)
        self.assertEqual(2, result.page_count)
        self.assertEqual(5, len(result.item))
        self.assertFalse(result.truncated)

    def test_does_not_stop_on_a_short_page(self):
        # A short page is not the end of a collection that is changing underneath the
        # reader. Stopping there skips items and reports the truncated list as complete.
        transport = _FakeTransport(
            [
                ([{"name": "EXAMPLE-one"}], fixture_text("link-header-two-links.txt")),
                ([{"name": "EXAMPLE-two"}], fixture_text("link-header-last-page.txt")),
            ]
        )
        result = rest.paged_result(a_context(), "user/repos", transport=transport)
        self.assertEqual(2, len(result.item))

    def test_reports_a_truncated_walk_rather_than_truncating_quietly(self):
        # An inventory that reports fewer repositories than exist, while claiming to be
        # complete, is worse than one that fails.
        transport = _FakeTransport(
            [
                ([{"name": f"EXAMPLE-{n}"}], fixture_text("link-header-two-links.txt"))
                for n in range(3)
            ]
        )
        result = rest.paged_result(
            a_context(maximum_page_count=2), "user/repos", transport=transport
        )
        self.assertTrue(result.truncated)
        self.assertEqual(2, result.page_count)

    def test_sends_the_page_size_on_the_first_request(self):
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        rest.paged_result(a_context(page_size=100), "user/repos", transport=transport)
        self.assertEqual(100, transport.calls[0]["query"]["per_page"])

    def test_takes_the_later_query_from_the_link_header_rather_than_rebuilding_it(self):
        transport = _FakeTransport(
            [
                ([], fixture_text("link-header-two-links.txt")),
                ([], fixture_text("link-header-last-page.txt")),
            ]
        )
        rest.paged_result(a_context(), "user/repos", transport=transport)
        self.assertEqual("2", transport.calls[1]["query"]["page"])

    def test_handles_an_empty_collection_which_is_a_fresh_account(self):
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        result = rest.paged_result(a_context(), "user/repos", transport=transport)
        self.assertEqual([], result.item)
        self.assertEqual(1, result.page_count)


class TheAccountListingComesFromTheAuthenticatedEndpoint(unittest.TestCase):
    def test_reads_user_repos_and_never_the_public_endpoint(self):
        # The public endpoint omits every private repository, so an inventory built on
        # it reports itself complete while being short. ADR 0005.
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        rest.owned_repositories(a_context(), transport=transport)
        self.assertEqual("user/repos", transport.calls[0]["path"])
        self.assertNotIn("users/", transport.calls[0]["path"])

    def test_asks_for_the_repositories_the_account_owns_and_not_the_ones_it_reaches(self):
        # The default affiliation also brings in repositories the account collaborates
        # on or reaches through an organization, which are not the account's to
        # configure.
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        rest.owned_repositories(a_context(), transport=transport)
        self.assertEqual("owner", transport.calls[0]["query"]["affiliation"])

    def test_hands_the_status_guidance_to_the_transport_as_data(self):
        # The mechanism that lets http.py stay free of GitHub knowledge while a 403
        # still says something useful.
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        rest.owned_repositories(a_context(), transport=transport)
        self.assertIn(404, transport.calls[0]["status_message"])

    def test_sends_no_method_other_than_get(self):
        # The negative assertion that matters. Nothing in this module may reach the
        # transport asking for anything but a read.
        transport = _FakeTransport([([], fixture_text("link-header-last-page.txt"))])
        rest.owned_repositories(a_context(), transport=transport)
        for call in transport.calls:
            self.assertNotIn("method", call)
            self.assertNotIn("data", call)


class TheContextIsResolvedOnceAndNeverEchoesTheToken(unittest.TestCase):
    def setUp(self):
        import os

        self.original = dict(os.environ)
        self.addCleanup(self._restore)
        os.environ["EXAMPLE_API_URL"] = "https://api.example.com"
        os.environ["EXAMPLE_OWNER"] = "EXAMPLE-owner"
        os.environ["EXAMPLE_TOKEN"] = "EXAMPLE-token"
        self.context = {
            "github": {
                "apiBaseUrlEnv": "EXAMPLE_API_URL",
                "ownerEnv": "EXAMPLE_OWNER",
                "readTokenEnv": "EXAMPLE_TOKEN",
                "apiVersion": "2022-11-28",
            },
            "defaults": {
                "requestTimeoutSeconds": 60,
                "maximumRetryCount": 3,
                "retryAfterCapSeconds": 120,
                "pageSize": 100,
                "maximumPageCount": 50,
            },
        }

    def _restore(self):
        import os

        os.environ.clear()
        os.environ.update(self.original)

    def test_stores_the_token_only_as_a_header_value(self):
        # Nothing downstream can log "the token" without also having decided to log a
        # header.
        context = rest.new_context(self.context)
        self.assertEqual("Bearer EXAMPLE-token", context.headers["Authorization"])
        self.assertNotIn("EXAMPLE-token", str(context.owner))
        self.assertNotIn("token", [field.lower() for field in vars(context)])

    def test_sends_a_user_agent_because_github_rejects_a_request_without_one(self):
        # The 403 explains itself in the body, which a status-code-only path never
        # reads.
        self.assertEqual("GitHub_AsCode", rest.new_context(self.context).headers["User-Agent"])

    def test_rejects_an_owner_that_is_not_a_login_without_echoing_it(self):
        import os

        pasted = ("gh" + "p_") + ("EXAMPLE" * 5)
        os.environ["EXAMPLE_OWNER"] = pasted
        with self.assertRaises(http.ConfigurationError) as caught:
            rest.new_context(self.context)
        self.assertNotIn(pasted, str(caught.exception))
        self.assertIn(f"{len(pasted)} characters", str(caught.exception))

    def test_defaults_to_the_read_token_so_a_writer_has_to_ask(self):
        self.assertEqual("EXAMPLE_TOKEN", rest.new_context(self.context).token_environment_name)


if __name__ == "__main__":
    unittest.main()
