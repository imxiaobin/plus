import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, ArrowRightLeft, KeyRound, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiFetch } from "@/lib/utils";
import { useI18n } from "@/lib/i18n-context";

type MonitorGroup = { id: string; name: string };

type MonitorItem = {
  account_id: number;
  email: string;
  sub2_account_id: string;
  authorized_at: string;
  availability: string;
  status: string;
  schedulable: boolean;
  in_use: boolean;
  current_concurrency: number;
  concurrency: number;
  groups: MonitorGroup[];
  models: string[];
  models_unlimited: boolean;
  error_message: string;
  last_used_at: string;
  codex_5h_used_percent: number | null;
  codex_7d_used_percent: number | null;
  today_requests: number;
  today_cost: number;
  can_reauthorize: boolean;
  authorize_status: string;
};

type MonitorSummary = {
  total: number;
  available: number;
  in_use: number;
  error: number;
  rate_limited: number;
  inactive: number;
  missing: number;
  paused: number;
  unschedulable: number;
};

type AvailabilityFilter = "all" | "available" | "in_use" | "error" | "rate_limited" | "missing";

function apiErrorDetail(error: unknown): string {
  const raw = String((error as { message?: string })?.message || error || "");
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string" && parsed.detail.trim()) return parsed.detail;
  } catch {
    /* not JSON */
  }
  return raw;
}

function formatTime(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatPercent(value: number | null) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${Math.round(value)}%`;
}

function availabilityClass(value: string) {
  if (value === "available") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (value === "error" || value === "missing") return "border-red-500/30 bg-red-500/10 text-red-300";
  if (value === "rate_limited" || value === "paused") return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  return "border-[var(--border)] bg-[var(--bg-pane)] text-[var(--text-muted)]";
}

export default function Sub2ApiMonitor() {
  const { t } = useI18n();
  const [items, setItems] = useState<MonitorItem[]>([]);
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notConfigured, setNotConfigured] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<AvailabilityFilter>("all");
  const [authorizingId, setAuthorizingId] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [noticeIsTask, setNoticeIsTask] = useState(false);
  const [mappingBusy, setMappingBusy] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await apiFetch("/config/sub2api/monitor");
      setItems(Array.isArray(data?.items) ? data.items : []);
      setSummary(data?.summary || null);
      setError("");
      setNotConfigured(false);
    } catch (err) {
      const detail = apiErrorDetail(err);
      if (detail.includes("请先在设置中填写 Sub2API")) {
        setNotConfigured(true);
        setError("");
      } else {
        setError(detail || t("sub2.monitor.loadFailed"));
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
    const tick = () => {
      if (document.hidden) return;
      void load(true);
    };
    const timer = window.setInterval(tick, 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  const reauthorize = async (item: MonitorItem) => {
    setAuthorizingId(item.account_id);
    setError("");
    setNotice("");
    setNoticeIsTask(false);
    try {
      await apiFetch(`/accounts/${item.account_id}/authorize/sub2api`, { method: "POST" });
      setNotice(t("sub2.monitor.reauthorizeStarted"));
      setNoticeIsTask(true);
      await load(true);
    } catch (err) {
      setError(apiErrorDetail(err) || t("sub2.monitor.reauthorizeFailed"));
    } finally {
      setAuthorizingId(null);
    }
  };

  const toggleSolTerra = async (enable: boolean) => {
    if (mappingBusy) return;
    setMappingBusy(true);
    setError("");
    setNotice("");
    setNoticeIsTask(false);
    try {
      const preview = await apiFetch("/config/sub2api/sol-terra-mapping");
      const total = Number(preview?.total || 0);
      const previewItems = Array.isArray(preview?.items) ? preview.items : [];
      if (total <= 0) {
        setError(t("sub2.monitor.solTerra.empty"));
        return;
      }
      const examples = previewItems
        .slice(0, 5)
        .map((item: { email?: string; name?: string }) => item.email || item.name || "")
        .filter(Boolean)
        .join("、");
      const confirmed = window.confirm(
        t(enable ? "sub2.monitor.solTerra.confirmAdd" : "sub2.monitor.solTerra.confirmRemove", {
          count: total,
          examples: examples ? `\n例如：${examples}${total > 5 ? "…" : ""}` : "",
        }),
      );
      if (!confirmed) return;
      const result = await apiFetch("/config/sub2api/sol-terra-mapping", {
        method: "POST",
        body: JSON.stringify({ enable }),
      });
      const success = Number(result?.success || 0);
      const failed = Number(result?.failed || 0);
      const updated = Number(result?.updated || 0);
      const skipped = Number(result?.skipped || 0);
      if (failed > 0) {
        setNotice(
          t("sub2.monitor.solTerra.partial", {
            action: enable ? t("sub2.monitor.solTerra.add") : t("sub2.monitor.solTerra.remove"),
            success,
            updated,
            failed,
          }),
        );
      } else {
        const suffix = skipped ? t("sub2.monitor.solTerra.skipped", { skipped }) : "";
        setNotice(
          `${t(enable ? "sub2.monitor.solTerra.doneAdd" : "sub2.monitor.solTerra.doneRemove", { success })}${suffix}`,
        );
      }
      await load(true);
    } catch (err) {
      setError(apiErrorDetail(err) || t("sub2.monitor.solTerra.failed"));
    } finally {
      setMappingBusy(false);
    }
  };

  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    return items.filter((item) => {
      if (filter === "in_use" && !item.in_use) return false;
      if (filter !== "all" && filter !== "in_use" && item.availability !== filter) return false;
      if (!text) return true;
      return (
        item.email.toLowerCase().includes(text) ||
        item.sub2_account_id.includes(text) ||
        item.groups.some((group) => group.name.toLowerCase().includes(text))
      );
    });
  }, [items, query, filter]);

  const availabilityLabel = (value: string) => {
    switch (value) {
      case "available":
        return t("sub2.monitor.availability.available");
      case "in_use":
        return t("sub2.monitor.availability.in_use");
      case "error":
        return t("sub2.monitor.availability.error");
      case "rate_limited":
        return t("sub2.monitor.availability.rate_limited");
      case "paused":
        return t("sub2.monitor.availability.paused");
      case "inactive":
        return t("sub2.monitor.availability.inactive");
      case "unschedulable":
        return t("sub2.monitor.availability.unschedulable");
      case "missing":
        return t("sub2.monitor.availability.missing");
      default:
        return value;
    }
  };

  const filters: { id: AvailabilityFilter; label: string; count: number }[] = [
    { id: "all", label: t("sub2.monitor.filter.all"), count: summary?.total ?? 0 },
    { id: "available", label: t("sub2.monitor.filter.available"), count: summary?.available ?? 0 },
    { id: "in_use", label: t("sub2.monitor.filter.inUse"), count: summary?.in_use ?? 0 },
    { id: "error", label: t("sub2.monitor.filter.error"), count: summary?.error ?? 0 },
    { id: "rate_limited", label: t("sub2.monitor.filter.rateLimited"), count: summary?.rate_limited ?? 0 },
    { id: "missing", label: t("sub2.monitor.filter.missing"), count: summary?.missing ?? 0 },
  ];

  const cards = [
    { label: t("sub2.monitor.summary.total"), value: summary?.total ?? 0 },
    { label: t("sub2.monitor.summary.available"), value: summary?.available ?? 0 },
    { label: t("sub2.monitor.summary.inUse"), value: summary?.in_use ?? 0 },
    { label: t("sub2.monitor.summary.error"), value: summary?.error ?? 0 },
    { label: t("sub2.monitor.summary.missing"), value: summary?.missing ?? 0 },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">{t("sub2.monitor.title")}</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">{t("sub2.monitor.desc")}</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => void toggleSolTerra(true)} disabled={loading || mappingBusy || notConfigured}>
            <ArrowRightLeft className="mr-2 h-4 w-4" />
            {t("sub2.monitor.solTerra.add")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void toggleSolTerra(false)} disabled={loading || mappingBusy || notConfigured}>
            <ArrowRightLeft className="mr-2 h-4 w-4" />
            {t("sub2.monitor.solTerra.remove")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            {t("common.refresh")}
          </Button>
        </div>
      </div>

      {notConfigured ? (
        <Card className="flex items-start gap-3">
          <Activity className="mt-0.5 h-4 w-4 text-[var(--text-muted)]" />
          <div className="space-y-2 text-sm">
            <p className="text-[var(--text-secondary)]">{t("sub2.monitor.notConfigured")}</p>
            <Link to="/settings?tab=sub2api" className="text-[var(--accent)] hover:underline">
              {t("sub2.monitor.goSettings")}
            </Link>
          </div>
        </Card>
      ) : null}

      {error ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>
      ) : null}
      {notice ? (
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          {notice}
          {noticeIsTask ? (
            <>
              {" "}
              <Link to="/tasks" className="underline underline-offset-2">
                {t("sub2.monitor.viewTask")}
              </Link>
            </>
          ) : null}
        </div>
      ) : null}

      {!notConfigured ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {cards.map((card) => (
              <Card key={card.label} className="py-3">
                <div className="text-xs text-[var(--text-muted)]">{card.label}</div>
                <div className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">{card.value}</div>
              </Card>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {filters.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setFilter(item.id)}
                className={`rounded-lg border px-3 py-1.5 text-sm ${
                  filter === item.id
                    ? "border-[var(--accent)] bg-[var(--accent)]/15 text-[var(--text-primary)]"
                    : "border-[var(--border)] bg-[var(--bg)] text-[var(--text-secondary)]"
                }`}
              >
                {item.label} {item.count}
              </button>
            ))}
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("sub2.monitor.search")}
              className="control-surface ml-auto min-w-[180px] flex-1 sm:max-w-xs"
            />
          </div>

          <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-card)]">
            {loading && items.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-[var(--text-muted)]">{t("common.loading")}</div>
            ) : filtered.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-[var(--text-muted)]">{t("sub2.monitor.empty")}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="border-b border-[var(--border)] text-xs text-[var(--text-muted)]">
                    <tr>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.email")}</th>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.status")}</th>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.groups")}</th>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.models")}</th>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.usage")}</th>
                      <th className="px-4 py-3 font-medium">{t("sub2.monitor.col.lastUsed")}</th>
                      <th className="px-4 py-3 font-medium"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {filtered.map((item) => (
                      <tr key={`${item.account_id}-${item.sub2_account_id}`}>
                        <td className="px-4 py-3 align-top">
                          <div className="font-medium text-[var(--text-primary)]">{item.email}</div>
                          <div className="mt-0.5 font-mono text-xs text-[var(--text-muted)]">#{item.sub2_account_id}</div>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <div className="flex flex-wrap gap-1">
                            <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${availabilityClass(item.availability)}`}>
                              {availabilityLabel(item.availability)}
                            </span>
                            {item.in_use ? (
                              <span className="inline-flex rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">
                                {t("sub2.monitor.availability.in_use")} {item.current_concurrency}/{item.concurrency || "?"}
                              </span>
                            ) : null}
                          </div>
                          {item.error_message ? (
                            <div className="mt-1 max-w-xs break-all text-xs text-red-300">{item.error_message}</div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 align-top text-[var(--text-secondary)]">
                          {item.groups.length
                            ? item.groups.map((group) => group.name).join("、")
                            : "—"}
                        </td>
                        <td className="px-4 py-3 align-top">
                          {item.models_unlimited ? (
                            <span className="text-xs text-[var(--text-muted)]">{t("sub2.monitor.unlimitedModels")}</span>
                          ) : item.models.length ? (
                            <div className="flex max-w-xs flex-wrap gap-1">
                              {item.models.slice(0, 6).map((model) => (
                                <span key={model} className="rounded border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)]">
                                  {model}
                                </span>
                              ))}
                              {item.models.length > 6 ? (
                                <span className="text-[11px] text-[var(--text-muted)]">+{item.models.length - 6}</span>
                              ) : null}
                            </div>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="px-4 py-3 align-top text-xs text-[var(--text-secondary)]">
                          <div>5h {formatPercent(item.codex_5h_used_percent)}</div>
                          <div>7d {formatPercent(item.codex_7d_used_percent)}</div>
                          <div>
                            {t("sub2.monitor.today")} {item.today_requests} / {item.today_cost ? item.today_cost.toFixed(2) : "0"}
                          </div>
                        </td>
                        <td className="px-4 py-3 align-top text-xs text-[var(--text-muted)]">{formatTime(item.last_used_at || item.authorized_at)}</td>
                        <td className="px-4 py-3 align-top text-right">
                          {item.can_reauthorize || item.authorize_status === "running" ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={item.authorize_status === "running" || authorizingId === item.account_id}
                              onClick={() => void reauthorize(item)}
                            >
                              <KeyRound className="mr-1.5 h-3.5 w-3.5" />
                              {item.authorize_status === "running" || authorizingId === item.account_id
                                ? t("sub2.monitor.reauthorizing")
                                : t("sub2.monitor.reauthorize")}
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
