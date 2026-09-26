const STORM_COLORS = [
  '#1677ff',
  '#ef4444',
  '#10b981',
  '#f59e0b',
  '#8b5cf6',
  '#ec4899',
  '#06b6d4',
  '#f97316',
  '#84cc16',
  '#6366f1',
]

export function getStormColor(index: number): string {
  return STORM_COLORS[
    index % STORM_COLORS.length
  ]
}