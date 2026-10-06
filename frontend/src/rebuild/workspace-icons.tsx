import {
  Activity, AlertTriangle, ArrowLeftRight, ArrowRight, BarChart3, Bell, BookOpen, Bot, Boxes, Calculator,
  CalendarRange, Check, ChevronDown, CircleDollarSign, ClipboardList, Copy, Crosshair, Database, Download,
  FileSpreadsheet, FileText, Filter, FlaskConical, Flag, FolderOpen, Gauge, History, Inbox, Info, Layers,
  LayoutDashboard, LayoutList, Lightbulb, LineChart, ListChecks, Moon, Newspaper, NotebookPen, PieChart,
  Play, Plus, RefreshCw, Rows3, Save, Search, Settings, Share2, Sparkles, Sun, Target, Trash2, TrendingUp,
  Upload, Wallet, X, type LucideIcon,
} from 'lucide-react'

/** Icons by workspace role. Pages and components refer to names, never to icon files. */
const icons = {
  // Main navigation
  overview: LayoutDashboard, trades: ArrowLeftRight, flows: Wallet, snapshots: Layers, review: NotebookPen,
  period: CalendarRange, statistics: BarChart3, market: LineChart, research: FlaskConical, signals: Crosshair,
  backtest: History, news: Newspaper, events: Database, valuation: Gauge, insights: Lightbulb, ai: Sparkles,
  tasks: ListChecks, settings: Settings, share: Share2, simulation: Play,
  // Actions
  add: Plus, refresh: RefreshCw, download: Download, upload: Upload, save: Save, delete: Trash2, close: X,
  copy: Copy, search: Search, filter: Filter, open: FolderOpen, next: ArrowRight, expand: ChevronDown,
  check: Check, theme: Moon, light: Sun, density: Rows3, comfortable: LayoutList, run: Play, bot: Bot,
  // Content roles
  asset: CircleDollarSign, nav: TrendingUp, target: Target, count: ClipboardList, chart: Activity,
  reminder: Bell, holdings: PieChart, data: Boxes, warning: AlertTriangle, info: Info, empty: Inbox,
  document: FileText, spreadsheet: FileSpreadsheet, flag: Flag, guide: BookOpen, calculator: Calculator,
} satisfies Record<string, LucideIcon>

export type IconName = keyof typeof icons
export type IconTone = 'blue' | 'green' | 'amber' | 'violet' | 'teal' | 'pink'

export function Icon({ name, size, className }: { name: IconName | string, size?: number, className?: string }) {
  const Component = icons[name as IconName] ?? LayoutDashboard
  return <Component aria-hidden="true" focusable="false" className={className ?? 'workspace-icon'} size={size} strokeWidth={1.8} />
}

/** Legacy name kept for existing navigation call sites. */
export function WorkspaceIcon({ name }: { name: string }) {
  return <Icon name={name} />
}

/** A tinted square that frames an icon, used for page, card and metric headings. */
export function IconTile({ name, tone = 'blue', size = 'md' }: { name: IconName | string, tone?: IconTone, size?: 'sm' | 'md' | 'lg' }) {
  return <span className={`icon-tile icon-tile-${size}`} data-tone={tone} aria-hidden="true"><Icon name={name} className="icon-tile-glyph" /></span>
}
