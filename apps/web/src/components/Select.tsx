import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

type Size = "xs" | "sm" | "md";

/** Box sizes, matched to the inputs they sit beside: `md` to the py-2 text inputs in forms,
 * `sm` to toolbar buttons and filters, `xs` to small chips inside list rows. Text is 16px on
 * phones whatever the size, or iOS zooms the page when the picker opens. */
const SIZE: Record<Size, { box: string; bg: string; icon: number; right: string }> = {
  md: { box: "rounded-md py-2 pl-3 pr-9 text-base", bg: "bg-panel", icon: 16, right: "right-3" },
  sm: { box: "rounded-md py-1 pl-2.5 pr-8 text-base sm:text-sm", bg: "bg-ink", icon: 14, right: "right-2.5" },
  xs: { box: "rounded py-0.5 pl-1.5 pr-5 text-base sm:text-xs", bg: "bg-ink", icon: 11, right: "right-1" },
};

/** The one dropdown used across the app. It stays a native select, so phones get their own
 * picker and the keyboard works as everywhere else, but it is drawn like the other inputs:
 * our own chevron inside the box (the browser's arrow can't crowd the text), a dark option
 * list, a focus ring and a hover state. `tone` replaces the default border and background,
 * e.g. for the coloured memory-type chips. */
export function Select({
  className = "",
  size = "sm",
  tone,
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> & { size?: Size; tone?: string }) {
  const s = SIZE[size];
  return (
    <span className={`relative inline-flex min-w-0 ${className}`}>
      <select
        {...props}
        className={`w-full min-w-0 cursor-pointer appearance-none truncate [color-scheme:dark] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-400 disabled:cursor-default disabled:opacity-50 ${s.box} ${
          tone ?? `border border-edge hover:border-slate-600 ${s.bg}`
        }`}
      />
      <ChevronDown
        size={s.icon}
        className={`pointer-events-none absolute top-1/2 -translate-y-1/2 opacity-70 ${s.right}`}
        aria-hidden
      />
    </span>
  );
}
