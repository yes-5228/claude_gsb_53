import Tag from '../../../components/common/Tag.jsx'
import { EXCEEDANCE_STATUS_TONE } from '../../../constants/index.js'
import { formatDateTime } from '../../../utils/format.js'

/** 最近批量操作记录: 每次批量标注的操作人、说明与处理结果留痕. */
export default function BatchOperationLog({ data }) {
  const items = data?.items || []
  if (items.length === 0) return null

  return (
    <div className="card" style={{ boxShadow: 'none' }}>
      <div className="card-body tight">
        <div className="stack" style={{ gap: 8 }}>
          <span className="field-label">最近批量操作 (操作人 / 说明 / 结果留痕)</span>
          {items.map((item) => (
            <div key={item.id} className="batch-log-item">
              <div className="inline" style={{ gap: 8 }}>
                <Tag tone={EXCEEDANCE_STATUS_TONE[item.status]}>{item.status_label}</Tag>
                <span className="strong">{item.annotator || '未署名'}</span>
                <span className="small muted">{formatDateTime(item.created_at)}</span>
                <span className="small">
                  勾选 {item.requested} · 成功 {item.updated}
                  {item.failed ? <span className="danger-text"> · 失败 {item.failed}</span> : null}
                  {item.reannotated ? <span className="muted"> · 再次标注 {item.reannotated}</span> : null}
                </span>
              </div>
              <div className="small muted">
                {item.note || '未填写说明'}
                {item.failed_items?.length
                  ? ` · 失败: ${item.failed_items.map((f) => `#${f.id} ${f.reason}`).join('; ')}`
                  : ''}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
