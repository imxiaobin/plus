import { useCallback, useEffect, useState } from 'react'
import { Check, CircleAlert, LoaderCircle, Power, RefreshCw, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/utils'

type HttpProxy = {
  id: number
  url: string
  host: string
  port: number | null
  user: string
  region: string
  is_active: boolean
  success_count: number
  fail_count: number
  last_checked: string | null
}

type ImportResult = {
  imported: number
  skipped: number
  invalid: string[]
  total: number
}

function formatDate(value: string | null) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

export default function HttpProxyPoolSettings() {
  const [items, setItems] = useState<HttpProxy[]>([])
  const [active, setActive] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [text, setText] = useState('')
  const [checkingId, setCheckingId] = useState<number | null>(null)
  const [deletingAll, setDeletingAll] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await apiFetch('/http-proxies')
      setItems(Array.isArray(data?.items) ? data.items : [])
      setActive(Number(data?.active || 0))
    } catch (err: any) {
      setError(err?.message || '读取 HTTP 代理池失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const importProxies = async () => {
    if (!text.trim()) {
      setError('请粘贴代理，每行一条')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const result = await apiFetch('/http-proxies/import', {
        method: 'POST',
        body: JSON.stringify({ text }),
      }) as ImportResult
      const invalidHint = result.invalid?.length ? `，无法解析 ${result.invalid.length} 条` : ''
      setNotice(`导入 ${result.imported} 条，跳过重复 ${result.skipped} 条${invalidHint}`)
      setText('')
      await load()
    } catch (err: any) {
      setError(err?.message || '批量导入失败')
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async (item: HttpProxy) => {
    try {
      await apiFetch(`/http-proxies/${item.id}`, {
        method: 'PUT',
        body: JSON.stringify({ is_active: !item.is_active }),
      })
      await load()
    } catch (err: any) {
      setError(err?.message || '更新代理状态失败')
    }
  }

  const remove = async (item: HttpProxy) => {
    if (!window.confirm(`确定删除 ${item.host}:${item.port || ''}？`)) return
    try {
      await apiFetch(`/http-proxies/${item.id}`, { method: 'DELETE' })
      await load()
    } catch (err: any) {
      setError(err?.message || '删除代理失败')
    }
  }

  const removeAll = async () => {
    if (!items.length) return
    if (!window.confirm(`确定删除全部 ${items.length} 条代理？此操作不可恢复。`)) return
    setDeletingAll(true)
    setError('')
    setNotice('')
    try {
      const result = await apiFetch('/http-proxies', { method: 'DELETE' })
      setNotice(`已删除 ${Number(result?.deleted || items.length)} 条代理`)
      await load()
    } catch (err: any) {
      setError(err?.message || '删除全部代理失败')
    } finally {
      setDeletingAll(false)
    }
  }

  const check = async (item: HttpProxy) => {
    setCheckingId(item.id)
    setError('')
    setNotice('')
    try {
      const data = await apiFetch(`/http-proxies/${item.id}/check`, { method: 'POST' })
      setNotice(data?.ok ? `${item.host} 预检通过：${data.detail}` : `${item.host} 预检失败：${data?.detail || ''}`)
      await load()
    } catch (err: any) {
      setError(err?.message || '预检失败')
    } finally {
      setCheckingId(null)
    }
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-[var(--text-muted)]">
        批量导入 HTTP 动态代理，格式 <span className="font-mono text-[var(--text-secondary)]">host:port:user:pass</span>
        。也支持 <span className="font-mono text-[var(--text-secondary)]">http://user:pass@host:port</span>。密码不会明文展示。
      </p>

      {notice && (
        <div className="flex items-center gap-2 break-all border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
          <Check className="h-4 w-4 shrink-0" />
          {notice}
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 break-all border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <section className="border border-[var(--border)] bg-[var(--bg-card)] p-4">
        <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
          批量导入
          <textarea
            value={text}
            onChange={event => setText(event.target.value)}
            rows={6}
            placeholder={'us.rrp.example.com:10000:USER123-zone-custom-region-US:password\ngw.example.com:10000:user:pass'}
            className="rounded-md border border-[var(--border)] bg-transparent px-3 py-2 font-mono text-xs text-[var(--text-primary)]"
          />
        </label>
        <div className="mt-3 flex justify-end">
          <Button onClick={() => void importProxies()} disabled={saving}>
            {saving && <LoaderCircle className="mr-1.5 h-4 w-4 animate-spin" />}
            {saving ? '导入中' : '导入'}
          </Button>
        </div>
      </section>

      <section className="overflow-hidden border border-[var(--border)] bg-[var(--bg-card)]">
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <div className="text-sm font-medium text-[var(--text-primary)]">
            代理列表 <span className="text-[var(--text-muted)]">{items.length}</span>
            <span className="ml-2 text-xs text-[var(--text-muted)]">启用 {active}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void removeAll()}
              disabled={loading || deletingAll || items.length === 0}
            >
              {deletingAll ? <LoaderCircle className="mr-1.5 h-4 w-4 animate-spin" /> : <Trash2 className="mr-1.5 h-4 w-4 text-red-400" />}
              {deletingAll ? '删除中' : '删除全部'}
            </Button>
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={`mr-1.5 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-[var(--bg-pane)]/60 text-left text-xs text-[var(--text-muted)]">
              <tr>
                <th className="px-4 py-3">代理</th>
                <th className="px-4 py-3">账号</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">成功/失败</th>
                <th className="px-4 py-3">最近检测</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {items.map(item => (
                <tr key={item.id} className={`${item.is_active ? '' : 'opacity-50'} hover:bg-[var(--bg-hover)]/50`}>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--text-primary)]">
                    {item.host}{item.port ? `:${item.port}` : ''}
                  </td>
                  <td className="max-w-[280px] break-all px-4 py-3 font-mono text-xs text-[var(--text-secondary)]">
                    {item.user || '-'}
                  </td>
                  <td className="px-4 py-3">
                    {item.is_active ? <span className="text-emerald-400">启用</span> : <span className="text-[var(--text-muted)]">停用</span>}
                  </td>
                  <td className="px-4 py-3 text-[var(--text-secondary)]">{item.success_count}/{item.fail_count}</td>
                  <td className="px-4 py-3 text-xs text-[var(--text-muted)]">{formatDate(item.last_checked)}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" title="预检出网" onClick={() => void check(item)} disabled={checkingId === item.id}>
                        <RefreshCw className={`h-4 w-4 ${checkingId === item.id ? 'animate-spin' : ''}`} />
                      </Button>
                      <Button variant="ghost" size="sm" title={item.is_active ? '停用' : '启用'} onClick={() => void toggleActive(item)}>
                        <Power className={`h-4 w-4 ${item.is_active ? 'text-emerald-400' : 'text-[var(--text-muted)]'}`} />
                      </Button>
                      <Button variant="ghost" size="sm" title="删除" onClick={() => void remove(item)}>
                        <Trash2 className="h-4 w-4 text-red-400" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-14 text-center text-[var(--text-muted)]">
                    暂无 HTTP 代理，请先批量导入
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
