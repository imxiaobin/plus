import { useCallback, useEffect, useState } from 'react'
import ProviderCards from '@/components/settings/ProviderCards'
import { getConfigOptions } from '@/lib/app-data'
import type { ProviderOption, ProviderSetting } from '@/lib/config-options'
import { useI18n } from '@/lib/i18n-context'
import type { TranslationKey } from '@/lib/i18n'

type ProviderKind = 'mailbox' | 'sms'

const KIND_CONFIG: Record<ProviderKind, {
  catalogKey: 'mailbox_providers' | 'sms_providers'
  settingsKey: 'mailbox_settings' | 'sms_settings'
  usageKey: TranslationKey
}> = {
  mailbox: {
    catalogKey: 'mailbox_providers',
    settingsKey: 'mailbox_settings',
    usageKey: 'settings.provider.mailboxUsage',
  },
  sms: {
    catalogKey: 'sms_providers',
    settingsKey: 'sms_settings',
    usageKey: 'settings.provider.smsUsage',
  },
}

export default function Settings({ kind = 'mailbox' }: { kind?: ProviderKind }) {
  const { t } = useI18n()
  const config = KIND_CONFIG[kind]
  const [catalog, setCatalog] = useState<ProviderOption[]>([])
  const [settings, setSettings] = useState<ProviderSetting[]>([])
  const [error, setError] = useState('')

  const loadProviders = useCallback(async () => {
    try {
      const options = await getConfigOptions()
      setCatalog(options[config.catalogKey] || [])
      setSettings(options[config.settingsKey] || [])
      setError('')
    } catch {
      setCatalog([])
      setSettings([])
      setError(t('register.providerMetadataError'))
    }
  }, [config.catalogKey, config.settingsKey, t])

  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}
      <div className="rounded-lg border border-[var(--accent-edge)] bg-[var(--accent-soft)] px-4 py-3 text-sm text-[var(--text-secondary)]">
        {t(config.usageKey)}
      </div>
      <ProviderCards
        providerType={kind}
        catalog={catalog}
        settings={settings}
        onReload={loadProviders}
      />
    </div>
  )
}
