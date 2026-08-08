import { Alert, Badge, Button, Modal, Spinner } from 'react-bootstrap'
import type { ReactNode } from 'react'

/** Maps a sync status onto a Bootstrap variant. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge bg="secondary">Never run</Badge>
  const variant =
    status === 'SUCCESS'
      ? 'success'
      : status === 'NO_RECORDS'
        ? 'secondary'
        : status === 'PARTIAL'
          ? 'warning'
          : status === 'RUNNING'
            ? 'info'
            : 'danger'
  return <Badge bg={variant}>{status.replaceAll('_', ' ')}</Badge>
}

export function BoolBadge({ value, on, off }: { value: boolean; on: string; off: string }) {
  return <Badge bg={value ? 'success' : 'secondary'}>{value ? on : off}</Badge>
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="d-flex align-items-center gap-2 text-muted py-4">
      <Spinner animation="border" size="sm" /> {label}
    </div>
  )
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <Alert variant="danger" className="mb-3">
      {message}
    </Alert>
  )
}

export function PageHeader({ title, subtitle, actions }: {
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <div className="d-flex justify-content-between align-items-start mb-4 flex-wrap gap-2">
      <div>
        <h1 className="h3 mb-1">{title}</h1>
        {subtitle && <p className="text-muted mb-0">{subtitle}</p>}
      </div>
      {actions && <div className="d-flex gap-2">{actions}</div>}
    </div>
  )
}

/** Confirmation for anything destructive or that talks to a real device. */
export function ConfirmDialog({
  show, title, body, confirmLabel = 'Confirm', variant = 'danger', busy, onConfirm, onCancel,
}: {
  show: boolean
  title: string
  body: ReactNode
  confirmLabel?: string
  variant?: string
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Modal show={show} onHide={onCancel} centered>
      <Modal.Header closeButton>
        <Modal.Title className="h5">{title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>{body}</Modal.Body>
      <Modal.Footer>
        <Button variant="outline-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button variant={variant} onClick={onConfirm} disabled={busy}>
          {busy && <Spinner animation="border" size="sm" className="me-2" />}
          {confirmLabel}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}
