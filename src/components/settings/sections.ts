export const SETTINGS_ROUTE_SECTIONS = [
  'teams',
  'roles',
  'models',
  'thirdPartyServices',
  'skills',
  'maintenance',
  'advanced',
] as const;

export type SettingsRouteSection = (typeof SETTINGS_ROUTE_SECTIONS)[number];

export const DEFAULT_SETTINGS_SECTION: SettingsRouteSection = 'teams';

export const SETTINGS_NAV_ITEMS = [
  'teams',
  'roles',
  'models',
  'thirdPartyServices',
  'skills',
  'maintenance',
  'advanced',
  'quickInit',
] as const;

export type SettingsNavItemId = (typeof SETTINGS_NAV_ITEMS)[number];

export function isSettingsRouteSection(value: string): value is SettingsRouteSection {
  return SETTINGS_ROUTE_SECTIONS.includes(value as SettingsRouteSection);
}
