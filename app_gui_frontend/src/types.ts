export type PayloadRef = {
  id: string;
  preview: string;
  size: number;
  truncated: boolean;
  content_type?: string;
};

export type ContextEvent = {
  event_id: string;
  event_type: string;
  session_id: string;
  source: string;
  created_at: number;
  payload: Record<string, any>;
};

export type SessionState = {
  session_id: string;
  model?: string;
  running: boolean;
  event_count: number;
  last_response?: string | null;
  last_error?: string | null;
  token_usage?: number | string;
};

export type ApiStatus = 'idle' | 'connecting' | 'ready' | 'running' | 'error';
