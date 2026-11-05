// Tabs as Machine's strategy edit panel lays them out (layerTabs.component.js, 2026-11-05): a 32px row on
// the page surface with a line above and below, each tab 12px medium text with a divider on its right, the
// active one raised to the control surface; the panels sit flush under the row (no top margin). Radix Tabs
// under it, the shadcn way, with Machine's slate classes mapped to this app's theme tokens.
"use client"

import * as React from "react"
import { cn } from "@/lib/cn"
import { Tabs as TabsPrimitive } from "radix-ui"

const Tabs = TabsPrimitive.Root

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      "flex h-8 items-center justify-start bg-page border-t border-b border-line rounded-none p-0 gap-0 overflow-x-auto strip-scroll shrink-0 w-full",
      className,
    )}
    {...props}
  />
))
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex items-center gap-1 whitespace-nowrap h-8 px-2.5 py-0 rounded-none border-r border-line text-xs font-medium transition-all",
      "text-fg-faint hover:bg-control-hover hover:text-fg-subtle data-[state=active]:bg-control data-[state=active]:text-fg-muted",
      "focus-visible:outline-none focus-visible:ring-0 disabled:pointer-events-none disabled:opacity-50 cursor-pointer",
      className,
    )}
    {...props}
  />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("mt-0 focus-visible:outline-none focus-visible:ring-0 data-[state=inactive]:hidden", className)}
    {...props}
  />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

export { Tabs, TabsList, TabsTrigger, TabsContent }
