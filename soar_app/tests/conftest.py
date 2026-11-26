import json

import httpx
import pytest

from src.client import PromotionClient

BASE = "https://sh1:8089"


class Recorder:
    """A MockTransport that answers from a route table and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (
            request.method,
            request.url.path.removeprefix("/services/data_promotion/"),
        )
        answer = self.routes.get(key)
        if answer is None:
            return httpx.Response(404, json={"error": "no such endpoint"})
        if callable(answer):
            return answer(request)
        status, body = answer
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    def bodies(self, method="POST"):
        return [json.loads(r.content) for r in self.requests if r.method == method]


@pytest.fixture
def api():
    """api(routes) -> (PromotionClient, Recorder)."""

    def make(routes, base=BASE):
        rec = Recorder(routes)
        return PromotionClient(base, "tok", transport=httpx.MockTransport(rec)), rec

    return make
