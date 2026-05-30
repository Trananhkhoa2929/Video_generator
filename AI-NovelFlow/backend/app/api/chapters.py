"""
章节路由 - 章节 CRUD 和批量导入相关接口
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.novel import Chapter
from app.repositories import NovelRepository, ChapterRepository, CharacterRepository, SceneRepository, PropRepository
from app.api.deps import get_novel_repo, get_chapter_repo, get_character_repo, get_scene_repo, get_prop_repo
from app.schemas.storyboard import (
    SegmentEnrichRequest,
    SegmentSplitRequest,
    StoryboardConvertRequest,
    StoryboardGeneratePanelsRequest,
    StoryboardNormalizeRequest,
    VisualAssetExtractRequest,
)
from app.utils.time_utils import format_datetime
from app.utils.text_utils import detect_encoding, parse_chapters_from_text

router = APIRouter()


# ==================== 章节 CRUD ====================

@router.get("/{novel_id}/chapters", response_model=dict)
async def list_chapters(
    novel_id: str, 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """获取章节列表"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapters = chapter_repo.list_by_novel(novel_id)
    return {
        "success": True,
        "data": [chapter_repo.to_response(c) for c in chapters]
    }


@router.post("/{novel_id}/chapters", response_model=dict)
async def create_chapter(
    novel_id: str, 
    data: dict, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """创建章节"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapter = Chapter(
        novel_id=novel_id,
        number=data.get("number", 1),
        title=data["title"],
        content=data.get("content", ""),
    )
    db.add(chapter)
    
    # 更新章节数
    novel.chapter_count = chapter_repo.count_by_novel(novel_id) + 1
    
    db.commit()
    db.refresh(chapter)
    
    return {
        "success": True,
        "data": chapter_repo.to_response(chapter)
    }


@router.get("/{novel_id}/chapters/{chapter_id}", response_model=dict)
async def get_chapter(
    novel_id: str, 
    chapter_id: str, 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """获取章节详情"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    return {
        "success": True,
        "data": chapter_repo.to_detail_response(chapter)
    }


@router.put("/{novel_id}/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    novel_id: str, 
    chapter_id: str, 
    data: dict, 
    db: Session = Depends(get_db),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """更新章节"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if "title" in data:
        chapter.title = data["title"]
    if "content" in data:
        chapter.content = data["content"]
    if "parsedData" in data:
        chapter.parsed_data = data["parsedData"]
    
    db.commit()
    db.refresh(chapter)
    
    return {
        "success": True,
        "data": {
            **chapter_repo.to_response(chapter),
            "content": chapter.content,
            "parsedData": chapter.parsed_data,
            "updatedAt": format_datetime(chapter.updated_at),
        }
    }


@router.delete("/{novel_id}/chapters/{chapter_id}")
async def delete_chapter(
    novel_id: str, 
    chapter_id: str, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """删除章节"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    db.delete(chapter)
    
    # 更新小说章节数
    novel = novel_repo.get_by_id(novel_id)
    if novel:
        novel.chapter_count = chapter_repo.count_by_novel(novel_id) - 1
    
    db.commit()
    
    return {"success": True, "message": "删除成功"}


# ==================== 章节角色/场景解析 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/parse-characters/", response_model=dict)
async def parse_chapter_characters(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo)
):
    """解析单章节内容，提取角色信息（支持增量更新）"""
    from app.services.novel_service import NovelService
    
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}
    
    service = NovelService(db)
    return await service.parse_characters(
        novel_id=novel_id,
        chapters=[chapter],
        start_chapter=chapter.number,
        end_chapter=chapter.number,
        is_incremental=is_incremental,
        character_repo=character_repo
    )


@router.post("/{novel_id}/chapters/{chapter_id}/parse-scenes/", response_model=dict)
async def parse_chapter_scenes(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    scene_repo: SceneRepository = Depends(get_scene_repo)
):
    """解析单章节内容，提取场景信息（支持增量更新）"""
    from app.services.novel_service import NovelService

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}

    service = NovelService(db)
    return await service.parse_scenes(
        novel_id=novel_id,
        chapter=chapter,
        is_incremental=is_incremental,
        scene_repo=scene_repo
    )


@router.post("/{novel_id}/chapters/{chapter_id}/parse-props/", response_model=dict)
async def parse_chapter_props(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    prop_repo: PropRepository = Depends(get_prop_repo)
):
    """解析单章节内容，提取道具信息（支持增量更新）"""
    from app.services.novel_service import NovelService

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}

    service = NovelService(db)
    return await service.parse_props(
        novel_id=novel_id,
        chapters=[chapter],
        start_chapter=chapter.number,
        end_chapter=chapter.number,
        is_incremental=is_incremental,
        prop_repo=prop_repo
    )


# ==================== 章节拆分 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/split", response_model=dict)
async def split_chapter(
    novel_id: str, 
    chapter_id: str, 
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo),
    scene_repo: SceneRepository = Depends(get_scene_repo),
    prop_repo: PropRepository = Depends(get_prop_repo)
):
    """使用小说配置的拆分提示词将章节拆分为分镜"""
    from app.services.novel_service import NovelService
    
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    # 获取当前小说的所有角色、场景和道具列表
    character_names = character_repo.get_names_by_novel(novel_id)
    scene_names = scene_repo.get_names_by_novel(novel_id)
    prop_names = prop_repo.get_names_by_novel(novel_id)
    
    service = NovelService(db)
    return await service.split_chapter(
        novel=novel,
        chapter=chapter,
        character_names=character_names,
        scene_names=scene_names,
        prop_names=prop_names
    )


# ==================== storyboard segmentation and asset mapping ====================

@router.get("/{novel_id}/chapters/{chapter_id}/segments", response_model=dict)
async def get_chapter_segments(
    novel_id: str,
    chapter_id: str,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Return stored visual/cinematic segments for a chapter."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    from app.services.segment_service import SegmentService

    return {
        "success": True,
        "data": SegmentService(db).get_segment_data(chapter),
    }


@router.post("/{novel_id}/chapters/{chapter_id}/segments/split", response_model=dict)
async def split_chapter_segments(
    novel_id: str,
    chapter_id: str,
    request: SegmentSplitRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Split raw chapter text into reference-style visual/cinematic segments."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    from app.services.segment_service import SegmentService

    return await SegmentService(db).split_chapter(chapter, overwrite=request.overwrite)


@router.post("/{novel_id}/chapters/{chapter_id}/segments/enrich", response_model=dict)
async def enrich_chapter_segments(
    novel_id: str,
    chapter_id: str,
    request: SegmentEnrichRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Enrich stored segments with beat, panel, emotion, location, and transition metadata."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    from app.services.segment_service import SegmentService

    return await SegmentService(db).enrich_segments(chapter, overwrite=request.overwrite)

@router.post("/{novel_id}/chapters/{chapter_id}/storyboard/extract-assets", response_model=dict)
async def extract_storyboard_assets(
    novel_id: str,
    chapter_id: str,
    request: VisualAssetExtractRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """
    Extract a richer production asset library from chapter text.

    This is the recommended preflight before split/shot generation when the
    default character/scene/prop parsers under-detect visual assets.
    """
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not chapter.content:
        return {"success": False, "message": "Chapter content is empty"}

    from app.services.visual_asset_service import VisualAssetService

    service = VisualAssetService(db)
    return await service.extract_chapter_assets(
        chapter=chapter,
        store=request.store,
        update_existing=request.update_existing,
        max_characters=request.max_characters,
        max_scenes=request.max_scenes,
        max_props=request.max_props,
        ensure_segments=request.ensure_segments,
        enrich_segments=request.enrich_segments,
    )


@router.get("/{novel_id}/chapters/{chapter_id}/storyboard/assets", response_model=dict)
async def get_storyboard_assets(
    novel_id: str,
    chapter_id: str,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Return the canonical Character/Scene/Prop library used for storyboard mapping."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="å°è¯´ä¸å­˜åœ¨")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="ç« èŠ‚ä¸å­˜åœ¨")

    from app.services.storyboard_service import StoryboardService

    service = StoryboardService(db)
    return {
        "success": True,
        "data": service.get_asset_library(novel_id),
    }


@router.post("/{novel_id}/chapters/{chapter_id}/storyboard/generate-panels", response_model=dict)
async def generate_storyboard_panels(
    novel_id: str,
    chapter_id: str,
    request: StoryboardGeneratePanelsRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Generate storyboard panels from enriched segments and optionally create Shot rows."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    from app.services.storyboard_service import StoryboardService

    return await StoryboardService(db).generate_panels_from_segments(
        chapter=chapter,
        style=request.style,
        store=request.store,
        create_shots=request.create_shots,
        overwrite_shots=request.overwrite_shots,
        max_segments=request.max_segments,
    )


@router.post("/{novel_id}/chapters/{chapter_id}/storyboard/normalize-panels", response_model=dict)
async def normalize_storyboard_panels(
    novel_id: str,
    chapter_id: str,
    request: StoryboardNormalizeRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """
    Normalize raw external panels against NovelFlow libraries.

    The output is still editable JSON, but image/video prompts are composed from
    canonical Character, Scene, and Prop records so visual identity is stable.
    """
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="å°è¯´ä¸å­˜åœ¨")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="ç« èŠ‚ä¸å­˜åœ¨")

    from app.services.storyboard_service import StoryboardService

    service = StoryboardService(db)
    data = service.normalize_panels(
        chapter=chapter,
        panels=request.panels,
        style=request.style,
        store=request.store,
    )

    if request.create_shots:
        data = service.convert_panels_to_shots(
            chapter=chapter,
            panels=data.get("normalizedPanels", []),
            style=request.style,
            overwrite=request.overwrite_shots,
            store=request.store,
        )

    return {
        "success": True,
        "data": data,
    }


@router.post("/{novel_id}/chapters/{chapter_id}/storyboard/convert-panels-to-shots", response_model=dict)
async def convert_storyboard_panels_to_shots(
    novel_id: str,
    chapter_id: str,
    request: StoryboardConvertRequest,
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    db: Session = Depends(get_db),
):
    """Create NovelFlow Shot rows from normalized or raw storyboard panels."""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="å°è¯´ä¸å­˜åœ¨")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="ç« èŠ‚ä¸å­˜åœ¨")

    from app.services.storyboard_service import StoryboardService

    service = StoryboardService(db)
    return {
        "success": True,
        "data": service.convert_panels_to_shots(
            chapter=chapter,
            panels=request.panels,
            style=request.style,
            overwrite=request.overwrite,
            store=request.store,
        ),
    }


# ==================== 批量导入 ====================

@router.post("/{novel_id}/chapters/batch-import/preview", response_model=dict)
async def batch_import_preview(
    novel_id: str,
    file: UploadFile = File(...),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """批量导入预览：解析 TXT 文件但不入库，返回章节列表及操作类型。"""
    # 校验文件扩展名
    if not file.filename or not file.filename.lower().endswith('.txt'):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 读取文件内容
    raw_bytes = await file.read()
    if not raw_bytes:
        return {
            "success": True,
            "data": {
                "chapters": [],
                "summary": {"total": 0, "new": 0, "replace": 0},
                "errors": [],
            }
        }

    encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors='replace')

    # 解析章节
    chapters, errors = parse_chapters_from_text(text)

    # 获取已有章节号
    existing_chapters = chapter_repo.list_by_novel(novel_id)
    existing_numbers = {c.number for c in existing_chapters}

    # 计算 action 类型
    preview_chapters = []
    new_count = 0
    replace_count = 0
    for ch in chapters:
        action = "replace" if ch['number'] in existing_numbers else "new"
        if action == "new":
            new_count += 1
        else:
            replace_count += 1
        preview_chapters.append({
            "number": ch['number'],
            "title": ch['title'],
            "content_length": len(ch['content']),
            "action": action,
        })

    return {
        "success": True,
        "data": {
            "chapters": preview_chapters,
            "summary": {
                "total": len(preview_chapters),
                "new": new_count,
                "replace": replace_count,
            },
            "errors": errors,
        }
    }


@router.post("/{novel_id}/chapters/batch-import", response_model=dict)
async def batch_import_chapters(
    novel_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """批量导入执行：解析 TXT 文件并执行 bulk_upsert。"""
    # 校验文件扩展名
    if not file.filename or not file.filename.lower().endswith('.txt'):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 读取文件内容
    raw_bytes = await file.read()
    if not raw_bytes:
        return {
            "success": True,
            "data": {
                "total": 0,
                "created": 0,
                "updated": 0,
                "failed": 0,
                "errors": [],
                "chapters": [],
            },
            "message": "文件为空",
        }

    encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors='replace')

    # 解析章节
    chapters, parse_errors = parse_chapters_from_text(text)

    # 完全无法解析章节
    if not chapters:
        return {
            "success": False,
            "message": "无法解析章节，请检查文件格式",
            "data": {"errors": parse_errors},
        }

    # 执行批量 upsert
    result = chapter_repo.bulk_upsert(novel_id, chapters)

    # 合并解析错误和 upsert 错误
    all_errors = parse_errors + result['errors']

    success_count = result['created'] + result['updated']
    failed_count = result['failed']

    # 构建消息
    if failed_count == 0:
        message = f"导入完成：成功 {success_count} 个"
    else:
        message = f"导入完成：成功 {success_count} 个，失败 {failed_count} 个"

    return {
        "success": True,
        "data": {
            "total": len(chapters),
            "created": result['created'],
            "updated": result['updated'],
            "failed": failed_count,
            "errors": all_errors,
            "chapters": result['chapters'],
        },
        "message": message,
    }
