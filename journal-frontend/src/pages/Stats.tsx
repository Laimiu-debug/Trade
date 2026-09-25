import { useEffect, useState } from 'react';
import { api, fmtMoney, type Overview } from '../api';
import { Chart, CHART_COLORS, baseAxis, baseTooltip } from '../Chart';
import { Empty, Stat } from '../components';

export default function Stats() {
  const [ov, setOv] = useState<Overview | null>(null);

  useEffect(() => {
    api.get<Overview>('/api/stats/overview').then(setOv).catch(() => {});
  }, []);

  if (!ov) return <div className="empty">加载中…</div>;
  const hasCurve = ov.curve.length > 0;

  const returnsBar = (data: { period: string; return_pct: number }[]) => ({
    grid: { left: 48, right: 16, top: 20, bottom: 28 },
    tooltip: baseTooltip,
    xAxis: { type: 'category', data: data.map(d => d.period), ...baseAxis },
    yAxis: { type: 'value', ...baseAxis, axisLabel: { ...baseAxis.axisLabel, formatter: '{value}%' } },
    series: [{
      type: 'bar',
      data: data.map(d => ({
        value: d.return_pct,
        itemStyle: { color: d.return_pct >= 0 ? CHART_COLORS.up : CHART_COLORS.down, borderRadius: 3 },
      })),
      barMaxWidth: 26,
    }],
  });

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h2 className="page-title">统计分析</h2>
          <div className="page-sub">数字不撒谎</div>
        </div>
      </div>

      <div className="grid grid-4">
        <div className="card"><Stat small label="历史最大回撤" value={`${ov.max_drawdown_pct}%`} /></div>
        <div className="card"><Stat small label="回合胜率" gold value={ov.round_stats.win_rate != null ? `${ov.round_stats.win_rate}%` : '—'} note={`${ov.round_stats.win_count}胜 ${ov.round_stats.lose_count}负`} /></div>
        <div className="card"><Stat small label="盈亏比" value={ov.round_stats.profit_loss_ratio ?? '—'} /></div>
        <div className="card"><Stat small label="累计回合盈亏" tone={ov.round_stats.total_pnl >= 0 ? 'pos' : 'neg'} value={`¥${fmtMoney(ov.round_stats.total_pnl)}`} /></div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">净值曲线 与 回撤</h3>
        {hasCurve ? (
          <Chart height={320} option={{
            grid: [
              { left: 52, right: 16, top: 20, height: 170 },
              { left: 52, right: 16, top: 220, height: 60 },
            ],
            tooltip: baseTooltip,
            xAxis: [
              { type: 'category', gridIndex: 0, data: ov.curve.map(p => p.date), ...baseAxis, axisLabel: { show: false } },
              { type: 'category', gridIndex: 1, data: ov.curve.map(p => p.date), ...baseAxis },
            ],
            yAxis: [
              { type: 'value', gridIndex: 0, scale: true, ...baseAxis },
              { type: 'value', gridIndex: 1, ...baseAxis, axisLabel: { ...baseAxis.axisLabel, formatter: '{value}%' } },
            ],
            series: [
              {
                type: 'line', xAxisIndex: 0, yAxisIndex: 0,
                data: ov.curve.map(p => p.nav), showSymbol: false,
                lineStyle: { color: CHART_COLORS.gold, width: 2 },
                areaStyle: { color: CHART_COLORS.goldSoft },
              },
              {
                type: 'line', xAxisIndex: 1, yAxisIndex: 1,
                data: ov.curve.map(p => p.drawdown_pct), showSymbol: false,
                lineStyle: { color: CHART_COLORS.down, width: 1.5 },
                areaStyle: { color: 'rgba(76,175,135,0.15)' },
              },
            ],
          }} />
        ) : <Empty text="暂无数据" />}
      </div>

      <div className="grid grid-2" style={{ marginTop: 18 }}>
        <div className="card">
          <h3 className="card-title">周收益率</h3>
          {ov.weekly_returns.length ? <Chart height={220} option={returnsBar(ov.weekly_returns.slice(-26))} /> : <Empty text="暂无数据" />}
        </div>
        <div className="card">
          <h3 className="card-title">月收益率</h3>
          {ov.monthly_returns.length ? <Chart height={220} option={returnsBar(ov.monthly_returns.slice(-24))} /> : <Empty text="暂无数据" />}
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">节点耗时趋势 · 越短越强<span className="muted">首次点亮间隔天数</span></h3>
        {ov.node_timing.filter(t => t.days_taken != null).length ? (
          <Chart height={220} option={{
            grid: { left: 48, right: 16, top: 20, bottom: 28 },
            tooltip: baseTooltip,
            xAxis: { type: 'category', data: ov.node_timing.map(t => `Lv.${t.level}`), ...baseAxis },
            yAxis: { type: 'value', name: '天', nameTextStyle: { color: CHART_COLORS.text }, ...baseAxis },
            series: [{
              type: 'bar',
              data: ov.node_timing.map(t => t.days_taken),
              itemStyle: { color: CHART_COLORS.gold, borderRadius: 3 },
              barMaxWidth: 30,
            }],
          }} />
        ) : <Empty text="点亮第一个节点后，这里开始记录你的成长速度" />}
      </div>

      <div className="card no-print" style={{ marginTop: 18 }}>
        <h3 className="card-title">数据备份</h3>
        <div className="row">
          <a className="btn" href="/journal-app/api/export/json">导出 JSON 备份</a>
          <a className="btn" href="/journal-app/api/export/markdown">导出复盘 Markdown</a>
        </div>
      </div>
    </div>
  );
}
