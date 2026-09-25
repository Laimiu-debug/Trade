import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, fmtMoney, fmtPct, type DayDetail, type FlashCard, type NodeInfo, type Overview } from '../api';
import { Chart, CHART_COLORS, baseAxis, baseTooltip } from '../Chart';
import { Empty, SideTag, Stat, useToast } from '../components';

interface NodesResp {
  state: Overview['state'];
  nodes: NodeInfo[];
  timing: { level: number; first_lit: string; days_taken: number | null }[];
}

export default function Dashboard() {
  const toast = useToast();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [nodes, setNodes] = useState<NodesResp | null>(null);
  const [card, setCard] = useState<FlashCard | null>(null);
  const [dayDetail, setDayDetail] = useState<DayDetail | null>(null);
  const [dayLoading, setDayLoading] = useState(false);

  useEffect(() => {
    api.get<Overview>('/api/stats/overview').then(setOverview).catch(e => toast(String(e)));
    api.get<NodesResp>('/api/capital/nodes').then(setNodes).catch(e => toast(String(e)));
    api.get<FlashCard | null>('/api/cards/random').then(setCard).catch(e => toast(String(e)));
  }, [toast]);

  const openDayDetail = (date: string) => {
    setDayLoading(true);
    setDayDetail(null);
    api.get<DayDetail>(`/api/stats/day/${date}`)
      .then(setDayDetail)
      .catch(e => { toast(String(e)); setDayDetail(null); })
      .finally(() => setDayLoading(false));
  };

  const state = overview?.state;
  const hasData = (overview?.curve.length ?? 0) > 0;

  return (
    <div className="fade-in">
      <div className="hero">
        <img className="hero-logo" src="/journal-app/logo.png" alt="Trading MS" />
        <div className="hero-text">
          <h2 className="hero-title">Trading MS</h2>
          <div className="page-sub">{state?.day ? `数据截至 ${state.day}` : '尚未录入数据'}</div>
        </div>
        <Link className="btn" to="/journal">写今日复盘 →</Link>
      </div>

      {overview && overview.missing_reviews.length > 0 && (
        <div className="alert" style={{ marginBottom: 18 }}>
          有 {overview.missing_reviews.length} 个交易日未写复盘：
          {overview.missing_reviews.slice(-5).map((d, i) => (
            <span key={d}>
              {i > 0 ? '、' : ''}
              <Link to={`/journal?day=${d}`}>{d}</Link>
            </span>
          ))}
          {overview.missing_reviews.length > 5 ? ' …' : ''}
        </div>
      )}

      {overview && overview.missing_snapshots.length > 0 && (
        <div className="alert" style={{ marginBottom: 18 }}>
          有 {overview.missing_snapshots.length} 个交易日缺收盘快照：
          {overview.missing_snapshots.slice(-5).map((d, i) => (
            <span key={d}>
              {i > 0 ? '、' : ''}
              <Link to="/capital">{d}</Link>
            </span>
          ))}
          {overview.missing_snapshots.length > 5 ? ' …' : ''}
          <span className="muted">（可在资金账本补录）</span>
        </div>
      )}

      <div className="grid grid-4">
        <div className="card">
          <Stat label="当前净值" gold value={state ? state.nav.toFixed(4) : '—'}
            note={state && state.drawdown_pct < 0 ? <span className="neg">距峰值 {fmtPct(state.drawdown_pct)}</span> : '处于净值高点'} />
        </div>
        <div className="card">
          <Stat label="账户资产" value={state ? `¥${fmtMoney(state.assets)}` : '—'}
            note={state ? `份额 ${fmtMoney(state.shares)}` : ''} />
        </div>
        <div className="card">
          <Stat label="已点亮节点" gold value={state ? `${state.lit_count} / ${state.node_count}` : '—'}
            note={state?.next_level ? `下一节点 Lv.${state.next_level}` : '全部达成'} />
        </div>
        <div className="card">
          <Stat label="距下一节点" value={state?.next_gap_pct != null ? fmtPct(state.next_gap_pct, false) : '—'}
            note={state?.next_assets_target ? `目标资产 ¥${fmtMoney(state.next_assets_target)}` : ''} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">
          节点征途
          <span className="muted">
            {state ? `每节点 +${state.wave_pct}% · 共 ${state.node_count} 节 · 回撤即熄灭` : '回撤即熄灭'}
          </span>
        </h3>
        {state && (
          <div className="progress-block">
            <div className="progress-labels">
              <span>总进度</span>
              <span><span className="strong">{state.lit_count}</span> / {state.node_count} · {((state.lit_count / state.node_count) * 100).toFixed(1)}%</span>
            </div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${Math.max(0.5, (state.lit_count / state.node_count) * 100)}%` }} />
            </div>
            {state.next_level != null && (
              <div style={{ marginTop: 10 }}>
                <div className="progress-labels">
                  <span>当前段 · 冲击 Lv.{state.next_level}（净值 {state.next_threshold?.toFixed(3)}）</span>
                  <span className="strong">{state.leg_progress_pct.toFixed(1)}%</span>
                </div>
                <div className="progress-track">
                  <div className="progress-fill slim" style={{ width: `${Math.max(0.5, state.leg_progress_pct)}%` }} />
                </div>
              </div>
            )}
          </div>
        )}
        {nodes ? (
          <div className="node-grid">
            {nodes.nodes.map(n => {
              const isNext = nodes.state.next_level === n.level;
              return (
                <div key={n.level}
                  className={`node-cell${n.lit ? ' lit' : ''}${isNext ? ' next' : ''}`}
                  title={`Lv.${n.level} 净值 ${n.threshold.toFixed(2)}${n.assets_equiv ? ` ≈ ¥${fmtMoney(n.assets_equiv)}` : ''}`}>
                  <span className="lv">{n.level}</span>
                </div>
              );
            })}
          </div>
        ) : <Empty text="录入初始资金后，征途从这里开始" />}
      </div>

      <div className="grid grid-2" style={{ marginTop: 18 }}>
        <div className="card">
          <h3 className="card-title">
            净值曲线
            <span className="muted">点击数据点查看当日持仓与操作</span>
          </h3>
          {hasData ? (
            <Chart height={240} onPointClick={(_i, date) => openDayDetail(date)} option={{
              grid: { left: 48, right: 16, top: 20, bottom: 28 },
              tooltip: baseTooltip,
              xAxis: { type: 'category', data: overview!.curve.map(p => p.date), ...baseAxis },
              yAxis: { type: 'value', scale: true, ...baseAxis },
              series: [{
                type: 'line', data: overview!.curve.map(p => p.nav),
                showSymbol: true, symbolSize: 6,
                lineStyle: { color: CHART_COLORS.gold, width: 2 },
                areaStyle: { color: CHART_COLORS.goldSoft },
                markLine: state?.next_threshold ? {
                  silent: true, symbol: 'none',
                  lineStyle: { color: CHART_COLORS.text, type: 'dashed' },
                  data: [{ yAxis: state.next_threshold, label: { formatter: `Lv.${state.next_level}`, color: CHART_COLORS.text } }],
                } : undefined,
              }],
            }} />
          ) : <Empty text="暂无净值数据 — 请先在「设置」录入初始资金，并在「资金账本」补录每日快照" />}
        </div>

        <div className="card">
          <h3 className="card-title">今日闪记<Link to="/cards" className="muted" style={{ textDecoration: 'none' }}>全部 →</Link></h3>
          {card ? (
            <div className="flash-card">
              <div className="content">{card.content}</div>
              <div className="meta">
                <span>{card.tags && card.tags.split(',').map(t => <span key={t} className="tag gold">{t}</span>)}</span>
                <span>{card.created_at.slice(0, 10)}</span>
              </div>
            </div>
          ) : <Empty text="还没有闪记。灵感来临时，记下来。" />}

          {overview && overview.round_stats.total_rounds > 0 && (
            <>
              <hr className="divider" />
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <Stat small label="回合胜率" value={overview.round_stats.win_rate != null ? `${overview.round_stats.win_rate}%` : '—'} />
                <Stat small label="盈亏比" value={overview.round_stats.profit_loss_ratio ?? '—'} />
                <Stat small label="累计回合盈亏"
                  tone={overview.round_stats.total_pnl >= 0 ? 'pos' : 'neg'}
                  value={`¥${fmtMoney(overview.round_stats.total_pnl)}`} />
              </div>
            </>
          )}
        </div>
      </div>

      {(dayDetail || dayLoading) && (
        <div className="day-detail-overlay no-print" onClick={() => { setDayDetail(null); setDayLoading(false); }}>
          <div className="day-detail-panel" onClick={e => e.stopPropagation()}>
            <div className="page-head" style={{ marginBottom: 12 }}>
              <h3 className="card-title" style={{ margin: 0 }}>
                {dayLoading ? '加载中…' : `${dayDetail?.date} 详情`}
              </h3>
              <button className="ghost" onClick={() => { setDayDetail(null); setDayLoading(false); }}>关闭</button>
            </div>
            {dayDetail && (
              <>
                <div className="row" style={{ gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
                  {dayDetail.nav != null && <span className="mono">净值 {dayDetail.nav.toFixed(4)}</span>}
                  {dayDetail.assets != null && <span className="mono">资产 ¥{fmtMoney(dayDetail.assets)}</span>}
                  {dayDetail.drawdown_pct != null && dayDetail.drawdown_pct < 0 && (
                    <span className="neg mono">回撤 {fmtPct(dayDetail.drawdown_pct)}</span>
                  )}
                  {dayDetail.snapshot?.total_assets != null && (
                    <span className="muted mono">
                      快照 ¥{fmtMoney(dayDetail.snapshot.total_assets)}
                      {dayDetail.snapshot.estimated ? '（推算）' : ''}
                    </span>
                  )}
                </div>

                <h4 className="card-title" style={{ fontSize: 13 }}>当日操作</h4>
                {dayDetail.trades.length === 0 ? (
                  <div className="muted" style={{ marginBottom: 14, fontSize: 12 }}>无交易记录</div>
                ) : (
                  <table style={{ marginBottom: 14 }}>
                    <thead><tr><th>方向</th><th>标的</th><th style={{ textAlign: 'right' }}>价格×数量</th></tr></thead>
                    <tbody>
                      {dayDetail.trades.map(t => (
                        <tr key={t.id}>
                          <td><SideTag side={t.side} /></td>
                          <td>{t.name || t.code} <span className="muted mono">{t.code}</span></td>
                          <td style={{ textAlign: 'right' }} className="mono">{t.price} × {t.qty}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <h4 className="card-title" style={{ fontSize: 13 }}>收盘持仓</h4>
                {dayDetail.positions.length === 0 ? (
                  <Empty text="无持仓数据（可在资金账本补录快照）" />
                ) : (
                  <div className="snap-card-positions">
                    {dayDetail.positions.map((p, i) => (
                      <div className="snap-pos-row" key={`${p.code}-${i}`}>
                        <span className="mono">{p.code}</span>
                        <span>{p.name ?? '—'}</span>
                        <span className="mono muted">{p.qty != null ? `${p.qty}股` : '—'}</span>
                        <span className="mono muted">
                          {p.market_value != null ? `¥${fmtMoney(p.market_value)}` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {dayDetail.ai_summary && (
                  <div className="journal-day-summary" style={{ marginTop: 14 }}>
                    <div className="journal-day-summary-label">复盘 AI 总评</div>
                    {dayDetail.ai_summary}
                  </div>
                )}

                <div className="row" style={{ marginTop: 16, gap: 8 }}>
                  <Link className="btn" to={`/journal?day=${dayDetail.date}`} onClick={() => setDayDetail(null)}>查看复盘 →</Link>
                  <Link className="ghost" to="/trades">交易记录</Link>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
