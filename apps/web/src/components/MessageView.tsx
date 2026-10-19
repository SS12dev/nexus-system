import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../lib/types";

export function MessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-sky-600/80 px-4 py-2.5">
          {message.content}
        </div>
      </div>
    );
  }
  const waiting = message.status === "streaming" && !message.content;
  return (
    <div className="max-w-full">
      {waiting ? (
        <div className="flex gap-1 py-3" aria-label="Jarvis is thinking">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="size-2 animate-bounce rounded-full bg-sky-400"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
      ) : (
        <div className="prose prose-invert max-w-none prose-pre:bg-panel prose-pre:border prose-pre:border-edge">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
      )}
      {message.status === "failed" && (
        <p className="mt-1 text-sm text-rose-400">{message.error ?? "Jarvis couldn't answer."}</p>
      )}
      {message.status === "complete" && message.output_tokens != null && (
        <p className="mt-1 text-xs text-slate-600">
          {message.model} · {message.input_tokens} in / {message.output_tokens} out
        </p>
      )}
    </div>
  );
}
