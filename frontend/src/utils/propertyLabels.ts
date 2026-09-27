import type { Property } from '../types/Property'

/*
 * Plain-language labels for the demo's building facts and mitigation features, shared
 * by the map popup, the impact card and the analysis.
 */

const FEATURE_LABELS: Record<string, string> = {
  shutters: 'Storm shutters',
  roof_straps: 'Roof straps',
}

export function featureLabel(features: string[]): string {
  if (features.length === 0) return 'None'

  // Shutters first: that is the order people say it in.
  return [...features]
    .sort((a, b) => (a === 'shutters' ? -1 : b === 'shutters' ? 1 : 0))
    .map((feature) => FEATURE_LABELS[feature] ?? feature.replace(/_/g, ' '))
    .map((label, index) => (index === 0 ? label : label.charAt(0).toLowerCase() + label.slice(1)))
    .join(' + ')
}

export function buildLabel(property: Pick<Property, 'vulnerability_class' | 'roof_shape'>): string {
  const code =
    property.vulnerability_class === 'post_fbc_2002'
      ? 'Built to the 2002 code'
      : 'Built before the 2002 code'
  const roof = property.roof_shape === 'unknown' ? '' : ` · ${property.roof_shape} roof`

  return `${code}${roof}`
}

/*
 * What an upgrade row changes, by the features it adds; falls back to the upgrade id
 * for a response that does not carry features.
 */
export function upgradeLabel(row: { upgrade_id: string; features_added: string[] | null }): string {
  if (row.features_added && row.features_added.length > 0) {
    return featureLabel(row.features_added)
  }

  return featureLabel(row.upgrade_id.split('_roof_straps').length > 1 ? ['shutters', 'roof_straps'] : [row.upgrade_id])
}
