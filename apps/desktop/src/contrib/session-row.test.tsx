import { afterEach, describe, expect, it, vi } from 'vitest'

import { host } from '@/sdk'
import { $selectedStoredSessionId, setSelectedStoredSessionId } from '@/store/session'

import { createSelectSessionAndOpenAgentsAction, type SessionRowContext, setSessionRowHostAction } from './session-row'

const context: SessionRowContext = {
  profile: 'default',
  storedSessionId: 'stored-1'
}

afterEach(() => {
  setSelectedStoredSessionId(null)
  setSessionRowHostAction(null)
})

describe('session row host action', () => {
  it('selects the stored session before opening the Agents overlay', async () => {
    const events: string[] = []

    const openAgents = vi.fn((returnPath: string) => {
      expect($selectedStoredSessionId.get()).toBe('stored-1')
      events.push(`opened:${returnPath}`)
    })

    setSessionRowHostAction(createSelectSessionAndOpenAgentsAction({ openAgents }))

    await host.selectSessionAndOpenAgents(context)

    expect(events).toEqual(['opened:/stored-1'])
    expect(openAgents).toHaveBeenCalledTimes(1)
    expect(openAgents).toHaveBeenCalledWith('/stored-1')
  })

  it('exposes the registered action through the SDK host', async () => {
    const implementation = vi.fn(async (_context: SessionRowContext) => undefined)

    setSessionRowHostAction(implementation)

    await host.selectSessionAndOpenAgents(context)

    expect(implementation).toHaveBeenCalledTimes(1)
    expect(implementation).toHaveBeenCalledWith(context)
  })
})
