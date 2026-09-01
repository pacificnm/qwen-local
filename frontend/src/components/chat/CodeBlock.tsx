import type { ReactNode } from "react";
import { useEditor } from "../../store/editor";
import { nodeText } from "./NodeText";

function codeClassOf(children: ReactNode): string {
  if (Array.isArray(children)) {
    for (const c of children) {
      const hit = codeClassOf(c);
      if (hit) return hit;
    }
    return "";
  }
  if (children && typeof children === "object" && "props" in children) {
    const p = (children as { type?: unknown; props?: { className?: string } }).props;
    if (p?.className) return p.className;
  }
  return "";
}

/** Fenced code block wrapped with a language tag + "Open in editor" action. */
export function CodeBlock({ children }: { children?: ReactNode }) {
  const openSnippet = useEditor((s) => s.openSnippet);
  const langMatch = /language-([\w+-]+)/.exec(codeClassOf(children));
  const lang = langMatch?.[1] ?? "";
  const text = nodeText(children).replace(/\n+$/, "");
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span className="codeblock-lang">{lang || "code"}</span>
        <button
          type="button"
          className="codeblock-open"
          onClick={() => openSnippet(text, lang)}
          title="Open this code in the editor pane"
        >
          ⧉ Open in editor
        </button>
      </div>
      <div className="codeblock-body">{children}</div>
    </div>
  );
}
