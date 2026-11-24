/* A native <select> styled like the inputs: keyboard, screen readers and phones handle it
   natively (role pickers in the members and audit pages). */

import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1 text-sm shadow-xs outline-none",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        "disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground",
        className)}
      {...props}
    />
  )
}
