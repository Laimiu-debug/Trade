import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from '../../../journal-frontend/src/pages/Settings';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), toast: vi.fn() }));
vi.mock('../../../journal-frontend/src/api', async importOriginal => ({
  ...await importOriginal<object>(), api: { get: mocks.get, post: mocks.post, put: vi.fn(), del: vi.fn() },
}));
vi.mock('../../../journal-frontend/src/components', async importOriginal => ({
  ...await importOriginal<object>(), useToast: () => mocks.toast,
}));
vi.mock('../../../journal-frontend/src/exportPdf', () => ({ clearPdfSettingsCache: vi.fn() }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockImplementation(async (path: string) => path === '/api/settings' ? { data_dir: 'C:/active-data' } : []);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('journal data-directory migration', () => {
  it('ignores an outdated target preview and waits for the current parent to be verified', async () => {
    const old = deferred<{ target_dir: string; note: string }>();
    const current = deferred<{ target_dir: string; note: string }>();
    mocks.post.mockImplementation((_path: string, body: { target_dir: string }) => body.target_dir === 'D:/old' ? old.promise : current.promise);
    render(<Settings />);
    const directory = screen.getByPlaceholderText('点击「浏览…」选择，或手动粘贴路径');
    fireEvent.change(directory, { target: { value: 'D:/old' } });
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    fireEvent.change(directory, { target: { value: 'E:/new' } });
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    await act(async () => { old.resolve({ target_dir: 'D:/old/TradingMS-data', note: '旧目标' }); });
    expect(screen.getByRole('button', { name: '复制数据并准备重启' })).toBeDisabled();
    expect(screen.queryByText('D:/old/TradingMS-data')).not.toBeInTheDocument();
    await act(async () => { current.resolve({ target_dir: 'E:/new/TradingMS-data', note: '已校验' }); });
    expect(screen.getByRole('button', { name: '复制数据并准备重启' })).toBeEnabled();
    expect(screen.getByText('E:/new/TradingMS-data')).toBeInTheDocument();
  });

  it('submits once, retains the active path, and requires a full restart after copying', async () => {
    const copied = deferred<{ new_dir: string; active_dir: string; restart_required: boolean }>();
    mocks.post.mockImplementation((path: string) => path.endsWith('preview-data-dir')
      ? Promise.resolve({ target_dir: 'E:/new/TradingMS-data', note: '已校验' }) : copied.promise);
    const timers = vi.spyOn(window, 'setTimeout');
    render(<Settings />);
    fireEvent.change(screen.getByPlaceholderText('点击「浏览…」选择，或手动粘贴路径'), { target: { value: 'E:/new' } });
    const button = screen.getByRole('button', { name: '复制数据并准备重启' });
    await waitFor(() => expect(button).toBeEnabled());
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    expect(mocks.post.mock.calls.filter(([path]) => path.endsWith('move-data'))).toHaveLength(1);
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    await act(async () => { copied.resolve({ active_dir: 'C:/active-data', new_dir: 'E:/new/TradingMS-data', restart_required: true }); });
    expect(screen.getByRole('status')).toHaveTextContent('当前读取目录：C:/active-data');
    expect(screen.getByRole('status')).toHaveTextContent('下次启动目录：E:/new/TradingMS-data');
    expect(screen.getByRole('status')).toHaveTextContent('刷新页面不会切换数据目录');
    expect(screen.getByRole('button', { name: '保存全部设置' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '等待完全退出并重启' })).toBeDisabled();
    expect(timers.mock.calls.some(([, delay]) => delay === 1800)).toBe(false);
  });
});
