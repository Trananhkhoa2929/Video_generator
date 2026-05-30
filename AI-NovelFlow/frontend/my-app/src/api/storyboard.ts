import { api } from './index';

export interface StoryboardNormalizeRequest {
  panels?: Record<string, unknown>[];
  style?: string;
  store?: boolean;
  create_shots?: boolean;
  overwrite_shots?: boolean;
}

export interface StoryboardConvertRequest {
  panels?: Record<string, unknown>[];
  style?: string;
  overwrite?: boolean;
  store?: boolean;
}

export interface StoryboardGeneratePanelsRequest {
  style?: string;
  store?: boolean;
  create_shots?: boolean;
  overwrite_shots?: boolean;
  max_segments?: number;
}

export interface VisualAssetExtractRequest {
  store?: boolean;
  update_existing?: boolean;
  max_characters?: number;
  max_scenes?: number;
  max_props?: number;
  ensure_segments?: boolean;
  enrich_segments?: boolean;
}

export const storyboardApi = {
  extractAssets: (novelId: string, chapterId: string, data: VisualAssetExtractRequest = {}) =>
    api.post<Record<string, unknown>>(`/novels/${novelId}/chapters/${chapterId}/storyboard/extract-assets`, data),

  fetchAssets: (novelId: string, chapterId: string) =>
    api.get<Record<string, unknown>>(`/novels/${novelId}/chapters/${chapterId}/storyboard/assets`),

  generatePanels: (novelId: string, chapterId: string, data: StoryboardGeneratePanelsRequest = {}) =>
    api.post<Record<string, unknown>>(`/novels/${novelId}/chapters/${chapterId}/storyboard/generate-panels`, data),

  normalizePanels: (novelId: string, chapterId: string, data: StoryboardNormalizeRequest) =>
    api.post<Record<string, unknown>>(`/novels/${novelId}/chapters/${chapterId}/storyboard/normalize-panels`, data),

  convertPanelsToShots: (novelId: string, chapterId: string, data: StoryboardConvertRequest) =>
    api.post<Record<string, unknown>>(`/novels/${novelId}/chapters/${chapterId}/storyboard/convert-panels-to-shots`, data),
};
