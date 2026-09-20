import { useCallback, useEffect, useRef, useState } from 'react'
import { batchAnnotate, fetchExceedanceIds, listExceedances } from '../../api/exceedances.js'
import Pagination from '../../components/common/Pagination.jsx'
import { SectionCard } from '../../components/common/Card.jsx'
import { Alert } from '../../components/common/Feedback.jsx'
import Tag from '../../components/common/Tag.jsx'
import { useToast } from '../../components/common/ToastProvider.jsx'
import { useListQuery } from '../../hooks/useListQuery.js'
import AnnotationModal from './components/AnnotationModal.jsx'
import BatchResultModal from './components/BatchResultModal.jsx'
import ExceedanceFilters from './components/ExceedanceFilters.jsx'
import ExceedanceSummaryCards from './components/ExceedanceSummaryCards.jsx'
import ExceedanceTable from './components/ExceedanceTable.jsx'

const INITIAL_FILTERS = {
  status: '',
  level: '',
  pollutant: '',
  station_id: '',
  date_from: '',
  date_to: '',
  keyword: ''
}

const MAX_BATCH_SIZE = 500

const newRequestId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `batch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`

export default function ExceedancesPage() {
  const toast = useToast()
  const query = useListQuery(listExceedances, INITIAL_FILTERS)
  const [selected, setSelected] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [batch, setBatch] = useState({ status: 'confirmed', mode: 'atomic', note: '', annotator: '' })
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const requestIdRef = useRef(null)

  const { reload } = query

  // 勾选或批量意图变化后, 重新生成幂等键; 提交失败重试时保持不变
  useEffect(() => {
    requestIdRef.current = null
  }, [selected, batch.status, batch.mode, batch.note, batch.annotator])

  const toggleRow = useCallback((id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }, [])

  const toggleAll = useCallback(
    (ids) => {
      setSelected((prev) => (ids.every((id) => prev.includes(id)) ? prev.filter((id) => !ids.includes(id)) : Array.from(new Set([...prev, ...ids]))))
    },
    []
  )

  const selectAllPages = async () => {
    try {
      const payload = await fetchExceedanceIds(query.filters)
      setSelected((prev) => Array.from(new Set([...prev, ...payload.ids])))
      if (payload.truncated) {
        toast.warning(`筛选范围内共 ${payload.total} 条, 一次最多处理 ${MAX_BATCH_SIZE} 条, 已选中前 ${payload.ids.length} 条`)
      } else {
        toast.info(`已跨页选中全部 ${payload.ids.length} 条记录`)
      }
    } catch (error) {
      toast.error(error.message)
    }
  }

  const submitBatch = async () => {
    if (selected.length === 0) {
      toast.warning('请先勾选需要标注的超标记录')
      return
    }
    if (selected.length > MAX_BATCH_SIZE) {
      toast.error(`一次最多提交 ${MAX_BATCH_SIZE} 条, 当前已选 ${selected.length} 条, 请分批处理`)
      return
    }
    if (!batch.annotator.trim()) {
      toast.warning('请填写标注人, 批量操作必须留痕')
      return
    }
    if (batch.status !== 'pending' && !batch.note.trim()) {
      toast.warning('确认或忽略前必须填写标注说明, 否则不允许提交')
      return
    }
    if (!requestIdRef.current) requestIdRef.current = newRequestId()

    setBusy(true)
    try {
      const payload = await batchAnnotate({
        ids: selected,
        status: batch.status,
        mode: batch.mode,
        note: batch.note.trim() || null,
        annotator: batch.annotator.trim(),
        request_id: requestIdRef.current
      })
      if (payload.deduplicated) {
        toast.info('该批次已提交过, 仅生效一次, 展示首次处理结果')
      } else if (payload.failed?.length) {
        toast.warning(`已处理 ${payload.updated} 条, ${payload.failed.length} 条未处理`)
      } else {
        toast.success(`已标注 ${payload.updated} 条记录`)
      }
      setResult(payload)
      setSelected([])
      setBatch((prev) => ({ ...prev, note: '' }))
      requestIdRef.current = null
      // 待办数量 / 状态分布 / 等级分布 / 高发因子排名随列表一并刷新
      await reload()
    } catch (error) {
      // 整批模式下的失败: 一条都不生效, 保留勾选便于修正后重试
      toast.error(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <ExceedanceSummaryCards summary={query.summary} />

      <ExceedanceFilters
        value={query.filters}
        loading={query.loading}
        onSubmit={(next) => {
          setSelected([])
          query.setFilters(next)
        }}
        onReset={() => {
          setSelected([])
          query.setFilters(INITIAL_FILTERS)
        }}
      />

      {query.error ? <Alert tone="error">{query.error.message}</Alert> : null}

      <SectionCard
        title="超标记录工作台"
        hint="点击行可打开单条标注; 勾选支持跨页保留, 可批量确认或忽略"
        actions={
          <>
            <Tag tone="primary">已选 {selected.length} 条</Tag>
            <button
              type="button"
              className="btn btn-sm"
              onClick={selectAllPages}
              disabled={query.loading || query.total === 0}
            >
              全选全部 {query.total} 条
            </button>
            <button type="button" className="btn btn-sm" onClick={reload} disabled={query.loading}>
              刷新
            </button>
          </>
        }
      >
        <div className="stack">
          <div className="card" style={{ boxShadow: 'none' }}>
            <div className="card-body tight">
              <div className="inline">
                <span className="field-label">批量标注</span>
                <select
                  className="select"
                  style={{ width: 150 }}
                  value={batch.status}
                  onChange={(event) => setBatch({ ...batch, status: event.target.value })}
                >
                  <option value="confirmed">已确认</option>
                  <option value="ignored">已忽略</option>
                  <option value="pending">重置为待标注</option>
                </select>
                <select
                  className="select"
                  style={{ width: 220 }}
                  value={batch.mode}
                  onChange={(event) => setBatch({ ...batch, mode: event.target.value })}
                  title="整批生效: 任一记录不可处理则全部不生效; 逐条处理: 跳过失败项并逐条说明原因"
                >
                  <option value="atomic">整批生效 (任一失败全部不生效)</option>
                  <option value="partial">逐条处理 (跳过失败项)</option>
                </select>
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 220 }}
                  placeholder={batch.status === 'pending' ? '标注说明 (选填)' : '标注说明 (确认或忽略时必填)'}
                  value={batch.note}
                  onChange={(event) => setBatch({ ...batch, note: event.target.value })}
                />
                <input
                  className="input"
                  style={{ width: 140 }}
                  placeholder="标注人 (必填)"
                  value={batch.annotator}
                  onChange={(event) => setBatch({ ...batch, annotator: event.target.value })}
                />
                <button type="button" className="btn btn-primary" onClick={submitBatch} disabled={busy}>
                  {busy ? '提交中...' : '提交批量标注'}
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setSelected([])}
                  disabled={selected.length === 0}
                >
                  清空选择
                </button>
              </div>
            </div>
          </div>

          <ExceedanceTable
            rows={query.items}
            loading={query.loading}
            selectedIds={selected}
            onToggleRow={toggleRow}
            onToggleAll={toggleAll}
            onOpen={(row) => setActiveId(row.id)}
          />
          <Pagination
            page={query.page}
            pages={query.pages}
            total={query.total}
            pageSize={query.pageSize}
            onPageChange={query.setPage}
            onPageSizeChange={query.setPageSize}
          />
        </div>
      </SectionCard>

      <AnnotationModal
        exceedanceId={activeId}
        onClose={() => setActiveId(null)}
        onSaved={() => {
          setActiveId(null)
          reload()
        }}
      />

      <BatchResultModal result={result} onClose={() => setResult(null)} />
    </>
  )
}
