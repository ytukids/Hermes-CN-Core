import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/hermes'

import { filterCommandCenterSessions } from './sessions'

const session = (over: Partial<SessionInfo> & { id: string }): SessionInfo =>
  ({ title: over.id, ...over }) as SessionInfo

describe('filterCommandCenterSessions', () => {
  it('sorts newest-first by last_active || started_at || 0', () => {
    const out = filterCommandCenterSessions(
      [
        session({ id: 'b', last_active: 200 }),
        session({ id: 'a', last_active: 300 }),
        session({ id: 'c', last_active: 100 })
      ],
      ''
    )

    expect(out.map(s => s.id)).toEqual(['a', 'b', 'c'])
  })

  it('falls back to started_at, then 0, when last_active is missing', () => {
    const out = filterCommandCenterSessions(
      [
        session({ id: 'no-times' }),
        session({ id: 'started-only', started_at: 50 }),
        session({ id: 'active', last_active: 500 })
      ],
      ''
    )

    expect(out.map(s => s.id)).toEqual(['active', 'started-only', 'no-times'])
  })

  it('returns the full sorted list for an empty query without mutating the input', () => {
    const input = [session({ id: 'x', last_active: 1 }), session({ id: 'y', last_active: 2 })]
    const out = filterCommandCenterSessions(input, '')

    expect(out.map(s => s.id)).toEqual(['y', 'x'])
    expect(input.map(s => s.id)).toEqual(['x', 'y']) // original order untouched
  })

  it('matches case-insensitively across title and id', () => {
    const byTitle = filterCommandCenterSessions([session({ id: '1', title: 'Deploy Pipeline' })], 'DEPLOY')
    const byId = filterCommandCenterSessions([session({ id: 'abc-123', title: 'untitled' })], 'abc-1')

    expect(byTitle.map(s => s.id)).toEqual(['1'])
    expect(byId.map(s => s.id)).toEqual(['abc-123'])
  })

  it('returns an empty array when nothing matches', () => {
    expect(filterCommandCenterSessions([session({ id: '1', title: 'hello' })], 'zzz')).toEqual([])
  })
})
