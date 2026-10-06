import { useContext, useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, BarChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, MarkLineComponent, DataZoomComponent, LegendComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';
import { designTokens } from './design-tokens.generated';
import { readTheme, ThemeContext } from './theme';

echarts.use([
  LineChart, BarChart, GridComponent, TooltipComponent,
  MarkLineComponent, DataZoomComponent, LegendComponent, CanvasRenderer,
]);

const colors = () => designTokens.themes[readTheme()].color;

export const CHART_COLORS = {
  get gold() { return colors()['chart.series1']; },
  get goldSoft() { return colors()['bg.selected']; },
  get up() { return colors()['market.up']; },
  get down() { return colors()['market.down']; },
  get downSoft() { return colors()['market.downBg']; },
  get text() { return colors()['chart.axis']; },
  get grid() { return colors()['chart.grid']; },
};

export function Chart({
  option,
  height = 280,
  onPointClick,
}: {
  option: EChartsCoreOption;
  height?: number;
  onPointClick?: (index: number, date: string, value: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useContext(ThemeContext);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const onPointClickRef = useRef(onPointClick);
  onPointClickRef.current = onPointClick;

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current = echarts.init(ref.current);
    const onResize = () => chartRef.current?.resize();
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => onResize())
      : null;
    ro?.observe(ref.current);
    const handler = (params: unknown) => {
      const p = params as { componentType?: string; dataIndex?: number; name?: string; value?: number };
      if (p.componentType === 'series' && p.dataIndex != null && onPointClickRef.current) {
        onPointClickRef.current(p.dataIndex, String(p.name ?? ''), Number(p.value ?? 0));
      }
    };
    chartRef.current.on('click', handler);
    return () => {
      window.removeEventListener('resize', onResize);
      ro?.disconnect();
      chartRef.current?.off('click', handler);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option, theme]);

  return <div ref={ref} style={{ width: '100%', height }} />;
}

export const baseAxis = {
  get axisLine() { return { lineStyle: { color: CHART_COLORS.grid } }; },
  get axisLabel() { return { color: CHART_COLORS.text, fontSize: 11 }; },
  get splitLine() { return { lineStyle: { color: CHART_COLORS.grid, opacity: 0.5 } }; },
};

export const baseTooltip = {
      trigger: 'axis' as const,
      get backgroundColor() { return colors()['bg.surface']; },
      get borderColor() { return colors()['border.subtle']; },
      get textStyle() { return { color: colors()['text.primary'], fontSize: designTokens.sizePx.chartTooltipFont }; },
    };
