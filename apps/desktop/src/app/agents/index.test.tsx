import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { reasoningEffortLabel } from '@/lib/reasoning-effort'
import { $subagentsBySession } from '@/store/subagents'
import type * as WindowsStore from '@/store/windows'

import { AgentsView } from './index'

const mocks = vi.hoisted(() => ({
  openSessionInNewWindow: vi.fn()
}))

vi.mock('@/i18n', () => ({
  translateNow: (key: string) => key,
  useI18n: () => ({
    t: {
      agents: {
        activeCount: (count: number) => `${count} active`,
        agentsCount: (count: number) => `${count} agents`,
        ageHours: (count: number) => `${count}h`,
        ageMinutes: (count: number) => `${count}m`,
        ageNow: 'now',
        ageSeconds: (count: number) => `${count}s`,
        close: 'Close',
        delegation: (count: number) => `Delegation ${count}`,
        done: 'Done',
        durationMinutes: (minutes: number, seconds: number) => `${minutes}m ${seconds}s`,
        durationSeconds: (seconds: string) => `${seconds}s`,
        failed: 'Failed',
        failedCount: (count: number) => `${count} failed`,
        files: 'Files',
        filesCount: (count: number) => `${count} files`,
        moreFiles: (count: number) => `+${count} more`,
        running: 'Running',
        streaming: 'Streaming',
        subtitle: 'Delegated work',
        title: 'Agents',
        tokens: (count: string) => `${count} tokens`,
        toolsCount: (count: number) => `${count} tools`,
        updatedAgo: (value: string) => `updated ${value}`,
        workers: (count: number) => `${count} workers`,
        workersActive: (count: number) => `${count} active workers`
      }
    }
  })
}))

vi.mock('@/store/windows', async importOriginal => {
  const actual = await importOriginal<typeof WindowsStore>()

  return {
    ...actual,
    openSessionInNewWindow: mocks.openSessionInNewWindow
  }
})

afterEach(() => {
  cleanup()
  $subagentsBySession.set({})
  mocks.openSessionInNewWindow.mockReset()
})

beforeEach(() => {
  Element.prototype.animate = function animate() {
    return {
      cancel: () => {},
      finished: Promise.resolve()
    } as unknown as Animation
  }

  mocks.openSessionInNewWindow.mockResolvedValue(undefined)
})

describe('AgentsView', () => {
  it('shows child model and reasoning label and opens a child session without collapsing its row', () => {
    // Arrange
    const now = Date.now()
    $subagentsBySession.set({
      parent: [
        {
          filesRead: [],
          filesWritten: [],
          goal: 'Research files',
          id: 'child-with-session',
          model: 'GPT Luna',
          parentId: null,
          reasoningEffort: 'high',
          sessionId: 'child-1',
          startedAt: now,
          status: 'completed',
          stream: [{ at: now, kind: 'summary', text: 'Done' }],
          taskCount: 1,
          taskIndex: 0,
          updatedAt: now
        },
        {
          filesRead: [],
          filesWritten: [],
          goal: 'No session child',
          id: 'child-without-session',
          parentId: null,
          startedAt: now + 1,
          status: 'completed',
          stream: [],
          taskCount: 1,
          taskIndex: 1,
          updatedAt: now + 1
        }
      ]
    })

    // Act
    render(<AgentsView onClose={vi.fn()} />)

    // Assert
    expect(screen.getByText(new RegExp(`GPT Luna · ${reasoningEffortLabel('high')}`))).toBeTruthy()
    const rowToggle = screen.getByRole('button', { name: /Research files.*GPT Luna.*High/ })
    expect(rowToggle.getAttribute('aria-expanded')).toBe('true')

    const openChild = screen.getByRole('button', { name: 'Open child session: Research files' })
    fireEvent.click(openChild)

    expect(mocks.openSessionInNewWindow).toHaveBeenCalledWith('child-1', { watch: true })
    expect(rowToggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.queryByRole('button', { name: 'Open child session: No session child' })).toBeNull()
  })
})
