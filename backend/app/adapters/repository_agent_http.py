"""Transport for requests that carry a managed repository credential."""
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_repository_agent(request: urllib.request.Request, *, timeout: float):
    target = urllib.parse.urlsplit(request.full_url)
    if target.username is not None or target.password is not None or not target.hostname:
        raise ValueError("Agent URL không hợp lệ")
    if target.scheme != "https" and not (
        target.scheme == "http" and target.hostname in ("127.0.0.1", "::1")
    ):
        raise ValueError("Managed Git yêu cầu Agent HTTPS hoặc loopback")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)
