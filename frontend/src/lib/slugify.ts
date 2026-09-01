/** Derive a short git-branch-safe slug from a file path. */
export function slugify(input: string): string {
  const base = input.split("/").pop() ?? input;
  const slug = base
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "edits";
}
