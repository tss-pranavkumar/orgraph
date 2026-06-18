// Simple data models.

export interface Identifiable {
  id(): string;
}

export enum Role {
  Admin = "admin",
  Member = "member",
}

export class User {
  constructor(public userId: number, public name: string) {}

  displayName(): string {
    return `${this.name} (${this.userId})`;
  }
}

export class AdminUser extends User {
  constructor(userId: number, name: string, public role: string) {
    super(userId, name);
  }

  label(): string {
    return `${this.displayName()} [${this.role}]`;
  }
}

export class Order {
  items: string[] = [];

  constructor(public orderId: number, public user: User) {}

  addItem(item: string): void {
    this.items.push(item);
  }

  totalItems(): number {
    return this.items.length;
  }
}
