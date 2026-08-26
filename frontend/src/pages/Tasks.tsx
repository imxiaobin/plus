import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, Square, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { TaskLogPanel } from '@/components/tasks/TaskLogPanel'
import { getTaskStatusText } from '@/lib/tasks'
import { apiFetch } from '@/lib/utils'

const TYPE_LABELS: Record<string, string> = {
  register: '协议注册',
  refresh_token_check: '401 验活',
}

function statusClass(status: string) {
  if (status === 'failed') return 'bg-red-500/10 text-red-300 ring-red-500/30'
  if (status === 'succeeded') return 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30'
  if (status === 'running' || status === 'claimed' || status === 'pending') {
    return 'bg-sky-500/10 text-sky-300 ring-sky-500/30'
  }
  return 'bg-amber-500/10 text-amber-300 ring-amber-500/30'
}

function sameTaskSnapshot(left: any[], right: any[]) {
  if (left.length !== right.length) return false
  return left.every((task, index) => {
    const other = right[index]
    return (
      task.task_id === other.task_id &&
      task.status === other.status &&
      task.progress === other.progress &&
      task.error === other.error &&
      Boolean(task.terminal) === Boolean(other.terminal) &&
      Boolean(task.cancellable) === Boolean(other.cancellable)
    )
  })
}

export default function Tasks() {
  const [tasks, setTasks] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [stoppingId, setStoppingId] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [expandedLogs, setExpandedLogs] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await apiFetch('/tasks?limit=50')
      const next = Array.isArray(data?.items) ? data.items : []
      setTasks((current) => (sameTaskSnapshot(current, next) ? current : next))
      setError('')
    } catch (err: any) {
      setError(err?.message || '读取任务失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const hasRunning = useMemo(() => tasks.some((task) => !task.terminal), [tasks])

  useEffect(() => {
    void load()
    const schedule = () => {
      const hidden = typeof document !== 'undefined' && document.hidden
      return hidden ? 15000 : hasRunning ? 2500 : 8000
    }
    let timer = window.setInterval(() => void load(), schedule())
    const reschedule = () => {
      window.clearInterval(timer)
      timer = window.setInterval(() => void load(), schedule())
    }
    document.addEventListener('visibilitychange', reschedule)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', reschedule)
    }
  }, [load, hasRunning])

  const handleLogDone = useCallback(() => {
    void load()
  }, [load])

  const toggleLogs = (taskId: string) => {
    setExpandedLogs((current) => ({ ...current, [taskId]: !current[taskId] }))
  }

  const stop = async (taskId: string) => {
    setStoppingId(taskId)
    try {
      await apiFetch(`/tasks/${taskId}/cancel`, { method: 'POST' })
      await load()
    } catch (err: any) {
      setError(err?.message || '停止任务失败')
    } finally {
      setStoppingId(null)
    }
  }

  const clearFinished = async () => {
    setClearing(true)
    try {
      await apiFetch('/tasks/clear', { method: 'POST' })
      await load()
    } catch (err: any) {
      setError(err?.message || '清空任务失败')
    } finally {
      setClearing(false)
    }
  }

  const running = useMemo(() => tasks.filter(task => !task.terminal), [tasks])
  const finished = useMemo(() => tasks.filter(task => task.terminal), [tasks])

  const renderTask = (task: any) => {
    const live = !task.terminal
    const logsOpen = live || Boolean(expandedLogs[task.task_id])
    return (
      <div key={task.task_id} className="space-y-3 px-4 py-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-[var(--text-primary)]">{TYPE_LABELS[task.type] || task.type}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${statusClass(task.status)}`}>
                {getTaskStatusText(task.status)}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-[var(--text-secondary)]">
              <span>进度 <b className="text-[var(--text-primary)]">{task.progress}</b></span>
              <span className="font-mono text-xs text-[var(--text-muted)]" title={task.task_id}>{task.task_id}</span>
            </div>
            {task.error ? (
              <div className="mt-2 break-all text-sm text-red-300">{task.error}</div>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {!live ? (
              <Button type="button" variant="outline" size="sm" onClick={() => toggleLogs(task.task_id)}>
                {logsOpen ? '收起日志' : '查看日志'}
              </Button>
            ) : null}
            {task.cancellable ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void stop(task.task_id)}
                disabled={stoppingId === task.task_id}
                className="border-red-500/35 text-red-300 hover:bg-red-500/10 hover:text-red-200"
              >
                <Square className="mr-2 h-3.5 w-3.5" />
                {stoppingId === task.task_id ? '停止中…' : '停止任务'}
              </Button>
            ) : null}
          </div>
        </div>
        {logsOpen ? (
          <TaskLogPanel taskId={task.task_id} compact live={live} onDone={handleLogDone} />
        ) : null}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">任务</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            运行中 {running.length} 个，最近结束 {finished.length} 个。失败任务也会留在这里，可看日志和原因。
          </p>
        </div>
        <div className="flex gap-2">
          {finished.length > 0 && (
            <Button variant="outline" size="sm" onClick={() => void clearFinished()} disabled={clearing}>
              <Trash2 className="mr-2 h-4 w-4" />
              {clearing ? '清空中…' : '清空任务'}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新
          </Button>
        </div>
      </div>

      {error ? <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div> : null}

      <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-card)]">
        {!loading && tasks.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-[var(--text-muted)]">还没有任务</div>
        ) : (
          <div className="divide-y divide-[var(--border)]">
            {running.map(renderTask)}
            {finished.map(renderTask)}
          </div>
        )}
      </div>
    </div>
  )
}
