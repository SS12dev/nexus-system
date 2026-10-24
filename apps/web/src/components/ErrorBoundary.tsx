import { AlertTriangle } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { reportClientError } from "../lib/api";

/** Shows what broke, instead of a blank screen, when a page throws while rendering. A new
 * `resetKey` (the route) clears it, so moving to another page recovers without a reload. */
export class ErrorBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { error: Error | null; key?: string }
> {
  state: { error: Error | null; key?: string } = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: { resetKey?: string }, state: { error: Error | null; key?: string }) {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
    reportClientError(error, location.pathname);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl rounded-lg border border-rose-500/40 bg-rose-500/10 p-4">
          <h1 className="flex items-center gap-2 font-medium text-rose-200">
            <AlertTriangle size={16} /> This page hit an error
          </h1>
          <p className="mt-1 text-sm text-slate-300">
            Other pages still work. The details were sent to the owner's Admin page; copy them below if you report it.
          </p>
          <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-ink p-3 text-xs whitespace-pre-wrap text-slate-300">
            {`${error.name}: ${error.message}\n${error.stack ?? ""}`}
          </pre>
          <button
            onClick={() => location.reload()}
            className="mt-3 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
