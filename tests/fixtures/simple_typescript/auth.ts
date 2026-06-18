// Auth utilities.
import { User } from "./models";

export function authenticate(userId: number, token: string): User | null {
  if (!token) {
    return null;
  }
  return new User(userId, "test");
}

export function verifyToken(token: string): boolean {
  return token.length > 8;
}
