import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigState } from '../types';
import { cloneJsonObject } from './utils';

export class OtherExtraParamsTemplate extends ExtraParamsConfigTemplate {
  readonly id = 'other';
  readonly labelKey = 'settings.extraParamsConfig.modelOther';
  readonly displayName = 'Other';
  readonly descriptionKey = 'settings.extraParamsConfig.otherDesc';
  readonly fallbackDescription = 'Use raw JSON editing only.';

  match(_modelName: string): boolean {
    return false;
  }

  getVisualSchema(): null {
    return null;
  }

  getDefaultState(): VisualConfigState {
    return {};
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    return {};
  }

  writeVisualState(params: JsonObject, _state: VisualConfigState, protocol: string): JsonObject {
    return cloneJsonObject(params);
  }
}
