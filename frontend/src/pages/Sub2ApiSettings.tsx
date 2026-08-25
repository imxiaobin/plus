import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, Plus, RefreshCw, Save, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/utils";
import { getConfig, invalidateConfigCache } from "@/lib/app-data";
import { useI18n } from "@/lib/i18n-context";

type Sub2Group = {
  id: string;
  name: string;
  platform?: string;
  status?: string;
};

type MappingRow = { from: string; to: string };

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

function parseCsv(value: string): string[] {
  return String(value || "")
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinCsv(ids: string[]): string {
  return [...new Set(ids.map((item) => item.trim()).filter(Boolean))].join(",");
}

function parseMapping(value: string): MappingRow[] {
  try {
    const parsed = JSON.parse(value || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
    return Object.entries(parsed).map(([from, to]) => ({ from, to: String(to ?? "") }));
  } catch {
    return [];
  }
}

function serializeMapping(rows: MappingRow[]): string {
  const mapping: Record<string, string> = {};
  for (const row of rows) {
    const from = row.from.trim();
    const to = row.to.trim();
    if (from && to) mapping[from] = to;
  }
  return Object.keys(mapping).length ? JSON.stringify(mapping) : "";
}

function firstGroupId(value: string): number {
  const parsed = Number(parseCsv(value)[0] || 0);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

export default function Sub2ApiSettings() {
  const { t } = useI18n();
  const [form, setForm] = useState({
    sub2api_url: "",
    sub2api_api_key: "",
    sub2api_concurrency: "3",
    sub2api_priority: "50",
    sub2api_group_ids: "",
    sub2api_models: "",
    sub2api_model_mapping: "",
  });
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [groups, setGroups] = useState<Sub2Group[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [customModel, setCustomModel] = useState("");
  const [mappingRows, setMappingRows] = useState<MappingRow[]>([]);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [groupsLoaded, setGroupsLoaded] = useState(false);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedGroupIds = useMemo(() => parseCsv(form.sub2api_group_ids), [form.sub2api_group_ids]);
  const selectedModels = useMemo(() => parseCsv(form.sub2api_models), [form.sub2api_models]);
  const canLoad = Boolean(form.sub2api_url.trim() && (form.sub2api_api_key.trim() || keyConfigured));

  const fetchGroups = async (url: string, apiKey: string) => {
    if (!url.trim()) return;
    setLoadingGroups(true);
    try {
      const data = await apiFetch("/config/sub2api/groups", {
        method: "POST",
        body: JSON.stringify({ sub2api_url: url, sub2api_api_key: apiKey }),
      });
      setGroups(Array.isArray(data?.items) ? data.items : []);
      setGroupsLoaded(true);
    } catch (err) {
      setGroups([]);
      setGroupsLoaded(false);
      setError(apiErrorDetail(err) || t("settings.sub2api.loadGroupsFailed"));
    } finally {
      setLoadingGroups(false);
    }
  };

  const fetchModels = async (url: string, apiKey: string, groupId: number) => {
    if (!url.trim()) return;
    setLoadingModels(true);
    try {
      const data = await apiFetch("/config/sub2api/models", {
        method: "POST",
        body: JSON.stringify({
          sub2api_url: url,
          sub2api_api_key: apiKey,
          group_id: groupId,
        }),
      });
      const items = Array.isArray(data?.items) ? data.items.map((item: unknown) => String(item || "").trim()).filter(Boolean) : [];
      setModels(items);
      setModelsLoaded(true);
    } catch (err) {
      setModels([]);
      setModelsLoaded(false);
      setError(apiErrorDetail(err) || t("settings.sub2api.loadModelsFailed"));
    } finally {
      setLoadingModels(false);
    }
  };

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        const url = String(cfg.sub2api_url || "");
        const configured = String(cfg.sub2api_api_key_configured || "") === "1";
        const groupIds = String(cfg.sub2api_group_ids || "");
        setForm({
          sub2api_url: url,
          sub2api_api_key: "",
          sub2api_concurrency: String(cfg.sub2api_concurrency || "3"),
          sub2api_priority: String(cfg.sub2api_priority || "50"),
          sub2api_group_ids: groupIds,
          sub2api_models: String(cfg.sub2api_models || ""),
          sub2api_model_mapping: String(cfg.sub2api_model_mapping || ""),
        });
        setMappingRows(parseMapping(String(cfg.sub2api_model_mapping || "")));
        setKeyConfigured(configured);
        if (url && configured) {
          void fetchGroups(url, "");
          void fetchModels(url, "", firstGroupId(groupIds));
        }
      })
      .catch(() => {
        setError(t("settings.sub2api.loadFailed"));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      await fetchGroups(form.sub2api_url, form.sub2api_api_key);
      await fetchModels(form.sub2api_url, form.sub2api_api_key, firstGroupId(form.sub2api_group_ids));
    } catch (err) {
      setError(apiErrorDetail(err) || t("settings.sub2api.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const toggleValue = (field: "sub2api_group_ids" | "sub2api_models", id: string) => {
    const current = parseCsv(form[field]);
    const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
    setForm((state) => ({ ...state, [field]: joinCsv(next) }));
  };

  const addCustomModel = () => {
    const value = customModel.trim();
    if (!value) return;
    setForm((state) => ({ ...state, sub2api_models: joinCsv([...parseCsv(state.sub2api_models), value]) }));
    setCustomModel("");
  };

  const persistMapping = (rows: MappingRow[]) => {
    setMappingRows(rows);
    setForm((state) => ({ ...state, sub2api_model_mapping: serializeMapping(rows) }));
  };

  const updateMapping = (index: number, key: keyof MappingRow, value: string) => {
    persistMapping(mappingRows.map((row, rowIndex) => (rowIndex === index ? { ...row, [key]: value } : row)));
  };

  const addMapping = () => {
    persistMapping([...mappingRows, { from: "", to: "" }]);
  };

  const removeMapping = (index: number) => {
    persistMapping(mappingRows.filter((_, rowIndex) => rowIndex !== index));
  };

  const knownGroupIds = new Set(groups.map((item) => item.id));
  const unknownGroups = selectedGroupIds.filter((id) => !knownGroupIds.has(id));
  const modelOptions = [...new Set([...models, ...selectedModels])];
  const unknownModels = selectedModels.filter((id) => !models.includes(id));

  const chipClass = (selected: boolean) =>
    `rounded-lg border px-3 py-1.5 text-left text-sm transition-colors ${
      selected
        ? "border-[var(--accent)] bg-[var(--accent)]/15 text-[var(--text-primary)]"
        : "border-[var(--border)] bg-[var(--bg)] text-[var(--text-secondary)] hover:border-[var(--accent)]/40"
    }`;

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
      <datalist id="sub2-model-options">
        {modelOptions.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
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
        <div className="grid gap-4 px-4 py-3.5 sm:grid-cols-2">
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
        </div>
        <div className="grid gap-2 px-4 py-3.5 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.groupIds")}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void fetchGroups(form.sub2api_url, form.sub2api_api_key)}
              disabled={loadingGroups || !canLoad}
            >
              {loadingGroups ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              {t("settings.sub2api.loadGroups")}
            </Button>
          </div>
          {groupsLoaded || groups.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {groups.map((group) => (
                <button key={group.id} type="button" onClick={() => toggleValue("sub2api_group_ids", group.id)} className={chipClass(selectedGroupIds.includes(group.id))}>
                  <span className="font-medium">{group.name}</span>
                  <span className="ml-2 text-xs text-[var(--text-muted)]">
                    #{group.id}
                    {group.platform ? ` · ${group.platform}` : ""}
                  </span>
                </button>
              ))}
              {unknownGroups.map((id) => (
                <button key={`unknown-group-${id}`} type="button" onClick={() => toggleValue("sub2api_group_ids", id)} className={chipClass(true)}>
                  #{id}
                </button>
              ))}
            </div>
          ) : (
            <input
              value={form.sub2api_group_ids}
              onChange={(event) => setForm((current) => ({ ...current, sub2api_group_ids: event.target.value }))}
              placeholder="1,2"
              className="control-surface"
            />
          )}
          {groupsLoaded && groups.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.groupsEmpty")}</p>
          ) : null}
          <p className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.groupIdsHint")}</p>
        </div>
        <div className="grid gap-2 px-4 py-3.5 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.models")}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void fetchModels(form.sub2api_url, form.sub2api_api_key, firstGroupId(form.sub2api_group_ids))}
              disabled={loadingModels || !canLoad}
            >
              {loadingModels ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              {t("settings.sub2api.loadModels")}
            </Button>
          </div>
          {modelsLoaded || models.length > 0 || selectedModels.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {models.map((model) => (
                <button key={model} type="button" onClick={() => toggleValue("sub2api_models", model)} className={chipClass(selectedModels.includes(model))}>
                  {model}
                </button>
              ))}
              {unknownModels.map((model) => (
                <button key={`unknown-model-${model}`} type="button" onClick={() => toggleValue("sub2api_models", model)} className={chipClass(true)}>
                  {model}
                </button>
              ))}
            </div>
          ) : null}
          <div className="flex gap-2">
            <input
              value={customModel}
              onChange={(event) => setCustomModel(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addCustomModel();
                }
              }}
              list="sub2-model-options"
              placeholder={t("settings.sub2api.customModel")}
              className="control-surface flex-1"
            />
            <Button type="button" variant="outline" size="sm" onClick={addCustomModel}>
              <Plus className="mr-1 h-4 w-4" />
              {t("settings.sub2api.addModel")}
            </Button>
          </div>
          <p className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.modelsHint")}</p>
        </div>
        <div className="grid gap-2 px-4 py-3.5 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-[var(--text-secondary)]">{t("settings.sub2api.modelMapping")}</span>
            <Button type="button" variant="outline" size="sm" onClick={addMapping}>
              <Plus className="mr-1 h-4 w-4" />
              {t("settings.sub2api.addMapping")}
            </Button>
          </div>
          {mappingRows.length ? (
            <div className="space-y-2">
              {mappingRows.map((row, index) => (
                <div key={`mapping-${index}`} className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-2">
                  <input
                    value={row.from}
                    onChange={(event) => updateMapping(index, "from", event.target.value)}
                    list="sub2-model-options"
                    placeholder={t("settings.sub2api.mappingFrom")}
                    className="control-surface"
                  />
                  <span className="text-[var(--text-muted)]">→</span>
                  <input
                    value={row.to}
                    onChange={(event) => updateMapping(index, "to", event.target.value)}
                    list="sub2-model-options"
                    placeholder={t("settings.sub2api.mappingTo")}
                    className="control-surface"
                  />
                  <Button type="button" variant="ghost" size="icon" onClick={() => removeMapping(index)} title={t("settings.sub2api.removeMapping")}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.mappingEmpty")}</p>
          )}
          <p className="text-xs text-[var(--text-muted)]">{t("settings.sub2api.modelMappingHint")}</p>
        </div>
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
