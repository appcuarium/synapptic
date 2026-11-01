// Settings: provider, model, API key, batches, headless sessions. Reads and writes the real
// ~/.synapptic/config.yaml over the websocket (config.get / config.update / providers.list).
// Edits are a local draft until Save; a save from another tab arrives as a "config" event.
// Built on shadcn (Button, Input, Label, Select, Toggle) with the mockup's classes on top.
import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { fieldClasses } from '@/components/machine/field';
import { ToggleRow } from '@/components/machine/OnOffToggle';
import { TileStrip } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/cn';
import { useWsMutation, useWsQuery } from '@/lib/ws/react';
import type { ConfigPatch, DashboardConfig, ProviderInfo } from './types';

// Colours, weight and touch height come from the Button variants (template tokens); only the mockup's 2px corners here.
const buttonClasses = 'rounded-xs shadow-none';
const labelClasses = 'text-sm leading-5 font-normal text-primary';
// Providers have no metric to grade by, so strength marks the selection: the chosen provider at full
// strength, the others low (a top edge and a 2% scale alone are not enough to read which one is chosen).
const SELECTED_TILE_STRENGTH = 1;
const UNSELECTED_TILE_STRENGTH = 0.15;

interface Draft {
  provider: string;
  model: string;
  apiKey: string;           // write-only: empty = leave the stored key as it is
  batchesExtract: boolean;
  includeHeadless: boolean;
}

function draftFrom(config: DashboardConfig): Draft {
  return {
    provider: config.provider,
    model: config.model,
    apiKey: '',
    batchesExtract: Boolean(config.batches?.extract),
    includeHeadless: Boolean(config.include_headless_sessions),
  };
}

function patchFrom(draft: Draft, config: DashboardConfig): ConfigPatch {
  const patch: ConfigPatch = {};
  if (draft.provider !== config.provider) patch.provider = draft.provider;
  if (draft.model !== config.model) patch.model = draft.model;
  if (draft.apiKey) patch.api_key = draft.apiKey;
  if (draft.batchesExtract !== Boolean(config.batches?.extract)) patch.batches = { extract: draft.batchesExtract };
  if (draft.includeHeadless !== Boolean(config.include_headless_sessions)) {
    patch.include_headless_sessions = draft.includeHeadless;
  }
  return patch;
}

export function SettingsPage() {
  const config = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  const providers = useWsQuery<ProviderInfo[]>('providers.list');
  const save = useWsMutation<DashboardConfig, ConfigPatch>('config.update');
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    if (config.data) setDraft(draftFrom(config.data));
  }, [config.data]);

  if (config.error || providers.error) {
    return (
      <PageHeader title="Settings">
        <span className="text-[13px] text-fg-subtle">Could not load the settings: {(config.error ?? providers.error)?.message}</span>
      </PageHeader>
    );
  }
  if (!config.data || !providers.data || !draft) {
    return <PageHeader title="Settings"><span className="text-[13px] text-fg-subtle">Loading…</span></PageHeader>;
  }

  const current = config.data;
  const providerList = providers.data;
  const provider = providerList.find((p) => p.id === draft.provider);
  const models = provider?.models.some((m) => m.id === draft.model)
    ? provider.models
    : [{ id: draft.model, label: draft.model }, ...(provider?.models ?? [])];
  const patch = patchFrom(draft, current);
  const dirty = Object.keys(patch).length > 0;
  const update = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });

  return (
    <section className="flex flex-col">
      <PageHeader title="Settings">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          Saved to <span className="text-fg-muted">~/.synapptic/config.yaml</span>, the same file the CLI uses
        </span>
        {/* Only while there is something to save: no shaded buttons on an unchanged page */}
        {(dirty || save.isPending) && (
          <>
            <Button variant="secondary" className={cn(buttonClasses, 'ml-auto')}
              disabled={save.isPending} onClick={() => setDraft(draftFrom(current))}>
              Discard
            </Button>
            <Button className={buttonClasses}
              disabled={save.isPending} onClick={() => save.mutate(patch)}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
          </>
        )}
      </PageHeader>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-px bg-line border-b border-line">
        <div className="bg-page lg:col-span-2 flex flex-col">
          <TileStrip label="Provider" selectedId={draft.provider}
            statusLeft={<span>{providerList.length} providers</span>}
            onSelect={(id: string) => update({
              provider: id,
              model: providerList.find((p) => p.id === id)?.default_model || draft.model,
            })}
            tiles={providerList.map((p) => ({
              id: p.id,
              name: `${p.requires_key ? 'API key' : 'no API key'}${p.batches ? ' · batches' : ''}`,
              value: p.name,
              sub: p.id,
              strength: p.id === draft.provider ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
            }))} />
          <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            <div className="flex flex-col gap-1">
              <Label htmlFor="model" className={labelClasses}>Model</Label>
              <Select value={draft.model} onValueChange={(model) => update({ model })}>
                <SelectTrigger id="model" className={fieldClasses}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="text-muted-foreground">
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id} className="focus:text-muted-foreground">{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {provider?.requires_key && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="api-key" className={labelClasses}>API key</Label>
                <Input id="api-key" type="password" autoComplete="off" className={fieldClasses}
                  placeholder={current.api_key_set ? `set, ends in ${current.api_key_hint}` : 'not set'}
                  value={draft.apiKey} onChange={(e) => update({ apiKey: e.target.value })} />
              </div>
            )}
          </div>
        </div>

        {/* Toggles as Machine lays out settings: no group header, a grid of label + description + control */}
        <div className="bg-page lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 px-4 py-4">
          {provider?.batches && (
            <ToggleRow id="batches-extract" label="Message Batches"
              description="extraction at half price, results at the next checkpoint (synthesis is always direct)"
              on={draft.batchesExtract} onChange={(on) => update({ batchesExtract: on })} />
          )}
          <ToggleRow id="include-headless" label="Headless runs"
            description="treat claude -p runs as sessions (scratchpads never count)"
            on={draft.includeHeadless} onChange={(on) => update({ includeHeadless: on })} />
        </div>
      </div>
      {save.error && <p className="p-4 text-sm text-fg-muted gutter">Not saved: {save.error.message}</p>}
    </section>
  );
}
