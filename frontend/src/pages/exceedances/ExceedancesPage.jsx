import { useCallback, useState } from 'react'
import { listAnnotationBatches, listExceedances } from '../../api/exceedances.js'
import Pagination from '../../components/common/Pagination.jsx'
import { SectionCard } from '../../components/common/Card.jsx'
import { Alert } from '../../components/common/Feedback.jsx'
import Tag from '../../components/common/Tag.jsx'
import { useToast } from '../../components/common/ToastProvider.jsx'
import { useAsyncData } from '../../hooks/useAsyncData.js'
import { useListQuery } from '../../hooks/useListQuery.js'
import AnnotationModal from './components/AnnotationModal.jsx'
import BatchAnnotateDialog from './components/BatchAnnotateDialog.jsx'
import BatchOperationLog from './components/BatchOperationLog.jsx'
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

const ANNOTATOR_STORAGE_KEY = 'exceedance.annotator'

/** 每次批量操作生成一个幂等批次号: 同一批重复提交只产生一次处理结果. */
function newBatchKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `batch-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export default function ExceedancesPage() {
  const toast = useToast()
  const query = useListQuery(listExceedances, INITIAL_FILTERS)
  const loadBatches = useCallback(() => listAnnotationBatches({ limit: 8 }), [])
  const batchesQuery = useAsyncData(loadBatches)
  const [selected, setSelected] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [batch, setBatch] = useState({
    status: 'confirmed',
    note: '',
    annotator: localStorage.getItem(ANNOTATOR_STORAGE_KEY) || ''
  })
  const [batchErrors, setBatchErrors] = useState({})
  const [intent, setIntent] = useState(null)

  const { reload } = query
  const { reload: reloadBatches } = batchesQuery

  const toggleRow = useCallback((id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }, [])

  const toggleAll = useCallback((ids) => {
    setSelected((prev) =>
      ids.every((id) => prev.includes(id))
        ? prev.filter((id) => !ids.includes(id))
        : Array.from(new Set([...prev, ...ids]))
    )
  }, [])

  // 提交前校验: 每次批量操作必须留下操作人; 确认或忽略必须填写说明, 否则不允许提交
  const openBatchConfirm = () => {
    if (selected.length === 0) {
      toast.warning('请先勾选需要标注的超标记录')
      return
    }
    const errors = {}
    if (!batch.annotator.trim()) errors.annotator = '请填写操作人'
    if (batch.status !== 'pending' && !batch.note.trim()) {
      errors.note = '确认或忽略时必须填写标注说明'
    }
    setBatchErrors(errors)
    if (Object.keys(errors).length > 0) return

    const annotator = batch.annotator.trim()
    localStorage.setItem(ANNOTATOR_STORAGE_KEY, annotator)
    setIntent({
      key: newBatchKey(),
      ids: [...selected],
      status: batch.status,
      note: batch.note.trim(),
      annotator
    })
  }

  // 批量结果对账: 勾选数必须与实际处理数严格一致; 刷新列表与统计
  const handleSettled = useCallback(
    (result, settledIntent) => {
      const failed = result.failed || []
      const updatedIds = new Set(result.updated_ids || [])
      const accounted = updatedIds.size + failed.length
      if (result.requested !== settledIntent.ids.length || accounted !== result.requested) {
        toast.warning(
          `勾选 ${settledIntent.ids.length} 条, 实际处理 ${accounted} 条, 数量不一致, 请刷新后核对`
        )
      }
      if (result.idempotent_replay) {
        toast.info('该批次已提交过, 返回首次处理结果, 未重复处理')
      } else if (failed.length === 0) {
        toast.success(`已标注 ${result.updated} 条记录`)
      } else {
        toast.warning(`成功 ${result.updated} 条, 失败 ${failed.length} 条, 明细见结果面板`)
      }
      // 已处理的移出勾选, 失败的保留勾选便于核对后重试
      setSelected((prev) => prev.filter((id) => !updatedIds.has(id)))
      setBatch((prev) => ({ ...prev, note: '' }))
      reload()
      reloadBatches()
    },
    [reload, reloadBatches, toast]
  )

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
        hint="点击行可打开单条标注; 勾选支持跨页累积, 翻页不会清空已选记录"
        actions={
          <>
            <Tag tone="primary">已选 {selected.length} 条</Tag>
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
                  onChange={(event) => {
                    setBatch({ ...batch, status: event.target.value })
                    setBatchErrors({})
                  }}
                >
                  <option value="confirmed">已确认</option>
                  <option value="ignored">已忽略</option>
                  <option value="pending">重置为待标注</option>
                </select>
                <input
                  className={`input ${batchErrors.note ? 'invalid' : ''}`}
                  style={{ flex: 1, minWidth: 220 }}
                  placeholder="标注说明 (确认或忽略时必填)"
                  value={batch.note}
                  onChange={(event) => setBatch({ ...batch, note: event.target.value })}
                />
                <input
                  className={`input ${batchErrors.annotator ? 'invalid' : ''}`}
                  style={{ width: 140 }}
                  placeholder="操作人 (必填)"
                  value={batch.annotator}
                  onChange={(event) => setBatch({ ...batch, annotator: event.target.value })}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={openBatchConfirm}
                  disabled={selected.length === 0}
                >
                  提交批量标注
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
              {batchErrors.note || batchErrors.annotator ? (
                <div className="small danger-text" style={{ marginTop: 6 }}>
                  {[batchErrors.annotator, batchErrors.note].filter(Boolean).join('; ')}
                </div>
              ) : null}
            </div>
          </div>

          <BatchOperationLog data={batchesQuery.data} />

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

      <BatchAnnotateDialog
        intent={intent}
        onClose={() => setIntent(null)}
        onSettled={handleSettled}
      />

      <AnnotationModal
        exceedanceId={activeId}
        onClose={() => setActiveId(null)}
        onSaved={() => {
          setActiveId(null)
          reload()
          reloadBatches()
        }}
      />
    </>
  )
}
