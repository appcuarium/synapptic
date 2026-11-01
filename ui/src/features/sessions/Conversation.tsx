// One session's turns, with the mockup's classes (renderTurn): your turns right, the agent's left, tool calls
// as chips above the text, compactions as a dashed marker. Text is rendered as elements, never as HTML.
import { cn } from '@/lib/cn';
import { fencedParts, formatCount, formatTime, inlineParts, toolChip } from './format';
import type { Block, Turn, Usage } from './types';

type TextBlock = Extract<Block, { type: 'text' }>;
type ToolBlock = Extract<Block, { type: 'tool_use' }>;
type CompactionBlock = Extract<Block, { type: 'compaction' }>;

function usageLabel(usage: Usage): string {
  return `${formatCount(usage.input_tokens)} in / ${formatCount(usage.output_tokens)} out / ${formatCount(usage.cache_read)} cache`;
}

function Prose({ text }: { text: string }) {
  return (
    <span className="whitespace-pre-wrap break-words">
      {inlineParts(text).map((part, i) => {
        if (part.kind === 'code') return <code key={i} className="bg-surface px-1 py-px rounded-[3px] text-[13px]">{part.text}</code>;
        if (part.kind === 'bold') return <strong key={i} className="text-fg">{part.text}</strong>;
        return part.text;
      })}
    </span>
  );
}

function Text({ text }: { text: string }) {
  return (
    <>
      {fencedParts(text.trim()).map((part, i) => (part.kind === 'code'
        ? <pre key={i} className="bg-surface p-2 rounded-sm overflow-x-auto my-2 text-[13px]"><code>{part.text}</code></pre>
        : <Prose key={i} text={part.text} />))}
    </>
  );
}

function ToolChip({ tool }: { tool: ToolBlock }) {
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 mr-1 mb-1 rounded-full border border-line bg-surface text-[11px] text-fg-muted">
      <span className="font-semibold">{tool.name}</span>
      <span className="text-fg-faint">{toolChip(tool.summary)}</span>
    </span>
  );
}

function Compaction({ block, timestamp }: { block: CompactionBlock; timestamp: string }) {
  const meta = [block.trigger, `${formatCount(block.pre_tokens)} tokens`, formatTime(timestamp)].filter(Boolean).join(' · ');
  return (
    <div className="my-6 px-4 py-2.5 rounded-lg border border-dashed border-control-hover bg-surface text-center text-xs text-fg-muted">
      <span className="font-semibold">context compacted</span>
      <span className="ml-2 text-fg-faint">{meta}</span>
    </div>
  );
}

export function TurnView({ turn }: { turn: Turn }) {
  const compaction = turn.blocks.find((b): b is CompactionBlock => b.type === 'compaction');
  if (compaction) return <Compaction block={compaction} timestamp={turn.timestamp} />;
  const user = turn.role === 'user';
  const tools = turn.blocks.filter((b): b is ToolBlock => b.type === 'tool_use');
  const texts = turn.blocks.filter((b): b is TextBlock => b.type === 'text');
  const header = [user ? 'You' : 'Agent', formatTime(turn.timestamp), turn.model].filter(Boolean).join(' · ');
  return (
    <div className={cn('mb-4 max-w-[85%]', user ? 'ml-auto' : 'mr-auto')}>
      <div className={cn('text-[11px] text-fg-faint mb-1 flex gap-2 items-center', user && 'justify-end')}>
        {header}
        {turn.usage && <span className="text-[10px] text-fg-faint bg-surface px-1.5 py-0.5 rounded">{usageLabel(turn.usage)}</span>}
      </div>
      <div className={cn('px-4 py-3 rounded-xl border border-line text-sm leading-relaxed text-fg-muted',
        user ? 'bg-surface-2 rounded-br-sm' : 'bg-surface rounded-bl-sm')}>
        {tools.length > 0 && <div className="my-1">{tools.map((tool, i) => <ToolChip key={i} tool={tool} />)}</div>}
        {texts.length > 0 && <Text text={texts.map((block) => block.text.trim()).join('\n\n')} />}
      </div>
    </div>
  );
}
