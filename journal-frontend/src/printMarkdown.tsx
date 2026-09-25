import type { ReactNode } from 'react';

function parseInline(text: string, keyBase: number): ReactNode {
  const parts: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(<strong key={`${keyBase}-b${i++}`}>{m[1]}</strong>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  if (parts.length === 0) return text;
  if (parts.length === 1) return parts[0];
  return <>{parts}</>;
}

/** 将 AI 输出的 Markdown 轻量转为打印友好排版（去除 # 等符号） */
export function PrintMarkdownBody({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let listBuf: string[] = [];
  let key = 0;

  const flushList = () => {
    if (!listBuf.length) return;
    blocks.push(
      <ul className="print-md-list" key={key++}>
        {listBuf.map((item, idx) => (
          <li key={idx}>{parseInline(item, key * 100 + idx)}</li>
        ))}
      </ul>,
    );
    listBuf = [];
  };

  for (const raw of lines) {
    const t = raw.trim();
    if (!t) {
      flushList();
      continue;
    }

    const heading = t.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      flushList();
      blocks.push(
        <div className="print-md-heading" key={key++}>
          {parseInline(heading[1], key)}
        </div>,
      );
      continue;
    }

    const bullet = t.match(/^[-*•]\s+(.+)$/);
    if (bullet) {
      listBuf.push(bullet[1]);
      continue;
    }

    const numbered = t.match(/^\d+\.\s+(.+)$/);
    if (numbered) {
      listBuf.push(numbered[1]);
      continue;
    }

    flushList();
    blocks.push(
      <p className="print-md-p" key={key++}>
        {parseInline(t, key)}
      </p>,
    );
  }
  flushList();

  if (!blocks.length) return null;
  return <div className="print-markdown">{blocks}</div>;
}
