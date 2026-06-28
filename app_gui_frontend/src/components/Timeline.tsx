import type { ContextEvent } from '../api'

export function Timeline({ events }: { events: ContextEvent[] }) {
  return <footer className="timeline glass-panel">
    {events.slice(-40).map(event => <div key={event.event_id} className="tick" title={event.event_type}>{event.event_type.replace(/_/g, ' ')}</div>)}
  </footer>
}
