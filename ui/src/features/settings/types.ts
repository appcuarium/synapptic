// Shapes of the server methods config.get / config.update / providers.list
// (src/synapptic/dashboard/handlers.py).
export const TUNING_LEVELS = ['off', 'conservative', 'normal', 'eager'] as const;
export type TuningLevel = (typeof TUNING_LEVELS)[number];

export interface DashboardConfig {
  provider: string;
  model: string;
  api_key_set: boolean;
  api_key_hint: string;
  batches?: { extract?: boolean };
  include_headless_sessions?: boolean;
  write_rules: boolean;
  tuning: TuningLevel;   // how readily the guards' words change on their own
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
  write_rules?: boolean;
  tuning?: TuningLevel;
}
