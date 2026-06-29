import { describe, expect, it } from 'vitest'

import type { SkillInfo } from '@/types/hermes'

import { flattenSkillGroups, type SkillFlatRow } from './virtual-skills-list'

const skill = (name: string, over: Partial<SkillInfo> = {}): SkillInfo =>
  ({ category: 'general', description: '', enabled: true, name, ...over }) as SkillInfo

const groups: Array<[string, SkillInfo[]]> = [
  ['general', [skill('a'), skill('b')]],
  ['writing', [skill('c')]]
]

const headers = (rows: SkillFlatRow[]) =>
  rows.filter((r): r is Extract<SkillFlatRow, { kind: 'header' }> => r.kind === 'header')

describe('flattenSkillGroups', () => {
  it('emits a header per group followed by its skills when showHeaders is true', () => {
    const rows = flattenSkillGroups(groups, true)

    expect(rows.map(r => r.kind)).toEqual(['header', 'skill', 'skill', 'header', 'skill'])
    expect(rows.map(r => r.key)).toEqual(['header:general', 'skill:a', 'skill:b', 'header:writing', 'skill:c'])
  })

  it('marks only the first group header as first (matches space-y first-child no-margin)', () => {
    expect(headers(flattenSkillGroups(groups, true)).map(h => h.first)).toEqual([true, false])
  })

  it('emits no header rows when showHeaders is false', () => {
    const rows = flattenSkillGroups(groups, false)

    expect(rows.every(r => r.kind === 'skill')).toBe(true)
    expect(rows).toHaveLength(3)
  })

  it('preserves skill order within and across groups', () => {
    const rows = flattenSkillGroups(groups, false)

    expect(rows.map(r => (r.kind === 'skill' ? r.skill.name : null))).toEqual(['a', 'b', 'c'])
  })

  it('produces unique keys for every row', () => {
    const rows = flattenSkillGroups(groups, true)

    expect(new Set(rows.map(r => r.key)).size).toBe(rows.length)
  })

  it('returns an empty array for no groups', () => {
    expect(flattenSkillGroups([], true)).toEqual([])
  })
})
