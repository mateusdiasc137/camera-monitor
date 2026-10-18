import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { AuthGate } from './features/auth/AuthGate'
import { CamerasPage } from './features/cameras/CamerasPage'
import { LivePage } from './features/live/LivePage'
import { RecordingsPage } from './features/recordings/RecordingsPage'
import { StoragePage } from './features/storage/StoragePage'
import { SystemStatus } from './features/system/SystemStatus'

const TABS = [
  { id: 'live', label: 'Ao vivo' },
  { id: 'recordings', label: 'Gravações' },
  { id: 'cameras', label: 'Câmeras' },
  { id: 'storage', label: 'Armazenamento' },
  { id: 'system', label: 'Sistema' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function App() {
  const [queryClient] = useState(() => new QueryClient())
  const [tab, setTab] = useState<TabId>('live')

  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        {(user) => (
          <>
            <nav className="tabs" aria-label="Seções">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={tab === t.id ? 'tab tab--active' : 'tab'}
                  aria-current={tab === t.id ? 'page' : undefined}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </nav>
            {tab === 'live' && <LivePage />}
            {tab === 'recordings' && <RecordingsPage />}
            {tab === 'cameras' && <CamerasPage isAdmin={user.role === 'admin'} />}
            {tab === 'storage' && <StoragePage isAdmin={user.role === 'admin'} />}
            {tab === 'system' && <SystemStatus />}
          </>
        )}
      </AuthGate>
    </QueryClientProvider>
  )
}
