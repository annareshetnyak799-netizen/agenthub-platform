from fastapi import HTTPException, Request


def extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip()


def require_bearer_token(request: Request, expected_token: str, realm: str) -> None:
    token = extract_bearer_token(request)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail=f"Missing bearer token for {realm}",
        )
    if token != expected_token:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid bearer token for {realm}",
        )
