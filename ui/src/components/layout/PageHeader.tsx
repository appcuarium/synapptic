// The toolbar row every page starts with (mockup: h-14, title left, actions right).
import type { ReactNode } from 'react';

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="h-14 touch:h-16 flex items-center gap-2 border-b border-line gutter">
      <h1 className="text-base font-semibold text-fg shrink-0 mr-2">{title}</h1>
      {children}
    </div>
  );
}

// A page that is in the mockup but not wired to the server yet: says so instead of showing sample data.
export function NotBuiltYet({ title }: { title: string }) {
  return (
    <section className="flex flex-col">
      <PageHeader title={title} />
      <p className="p-4 text-sm text-fg-subtle gutter">Not built yet: this page is in the mockup and comes after Settings.</p>
    </section>
  );
}
