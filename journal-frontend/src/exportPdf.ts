import { api } from './api';
import { buildDailyPdfFilename, buildMonthlyPdfFilename, buildWeeklyPdfFilename, wrapPrintHtml } from './printCss';

type PdfSettings = { pdf_username?: string; pdf_export_dir?: string };

let settingsCache: PdfSettings | null = null;

async function loadPdfSettings(): Promise<PdfSettings> {
  if (settingsCache) return settingsCache;
  const v = await api.get<PdfSettings & Record<string, string>>('/api/settings');
  settingsCache = { pdf_username: v.pdf_username ?? '', pdf_export_dir: v.pdf_export_dir ?? '' };
  return settingsCache;
}

export function clearPdfSettingsCache() {
  settingsCache = null;
}

export async function exportToPdf(options: {
  bodyHtml: string;
  docTitle: string;
  filename: string;
  onDone: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const settings = await loadPdfSettings();
  const html = wrapPrintHtml(options.docTitle, options.bodyHtml);
  const exportDir = settings.pdf_export_dir?.trim();

  if (exportDir) {
    try {
      const res = await api.post<{ path: string; filename: string }>('/api/export/pdf', {
        html,
        filename: options.filename,
      });
      options.onDone(`PDF 已保存：${res.path}`);
    } catch (e) {
      options.onError(String(e).replace(/^Error:\s*/, ''));
    }
    return;
  }

  const prevTitle = document.title;
  document.title = options.filename.replace(/\.pdf$/i, '');
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
  document.body.appendChild(iframe);
  const doc = iframe.contentDocument;
  if (!doc) {
    document.title = prevTitle;
    iframe.remove();
    options.onError('无法打开打印窗口');
    return;
  }
  doc.open();
  doc.write(html);
  doc.close();
  iframe.contentWindow?.focus();
  iframe.contentWindow?.print();
  setTimeout(() => {
    document.title = prevTitle;
    iframe.remove();
  }, 1000);
  options.onDone('请在打印对话框中选择「另存为 PDF」（可在设置中配置保存路径以自动导出）');
}

export async function exportDailyPdf(
  day: string, bodyHtml: string,
  onDone: (msg: string) => void, onError: (msg: string) => void,
) {
  const settings = await loadPdfSettings();
  const username = settings.pdf_username ?? '';
  const filename = buildDailyPdfFilename(username, day);
  const docTitle = filename.replace(/\.pdf$/i, '');
  await exportToPdf({ bodyHtml, docTitle, filename, onDone, onError });
}

export async function exportWeeklyPdf(
  year: number, week: number, bodyHtml: string,
  onDone: (msg: string) => void, onError: (msg: string) => void,
) {
  const settings = await loadPdfSettings();
  const username = settings.pdf_username ?? '';
  const filename = buildWeeklyPdfFilename(username, year, week);
  await exportToPdf({ bodyHtml, docTitle: filename.replace(/\.pdf$/i, ''), filename, onDone, onError });
}

export async function exportMonthlyPdf(
  year: number, month: number, bodyHtml: string,
  onDone: (msg: string) => void, onError: (msg: string) => void,
) {
  const settings = await loadPdfSettings();
  const username = settings.pdf_username ?? '';
  const filename = buildMonthlyPdfFilename(username, year, month);
  await exportToPdf({ bodyHtml, docTitle: filename.replace(/\.pdf$/i, ''), filename, onDone, onError });
}
