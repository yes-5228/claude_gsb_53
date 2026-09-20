import Modal from '../../../components/common/Modal.jsx'
import Tag from '../../../components/common/Tag.jsx'
import { Alert } from '../../../components/common/Feedback.jsx'
import {
  EXCEEDANCE_LEVEL_LABELS,
  EXCEEDANCE_LEVEL_TONE,
  EXCEEDANCE_STATUS_LABELS,
  EXCEEDANCE_STATUS_TONE
} from '../../../constants/index.js'
import { formatDateTime } from '../../../utils/format.js'

const DIFF_FIELDS = [
  { key: 'status', label: '状态', format: (value) => EXCEEDANCE_STATUS_LABELS[value] || value || '—' },
  { key: 'level', label: '等级', format: (value) => EXCEEDANCE_LEVEL_LABELS[value] || value || '—' },
  { key: 'note', label: '说明', format: (value) => value || '—' },
  { key: 'annotator', label: '标注人', format: (value) => value || '—' },
  { key: 'annotated_at', label: '标注时间', format: (value) => (value ? formatDateTime(value) : '—') }
]

function DiffLines({ item }) {
  const lines = DIFF_FIELDS.filter(
    (field) => (item.before?.[field.key] ?? null) !== (item.after?.[field.key] ?? null)
  )
  if (lines.length === 0) return <div className="small muted">内容无变化</div>
  return (
    <div className="stack" style={{ gap: 4 }}>
      {lines.map((field) => (
        <div key={field.key} className="small">
          <span className="muted">{field.label}: </span>
          <span>{field.format(item.before?.[field.key])}</span>
          <span className="muted"> → </span>
          <span className="strong">{field.format(item.after?.[field.key])}</span>
        </div>
      ))}
    </div>
  )
}

export default function BatchResultModal({ result, onClose }) {
  if (!result) return null

  const failed = result.failed || []
  const updatedItems = (result.items || []).filter((item) => item.result === 'updated')
  const reannotated = updatedItems.filter((item) => item.reannotated).length
  const balanced = result.requested === result.updated + failed.length

  return (
    <Modal
      open={Boolean(result)}
      wide
      title="批量标注处理结果"
      onClose={onClose}
      footer={
        <button type="button" className="btn btn-primary" onClick={onClose}>
          知道了
        </button>
      }
    >
      <div className="stack">
        {result.deduplicated ? (
          <Alert tone="info">该批次为重复提交, 仅生效一次, 以下为首次处理的结果。</Alert>
        ) : null}

        <div className="stat-grid">
          <div className="stat-card">
            <div className="stat-label">勾选数量</div>
            <div className="stat-value">{result.requested}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">处理成功</div>
            <div className="stat-value">{result.updated}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">失败 / 跳过</div>
            <div className="stat-value">{failed.length}</div>
          </div>
        </div>

        {balanced ? (
          <Alert tone="success">
            数量对账一致: 勾选 {result.requested} 条 = 成功 {result.updated} 条 + 失败 {failed.length} 条
          </Alert>
        ) : (
          <Alert tone="error">
            数量对账不一致: 勾选 {result.requested} 条, 成功 {result.updated} 条, 失败 {failed.length} 条,
            请刷新后重试或联系管理员
          </Alert>
        )}

        <dl className="kv">
          <dt>处理方式</dt>
          <dd>{result.mode === 'atomic' ? '整批生效 (任一失败则全部不生效)' : '逐条处理 (跳过失败项)'}</dd>
          <dt>目标状态</dt>
          <dd>
            <Tag tone={EXCEEDANCE_STATUS_TONE[result.status]}>
              {EXCEEDANCE_STATUS_LABELS[result.status] || result.status}
            </Tag>
            {result.level ? (
              <Tag tone={EXCEEDANCE_LEVEL_TONE[result.level]}>
                {EXCEEDANCE_LEVEL_LABELS[result.level] || result.level}
              </Tag>
            ) : null}
          </dd>
          <dt>操作人</dt>
          <dd>{result.annotator || '—'}</dd>
          <dt>操作说明</dt>
          <dd>{result.note || '—'}</dd>
          <dt>处理时间</dt>
          <dd>{formatDateTime(result.processed_at)}</dd>
          <dt>批次号</dt>
          <dd className="mono small">{result.request_id}</dd>
        </dl>

        {failed.length > 0 ? (
          <div>
            <div className="field-label" style={{ marginBottom: 6 }}>
              未处理记录 (逐条原因)
            </div>
            <div className="stack" style={{ gap: 6 }}>
              {failed.map((item) => (
                <Alert key={item.id} tone="warning">
                  #{item.id} · {item.reason}
                </Alert>
              ))}
            </div>
          </div>
        ) : null}

        {updatedItems.length > 0 ? (
          <div>
            <div className="field-label" style={{ marginBottom: 6 }}>
              处理明细{reannotated > 0 ? ` (含 ${reannotated} 条重复标注, 可对比前后差异)` : ''}
            </div>
            <div className="stack" style={{ gap: 8 }}>
              {updatedItems.map((item) => (
                <div key={item.id} className="card" style={{ boxShadow: 'none' }}>
                  <div className="card-body tight">
                    <div className="inline" style={{ marginBottom: 6 }}>
                      <span className="strong mono">#{item.id}</span>
                      <span className="small">{item.station_name || '—'}</span>
                      <span className="small muted">{item.pollutant}</span>
                      {item.reannotated ? <Tag tone="warning">重复标注</Tag> : null}
                      {!item.changed ? <Tag tone="neutral">无变化</Tag> : null}
                    </div>
                    <DiffLines item={item} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
