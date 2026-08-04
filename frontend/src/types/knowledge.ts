export interface Knowledge {
  id: number;
  agent_name: string;
  event_name: string;
  knowledge_type: string;
  severity: string;
  title: string;
  message: string;
  account_id: number | null;
  resolved: boolean;
  created_at: string;
}