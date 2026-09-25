import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { api, today } from './api';

// ---------- Toast ----------

const ToastContext = createContext<(msg: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.setTimeout(() => setMsg(null), 2600);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {msg && <div className="toast">{msg}</div>}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// ---------- 小组件 ----------

export function Stat({ label, value, note, gold, small, tone }: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  gold?: boolean;
  small?: boolean;
  tone?: 'pos' | 'neg' | null;
}) {
  const cls = ['stat-value', gold ? 'gold' : '', small ? 'sm' : '', tone ?? ''].join(' ');
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className={cls}>{value}</div>
      {note != null && note !== '' && <div className="stat-note">{note}</div>}
    </div>
  );
}

export function SideTag({ side }: { side: string }) {
  if (side === 'buy') return <span className="tag buy">买入</span>;
  if (side === 'sell') return <span className="tag sell">卖出</span>;
  return <span className="tag">{side}</span>;
}

export function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

// ---------- 统一下拉 / 选择器 ----------

function ChevronIcon() {
  return (
    <svg className="ui-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function useClickOutside(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [ref, onClose]);
}

export interface SelectOption { value: string; label: string }

export function Select({ value, onChange, options, placeholder = '请选择', style, className }: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  placeholder?: string;
  style?: CSSProperties;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const current = options.find(o => o.value === value);

  useClickOutside(wrapRef, () => setOpen(false));

  return (
    <div className={`ui-picker${className ? ` ${className}` : ''}`} ref={wrapRef} style={style}>
      <button type="button" className={`ui-picker-trigger${open ? ' open' : ''}`} onClick={() => setOpen(v => !v)}>
        <span className={current ? '' : 'placeholder'}>{current?.label ?? placeholder}</span>
        <ChevronIcon />
      </button>
      {open && (
        <div className="ui-dropdown">
          {options.map(o => (
            <button
              key={o.value}
              type="button"
              className={`ui-dropdown-item${o.value === value ? ' selected' : ''}`}
              onMouseDown={e => e.preventDefault()}
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- 日期 / 数字 / 股票选择 ----------

const WEEKDAY_ZH = ['日', '一', '二', '三', '四', '五', '六'];

function parseYmd(v: string): { y: number; m: number; d: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return { y, m: mo, d };
}

function toYmd(y: number, m: number, d: number) {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function formatDateLabel(v: string) {
  const p = parseYmd(v);
  if (!p) return '';
  return `${p.y} 年 ${p.m} 月 ${p.d} 日`;
}

function splitYmd(v: string) {
  const p = parseYmd(v);
  if (p) return p;
  const now = new Date();
  return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
}

export function DateInput({ value, onChange, style, className }: {
  value: string;
  onChange: (v: string) => void;
  style?: CSSProperties;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const init = splitYmd(value);
  const [viewYear, setViewYear] = useState(init.y);
  const [viewMonth, setViewMonth] = useState(init.m);

  useEffect(() => {
    const p = parseYmd(value);
    if (p) {
      setViewYear(p.y);
      setViewMonth(p.m);
    }
  }, [value]);

  useClickOutside(wrapRef, () => setOpen(false));

  const shiftMonth = (delta: number) => {
    let y = viewYear;
    let m = viewMonth + delta;
    if (m < 1) { y -= 1; m = 12; }
    else if (m > 12) { y += 1; m = 1; }
    setViewYear(y);
    setViewMonth(m);
  };

  const pickDay = (d: number) => {
    onChange(toYmd(viewYear, viewMonth, d));
    setOpen(false);
  };

  const daysInMonth = new Date(viewYear, viewMonth, 0).getDate();
  const firstWeekday = new Date(viewYear, viewMonth - 1, 1).getDay();
  const todayStr = today();
  const dayCells: ReactNode[] = [];
  for (let d = 1; d <= daysInMonth; d += 1) {
    const ymd = toYmd(viewYear, viewMonth, d);
    const selected = ymd === value;
    const isToday = ymd === todayStr;
    dayCells.push(
      <button
        key={d}
        type="button"
        className={`date-picker-cell day${selected ? ' selected' : ''}${isToday ? ' today' : ''}`}
        style={d === 1 ? { gridColumnStart: firstWeekday + 1 } : undefined}
        onClick={() => pickDay(d)}
      >
        {d}
      </button>,
    );
  }

  return (
    <div className={`ui-picker date-picker${className ? ` ${className}` : ''}`} ref={wrapRef} style={style}>
      <button type="button" className={`ui-picker-trigger${open ? ' open' : ''}`} onClick={() => setOpen(v => !v)}>
        <span className={value ? '' : 'placeholder'}>{formatDateLabel(value) || '选择日期'}</span>
        <svg className="ui-picker-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 4h16v16H4zM8 2v4M16 2v4M4 10h16" />
        </svg>
      </button>
      {open && (
        <div className="date-picker-panel">
          <div className="date-picker-head">
            <button type="button" className="ghost" onClick={() => shiftMonth(-1)} aria-label="上一月">‹</button>
            <span className="date-picker-title">{viewYear} 年 {viewMonth} 月</span>
            <button type="button" className="ghost" onClick={() => shiftMonth(1)} aria-label="下一月">›</button>
          </div>
          <div className="date-picker-grid">
            {WEEKDAY_ZH.map(w => <span key={w} className="date-picker-weekday">{w}</span>)}
            {dayCells}
          </div>
          <div className="date-picker-foot">
            <button type="button" className="ghost" onClick={() => { onChange(todayStr); setOpen(false); }}>今天</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function NumberInput({ value, onChange, placeholder, style, className }: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <input
      type="text"
      inputMode="decimal"
      className={`input-number${className ? ` ${className}` : ''}`}
      style={style}
      value={value}
      placeholder={placeholder}
      onChange={e => {
        const v = e.target.value;
        if (v === '' || /^-?\d*\.?\d*$/.test(v)) onChange(v);
      }}
    />
  );
}

export function QtyStepper({ value, onChange, step = 100, min = 0, style, className }: {
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  style?: CSSProperties;
  className?: string;
}) {
  const dec = () => onChange(Math.max(min, value - step));
  const inc = () => onChange(value + step);
  return (
    <div className={`qty-stepper${className ? ` ${className}` : ''}`} style={style}>
      <button type="button" className="qty-stepper-btn" onClick={dec} disabled={value <= min} aria-label={`减少 ${step} 股`}>−</button>
      <input
        type="text"
        inputMode="numeric"
        className="qty-stepper-input input-number"
        value={String(value)}
        onChange={e => {
          const v = e.target.value;
          if (v === '' || /^\d+$/.test(v)) onChange(v === '' ? 0 : parseInt(v, 10));
        }}
      />
      <button type="button" className="qty-stepper-btn" onClick={inc} aria-label={`增加 ${step} 股`}>+</button>
    </div>
  );
}

interface StockHit { code: string; name: string }

function usePickerClickOutside(
  wrapRef: RefObject<HTMLElement | null>,
  dropdownRef: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (wrapRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [wrapRef, dropdownRef, onClose]);
}

export function StockPicker({ code, name, onSelect, style }: {
  code: string;
  name: string;
  onSelect: (code: string, name: string) => void;
  style?: CSSProperties;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<StockHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const editingRef = useRef(false);
  const closeDropdown = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!editingRef.current) {
      setQuery(code && name ? `${code} ${name}` : code || name || '');
    }
  }, [code, name]);

  const updateDropdownPos = useCallback(() => {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    setDropdownPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
  }, []);

  useEffect(() => {
    if (!open) {
      setDropdownPos(null);
      return;
    }
    updateDropdownPos();
    window.addEventListener('scroll', updateDropdownPos, true);
    window.addEventListener('resize', updateDropdownPos);
    return () => {
      window.removeEventListener('scroll', updateDropdownPos, true);
      window.removeEventListener('resize', updateDropdownPos);
    };
  }, [open, updateDropdownPos]);

  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 1) {
      setHits([]);
      return;
    }
    const t = window.setTimeout(() => {
      setLoading(true);
      api.get<StockHit[]>(`/api/market/search/stocks?q=${encodeURIComponent(q)}`)
        .then(setHits)
        .catch(() => setHits([]))
        .finally(() => setLoading(false));
    }, 220);
    return () => window.clearTimeout(t);
  }, [query, open]);

  usePickerClickOutside(wrapRef, dropdownRef, closeDropdown);

  const pick = useCallback((hit: StockHit) => {
    onSelect(hit.code, hit.name);
    setQuery(`${hit.code} ${hit.name}`);
    setOpen(false);
  }, [onSelect]);

  useEffect(() => {
    if (!open || loading || hits.length === 0) return;
    const digits = query.replace(/\D/g, '');
    if (digits.length !== 6) return;
    const exact = hits.find(h => h.code === digits.padStart(6, '0'));
    if (exact && (!name || exact.name !== name)) {
      pick(exact);
    }
  }, [hits, loading, open, query, code, name, pick]);

  useEffect(() => {
    const digits = (code || '').replace(/\D/g, '');
    if (digits.length !== 6 || name) return;
    api.get<StockHit>(`/api/market/lookup/stock?q=${encodeURIComponent(digits)}`)
      .then(hit => {
        if (hit.name) {
          onSelect(hit.code, hit.name);
          if (!editingRef.current) setQuery(`${hit.code} ${hit.name}`);
        }
      })
      .catch(() => {});
  }, [code, name, onSelect]);

  const resolveQuery = useCallback(async () => {
    const q = query.trim();
    if (!q || (code && name)) return;
    try {
      const hit = await api.get<StockHit>(`/api/market/lookup/stock?q=${encodeURIComponent(q)}`);
      onSelect(hit.code, hit.name);
      setQuery(`${hit.code} ${hit.name}`);
    } catch {
      // 保留用户输入，提交时再提示
    }
  }, [query, code, name, onSelect]);

  const dropdown = open && dropdownPos && (loading || hits.length > 0 || query.trim().length >= 1)
    ? createPortal(
      <div
        ref={dropdownRef}
        className="ui-dropdown-fixed"
        style={{ top: dropdownPos.top, left: dropdownPos.left, width: dropdownPos.width }}
      >
        {loading && <div className="ui-dropdown-item muted">搜索中…</div>}
        {!loading && hits.length === 0 && query.trim().length >= 1 && (
          <div className="ui-dropdown-item muted">无匹配结果</div>
        )}
        {!loading && hits.map(h => (
          <button key={h.code} type="button" className="ui-dropdown-item" onMouseDown={e => e.preventDefault()} onClick={() => pick(h)}>
            <span className="mono">{h.code}</span>
            <span>{h.name}</span>
          </button>
        ))}
      </div>,
      document.body,
    )
    : null;

  return (
    <div className="ui-picker stock-picker" ref={wrapRef} style={style}>
      <input
        className={`ui-picker-field${open ? ' open' : ''}`}
        placeholder="输入代码或名称"
        value={query}
        onFocus={() => { editingRef.current = true; setOpen(true); }}
        onBlur={() => {
          editingRef.current = false;
          window.setTimeout(() => { void resolveQuery(); }, 180);
        }}
        onChange={e => {
          setQuery(e.target.value);
          setOpen(true);
          if (!e.target.value.trim()) onSelect('', '');
        }}
      />
      <ChevronIcon />
      {dropdown}
    </div>
  );
}
