import { AssistantNameForm } from "../components/AssistantNameForm";
import { Orb } from "../components/Orb";
import { useStore } from "../store/chat";

/** Shown once, right after someone first signs in: they name their own assistant. */
export function NameAssistantPage() {
  const user = useStore((s) => s.user)!;
  const first = user.name?.split(" ")[0];
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto px-6">
      <div className="w-full max-w-md text-center">
        <div className="mb-6 flex justify-center">
          <Orb state="idle" size={96} />
        </div>
        <h1 className="mb-1 text-2xl font-semibold">
          {first ? `Welcome, ${first}.` : "Welcome."} What should your assistant be called?
        </h1>
        <p className="mb-6 text-sm text-slate-400">
          Pick one or type your own. It's yours alone, and you can change it later in Settings.
        </p>
        <div className="text-left">
          <AssistantNameForm submitLabel="Continue" />
        </div>
      </div>
    </div>
  );
}
