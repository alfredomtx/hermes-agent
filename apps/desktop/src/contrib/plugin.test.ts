import { describe, expect, it } from 'vitest'

import type { HermesPlugin } from './plugin'
import { SESSION_ROW_AREAS, type SessionRowContext } from './types'

const plugin: HermesPlugin = {
  id: 'session-row-actions',
  register: context => {
    context.register({
      id: 'agents',
      area: SESSION_ROW_AREAS.trailing,
      render: (row: SessionRowContext) => row.storedSessionId
    })

    context.registerMany([
      {
        id: 'agents-batch',
        area: SESSION_ROW_AREAS.trailing,
        render: (row: SessionRowContext) => row.profile
      }
    ])
  }
}

describe('plugin contribution types', () => {
  it('accepts contextual session-row renders through both registration APIs', () => {
    expect(plugin.id).toBe('session-row-actions')
  })
})
