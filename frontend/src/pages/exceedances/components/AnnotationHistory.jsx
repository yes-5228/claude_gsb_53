import { useCallback } from 'react'
import { listExceedanceAnnotations } from '../../../api/exceedances.js'
import Tag from '../../../components/common/Tag.jsx'
import { EmptyState, Loading } from '../../../components/common/Feedback.jsx'
import { EXCEEDANCE_STATUS_TONE } from '../../../constants/index.js'
import { useAsyncData } from '../../../hooks/useAsyncData.js'
import { formatDateTime } from '../../../utils/format.js'

function DiffLine({ label, prev, next }) {
  if (prev === next) return null
  return (
    <div className="small">
      <span className="muted">{label}: </span>
      <span className="muted" style={{ textDecoration: 'line-through' }}>{prev || '未填写'}</span>
      <span className="muted"> → </span>
      <span className="strong">{next || '未填写'}</span>
    </div>
  )
}

/** 单条超标记录的标注历史: 每次标注的前后值对照, 能看出两次标注的差别. */
export default function AnnotationHistory({ exceedanceId }) {
  const loader = useCallback(() => listExceedanceAnnotations(exceedanceId), [exceedanceId])
  const { data, loading } = useAsyncData(loader, {
    immediate: Boolean(exceedanceId),
    initial: null
  })
  const items = data?.items || []

  if (!exceedanceId) return null
  if (loading && !data) return <Loading text="加载标注历史..." />

  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="field-label">标注历史 (前后对照)</span>
      {items.length === 0 ? (
        <EmptyState text="暂无标注历史, 保存后将在此留痕" icon="🕘" />
      ) : (
        items.map((entry) => (
          <div key={entry.id} className="history-item">
            <div className="inline" style={{ gap: 8 }}>
              <Tag tone={EXCEEDANCE_STATUS_TONE[entry.new_status]}>{entry.new_status_label}</Tag>
              <span className="small muted">
                {formatDateTime(entry.created_at)} · {entry.annotator || '未署名'} · {entry.source_label}
              </span>
            </div>
            <DiffLine label="状态" prev={entry.prev_status_label} next={entry.new_status_label} />
            <DiffLine label="等级" prev={entry.prev_level_label} next={entry.new_level_label} />
            <DiffLine label="说明" prev={entry.prev_note} next={entry.new_note} />
            <DiffLine label="操作人" prev={entry.prev_annotator} next={entry.annotator} />
          </div>
        ))
      )}
    </div>
  )
}
