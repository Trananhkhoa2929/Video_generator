import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { toast } from '../../../stores/toastStore';
import { useTranslation } from '../../../stores/i18nStore';
import { novelApi } from '../../../api/novels';
import { chapterApi, type ParseResult, type SegmentData } from '../../../api/chapters';
import { shotsApi, type Shot } from '../../../api/shots';
import { propApi } from '../../../api/props';
import type { Chapter, Novel } from '../../../types';
import type { ParseResultData, PreviewImageState } from '../types';

export function useChapterDetailState() {
  const { t } = useTranslation();
  const { id, cid } = useParams<{ id: string; cid: string }>();
  const navigate = useNavigate();

  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [novel, setNovel] = useState<Novel | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [content, setContent] = useState('');
  const [title, setTitle] = useState('');
  const [previewImage, setPreviewImage] = useState<PreviewImageState>({ isOpen: false, url: null, index: 0, images: [] });
  const [parsingChapter, setParsingChapter] = useState(false);
  const [parsingScenes, setParsingScenes] = useState(false);
  const [parsingProps, setParsingProps] = useState(false);
  const [splittingSegments, setSplittingSegments] = useState(false);
  const [enrichingSegments, setEnrichingSegments] = useState(false);
  const [generatingPanels, setGeneratingPanels] = useState(false);
  const [creatingShots, setCreatingShots] = useState(false);
  const [extractingAssets, setExtractingAssets] = useState(false);
  const [segmentData, setSegmentData] = useState<SegmentData | null>(null);
  const [storyboardData, setStoryboardData] = useState<any>(null);
  const [shots, setShots] = useState<Shot[]>([]);
  const [parseResult, setParseResult] = useState<ParseResultData | null>(null);
  const [parseScenesResult, setParseScenesResult] = useState<ParseResultData | null>(null);
  const [parsePropsResult, setParsePropsResult] = useState<ParseResultData | null>(null);

  useEffect(() => { if (id && cid) fetchData(); }, [id, cid]);

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const novelData = await novelApi.fetch(id!);
      if (novelData.success && novelData.data) setNovel(novelData.data);
      const chapterData = await chapterApi.fetch(id!, cid!);
      if (chapterData.success && chapterData.data) {
        setChapter(chapterData.data);
        setTitle(chapterData.data.title);
        setContent(chapterData.data.content || '');
        const parsedData = parseChapterData(chapterData.data.parsedData);
        setSegmentData(parsedData?.segments || null);
        setStoryboardData(parsedData?.storyboard || null);
      }

      try {
        const shotData = await shotsApi.getShots(id!, cid!);
        setShots(shotData.success ? shotData.data || [] : []);
      } catch (error) {
        console.error('Fetch shots failed:', error);
        setShots([]);
      }
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const data = await chapterApi.update(id!, cid!, { title, content });
      if (data.success && data.data) { setChapter(data.data); toast.success(t('common.saveSuccess')); }
    } catch (error) {
      console.error('保存失败:', error);
      toast.error(t('common.saveFailed'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('chapterDetail.confirmDelete'))) return;
    try {
      await chapterApi.delete(id!, cid!);
      navigate(`/novels/${id}`);
    } catch (error) {
      console.error('删除失败:', error);
      toast.error(t('chapterDetail.deleteFailed'));
    }
  };

  const handleGenerate = () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.pleaseEditContent')); return; }
    navigate(`/novels/${id}/chapters/${cid}/generate`);
  };

  const handleParseCharacters = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingChapter(true);
    setParseResult(null);
    try {
      const data = await chapterApi.parseCharacters(id!, cid!);
      if (data.success) {
        // statistics 在返回对象的根级别，不是在 data 里
        const stats: ParseResult = (data as any).statistics || data.data?.statistics || { created: 0, updated: 0, total: 0 };
        setParseResult({ created: stats.created || 0, updated: stats.updated || 0, total: stats.total || 0 });
        if (stats.created > 0) toast.success(t('chapterDetail.parseResult', { created: stats.created, updated: stats.updated }));
        else toast.info(t('chapterDetail.noNewCharacters'));
      } else {
        toast.error(t('chapterDetail.parseFailed') + ': ' + data.message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parseFailed') + ':', error);
      toast.error(t('chapterDetail.parseFailed'));
    } finally {
      setParsingChapter(false);
    }
  };

  const handleParseScenes = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingScenes(true);
    setParseScenesResult(null);
    try {
      const data = await chapterApi.parseScenes(id!, cid!);
      if (data.success) {
        // statistics 在返回对象的根级别，不是在 data 里
        const stats: ParseResult = (data as any).statistics || data.data?.statistics || { created: 0, updated: 0, total: 0 };
        setParseScenesResult({ created: stats.created || 0, updated: stats.updated || 0, total: stats.total || 0 });
        if (stats.created > 0 || stats.updated > 0) toast.success(t('chapterDetail.parseScenesResult', { created: stats.created || 0, updated: stats.updated || 0 }));
        else toast.info(t('chapterDetail.noNewScenes'));
      } else {
        toast.error(t('chapterDetail.parseScenesFailed') + ': ' + data.message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parseScenesFailed') + ':', error);
      toast.error(t('chapterDetail.parseScenesFailed'));
    } finally {
      setParsingScenes(false);
    }
  };

  const handleParseProps = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingProps(true);
    setParsePropsResult(null);
    try {
      const data = await propApi.parseChapterProps(id!, cid!, true);
      if (data.success) {
        // statistics 在返回对象的根级别，不是在 data 里
        const stats = (data as any)?.statistics || (data.data as any)?.statistics || { created: 0, updated: 0 };
        setParsePropsResult({ created: stats.created || 0, updated: stats.updated || 0, total: stats.total || 0 });
        if ((stats.created ?? 0) > 0 || (stats.updated ?? 0) > 0) {
          toast.success(t('chapterDetail.parsePropsResult', { created: stats.created || 0, updated: stats.updated || 0 }));
        } else {
          toast.info(t('chapterDetail.noNewProps'));
        }
      } else {
        toast.error(t('chapterDetail.parsePropsFailed') + ': ' + data.message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parsePropsFailed') + ':', error);
      toast.error(t('chapterDetail.parsePropsFailed'));
    } finally {
      setParsingProps(false);
    }
  };

  const handleSplitSegments = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setSplittingSegments(true);
    try {
      const data = await chapterApi.splitSegments(id!, cid!, true);
      if (data.success) {
        setSegmentData(data.data || null);
        toast.success(`Segments created: ${data.data?.raw?.length || 0}`);
        await fetchData();
      } else {
        toast.error(`Segment split failed: ${data.message || 'unknown error'}`);
      }
    } catch (error) {
      console.error('Segment split failed:', error);
      toast.error('Segment split failed');
    } finally {
      setSplittingSegments(false);
    }
  };

  const handleEnrichSegments = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setEnrichingSegments(true);
    try {
      const data = await chapterApi.enrichSegments(id!, cid!, true);
      if (data.success) {
        setSegmentData(data.data || null);
        toast.success(`Segments enriched: ${data.data?.enriched?.length || 0}`);
        await fetchData();
      } else {
        toast.error(`Segment enrichment failed: ${data.message || 'unknown error'}`);
      }
    } catch (error) {
      console.error('Segment enrichment failed:', error);
      toast.error('Segment enrichment failed');
    } finally {
      setEnrichingSegments(false);
    }
  };

  const handleExtractVisualAssets = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setExtractingAssets(true);
    try {
      const data = await chapterApi.extractVisualAssets(id!, cid!);
      if (data.success) {
        const storage = (data.data as any)?.storage || {};
        const created = storage.created || {};
        const updated = storage.updated || {};
        const createdTotal =
          (created.characters?.length || 0) +
          (created.scenes?.length || 0) +
          (created.props?.length || 0);
        const updatedTotal =
          (updated.characters?.length || 0) +
          (updated.scenes?.length || 0) +
          (updated.props?.length || 0);
        toast.success(`Assets extracted: ${createdTotal} created, ${updatedTotal} updated`);
        await fetchData();
      } else {
        toast.error(`Asset extraction failed: ${data.message || (data as any).error || 'unknown error'}`);
      }
    } catch (error) {
      console.error('Asset extraction failed:', error);
      toast.error('Asset extraction failed');
    } finally {
      setExtractingAssets(false);
    }
  };

  const handleGeneratePanels = async (createShots = false) => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }

    if (createShots) {
      setCreatingShots(true);
      try {
        const storedPanelCount =
          (Array.isArray(storyboardData?.normalized_panels) ? storyboardData.normalized_panels.length : 0) ||
          (Array.isArray(storyboardData?.panels) ? storyboardData.panels.length : 0);
        if (!storedPanelCount) {
          toast.warning('Generate panels before creating shots');
          return;
        }

        const data = await chapterApi.convertStoryboardPanelsToShots(id!, cid!, true);
        if (data.success && !(data.data as any)?.error) {
          const shotCount = (data.data as any)?.shots?.length || 0;
          toast.success(`Shots created: ${shotCount}`);
          await fetchData();
        } else {
          toast.error(`Shot creation failed: ${(data.data as any)?.error || data.message || (data as any).error || 'unknown error'}`);
        }
      } catch (error) {
        console.error('Shot creation failed:', error);
        toast.error('Shot creation failed');
      } finally {
        setCreatingShots(false);
      }
      return;
    }

    setGeneratingPanels(true);
    try {
      const data = await chapterApi.generateStoryboardPanels(id!, cid!, createShots);
      if (data.success) {
        const panelCount = (data.data as any)?.panelCount || (data.data as any)?.normalizedPanels?.length || 0;
        toast.success(`Panels generated: ${panelCount}`);
        await fetchData();
      } else {
        toast.error(`Panel generation failed: ${data.message || (data as any).error || 'unknown error'}`);
      }
    } catch (error) {
      console.error('Panel generation failed:', error);
      toast.error('Panel generation failed');
    } finally {
      setGeneratingPanels(false);
    }
  };

  const openImagePreview = useCallback((url: string, index: number, images: string[]) => {
    setPreviewImage({ isOpen: true, url, index, images });
  }, []);

  const closeImagePreview = useCallback(() => {
    setPreviewImage({ isOpen: false, url: null, index: 0, images: [] });
  }, []);

  const navigatePreview = useCallback((direction: 'prev' | 'next') => {
    if (!previewImage.images.length) return;
    const newIndex = direction === 'prev'
      ? (previewImage.index === 0 ? previewImage.images.length - 1 : previewImage.index - 1)
      : (previewImage.index === previewImage.images.length - 1 ? 0 : previewImage.index + 1);
    setPreviewImage({ ...previewImage, url: previewImage.images[newIndex], index: newIndex });
  }, [previewImage]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!previewImage.isOpen) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); navigatePreview('prev'); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); navigatePreview('next'); }
      else if (e.key === 'Escape') { e.preventDefault(); closeImagePreview(); }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [previewImage.isOpen, previewImage.index, previewImage.images, navigatePreview, closeImagePreview]);

  return {
    // State
    id, cid, chapter, novel, isLoading, isSaving, content, setContent, title, setTitle,
    previewImage, parsingChapter, parsingScenes, parsingProps, splittingSegments, enrichingSegments,
    generatingPanels, creatingShots, extractingAssets, segmentData, storyboardData, shots, parseResult, parseScenesResult, parsePropsResult,
    // Actions
    handleSave, handleDelete, handleGenerate, handleParseCharacters, handleParseScenes, handleParseProps,
    handleSplitSegments, handleEnrichSegments, handleExtractVisualAssets, handleGeneratePanels,
    openImagePreview, closeImagePreview, navigatePreview,
  };
}

function parseChapterData(value: unknown): any {
  if (!value) return null;
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return null;
    }
  }
  return value;
}
