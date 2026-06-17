"""Simple data models."""


class User:
    def __init__(self, user_id: int, name: str):
        self.user_id = user_id
        self.name = name

    def display_name(self) -> str:
        return f"{self.name} ({self.user_id})"


class Order:
    def __init__(self, order_id: int, user: User):
        self.order_id = order_id
        self.user = user
        self.items: list[str] = []

    def add_item(self, item: str) -> None:
        self.items.append(item)

    def total_items(self) -> int:
        return len(self.items)
