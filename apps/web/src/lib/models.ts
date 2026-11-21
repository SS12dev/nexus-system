import { useStore } from "../store/chat";
import type { ModelName } from "./types";

/** "pro", "nexus-pro" or "openai/gpt-5.4" -> the alias key used in /api/me models. */
export function aliasOf(tierOrModel: string): string {
  return tierOrModel.startsWith("nexus-") ? tierOrModel : `nexus-${tierOrModel}`;
}

/** The real model's name for a tier or alias, e.g. "pro" -> "GPT-5.4". Falls back to what it was given. */
export function modelLabel(models: Record<string, ModelName> | undefined, tierOrModel: string | null | undefined): string {
  if (!tierOrModel) return "";
  return models?.[aliasOf(tierOrModel)]?.label ?? tierOrModel;
}

/** A function naming the real model behind a tier or alias, from the signed-in user's /api/me. */
export function useModelLabel(): (tierOrModel: string | null | undefined) => string {
  const models = useStore((s) => s.user?.models);
  return (t) => modelLabel(models, t);
}
