export interface ClarificationQuestion {
  question: string;
  options: string[];
  recommended: number;
}

export interface Clarification {
  content: string;
  questions: ClarificationQuestion[];
}

const MARKER = /<!--\s*nexus-clarification\s*([\s\S]*?)\s*-->/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseClarification(content: string): Clarification | null {
  const match = MARKER.exec(content);
  if (!match) return null;
  const visibleContent = content.replace(MARKER, "").trim();
  try {
    const parsed: unknown = JSON.parse(match[1]);
    if (!isRecord(parsed) || !Array.isArray(parsed.questions)) return { content: visibleContent, questions: [] };
    const questions = parsed.questions.slice(0, 3).flatMap((entry): ClarificationQuestion[] => {
      if (!isRecord(entry) || typeof entry.question !== "string" || !Array.isArray(entry.options))
        return [];
      const options = entry.options
        .filter((option): option is string => typeof option === "string" && option.trim().length > 0)
        .slice(0, 4)
        .map((option) => option.trim().slice(0, 160));
      if (!entry.question.trim() || options.length < 2) return [];
      const recommended =
        typeof entry.recommended === "number" &&
        Number.isInteger(entry.recommended) &&
        entry.recommended >= 0 &&
        entry.recommended < options.length
          ? entry.recommended
          : 0;
      return [{ question: entry.question.trim().slice(0, 500), options, recommended }];
    });
    return { content: visibleContent, questions };
  } catch {
    return { content: visibleContent, questions: [] };
  }
}
