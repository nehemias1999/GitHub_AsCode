"""What is GitHub-specific about talking to the REST API.

The generic transport half is in `http.py`, which owns `urllib.request` and knows
nothing about GitHub. This module owns the parts that are GitHub rules and would be
wrong to share:

**Pagination.** The next page is read from the `Link` header, never computed. A caller
that builds `?page=N` and stops on a short page will, when the collection changes
underneath it, skip items and report the truncated list as complete.

**The account listing trap.** The endpoint with the account name in its path returns
PUBLIC repositories only. The private ones exist solely behind `GET /user/repos` with
an authenticated token. Every private repository is therefore missing in silence from
an inventory built on the first endpoint - the inventory whose entire job is to be
complete.

**Rate limits, of which there are two.** The primary budget is 5000 requests an hour and
is visible in `x-ratelimit-remaining`. The secondary limits are undocumented ceilings on
bursts and answer 403 or 429 with `retry-after`. Reading those headers is a GitHub rule
and lives here; deciding what to do about the numbers is arithmetic and lives in
`http.py`.

**Token shape.** A classic PAT answers with `x-oauth-scopes`; a fine-grained one does
not. That single header is how this repository can refuse to run a write with a token
whose blast radius includes removing a repository.
"""

import datetime
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from github_as_code import configuration, http

# Status code to guidance, passed to the transport as DATA. It is the mechanism that
# lets http.py stay free of GitHub knowledge while a 403 still says something useful.
STATUS_MESSAGE = {
    401: (
        "The token was rejected. Either it is not set, has been revoked, or has expired. A "
        "fine-grained token expires on a fixed date; inventory reports the days remaining."
    ),
    403: (
        "Forbidden. Three different causes share this code: the token lacks the fine-grained "
        "permission for this endpoint, the primary rate limit is exhausted, or a secondary "
        "rate limit was tripped by a burst. Check x-ratelimit-remaining to tell them apart."
    ),
    404: (
        'Not found - which on GitHub also means "exists, but this token cannot see it". Do '
        "not read this as absence unless absence was confirmed another way."
    ),
    422: (
        "Unprocessable. For a contents write this is the API refusing to overwrite an "
        "existing file, which is the intended outcome here rather than an error."
    ),
    429: "Too many requests. A secondary rate limit; retry-after says how long to wait.",
}

# A GitHub login: 1 to 39 characters, alphanumeric with single internal hyphens.
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$", re.ASCII)


def status_messages() -> dict[int, str]:
    """The status-code-to-guidance map handed to the transport.

    Returns a copy, so a caller cannot edit the shared map.

    Returns:
        A mapping of status code to guidance.

    Example:
        >>> 404 in status_messages()
        True
    """
    return dict(STATUS_MESSAGE)


@dataclass(frozen=True)
class GitHubContext:
    """Everything a run needs to make a request, resolved once."""

    base_url: str
    owner: str
    headers: dict[str, str]
    token_environment_name: str
    timeout_seconds: int = 60
    maximum_retry_count: int = 3
    retry_after_cap_seconds: int = 120
    page_size: int = 100
    maximum_page_count: int = 50


@dataclass(frozen=True)
class RateLimitState:
    """The budget, as the response headers reported it. Any field may be None."""

    limit: int | None
    remaining: int | None
    reset_utc: datetime.datetime | None
    resource: str


@dataclass(frozen=True)
class TokenShape:
    """Which kind of token answered, and when it expires."""

    is_classic: bool
    scope: list[str] = field(default_factory=list)
    expires_utc: datetime.datetime | None = None
    days_until_expiry: int | None = None


@dataclass(frozen=True)
class PagedResult:
    """Every item in a collection, and whether the walk was cut short."""

    item: list[Any]
    page_count: int
    truncated: bool
    rate_limit: RateLimitState | None


@dataclass(frozen=True)
class GitHubResponse:
    """One answered request: the parsed body, plus the headers that are answers."""

    content: Any
    headers: Any
    status_code: int
    rate_limit: RateLimitState
    link: str


def new_context(project_context: dict, token_environment_name: str | None = None) -> GitHubContext:
    """Resolve the API base URL, owner and token into one context object.

    The configuration declares the NAME of every value; this turns those names into
    values, validates them, and returns the single object every request in a run is
    built from.

    The token never appears in the returned object as itself: it is already an
    Authorization header value by the time it is stored, so nothing downstream can log
    "the token" without also having decided to log a header.

    Args:
        project_context: The parsed project context.
        token_environment_name: Which declared token to resolve. Defaults to the read
            token, so an automation has to ask explicitly to hold one that can write.

    Returns:
        A GitHubContext.

    Raises:
        http.ConfigurationError: The owner is not a valid GitHub login.
        configuration.ConfigurationError: A declared variable is unset.
    """
    github = project_context["github"]
    defaults = project_context["defaults"]

    if not token_environment_name:
        token_environment_name = github["readTokenEnv"]

    base_url = http.assert_base_url(
        configuration.required_value(github["apiBaseUrlEnv"]), github["apiBaseUrlEnv"]
    )

    owner = configuration.required_value(github["ownerEnv"])
    if not _OWNER_PATTERN.match(owner):
        # Not echoed, for the same reason assert_base_url stopped echoing. .env holds
        # the owner and the token a few lines apart, and a value that fails this check
        # is by definition not a login - which makes it more likely to be the thing that
        # was pasted by mistake. A login is at most 39 characters, so the length alone
        # usually identifies the error.
        raise http.ConfigurationError(
            f"{github['ownerEnv']} is not a valid GitHub account name: {len(owner)} characters, "
            "which does not match the allowed shape. The value is not shown here, because a "
            "value in the wrong line of .env is usually a credential. Expected the login only - "
            "not a URL, and not owner/repo."
        )

    token = configuration.required_value(token_environment_name)

    headers = {
        "Authorization": http.bearer_authorization_header(token),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": github["apiVersion"],
        # GitHub rejects a request with no User-Agent outright, with a 403 whose body
        # explains it - but only if the body is read, which a status-code-only path
        # does not do.
        "User-Agent": "GitHub_AsCode",
    }

    return GitHubContext(
        base_url=base_url,
        owner=owner,
        headers=headers,
        token_environment_name=token_environment_name,
        timeout_seconds=int(defaults["requestTimeoutSeconds"]),
        maximum_retry_count=int(defaults["maximumRetryCount"]),
        retry_after_cap_seconds=int(defaults["retryAfterCapSeconds"]),
        page_size=int(defaults["pageSize"]),
        maximum_page_count=int(defaults["maximumPageCount"]),
    )


def _int_header(headers: Any, name: str) -> int | None:
    raw = http.response_header(headers, name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def rate_limit_state(headers: Any) -> RateLimitState:
    """Read the rate limit budget out of response headers.

    Pure function over headers, so the whole of rate limit reporting is testable from a
    fixture with no network.

    A response with none of these headers is not an error and not zero: the numbers are
    None, because "unknown" and "none left" must not look alike to a caller deciding
    whether to continue.

    Args:
        headers: Response headers.

    Returns:
        A RateLimitState.

    Example:
        >>> rate_limit_state({"x-ratelimit-remaining": "4999"}).remaining
        4999
    """
    reset_epoch = _int_header(headers, "x-ratelimit-reset")
    reset_utc = None
    if reset_epoch is not None:
        # A UNIX epoch second, not an HTTP date. Treating it as a date silently produces
        # 1970 and a "reset 56 years ago" line in a report.
        reset_utc = datetime.datetime.fromtimestamp(reset_epoch, datetime.UTC)

    return RateLimitState(
        limit=_int_header(headers, "x-ratelimit-limit"),
        remaining=_int_header(headers, "x-ratelimit-remaining"),
        reset_utc=reset_utc,
        resource=http.response_header(headers, "x-ratelimit-resource"),
    )


def token_shape(headers: Any, now: datetime.datetime | None = None) -> TokenShape:
    """Tell a classic personal access token from a fine-grained one.

    A classic PAT answers with `x-oauth-scopes` listing its scopes. A fine-grained token
    has no scopes and sends no such header. That is the whole test, and it is worth
    having because the two have very different blast radii: there is no fine-grained
    permission equivalent to removing a repository, so a fine-grained token cannot do it
    at all, while a classic token with the matching scope can.

    Fine-grained tokens also send their expiry date, which is the only way to warn
    before a scheduled run starts failing with a 401 that reads like revocation.

    Args:
        headers: Response headers.
        now: Injected for tests.

    Returns:
        A TokenShape.

    Example:
        >>> token_shape({"x-oauth-scopes": "repo, read:org"}).is_classic
        True
    """
    scope_header = http.response_header(headers, "x-oauth-scopes")
    scopes = [part.strip() for part in scope_header.split(",") if part.strip()]

    expires_utc = None
    days_until_expiry = None
    raw = http.response_header(headers, "github-authentication-token-expiration")
    if raw:
        # The header reads "2026-12-31 23:59:59 UTC", which no single standard parse
        # handles, so the zone label is dropped and the rest read as universal time.
        normalized = re.sub(r"\s+UTC$", "", raw).strip()
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.datetime.strptime(normalized, pattern).replace(
                    tzinfo=datetime.UTC
                )
            except ValueError:
                continue
            expires_utc = parsed
            moment = now or datetime.datetime.now(datetime.UTC)
            # Floor, not round: a token with 0.4 days left has 0 days left, and
            # reporting 1 would be the wrong side of the warning threshold. A past
            # expiry stays negative rather than clamping to zero, because "expired
            # three days ago" and "expires today" call for different actions.
            days_until_expiry = (parsed - moment).days
            break

    return TokenShape(
        is_classic=bool(scope_header),
        scope=scopes,
        expires_utc=expires_utc,
        days_until_expiry=days_until_expiry,
    )


@dataclass(frozen=True)
class RelativeTarget:
    """A next-page URL, split into what the transport takes."""

    path: str
    query: dict[str, str]


def relative_target(url: str, base_url: str) -> RelativeTarget:
    """Split an absolute API URL into the path and query the transport takes.

    The `Link` header gives the next page as an absolute URL. The transport builds a URL
    from a base plus a path plus a query mapping, so this converts one into the other
    rather than having two places that construct URLs.

    Doing it this way, instead of computing `?page=N+1`, is what makes pagination follow
    what the API actually said. Some endpoints paginate by an opaque cursor that cannot
    be computed at all.

    A URL whose host differs from the base URL is rejected. A `Link` header is
    server-controlled input, and following it to another host would send the
    Authorization header there - the same disclosure the redirect refusal in `http.py`
    exists to prevent, arriving by a different route.

    Args:
        url: The absolute URL from the Link header.
        base_url: The normalised API base URL the run is bound to.

    Returns:
        A RelativeTarget.

    Raises:
        http.TransportError: The URL is not absolute, or points at another host.

    Example:
        >>> relative_target("https://example.com/user/repos?page=2", "https://example.com").path
        'user/repos'
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise http.TransportError(
            f"The Link header offered a next page that is not an absolute URL: '{url}'."
        )

    base = urllib.parse.urlsplit(base_url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        raise http.TransportError(
            f"The Link header pointed at {parsed.scheme}://{parsed.netloc}, which is not the "
            f"configured API host {base.scheme}://{base.netloc}. Refusing to follow it: the "
            "Authorization header would go with the request."
        )

    base_path = base.path.rstrip("/")
    path = parsed.path
    if base_path and path.startswith(base_path):
        path = path[len(base_path) :]
    path = path.strip("/")

    # Unescaped, because build_uri escapes on the way back out. Passing the escaped form
    # through would double-encode every cursor.
    query = {
        name: value
        for name, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    }

    return RelativeTarget(path=path, query=query)


def request(
    context: GitHubContext,
    path: str,
    query: dict | None = None,
    allow_not_found: bool = False,
    transport=http.read_only_request,
) -> GitHubResponse | None:
    """Send one authenticated GET to the API and return the parsed body.

    Adds the two things a GitHub caller needs on top of the generic transport: the
    status guidance map, and the response headers, which on this API are sometimes the
    answer rather than metadata - `Link` carries pagination and `x-ratelimit-remaining`
    carries the budget.

    Args:
        context: The context from `new_context`.
        path: API path with no leading slash, such as 'user/repos'.
        query: Optional query values.
        allow_not_found: Return None instead of raising on 404. Use only where absence
            has been established another way: on GitHub a 404 also means "no
            permission".
        transport: Injected for tests.

    Returns:
        A GitHubResponse, or None on an allowed 404.
    """
    response = transport(
        base_url=context.base_url,
        path=path,
        headers=context.headers,
        query=query,
        timeout_seconds=context.timeout_seconds,
        maximum_retry_count=context.maximum_retry_count,
        retry_after_cap_seconds=context.retry_after_cap_seconds,
        status_message=STATUS_MESSAGE,
        allow_not_found=allow_not_found,
    )
    if response is None:
        return None

    return GitHubResponse(
        # The full URL, not "GET {path}": parse_json_response composes its own
        # "GET {uri} answered with..." message, so prefixing here produced
        # "GET GET user/repos answered with HTML" - and dropped the base URL, which is
        # the one thing the message then tells the reader to go and check.
        content=http.parse_json_response(
            response.content, http.build_uri(context.base_url, path, query)
        ),
        headers=response.headers,
        status_code=response.status_code,
        rate_limit=rate_limit_state(response.headers),
        link=http.response_header(response.headers, "Link"),
    )


def paged_result(
    context: GitHubContext,
    path: str,
    query: dict | None = None,
    transport=http.read_only_request,
) -> PagedResult:
    """Follow `Link rel="next"` and return every item in a collection.

    The loop terminates on one condition only: the `Link` header stopped offering a next
    page. It does not stop on a short page, because a short page is not the end of a
    collection that is changing underneath the reader.

    Reaching `maximum_page_count` is NOT a quiet truncation. The result says so, and the
    caller is expected to turn that into a blocked plan. An inventory that reports fewer
    repositories than exist, while claiming to be complete, is worse than one that
    fails: the whole point of the phase-1 inventory is to be the input to a decision.

    Args:
        context: The context from `new_context`.
        path: API path with no leading slash.
        query: Query values applied to the FIRST request. Later pages use the query the
            Link header supplies, so per_page does not have to be re-sent or re-derived.
        transport: Injected for tests.

    Returns:
        A PagedResult.
    """
    effective_query = {"per_page": context.page_size}
    if query:
        effective_query.update(query)

    items: list[Any] = []
    current_path = path
    current_query: dict = effective_query
    page_count = 0
    truncated = False
    rate_limit = None

    while True:
        response = request(context, current_path, current_query, transport=transport)
        page_count += 1
        rate_limit = response.rate_limit

        body = response.content
        if isinstance(body, list):
            items.extend(item for item in body if item is not None)
        elif body is not None:
            items.append(body)

        next_url = http.link_header_target(response.link, "next")
        if not next_url:
            break

        if page_count >= context.maximum_page_count:
            truncated = True
            break

        target = relative_target(next_url, context.base_url)
        current_path = target.path
        current_query = target.query

    return PagedResult(
        item=items, page_count=page_count, truncated=truncated, rate_limit=rate_limit
    )


def owned_repositories(context: GitHubContext, transport=http.read_only_request) -> PagedResult:
    """Every repository the authenticated account owns, public and private.

    Uses `GET /user/repos` with `affiliation=owner`, and NOT the endpoint with the
    account name in the path, which returns public repositories only.

    That distinction is the reason this function exists rather than each caller building
    a path: the public endpoint omits every private repository, so an inventory built on
    it reports itself complete while being short by however many the account has. See
    docs/adr/0005-authenticated-account-listing.md.

    `affiliation=owner` rather than the default, because the default also brings in
    repositories the account collaborates on or reaches through an organization, which
    are not the account's to configure.

    Args:
        context: The context from `new_context`.
        transport: Injected for tests.

    Returns:
        The paged result.
    """
    return paged_result(
        context,
        "user/repos",
        {"affiliation": "owner", "sort": "full_name", "direction": "asc"},
        transport=transport,
    )
