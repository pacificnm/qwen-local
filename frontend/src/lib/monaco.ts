/**
 * Monaco offline setup: bundle the editor + language workers with Vite
 * (no CDN) and hand the instance to @monaco-editor/react so its loader
 * never reaches the network. Import this module before mounting <Editor>.
 */
import * as monaco from "monaco-editor";
// monaco-editor 0.56 exports map: "./X" resolves to "esm/vs/X.js", so worker
// subpaths drop the "esm/vs/" prefix.
import editorWorker from "monaco-editor/editor/editor.worker?worker";
import cssWorker from "monaco-editor/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/language/html/html.worker?worker";
import jsonWorker from "monaco-editor/language/json/json.worker?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker?worker";
import { loader } from "@monaco-editor/react";

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

loader.config({ monaco });

const EXT_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  rs: "rust",
  go: "go",
  java: "java",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cs: "csharp",
  rb: "ruby",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  sql: "sql",
  html: "html",
  css: "css",
  scss: "scss",
  less: "less",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  md: "markdown",
  dockerfile: "dockerfile",
};

/** Map a file path or chat-snippet language hint to a Monaco language id. */
export function detectLanguage(pathOrLang: string): string {
  const clean = pathOrLang.trim().toLowerCase();
  if (EXT_LANG[clean]) return EXT_LANG[clean];
  const base = clean.split("/").pop() ?? clean;
  if (base === "dockerfile") return "dockerfile";
  const ext = base.includes(".") ? base.split(".").pop() ?? "" : "";
  return EXT_LANG[ext] ?? "plaintext";
}

export { monaco };
