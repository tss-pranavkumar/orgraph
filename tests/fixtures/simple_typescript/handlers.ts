// HTTP-style handlers.
import { authenticate, verifyToken } from "./auth";
import { Order, User } from "./models";

export function getUser(userId: number, token: string): Record<string, unknown> {
  const user = authenticate(userId, token);
  if (user === null) {
    return { error: "unauthorized" };
  }
  return { id: userId, name: user.displayName() };
}

export function createOrder(
  userId: number,
  token: string,
  items: string[]
): Record<string, unknown> {
  if (!verifyToken(token)) {
    return { error: "invalid token" };
  }
  const user = new User(userId, "buyer");
  const order = new Order(1, user);
  for (const item of items) {
    order.addItem(item);
  }
  return { orderId: order.orderId, items: order.totalItems() };
}
