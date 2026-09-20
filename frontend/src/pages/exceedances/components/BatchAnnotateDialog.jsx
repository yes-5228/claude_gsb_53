import { useEffect, useState } from 'react'
import { batchAnnotate } from '../../../api/exceedances.js'
import Modal from '../../../components/common/Modal.jsx'
import Tag from '../../../components/common/Tag.jsx'
import { Alert } from '../../../components/common/Feedback.jsx'
import { EXCEEDANCE_STATUS_LABELS, EXCEEDANCE_STATUS_TONE } from '../../../constants/index.js'
import { formatDateTime } from '../../../utils/format.js'

/**
 * 批量标注确认弹窗: 确认 -> 提交 -> 结果对账.
 * intent = { key, ids, status, note, annotator }; key 为幂等批次号,
 * 弹窗生命周期内重试都使用同一批次号, 重复提交不会产生重复处理结果.
 */
export default function BatchAnnotateDialog({ intent, onClose, onSettled }) {
  const [phase, setPhase] = useState('confirm')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => {
    setPhase('confirm')
    setError(null)
    setResult(null)
  }, [intent?.key])

  if (!intent) return null

  const statusLabel = EXCEEDANCE_STATUS_LABELS[intent.status] || intent.status

  const submit = async () => {
    setPhase('busy')
    setError(null)
    try {
      const payload = await batchAnnotate({
        ids: intent.ids,
        status: intent.status,
        note: intent.note || null,
        annotator: intent.annotator || null,
        batch_id: intent.key
      })
      setResult(payload)
      setPhase('done')
      onSettled?.(payload, intent)
    } catch (err) {
      setError(err)
      setPhase('confirm')
    }
  }

  const failed = result?.failed || []
  const footer =
    phase === 'done' ? (
      <button type="button" className="btn btn-primary" onClick={onClose}>
        完成
      </button>
    ) : (
      <>
        <button type="button" className="btn" onClick={onClose} disabled={phase === 'busy'}>
          取消
        </button>
        <button type="button" className="btn btn-primary" onClick={submit} disabled={phase === 'busy'}>
          {phase === 'busy' ? '提交中...' : error ? '重试提交' : '确认提交'}
        </button>
      </>
    )

  return (
    <Modal open title="批量标注确认" onClose={phase === 'busy' ? undefined : onClose} footer={footer}>
      <div className="stack">
        {phase !== 'done' ? (
          <>
            <div>
              将对跨页勾选的 <strong>{intent.ids.length}</strong> 条超标记录执行
              <Tag tone={EXCEEDANCE_STATUS_TONE[intent.status]}> {statusLabel} </Tag>
              操作, 提交后待办数量与统计分布会同步刷新。
            </div>
            <dl className="kv">
              <dt>操作人</dt>
              <dd>{intent.annotator}</dd>
              <dt>标注说明</dt>
              <dd>{intent.note || <span className="muted">未填写</span>}</dd>
              <dt>批次号</dt>
              <dd className="mono small">{intent.key}</dd>
            </dl>
            {error ? (
              <Alert tone="error">
                {error.status === 422
                  ? `整批未生效: ${error.message}`
                  : `提交失败: ${error.message}。可直接重试, 同一批次号不会重复处理。`}
              </Alert>
            ) : null}
          </>
        ) : (
          <>
            {result.idempotent_replay ? (
              <Alert tone="info">该批次此前已提交成功, 以下为首次处理结果, 未重复处理。</Alert>
            ) : null}
            <div className="stat-grid">
              <div className="stat-card">
                <div className="stat-label">勾选数量</div>
                <div className="stat-value">{result.requested}</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">处理成功</div>
                <div className="stat-value success-text">{result.updated}</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">处理失败</div>
                <div className={`stat-value ${failed.length ? 'danger-text' : ''}`}>{failed.length}</div>
              </div>
            </div>
            <dl className="kv">
              <dt>处理时间</dt>
              <dd>{formatDateTime(result.processed_at)}</dd>
              <dt>操作人</dt>
              <dd>{result.annotator || intent.annotator}</dd>
              {result.reannotated ? (
                <>
                  <dt>再次标注</dt>
                  <dd>{result.reannotated} 条此前已标注, 前后差别可在单条标注历史中查看</dd>
                </>
              ) : null}
            </dl>
            {failed.length ? (
              <Alert tone="warning">
                <div className="stack" style={{ gap: 4 }}>
                  <strong>失败明细 (已保留勾选, 可处理后重试):</strong>
                  {failed.map((item) => (
                    <span key={item.id} className="small">
                      #{item.id}: {item.reason}
                    </span>
                  ))}
                </div>
              </Alert>
            ) : (
              <Alert tone="success">勾选数量与实际处理数一致, 全部处理成功。</Alert>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
