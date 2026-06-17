"""HTTP-style handlers."""
from auth import authenticate, verify_token
from models import Order, User


def get_user(user_id: int, token: str) -> dict:
    user = authenticate(user_id, token)
    if user is None:
        return {"error": "unauthorized"}
    return {"id": user.user_id, "name": user.display_name()}


def create_order(user_id: int, token: str, items: list[str]) -> dict:
    if not verify_token(token):
        return {"error": "invalid token"}
    user = User(user_id=user_id, name="buyer")
    order = Order(order_id=1, user=user)
    for item in items:
        order.add_item(item)
    return {"order_id": order.order_id, "items": order.total_items()}
