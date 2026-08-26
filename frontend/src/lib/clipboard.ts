/** Clipboard helpers that degrade gracefully outside a secure context
 * (the app is served over LAN http, where `navigator.clipboard` may be absent). */

export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the legacy path below
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Read text from the clipboard; null if the API is unavailable or denied. */
export async function readText(): Promise<string | null> {
  if (navigator.clipboard?.readText) {
    try {
      return await navigator.clipboard.readText();
    } catch {
      return null;
    }
  }
  return null;
}
