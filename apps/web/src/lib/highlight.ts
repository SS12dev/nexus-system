import type { HLJSApi } from "highlight.js";

let loading: Promise<HLJSApi> | null = null;

/** highlight.js with only the languages the preview needs, loaded on first use. */
export function loadHighlighter(): Promise<HLJSApi> {
  loading ??= (async () => {
    const [core, python, javascript, typescript, json, bash, yaml, xml, css, sql, ini, plaintext] =
      await Promise.all([
        import("highlight.js/lib/core"),
        import("highlight.js/lib/languages/python"),
        import("highlight.js/lib/languages/javascript"),
        import("highlight.js/lib/languages/typescript"),
        import("highlight.js/lib/languages/json"),
        import("highlight.js/lib/languages/bash"),
        import("highlight.js/lib/languages/yaml"),
        import("highlight.js/lib/languages/xml"),
        import("highlight.js/lib/languages/css"),
        import("highlight.js/lib/languages/sql"),
        import("highlight.js/lib/languages/ini"),
        import("highlight.js/lib/languages/plaintext"),
      ]);
    const hljs = core.default;
    const langs = { python, javascript, typescript, json, bash, yaml, xml, css, sql, ini, plaintext };
    for (const [name, mod] of Object.entries(langs)) hljs.registerLanguage(name, mod.default);
    return hljs;
  })();
  return loading;
}
