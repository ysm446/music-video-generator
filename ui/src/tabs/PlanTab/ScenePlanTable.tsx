import { useEffect, useState } from 'react'
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
  scenes: Scene[]
  /** 行の section/plot が変更されたとき */
  onChange: (rows: SceneRow[]) => void
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

export default function ScenePlanTable({ scenes, onChange }: Props) {
  const [rows, setRows] = useState<SceneRow[]>(toRows(scenes))

  // 外からシーンが更新されたら行を再構築
  useEffect(() => {
    setRows(toRows(scenes))
  }, [scenes])

  function update(idx: number, field: 'section' | 'plot', value: string) {
    const updated = rows.map((r, i) => (i === idx ? { ...r, [field]: value } : r))
    setRows(updated)
    onChange(updated)
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--color-surface2)' }}>
            <th style={th}>ID</th>
            <th style={th}>時間</th>
            <th style={th}>状態</th>
            <th style={{ ...th, width: '15%' }}>セクション</th>
            <th style={{ ...th, width: '55%' }}>プロット</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr
              key={row.scene_id}
              style={{ borderBottom: '1px solid var(--color-border)' }}
            >
              <td style={td}>{row.scene_id}</td>
              <td style={{ ...td, fontSize: 11, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                {row.time}
              </td>
              <td style={{ ...td, textAlign: 'center' }}>
                {STATUS_ICONS[row.status]}
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
          ))}
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
