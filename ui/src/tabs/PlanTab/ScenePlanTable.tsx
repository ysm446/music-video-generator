import { useEffect, useRef, useState } from 'react'
import type { Scene } from '../../types/scene'
import { STATUS_ICONS } from '../../types/scene'

export interface SceneRow {
  scene_id: number
  time: string
  section: string
  plot: string
  status: Scene['status']
}

interface Props {
  projectName: string
  scenes: Scene[]
  /** 行の section/plot が変更されたとき */
  onChange: (rows: SceneRow[]) => void
  /** ドラッグ&ドロップで並び替えたとき */
  onReorder?: (sceneId: number, targetIndex: number) => void
}

function toRows(scenes: Scene[]): SceneRow[] {
  return scenes.map(s => ({
    scene_id: s.scene_id,
    time: `${s.start_time.toFixed(1)}s-${s.end_time.toFixed(1)}s`,
    section: s.section,
    plot: s.plot,
    status: s.status,
  }))
}

export default function ScenePlanTable({ projectName, scenes, onChange, onReorder }: Props) {
  const [rows, setRows] = useState<SceneRow[]>(toRows(scenes))
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [overIndex, setOverIndex] = useState<number | null>(null)
  const dragSceneId = useRef<number | null>(null)

  // 外からシーンが更新されたら行を再構築
  useEffect(() => {
    setRows(toRows(scenes))
  }, [scenes])

  function update(idx: number, field: 'section' | 'plot', value: string) {
    const updated = rows.map((r, i) => (i === idx ? { ...r, [field]: value } : r))
    setRows(updated)
    onChange(updated)
  }

  function handleDragStart(e: React.DragEvent, idx: number) {
    setDragIndex(idx)
    dragSceneId.current = rows[idx].scene_id
    e.dataTransfer.effectAllowed = 'move'
  }

  function handleDragOver(e: React.DragEvent, idx: number) {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setOverIndex(idx)
  }

  function handleDrop(e: React.DragEvent, idx: number) {
    e.preventDefault()
    if (dragIndex === null || dragIndex === idx) {
      setDragIndex(null)
      setOverIndex(null)
      return
    }
    // ローカルで並び替えプレビュー更新
    const reordered = [...rows]
    const [moved] = reordered.splice(dragIndex, 1)
    reordered.splice(idx, 0, moved)
    setRows(reordered)
    onChange(reordered)

    // バックエンドに保存
    if (dragSceneId.current !== null) {
      onReorder?.(dragSceneId.current, idx)
    }

    setDragIndex(null)
    setOverIndex(null)
    dragSceneId.current = null
  }

  function handleDragEnd() {
    setDragIndex(null)
    setOverIndex(null)
    dragSceneId.current = null
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--color-surface2)' }}>
            <th style={{ ...th, width: 24 }}></th>
            <th style={th}>ID</th>
            <th style={th}>時間</th>
            <th style={th}>状態</th>
            <th style={{ ...th, width: 72 }}>画像</th>
            <th style={{ ...th, width: '15%' }}>セクション</th>
            <th style={{ ...th, width: '55%' }}>プロット</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const isDragging = dragIndex === idx
            const isOver = overIndex === idx && dragIndex !== idx
            const hasImage = row.status === 'image_done' || row.status === 'video_done'
            const sceneDir = `scene_${String(row.scene_id).padStart(3, '0')}`
            const thumbUrl = `/api/files/${projectName}/scenes/${sceneDir}/image.png`
            return (
              <tr
                key={row.scene_id}
                draggable
                onDragStart={e => handleDragStart(e, idx)}
                onDragOver={e => handleDragOver(e, idx)}
                onDrop={e => handleDrop(e, idx)}
                onDragEnd={handleDragEnd}
                style={{
                  borderBottom: isOver
                    ? '2px solid var(--color-primary)'
                    : '1px solid var(--color-border)',
                  opacity: isDragging ? 0.4 : 1,
                  background: isOver ? 'rgba(233,69,96,0.06)' : undefined,
                  transition: 'opacity 0.1s',
                }}
              >
                <td style={{ ...td, cursor: 'grab', textAlign: 'center', color: 'var(--color-muted)', userSelect: 'none' }}>
                  ⠿
                </td>
                <td style={td}>{row.scene_id}</td>
                <td style={{ ...td, fontSize: 11, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                  {row.time}
                </td>
                <td style={{ ...td, textAlign: 'center' }}>
                  {STATUS_ICONS[row.status]}
                </td>
                <td style={{ ...td, padding: '3px 6px' }}>
                  {hasImage && (
                    <img
                      src={thumbUrl}
                      alt=""
                      style={{ width: 64, height: 36, objectFit: 'cover', borderRadius: 3, display: 'block', background: 'var(--color-surface2)' }}
                    />
                  )}
                </td>
                <td style={td}>
                  <input
                    type="text"
                    value={row.section}
                    onChange={e => update(idx, 'section', e.target.value)}
                    placeholder="intro/verse..."
                    style={{ width: '100%', fontSize: 12 }}
                  />
                </td>
                <td style={td}>
                  <textarea
                    value={row.plot}
                    onChange={e => update(idx, 'plot', e.target.value)}
                    rows={2}
                    placeholder="このシーンの内容..."
                    style={{ width: '100%', fontSize: 12, minHeight: 'unset', resize: 'vertical' }}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const th: React.CSSProperties = {
  padding: '6px 8px',
  textAlign: 'left',
  fontWeight: 600,
  fontSize: 12,
  color: 'var(--color-text-muted)',
  borderBottom: '1px solid var(--color-border)',
}

const td: React.CSSProperties = {
  padding: '4px 8px',
  verticalAlign: 'top',
}
