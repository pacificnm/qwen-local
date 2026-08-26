/** Tiny pub/sub so the file tree can drop a file path into the chat composer
 * without coupling the two components (the draft text lives in ChatPane state). */
type Listener = (path: string) => void;

const listeners = new Set<Listener>();

export function onFileMention(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function addFileToChat(path: string): void {
  for (const fn of [...listeners]) fn(path);
}
