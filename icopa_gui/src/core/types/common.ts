export type AnyRecord = Record<string, unknown>;

export interface BaseEntity {
  id?: number;
  name?: string;
  created_at?: string;
  updated_at?: string;
}
