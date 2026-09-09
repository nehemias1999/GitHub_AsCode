"""Read-only HTTP, shared by every transport.

The port of GitHubAsCode.Http.psm1. It exists because there are two API surfaces
here, GitHub's REST v3 and its GraphQL v4, and a retry policy implemented twice is a
retry policy that drifts. It knows what a request, a retry and a credential are, and
nothing about either surface: no URL, no endpoint, no permission name appears in it.

READ ONLY BY CONSTRUCTION. There is no method parameter and no code path that sends
anything but GET, so this module cannot write at all, and the absence tests in
tests/python/test_write_boundary.py assert exactly that.

Widening this is the single edit that turns GitHub_AsCode into a tool that can damage
the account, so it is an ADR and not an edit. See docs/adr/0001-write-boundary.md.

Because every request is a GET, and a GET is idempotent, retrying is always safe. A
module that also wrote could not use this policy: a POST retried after the server had
already committed manufactures a duplicate.

Two things about the Python transport that have no PowerShell counterpart, and both
are in docs/adr/0006-python-and-the-standard-library.md because a naive port gets them
wrong with no error and no log line:

**urllib follows redirects by default, and re-sends the headers set on the request** -
including Authorization. That inverts the most carefully reasoned security decision in
the PowerShell module, which set MaximumRedirection = 0 for precisely this reason. The
opener below refuses redirects, and tests/python/test_redirect_refusal.py proves with
two real servers that the token never reaches the second host.

**Request(data=...) silently promotes a GET to a POST.** That is the Python write
vector. PowerShell had exactly one way in, `-Method`, and the guard set was shaped
around it; the guards here are shaped around this one instead.
"""

import base64
import datetime
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

# The statuses worth trying again. Exposed as a function below so the suite asserts
# against the same list the module enforces rather than restating it and drifting.
_RETRYABLE_STATUS_CODE = (408, 429, 500, 502, 503, 504)

# Anything at or above this is not a transport failure to retry but an answer.
_REDIRECT_RANGE = range(300, 400)


class TransportError(Exception):
    """A request could not be completed, or answered with something unusable."""


class ConfigurationError(TransportError):
    """The operator's configuration is wrong, and the message says what to fix.

    A subclass of TransportError so a caller that wants to report "the request did not
    work" catches one type, while a caller that wants to separate "fix your .env" from
    "the server is down" can still tell them apart.
    """


@dataclass(frozen=True)
class Response:
    """One answered request: the decoded body, the headers, and the status."""

    content: str
    headers: Mapping[str, str] | Any
    status_code: int


def _warn(message: str) -> None:
    """Write an operational warning where the operator will see it.

    stderr rather than the warnings module, which deduplicates, can be filtered to
    silence, and is aimed at developers. This is the port of Write-Warning, and the
    thing it warns about - a token travelling unencrypted - must not be the line that
    gets swallowed.
    """
    print(f"WARNING: {message}", file=sys.stderr)


def assert_base_url(url: str | None, variable_name: str) -> str:
    """Validate and normalise a base URL.

    Pure function. Returns the URL with any trailing slash removed, so every caller
    concatenates against the same shape. A URL carrying a query or a fragment is
    refused: a base URL with a query string breaks every URL derived from it, because
    the query lands in the middle of the path and nothing complains.

    Args:
        url: Candidate base URL.
        variable_name: Environment variable the value came from, named in any failure
            so the message points at what to fix.

    Returns:
        The normalised base URL.

    Raises:
        ConfigurationError: The value is empty, is not an absolute http(s) URL, or
            carries userinfo, a query or a fragment.

    Example:
        >>> assert_base_url("https://example.com/", "GITHUB_API_URL")
        'https://example.com'
    """
    if url is None or not url.strip():
        raise ConfigurationError(
            f"{variable_name} is empty. Set it in .env to an absolute URL, "
            "for example https://example.com."
        )

    trimmed = url.strip().rstrip("/")

    try:
        parsed = urllib.parse.urlsplit(trimmed)
    except ValueError:
        parsed = None

    if parsed is None or not parsed.scheme or not parsed.netloc:
        # The value is DESCRIBED, not echoed, and that is the whole point.
        #
        # This is the branch a pasted credential reaches: a token is not an absolute
        # URL, so it lands here, and .env holds the API URL, the owner and the token
        # within a few lines of each other. The PowerShell version had this same fix
        # applied after the message was found printing a token in full.
        #
        # Length and the presence of a scheme separator are enough to tell a typo'd
        # URL from something that is not a URL at all, which is what the reader needs.
        shape = (
            "contains '://' but could not be parsed"
            if "://" in trimmed
            else "does not contain '://'"
        )
        raise ConfigurationError(
            f"{variable_name} is not an absolute URL: {len(trimmed)} characters, and it "
            f"{shape}. The value is not shown here, because a value in the wrong line of "
            ".env is usually a credential. Set it to an absolute URL, for example "
            "https://api.github.com."
        )

    if parsed.scheme not in ("http", "https"):
        raise ConfigurationError(
            f"{variable_name} uses scheme '{parsed.scheme}'. Only http and https are supported."
        )

    # http is accepted and announced, not accepted silently. Every request carries the
    # token in an Authorization header, in the clear - Bearer neither encodes nor
    # encrypts anything - so on plain http the token is readable by anything on the
    # path. It stays allowed because an API endpoint on a private network without a
    # certificate is a real situation, and refusing it outright would push people
    # towards disabling TLS checks instead, which is worse.
    if parsed.scheme == "http":
        _warn(
            f"{variable_name} uses http, so the API token travels unencrypted on every "
            "request. Use https unless this is a network you control end to end."
        )

    # Credentials in the base URL, rejected rather than carried. The header is already
    # how this authenticates, so userinfo adds nothing - and the base URL is
    # interpolated into error messages and written to detail.apiBaseUrl in every
    # report. One misconfigured .env would copy a credential into every artefact the
    # tool writes. This message does not echo the URL back.
    if "@" in parsed.netloc:
        raise ConfigurationError(
            f"{variable_name} carries credentials in the URL (a user[:password]@ before the "
            "host). Remove them: authentication uses the token from the environment, and a "
            "URL with userinfo would be copied into reports and error messages."
        )
    if parsed.query:
        raise ConfigurationError(
            f"{variable_name} carries a query string. Remove it: the query of a derived URL "
            "would end up in the middle of the path."
        )
    if parsed.fragment:
        raise ConfigurationError(f"{variable_name} carries a fragment. Remove it.")

    return trimmed


def build_uri(base_url: str, path: str, query: Mapping[str, Any] | None = None) -> str:
    """Build an absolute URL from a base URL, a path and an optional query.

    Pure function. Query values are escaped here, so no caller has to remember to
    escape one.

    Args:
        base_url: Normalised base URL.
        path: Path with no leading slash.
        query: Optional query values. A None value is omitted rather than sent empty.

    Returns:
        The absolute URL.

    Example:
        >>> build_uri("https://example.com", "user/repos", {"per_page": 100})
        'https://example.com/user/repos?per_page=100'
    """
    uri = base_url.rstrip("/")
    clean_path = path.strip("/")
    if clean_path:
        uri += "/" + clean_path

    if query:
        # Sorted, because dictionary order is insertion order and a caller building the
        # same request two ways would produce two URLs. Query order does not change what
        # a GET means, so nothing is broken today - it stops a log line or a cache key
        # from disagreeing about identical requests later.
        pairs = [
            "{}={}".format(
                urllib.parse.quote(str(key), safe=""),
                urllib.parse.quote(str(query[key]), safe=""),
            )
            for key in sorted(query)
            if query[key] is not None
        ]
        if pairs:
            uri += "?" + "&".join(pairs)

    return uri


def bearer_authorization_header(secret: str) -> str:
    """Build a Bearer Authorization header value.

    A token is sent as-is, with no encoding step. That is the whole function, and it
    exists anyway for one reason: so that no transport module builds the string
    itself. A caller writing f"Bearer {token}" by hand is a caller that can write
    "Bearer  {token}", and the resulting 401 reads like a revoked token.

    The token is trimmed, because a value pasted into a .env file arrives with a
    trailing newline more often than not.

    Args:
        secret: The personal access token.

    Returns:
        The header value, beginning with 'Bearer '.

    Raises:
        ConfigurationError: The token is empty or contains whitespace.

    Example:
        >>> bearer_authorization_header("EXAMPLE-token")
        'Bearer EXAMPLE-token'
    """
    trimmed = secret.strip()
    if not trimmed:
        raise ConfigurationError(
            "The token is empty. The configuration names the environment variable that "
            "should hold it; check that the variable is set in .env and that the "
            "bootstrap loaded it."
        )
    if any(character.isspace() for character in trimmed):
        raise ConfigurationError(
            "The token contains whitespace, which cannot be sent in a header. This is "
            "usually a value that was pasted across a line break."
        )

    return "Bearer " + trimmed


def basic_authorization_header(user_name: str, secret: str) -> str:
    """Build a Basic Authorization header value.

    UTF-8 rather than ASCII. A user name or an email address is allowed to contain a
    non-ASCII character, and ASCII encoding turns it into a question mark - producing
    a 401 that reads like a wrong token and sends the reader looking in the wrong
    place.

    Args:
        user_name: User name or email address.
        secret: API token or password.

    Returns:
        The header value, beginning with 'Basic '.

    Example:
        >>> basic_authorization_header("EXAMPLE-owner", "EXAMPLE-token")
        'Basic RVhBTVBMRS1vd25lcjpFWEFNUExFLXRva2Vu'
    """
    encoded = base64.b64encode(f"{user_name}:{secret}".encode()).decode("ascii")
    return "Basic " + encoded


def response_header(headers: Any, name: str) -> str:
    """Read one response header, whatever shape the caller is holding.

    urllib hands back an email.message.Message, a test hands back a dict, and a header
    may legitimately appear more than once. Code that assumes one of those shapes
    works in one place and returns something useless in the other.

    Args:
        headers: Response headers, or None.
        name: Header name, matched case-insensitively.

    Returns:
        The header value, several occurrences joined with ', ', or an empty string
        when absent.

    Example:
        >>> response_header({"Link": "<https://example.com>; rel=\\"next\\""}, "link")
        '<https://example.com>; rel="next"'
    """
    if headers is None:
        return ""

    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        return ", ".join(str(value) for value in values) if values else ""

    for key in headers:
        if str(key).lower() == name.lower():
            value = headers[key]
            if isinstance(value, str):
                return value
            if isinstance(value, Iterable):
                return ", ".join(str(item) for item in value)
            return str(value)

    return ""


def retry_after_seconds(headers: Any) -> int:
    """Read Retry-After from a failed response, in seconds.

    Retry-After is either a count of seconds or an HTTP-date, and both shapes appear
    in practice behind a reverse proxy.

    The PowerShell version pinned both parses to the invariant culture, because an
    HTTP-date is English by specification and parsing it under, say, a Spanish culture
    fails silently - dropping the backoff the server asked for and retrying
    immediately against a server that just said it was overloaded. email.utils reads
    the grammar rather than a locale, so that hazard does not survive the port. The
    reason is kept here because the ledger of why each guard exists is worth more than
    the guard.

    Args:
        headers: Response headers from the failure, or None.

    Returns:
        Seconds to wait, or 0 when absent or unparsable.

    Example:
        >>> retry_after_seconds({"Retry-After": "47"})
        47
    """
    raw = response_header(headers, "Retry-After").strip()
    if not raw:
        return 0

    try:
        return max(0, int(raw))
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return 0
    if when is None:
        return 0

    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    delta = (when - datetime.datetime.now(datetime.UTC)).total_seconds()
    return max(0, int(delta))


@dataclass(frozen=True)
class RetryDecision:
    """Whether to try again, and how long to wait first."""

    should_retry: bool
    delay_seconds: int


def retry_decision(
    status_code: int,
    attempt: int,
    maximum_retry_count: int,
    retry_after: int = 0,
    retry_after_cap_seconds: int = 120,
) -> RetryDecision:
    """Decide whether a failed request should be retried, and after how long.

    Pure function, so the retry policy is testable offline instead of only observable
    during an outage.

    Two rules are worth stating because the obvious implementation gets them
    backwards:

    A failure carrying NO status code - a DNS failure, a TLS reset, a timeout - is the
    most transient failure there is, and is retried. Classifying it as non-retryable
    because there is no code to match against is a common mistake, and it makes the
    tool fail on exactly the conditions retry exists for.

    An honoured Retry-After is capped. A service or proxy answering
    'Retry-After: 999999' would otherwise park the run in sleep() for days.

    Args:
        status_code: HTTP status code, or 0 when the failure carried none.
        attempt: 1-based number of the attempt that just failed.
        maximum_retry_count: Total attempts allowed.
        retry_after: Value of the Retry-After header, or 0 when absent.
        retry_after_cap_seconds: Upper bound applied to both the honoured Retry-After
            and this module's own backoff.

    Returns:
        A RetryDecision.

    Example:
        >>> retry_decision(503, attempt=1, maximum_retry_count=3).should_retry
        True
    """
    retryable = status_code == 0 or status_code in _RETRYABLE_STATUS_CODE

    if attempt >= maximum_retry_count or not retryable:
        return RetryDecision(should_retry=False, delay_seconds=0)

    if retry_after > 0:
        delay = min(retry_after, retry_after_cap_seconds)
    else:
        delay = min(2**attempt, retry_after_cap_seconds)

    return RetryDecision(should_retry=True, delay_seconds=int(delay))


def retryable_status_codes() -> tuple[int, ...]:
    """The status codes this module retries.

    Exported so the test suite asserts against the same list the module enforces,
    rather than restating it and drifting from it.

    Returns:
        The status codes.

    Example:
        >>> 503 in retryable_status_codes()
        True
    """
    return _RETRYABLE_STATUS_CODE


def parse_json_response(content: str, uri: str) -> Any:
    """Parse a response body as JSON, and explain a non-JSON body rather than failing
    on it obscurely.

    A raw json.loads failure reads "Expecting value: line 1 column 1" and names a line
    inside a transport module, which tells the reader nothing about the cause.

    The cause is nearly always one of two things, and both are configuration. The base
    URL points at a web UI rather than at the API root - a URL copied out of a browser
    address bar carries a UI path - and every request built from it lands on an HTML
    page that answers 200. Or a reverse proxy or SSO gateway is answering with a login
    page instead of passing the request through, which also answers 200 with HTML.

    Either way the status code is fine and the body is a document, so nothing upstream
    notices. This function names both possibilities.

    Args:
        content: The response body.
        uri: The URL that was requested, quoted in any failure.

    Returns:
        The parsed object.

    Raises:
        TransportError: The body is empty or is not JSON.

    Example:
        >>> parse_json_response('{"login": "EXAMPLE-owner"}', "https://example.com/user")
        {'login': 'EXAMPLE-owner'}
    """
    if not content or not content.strip():
        raise TransportError(
            f"GET {uri} answered with an empty body where JSON was expected. A 200 with no "
            "body usually means a proxy in front of the service handled the request itself."
        )

    looks_like_markup = content.lstrip().startswith("<")

    try:
        return json.loads(content)
    except ValueError as error:
        if looks_like_markup:
            raise TransportError(
                f"GET {uri} answered with HTML or XML where JSON was expected. Two usual "
                "causes: the configured base URL points at a web UI rather than the API root "
                "- a URL copied from a browser address bar carries a UI path - or a proxy or "
                "SSO gateway returned a login page. Check the base URL in .env and remove any "
                "path that belongs to the UI."
            ) from error
        raise TransportError(f"GET {uri} answered with a body that is not JSON: {error}") from error


def link_header_target(link_header: str | None, relation: str) -> str | None:
    """Return the URL for one relation from an RFC 5988 Link header.

    Pagination is the one place where guessing quietly produces a wrong answer instead
    of an error. A caller that builds ?page=N itself and stops when a page comes back
    short will, if the collection changes underneath it, skip items and report the
    truncated list as complete. So the next page is never computed: it is read from
    the Link header, and when the header stops offering one, the collection is
    finished.

    The parser is deliberately literal about the grammar. The URL is taken from
    between the angle brackets and NOT unescaped - it is already a valid absolute URL
    and carries an opaque cursor in some APIs, which unescaping would corrupt. A comma
    may appear inside the URL, in a query value, so segmentation is driven by the
    angle brackets rather than by splitting on ','. rel values may be quoted or bare,
    and one link may carry several: rel="next last".

    Args:
        link_header: The raw Link header value. Empty or absent is not an error: it
            means there is no next page.
        relation: The relation to look for, such as 'next', 'last', 'prev' or 'first'.

    Returns:
        The absolute URL, or None when the header offers no such relation.

    Example:
        >>> link_header_target('<https://example.com/x?page=2>; rel="next"', "next")
        'https://example.com/x?page=2'
    """
    if not link_header:
        return None

    for match in re.finditer(r"<(?P<target>[^>]*)>(?P<parameters>[^<]*)", link_header):
        target = match.group("target").strip()
        if not target:
            continue

        for parameter in match.group("parameters").split(";"):
            parameter = parameter.strip()
            if not parameter:
                continue

            separator = parameter.find("=")
            if separator < 1:
                continue

            name = parameter[:separator].strip().strip(",").strip()
            if name != "rel":
                continue

            # The comma separating one link from the next lands at the END of this
            # segment, because segmentation is driven by the angle brackets and a
            # link's parameters run up to the following '<'. So it is stripped BEFORE
            # the quotes: trimming quotes first leaves rel="next", as 'next",' - a
            # value that matches no relation, which is how a Link header with more
            # than one link silently reported "no next page" and truncated a
            # collection to its first page.
            value = parameter[separator + 1 :].strip()
            value = value.rstrip(",").strip()
            value = value.strip('"').strip("'")

            if relation in value.split():
                return target

    return None


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect handler that refuses.

    urllib follows redirects by default and re-sends the headers set on the request,
    Authorization included. Sending a Bearer token to whatever host a 30x names is
    credential disclosure to a third party, and it happens with no error and no log
    line - which is what makes it worse than a failure.

    Returning None from redirect_request leaves the 3xx unhandled, so urllib raises it
    as an HTTPError and the caller below turns it into the configuration problem it
    almost always is.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    """An opener that refuses redirects and will not negotiate below TLS 1.2.

    The TLS floor is not left to the machine: a Bearer token travels on every request,
    and the PowerShell module set the same floor for the same reason. build_opener
    replaces the default handler of a class with the instance given, which is how the
    refusing redirect handler displaces the following one.
    """
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(
        _RefuseRedirect(), urllib.request.HTTPSHandler(context=context)
    )


def read_only_request(
    base_url: str,
    path: str,
    headers: Mapping[str, str],
    query: Mapping[str, Any] | None = None,
    timeout_seconds: int = 60,
    maximum_retry_count: int = 3,
    retry_after_cap_seconds: int = 120,
    status_message: Mapping[int, str] | None = None,
    allow_not_found: bool = False,
    opener: urllib.request.OpenerDirector | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Response | None:
    """Send one authenticated GET and return body, headers and status.

    The only function in this repository that performs network I/O. It sends GET and
    nothing else: no parameter exists that could make it write, and no request built
    here is given a body - which is the only way urllib would turn a GET into a POST.

    The body is decoded from the raw bytes as UTF-8 rather than trusting the response
    charset, and a leading byte order mark is dropped.

    Args:
        base_url: Normalised base URL.
        path: Path with no leading slash.
        headers: Request headers, including Authorization.
        query: Optional query values.
        timeout_seconds: Per-attempt timeout.
        maximum_retry_count: Total attempts allowed.
        retry_after_cap_seconds: Upper bound on an honoured Retry-After.
        status_message: Status code to guidance, supplied by the calling transport. It
            is data, so this module stays free of any knowledge about GitHub while a
            403 can still name the fine-grained permission that is missing.
        allow_not_found: Return None instead of raising on 404, where absence is
            itself an answer.
        opener: Injected for tests. Defaults to the redirect-refusing opener.
        sleep: Injected for tests, so the retry policy can be exercised without
            actually waiting.

    Returns:
        A Response, or None on an allowed 404.

    Raises:
        TransportError: The request was redirected, failed after its last attempt, or
            answered with a status the caller supplied a message for.

    Example:
        >>> read_only_request(  # doctest: +SKIP
        ...     "https://example.com", "user", {"Authorization": "Bearer EXAMPLE-token"}
        ... )
    """
    uri = build_uri(base_url, path, query)
    director = opener if opener is not None else _build_opener()

    attempt = 0
    while True:
        attempt += 1
        try:
            # No `data` argument, ever. Passing one promotes this to a POST, which is
            # the whole Python write vector, and is why the absence tests read this
            # call out of the parse tree rather than trusting the comment.
            request = urllib.request.Request(uri, headers=dict(headers), method="GET")
            with director.open(request, timeout=timeout_seconds) as response:
                raw = response.read()
                content = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
                return Response(
                    content=content,
                    headers=response.headers,
                    status_code=int(response.status),
                )
        except urllib.error.HTTPError as error:
            status_code = int(error.code)
            failure_headers = error.headers
            message = str(error)
            # An HTTPError is itself an open response object. Left to the garbage
            # collector it emits a ResourceWarning and holds the socket until then,
            # which on a retry loop means several connections open at once against a
            # server that has already said it is struggling. The headers survive the
            # close, and they are all this needs.
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # No HTTP response at all: DNS, TLS, a dropped connection, a timeout.
            # Status 0 is what retry_decision reads as the most transient failure
            # there is.
            status_code = 0
            failure_headers = None
            message = str(error)

        if status_code == 404 and allow_not_found:
            return None

        if status_code in _REDIRECT_RANGE:
            raise TransportError(
                f"GET {uri} was redirected (HTTP {status_code}), and redirects are not "
                "followed because forwarding the Authorization header to another host would "
                "disclose the credential. Check the base URL: http against https, or a "
                "missing or extra path prefix."
            )

        if status_message and status_code in status_message:
            raise TransportError(
                f"GET {uri} failed with HTTP {status_code}. {status_message[status_code]}"
            )

        decision = retry_decision(
            status_code=status_code,
            attempt=attempt,
            maximum_retry_count=maximum_retry_count,
            retry_after=retry_after_seconds(failure_headers),
            retry_after_cap_seconds=retry_after_cap_seconds,
        )

        if not decision.should_retry:
            detail = f"HTTP {status_code}" if status_code > 0 else "no HTTP response"
            raise TransportError(
                f"GET {uri} failed after {attempt} attempt(s) ({detail}): {message}"
            )

        sleep(decision.delay_seconds)
