import { useCallback, useEffect, useRef, useState } from 'react';
import { api, fmtMoney, today } from '../api';
import { clearPdfSettingsCache } from '../exportPdf';
import { SETTINGS_UPDATED_EVENT } from '../AiBusy';
import { Empty, NumberInput, useToast, DateInput, Select } from '../components';

const AI_BUSY_MODE_OPTIONS = [
  { value: 'banner', label: '顶部提示条（可继续操作，推荐）' },
  { value: 'overlay', label: '全屏遮罩（阻止操作直到完成）' },
];

const FLOW_KIND_OPTIONS = [
  { value: 'initial', label: '初始资金' },
  { value: 'deposit', label: '入金' },
  { value: 'withdraw', label: '出金' },
];
const KIND_LABEL: Record<string, string> = { initial: '初始资金', deposit: '入金', withdraw: '出金' };
interface FlowRow { id: number; flow_date: string; kind: string; amount: number; note: string }

type SettingsMap = Record<string, string> & { ai_score_api_key_set?: boolean; ai_ocr_api_key_set?: boolean };

export default function Settings() {
  const toast = useToast();
  const [values, setValues] = useState<SettingsMap>({});
  const [scoreKeySet, setScoreKeySet] = useState(false);
  const [ocrKeySet, setOcrKeySet] = useState(false);
  const [dataDir, setDataDir] = useState('');
  const [pickDir, setPickDir] = useState('');
  const [targetDir, setTargetDir] = useState('');
  const [targetNote, setTargetNote] = useState('');
  const [moving, setMoving] = useState(false);
  const moveLock = useRef(false);
  const previewSequence = useRef(0);
  const verifiedParent = useRef('');
  const [restartNotice, setRestartNotice] = useState<{ active_dir: string; new_dir: string } | null>(null);
  const [picking, setPicking] = useState(false);
  const [pdfPickDir, setPdfPickDir] = useState('');
  const [pickingPdf, setPickingPdf] = useState(false);
  const [testing, setTesting] = useState<'' | 'score' | 'ocr' | 'market'>('');
  const [importing, setImporting] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);
  const [flows, setFlows] = useState<FlowRow[]>([]);
  const [flowForm, setFlowForm] = useState({ flow_date: today(), kind: 'deposit', amount: '', note: '' });

  const reloadFlows = useCallback(() => {
    api.get<FlowRow[]>('/api/capital/flows').then(setFlows).catch(e => toast(String(e)));
  }, [toast]);

  useEffect(reloadFlows, [reloadFlows]);

  const refreshTargetPreview = useCallback(async (dir: string) => {
    const sequence = ++previewSequence.current;
    const trimmed = dir.trim();
    if (!trimmed) {
      setTargetDir('');
      setTargetNote('');
      return;
    }
    try {
      const r = await api.post<{ target_dir: string; note: string }>('/api/system/preview-data-dir', { target_dir: trimmed });
      if (sequence !== previewSequence.current) return;
      verifiedParent.current = trimmed;
      setTargetDir(r.target_dir);
      setTargetNote(r.note);
    } catch (e) {
      if (sequence !== previewSequence.current) return;
      verifiedParent.current = '';
      setTargetDir('');
      setTargetNote(String(e).replace(/^Error:\s*/, ''));
    }
  }, []);

  const changePickDir = useCallback((value: string) => {
    previewSequence.current += 1;
    verifiedParent.current = '';
    setTargetDir(''); setTargetNote(''); setPickDir(value);
  }, []);

  useEffect(() => () => { previewSequence.current += 1; }, []);

  useEffect(() => {
    const t = window.setTimeout(() => { refreshTargetPreview(pickDir); }, 300);
    return () => window.clearTimeout(t);
  }, [pickDir, refreshTargetPreview]);

  useEffect(() => {
    api.get<SettingsMap>('/api/settings').then(v => {
      setScoreKeySet(Boolean(v.ai_score_api_key_set));
      setOcrKeySet(Boolean(v.ai_ocr_api_key_set));
      setDataDir(v.data_dir ?? '');
      setPdfPickDir(v.pdf_export_dir ?? '');
      delete v.ai_score_api_key_set;
      delete v.ai_ocr_api_key_set;
      delete v.ai_api_key_set;
      delete v.data_dir;
      setValues(v);
    }).catch(e => toast(String(e)));
  }, [toast]);

  const set = (k: string, v: string) => setValues(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    try {
      await api.put('/api/settings', { values: { ...values, pdf_export_dir: pdfPickDir } });
      clearPdfSettingsCache();
      window.dispatchEvent(new Event(SETTINGS_UPDATED_EVENT));
      toast('设置已保存');
      if (values.ai_score_api_key) setScoreKeySet(true);
      if (values.ai_ocr_api_key) setOcrKeySet(true);
      set('ai_score_api_key', '');
      set('ai_ocr_api_key', '');
    } catch (e) { toast(String(e)); }
  };

  const pickPdfFolder = async () => {
    setPickingPdf(true);
    try {
      const r = await api.get<{ path: string | null; cancelled: boolean }>('/api/system/pick-folder');
      if (r.cancelled || !r.path) return;
      setPdfPickDir(r.path);
      set('pdf_export_dir', r.path);
    } catch (e) {
      toast(String(e).replace(/^Error:\s*/, ''));
    } finally {
      setPickingPdf(false);
    }
  };

  const pickFolder = async () => {
    setPicking(true);
    try {
      const r = await api.get<{ path: string | null; cancelled: boolean }>('/api/system/pick-folder');
      if (r.cancelled || !r.path) return;
      changePickDir(r.path);
    } catch (e) {
      toast(String(e).replace(/^Error:\s*/, ''));
    } finally {
      setPicking(false);
    }
  };

  const moveData = async () => {
    if (moveLock.current || restartNotice) return;
    const parent = pickDir.trim();
    if (!parent) { toast('请先选择目标文件夹'); return; }
    if (!targetDir) { toast(targetNote || '目标路径无效'); return; }
    if (verifiedParent.current !== parent) { toast('目标位置正在校验，请稍候'); return; }
    if (!window.confirm(`将把数据复制到：\n${targetDir}\n\n原数据保留；复制完成后暂停写入，请完全退出并重新启动程序以使用目标目录。确认继续？`)) return;
    moveLock.current = true;
    setMoving(true);
    try {
      const r = await api.post<{ new_dir: string; active_dir?: string; restart_required?: boolean; need_restart?: boolean }>('/api/system/move-data', { target_dir: parent });
      const active = r.active_dir || dataDir;
      setDataDir(active);
      setRestartNotice({ active_dir: active, new_dir: r.new_dir });
      toast('数据已复制，当前目录保持不变。请完全退出并重新启动程序以使用新目录。');
    } catch (e) { toast(String(e)); } finally { moveLock.current = false; setMoving(false); }
  };

  const testAI = async (kind: 'score' | 'ocr') => {
    setTesting(kind);
    try {
      const payload = kind === 'ocr'
        ? { kind, base_url: values.ai_ocr_base_url ?? '', api_key: values.ai_ocr_api_key ?? '', model: values.ai_ocr_vision_model ?? '' }
        : { kind, base_url: values.ai_score_base_url ?? '', api_key: values.ai_score_api_key ?? '', model: values.ai_score_text_model ?? '' };
      const r = await api.post<{ ok: boolean; message: string }>('/api/settings/test-ai', payload);
      toast(r.message);
    } catch (e) { toast(String(e)); } finally { setTesting(''); }
  };

  const testMarket = async () => {
    setTesting('market');
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/api/settings/test-market');
      toast(r.message);
    } catch (e) { toast(String(e)); } finally { setTesting(''); }
  };

  const exportBackup = () => {
    window.open('/journal-app/api/export/json', '_blank');
  };

  const importBackup = async (file: File) => {
    if (!window.confirm('将从备份恢复全量数据并覆盖现有数据，确定继续？')) return;
    setImporting(true);
    try {
      const payload = JSON.parse(await file.text()) as Record<string, unknown>;
      const r = await api.post<{ ok: boolean; counts: Record<string, number> }>('/api/import/json', { data: payload });
      const summary = Object.entries(r.counts).map(([k, v]) => `${k} ${v}`).join(' · ');
      toast(`恢复成功：${summary}`);
      setTimeout(() => window.location.reload(), 1600);
    } catch (e) { toast(String(e)); } finally {
      setImporting(false);
      if (importRef.current) importRef.current.value = '';
    }
  };

  const addFlow = async () => {
    const amount = parseFloat(flowForm.amount);
    if (!amount || amount <= 0) { toast('请输入有效金额'); return; }
    try {
      await api.post('/api/capital/flows', { ...flowForm, amount });
      setFlowForm({ ...flowForm, amount: '', note: '' });
      toast('已记录');
      reloadFlows();
    } catch (e) { toast(String(e)); }
  };

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h2 className="page-title">设置</h2>
          <div className="page-sub">费率、出入金、行情源与 AI 配置</div>
        </div>
        <button className="primary" onClick={save} disabled={moving || Boolean(restartNotice)}>保存全部设置</button>
      </div>

      {restartNotice && <div className="card" role="status" style={{ marginBottom: 18 }}><h3>目录复制完成，等待重启</h3><p>当前读取目录：{restartNotice.active_dir}</p><p>下次启动目录：{restartNotice.new_dir}</p><p>原数据仍保留，服务已暂停写入。请从托盘完全退出程序，再重新打开；刷新页面不会切换数据目录。</p></div>}

      <div className="card" style={{ marginBottom: 18 }}>
        <h3 className="card-title">出入金流水</h3>
        <div className="muted" style={{ marginBottom: 12 }}>
          初始资金、入金、出金按单位净值法折算份额，收益率不受出入金影响。
        </div>
        <div className="row" style={{ marginBottom: 16 }}>
          <DateInput value={flowForm.flow_date} onChange={v => setFlowForm({ ...flowForm, flow_date: v })} />
          <Select
            value={flowForm.kind}
            onChange={v => setFlowForm({ ...flowForm, kind: v })}
            options={FLOW_KIND_OPTIONS}
            style={{ width: 118 }}
          />
          <NumberInput placeholder="金额" style={{ flex: 1, minWidth: 100 }}
            value={flowForm.amount}
            onChange={v => setFlowForm({ ...flowForm, amount: v })} />
          <button className="primary" onClick={addFlow}>记录</button>
        </div>
        {flows.length === 0 ? <Empty text="暂无流水，请先添加初始资金" /> : (
          <table>
            <thead><tr><th>日期</th><th>类型</th><th style={{ textAlign: 'right' }}>金额</th><th>备注</th><th /></tr></thead>
            <tbody>
              {flows.map(f => (
                <tr key={f.id}>
                  <td>{f.flow_date}</td>
                  <td><span className={`tag${f.kind === 'withdraw' ? ' sell' : ' gold'}`}>{KIND_LABEL[f.kind] ?? f.kind}</span></td>
                  <td style={{ textAlign: 'right' }} className="mono">¥{fmtMoney(f.amount)}</td>
                  <td className="muted">{f.note || '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="danger-ghost" onClick={async () => {
                      await api.del(`/api/capital/flows/${f.id}`); reloadFlows();
                    }}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <h3 className="card-title">胜利目标</h3>
        <div className="grid grid-2">
          <label className="field"><span>节点总数（默认 50）</span>
            <NumberInput value={values.node_count ?? ''} onChange={v => set('node_count', v)} />
          </label>
          <label className="field"><span>每节点涨幅 %（默认 30，即净值阶梯 ×1.3ⁿ）</span>
            <NumberInput value={values.wave_pct ?? ''} onChange={v => set('wave_pct', v)} />
          </label>
        </div>
        <div className="muted">修改后所有节点判定、进度条、耗时统计立即按新目标重算，历史数据不受影响。</div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h3 className="card-title">交易费率</h3>
          <label className="field"><span>佣金费率（如 0.00025 = 万2.5）</span>
            <input value={values.commission_rate ?? ''} onChange={e => set('commission_rate', e.target.value)} />
          </label>
          <label className="field"><span>最低佣金（元）</span>
            <input value={values.commission_min ?? ''} onChange={e => set('commission_min', e.target.value)} />
          </label>
          <label className="field"><span>印花税率（卖出，如 0.0005 = 万5）</span>
            <input value={values.stamp_tax_rate ?? ''} onChange={e => set('stamp_tax_rate', e.target.value)} />
          </label>
          <label className="field"><span>过户费率（如 0.00001）</span>
            <input value={values.transfer_fee_rate ?? ''} onChange={e => set('transfer_fee_rate', e.target.value)} />
          </label>
        </div>

        <div className="card">
          <h3 className="card-title">
            <span>行情数据源</span>
            <button className="ghost no-print" onClick={testMarket} disabled={testing !== ''}>
              {testing === 'market' ? '测试中…' : '测试连接'}
            </button>
          </h3>
          <label className="field"><span>优先级链路（逗号分隔：tdx / akshare / web）</span>
            <input value={values.market_priority ?? ''} onChange={e => set('market_priority', e.target.value)} />
          </label>
          <label className="field"><span>通达信 vipdoc 目录路径</span>
            <input value={values.tdx_path ?? ''} onChange={e => set('tdx_path', e.target.value)} placeholder="D:\new_tdx\vipdoc" />
          </label>
          <div className="muted">
            tdx：本地通达信日线直读，最快；akshare：免费公开接口；web：东方财富公开行情。逐源降级尝试。
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">AI 等待提示</h3>
        <label className="field" style={{ maxWidth: 420 }}>
          <span>长任务（打分、复盘、预演分析）进行时的提示方式</span>
          <Select
            value={values.ai_busy_mode === 'overlay' ? 'overlay' : 'banner'}
            onChange={v => set('ai_busy_mode', v)}
            options={AI_BUSY_MODE_OPTIONS}
          />
        </label>
        <div className="muted">
          顶部提示条不会挡住页面，适合等待较久时继续查看或编辑；全屏遮罩会阻止操作直到 AI 返回。
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">
          <span>AI 操作打分（文本模型）</span>
          <button className="ghost no-print" onClick={() => testAI('score')} disabled={testing !== ''}>
            {testing === 'score' ? '测试中…' : '测试连接'}
          </button>
        </h3>
        <div className="grid grid-3">
          <label className="field"><span>Base URL</span>
            <input value={values.ai_score_base_url ?? ''} onChange={e => set('ai_score_base_url', e.target.value)}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          </label>
          <label className="field"><span>API Key {scoreKeySet && <span className="tag gold">已配置</span>}</span>
            <input type="password" value={values.ai_score_api_key ?? ''} onChange={e => set('ai_score_api_key', e.target.value)}
              placeholder={scoreKeySet ? '留空则保持不变' : 'sk-…'} />
          </label>
          <label className="field"><span>文本模型</span>
            <input value={values.ai_score_text_model ?? ''} onChange={e => set('ai_score_text_model', e.target.value)} placeholder="如 qwen-plus / glm-4-air" />
          </label>
        </div>
        <div className="muted">仅在「每日复盘」的「AI 打分」使用。未配置时打分可手动填写。</div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">
          <span>AI 截图识别（视觉模型）</span>
          <button className="ghost no-print" onClick={() => testAI('ocr')} disabled={testing !== ''}>
            {testing === 'ocr' ? '测试中…' : '测试连接'}
          </button>
        </h3>
        <div className="grid grid-3">
          <label className="field"><span>Base URL</span>
            <input value={values.ai_ocr_base_url ?? ''} onChange={e => set('ai_ocr_base_url', e.target.value)}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          </label>
          <label className="field"><span>API Key {ocrKeySet && <span className="tag gold">已配置</span>}</span>
            <input type="password" value={values.ai_ocr_api_key ?? ''} onChange={e => set('ai_ocr_api_key', e.target.value)}
              placeholder={ocrKeySet ? '留空则保持不变' : 'sk-…'} />
          </label>
          <label className="field"><span>视觉模型</span>
            <input value={values.ai_ocr_vision_model ?? ''} onChange={e => set('ai_ocr_vision_model', e.target.value)} placeholder="如 qwen-vl-plus / glm-4v" />
          </label>
        </div>
        <div className="muted">仅在「上传截图识别」交易时使用，与打分互不影响。未配置时交易可手动录入。</div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">PDF 导出</h3>
        <div className="grid grid-2">
          <label className="field"><span>用户名（用于 PDF 页眉与文件名）</span>
            <input
              value={values.pdf_username ?? ''}
              onChange={e => set('pdf_username', e.target.value)}
              placeholder="如：张三"
            />
          </label>
          <label className="field"><span>PDF 保存路径</span>
            <div className="row" style={{ marginTop: 0 }}>
              <input
                style={{ flex: 1, minWidth: 200 }}
                placeholder="留空则使用浏览器打印对话框另存为 PDF"
                value={pdfPickDir}
                onChange={e => { setPdfPickDir(e.target.value); set('pdf_export_dir', e.target.value); }}
              />
              <button type="button" onClick={pickPdfFolder} disabled={pickingPdf}>
                {pickingPdf ? '选择中…' : '浏览…'}
              </button>
            </div>
          </label>
        </div>
        <div className="muted">
          配置保存路径后，导出将直接写入该目录（需 Windows Edge）。文件名示例：
          <span className="mono">张三 Trading MS 7月10日 复盘日志.pdf</span>
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">数据备份与恢复</h3>
        <div className="muted" style={{ marginBottom: 12 }}>
          导出 JSON 全量备份（交易、快照、复盘、闪记等）；恢复将覆盖当前全部数据。
        </div>
        <div className="row">
          <button onClick={exportBackup}>导出 JSON 备份</button>
          <input ref={importRef} type="file" accept="application/json,.json" style={{ display: 'none' }}
            onChange={e => e.target.files?.[0] && void importBackup(e.target.files[0])} />
          <button className="primary" onClick={() => importRef.current?.click()} disabled={importing}>
            {importing ? '恢复中…' : '从 JSON 恢复'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3 className="card-title">数据存储位置</h3>
        <div className="data-dir-panel">
          <div className="data-dir-current">
            <span className="data-dir-label">当前位置</span>
            <div className="data-dir-path">{dataDir || '—'}</div>
          </div>
          <div>
            <span className="data-dir-label">迁移到新位置</span>
            <div className="row" style={{ marginTop: 6 }}>
              <input
                placeholder="点击「浏览…」选择，或手动粘贴路径"
                style={{ flex: 1, minWidth: 240 }}
                value={pickDir}
                disabled={moving || Boolean(restartNotice)}
                onChange={e => changePickDir(e.target.value)}
              />
              <button type="button" onClick={pickFolder} disabled={picking || moving || Boolean(restartNotice)}>
                {picking ? '选择中…' : '浏览…'}
              </button>
              <button className="primary" onClick={moveData} disabled={moving || !targetDir || Boolean(restartNotice)}>
                {moving ? '复制中…' : restartNotice ? '等待完全退出并重启' : '复制数据并准备重启'}
              </button>
            </div>
            {targetDir && targetDir !== pickDir && (
              <div className="data-dir-preview">
                实际存储路径：<strong>{targetDir}</strong>
                <br />{targetNote}
              </div>
            )}
            {targetDir && targetDir === pickDir && targetNote && (
              <div className="data-dir-preview">{targetNote}</div>
            )}
            {!targetDir && targetNote && pickDir.trim() && (
              <div className="data-dir-preview" style={{ borderColor: 'rgba(255,107,107,0.35)', background: 'rgba(255,107,107,0.08)', color: 'var(--up)' }}>
                {targetNote}
              </div>
            )}
          </div>
        </div>
        <div className="muted" style={{ marginTop: 12 }}>
          选择文件夹后，若目录非空将自动在其下创建 <span className="mono">TradingMS-data</span> 子文件夹。换电脑时拷走数据目录即可。
        </div>
      </div>
    </div>
  );
}
