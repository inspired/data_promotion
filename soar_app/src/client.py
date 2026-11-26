"""HTTP client for the Data Promotion REST API on the Search Head (/services/data_promotion)."""

from typing import Any

import httpx
from soar_sdk.exceptions import ActionFailure

API_PATH = "/services/data_promotion/"
BODY_PREVIEW = 200


class ApiError(ActionFailure):
    """A failed call to the Data Promotion API, with the HTTP status and the field at fault."""

    def __init__(
        self, message: str, status: int | None = None, field: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.field = field


def _error_from(response: httpx.Response) -> ApiError:
    status = response.status_code
    field = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("error"):
        text = str(body["error"])
        field = body.get("field") or None
    else:
        text = " ".join(response.text.split())[:BODY_PREVIEW] or response.reason_phrase
    message = f"{status} {text}"
    if field:
        message += f" (field: {field})"
    if status == 401:
        message += " — check the asset's token"
    elif status == 403:
        message += " — the token's user needs the promote_data capability (role promotion_user)"
    return ApiError(message, status=status, field=field)


class PromotionClient:
    """Calls the API with a Splunk bearer token. Paths are relative to /services/data_promotion."""

    def __init__(
        self,
        base_url: str,
        token: str,
        verify: bool = True,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/").removesuffix(API_PATH.rstrip("/"))
        self._http = httpx.Client(
            base_url=self.base_url + API_PATH,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=verify,
            timeout=timeout,
            transport=transport,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict:
        return self._request("POST", path, json=body or {})

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ApiError(f"Cannot reach {self.base_url}: {e}") from e
        if response.is_error:
            raise _error_from(response)
        try:
            return response.json()
        except ValueError as e:
            raise ApiError(
                f"{response.status_code} answer is not JSON — is base_url the Search Head's management port?",
                status=response.status_code,
            ) from e

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PromotionClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
