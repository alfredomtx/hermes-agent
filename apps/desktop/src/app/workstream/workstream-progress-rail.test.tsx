import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $clarifyRequests } from '@/store/clarify'
import { $backgroundStatusBySession } from '@/store/composer-status'
import { $activeSessionId, $attentionSessionIds, $selectedStoredSessionId, $workingSessionIds } from '@/store/session'
import { $subagentsBySession, type SubagentProgress } from '@/store/subagents'
import { $todosBySession } from '@/store/todos'
import { $workflowProgressBySession } from '@/store/workflows'

import { WorkstreamProgressRail } from './workstream-progress-rail'

const subagent = (over: Partial<SubagentProgress> = {}): SubagentProgress => ({
  filesRead: [],
  filesWritten: [],
  goal: 'Audit implementation',
  id: 'agent-1',
  parentId: null,
  startedAt: 1,
  status: 'running',
  stream: [{ at: 2, kind: 'tool', text: 'Search Files("workstream")' }],
  taskCount: 1,
  taskIndex: 0,
  updatedAt: 2,
  currentTool: 'search_files',
  ...over
})

describe('WorkstreamProgressRail', () => {
  beforeEach(() => {
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    $attentionSessionIds.set([])
    $workingSessionIds.set([])
    $todosBySession.set({})
    $subagentsBySession.set({})
    $backgroundStatusBySession.set({})
    $clarifyRequests.set({})
    $workflowProgressBySession.set({})
  })

  afterEach(() => cleanup())

  it('renders the selected stored session using runtime-keyed live work', () => {
    $selectedStoredSessionId.set('stored-1')
    $activeSessionId.set('runtime-1')
    $attentionSessionIds.set(['runtime-1'])
    $workingSessionIds.set(['runtime-1'])
    $todosBySession.set({
      'runtime-1': [
        { id: 'done', content: 'Scope default decisions', status: 'completed' },
        { id: 'active', content: 'Build progress rail', status: 'in_progress' }
      ]
    })
    $subagentsBySession.set({ 'runtime-1': [subagent()] })
    $backgroundStatusBySession.set({
      'runtime-1': [{ id: 'proc-1', state: 'running', title: 'npm run test:ui', type: 'background' }]
    })
    $clarifyRequests.set({
      'runtime-1': {
        choices: ['Ship it', 'Hold it'],
        question: 'Ship Phase 2 now?',
        requestId: 'clarify-1',
        sessionId: 'runtime-1'
      }
    })
    $workflowProgressBySession.set({
      'runtime-1': {
        id: 'wf-1',
        phases: [
          { id: 'scan', title: 'Scan', status: 'completed' },
          { id: 'verify', title: 'Verify', status: 'running' }
        ],
        title: 'Phase 2 workflow'
      }
    })

    render(<WorkstreamProgressRail />)

    expect(screen.getAllByText('needs your input').length).toBeGreaterThan(0)
    expect(screen.getByText('Ship Phase 2 now?')).toBeTruthy()
    expect(screen.getByText('Ship it')).toBeTruthy()
    expect(screen.getByText('Hold it')).toBeTruthy()
    expect(screen.getByText(/2 todos/)).toBeTruthy()
    expect(screen.getByText('Build progress rail')).toBeTruthy()
    expect(screen.getByText('Audit implementation')).toBeTruthy()
    expect(screen.getByText('Search Files("workstream")')).toBeTruthy()
    expect(screen.getByText('npm run test:ui')).toBeTruthy()
    expect(screen.getByText('Phase 2 workflow')).toBeTruthy()
    expect(screen.getByText('Verify')).toBeTruthy()
  })

  it('renders an empty selected-session rail without stealing inactive sessions work', () => {
    $selectedStoredSessionId.set('stored-1')
    $todosBySession.set({ other: [{ id: 'other', content: 'Do not show', status: 'pending' }] })

    render(<WorkstreamProgressRail />)

    expect(screen.getByText('No live work for this session')).toBeTruthy()
    expect(screen.queryByText('Do not show')).toBeNull()
  })
})
