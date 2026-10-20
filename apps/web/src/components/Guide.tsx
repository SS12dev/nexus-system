import { ChevronDown, Info } from "lucide-react";
import type { ReactNode } from "react";

/** A folded "how this page works" panel under a page's heading. */
export function Guide({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group mb-6 rounded-lg border border-edge bg-panel/60 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-slate-300 select-none">
        <Info size={15} className="text-sky-300" />
        {title}
        <ChevronDown size={14} className="ml-auto transition-transform group-open:rotate-180" />
      </summary>
      <div className="grid gap-4 border-t border-edge px-4 py-3 text-slate-400 sm:grid-cols-2">
        {children}
      </div>
    </details>
  );
}

export const GuideHeading = ({ children }: { children: ReactNode }) => (
  <h3 className="mb-1 font-medium text-slate-200">{children}</h3>
);
