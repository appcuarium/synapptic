import { describe, expect, it } from 'vitest';
import { fencedParts, formatCount, inlineParts, toolChip } from './format';

describe('formatCount', () => {
  it('shortens thousands and millions as the mockup does', () => {
    expect(formatCount(940)).toBe('940');
    expect(formatCount(1200)).toBe('1.2k');
    expect(formatCount(212_400)).toBe('212.4k');
    expect(formatCount(1_675_463)).toBe('1.7M');
  });
});

describe('fencedParts', () => {
  it('splits code fences out of prose and keeps the code verbatim', () => {
    expect(fencedParts('Run this:\n```bash\nsynapptic relay enable\nsynapptic relay start -d\n```\nthen open it.')).toEqual([
      { kind: 'prose', text: 'Run this:\n' },
      { kind: 'code', text: 'synapptic relay enable\nsynapptic relay start -d\n' },
      { kind: 'prose', text: '\nthen open it.' },
    ]);
  });

  it('treats an unclosed fence as prose', () => {
    expect(fencedParts('a ``` b')).toEqual([{ kind: 'prose', text: 'a ``` b' }]);
  });
});

describe('inlineParts', () => {
  it('finds inline code and bold, leaving the rest as text', () => {
    expect(inlineParts('Run `relay start` at **http://127.0.0.1:5100**.')).toEqual([
      { kind: 'text', text: 'Run ' },
      { kind: 'code', text: 'relay start' },
      { kind: 'text', text: ' at ' },
      { kind: 'bold', text: 'http://127.0.0.1:5100' },
      { kind: 'text', text: '.' },
    ]);
  });
});

describe('toolChip', () => {
  it('shows the description when the call has one (mockup: "Bash · Find the relay entry point")', () => {
    expect(toolChip('command: ls src/synapptic/relay, description: Find the relay entry point')).toBe('Find the relay entry point');
  });

  it('otherwise shows the first argument value, not the key, cut to a chip', () => {
    expect(toolChip('file_path: /a/b.py, offset: 10')).toBe('/a/b.py');
    expect(toolChip('')).toBe('');
    expect(toolChip(`file_path: ${'x'.repeat(100)}`)).toHaveLength(60);
  });
});
