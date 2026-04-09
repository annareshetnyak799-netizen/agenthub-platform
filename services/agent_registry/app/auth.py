from fastapi import HTTPException, Request


def require_bearer_token(request: Request, expected_token: str, realm: str) -> None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=f"Missing bearer token for {realm}",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected_token:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid bearer token for {realm}",
        )
