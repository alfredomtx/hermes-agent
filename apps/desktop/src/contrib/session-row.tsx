import type { ReactNode } from 'react'

import { sessionRoute } from '@/app/routes'
import { setSelectedStoredSessionId } from '@/store/session'

import { ContribBoundary } from './react/boundary'
import { useContributions } from './react/use-contributions'
import type { Contribution, SessionRowContext, SessionRowContribution } from './types'
import { SESSION_ROW_AREAS } from './types'

export { SESSION_ROW_AREAS }
export type { SessionRowContext, SessionRowContribution }

export type SessionRowHostAction = (context: SessionRowContext) => Promise<void> | void

let sessionRowHostAction: SessionRowHostAction | null = null

/** Install the renderer-owned implementation of the SDK's session-row action. */
export function setSessionRowHostAction(action: SessionRowHostAction | null): void {
  sessionRowHostAction = action
}

/** Select a durable session and open the native Agents overlay through the host. */
export async function selectSessionAndOpenAgents(context: SessionRowContext): Promise<void> {
  const action = sessionRowHostAction

  if (!action) {
    throw new Error('Hermes session-row host action unavailable')
  }

  await action(context)
}

export interface SelectSessionAndOpenAgentsDependencies {
  openAgents: (returnPath: string) => void
}

/** Select the durable session and open Agents with its exact return route. */
export function createSelectSessionAndOpenAgentsAction({
  openAgents
}: SelectSessionAndOpenAgentsDependencies): SessionRowHostAction {
  return context => {
    setSelectedStoredSessionId(context.storedSessionId)
    openAgents(sessionRoute(context.storedSessionId))
  }
}

interface SessionRowTrailingProps {
  context: SessionRowContext
}

function SessionRowContributionRenderer({
  contribution,
  context
}: {
  contribution: SessionRowContribution
  context: SessionRowContext
}) {
  return <>{contribution.render(context)}</>
}

function asSessionRowContribution(contribution: Contribution): SessionRowContribution | null {
  if (contribution.area !== SESSION_ROW_AREAS.trailing || typeof contribution.render !== 'function') {
    return null
  }

  return contribution as SessionRowContribution
}

/** Render plugin-provided controls in the native row action cluster. */
export function SessionRowTrailing({ context }: SessionRowTrailingProps): ReactNode {
  const contributions = useContributions(SESSION_ROW_AREAS.trailing)

  const trailing = contributions.flatMap(contribution => {
    const typed = asSessionRowContribution(contribution)

    return typed ? [typed] : []
  })

  if (trailing.length === 0) {
    return null
  }

  return (
    <div
      className="flex shrink-0 items-center gap-0.5"
      data-row-actions
      data-session-row-trailing
      onClick={event => event.stopPropagation()}
      onPointerDown={event => event.stopPropagation()}
    >
      {trailing.map(contribution => (
        <ContribBoundary id={contribution.id} key={contribution.id} variant="chip">
          <SessionRowContributionRenderer context={context} contribution={contribution} />
        </ContribBoundary>
      ))}
    </div>
  )
}
