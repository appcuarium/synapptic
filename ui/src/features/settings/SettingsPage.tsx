// Settings: one strip with the Global card first (what applies in every project), then the providers and
// the custom endpoints saved so far, each a card; under the strip, the picked card's settings as Machine's
// settings form lays them out (operator 2026-11-06: machine.dev/strategy-analytics/<id>/edit): a section
// bar, rows with the label and its line on the left and the control on the right, three columns. Reads and
// writes the real ~/.synapptic/config.yaml over the websocket (config.get / config.update / providers.list).
// Edits are a local draft until Save; a save from another tab arrives as a "config" event.
import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { notify } from '@/components/machine/Notice';
import { SettingInput, SettingRow, SettingSelect, SettingToggle, SettingsSection } from '@/components/machine/SettingsForm';
import { TONE_CLASSES } from '@/components/machine/TileLabel';
import { TileStrip } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/cn';
import { useWsMutation, useWsQuery } from '@/lib/ws/react';
import { TUNING_LEVELS, type ConfigPatch, type CustomEndpoint, type DashboardConfig, type ProviderInfo, type TuningLevel } from './types';

// Colours, weight and touch height come from the Button variants (template tokens); only the mockup's 2px corners here.
const buttonClasses = 'rounded-xs shadow-none';
// A delete wears the labels' rose (TileLabel's rose tone, the same in both themes; operator 2026-11-06: the
// theme's colours, not shadcn's destructive variant), the page's button shape otherwise.
const deleteButtonClasses = cn(buttonClasses, TONE_CLASSES.rose, 'hover:bg-rose-700');
// Providers have no metric to grade by, so strength marks the selection: the chosen card at full strength,
// the others low (a top edge and a 2% scale alone are not enough to read which one is chosen).
const SELECTED_TILE_STRENGTH = 1;
const UNSELECTED_TILE_STRENGTH = 0.15;
// the strip's first card: the settings for every project (never a provider id)
const GLOBAL_TILE = '__global__';
// a saved custom endpoint's card, by its URL
const ENDPOINT_TILE = 'endpoint:';
const CUSTOM_PROVIDER = 'custom';

interface Draft {
  provider: string;
  model: string;
  apiKey: string;           // write-only: empty = leave the stored key as it is
  apiUrl: string;           // the endpoint's URL, for a provider that takes one (the CLI's "API URL" question)
  endpointName: string;     // a custom endpoint's card name (saved with its URL)
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

// Validation on Save (operator 2026-11-06): the rules the server applies, checked here first so a failing
// field is marked and named before anything is sent; every field that fails is marked, the notice counts them.
// Only what the save touches is checked: the keyword when it changes, the provider card's fields when any
// of them (or the provider) changes; a stored state another card is in never blocks an unrelated save.
type Field = 'keyword' | 'model' | 'apiUrl' | 'apiKey';
type Errors = Partial<Record<Field, string>>;
const HTTP_URL = /^https?:\/\/\S+$/i;
const CARD_FIELDS: (keyof ConfigPatch)[] = ['provider', 'model', 'api_url', 'api_key', 'endpoint_name'];

function validate(draft: Draft, provider: ProviderInfo | undefined, current: DashboardConfig, patch: ConfigPatch): Errors {
  const errors: Errors = {};
  const keyword = draft.keyword.trim();
  if ('keyword' in patch && keyword && !keywordIsPlain(keyword)) errors.keyword = KEYWORD_NOTE;
  if (!CARD_FIELDS.some((field) => field in patch)) return errors;
  const url = draft.apiUrl.trim();
  if (provider?.requires_url && !url) errors.apiUrl = 'the endpoint’s URL is needed';
  else if (url && !HTTP_URL.test(url)) errors.apiUrl = 'the URL starts with http:// or https://';
  if (provider && provider.models.length === 0 && !draft.model.trim()) errors.model = 'the model’s name is needed';
  if (provider?.requires_key && !current.api_key_set && !draft.apiKey) errors.apiKey = 'an API key is needed';
  return errors;
}

function refusal(errors: Errors): string {
  const messages = Object.values(errors);
  return messages.length === 1 ? `Not saved: ${messages[0]}` : `Not saved: ${messages.length} fields need a look`;
}

function endpointName(endpoints: CustomEndpoint[] | undefined, url: string): string {
  return endpoints?.find((e) => e.url === url)?.name ?? '';
}

function draftFrom(config: DashboardConfig): Draft {
  return {
    provider: config.provider,
    model: config.model,
    apiKey: '',
    apiUrl: config.api_url ?? '',
    endpointName: config.provider === CUSTOM_PROVIDER ? endpointName(config.endpoints, config.api_url ?? '') : '',
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
  if (draft.apiUrl.trim() !== (config.api_url ?? '')) patch.api_url = draft.apiUrl.trim();
  const name = draft.endpointName.trim();
  if (draft.provider === CUSTOM_PROVIDER && name && name !== endpointName(config.endpoints, draft.apiUrl.trim())) patch.endpoint_name = name;
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
  const [showGlobal, setShowGlobal] = useState(true);   // the Global card picked in the strip (the page opens on it; else a provider's or an endpoint's)
  const [checked, setChecked] = useState(false);        // a Save was refused: the fields stay marked until they pass
  const [confirmRemove, setConfirmRemove] = useState<CustomEndpoint | null>(null);   // Machine's delete dialog, for a saved endpoint

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
  const endpoints = current.endpoints ?? [];
  const provider = providerList.find((p) => p.id === draft.provider);
  const models = provider?.models.some((m) => m.id === draft.model)
    ? provider.models
    : [{ id: draft.model, label: draft.model }, ...(provider?.models ?? [])];
  const batchProviders = providerList.filter((p) => p.batches).map((p) => p.name).join(', ');
  const patch = patchFrom(draft, current);
  const dirty = Object.keys(patch).length > 0;
  const keywordBad = draft.keyword.trim() !== '' && !keywordIsPlain(draft.keyword.trim());
  const errors = checked ? validate(draft, provider, current, patch) : {};
  const update = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });
  const submit = () => {
    const found = validate(draft, provider, current, patch);
    if (Object.keys(found).length > 0) {
      setChecked(true);
      notify('warning', refusal(found));
      return;
    }
    save.mutate(patch, {
      onSuccess: () => { setChecked(false); notify('done', 'Settings saved'); },
      onError: (error) => notify('error', `Not saved: ${error.message}`),
    });
  };
  // a saved endpoint is removed with its card (operator 2026-11-06), after Machine's confirmation
  const remove = (endpoint: CustomEndpoint) => {
    setConfirmRemove(null);
    save.mutate({ remove_endpoint: endpoint.url }, {
      onSuccess: () => notify('done', `${endpoint.name} removed`),
      onError: (error) => notify('error', `Not removed: ${error.message}`),
    });
  };
  // the card shown: Global, a saved endpoint (the custom provider on its URL), or the provider
  const pickedEndpoint = draft.provider === CUSTOM_PROVIDER ? endpoints.find((e) => e.url === draft.apiUrl.trim()) : undefined;
  const selectedTile = showGlobal ? GLOBAL_TILE : pickedEndpoint ? `${ENDPOINT_TILE}${pickedEndpoint.url}` : draft.provider;
  const cardTitle = showGlobal ? 'Global' : pickedEndpoint ? pickedEndpoint.name : provider?.name ?? draft.provider;

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
              disabled={save.isPending} onClick={() => { setDraft(draftFrom(current)); setChecked(false); }}>
              Discard
            </Button>
            <Button className={buttonClasses} disabled={save.isPending} onClick={submit}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
          </>
        )}
      </PageHeader>

      <div className="flex flex-col">
        {/* One strip: the Global card first (operator 2026-11-05), the providers, then every custom endpoint
            saved so far as a card of its own (operator 2026-11-06). The form under the strip belongs to the card. */}
        <TileStrip label="Settings" selectedId={selectedTile}
          statusLeft={<span>Global, {providerList.length} providers{endpoints.length ? ` and ${endpoints.length} endpoint${endpoints.length === 1 ? '' : 's'}` : ''}</span>}
          onSelect={(id: string) => {
            if (id === GLOBAL_TILE) { setShowGlobal(true); return; }
            setShowGlobal(false);
            if (id.startsWith(ENDPOINT_TILE)) {
              const entry = endpoints.find((e) => e.url === id.slice(ENDPOINT_TILE.length));
              if (entry) update({ provider: CUSTOM_PROVIDER, apiUrl: entry.url, model: entry.model, endpointName: entry.name });
              return;
            }
            // the custom card itself is a new endpoint: a clean form
            if (id === CUSTOM_PROVIDER) { update({ provider: id, apiUrl: '', model: '', endpointName: '' }); return; }
            update({ provider: id, model: providerList.find((p) => p.id === id)?.default_model || draft.model });
          }}
          tiles={[
            { id: GLOBAL_TILE, name: 'every project', value: 'Global', sub: 'tuning · rule keyword',
              strength: showGlobal ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH },
            // the providers, then the saved endpoints, then the custom card that adds one, always last
            // (operator 2026-11-06)
            ...providerList.filter((p) => p.id !== CUSTOM_PROVIDER).map((p) => ({
              id: p.id,
              name: `${p.requires_key ? 'API key' : 'no API key'}${p.batches ? ' · batches' : ''}`,
              value: p.name,
              sub: p.id,
              strength: selectedTile === p.id ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
            })),
            ...endpoints.map((e) => ({
              id: `${ENDPOINT_TILE}${e.url}`,
              name: 'custom endpoint',
              value: e.name,
              sub: e.model || e.url,
              title: `${e.name} · ${e.url}`,
              strength: selectedTile === `${ENDPOINT_TILE}${e.url}` ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
            })),
            ...providerList.filter((p) => p.id === CUSTOM_PROVIDER).map((p) => ({
              id: p.id,
              name: 'add one',
              value: p.name,
              sub: p.id,
              strength: selectedTile === p.id ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
            })),
          ]} />

        {showGlobal ? (
          <SettingsSection title="Global">
            <SettingRow id="tuning-label" label="Guards tuning" note="how readily the guards’ words change on their own">
              <SettingSelect id="tuning" labelledBy="tuning-label" value={draft.tuning} width="w-[220px]"
                onValueChange={(tuning) => update({ tuning: tuning as TuningLevel })}
                options={TUNING_LEVELS.map((level) => ({ id: level, label: TUNING_LABELS[level] }))} />
            </SettingRow>
            {current.keyword !== undefined && (
              <SettingRow id="keyword-label" label="Rule keyword" note={keywordBad ? KEYWORD_NOTE : 'the word that makes a rule of a prompt'} error={errors.keyword}>
                <SettingInput id="keyword" aria-labelledby="keyword-label" autoComplete="off" spellCheck={false} maxLength={KEYWORD_MAX_LENGTH}
                  placeholder={current.keyword} aria-invalid={Boolean(errors.keyword) || undefined}
                  value={draft.keyword} onChange={(e) => update({ keyword: e.target.value })} />
              </SettingRow>
            )}
            <SettingToggle id="batches-extract" label="Message Batches"
              note={`extraction at half price, results at the next checkpoint; with ${batchProviders || 'a provider that has batches'}`}
              on={draft.batchesExtract} onChange={(on) => update({ batchesExtract: on })} />
            <SettingToggle id="include-headless" label="Headless runs"
              note="treat claude -p runs as sessions (scratchpads never count)"
              on={draft.includeHeadless} onChange={(on) => update({ includeHeadless: on })} />
            <SettingToggle id="write-rules" label="Rules in your tools"
              note="plug the learned rules into your coding tools (off: still learning)"
              on={draft.writeRules} onChange={(on) => update({ writeRules: on })} />
          </SettingsSection>
        ) : (
          <SettingsSection title={cardTitle}>
            {provider?.requires_url && (
              <SettingRow id="endpoint-name-label" label="Name" note="the card’s name in the strip">
                <SettingInput id="endpoint-name" aria-labelledby="endpoint-name-label" autoComplete="off" spellCheck={false}
                  placeholder="a name for this endpoint" value={draft.endpointName} onChange={(e) => update({ endpointName: e.target.value })} />
              </SettingRow>
            )}
            <SettingRow id="model-label" label="Model" note={provider && provider.models.length === 0 ? 'the model’s name at the endpoint' : 'the model the extraction and the synthesis use'} error={errors.model}>
              {/* a provider with no model list (a custom endpoint) takes the model's name typed, as the CLI asks it */}
              {provider && provider.models.length === 0 ? (
                <SettingInput id="model" aria-labelledby="model-label" autoComplete="off" spellCheck={false} aria-invalid={Boolean(errors.model) || undefined}
                  placeholder="the model’s name" value={draft.model} onChange={(e) => update({ model: e.target.value })} />
              ) : (
                <SettingSelect id="model" labelledBy="model-label" value={draft.model} width="w-[280px]"
                  onValueChange={(model) => update({ model })} options={models} />
              )}
            </SettingRow>
            {(provider?.requires_url || provider?.default_url) && (
              <SettingRow id="api-url-label" label="API URL" note={provider.default_url ? `the server’s address (${provider.default_url} unless changed)` : 'the endpoint’s address'} error={errors.apiUrl}>
                <SettingInput id="api-url" aria-labelledby="api-url-label" type="url" autoComplete="off" spellCheck={false} aria-invalid={Boolean(errors.apiUrl) || undefined}
                  placeholder="the endpoint’s URL" value={draft.apiUrl} onChange={(e) => update({ apiUrl: e.target.value })} />
              </SettingRow>
            )}
            {(provider?.requires_key || provider?.requires_url) && (
              <SettingRow id="api-key-label" label="API key" note={provider?.requires_key ? (current.api_key_set ? `set, ends in ${current.api_key_hint}` : 'not set') : 'only if the endpoint wants one'} error={errors.apiKey}>
                <SettingInput id="api-key" aria-labelledby="api-key-label" type="password" autoComplete="off" aria-invalid={Boolean(errors.apiKey) || undefined}
                  placeholder={current.api_key_set ? `set, ends in ${current.api_key_hint}` : 'not set'}
                  value={draft.apiKey} onChange={(e) => update({ apiKey: e.target.value })} />
              </SettingRow>
            )}
          </SettingsSection>
        )}
        {/* a saved endpoint's Delete under its container (operator 2026-11-06), the button alone at the right */}
        {!showGlobal && pickedEndpoint && (
          <div className="flex justify-end px-4 py-3">
            <Button className={deleteButtonClasses} disabled={save.isPending} onClick={() => setConfirmRemove(pickedEndpoint)}>Delete endpoint</Button>
          </div>
        )}
      </div>
      {save.error && <p className="p-4 text-sm text-fg-muted gutter">Not saved: {save.error.message}</p>}
      {/* Machine's delete confirmation (conditionCard: the name in the question, "cannot be undone", Cancel, a red Delete) */}
      <Dialog open={confirmRemove !== null} onOpenChange={(open) => { if (!open) setConfirmRemove(null); }}>
        <DialogContent onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>Delete endpoint</DialogTitle>
            <DialogDescription>
              Remove <span className="font-medium text-fg-muted">{confirmRemove?.name}</span> from your endpoints? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {/* the page's own pair: Cancel as Discard is, Delete in the labels' rose */}
            <Button variant="secondary" className={buttonClasses} onClick={() => setConfirmRemove(null)}>Cancel</Button>
            <Button className={deleteButtonClasses} onClick={() => { if (confirmRemove) remove(confirmRemove); }}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
