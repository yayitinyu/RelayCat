export interface User {
  id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  is_verified: number; // SQLite uses 0/1 for boolean
  is_banned: number;
  created_at: number;
  updated_at: number;
}

export interface MessageRoute {
  id?: number;
  user_id: number;
  admin_message_id: number;
  user_message_id: number;
  created_at?: number;
}

export interface Rule {
  id?: number;
  rule_type: string;
  pattern: string;
  action: string;
  is_active: number;
  created_at?: number;
}

export interface Setting {
  key: string;
  value: string;
  description: string | null;
}
