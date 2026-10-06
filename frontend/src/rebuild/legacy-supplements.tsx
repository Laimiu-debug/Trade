import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Action = { kind:string; target_scope?:string; account_id?:string; expected_revision?:number; before:unknown; after:unknown; diff?:Array<{path:string;before:unknown;after:unknown}>; [key:string]:unknown }
type Item = { key:string; label:string; source:unknown; status:'ready'|'blocked'|'applied'; reason:string|null; action:Action|null; target?:unknown }
type Catalog = { import_id:string; expected_revision:number; account_id:string|null; source_sha256:string; logical_sha256:string; items:Item[]; unmapped:Array<{source:string;reason:string}>; notes:string[]; selected_keys?:string[]; selected?:Item[]; can_apply?:boolean; preview_sha256?:string }
type Batch = { result:{id:string;revision:number;created_at:string;targets:unknown[]}; preview:Catalog }
const style = {whiteSpace:'pre-wrap' as const,overflowWrap:'anywhere' as const,maxHeight:280,overflow:'auto'}
const pretty=(value:unknown)=>JSON.stringify(value,null,2)
const settingsNames:Record<string,string>={fees:'账户费用',targets:'账户目标',market_sources:'全局行情来源',ai:'全局模型配置',print:'打印署名与本机导出目录'}
const label=(item:Item)=>item.key.startsWith('setting:') ? settingsNames[item.key.slice(8)]||item.label : `${item.label} · ${item.key}`

export function LegacySupplementEditor({importId,onChanged,onBusy}:{importId:string;onChanged?:()=>void|Promise<void>;onBusy?:(value:boolean)=>void}) {
  const [catalog,setCatalog]=useState<Catalog|null>(null)
  const [selected,setSelected]=useState<string[]>([])
  const [refs,setRefs]=useState({text:'TRADE_AI_TEXT_KEY',vision:'TRADE_AI_VISION_KEY'})
  const [preview,setPreview]=useState<Catalog|null>(null)
  const [ack,setAck]=useState(false)
  const [history,setHistory]=useState<Batch[]>([])
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  useEffect(()=>{let live=true;Promise.all([api<Catalog>(`/legacy-imports/${importId}/supplements`),api<Batch[]>(`/legacy-imports/${importId}/supplements/history`)]).then(([value,batches])=>{if(live){setCatalog(value);setHistory(batches)}}).catch(err=>{if(live)setError(err.message)});return()=>{live=false}},[importId])
  function invalidate(){setPreview(null);setAck(false);setNotice('')}
  async function work(action:()=>Promise<void>){setBusy(true);onBusy?.(true);setError('');try{await action()}catch(err){setError(err instanceof Error?err.message:'补充映射失败')}finally{setBusy(false);onBusy?.(false)}}
  const body=()=>({expected_revision:catalog!.expected_revision,selected_keys:selected,secret_refs:refs})
  async function reload(){const value=await api<Catalog>(`/legacy-imports/${importId}/supplements`);setCatalog(value);invalidate();setNotice('最新来源与目标已读取，原勾选和环境引用保留；请重新核对。')}
  async function inspect(){if(!catalog)return;const value=await api<Catalog>(`/legacy-imports/${importId}/supplements/preview`,'POST',body());setCatalog(value);invalidate();setNotice('已校验当前环境变量名称与目标版本，尚未写入。')}
  async function prepare(){if(!catalog)return;const value=await api<Catalog>(`/legacy-imports/${importId}/supplements/preview`,'POST',body());setPreview(value);setAck(false)}
  async function apply(){if(!preview?.preview_sha256||!ack)return;await api(`/legacy-imports/${importId}/supplements/apply`,'POST',{...body(),expected_preview_sha256:preview.preview_sha256,acknowledge_limitations:true});setSelected([]);setPreview(null);setAck(false);setCatalog(await api(`/legacy-imports/${importId}/supplements`));setHistory(await api(`/legacy-imports/${importId}/supplements/history`));setNotice('已保存所选补充映射；原档案和原文件不变。');await onChanged?.()}
  return <section className="market-detail"><h3>旧卡片、设置与回合 · 逐项补充映射</h3><p className="subtle">先查看来源与目标，勾选后预览差异，再确认本批。每项仅迁入一次。未提供图片二进制时保留引用说明，不读取旧路径。</p>{error&&<p className="alert error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {catalog&&<><p>档案修订 {catalog.expected_revision} · 关联账户 {catalog.account_id||'无，费用/目标/回合需先转换核心实盘'}</p><div className="toolbar"><button className="button secondary" disabled={busy} onClick={()=>void work(reload)}><Icon name="refresh" />重新读取来源与目标</button></div>
      <details><summary>模型配置迁入的环境变量引用</summary><p className="muted">只填写环境变量名称，不填写密钥值。旧密钥已脱敏，不会迁入；这些默认名称可修改，远程模型地址需要有效引用。保存配置不会调用模型。</p><div className="form-grid">{(['text','vision'] as const).map(channel=><label key={channel} className="field"><span>{channel==='text'?'文本':'视觉'}迁入密钥环境引用</span><input autoComplete="off" value={refs[channel]} disabled={busy} maxLength={89} onChange={event=>{setRefs(current=>({...current,[channel]:event.target.value}));invalidate()}} /></label>)}</div><button className="button secondary" disabled={busy} onClick={()=>void work(inspect)}>核对环境引用与最新目标</button></details>
      <div className="table-wrap"><table><thead><tr><th>选择</th><th>来源项目</th><th>目标 / 状态</th><th>对照</th></tr></thead><tbody>{catalog.items.map(item=><tr key={item.key}><td><input type="checkbox" aria-label={`补充迁入 ${label(item)}`} checked={selected.includes(item.key)} disabled={busy||(!selected.includes(item.key)&&(item.status!=='ready'||selected.length>=500))} onChange={event=>{setSelected(current=>event.target.checked?[...current,item.key]:current.filter(key=>key!==item.key));invalidate()}} /></td><td>{label(item)}</td><td>{item.status==='applied'?'已迁入':item.status==='blocked'?'不能直接映射':item.action?.account_id?'仅关联账户':'当前数据目录共享'}<p className="muted">{item.reason}</p>{item.action?.expected_revision!==undefined&&<small>目标版本 {item.action.expected_revision}</small>}</td><td><details><summary>查看原字段与拟写入内容</summary><pre style={style}>{pretty({source:item.source,action:item.action,existing_target:item.target})}</pre></details></td></tr>)}</tbody></table></div>{!catalog.items.length&&<p className="muted">没有当前支持的补充项目；资料继续保留在只读档案。</p>}
      {catalog.unmapped.length>0&&<details><summary>继续保留只读的项目与附件说明 · {catalog.unmapped.length}</summary>{catalog.unmapped.map((item,index)=><p key={index}>{item.source}：{item.reason}</p>)}</details>}
      <button className="button secondary" disabled={busy||!selected.length} onClick={()=>void work(prepare)}>预览所选 {selected.length} 项补充映射</button>
    </>}
    {preview&&<div className="market-detail"><h4>本批补充映射预览</h4><p style={{overflowWrap:'anywhere'}}>档案修订 {preview.expected_revision} · 预览 SHA256 {preview.preview_sha256}</p>{preview.selected?.map(item=><div key={item.key}><h4>{label(item)}</h4>{item.reason&&<p className="danger">{item.reason}</p>}<pre style={style}>{pretty(item.action?.diff||{before:item.action?.before,after:item.action?.after,identity:item.action})}</pre></div>)}{preview.notes.map(note=><p className="muted" key={note}>{note}</p>)}<label className="check-field"><input type="checkbox" disabled={busy||!preview.can_apply} checked={ack} onChange={event=>setAck(event.target.checked)} />我已核对本批每项来源、目标账户和前后差异，确认仅应用所选项目</label><button className="button primary" disabled={busy||!ack||!preview.can_apply} onClick={()=>void work(apply)}><Icon name="check" />确认应用本批补充映射</button>{!preview.can_apply&&<p className="danger">有项目无法安全映射，请取消该项或修复来源后重新预览。</p>}</div>}
    <details><summary>补充映射历史 · {history.length} 批</summary>{history.map(batch=><details key={batch.result.id}><summary>修订 {batch.result.revision} · {batch.result.created_at} · {batch.result.targets.length} 项</summary><pre style={style}>{pretty(batch)}</pre></details>)}</details>
  </section>
}
