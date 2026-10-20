import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

/** A native select drawn like the other inputs: our own chevron inside the box, so the
 * browser's arrow can't spill over the edge, and a dark option list. `large` matches the
 * py-2 text inputs in forms. */
export function Select({
  className = "",
  large = false,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { large?: boolean }) {
  return (
    <span className={`relative inline-flex ${className}`}>
      <select
        {...props}
        className={`w-full appearance-none rounded-md border border-edge [color-scheme:dark] disabled:opacity-50 ${
          large ? "bg-panel py-2 pl-3 pr-8" : "bg-ink py-1 pl-2 pr-7 text-sm"
        }`}
      />
      <ChevronDown
        size={14}
        className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-slate-400 ${large ? "right-3" : "right-2"}`}
        aria-hidden
      />
    </span>
  );
}
