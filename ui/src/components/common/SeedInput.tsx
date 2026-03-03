interface Props {
  value: number
  onChange: (v: number) => void
  /** 「♻️ PNGから読み取り」ボタンを表示するか */
  showReadFromPng?: boolean
  onReadFromPng?: () => void
  disabled?: boolean
}

/** シード入力 + 🎲ランダム + ♻️PNG読み取りボタン */
export default function SeedInput({ value, onChange, showReadFromPng, onReadFromPng, disabled }: Props) {
  return (
    <div className="flex gap-1 items-center">
      <input
        type="number"
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        disabled={disabled}
        style={{ width: 120, flex: 'none' }}
        min={-1}
        title="シード値（-1でランダム）"
      />
      <button
        type="button"
        className="btn-secondary"
        onClick={() => onChange(-1)}
        disabled={disabled}
        title="シードをランダム（-1）にする"
        style={{ fontSize: 16, padding: '2px 6px' }}
      >
        🎲
      </button>
      {showReadFromPng && (
        <button
          type="button"
          className="btn-secondary"
          onClick={onReadFromPng}
          disabled={disabled}
          title="生成済みPNGからシードを読み取る"
          style={{ fontSize: 16, padding: '2px 6px' }}
        >
          ♻️
        </button>
      )}
    </div>
  )
}
