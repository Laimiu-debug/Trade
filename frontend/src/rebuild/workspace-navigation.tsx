export function WorkspaceNavigation<T extends string>({ label, current, items, onChange }: {
  label: string; current: T; items: ReadonlyArray<readonly [T, string]>; onChange: (next: T) => void
}) {
  return <nav className="workspace-navigation" aria-label={label}>{items.map(([key, title]) =>
    <button key={key} type="button" className="workspace-navigation-item" aria-current={current === key ? 'page' : undefined} onClick={() => onChange(key)}>{title}</button>)}</nav>
}
