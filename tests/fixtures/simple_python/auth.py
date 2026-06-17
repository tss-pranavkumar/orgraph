"""Auth utilities."""
from models import User


def authenticate(user_id: int, token: str) -> User | None:
    if not token:
        return None
    return User(user_id=user_id, name="test")


def verify_token(token: str) -> bool:
    return len(token) > 8
