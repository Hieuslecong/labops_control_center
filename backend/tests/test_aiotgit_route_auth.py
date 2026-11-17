import unittest

from fastapi.routing import APIRoute

from app.api.dependencies import require_aiotgit, require_user
from app.api.v1.api import api_router


def dependency_calls(dependant):
    calls = set()
    for dependency in dependant.dependencies:
        calls.add(dependency.call)
        calls.update(dependency_calls(dependency))
    return calls


class AioGitRouteAuthTests(unittest.TestCase):
    def test_cli_routes_use_scoped_capability_without_human_cookie_guard(self):
        include = next(
            route
            for route in api_router.routes
            if type(route).__name__ == "_IncludedRouter"
            and route.include_context.prefix == "/repositories"
        )
        route = next(
            route
            for route in include.original_router.routes
            if isinstance(route, APIRoute)
            and route.path == "/cli/requests"
            and "POST" in route.methods
        )
        calls = dependency_calls(route.dependant)
        self.assertIn(require_aiotgit, calls)
        self.assertNotIn(require_user, calls)
        self.assertFalse(include.include_context.dependencies)

    def test_browser_repository_routes_still_require_human_identity(self):
        include = next(
            route
            for route in api_router.routes
            if type(route).__name__ == "_IncludedRouter"
            and route.include_context.prefix == "/repositories"
        )
        route = next(
            route
            for route in include.original_router.routes
            if isinstance(route, APIRoute)
            and route.path == "/requests"
            and "POST" in route.methods
        )
        self.assertIn(require_user, dependency_calls(route.dependant))


if __name__ == "__main__":
    unittest.main()
