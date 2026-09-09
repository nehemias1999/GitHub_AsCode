"""The transport, ported case by case from the Pester suites that cover it.

Every case is named after the failure it prevents, and every one of them exists
because the PowerShell version was written or fixed in response to something real.
Porting the code without porting these would move the behaviour and leave the reasons
behind - and a ported justification stops being evidence, which is the pattern AGENTS.md
names in section 7.

The redirect refusal has a file of its own, because proving it needs two real servers
rather than an assertion about a value.
"""

import unittest

from github_as_code import http

# The prefix is assembled rather than written, exactly as the PowerShell suite does it:
# a literal `github_pat_...` in a committed file is what the sensitive data gate looks
# for, and a test fixture that trips the secret scanner teaches people to ignore the
# scanner. The value is invented and matches no real token.
FINE_GRAINED = ("github" + "_pat_") + ("EXAMPLE" * 6)
CLASSIC = ("gh" + "p_") + ("EXAMPLE" * 5)


class TheRetryPolicyIsDecidedOffline(unittest.TestCase):
    def test_stops_at_the_attempt_limit_instead_of_retrying_forever(self):
        # The bound that turns a transient outage into a finite run. Without it a 503
        # loop is indistinguishable from a hang.
        decision = http.retry_decision(503, attempt=3, maximum_retry_count=3)
        self.assertFalse(decision.should_retry)
        self.assertEqual(0, decision.delay_seconds)

    def test_does_not_retry_a_status_the_server_will_answer_the_same_way_twice(self):
        # A 401 or a 404 is not going to change on its own. Retrying one wastes the
        # rate limit budget and delays the real error by the backoff.
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                decision = http.retry_decision(status, attempt=1, maximum_retry_count=3)
                self.assertFalse(decision.should_retry)

    def test_retries_a_transient_status_and_a_failure_with_no_status_at_all(self):
        # Status 0 is the shape of a DNS failure or a dropped connection, which is the
        # most retryable thing there is and the easiest to forget.
        for status in (0, 408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                decision = http.retry_decision(status, attempt=1, maximum_retry_count=3)
                self.assertTrue(decision.should_retry)

    def test_honours_retry_after_rather_than_its_own_backoff(self):
        # GitHub sends Retry-After on a secondary rate limit, and it is the only number
        # that knows how long the block lasts. Backing off less than it says escalates
        # the block.
        decision = http.retry_decision(429, attempt=1, maximum_retry_count=5, retry_after=47)
        self.assertEqual(47, decision.delay_seconds)

    def test_caps_an_absurd_retry_after_instead_of_sleeping_for_it(self):
        # A proxy can send a Retry-After measured in hours. Obeying it turns a
        # scheduled run into a hung process holding a credential in memory.
        decision = http.retry_decision(
            429, attempt=1, maximum_retry_count=5, retry_after=86400, retry_after_cap_seconds=120
        )
        self.assertEqual(120, decision.delay_seconds)

    def test_backs_off_exponentially_when_the_server_says_nothing(self):
        delays = [
            http.retry_decision(503, attempt=n, maximum_retry_count=5).delay_seconds
            for n in (1, 2, 3)
        ]
        self.assertLess(delays[0], delays[1])
        self.assertLess(delays[1], delays[2])

    def test_caps_its_own_backoff_too_so_a_high_limit_cannot_produce_a_long_sleep(self):
        decision = http.retry_decision(
            503, attempt=20, maximum_retry_count=99, retry_after_cap_seconds=120
        )
        self.assertLessEqual(decision.delay_seconds, 120)

    def test_agrees_with_the_list_it_exports_for_tests_to_check(self):
        for status in http.retryable_status_codes():
            with self.subTest(status=status):
                self.assertTrue(
                    http.retry_decision(status, attempt=1, maximum_retry_count=3).should_retry
                )


class RetryAfterIsReadWithoutALocale(unittest.TestCase):
    def test_reads_a_count_of_seconds(self):
        self.assertEqual(47, http.retry_after_seconds({"Retry-After": "47"}))

    def test_reads_an_http_date_rather_than_dropping_the_backoff(self):
        # The PowerShell version had to pin the parse to the invariant culture: an
        # HTTP-date is English by specification, and under another culture the parse
        # failed silently - retrying immediately against a server that had just said it
        # was overloaded. email.utils reads the grammar, so a date is a date here.
        future = {"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}
        self.assertGreater(http.retry_after_seconds(future), 0)

    def test_reports_a_past_date_as_zero_rather_than_as_a_negative_wait(self):
        past = {"Retry-After": "Wed, 21 Oct 1998 07:28:00 GMT"}
        self.assertEqual(0, http.retry_after_seconds(past))

    def test_treats_an_unparsable_value_as_absent_rather_than_failing(self):
        self.assertEqual(0, http.retry_after_seconds({"Retry-After": "soon"}))
        self.assertEqual(0, http.retry_after_seconds(None))


class TheBaseUrlIsCheckedWithoutEchoingIt(unittest.TestCase):
    def test_does_not_echo_the_value_when_it_is_not_a_url(self):
        # .env holds the API URL, the owner and the token within a few lines of each
        # other. A token is not an absolute URL, so it lands in this branch - and the
        # exception is not masked on its way out, so in a workflow the value would go
        # to the run log.
        with self.assertRaises(http.ConfigurationError) as caught:
            http.assert_base_url(FINE_GRAINED, "GITHUB_API_URL")
        self.assertNotIn(FINE_GRAINED, str(caught.exception))

    def test_still_says_enough_to_identify_the_mistake(self):
        # Not echoing must not mean not diagnosing.
        with self.assertRaises(http.ConfigurationError) as caught:
            http.assert_base_url(FINE_GRAINED, "GITHUB_API_URL")
        message = str(caught.exception)
        self.assertIn("GITHUB_API_URL", message)
        self.assertIn(f"{len(FINE_GRAINED)} characters", message)
        self.assertIn("does not contain", message)

    def test_does_not_echo_the_value_when_it_has_a_scheme_and_is_malformed(self):
        # urlsplit is more permissive than Uri.TryCreate, so this input reaches the
        # userinfo branch here where PowerShell reached the parse branch. Which branch
        # answers is not the property worth pinning; that neither one echoes is.
        with self.assertRaises(http.ConfigurationError) as caught:
            http.assert_base_url(f"https://{CLASSIC}@ api.example.com", "GITHUB_API_URL")
        self.assertNotIn(CLASSIC, str(caught.exception))

    def test_rejects_a_credential_in_the_url_without_echoing_it(self):
        with self.assertRaises(http.ConfigurationError) as caught:
            http.assert_base_url(f"https://someone:{CLASSIC}@api.example.com", "GITHUB_API_URL")
        message = str(caught.exception)
        self.assertIn("carries credentials in the URL", message)
        self.assertNotIn(CLASSIC, message)

    def test_accepts_a_normal_url_and_normalises_the_trailing_slash(self):
        self.assertEqual(
            "https://api.example.com",
            http.assert_base_url("https://api.example.com/", "GITHUB_API_URL"),
        )

    def test_rejects_a_scheme_that_is_not_http_or_https(self):
        with self.assertRaises(http.ConfigurationError) as caught:
            http.assert_base_url("ftp://api.example.com", "GITHUB_API_URL")
        self.assertIn("Only http and https", str(caught.exception))

    def test_rejects_a_query_or_a_fragment_that_would_land_mid_path(self):
        for url in ("https://api.example.com?page=1", "https://api.example.com#top"):
            with self.subTest(url=url), self.assertRaises(http.ConfigurationError):
                http.assert_base_url(url, "GITHUB_API_URL")

    def test_names_the_variable_when_the_value_is_missing(self):
        for value in (None, "", "   "):
            with self.subTest(value=value), self.assertRaises(http.ConfigurationError) as caught:
                http.assert_base_url(value, "GITHUB_API_URL")
            self.assertIn("GITHUB_API_URL", str(caught.exception))


class TheAuthorizationHeaderIsBuiltInOnePlace(unittest.TestCase):
    def test_never_puts_the_token_in_its_own_error_messages(self):
        with self.assertRaises(http.ConfigurationError) as caught:
            http.bearer_authorization_header(f"{CLASSIC} {CLASSIC}")
        self.assertNotIn(CLASSIC, str(caught.exception))

    def test_rejects_a_token_containing_whitespace_pasted_across_a_line_break(self):
        with self.assertRaises(http.ConfigurationError):
            http.bearer_authorization_header("EXAMPLE-to\nken")

    def test_trims_a_trailing_newline_rather_than_sending_an_unusable_header(self):
        # A header value containing a newline throws from the header collection rather
        # than failing as authentication, which sends the reader to the wrong place.
        self.assertEqual(
            "Bearer EXAMPLE-token", http.bearer_authorization_header("EXAMPLE-token\n")
        )

    def test_refuses_an_empty_token_instead_of_sending_the_word_bearer(self):
        with self.assertRaises(http.ConfigurationError):
            http.bearer_authorization_header("   ")

    def test_encodes_a_basic_credential_as_utf8_not_ascii(self):
        # A user name is allowed a non-ASCII character, and ASCII encoding turns it
        # into a question mark - a 401 that reads like a wrong token.
        import base64

        header = http.basic_authorization_header("us\u00e9r", "EXAMPLE-token")
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
        self.assertEqual("us\u00e9r:EXAMPLE-token", decoded)


class TheUrlBuilderProducesOneUrlPerRequest(unittest.TestCase):
    def test_escapes_a_query_value_so_no_caller_has_to_remember_to(self):
        uri = http.build_uri("https://example.com", "search", {"q": "a b&c"})
        self.assertEqual("https://example.com/search?q=a%20b%26c", uri)

    def test_sorts_the_query_so_the_same_request_is_the_same_url_twice(self):
        first = http.build_uri("https://example.com", "x", {"b": 2, "a": 1})
        second = http.build_uri("https://example.com", "x", {"a": 1, "b": 2})
        self.assertEqual(first, second)

    def test_omits_a_none_value_rather_than_sending_it_empty(self):
        self.assertEqual(
            "https://example.com/x?a=1",
            http.build_uri("https://example.com", "x", {"a": 1, "b": None}),
        )

    def test_does_not_double_the_slash_between_base_and_path(self):
        self.assertEqual(
            "https://example.com/user/repos",
            http.build_uri("https://example.com/", "/user/repos/"),
        )

    def test_leaves_the_base_alone_when_the_path_is_empty(self):
        self.assertEqual("https://example.com", http.build_uri("https://example.com", ""))


class TheLinkHeaderIsReadRatherThanGuessed(unittest.TestCase):
    def test_finds_the_next_page_when_the_header_carries_more_than_one_link(self):
        # The regression: the comma separating two links leaked into the rel value, so
        # the parser reported "no next page" and truncated the collection to page one.
        header = (
            '<https://example.com/user/repos?page=2>; rel="next", '
            '<https://example.com/user/repos?page=9>; rel="last"'
        )
        self.assertEqual(
            "https://example.com/user/repos?page=2", http.link_header_target(header, "next")
        )

    def test_does_not_truncate_a_url_that_contains_a_comma_in_a_query_value(self):
        header = '<https://example.com/x?fields=a,b,c&page=2>; rel="next"'
        self.assertEqual(
            "https://example.com/x?fields=a,b,c&page=2", http.link_header_target(header, "next")
        )

    def test_finds_a_relation_that_is_not_the_first_one_in_the_header(self):
        header = '<https://example.com/1>; rel="first", <https://example.com/9>; rel="last"'
        self.assertEqual("https://example.com/9", http.link_header_target(header, "last"))

    def test_returns_nothing_for_a_relation_the_header_does_not_offer(self):
        header = '<https://example.com/9>; rel="last"'
        self.assertIsNone(http.link_header_target(header, "next"))

    def test_treats_an_absent_header_as_no_next_page_rather_than_an_error(self):
        self.assertIsNone(http.link_header_target(None, "next"))
        self.assertIsNone(http.link_header_target("", "next"))

    def test_accepts_a_bare_rel_value_and_one_carrying_several_relations(self):
        self.assertEqual(
            "https://example.com/2",
            http.link_header_target("<https://example.com/2>; rel=next", "next"),
        )
        self.assertEqual(
            "https://example.com/9",
            http.link_header_target('<https://example.com/9>; rel="next last"', "last"),
        )


class AHeaderIsReadWhateverShapeItArrivesIn(unittest.TestCase):
    def test_matches_case_insensitively_because_a_server_chooses_the_casing(self):
        self.assertEqual("a", http.response_header({"X-Thing": "a"}, "x-thing"))

    def test_joins_a_header_that_appears_more_than_once(self):
        from email.message import Message

        message = Message()
        message["Warning"] = "one"
        message["Warning"] = "two"
        self.assertEqual("one, two", http.response_header(message, "warning"))

    def test_reports_an_absent_header_as_empty_rather_than_failing(self):
        self.assertEqual("", http.response_header({}, "link"))
        self.assertEqual("", http.response_header(None, "link"))


class ANonJsonBodyIsExplained(unittest.TestCase):
    def test_names_the_two_configuration_causes_when_the_body_is_markup(self):
        # The status code is 200 and the body is a login page or a UI page, so nothing
        # upstream notices. A raw json.loads failure names a line in the transport.
        with self.assertRaises(http.TransportError) as caught:
            http.parse_json_response("<!DOCTYPE html><html></html>", "https://example.com/user")
        message = str(caught.exception)
        self.assertIn("HTML or XML", message)
        self.assertIn("base URL", message)

    def test_says_a_200_with_no_body_usually_means_a_proxy_answered(self):
        with self.assertRaises(http.TransportError) as caught:
            http.parse_json_response("   ", "https://example.com/user")
        self.assertIn("empty body", str(caught.exception))

    def test_parses_an_ordinary_body(self):
        self.assertEqual(
            {"login": "EXAMPLE-owner"},
            http.parse_json_response('{"login": "EXAMPLE-owner"}', "https://example.com/user"),
        )


if __name__ == "__main__":
    unittest.main()
