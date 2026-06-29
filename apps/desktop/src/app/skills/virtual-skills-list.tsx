import { useVirtualizer } from '@tanstack/react-virtual'
import type { ReactNode } from 'react'
import { useMemo, useRef } from 'react'

import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { SkillInfo } from '@/types/hermes'

import { PAGE_INSET_X } from '../layout-constants'
import { asText, prettyName } from '../settings/helpers'

const HEADER_ESTIMATE_PX = 28
const SKILL_ESTIMATE_PX = 60
const OVERSCAN_ROWS = 12

export type SkillFlatRow =
  | { category: string; first: boolean; key: string; kind: 'header' }
  | { key: string; kind: 'skill'; skill: SkillInfo }

/**
 * Flatten grouped skills into one virtualizable row array (issue #19). When
 * showHeaders is true each group is preceded by a header row; otherwise only
 * skill rows are emitted (a specific category is active → a single group).
 * Pure + exported so the windowing-free logic is unit-testable (jsdom has no
 * layout, so the virtualizer geometry itself can't be asserted).
 */
export function flattenSkillGroups(groups: Array<[string, SkillInfo[]]>, showHeaders: boolean): SkillFlatRow[] {
  const rows: SkillFlatRow[] = []

  groups.forEach(([category, list], groupIndex) => {
    if (showHeaders) {
      rows.push({ category, first: groupIndex === 0, key: `header:${category}`, kind: 'header' })
    }

    for (const skill of list) {
      rows.push({ key: `skill:${skill.name}`, kind: 'skill', skill })
    }
  })

  return rows
}

interface VirtualSkillsListProps {
  empty: ReactNode
  groups: Array<[string, SkillInfo[]]>
  onToggle: (skill: SkillInfo, enabled: boolean) => void
  savingSkill: null | string
  showHeaders: boolean
}

/**
 * Virtualized skills list (issue #19). Mirrors the sidebar VirtualSessionList
 * pattern — padding-spacer layout + measureElement + data-index — over the
 * flattened header/skill rows, so only the visible window mounts instead of one
 * DOM node per skill. The original `space-y-*` margins are baked into the header
 * rows as PADDING (pt-4 = group gap, pb-1.5 = header→list gap) because the
 * virtualizer measures padding but not margin; skill rows keep their py-2.5.
 */
export function VirtualSkillsList({ empty, groups, onToggle, savingSkill, showHeaders }: VirtualSkillsListProps) {
  const { t } = useI18n()
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const rows = useMemo(() => flattenSkillGroups(groups, showHeaders), [groups, showHeaders])

  const virtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: index => (rows[index]?.kind === 'header' ? HEADER_ESTIMATE_PX : SKILL_ESTIMATE_PX),
    getItemKey: index => rows[index]?.key ?? index,
    getScrollElement: () => scrollerRef.current,
    initialRect: { height: 600, width: 480 },
    overscan: OVERSCAN_ROWS
  })

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()
  const paddingTop = virtualItems[0]?.start ?? 0
  const paddingBottom = Math.max(0, totalSize - (virtualItems[virtualItems.length - 1]?.end ?? 0))

  return (
    <div className={cn('h-full overflow-y-auto py-3', PAGE_INSET_X)} ref={scrollerRef}>
      {rows.length === 0 ? (
        empty
      ) : (
        <div style={{ paddingBottom: `${paddingBottom}px`, paddingTop: `${paddingTop}px` }}>
          {virtualItems.map(virtualItem => {
            const row = rows[virtualItem.index]

            if (!row) {
              return null
            }

            if (row.kind === 'header') {
              return (
                <div
                  className={cn(
                    'pb-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground',
                    !row.first && 'pt-4'
                  )}
                  data-index={virtualItem.index}
                  key={row.key}
                  ref={virtualizer.measureElement}
                >
                  {prettyName(row.category)}
                </div>
              )
            }

            const skill = row.skill

            return (
              <div
                className="grid gap-3 px-0 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                data-index={virtualItem.index}
                key={row.key}
                ref={virtualizer.measureElement}
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{skill.name}</div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {asText(skill.description) || t.skills.noDescription}
                  </p>
                </div>
                <Switch
                  checked={skill.enabled}
                  disabled={savingSkill === skill.name}
                  onCheckedChange={checked => void onToggle(skill, checked)}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
