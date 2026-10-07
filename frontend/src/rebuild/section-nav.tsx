import { useEffect, useState } from 'react'
import { Icon, type IconName } from './workspace-icons'

export type SectionNavItem = { id: string; label: string; icon?: IconName }

/** Sticky in-page table of contents for long pages; highlights the section in view. */
export function SectionNav({ items, label = '本页目录' }: { items: SectionNavItem[]; label?: string }) {
  const [active, setActive] = useState(items[0]?.id ?? '')
  const ids = items.map(item => item.id).join('|')
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const visible = new Map<string, number>()
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.set(entry.target.id, entry.boundingClientRect.top)
        else visible.delete(entry.target.id)
      }
      const first = ids.split('|').find(id => visible.has(id))
      if (first) setActive(first)
    }, { rootMargin: '-72px 0px -55% 0px' })
    for (const id of ids.split('|')) {
      const element = document.getElementById(id)
      if (element) observer.observe(element)
    }
    return () => observer.disconnect()
  }, [ids])
  return <nav className="section-nav" aria-label={label}>
    <p className="section-nav-title">{label}</p>
    {items.map(item => <a key={item.id} href={`#${item.id}`} aria-current={active === item.id ? 'location' : undefined}
      onClick={event => {
        const target = document.getElementById(item.id)
        if (!target) return
        event.preventDefault()
        setActive(item.id)
        target.scrollIntoView?.({ behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
      }}>{item.icon && <Icon name={item.icon} />}<span>{item.label}</span></a>)}
  </nav>
}
