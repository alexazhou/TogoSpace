export type JsonObject = Record<string, unknown>;
export type VisualFieldValue = boolean | number | string | null;
export type VisualConfigState = Record<string, VisualFieldValue>;

export interface VisualConfigOption {
  value: string;
  labelKey?: string;
  fallbackLabel: string;
}

interface VisualConfigFieldBase {
  key: string;
  labelKey: string;
  fallbackLabel: string;
  visibleWhen?: {
    key: string;
    equals: VisualFieldValue;
  };
}

export interface VisualConfigSwitchField extends VisualConfigFieldBase {
  control: 'switch';
}

export interface VisualConfigSelectField extends VisualConfigFieldBase {
  control: 'select';
  options: VisualConfigOption[];
}

export interface VisualConfigNumberField extends VisualConfigFieldBase {
  control: 'number';
  min?: number;
  max?: number;
  step?: number;
}

export type VisualConfigField =
  | VisualConfigSwitchField
  | VisualConfigSelectField
  | VisualConfigNumberField;

export interface VisualConfigSchema {
  fields: VisualConfigField[];
}

export abstract class ExtraParamsConfigTemplate {
  abstract readonly id: string;
  abstract readonly labelKey: string;
  abstract readonly displayName: string;
  abstract readonly descriptionKey: string;
  abstract readonly fallbackDescription: string;

  abstract match(modelName: string): boolean;
  abstract getVisualSchema(): VisualConfigSchema | null;
  abstract getDefaultState(): VisualConfigState;
  abstract readVisualState(params: JsonObject, protocol: string): VisualConfigState;
  abstract writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject;
}
