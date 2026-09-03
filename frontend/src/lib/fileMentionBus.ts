/** Tiny pub/sub so the file tree / editor can drop text into the chat composer
 * without coupling those components to it (the draft text lives in ChatPane state). */
type Listener = (text: string) => void;

const listeners = new Set<Listener>();

export function onFileMention(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function publish(text: string): void {
  for (const fn of [...listeners]) fn(text);
}

export function addFileToChat(path: string): void {
  publish(path);
}

/** Editor right-click → "Ask AI about Selection": the snippet as a fenced code
 * block, with the file path on its own line above it. */
export function addSnippetToChat(path: string, code: string, language: string): void {
  publish(`${path}\n\`\`\`${language}\n${code}\n\`\`\``);
}
