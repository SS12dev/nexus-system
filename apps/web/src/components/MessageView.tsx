import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../lib/types";
import { FileChip } from "./FileChip";
import { ToolSteps } from "./ToolSteps";
import { useAssistantName } from "../lib/assistant";

export function MessageView({ message, activity }: { message: Message; activity?: string | null }) {
  const assistant = useAssistantName();
  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-2">
        {!!message.attachments?.length && (
          <div className="flex max-w-[85%] flex-wrap justify-end gap-2">
            {message.attachments.map((f) => (
              <FileChip key={f.id} file={f} status={f.status} />
            ))}
          </div>
        )}
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-sky-600/80 px-4 py-2.5">
          {message.content}
        </div>
      </div>
    );
  }
  const streaming = message.status === "streaming";
  const waiting = streaming && !message.content;
  return (
    <div className="max-w-full">
      {!!message.steps?.length && <ToolSteps steps={message.steps} live={streaming} />}
      {waiting ? (
        <div className="flex items-center gap-3 py-3" aria-label={`${assistant} is thinking`}>
          <div className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="size-2 animate-bounce rounded-full bg-sky-400"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            ))}
          </div>
          {/* With steps listed above, the current one already shows there. */}
          {activity && !message.steps?.length && <span className="text-sm text-slate-400">{activity}…</span>}
        </div>
      ) : (
        <div className="prose prose-invert max-w-none prose-pre:bg-panel prose-pre:border prose-pre:border-edge">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
      )}
      {message.status === "failed" && (
        <p className="mt-1 text-sm text-rose-400">{message.error ?? `${assistant} couldn't answer.`}</p>
      )}
      {message.status === "complete" && message.output_tokens != null && (
        <p className="mt-1 text-xs text-slate-600">
          {message.model} · {message.input_tokens} in / {message.output_tokens} out
        </p>
      )}
    </div>
  );
}
