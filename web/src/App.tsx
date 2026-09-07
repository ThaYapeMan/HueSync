import { useState } from 'react'
import { usePreviewSocket } from '@/hooks/usePreviewSocket'
import { NowPlaying } from '@/pages/NowPlaying'
import { Latency } from '@/pages/Latency'
import { Players } from '@/pages/Players'
import { AnalysisConfigs } from '@/pages/AnalysisConfigs'
import { Scenes } from '@/pages/Scenes'
import { Couplings } from '@/pages/Couplings'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

type Tab = 'now-playing' | 'latency' | 'players' | 'analysis-configs' | 'scenes' | 'couplings'

const NAV_ITEMS: { value: Tab; label: string }[] = [
  { value: 'now-playing',      label: 'Now Playing' },
  { value: 'latency',          label: 'Latency' },
  { value: 'players',          label: 'Virtual Players' },
  { value: 'analysis-configs', label: 'Analysis' },
  { value: 'scenes',           label: 'Scenes' },
  { value: 'couplings',        label: 'Couplings' },
]

function ConnectionBadge({ connected, attempt }: { connected: boolean; attempt: number }) {
  if (connected) {
    return <Badge className="text-xs">● Live</Badge>
  }
  return (
    <Badge variant="destructive" className="text-xs">
      {attempt === 0 ? '● Connecting…' : `● Reconnecting (${attempt})…`}
    </Badge>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('now-playing')
  const { colour, onset, onset_bass, onset_mid, onset_treble, mix, bars, status, connected, reconnectAttempt } = usePreviewSocket()

  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="border-b border-border shrink-0">
        <div className="px-4 py-4 flex items-center justify-between">
          <h1 className="text-lg font-semibold tracking-tight">HueSync</h1>
          <div className="flex items-center gap-3">
            {status?.version && (
              <span className="text-xs text-muted-foreground font-mono">{status.version}</span>
            )}
            <ConnectionBadge connected={connected} attempt={reconnectAttempt} />
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <nav className="w-44 border-r border-border shrink-0 pt-2">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.value}
              onClick={() => setActiveTab(item.value)}
              className={cn(
                'w-full text-left px-4 py-2.5 text-sm transition-colors',
                activeTab === item.value
                  ? 'bg-muted font-medium text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              )}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <main className="flex-1 overflow-y-auto">
          <div className={cn('mx-auto px-6 py-6', activeTab === 'now-playing' ? 'max-w-xl' : 'max-w-3xl')}>
            {activeTab === 'now-playing' && (
              <NowPlaying colour={colour} onset={onset} onset_bass={onset_bass} onset_mid={onset_mid} onset_treble={onset_treble} mix={mix} bars={bars} status={status} />
            )}
            {activeTab === 'latency' && (
              <Latency
                syncMaster={status?.sync_master ?? null}
                syncMasterName={status?.sync_master_name ?? null}
              />
            )}
            {activeTab === 'players' && <Players />}
            {activeTab === 'analysis-configs' && <AnalysisConfigs />}
            {activeTab === 'scenes' && <Scenes />}
            {activeTab === 'couplings' && (
              <Couplings
                activeCouplingId={status?.active_coupling_id ?? null}
                onActivationChange={() => {}}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
