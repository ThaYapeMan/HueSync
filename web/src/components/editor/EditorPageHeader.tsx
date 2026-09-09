import { Button } from '@/components/ui/button'

interface Props {
  name: string
  placeholder?: string
  onNameChange: (v: string) => void
  isLive?: boolean
  error?: string | null
  saving?: boolean
  saveDisabled?: boolean
  onCancel: () => void
  onSave: () => void
  'aria-label'?: string
}

export function EditorPageHeader({
  name,
  placeholder = 'Name…',
  onNameChange,
  isLive,
  error,
  saving,
  saveDisabled,
  onCancel,
  onSave,
  'aria-label': ariaLabel,
}: Props) {
  return (
    <div className="flex items-center gap-4 px-6 py-3 border-b border-border shrink-0">
      <input
        aria-label={ariaLabel ?? 'Name'}
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        placeholder={placeholder}
        className="flex-1 bg-transparent text-base font-semibold outline-none placeholder:text-muted-foreground/40 min-w-0"
        data-testid="editor-name-input"
      />
      {isLive && (
        <span className="flex items-center gap-1.5 text-xs font-medium text-green-400 shrink-0">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
          Live
        </span>
      )}
      {error && (
        <span className="text-xs text-destructive shrink-0">{error}</span>
      )}
      <div className="flex items-center gap-2 shrink-0">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={onSave}
          disabled={saving || saveDisabled}
          data-testid="editor-save"
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
