import { Check, MessageCircleQuestion } from "lucide-react";
import { useState } from "react";
import type { ClarificationQuestion } from "../lib/clarification";

export function ClarificationCard({
  questions,
  disabled,
  onSubmit,
}: {
  questions: ClarificationQuestion[];
  disabled: boolean;
  onSubmit: (answers: string[]) => Promise<boolean>;
}) {
  const [answers, setAnswers] = useState<string[]>(() =>
    questions.map((question) => question.options[question.recommended] ?? ""),
  );
  const [custom, setCustom] = useState<boolean[]>(() => questions.map(() => false));
  const [customAnswers, setCustomAnswers] = useState<string[]>(() => questions.map(() => ""));
  const [submitting, setSubmitting] = useState(false);

  const values = questions.map((_, index) =>
    custom[index] ? customAnswers[index]?.trim() ?? "" : answers[index] ?? "",
  );
  const ready = values.every(Boolean);

  const submit = async () => {
    if (disabled || submitting || !ready) return;
    setSubmitting(true);
    try {
      await onSubmit(questions.map((question, index) => `${question.question}\nAnswer: ${values[index]}`));
    } finally {
      setSubmitting(false);
    }
  };

  if (!questions.length) return null;
  return (
    <section className="mt-3 rounded-xl border border-sky-400/30 bg-sky-950/20 p-3" aria-label="Clarifying questions">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-sky-200">
        <MessageCircleQuestion size={16} />
        A few choices before I start
        {disabled && <span className="ml-auto text-xs font-normal text-slate-500">Answered</span>}
      </div>
      <div className="space-y-4">
        {questions.map((question, index) => (
          <fieldset key={`${question.question}-${index}`} disabled={disabled || submitting}>
            <legend className="mb-2 text-sm text-slate-200">{question.question}</legend>
            <div className="flex flex-wrap gap-2">
              {question.options.map((option, optionIndex) => {
                const selected = !custom[index] && answers[index] === option;
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => {
                      setCustom((current) => current.map((value, i) => (i === index ? false : value)));
                      setAnswers((current) => current.map((value, i) => (i === index ? option : value)));
                    }}
                    className={`rounded-lg border px-3 py-2 text-left text-sm transition ${
                      selected
                        ? "border-sky-400 bg-sky-500/15 text-sky-100"
                        : "border-edge bg-panel text-slate-300 hover:border-sky-400/50"
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      {selected && <Check size={13} />}
                      {option}
                      {optionIndex === question.recommended && (
                        <span className="text-xs text-sky-300">Recommended</span>
                      )}
                    </span>
                  </button>
                );
              })}
              <button
                type="button"
                aria-pressed={custom[index]}
                onClick={() =>
                  setCustom((current) => current.map((value, i) => (i === index ? true : value)))
                }
                className={`rounded-lg border px-3 py-2 text-sm ${
                  custom[index] ? "border-sky-400 bg-sky-500/15 text-sky-100" : "border-edge text-slate-400"
                }`}
              >
                Other
              </button>
            </div>
            {custom[index] && (
              <input
                autoFocus
                value={customAnswers[index] ?? ""}
                onChange={(event) =>
                  setCustomAnswers((current) =>
                    current.map((value, i) => (i === index ? event.target.value : value)),
                  )
                }
                placeholder="Type your answer"
                className="mt-2 w-full rounded-md border border-edge bg-ink px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-400"
              />
            )}
          </fieldset>
        ))}
      </div>
      <button
        type="button"
        disabled={disabled || submitting || !ready}
        onClick={() => void submit()}
        className="mt-3 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
      >
        {submitting ? "Sending…" : disabled ? "Answered" : "Continue"}
      </button>
    </section>
  );
}
