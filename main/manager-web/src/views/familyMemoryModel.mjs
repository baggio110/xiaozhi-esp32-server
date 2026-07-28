export const DEFAULT_FAMILY_MEMORY_SETTINGS = Object.freeze({
  enabled: false,
  family_id: '',
  database_path: 'data/family_identity.db'
})

export function settingsFingerprint(settings) {
  return `${settings.family_id || ''}\n${settings.database_path || ''}`
}

export function canEnableFamilyMemory(preflight, checkedFingerprint, settings) {
  return Boolean(
    preflight &&
    preflight.can_enable === true &&
    preflight.overall !== 'FAIL' &&
    checkedFingerprint === settingsFingerprint(settings)
  )
}

export function checkTagType(level) {
  if (level === 'PASS') return 'success'
  if (level === 'FAIL') return 'danger'
  if (level === 'WARN') return 'warning'
  return 'info'
}

export function officialVoiceprintOptions(items) {
  if (!Array.isArray(items)) return []
  return items
    .filter(item => item && typeof item.id === 'string' && item.id.length > 0)
    .map(item => ({
      value: item.id,
      label: item.sourceName ? `${item.sourceName}（${item.id}）` : item.id
    }))
}

export const FAMILY_MEMORY_ACTIONS = Object.freeze({
  person: Object.freeze(['create', 'rename', 'enable', 'disable', 'show']),
  voiceprint: Object.freeze(['bind', 'revoke', 'replace', 'list'])
})
