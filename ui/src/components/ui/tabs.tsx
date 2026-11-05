// Tabs as Machine's strategy edit panel lays them out (layerTabs.component.js, 2026-11-05): a 32px row on
// the page surface with a line above and below, each tab 12px medium text with a divider on its right, the
// active one raised to the control surface; the panels sit flush under the row (no top margin). Radix Tabs
// under it, the shadcn way, with Machine's slate classes mapped to this app's theme tokens.
// The bar wears the labels' dark teal (TileLabel: the same fill in both themes, teal-100 text; operator
// 2026-11-06: "the dark teal color scheme for the tabs bar only"); the active tab sits on the 800 shade
// the label tones use, the others in teal-100 at reduced strength.
"use client"

import * as React from "react"
import { tileLabelStyle } from "@/components/machine/TileLabel"
import { cn } from "@/lib/cn"
import { Tabs as TabsPrimitive } from "radix-ui"

const Tabs = TabsPrimitive.Root

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, style, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    style={{ ...tileLabelStyle, ...style }}
    className={cn(
      "flex h-8 items-center justify-start border-t border-b border-line rounded-none p-0 gap-0 overflow-x-auto strip-scroll shrink-0 w-full",
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
      "text-teal-100/60 hover:bg-teal-800/60 hover:text-teal-100 data-[state=active]:bg-teal-800 data-[state=active]:text-teal-100",
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
