import { CodeBlock } from "./CodeBlock";

/** Shared ReactMarkdown component overrides (used by both the static and
 * streaming assistant bodies). */
export const markdownComponents = { pre: CodeBlock };
