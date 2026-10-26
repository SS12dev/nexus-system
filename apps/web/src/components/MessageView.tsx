import type { Message } from "../lib/types";
import { isPicture } from "../lib/format";
import { FileChip, PictureThumb } from "./FileChip";
import { LinkChip } from "./LinkChip";
import { Markdown } from "./Markdown";
import { RecallChips } from "./RecallChips";
import { ToolSteps } from "./ToolSteps";
import { useAssistantName } from "../lib/assistant";
import { parseClarification } from "../lib/clarification";

export function MessageView({ message, activity }: { message: Message; activity?: string | null }) {
  const assistant = useAssistantName();
  if (message.role === "user") {
    const pictures = (message.attachments ?? []).filter((f) => isPicture(f.name));
    const others = (message.attachments ?? []).filter((f) => !isPicture(f.name));
    return (
      <div className="flex flex-col items-end gap-2">
        {!!pictures.length && (
          <div className="flex max-w-[85%] flex-wrap justify-end gap-2">
            {pictures.map((f) => (
              <PictureThumb key={f.id} file={f} status={f.status} />
            ))}
          </div>
        )}
        {!!others.length && (
          <div className="flex max-w-[85%] flex-wrap justify-end gap-2">
            {others.map((f) => (
              <FileChip key={f.id} file={f} status={f.status} />
            ))}
          </div>
        )}
        {/* A screenshot can be sent on its own, with no words. */}
        {!!message.content && (
          <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-sky-600/80 px-4 py-2.5">
            {message.content}
          </div>
        )}
      </div>
    );
  }
  const streaming = message.status === "streaming";
  const waiting = streaming && !message.content;
  const clarification = parseClarification(message.content);
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
        <Markdown>{clarification?.content ?? message.content}</Markdown>
      )}
      {/* Files Nexus made for this reply, and links it saved (a repo, an app, a Google Doc). */}
      {(!!message.attachments?.length || !!message.links?.length) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {message.attachments?.map((f) => (
            <FileChip key={f.id} file={f} status={f.status} />
          ))}
          {message.links?.map((l) => (
            <LinkChip key={l.id} link={l} />
          ))}
        </div>
      )}
      {message.recalled && <RecallChips recalled={message.recalled} />}
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
