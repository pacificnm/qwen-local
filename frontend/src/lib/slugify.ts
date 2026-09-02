/** Derive a short git-branch-safe slug from free text (e.g. a branch description). */
export function slugifyText(input: string): string {
  const slug = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "branch";
}
