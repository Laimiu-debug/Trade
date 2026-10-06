"""Check legibility of shared light/dark design tokens (WCAG contrast ratios)."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_tokens import load_resolved  # noqa: E402


def luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(foreground: str, background: str) -> float:
    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + .05) / (a + .05)


def main() -> None:
    tokens = load_resolved()
    themes = tokens['themes']
    assert themes['light']['color'].keys() == themes['dark']['color'].keys(), 'Theme roles must match'
    checks = []
    for mode, values in themes.items():
        colors = values['color']
        pairs = [(text, bg, 4.5) for text in ('text.primary', 'text.secondary', 'text.muted')
                 for bg in ('bg.canvas', 'bg.surface', 'bg.subtle')]
        pairs += [('text.onPrimary', action, 4.5) for action in ('action.primary', 'action.hover', 'action.pressed')]
        pairs += [(f'{prefix}.{role}', f'{prefix}.{role}Bg', 4.5)
                  for prefix, roles in [('market', ('up', 'down')), ('status', ('success', 'warning', 'danger', 'info'))]
                  for role in roles]
        pairs += [('border.control', bg, 3) for bg in ('bg.canvas', 'bg.surface')]
        pairs += [(f'tone.{tone}', f'tone.{tone}Bg', 4.5) for tone in ('blue', 'green', 'amber', 'violet', 'teal', 'pink')]
        pairs += [('nav.activeText', 'nav.active', 4.5), ('nav.text', 'nav.background', 4.5),
                  ('action.primary', 'action.subtle', 4.5)]
        for foreground, background, minimum in pairs:
            ratio = contrast(colors[foreground], colors[background])
            checks.append((mode, foreground, background, ratio, minimum))
    failures = [f'{mode}: {fg} / {bg} = {ratio:.2f} (requires {minimum})'
                for mode, fg, bg, ratio, minimum in checks if ratio < minimum]
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'Shared design tokens: {len(checks)} light/dark contrast checks passed')


if __name__ == '__main__':
    main()
