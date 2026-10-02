import EChartsReactCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

export default function NavChart({ option }: { option: Record<string, unknown> }) {
  return <EChartsReactCore echarts={echarts} option={option} style={{ height: 320 }} />
}
