"""Authentication and authorisation helpers.

These functions inspect the `Authorization` header, validate API tokens and
enforce user roles.  They raise `HTTPException` with appropriate status codes
when authentication or authorisation fails.  All error messages are written
explicitly in English as required by the specification.
"""

from typing import Optional

from fastapi import HTTPException, Header, Depends
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, UserRole


def _extract_token(auth_header: Optional[str]) -> str:
    """Parse the Authorization header and return the API key.

    The expected format is `TOKEN <apiKey>`.  Any deviation results in a
    401 Unauthorized exception.  The message intentionally does not expose
    implementation details to the client.
    """
    if not auth_header:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    try:
        token_type, api_key = auth_header.split(" ", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    if token_type.upper() != "TOKEN" or not api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return api_key


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Return the `User` corresponding to the provided API key.

    If the header is missing, malformed or the key does not exist in the
    database, a 401 Unauthorized exception is raised.
    """
    api_key = _extract_token(authorization)
    user = db.query(User).filter(User.api_key == api_key).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return user


def require_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Ensure that a request is authenticated and return the `User`.

    This helper wraps `get_current_user` to provide a clearer name for use in
    endpoint signatures.  It does not catch exceptions.
    """
    return get_current_user(authorization=authorization, db=db)


def require_admin(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Ensure that the caller is an administrator.

    A 403 Forbidden exception is raised if the authenticated user does not
    possess the `ADMIN` role.
    """
    user = get_current_user(authorization=authorization, db=db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user