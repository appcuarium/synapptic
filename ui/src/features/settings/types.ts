// Shapes of the server methods config.get / config.update / providers.list
// (src/synapptic/dashboard/handlers.py).
export interface DashboardConfig {
  provider: string;
  model: string;
  api_key_set: boolean;
  api_key_hint: string;
  batches?: { extract?: boolean };
  include_headless_sessions?: boolean;
  [key: string]: unknown;
}

export interface ProviderInfo {
  id: string;
  name: string;
  requires_key: boolean;
  default_model: string;
  models: { id: string; label: string }[];
  batches: boolean;
}

export interface ConfigPatch extends Record<string, unknown> {
  provider?: string;
  model?: string;
  api_key?: string;
  batches?: { extract?: boolean };
  include_headless_sessions?: boolean;
}
