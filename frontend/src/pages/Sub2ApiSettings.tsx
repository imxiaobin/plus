import { useEffect, useState } from "react";
import { LoaderCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/utils";
import { getConfig, invalidateConfigCache } from "@/lib/app-data";
import { useI18n } from "@/lib/i18n-context";

function apiErrorDetail(error: unknown): string {
  const raw = String((error as { message?: string })?.message || error || "");
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    /* not JSON */
  }
  return raw;
}

export default function Sub2ApiSettings() {
  const { t } = useI18n();
  const [form, setForm] = useState({
    sub2api_url: "",
    sub2api_api_key: "",
    sub2api_concurrency: "3",
    sub2api_priority: "50",
    sub2api_group_ids: "",
  });
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setForm({
          sub2api_url: String(cfg.sub2api_url || ""),
          sub2api_api_key: "",
          sub2api_concurrency: String(cfg.sub2api_concurrency || "3"),
          sub2api_priority: String(cfg.sub2api_priority || "50"),
          sub2api_group_ids: String(cfg.sub2api_group_ids || ""),
        });
        setKeyConfigured(String(cfg.sub2api_api_key_configured || "") === "1");
      })
      .catch(() => {
        setError(t("settings.sub2api.loadFailed"));
      });
  }, [t]);

  const save = async () => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await apiFetch("/config", {
        method: "PUT",
        body: JSON.stringify({ data: form }),
      });
      invalidateConfigCache();
      if (form.sub2api_api_key.trim()) setKeyConfigured(true);
      setForm((current) => ({ ...current, sub2api_api_key: "" }));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(apiErrorDetail(err) || t("settings.sub2api.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setError("");
    setNotice("");
    try {
      await apiFetch("/config/sub2api/test", {
        method: "POST",
        body: JSON.stringify({
          sub2api_url: form.sub2api_url,
          sub2api_api_key: form.sub2api_api_key,
        }),
      });
      setNotice(t("settings.sub2api.testOk"));
    } catch (err) {
      setError(apiErrorDetail(err) || t("settings.sub2api.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-[var(--text-muted)]">{t("settings.sub2api.desc")}</p>
      {error ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          {notice}
        </div>
      ) : null}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] divide-y divide-[var(--border)]/50">
        <label className="grid gap-1 px-4 py-3.5 text-sm">
          <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.url")}</span>
          <input
            value={form.sub2api_url}
            onChange={(event) => setForm((current) => ({ ...current, sub2api_url: event.target.value }))}
            placeholder="http://127.0.0.1:8080"
            className="control-surface"
          />
        </label>
        <label className="grid gap-1 px-4 py-3.5 text-sm">
          <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.apiKey")}</span>
          <input
            type="password"
            value={form.sub2api_api_key}
            onChange={(event) => setForm((current) => ({ ...current, sub2api_api_key: event.target.value }))}
            placeholder={keyConfigured ? t("settings.sub2api.apiKeyConfigured") : ""}
            autoComplete="new-password"
            className="control-surface"
          />
          <span className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.apiKeyHint")}</span>
        </label>
        <div className="grid gap-4 px-4 py-3.5 sm:grid-cols-3">
          <label className="grid gap-1 text-sm">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.concurrency")}</span>
            <input
              type="number"
              min={1}
              value={form.sub2api_concurrency}
              onChange={(event) => setForm((current) => ({ ...current, sub2api_concurrency: event.target.value }))}
              className="control-surface"
            />
          </label>
          <label className="grid gap-1 text-sm">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.priority")}</span>
            <input
              type="number"
              value={form.sub2api_priority}
              onChange={(event) => setForm((current) => ({ ...current, sub2api_priority: event.target.value }))}
              className="control-surface"
            />
          </label>
          <label className="grid gap-1 text-sm">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.groupIds")}</span>
            <input
              value={form.sub2api_group_ids}
              onChange={(event) => setForm((current) => ({ ...current, sub2api_group_ids: event.target.value }))}
              placeholder="1,2"
              className="control-surface"
            />
          </label>
        </div>
        <p className="px-4 pb-3 text-xs text-[var(--text-muted)]">{t("settings.sub2api.groupIdsHint")}</p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button onClick={() => void save()} disabled={saving}>
          <Save className="mr-2 h-4 w-4" />
          {saved ? `${t("common.saved")} ✓` : saving ? t("common.saving") : t("common.saveSettings")}
        </Button>
        <Button variant="outline" onClick={() => void testConnection()} disabled={testing}>
          {testing ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : null}
          {t("settings.sub2api.test")}
        </Button>
      </div>
    </div>
  );
}
