import { useContext, useMemo } from 'react'
import { designTokens } from '@/shared/theme/design-tokens.generated'
import { WorkspaceThemeContext } from '@/shared/theme/theme-context'

type ChartColor = keyof typeof designTokens.themes.light.color

// Canvas charts need resolved colors; CSS var() strings cannot be painted.
export function chartColor(role: ChartColor): string {
  const mode = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
  return designTokens.themes[mode].color[role]
}

export function useChartTheme() {
  const mode = useContext(WorkspaceThemeContext)
  return useMemo(() => {
    const colors = designTokens.themes[mode].color
    const axis = {
      axisLabel: { color: colors['chart.axis'] },
      axisLine: { lineStyle: { color: colors['chart.grid'] } },
      splitLine: { lineStyle: { color: colors['chart.grid'] } },
    }
    return {
      textStyle: { color: colors['text.secondary'], fontFamily: designTokens.font.family.ui },
      categoryAxis: axis, valueAxis: axis,
      tooltip: {
        backgroundColor: colors['bg.surface'], borderColor: colors['border.subtle'],
        textStyle: { color: colors['text.primary'] },
      },
    }
  }, [mode])
}
