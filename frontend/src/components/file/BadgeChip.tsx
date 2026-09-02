export type Badge = { letter: string; color: string };

export function BadgeChip({ b }: { b: Badge }) {
  return (
    <span className="tree-badge" style={{ background: b.color }} title={`git: ${b.letter}`}>
      {b.letter}
    </span>
  );
}
