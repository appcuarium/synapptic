// Shapes of the server methods config.get / config.update / providers.list
// (src/synapptic/dashboard/handlers.py).
export const TUNING_LEVELS = ['off', 'conservative', 'normal', 'eager'] as const;
export type TuningLevel = (typeof TUNING_LEVELS)[number];

// A custom endpoint saved from Settings: a card of its own in the strip, one per URL.
export interface CustomEndpoint {
  name: string;
  url: string;
  model: string;
  api_key_set?: boolean;   // the endpoint holds a key of its own (the key itself never reaches the browser)
  default?: boolean;       // the custom provider is the default and this is its URL in use
}

// One provider's own settings (2026-11-06: every provider configured keeps its own; the key as set / last 4).
export interface ProviderSettings {
  model: string;
  api_url: string;
  api_key_set: boolean;
  api_key_hint: string;
}

export interface DashboardConfig {
  provider: string;        // the default: what every call without a choice runs on
  model: string;
  api_key_set: boolean;
  api_key_hint: string;
  api_url?: string;
  providers?: Record<string, ProviderSettings>;   // every provider configured, the default among them
  default_provider?: string;
  endpoints?: CustomEndpoint[];   // the custom endpoints saved so far, each a card in the strip
  batches?: { extract?: boolean };
  include_headless_sessions?: boolean;
  write_rules: boolean;
  tuning: TuningLevel;   // how readily the guards' words change on their own
  keyword?: string;      // the word that turns a prompt into a rule; absent without the core
  [key: string]: unknown;
}

export interface ProviderInfo {
  id: string;
  name: string;
  requires_key: boolean;
  requires_url: boolean;   // the custom endpoint: no URL, no provider
  default_url: string;     // a local server or an API with a known URL ('' when none)
  default_model: string;
  models: { id: string; label: string }[];
  batches: boolean;
}

export interface ConfigPatch extends Record<string, unknown> {
  provider?: string;
  model?: string;
  api_key?: string;
  api_url?: string;
  endpoint_name?: string;   // with the custom provider and a URL: the card's name
  remove_endpoint?: string; // the URL of a saved endpoint to drop, with its card
  set_default?: boolean;    // the card saved becomes the default
  batches?: { extract?: boolean };
  include_headless_sessions?: boolean;
  write_rules?: boolean;
  tuning?: TuningLevel;
  keyword?: string;
}
