import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, BarChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, MarkLineComponent, DataZoomComponent, LegendComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';

echarts.use([
  LineChart, BarChart, GridComponent, TooltipComponent,
  MarkLineComponent, DataZoomComponent, LegendComponent, CanvasRenderer,
]);

// 默认浅色（无 data-theme 属性）；仅 data-theme="dark" 为深色
const isLight = () => document.documentElement.dataset.theme !== 'dark';

const lightColors = {
  gold: '#e8a87c', goldSoft: 'rgba(232,168,124,0.2)',
  up: '#ff6b6b', down: '#4ecdc4', text: '#7a6f5e', grid: '#e6dcc6',
};
const darkColors = {
  gold: '#e8a87c', goldSoft: 'rgba(232,168,124,0.18)',
  up: '#ff6b6b', down: '#4ecdc4', text: '#b3a48c', grid: '#463829',
};

// 主题切换会触发页面 reload，因此模块加载时按当前主题取一次即可。
export const CHART_COLORS = isLight() ? lightColors : darkColors;

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
  }, [option]);

  return <div ref={ref} style={{ width: '100%', height }} />;
}

// 主题切换会触发页面 reload，因此模块加载时按当前主题计算一次即可。
export const baseAxis = {
  axisLine: { lineStyle: { color: CHART_COLORS.grid } },
  axisLabel: { color: CHART_COLORS.text, fontSize: 11 },
  splitLine: { lineStyle: { color: CHART_COLORS.grid, opacity: 0.5 } },
};

export const baseTooltip = isLight()
  ? {
      trigger: 'axis' as const,
      backgroundColor: '#ffffff',
      borderColor: '#e6dcc6',
      textStyle: { color: '#3a322a', fontSize: 12 },
    }
  : {
      trigger: 'axis' as const,
      backgroundColor: '#2c241d',
      borderColor: '#5a4836',
      textStyle: { color: '#f2ece0', fontSize: 12 },
    };
