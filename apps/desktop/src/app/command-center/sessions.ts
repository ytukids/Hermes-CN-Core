import type { SessionInfo } from '@/hermes'
import { sessionTitle } from '@/lib/chat-runtime'

/**
 * Sort sessions newest-first and (when a query is present) substring-filter on
 * title + id. Extracted from CommandCenterView's useMemo so it has a pure,
 * jsdom-free unit-test target — the virtualized list itself can't be tested
 * under jsdom (no layout). `query` is expected pre-trimmed by the caller.
 */
export function filterCommandCenterSessions(sessions: SessionInfo[], query: string): SessionInfo[] {
  const sorted = [...sessions].sort((a, b) => {
    const left = a.last_active || a.started_at || 0
    const right = b.last_active || b.started_at || 0

    return right - left
  })

  const needle = query.toLowerCase()

  if (!needle) {
    return sorted
  }

  return sorted.filter(session => {
    const haystack = `${sessionTitle(session)} ${session.id}`.toLowerCase()

    return haystack.includes(needle)
  })
}
