import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Wide tables and code blocks scroll sideways inside their own box instead of spilling past
// the panel; links leave the app in a new tab.
const COMPONENTS: Components = {
  table: ({ node: _node, ...props }) => (
    <div className="not-prose my-4 max-w-full overflow-x-auto rounded-md border border-edge">
      <table className="w-full border-collapse text-left text-sm" {...props} />
    </div>
  ),
  th: ({ node: _node, ...props }) => (
    <th className="min-w-24 border-b border-edge bg-panel px-3 py-2 align-bottom font-medium whitespace-nowrap" {...props} />
  ),
  td: ({ node: _node, ...props }) => (
    <td className="min-w-24 border-t border-edge/60 px-3 py-2 align-top text-slate-300" {...props} />
  ),
  pre: ({ node: _node, ...props }) => <pre className="max-w-full overflow-x-auto" {...props} />,
  a: ({ node: _node, href, ...props }) =>
    href?.startsWith("#") || href?.startsWith("/") ? (
      <a href={href} {...props} />
    ) : (
      <a href={href} target="_blank" rel="noreferrer" {...props} />
    ),
};

/** Markdown with GitHub tables, task lists and strikethrough, styled for the dark theme. */
export function Markdown({ children, size = "base", className = "" }: { children: string; size?: "sm" | "base"; className?: string }) {
  return (
    <div
      className={`prose prose-invert max-w-none min-w-0 break-words prose-pre:border prose-pre:border-edge prose-pre:bg-panel prose-code:rounded prose-code:bg-edge/60 prose-code:px-1 prose-code:py-0.5 prose-code:font-normal prose-code:before:content-none prose-code:after:content-none [&_pre_code]:bg-transparent [&_pre_code]:p-0 ${
        size === "sm" ? "prose-sm" : ""
      } ${className}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
