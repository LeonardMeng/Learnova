"""Errors shared by every layer, and how to explain them to the user.

Each Learnova error carries the HTTP status the API should answer with. `describe` turns an
exception into (status, message); the CLI prints the message, the API returns both. Exceptions it
doesn't know (bugs) return None and are left alone, so they still show a full traceback."""

from __future__ import annotations


class LearnovaError(Exception):
    """A problem worth showing to the user as-is."""

    status = 400


class NotFoundError(LearnovaError):
    status = 404


class ConflictError(LearnovaError):
    status = 409


def describe(exc: BaseException) -> tuple[int, str] | None:
    import httpx
    from notion_client.errors import APIErrorCode, APIResponseError, RequestTimeoutError

    from learnova.llm.factory import LLMCallError

    if isinstance(exc, LLMCallError):
        return exc.status, f"The model call failed. {exc}"
    if isinstance(exc, LearnovaError):
        return exc.status, str(exc)
    if isinstance(exc, APIResponseError):
        if exc.code in (APIErrorCode.ObjectNotFound, APIErrorCode.RestrictedResource):
            return 502, (
                "Notion can't see that page. Open it in Notion, click ••• → Connections, and add "
                f"your integration. Also check the page ID.\n(Notion said: {exc})"
            )
        if exc.code == APIErrorCode.Unauthorized:
            return 502, (
                "Notion rejected the token in NOTION_API_KEY. Check it is the integration's "
                "Internal Integration Secret."
            )
        return 502, f"Notion API error ({exc.code}): {exc}"
    if isinstance(exc, RequestTimeoutError):
        return 504, "Notion did not respond in time. Check your network and try again."
    if isinstance(exc, httpx.TransportError):
        return 502, f"Could not reach Notion: {exc}. Check your network or proxy."
    return None
