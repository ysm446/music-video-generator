import type { Scene } from '../../types/scene'
import { STATUS_ICONS } from '../../types/scene'

interface Props {
  scenes: Scene[]
  selectedId: number | null
  onSelect: (scene: Scene) => void
}

export default function SceneSidebar({ scenes, selectedId, onSelect }: Props) {
  return (
    <div style={{ overflowY: 'auto', flex: 1 }}>
      {scenes.map(s => (
        <button
          key={s.scene_id}
          onClick={() => onSelect(s)}
          style={{
            display: 'block',
            width: '100%',
            textAlign: 'left',
            padding: '6px 10px',
            background: s.scene_id === selectedId ? 'var(--color-surface2)' : 'transparent',
            border: 'none',
            borderBottom: '1px solid var(--color-border)',
            cursor: 'pointer',
            fontSize: 12,
            color: s.enabled ? 'inherit' : 'var(--color-muted)',
          }}
        >
          <span style={{ marginRight: 6 }}>{STATUS_ICONS[s.status]}</span>
          <span style={{ marginRight: 4, color: 'var(--color-muted)' }}>
            #{s.scene_id}
          </span>
          {s.section && <span>{s.section}</span>}
          {!s.enabled && <span style={{ marginLeft: 4, fontSize: 10 }}>[無効]</span>}
        </button>
      ))}
    </div>
  )
}
