import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, Clock, MessageSquare, Save, Loader2, Play, Trash2, Sparkles, MapPin, Film, Package, Scissors, ListTree, Users } from 'lucide-react';
import { useTranslation } from '../../stores/i18nStore';
import type { Chapter, Novel } from '../../types';
import type { Shot } from '../../api/shots';
import type { ParseResultData } from './types';
import { useChapterDetailState } from './hooks/useChapterDetailState';
import { ImagePreviewModal } from './components/ImagePreviewModal';
import { getStatusInfo } from './utils/getStatusInfo';

type PanelRecord = Record<string, any>;

function valueToText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(valueToText).filter(Boolean).join(', ');
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return (
      valueToText(record.name) ||
      valueToText(record.id) ||
      valueToText(record.label) ||
      valueToText(record.text) ||
      valueToText(record.dialogue) ||
      valueToText(record.line) ||
      valueToText(record.description)
    );
  }
  return '';
}

function valueToList(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map(valueToText).filter(Boolean)));
  }
  const text = valueToText(value);
  return text ? [text] : [];
}

function firstPanelText(panel: PanelRecord, keys: string[]): string {
  for (const key of keys) {
    const text = valueToText(panel[key]);
    if (text) return text;
  }
  return '';
}

function panelDialogues(panel: PanelRecord): string[] {
  const source = panel.dialogues || panel.dialogue || panel.lines || panel.narration;
  if (!source) return [];
  if (!Array.isArray(source)) return valueToList(source);

  return source
    .map((entry) => {
      if (typeof entry === 'string') return entry.trim();
      if (!entry || typeof entry !== 'object') return '';
      const speaker = valueToText(entry.speaker || entry.character || entry.name);
      const text = valueToText(entry.text || entry.line || entry.dialogue || entry.content);
      return [speaker, text].filter(Boolean).join(': ');
    })
    .filter(Boolean);
}

function PanelChips({ items, className }: { items: string[]; className: string }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.slice(0, 8).map((item) => (
        <span key={item} className={`rounded-full px-2 py-1 text-xs ${className}`}>
          {item}
        </span>
      ))}
      {items.length > 8 && <span className="rounded-full bg-gray-100 px-2 py-1 text-xs text-gray-500">+{items.length - 8}</span>}
    </div>
  );
}

function ParseResultCard({ result, type, onViewClick }: { result: ParseResultData; type: 'characters' | 'scenes' | 'props'; onViewClick: () => void }) {
  const { t } = useTranslation();
  const isCharacter = type === 'characters';
  const isScene = type === 'scenes';
  const isProp = type === 'props';

  let bgClass = 'bg-purple-50 border-purple-200';
  let iconBgClass = 'bg-purple-100';
  let iconClass = 'text-purple-600';
  let textClass = 'text-purple-800';
  let subTextClass = 'text-purple-600';
  let btnClass = 'bg-purple-600 hover:bg-purple-700';
  let Icon = Sparkles;
  let labelKey = 'chapterDetail.parseComplete';
  let btnKey = 'chapterDetail.viewCharacters';

  if (isScene) {
    bgClass = 'bg-teal-50 border-teal-200';
    iconBgClass = 'bg-teal-100';
    iconClass = 'text-teal-600';
    textClass = 'text-teal-800';
    subTextClass = 'text-teal-600';
    btnClass = 'bg-teal-600 hover:bg-teal-700';
    Icon = MapPin;
    labelKey = 'chapterDetail.parseScenesComplete';
    btnKey = 'chapterDetail.viewScenes';
  } else if (isProp) {
    bgClass = 'bg-amber-50 border-amber-200';
    iconBgClass = 'bg-amber-100';
    iconClass = 'text-amber-600';
    textClass = 'text-amber-800';
    subTextClass = 'text-amber-600';
    btnClass = 'bg-amber-600 hover:bg-amber-700';
    Icon = Package;
    labelKey = 'chapterDetail.parsePropsComplete';
    btnKey = 'chapterDetail.viewProps';
  }

  return (
    <div className={`card ${bgClass}`}>
      <div className="flex items-center gap-3">
        <div className={`p-2 ${iconBgClass} rounded-full`}><Icon className={`h-5 w-5 ${iconClass}`} /></div>
        <div>
          <p className={`font-medium ${textClass}`}>{t(labelKey)}</p>
          <p className={`text-sm ${subTextClass}`}>{t('chapterDetail.parseResult', { created: result.created, updated: result.updated })}</p>
        </div>
        <button onClick={onViewClick} className={`ml-auto btn-primary ${btnClass} text-sm`}>
          {t(btnKey)}
        </button>
      </div>
    </div>
  );
}

function GeneratedAssets({ chapter, onImageClick }: { chapter: Chapter; onImageClick: (url: string, idx: number, imgs: string[]) => void }) {
  const { t } = useTranslation();
  const hasAssets = chapter.characterImages?.length || chapter.shotImages?.length || chapter.shotVideos?.length;
  if (!hasAssets) return null;

  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('chapterDetail.generatedResources')}</h3>
      {chapter.characterImages?.length ? (
        <div className="mb-6">
          <h4 className="text-sm font-medium text-gray-700 mb-2">{t('chapterDetail.characterImages')}</h4>
          <div className="grid grid-cols-4 gap-4">
            {chapter.characterImages.map((img, idx) => (
              <img key={idx} src={img} alt={t('chapterDetail.characterImageAlt', { index: idx + 1 })}
                className="rounded-lg cursor-pointer hover:opacity-90 transition-opacity" onClick={() => onImageClick(img, idx, chapter.characterImages || [])} />
            ))}
          </div>
        </div>
      ) : null}
      {chapter.shotImages?.length ? (
        <div className="mb-6">
          <h4 className="text-sm font-medium text-gray-700 mb-2">{t('chapterDetail.shotImages')}</h4>
          <div className="grid grid-cols-4 gap-4">
            {chapter.shotImages.map((img, idx) => (
              <img key={idx} src={img} alt={t('chapterDetail.shotImageAlt', { index: idx + 1 })}
                className="rounded-lg cursor-pointer hover:opacity-90 transition-opacity" onClick={() => onImageClick(img, idx, chapter.shotImages || [])} />
            ))}
          </div>
        </div>
      ) : null}
      {chapter.shotVideos?.length ? (
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-2">{t('chapterDetail.shotVideos')}</h4>
          <div className="grid grid-cols-2 gap-4">
            {chapter.shotVideos.map((video, idx) => <video key={idx} src={video} controls className="rounded-lg" />)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SegmentLibrary({ data }: { data: any }) {
  const raw = Array.isArray(data?.raw) ? data.raw : [];
  const enriched = Array.isArray(data?.enriched) ? data.enriched : [];
  const display = enriched.length ? enriched : raw;

  if (!display.length) {
    return (
      <div className="card border-dashed border-gray-300">
        <div className="flex items-center gap-3">
          <ListTree className="h-5 w-5 text-gray-400" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Segments</h3>
            <p className="text-sm text-gray-500">No stored story segments yet.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <ListTree className="h-5 w-5 text-indigo-600" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Segments</h3>
            <p className="text-sm text-gray-500">
              {raw.length} raw / {enriched.length} enriched
            </p>
          </div>
        </div>
        {data?.updated_at && <span className="text-xs text-gray-400">{data.updated_at}</span>}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 max-h-96 overflow-y-auto pr-1">
        {display.map((segment: any, index: number) => (
          <div key={segment.id || index} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-semibold text-indigo-600">{segment.id || `segment-${index + 1}`}</span>
              {segment.narrative_mode && <span className="text-xs text-gray-500">{segment.narrative_mode}</span>}
            </div>
            {segment.key_visual && <p className="mt-2 text-sm text-gray-800">{segment.key_visual}</p>}
            <p className="mt-2 text-xs leading-5 text-gray-600 line-clamp-4">{segment.text_vi}</p>
            {segment.location?.name && (
              <p className="mt-2 text-xs text-gray-500">
                Location: {segment.location.name}
              </p>
            )}
            {(segment.suggested_beat_count || segment.suggested_panel_count) && (
              <p className="mt-1 text-xs text-gray-500">
                Beats: {segment.suggested_beat_count || '-'} / Panels: {segment.suggested_panel_count || '-'}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function StoryboardPanels({ data }: { data: any }) {
  const rawPanels = Array.isArray(data?.panels) ? data.panels : [];
  const normalizedPanels = Array.isArray(data?.normalized_panels) ? data.normalized_panels : [];
  const panels: PanelRecord[] = normalizedPanels.length ? normalizedPanels : rawPanels;
  const warnings: string[] = Array.isArray(data?.panel_generation_warnings) ? data.panel_generation_warnings.map(valueToText).filter(Boolean) : [];
  const sourceLabel = normalizedPanels.length ? 'Normalized panels' : rawPanels.length ? 'Raw panels' : 'No panels';

  if (!panels.length) {
    return (
      <div className="card border-dashed border-gray-300">
        <div className="flex items-center gap-3">
          <Film className="h-5 w-5 text-gray-400" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Storyboard Panels</h3>
            <p className="text-sm text-gray-500">No storyboard panels yet. Run Generate Panels to preview the visual plan here.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-3">
          <Film className="h-5 w-5 text-emerald-600" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Storyboard Panels</h3>
            <p className="text-sm text-gray-500">
              {sourceLabel}: {panels.length}
              {data?.panel_source_segment_count ? ` / Segments: ${data.panel_source_segment_count}` : ''}
              {data?.converted_shot_count ? ` / Shots: ${data.converted_shot_count}` : ''}
            </p>
          </div>
        </div>
        {data?.panel_generation_mode && (
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            {valueToText(data.panel_generation_mode).replace(/_/g, ' ')}
          </span>
        )}
      </div>

      {warnings.length ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />
            <div>
              <p className="text-sm font-medium text-amber-800">Panel warnings</p>
              <ul className="mt-1 space-y-1 text-xs text-amber-700">
                {warnings.slice(0, 4).map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}
              </ul>
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {panels.map((panel, index) => {
          const panelId = valueToText(panel.panel_id || panel.id || panel.shot_id) || `panel_${index + 1}`;
          const segmentId = valueToText(panel.segment_id || panel.segmentId || panel.segment);
          const description = firstPanelText(panel, ['description', 'beat_summary', 'action', 'visual_description', 'image_description']);
          const motion = firstPanelText(panel, ['video_description', 'motion', 'camera_motion', 'shot_motion']);
          const imagePrompt = firstPanelText(panel, ['image_prompt', 'prompt']);
          const duration = valueToText(panel.duration_seconds || panel.duration || panel.estimated_duration_seconds);
          const sceneItems = valueToList(panel.scene || panel.scene_id || panel.scene_name || panel.location);
          const characterItems = valueToList(panel.characters || panel.character_ids || panel.appearing_characters);
          const propItems = valueToList(panel.props || panel.prop_ids || panel.set_dressing || panel.objects);
          const dialogues = panelDialogues(panel);
          const panelWarnings = valueToList(panel.warnings || panel.warning);

          return (
            <div key={`${panelId}-${index}`} className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-100 bg-gray-50 px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{panelId}</p>
                    {segmentId && <p className="mt-0.5 text-xs text-indigo-600">Segment: {segmentId}</p>}
                  </div>
                  <span className="rounded-full bg-gray-900 px-2 py-1 text-xs font-semibold text-white">#{index + 1}</span>
                </div>
              </div>

              <div className="space-y-4 p-4">
                <div className="min-h-24 rounded-lg border border-gray-100 bg-gradient-to-br from-slate-50 to-white p-4">
                  {sceneItems.length ? (
                    <div className="mb-3 flex items-start gap-2 text-sm font-medium text-teal-700">
                      <MapPin className="mt-0.5 h-4 w-4 shrink-0" />
                      <span>{sceneItems.join(', ')}</span>
                    </div>
                  ) : null}
                  <p className="text-sm leading-6 text-gray-800">
                    {description || imagePrompt || 'No visual description stored for this panel.'}
                  </p>
                  {motion && <p className="mt-3 text-xs leading-5 text-gray-500">Motion: {motion}</p>}
                </div>

                {duration && (
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <Clock className="h-4 w-4" />
                    <span>{duration}s</span>
                  </div>
                )}

                {characterItems.length ? (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500">
                      <Users className="h-4 w-4" /> Characters
                    </div>
                    <PanelChips items={characterItems} className="bg-blue-50 text-blue-700" />
                  </div>
                ) : null}

                {propItems.length ? (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500">
                      <Package className="h-4 w-4" /> Props
                    </div>
                    <PanelChips items={propItems} className="bg-amber-50 text-amber-700" />
                  </div>
                ) : null}

                {dialogues.length ? (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500">
                      <MessageSquare className="h-4 w-4" /> Dialogue
                    </div>
                    <div className="space-y-1 rounded-lg bg-gray-50 p-3">
                      {dialogues.slice(0, 4).map((line, lineIndex) => (
                        <p key={`${panelId}-dialogue-${lineIndex}`} className="text-xs leading-5 text-gray-700">{line}</p>
                      ))}
                    </div>
                  </div>
                ) : null}

                {panelWarnings.length ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
                    {panelWarnings.join(' | ')}
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CompactStoryboardPanels({ data }: { data: any }) {
  const rawPanels = Array.isArray(data?.panels) ? data.panels : [];
  const normalizedPanels = Array.isArray(data?.normalized_panels) ? data.normalized_panels : [];
  const panels: PanelRecord[] = normalizedPanels.length ? normalizedPanels : rawPanels;
  const warnings: string[] = Array.isArray(data?.panel_generation_warnings) ? data.panel_generation_warnings.map(valueToText).filter(Boolean) : [];
  const sourceLabel = normalizedPanels.length ? 'Normalized panels' : rawPanels.length ? 'Raw panels' : 'No panels';

  if (!panels.length) {
    return (
      <div className="card border-dashed border-gray-300">
        <div className="flex items-center gap-3">
          <Film className="h-5 w-5 text-gray-400" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Storyboard Panels</h3>
            <p className="text-sm text-gray-500">No storyboard panels yet. Run Generate Panels to preview the visual plan here.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-3">
          <Film className="h-5 w-5 text-emerald-600" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Storyboard Panels</h3>
            <p className="text-sm text-gray-500">
              {sourceLabel}: {panels.length}
              {data?.panel_source_segment_count ? ` / Segments: ${data.panel_source_segment_count}` : ''}
              {data?.converted_shot_count ? ` / Shots: ${data.converted_shot_count}` : ''}
            </p>
          </div>
        </div>
        {data?.panel_generation_mode && (
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            {valueToText(data.panel_generation_mode).replace(/_/g, ' ')}
          </span>
        )}
      </div>

      {warnings.length ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />
            <div>
              <p className="text-sm font-medium text-amber-800">Panel warnings</p>
              <p className="mt-1 text-xs text-amber-700">{warnings.slice(0, 3).join(' | ')}</p>
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {panels.map((panel, index) => {
          const panelId = valueToText(panel.panel_id || panel.id || panel.shot_id) || `panel_${index + 1}`;
          const segmentId = valueToText(panel.segment_id || panel.segmentId || panel.segment);
          const description = firstPanelText(panel, ['description', 'beat_summary', 'action', 'visual_description', 'image_description']);
          const motion = firstPanelText(panel, ['video_description', 'motion', 'camera_motion', 'shot_motion']);
          const imagePrompt = firstPanelText(panel, ['image_prompt', 'prompt']);
          const duration = valueToText(panel.duration_seconds || panel.duration || panel.estimated_duration_seconds);
          const sceneItems = valueToList(panel.scene || panel.scene_id || panel.scene_name || panel.location);
          const characterItems = valueToList(panel.characters || panel.character_ids || panel.appearing_characters);
          const propItems = valueToList(panel.props || panel.prop_ids || panel.set_dressing || panel.objects);
          const dialogues = panelDialogues(panel);
          const panelWarnings = valueToList(panel.warnings || panel.warning);

          return (
            <details key={`${panelId}-${index}`} className="group overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
              <summary className="cursor-pointer list-none p-4 hover:bg-gray-50">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-gray-900">{panelId}</p>
                    {segmentId && <p className="mt-0.5 truncate text-xs text-indigo-600">Segment: {segmentId}</p>}
                  </div>
                  <span className="rounded-full bg-gray-900 px-2 py-1 text-xs font-semibold text-white">#{index + 1}</span>
                </div>
                {sceneItems.length ? <p className="mt-3 truncate text-xs font-medium text-teal-700">{sceneItems.join(', ')}</p> : null}
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">
                  {description || imagePrompt || 'No visual description stored for this panel.'}
                </p>
                <p className="mt-3 text-xs font-medium text-gray-400 group-open:hidden">Click to view details</p>
              </summary>

              <div className="space-y-4 border-t border-gray-100 p-4">
                <div className="rounded-lg border border-gray-100 bg-gradient-to-br from-slate-50 to-white p-4">
                  {sceneItems.length ? (
                    <div className="mb-3 flex items-start gap-2 text-sm font-medium text-teal-700">
                      <MapPin className="mt-0.5 h-4 w-4 shrink-0" />
                      <span>{sceneItems.join(', ')}</span>
                    </div>
                  ) : null}
                  <p className="text-sm leading-6 text-gray-800">
                    {description || imagePrompt || 'No visual description stored for this panel.'}
                  </p>
                  {motion && <p className="mt-3 text-xs leading-5 text-gray-500">Motion: {motion}</p>}
                </div>

                {duration && (
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <Clock className="h-4 w-4" />
                    <span>{duration}s</span>
                  </div>
                )}
                <PanelChips items={characterItems} className="bg-blue-50 text-blue-700" />
                <PanelChips items={propItems} className="bg-amber-50 text-amber-700" />
                {dialogues.length ? (
                  <div className="space-y-1 rounded-lg bg-gray-50 p-3">
                    {dialogues.slice(0, 4).map((line, lineIndex) => (
                      <p key={`${panelId}-dialogue-${lineIndex}`} className="text-xs leading-5 text-gray-700">{line}</p>
                    ))}
                  </div>
                ) : null}
                {panelWarnings.length ? <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">{panelWarnings.join(' | ')}</div> : null}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

function CreatedShots({ shots }: { shots: Shot[] }) {
  if (!shots.length) {
    return (
      <div className="card border-dashed border-gray-300">
        <div className="flex items-center gap-3">
          <Play className="h-5 w-5 text-gray-400" />
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Created Shots</h3>
            <p className="text-sm text-gray-500">No Shot rows yet. Run Create Shots after panels are generated.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="mb-5 flex items-center gap-3">
        <Play className="h-5 w-5 text-gray-700" />
        <div>
          <h3 className="text-lg font-semibold text-gray-900">Created Shots</h3>
          <p className="text-sm text-gray-500">{shots.length} database shots ready for image, audio, and video generation.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {shots.map((shot) => {
          const dialogues = panelDialogues(shot as unknown as PanelRecord);
          const status = [
            shot.imageStatus ? `Image: ${shot.imageStatus}` : '',
            shot.videoStatus ? `Video: ${shot.videoStatus}` : '',
          ].filter(Boolean).join(' / ');

          return (
            <details key={shot.id} className="group overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
              <summary className="cursor-pointer list-none p-4 hover:bg-gray-50">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-gray-900">Shot #{shot.index}</p>
                    {shot.scene && <p className="mt-0.5 truncate text-xs text-teal-700">{shot.scene}</p>}
                  </div>
                  <span className="rounded-full bg-gray-900 px-2 py-1 text-xs font-semibold text-white">{shot.duration || 4}s</span>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">{shot.description || 'No shot description.'}</p>
                {status && <p className="mt-2 text-xs text-gray-400">{status}</p>}
                <p className="mt-3 text-xs font-medium text-gray-400 group-open:hidden">Click to view details</p>
              </summary>

              <div className="space-y-4 border-t border-gray-100 p-4">
                <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                  <p className="text-sm leading-6 text-gray-800">{shot.description || 'No shot description.'}</p>
                  {shot.video_description && <p className="mt-3 text-xs leading-5 text-gray-500">Video: {shot.video_description}</p>}
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <Clock className="h-4 w-4" />
                  <span>{shot.duration || 4}s</span>
                </div>
                <PanelChips items={shot.characters || []} className="bg-blue-50 text-blue-700" />
                <PanelChips items={shot.props || []} className="bg-amber-50 text-amber-700" />
                {dialogues.length ? (
                  <div className="space-y-1 rounded-lg bg-gray-50 p-3">
                    {dialogues.slice(0, 5).map((line, index) => (
                      <p key={`${shot.id}-dialogue-${index}`} className="text-xs leading-5 text-gray-700">{line}</p>
                    ))}
                  </div>
                ) : null}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

export default function ChapterDetail() {
  const { t } = useTranslation();
  const state = useChapterDetailState();

  if (state.isLoading) return <div className="flex justify-center items-center h-64"><Loader2 className="h-8 w-8 animate-spin text-primary-600" /></div>;
  if (!state.chapter || !state.novel) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500">{t('chapterDetail.chapterNotExist')}</p>
        <Link to={`/novels/${state.id}`} className="text-primary-600 hover:underline mt-2 inline-block">{t('chapterDetail.backToNovel')}</Link>
      </div>
    );
  }

  const statusInfo = getStatusInfo(state.chapter.status, t);
  const StatusIcon = statusInfo.icon;
  const shouldSpin = !['completed', 'failed', 'pending'].includes(state.chapter.status);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to={`/novels/${state.id}`} className="p-2 text-gray-400 hover:text-gray-600 transition-colors"><ArrowLeft className="h-5 w-5" /></Link>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{t('chapterDetail.chapterTitle', { number: state.chapter.number, title: state.chapter.title })}</h1>
            <p className="text-sm text-gray-500">{state.novel.title}</p>
          </div>
        </div>
        <div className="flex gap-3">
          <button onClick={state.handleDelete} className="btn-secondary text-red-600 hover:text-red-700 border-red-200 hover:border-red-300">
            <Trash2 className="h-4 w-4 mr-2" />{t('common.delete')}
          </button>
          <button onClick={state.handleSave} disabled={state.isSaving} className="btn-primary">
            {state.isSaving ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Save className="h-4 w-4 mr-2" />}{t('common.save')}
          </button>
          <button onClick={state.handleParseCharacters} className="btn-secondary text-purple-600 border-purple-200 hover:bg-purple-50 disabled:opacity-50">
            {state.parsingChapter ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Sparkles className="h-4 w-4 mr-2" />}{t('chapterDetail.parseCharacters')}
          </button>
          <button onClick={state.handleParseScenes} className="btn-secondary text-teal-600 border-teal-200 hover:bg-teal-50 disabled:opacity-50" disabled={state.parsingScenes}>
            {state.parsingScenes ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <MapPin className="h-4 w-4 mr-2" />}{t('chapterDetail.parseScenes')}
          </button>
          <button onClick={state.handleParseProps} className="btn-secondary text-amber-600 border-amber-200 hover:bg-amber-50 disabled:opacity-50" disabled={state.parsingProps}>
            {state.parsingProps ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Package className="h-4 w-4 mr-2" />}{t('chapterDetail.parseProps')}
          </button>
          <button onClick={state.handleGenerate} className="btn-primary bg-green-600 hover:bg-green-700" disabled={state.chapter.status !== 'pending' && state.chapter.status !== 'failed'}>
            <Play className="h-4 w-4 mr-2" />{t('chapterDetail.generateVideo')}
          </button>
        </div>
      </div>

      {/* Status Bar */}
      <div className={`card ${statusInfo.bg}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusIcon className={`h-6 w-6 ${statusInfo.color} ${shouldSpin ? 'animate-spin' : ''}`} />
            <div>
              <p className={`font-medium ${statusInfo.color}`}>{statusInfo.text}</p>
              {state.chapter.progress > 0 && <p className="text-sm text-gray-500">{t('chapterDetail.progress', { progress: state.chapter.progress })}</p>}
            </div>
          </div>
          {state.chapter.status === 'completed' && state.chapter.finalVideo && (
            <a href={state.chapter.finalVideo} target="_blank" rel="noopener noreferrer" className="btn-primary">
              <Film className="h-4 w-4 mr-2" />{t('chapterDetail.viewVideo')}
            </a>
          )}
        </div>
      </div>

      {/* Story Pipeline */}
      <div className="card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Story Pipeline</h3>
            <p className="text-sm text-gray-500">
              Split the chapter into visual segments, enrich them, then extract the canonical asset library.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button
              onClick={state.handleSplitSegments}
              disabled={state.splittingSegments}
              className="btn-secondary text-sky-600 border-sky-200 hover:bg-sky-50 disabled:opacity-50"
            >
              {state.splittingSegments ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Scissors className="h-4 w-4 mr-2" />}
              Split Segments
            </button>
            <button
              onClick={state.handleEnrichSegments}
              disabled={state.enrichingSegments}
              className="btn-secondary text-indigo-600 border-indigo-200 hover:bg-indigo-50 disabled:opacity-50"
            >
              {state.enrichingSegments ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <ListTree className="h-4 w-4 mr-2" />}
              Enrich Segments
            </button>
            <button
              onClick={state.handleExtractVisualAssets}
              disabled={state.extractingAssets}
              className="btn-secondary text-purple-600 border-purple-200 hover:bg-purple-50 disabled:opacity-50"
            >
              {state.extractingAssets ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Sparkles className="h-4 w-4 mr-2" />}
              Extract Assets
            </button>
            <button
              onClick={() => state.handleGeneratePanels(false)}
              disabled={state.generatingPanels || state.creatingShots}
              className="btn-secondary text-emerald-600 border-emerald-200 hover:bg-emerald-50 disabled:opacity-50"
            >
              {state.generatingPanels ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Film className="h-4 w-4 mr-2" />}
              Generate Panels
            </button>
            <button
              onClick={() => state.handleGeneratePanels(true)}
              disabled={state.generatingPanels || state.creatingShots}
              className="btn-secondary text-gray-700 border-gray-200 hover:bg-gray-50 disabled:opacity-50"
            >
              {state.creatingShots ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Play className="h-4 w-4 mr-2" />}
              Create Shots
            </button>
          </div>
        </div>
        {(state.storyboardData?.normalized_panels?.length || state.storyboardData?.panels?.length) ? (
          <p className="mt-3 text-xs text-gray-500">
            Stored panels: {state.storyboardData.normalized_panels?.length || state.storyboardData.panels?.length || 0}
            {state.storyboardData.converted_shot_count ? ` / Shots: ${state.storyboardData.converted_shot_count}` : ''}
          </p>
        ) : null}
      </div>

      {/* Parse Results */}
      {state.parseResult && <ParseResultCard result={state.parseResult} type="characters" onViewClick={() => window.location.href = `/characters?novel=${state.id}&highlight=new`} />}
      {state.parseScenesResult && <ParseResultCard result={state.parseScenesResult} type="scenes" onViewClick={() => window.location.href = `/scenes?novel=${state.id}&highlight=new`} />}
      {state.parsePropsResult && <ParseResultCard result={state.parsePropsResult} type="props" onViewClick={() => window.location.href = `/props?novel=${state.id}&highlight=new`} />}

      <SegmentLibrary data={state.segmentData} />
      <CompactStoryboardPanels data={state.storyboardData} />
      <CreatedShots shots={state.shots} />

      {/* Content Editor */}
      <div className="card">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">{t('chapterDetail.chapterTitleLabel')}</label>
            <input type="text" value={state.title} onChange={(e) => state.setTitle(e.target.value)} className="input-field" placeholder={t('chapterDetail.chapterTitlePlaceholder')} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">{t('chapterDetail.chapterContentLabel')}</label>
            <textarea value={state.content} onChange={(e) => state.setContent(e.target.value)} rows={20} className="input-field font-mono text-sm" placeholder={t('chapterDetail.chapterContentPlaceholder')} />
            <p className="text-xs text-gray-500 mt-2">{t('chapterDetail.wordCount', { count: state.content.length })}</p>
          </div>
        </div>
      </div>

      <GeneratedAssets chapter={state.chapter} onImageClick={state.openImagePreview} />
      <ImagePreviewModal previewImage={state.previewImage} onClose={state.closeImagePreview} onNavigate={state.navigatePreview} />
    </div>
  );
}
