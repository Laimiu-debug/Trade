import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Journal from '../../../journal-frontend/src/pages/Journal';
import { useAutosave } from '../../../journal-frontend/src/hooks/usePersist';
import type { DailyReview } from '../../../journal-frontend/src/api';

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), close: vi.fn(), toast: vi.fn(), busy: vi.fn() }));
vi.mock('../../../journal-frontend/src/api', async importOriginal => ({
  ...await importOriginal<object>(),
  api: { get: mocks.get, put: mocks.put, post: mocks.post, del: vi.fn() },
}));
vi.mock('../../../journal-frontend/src/marketClose', () => ({ fetchCloseOnDay: mocks.close }));
vi.mock('../../../journal-frontend/src/AiBusy', () => ({ useAiBusy: () => ({ setBusy: mocks.busy }) }));
vi.mock('../../../journal-frontend/src/pages/periodicShared', () => ({ PeriodRounds: () => null }));
vi.mock('../../../journal-frontend/src/components', () => ({
  useToast: () => mocks.toast,
  Empty: ({ text }: { text: string }) => <span>{text}</span>,
  SideTag: () => null,
  DateInput: ({ value, onChange }: { value: string; onChange: (value: string) => void }) =>
    <input aria-label="复盘日期" value={value} onChange={event => onChange(event.target.value)} />,
  QtyStepper: ({ value, onChange }: { value: number; onChange: (value: number) => void }) =>
    <input aria-label="预演股数" value={value} onChange={event => onChange(Number(event.target.value))} />,
  StockPicker: ({ code, onSelect }: { code: string; onSelect: (code: string, name: string) => void }) => <div>
    <span data-testid="selected-stock">{code}</span>
    <button onClick={() => onSelect('111111', '股票 A')}>选择 A</button>
    <button onClick={() => onSelect('222222', '股票 B')}>选择 B</button>
  </div>,
}));
vi.mock('../../../journal-frontend/src/printPage', () => ({ printDailyReview: vi.fn() }));
vi.mock('../../../journal-frontend/src/printLayout', () => ({
  PrintDocument: () => null, PrintDocHeader: () => null, PrintScoreDimRows: () => null,
  PrintSection: () => null, PrintSummaryBox: () => null, PrintTextBlock: () => null, PrintTradeSide: () => null,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}

function review(day: string): DailyReview {
  return {
    review_date: day, market_observation: `行情 ${day}`, decision_review: '', mistakes: '', images: [],
    scores: {}, trade_scores: {}, ai_summary: '', next_market_forecast: '', next_watchlist: [],
    next_position_plan: '', next_risk_plan: '', next_position_rehearsal: [{ code: '000001', name: '初始', qty: 100, close: 10, note: '' }],
    today_positions: [], rehearsal_baseline: { cash: 100000, total_assets: 100000 }, prev_rehearsal: [],
    rehearsal_compare: [], rehearsal_ai_analysis: '', trades: [], t_groups: [], snapshot: null, day_rounds: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockImplementation(async (path: string) => path === '/api/settings' ? {} : review(path.split('/').at(-1)!));
  mocks.put.mockResolvedValue({});
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('journal autosave lifecycle', () => {
  it('saves the first dirty edit after the debounce and never writes the clean mount', async () => {
    vi.useFakeTimers();
    const save = vi.fn(async () => {});
    const { rerender } = renderHook(({ dirty, text }) => useAutosave(dirty, save, [text]), {
      initialProps: { dirty: false, text: 'clean' },
    });
    rerender({ dirty: true, text: 'first edit' });
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(save).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('flushes only the latest committed callback when a route unmounts', async () => {
    vi.useFakeTimers();
    const save = vi.fn(async (_day: string, _text: string) => {});
    const { rerender, unmount } = renderHook(({ day, text }) => useAutosave(true, () => save(day, text), [day, text]), {
      initialProps: { day: '2026-10-01', text: 'first' },
    });
    rerender({ day: '2026-10-01', text: 'last' });
    unmount();
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(save.mock.calls).toEqual([['2026-10-01', 'last']]);
  });

  it('cancels a discarded date scope instead of flushing it on a later unmount', async () => {
    vi.useFakeTimers();
    const save = vi.fn(async () => {});
    const { rerender, unmount } = renderHook(({ dirty, day }) => useAutosave(dirty, save, [day]), {
      initialProps: { dirty: true, day: '2026-10-01' },
    });
    rerender({ dirty: false, day: '2026-10-02' });
    unmount();
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(save).not.toHaveBeenCalled();
  });
});

describe('journal asynchronous edits', () => {
  function open() { return render(<MemoryRouter initialEntries={['/?day=2026-10-01']}><Journal /></MemoryRouter>); }

  it('ignores the old stock quote and preserves edits made while the new quote loads', async () => {
    const a = deferred<number | null>();
    const b = deferred<number | null>();
    mocks.close.mockImplementation((code: string) => code === '111111' ? a.promise : b.promise);
    open();
    fireEvent.click(await screen.findByRole('button', { name: '选择 A' }));
    fireEvent.click(screen.getByRole('button', { name: '选择 B' }));
    fireEvent.change(screen.getByPlaceholderText('备注'), { target: { value: '保留这条备注' } });
    fireEvent.change(screen.getByRole('textbox', { name: '预演股数' }), { target: { value: '200' } });
    await act(async () => { b.resolve(20); });
    await act(async () => { a.resolve(99); });
    expect(screen.getByTestId('selected-stock')).toHaveTextContent('222222');
    expect(screen.getByPlaceholderText('备注')).toHaveValue('保留这条备注');
    expect(screen.getByRole('textbox', { name: '预演股数' })).toHaveValue('200');
    expect(screen.getByText('20.000')).toBeInTheDocument();
    expect(screen.queryByText('99.000')).not.toBeInTheDocument();
  });

  it('does not apply an old day quote or save completion to the newly selected date', async () => {
    const quote = deferred<number | null>();
    const saved = deferred<object>();
    mocks.close.mockReturnValue(quote.promise);
    mocks.put.mockReturnValueOnce(saved.promise);
    open();
    fireEvent.click(await screen.findByRole('button', { name: '选择 A' }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByRole('textbox', { name: '复盘日期' }), { target: { value: '2026-10-02' } });
    const editor = await screen.findByDisplayValue('行情 2026-10-02');
    fireEvent.change(editor, { target: { value: '新日期草稿' } });
    await act(async () => { quote.resolve(99); saved.resolve({}); });
    expect(editor).toHaveValue('新日期草稿');
    expect(screen.getByTestId('selected-stock')).toHaveTextContent('000001');
    expect(screen.queryByText('99.000')).not.toBeInTheDocument();
    expect(screen.getByText('有未保存修改')).toBeInTheDocument();
    expect(mocks.toast).not.toHaveBeenCalledWith('复盘已保存');
  });

  it('flushes the last route edit after an in-flight save, using its original date', async () => {
    const saved = deferred<object>();
    mocks.put.mockReturnValueOnce(saved.promise);
    const { unmount } = open();
    const editor = await screen.findByDisplayValue('行情 2026-10-01');
    fireEvent.change(editor, { target: { value: '第一次编辑' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    fireEvent.change(editor, { target: { value: '离开前最后编辑' } });
    unmount();
    expect(mocks.put).toHaveBeenCalledTimes(1);
    await act(async () => { saved.resolve({}); });
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(2));
    expect(mocks.put.mock.calls[1][0]).toBe('/api/reviews/daily/2026-10-01');
    expect(mocks.put.mock.calls[1][1].market_observation).toBe('离开前最后编辑');
  });

  it('persists the returned AI scores instead of the old closure and preserves concurrent text edits', async () => {
    const analysis = deferred<object>();
    mocks.post.mockReturnValue(analysis.promise);
    mocks.get.mockImplementation(async (path: string) => {
      if (path === '/api/settings') return {};
      return { ...review(path.split('/').at(-1)!), trades: [{ id: 1, code: '000001', name: '初始', side: 'buy', price: 10, qty: 100, fees: 5 }] };
    });
    open();
    fireEvent.click(await screen.findByRole('button', { name: '✦ AI 分析全部' }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText('今天市场发生了什么？'), { target: { value: '等待期间的新编辑' } });
    const tradeScores = { '1': { execution: { ai: 4, comment: '新的评分' } } };
    const scores = { execution: { ai: 4, comment: '新的评分' } };
    await act(async () => { analysis.resolve({ trade_scores: tradeScores, scores, summary: '分析完成', merged_count: 1 }); });
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(2));
    expect(mocks.put.mock.calls[1][1]).toMatchObject({
      trade_scores: tradeScores, scores, market_observation: '等待期间的新编辑',
    });
    expect(screen.getByPlaceholderText('今天市场发生了什么？')).toHaveValue('等待期间的新编辑');
    expect(screen.queryByText('有未保存修改')).not.toBeInTheDocument();
  });

  it('ignores an AI response after leaving its date, including returning to that same date', async () => {
    const analysis = deferred<object>();
    mocks.post.mockReturnValue(analysis.promise);
    mocks.get.mockImplementation(async (path: string) => path === '/api/settings' ? {} : {
      ...review(path.split('/').at(-1)!), trades: [{ id: 1, code: '000001', name: '初始', side: 'buy', price: 10, qty: 100, fees: 5 }],
    });
    open();
    fireEvent.click(await screen.findByRole('button', { name: '✦ AI 分析全部' }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByRole('textbox', { name: '复盘日期' }), { target: { value: '2026-10-02' } });
    await screen.findByDisplayValue('行情 2026-10-02');
    fireEvent.change(screen.getByRole('textbox', { name: '复盘日期' }), { target: { value: '2026-10-01' } });
    await screen.findByDisplayValue('行情 2026-10-01');
    await act(async () => { analysis.resolve({ trade_scores: {}, scores: {}, summary: '过期分析', merged_count: 1 }); });
    expect(mocks.put).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('过期分析')).not.toBeInTheDocument();
    expect(mocks.toast).not.toHaveBeenCalledWith(expect.stringContaining('已完成'));
  });
});
