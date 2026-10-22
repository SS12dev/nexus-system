import { useStore } from "../store/chat";

/** The product's own name, shown before anyone signs in (login page, tab title). */
export const APP_NAME = "Nexus";

/** What an assistant is called until its user picks a name. */
export const DEFAULT_ASSISTANT_NAME = "Jarvis";

/** Names offered on the naming screen; anyone can type their own. */
export const SUGGESTED_NAMES = ["Jarvis", "Orion", "Friday", "Nova", "Atlas", "Sage"];

export const ASSISTANT_NAME_MAX = 24;

/** Mirrors the API's rule: letters, numbers, spaces, dots, hyphens and apostrophes, starting
 * with a letter or number. Returns the tidied name, or null when it isn't allowed. */
export function cleanAssistantName(raw: string): string | null {
  const name = raw.split(/\s+/).filter(Boolean).join(" ");
  if (!name || name.length > ASSISTANT_NAME_MAX) return null;
  return /^[\p{L}\p{N}][\p{L}\p{N} .'-]*$/u.test(name) ? name : null;
}

/** The name the signed-in user gave their assistant. */
export function useAssistantName(): string {
  return useStore((s) => s.user?.assistant_name) || DEFAULT_ASSISTANT_NAME;
}
