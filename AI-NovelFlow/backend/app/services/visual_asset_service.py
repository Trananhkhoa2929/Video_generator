"""Chapter segment-driven visual asset extraction for story-to-video production."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.novel import Chapter, Character, Prop, Scene
from app.services.llm_service import LLMService
from app.services.segment_service import SegmentService
from app.utils.json_parser import safe_parse_llm_json


ASSET_EXTRACTION_PROMPT = """You are a senior manga/anime production asset planner.

Task: read ALL enriched visual/cinematic segments from one novel chapter and
update a reusable VISUAL ASSET LIBRARY for later beat, panel, image, and video
generation.

This is not literary summary. Extract canonical visual assets so later shots can
reference the same characters, locations, and props consistently.

Rules:
1. Characters
- Include named speakers, named visible people, important referenced people,
  and visually present unnamed groups that need to appear on screen.
- Do not return a non-visual narrator as a visual character.
- Use existing asset names exactly when they refer to the same thing.
- Include a stable full-body appearance prompt. If details are missing, infer
  conservatively from role, age, genre, social status, and chapter context.

2. Scenes
- A scene is a reusable visual location or location variant.
- Split variants when camera area, interior/exterior, doorway/window/bedside,
  time of day, weather, lighting, crowd state, memory, myth, or abstract space
  would need a different background reference.
- Scene descriptions must describe environment only.

3. Props
- Include magic items, weapons, handled objects, repeated objects, story
  anchors, and set dressing: bed, desk, door, window, lamp, curtain, cup,
  scroll, token, insect, rain streaks, floor boards, etc.
- Exclude body parts, pure emotions, and abstract concepts.
- Each prop needs shape, material, color, texture, size, and distinctive marks.

Segment mindset:
- Read the full segment list before creating assets.
- A segment can be internal monologue. Still extract symbolic/memory/legend
  spaces and physical anchors if they are useful for panels.
- Attach source segment IDs to each returned asset so later stages can map
  assets back to story beats.
- Prefer canonical reusable names. Do not duplicate existing assets.

Return JSON only. No Markdown. No comments.

JSON validity rules:
- Compact JSON and pretty JSON are both acceptable; indentation does not matter.
- Every string value must be valid JSON.
- Do not place raw double quotes inside any string value.
- For `source_evidence`, paraphrase the evidence instead of copying dialogue
  with quote marks.
- If quote marks are unavoidable inside a string, escape each quote with a
  backslash.
- Do not use trailing commas.

Schema:
{
  "characters": [
    {
      "name": "canonical name",
      "description": "role, relationship, narrative function, aliases if any",
      "appearance": "one natural-language full-body reference prompt",
      "voice_prompt": "brief voice design",
      "source_evidence": "short paraphrase from the segment; no raw double quotes",
      "segment_ids": ["ch02_seg_01"],
      "importance": "hero|supporting|background"
    }
  ],
  "scenes": [
    {
      "name": "canonical scene name",
      "description": "short environment summary only",
      "setting": "detailed background reference prompt, environment only",
      "source_evidence": "short paraphrase from the segment; no raw double quotes",
      "segment_ids": ["ch02_seg_01"],
      "importance": "hero|supporting|background"
    }
  ],
  "props": [
    {
      "name": "canonical prop name",
      "description": "function, story usage, where it appears",
      "appearance": "detailed prop reference prompt",
      "source_evidence": "short paraphrase from the segment; no raw double quotes",
      "segment_ids": ["ch02_seg_01"],
      "importance": "hero|supporting|background"
    }
  ],
  "notes": ["warnings or assumptions"]
}
"""

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "prompt_templates",
    "visual_asset_extract.txt",
)


class VisualAssetService:
    """Extract and upsert canonical Character/Scene/Prop assets from all chapter segments."""

    def __init__(self, db: Session):
        self.db = db

    async def extract_chapter_assets(
        self,
        chapter: Chapter,
        store: bool = True,
        update_existing: bool = True,
        max_characters: int = 16,
        max_scenes: int = 20,
        max_props: int = 40,
        ensure_segments: bool = True,
        enrich_segments: bool = True,
    ) -> Dict[str, Any]:
        existing_library = self._asset_library(chapter.novel_id)
        segment_service = SegmentService(self.db)

        if ensure_segments:
            segment_data = await segment_service.ensure_enriched_segments(chapter, enrich=enrich_segments)
        else:
            segment_data = segment_service.get_segment_data(chapter)

        if segment_data.get("error"):
            return {"success": False, "message": segment_data["error"]}

        segments = segment_data.get("enriched") if enrich_segments else segment_data.get("raw")
        if not segments:
            return {
                "success": False,
                "message": "No chapter segments available. Run Split Segments and Enrich Segments first.",
            }

        llm_result = await self._ask_llm_for_chapter_segments(
            chapter=chapter,
            segments=segments,
            existing_library=existing_library,
            max_characters=max_characters,
            max_scenes=max_scenes,
            max_props=max_props,
        )
        if llm_result.get("error"):
            return {
                "success": False,
                "message": llm_result["error"],
                "raw": llm_result.get("raw"),
            }

        normalized = self._normalize_result(llm_result)

        storage_result = {
            "created": {"characters": [], "scenes": [], "props": []},
            "updated": {"characters": [], "scenes": [], "props": []},
            "matched": {"characters": [], "scenes": [], "props": []},
            "skipped": [],
            "narrator": None,
        }
        if store:
            storage_result = self._upsert_assets(
                chapter=chapter,
                data=normalized,
                update_existing=update_existing,
                source_segments=[segment.get("id", "") for segment in segments],
            )

        self._store_extraction_snapshot(
            chapter,
            {
                "raw": normalized,
                "store": store,
                "update_existing": update_existing,
                "ensure_segments": ensure_segments,
                "enrich_segments": enrich_segments,
                "extraction_mode": "all_segments_single_request",
                "segment_ids": [segment.get("id", "") for segment in segments],
                "storage": storage_result,
            },
        )

        return {
            "success": True,
            "data": {
                **normalized,
                "storage": storage_result,
                "existingAssetCounts": {
                    "characters": len(existing_library["characters"]),
                    "scenes": len(existing_library["scenes"]),
                    "props": len(existing_library["props"]),
                },
                "segmentCount": len(segments),
                "segmentErrors": [],
            },
        }

    async def _ask_llm_for_chapter_segments(
        self,
        chapter: Chapter,
        segments: List[Dict[str, Any]],
        existing_library: Dict[str, Any],
        max_characters: int,
        max_scenes: int,
        max_props: int,
    ) -> Dict[str, Any]:
        llm = LLMService()
        llm.temperature = None
        if not llm.api_key and llm.provider not in ("ollama", "custom", "chrome_debug"):
            return {"error": "LLM API key is not configured."}

        user_content = f"""Existing canonical asset library:
{json.dumps(self._compact_library(existing_library), ensure_ascii=False, indent=2)}

Requested maximums for the whole chapter:
- characters: {max_characters}
- scenes: {max_scenes}
- props: {max_props}

Chapter title: {chapter.title}
Chapter number: {chapter.number}

All chapter segments:
{json.dumps(segments, ensure_ascii=False, indent=2)}

Important:
- Read all segments before deciding canonical asset names.
- Extract only assets justified by the chapter segments, but reuse existing names.
- If a segment references memory, myth, legend, symbolic space, or inner
  monologue that can become panels, extract the corresponding scene variant.
- Include segment_ids on every returned asset. Use the source segment IDs from
  the input exactly.
- Keep total candidates compact enough to fit the requested maximums after
  deduplication.
"""
        result = await llm.chat_completion(
            system_prompt=self._get_system_prompt(),
            user_content=user_content,
            temperature=0.22,
            max_tokens=16000,
            response_format="json_object",
            task_type="extract_visual_assets",
            novel_id=chapter.novel_id,
            chapter_id=chapter.id,
        )
        if not result.get("success"):
            return {"error": result.get("error", "LLM request failed.")}

        data = safe_parse_llm_json(result.get("content", ""), default=None)
        if not data:
            return {
                "error": "Failed to parse LLM JSON response.",
                "raw": result.get("content", "")[:1000],
            }
        if not isinstance(data, dict):
            return {
                "error": "LLM returned the wrong JSON shape for asset extraction. Expected an object.",
                "raw": result.get("content", "")[:1000],
            }
        return data

    def _get_system_prompt(self) -> str:
        try:
            with open(PROMPT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                template = f.read().strip()
                if template:
                    return template
        except OSError:
            pass
        return ASSET_EXTRACTION_PROMPT

    def _normalize_result(self, data: Dict[str, Any], segment_id: Optional[str] = None) -> Dict[str, Any]:
        result = {
            "characters": [
                self._clean_character(item, segment_id)
                for item in self._as_list(data.get("characters"))
                if isinstance(item, dict) and self._clean_name(item.get("name"))
            ],
            "scenes": [
                self._clean_scene(item, segment_id)
                for item in self._as_list(data.get("scenes"))
                if isinstance(item, dict) and self._clean_name(item.get("name"))
            ],
            "props": [
                self._clean_prop(item, segment_id)
                for item in self._as_list(data.get("props"))
                if isinstance(item, dict) and self._clean_name(item.get("name"))
            ],
            "notes": [self._clean_text(note) for note in self._as_list(data.get("notes")) if self._clean_text(note)],
        }
        return result

    def _merge_normalized_results(self, first_pass: Dict[str, Any], next_pass: Dict[str, Any]) -> Dict[str, Any]:
        merged = {
            "characters": list(first_pass.get("characters", [])),
            "scenes": list(first_pass.get("scenes", [])),
            "props": list(first_pass.get("props", [])),
            "notes": list(first_pass.get("notes", [])) + list(next_pass.get("notes", [])),
        }
        for key in ("characters", "scenes", "props"):
            for item in next_pass.get(key, []):
                self._append_unique_asset(merged[key], item)
        return merged

    def _append_unique_asset(self, assets: List[Dict[str, str]], candidate: Dict[str, str]) -> None:
        name = candidate.get("name", "")
        key = self._normalize_key(name)
        if not key:
            return
        for existing in assets:
            existing_key = self._normalize_key(existing.get("name", ""))
            if not existing_key:
                continue
            if key == existing_key or key in existing_key or existing_key in key:
                self._merge_asset_metadata(existing, candidate)
                return
            if SequenceMatcher(None, key, existing_key).ratio() >= 0.88:
                self._merge_asset_metadata(existing, candidate)
                return
        assets.append(candidate)

    def _merge_asset_metadata(self, existing: Dict[str, str], candidate: Dict[str, str]) -> None:
        for field in ("description", "appearance", "setting", "voice_prompt", "source_evidence"):
            incoming = self._clean_text(candidate.get(field))
            current = self._clean_text(existing.get(field))
            if incoming and len(incoming) > len(current) + 30:
                existing[field] = incoming
        existing_segments = set(self._as_list(existing.get("segment_ids")))
        existing_segments.update(self._as_list(candidate.get("segment_ids")))
        existing["segment_ids"] = sorted(str(item) for item in existing_segments if item)

    def _compact_normalized(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "characters": [{"name": item.get("name", ""), "description": item.get("description", "")} for item in data.get("characters", [])],
            "scenes": [{"name": item.get("name", ""), "description": item.get("description", "")} for item in data.get("scenes", [])],
            "props": [{"name": item.get("name", ""), "description": item.get("description", "")} for item in data.get("props", [])],
        }

    def _clean_character(self, item: Dict[str, Any], segment_id: Optional[str]) -> Dict[str, Any]:
        return {
            "name": self._clean_name(item.get("name")),
            "description": self._clean_text(item.get("description")),
            "appearance": self._clean_text(item.get("appearance")),
            "voice_prompt": self._clean_text(item.get("voice_prompt")),
            "source_evidence": self._clean_text(item.get("source_evidence")),
            "importance": self._clean_text(item.get("importance")) or "supporting",
            "segment_ids": self._normalize_segment_ids(item.get("segment_ids"), segment_id),
        }

    def _clean_scene(self, item: Dict[str, Any], segment_id: Optional[str]) -> Dict[str, Any]:
        return {
            "name": self._clean_name(item.get("name")),
            "description": self._clean_text(item.get("description")),
            "setting": self._clean_text(item.get("setting")),
            "source_evidence": self._clean_text(item.get("source_evidence")),
            "importance": self._clean_text(item.get("importance")) or "supporting",
            "segment_ids": self._normalize_segment_ids(item.get("segment_ids"), segment_id),
        }

    def _clean_prop(self, item: Dict[str, Any], segment_id: Optional[str]) -> Dict[str, Any]:
        return {
            "name": self._clean_name(item.get("name")),
            "description": self._clean_text(item.get("description")),
            "appearance": self._clean_text(item.get("appearance")),
            "source_evidence": self._clean_text(item.get("source_evidence")),
            "importance": self._clean_text(item.get("importance")) or "supporting",
            "segment_ids": self._normalize_segment_ids(item.get("segment_ids"), segment_id),
        }

    def _normalize_segment_ids(self, value: Any, fallback_segment_id: Optional[str]) -> List[str]:
        ids = [self._clean_text(item) for item in self._as_list(value) if self._clean_text(item)]
        if not ids and fallback_segment_id:
            ids = [fallback_segment_id]
        return sorted(dict.fromkeys(ids))

    def _upsert_assets(
        self,
        chapter: Chapter,
        data: Dict[str, Any],
        update_existing: bool,
        source_segments: List[str],
    ) -> Dict[str, Any]:
        source_range = f"Chapter {chapter.number}: {', '.join(source_segments[:8])}"
        if len(source_segments) > 8:
            source_range += f", +{len(source_segments) - 8} segments"

        result = {
            "created": {"characters": [], "scenes": [], "props": []},
            "updated": {"characters": [], "scenes": [], "props": []},
            "matched": {"characters": [], "scenes": [], "props": []},
            "skipped": [],
            "narrator": self._ensure_narrator(chapter.novel_id),
        }

        character_index = self._index_existing(Character, chapter.novel_id)
        scene_index = self._index_existing(Scene, chapter.novel_id)
        prop_index = self._index_existing(Prop, chapter.novel_id)

        for item in data.get("characters", []):
            if self._is_narrator_name(item["name"]):
                result["skipped"].append({"type": "character", "name": item["name"], "reason": "narrator"})
                continue
            existing = self._find_existing(item["name"], character_index)
            if existing:
                result["matched"]["characters"].append(existing.name)
                if update_existing and self._update_character(existing, item, source_range, chapter.number):
                    result["updated"]["characters"].append(existing.name)
                continue
            created = Character(
                novel_id=chapter.novel_id,
                name=item["name"],
                description=item.get("description", ""),
                appearance=item.get("appearance", ""),
                voice_prompt=item.get("voice_prompt", ""),
                start_chapter=chapter.number,
                end_chapter=chapter.number,
                is_incremental=True,
                source_range=source_range,
                last_parsed_at=datetime.utcnow(),
            )
            self.db.add(created)
            self.db.flush()
            result["created"]["characters"].append(created.name)
            self._add_to_index(created, character_index)

        for item in data.get("scenes", []):
            existing = self._find_existing(item["name"], scene_index)
            if existing:
                result["matched"]["scenes"].append(existing.name)
                if update_existing and self._update_scene(existing, item, source_range, chapter.number):
                    result["updated"]["scenes"].append(existing.name)
                continue
            created = Scene(
                novel_id=chapter.novel_id,
                name=item["name"],
                description=item.get("description", ""),
                setting=item.get("setting", ""),
                start_chapter=chapter.number,
                end_chapter=chapter.number,
                is_incremental=True,
                source_range=source_range,
                last_parsed_at=datetime.utcnow(),
            )
            self.db.add(created)
            self.db.flush()
            result["created"]["scenes"].append(created.name)
            self._add_to_index(created, scene_index)

        for item in data.get("props", []):
            existing = self._find_existing(item["name"], prop_index)
            if existing:
                result["matched"]["props"].append(existing.name)
                if update_existing and self._update_prop(existing, item, source_range, chapter.number):
                    result["updated"]["props"].append(existing.name)
                continue
            created = Prop(
                novel_id=chapter.novel_id,
                name=item["name"],
                description=item.get("description", ""),
                appearance=item.get("appearance", ""),
                start_chapter=chapter.number,
                end_chapter=chapter.number,
                is_incremental=True,
                source_range=source_range,
                last_parsed_at=datetime.utcnow(),
            )
            self.db.add(created)
            self.db.flush()
            result["created"]["props"].append(created.name)
            self._add_to_index(created, prop_index)

        self.db.commit()
        return result

    def _ensure_narrator(self, novel_id: str) -> str:
        narrator = self.db.query(Character).filter(Character.novel_id == novel_id, Character.is_narrator == True).first()
        if narrator:
            return "exists"
        for name in ("\u65c1\u767d", "Narrator", "Nguoi dan truyen", "Người dẫn truyện"):
            narrator = self.db.query(Character).filter(Character.novel_id == novel_id, Character.name == name).first()
            if narrator:
                narrator.is_narrator = True
                narrator.description = narrator.description or "Non-visual narrator used for narration audio."
                narrator.voice_prompt = narrator.voice_prompt or "calm, steady narration voice"
                self.db.flush()
                return "updated"

        narrator = Character(
            novel_id=novel_id,
            name="\u65c1\u767d",
            is_narrator=True,
            description="Non-visual narrator used for narration audio.",
            voice_prompt="calm, steady narration voice, clear pacing, suitable for xianxia/manhua narration",
        )
        self.db.add(narrator)
        self.db.flush()
        return "created"

    def _update_character(self, existing: Character, item: Dict[str, str], source_range: str, chapter_number: int) -> bool:
        changed = False
        changed |= self._set_if_better(existing, "description", item.get("description"))
        changed |= self._set_if_better(existing, "appearance", item.get("appearance"))
        changed |= self._set_if_better(existing, "voice_prompt", item.get("voice_prompt"))
        changed |= self._touch_existing(existing, source_range, chapter_number)
        return changed

    def _update_scene(self, existing: Scene, item: Dict[str, str], source_range: str, chapter_number: int) -> bool:
        changed = False
        changed |= self._set_if_better(existing, "description", item.get("description"))
        changed |= self._set_if_better(existing, "setting", item.get("setting"))
        changed |= self._touch_existing(existing, source_range, chapter_number)
        return changed

    def _update_prop(self, existing: Prop, item: Dict[str, str], source_range: str, chapter_number: int) -> bool:
        changed = False
        changed |= self._set_if_better(existing, "description", item.get("description"))
        changed |= self._set_if_better(existing, "appearance", item.get("appearance"))
        changed |= self._touch_existing(existing, source_range, chapter_number)
        return changed

    def _set_if_better(self, obj: Any, attr: str, value: Optional[str]) -> bool:
        value = self._clean_text(value)
        if not value:
            return False
        current = self._clean_text(getattr(obj, attr, ""))
        if not current or len(value) > len(current) + 40:
            setattr(obj, attr, value)
            return True
        return False

    def _touch_existing(self, obj: Any, source_range: str, chapter_number: int) -> bool:
        changed = False
        if obj.start_chapter is None or chapter_number < obj.start_chapter:
            obj.start_chapter = chapter_number
            changed = True
        if obj.end_chapter is None or chapter_number > obj.end_chapter:
            obj.end_chapter = chapter_number
            changed = True
        if not obj.source_range:
            obj.source_range = source_range
            changed = True
        elif source_range not in obj.source_range:
            obj.source_range = f"{obj.source_range}, {source_range}"
            changed = True
        obj.is_incremental = True
        obj.last_parsed_at = datetime.utcnow()
        return changed

    def _asset_library(self, novel_id: str) -> Dict[str, List[Any]]:
        return {
            "characters": self.db.query(Character).filter(Character.novel_id == novel_id).all(),
            "scenes": self.db.query(Scene).filter(Scene.novel_id == novel_id).all(),
            "props": self.db.query(Prop).filter(Prop.novel_id == novel_id).all(),
        }

    def _compact_library(self, library: Dict[str, List[Any]]) -> Dict[str, Any]:
        return {
            "characters": [
                {"name": item.name, "description": item.description or "", "appearance": item.appearance or ""}
                for item in library["characters"]
                if not getattr(item, "is_narrator", False)
            ],
            "scenes": [
                {"name": item.name, "description": item.description or "", "setting": item.setting or ""}
                for item in library["scenes"]
            ],
            "props": [
                {"name": item.name, "description": item.description or "", "appearance": item.appearance or ""}
                for item in library["props"]
            ],
        }

    def _index_existing(self, model: Any, novel_id: str) -> Dict[str, Any]:
        items = self.db.query(model).filter(model.novel_id == novel_id).all()
        index = {"items": items, "keys": {}}
        for item in items:
            self._add_to_index(item, index)
        return index

    def _add_to_index(self, item: Any, index: Dict[str, Any]) -> None:
        key = self._normalize_key(getattr(item, "name", ""))
        if key:
            index["keys"][key] = item
        if item not in index["items"]:
            index["items"].append(item)

    def _find_existing(self, name: str, index: Dict[str, Any]) -> Optional[Any]:
        key = self._normalize_key(name)
        if not key:
            return None
        if key in index["keys"]:
            return index["keys"][key]

        best_item = None
        best_score = 0.0
        for item in index["items"]:
            item_key = self._normalize_key(getattr(item, "name", ""))
            if not item_key:
                continue
            if key in item_key or item_key in key:
                score = 0.94
            else:
                score = SequenceMatcher(None, key, item_key).ratio()
            if score > best_score:
                best_score = score
                best_item = item
        if best_item and best_score >= 0.88:
            return best_item
        return None

    def _store_extraction_snapshot(self, chapter: Chapter, payload: Dict[str, Any]) -> None:
        parsed_data = self._load_parsed_data(chapter)
        parsed_data["visual_asset_extraction"] = payload
        chapter.parsed_data = json.dumps(parsed_data, ensure_ascii=False)
        self.db.commit()

    def _load_parsed_data(self, chapter: Chapter) -> Dict[str, Any]:
        if not chapter.parsed_data:
            return {}
        if isinstance(chapter.parsed_data, dict):
            return dict(chapter.parsed_data)
        try:
            parsed = json.loads(chapter.parsed_data)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _normalize_key(self, value: Any) -> str:
        text = self._clean_name(value).lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    def _clean_name(self, value: Any) -> str:
        return self._clean_text(value).strip(" -_")

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return re.sub(r"\s+", " ", str(value)).strip()

    def _as_list(self, value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _is_narrator_name(self, name: str) -> bool:
        normalized = self._normalize_key(name)
        return normalized in {"narrator", "nguoidantruyen", "\u65c1\u767d"}
