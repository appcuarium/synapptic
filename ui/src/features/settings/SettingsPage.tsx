// Settings: provider, model, API key, batches, headless sessions. Reads and writes the real
// ~/.synapptic/config.yaml over the websocket (config.get / config.update / providers.list).
// Edits are a local draft until Save; a save from another tab arrives as a "config" event.
// Built on shadcn (Button, Input, Label, Select, Toggle) with the mockup's classes on top.
import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { fieldClasses, labelClasses } from '@/components/machine/field';
import { ToggleRow } from '@/components/machine/OnOffToggle';
import { TileStrip } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/cn';
import { useWsMutation, useWsQuery } from '@/lib/ws/react';
import { TUNING_LEVELS, type ConfigPatch, type DashboardConfig, type ProviderInfo, type TuningLevel } from './types';

// Colours, weight and touch height come from the Button variants (template tokens); only the mockup's 2px corners here.
const buttonClasses = 'rounded-xs shadow-none';
// Providers have no metric to grade by, so strength marks the selection: the chosen provider at full
// strength, the others low (a top edge and a 2% scale alone are not enough to read which one is chosen).
const SELECTED_TILE_STRENGTH = 1;
const UNSELECTED_TILE_STRENGTH = 0.15;
// the strip's first card: the settings for every project (never a provider id)
const GLOBAL_TILE = '__global__';

interface Draft {
  provider: string;
  model: string;
  apiKey: string;           // write-only: empty = leave the stored key as it is
  batchesExtract: boolean;
  includeHeadless: boolean;
  writeRules: boolean;
  tuning: TuningLevel;
  keyword: string;          // the word that turns a prompt into a rule ('' when the server has none to show)
}

// The keyword is one plain word of at most this many characters (letters and digits), with :, -, -> or >
// at its end if you like; the server refuses anything else, this is the same rule so Save never sends it.
const KEYWORD_MAX_LENGTH = 16;
const PLAIN_KEYWORD = /^[\p{L}\p{N}]+(?:->|[:\->])?$/u;
const KEYWORD_NOTE = 'a plain word, with :, -, -> or > at its end if you like';

function keywordIsPlain(word: string): boolean {
  return word.length <= KEYWORD_MAX_LENGTH && PLAIN_KEYWORD.test(word);
}

// What each position of the dial means, in the words the CLI uses (`synapptic config tuning`).
const TUNING_LABELS: Record<TuningLevel, string> = {
  off: 'off: the words stay as they are',
  conservative: 'conservative: changes only on strong evidence',
  normal: 'normal',
  eager: 'eager: changes on little evidence',
};

function draftFrom(config: DashboardConfig): Draft {
  return {
    provider: config.provider,
    model: config.model,
    apiKey: '',
    batchesExtract: Boolean(config.batches?.extract),
    includeHeadless: Boolean(config.include_headless_sessions),
    writeRules: config.write_rules,
    tuning: config.tuning,
    keyword: config.keyword ?? '',
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
  if (draft.writeRules !== config.write_rules) patch.write_rules = draft.writeRules;
  if (draft.tuning !== config.tuning) patch.tuning = draft.tuning;
  const keyword = draft.keyword.trim();
  if (keyword && keyword !== (config.keyword ?? '')) patch.keyword = keyword;
  return patch;
}

export function SettingsPage() {
  const config = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  const providers = useWsQuery<ProviderInfo[]>('providers.list');
  const save = useWsMutation<DashboardConfig, ConfigPatch>('config.update');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showGlobal, setShowGlobal] = useState(true);   // the Global card picked in the strip (the page opens on it; else the provider's)

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
  const batchProviders = providerList.filter((p) => p.batches).map((p) => p.name).join(', ');
  const patch = patchFrom(draft, current);
  const dirty = Object.keys(patch).length > 0;
  const keywordBad = draft.keyword.trim() !== '' && !keywordIsPlain(draft.keyword.trim());
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
              disabled={save.isPending || keywordBad} onClick={() => save.mutate(patch)}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
          </>
        )}
      </PageHeader>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-px bg-line border-b border-line">
        {/* One strip: the Global card first (what applies in every project: the guards' tuning, the rule keyword;
            operator 2026-11-05), then the providers. The fields under the strip belong to the card shown. */}
        <div className="bg-page lg:col-span-2 flex flex-col">
          <TileStrip label="Settings" selectedId={showGlobal ? GLOBAL_TILE : draft.provider}
            statusLeft={<span>Global and {providerList.length} providers</span>}
            onSelect={(id: string) => {
              if (id === GLOBAL_TILE) { setShowGlobal(true); return; }
              setShowGlobal(false);
              update({ provider: id, model: providerList.find((p) => p.id === id)?.default_model || draft.model });
            }}
            tiles={[
              { id: GLOBAL_TILE, name: 'every project', value: 'Global', sub: 'tuning · rule keyword',
                strength: showGlobal ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH },
              ...providerList.map((p) => ({
                id: p.id,
                name: `${p.requires_key ? 'API key' : 'no API key'}${p.batches ? ' · batches' : ''}`,
                value: p.name,
                sub: p.id,
                strength: !showGlobal && p.id === draft.provider ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
              })),
            ]} />
          <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            {showGlobal && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="tuning" className={labelClasses}>Guards tuning</Label>
                <Select value={draft.tuning} onValueChange={(tuning) => update({ tuning: tuning as TuningLevel })}>
                  <SelectTrigger id="tuning" className={fieldClasses}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="text-muted-foreground">
                    {TUNING_LEVELS.map((level) => (
                      <SelectItem key={level} value={level} className="focus:text-muted-foreground">{TUNING_LABELS[level]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {showGlobal && current.keyword !== undefined && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="keyword" className={labelClasses}>Rule keyword</Label>
                <Input id="keyword" autoComplete="off" spellCheck={false} maxLength={KEYWORD_MAX_LENGTH} className={fieldClasses}
                  placeholder={current.keyword} aria-invalid={keywordBad || undefined}
                  value={draft.keyword} onChange={(e) => update({ keyword: e.target.value })} />
                {keywordBad && <span className="text-[13px] text-fg-subtle">{KEYWORD_NOTE}</span>}
              </div>
            )}
            {!showGlobal && (
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
            )}
            {!showGlobal && provider?.requires_key && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="api-key" className={labelClasses}>API key</Label>
                <Input id="api-key" type="password" autoComplete="off" className={fieldClasses}
                  placeholder={current.api_key_set ? `set, ends in ${current.api_key_hint}` : 'not set'}
                  value={draft.apiKey} onChange={(e) => update({ apiKey: e.target.value })} />
              </div>
            )}
          </div>
        </div>

        {/* The Global card's toggles (operator 2026-11-05: global, not one copy under each provider), as Machine
            lays out settings: no group header, a grid of label + description + control. Message Batches is one
            setting that acts only where the provider has a batch endpoint. */}
        {showGlobal && (
          <div className="bg-page lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 px-4 py-4">
            <ToggleRow id="batches-extract" label="Message Batches"
              description={`extraction at half price, results at the next checkpoint; acts with ${batchProviders || 'a provider that has batches'} (synthesis is always direct)`}
              on={draft.batchesExtract} onChange={(on) => update({ batchesExtract: on })} />
            <ToggleRow id="include-headless" label="Headless runs"
              description="treat claude -p runs as sessions (scratchpads never count)"
              on={draft.includeHeadless} onChange={(on) => update({ includeHeadless: on })} />
            <ToggleRow id="write-rules" label="Rules in your tools"
              description="plug the learned rules into your coding tools (off: still learning)"
              on={draft.writeRules} onChange={(on) => update({ writeRules: on })} />
          </div>
        )}
      </div>
      {save.error && <p className="p-4 text-sm text-fg-muted gutter">Not saved: {save.error.message}</p>}
    </section>
  );
}
