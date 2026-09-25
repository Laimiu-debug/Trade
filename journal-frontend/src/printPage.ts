import { buildDailyPdfFilename, buildMonthlyPdfFilename, buildWeeklyPdfFilename } from './printCss';

/** 使用浏览器打印对话框（可选「另存为 PDF」） */
export function triggerBrowserPrint(docTitle: string, onHint?: (msg: string) => void) {
  const prev = document.title;
  document.title = docTitle;
  const cleanup = () => {
    document.title = prev;
    window.removeEventListener('afterprint', cleanup);
  };
  window.addEventListener('afterprint', cleanup);
  window.print();
  onHint?.('请在打印对话框中选择「另存为 PDF」或打印机');
}

export function printDailyReview(username: string, day: string, onHint: (msg: string) => void) {
  const title = buildDailyPdfFilename(username, day).replace(/\.pdf$/i, '');
  triggerBrowserPrint(title, onHint);
}

export function printWeeklyReview(
  username: string,
  year: number,
  week: number,
  onHint: (msg: string) => void,
) {
  const title = buildWeeklyPdfFilename(username, year, week).replace(/\.pdf$/i, '');
  triggerBrowserPrint(title, onHint);
}

export function printMonthlyReview(
  username: string,
  year: number,
  month: number,
  onHint: (msg: string) => void,
) {
  const title = buildMonthlyPdfFilename(username, year, month).replace(/\.pdf$/i, '');
  triggerBrowserPrint(title, onHint);
}
