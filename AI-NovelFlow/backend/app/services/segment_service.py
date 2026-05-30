"""Chapter segmentation and segment enrichment for the storyboard pipeline."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.novel import Chapter
from app.services.llm_service import LLMService
from app.utils.json_parser import safe_parse_llm_json


PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompt_templates")
BOUNDARY_PROMPT_PATH = os.path.join(PROMPT_DIR, "segment_boundary_detection.txt")
ENRICH_PROMPT_PATH = os.path.join(PROMPT_DIR, "segment_attribute_enrichment.txt")


class SegmentService:
    """Create and store visual/cinematic chapter segments in parsed_data."""

    def __init__(self, db: Session):
        self.db = db

    def get_segment_data(self, chapter: Chapter) -> Dict[str, Any]:
        parsed = self._load_parsed_data(chapter)
        data = parsed.get("segments")
        if not isinstance(data, dict):
            return {"raw": [], "enriched": [], "updated_at": None}
        return {
            "raw": self._as_list(data.get("raw")),
            "enriched": self._as_list(data.get("enriched")),
            "updated_at": data.get("updated_at"),
            "source": data.get("source", "llm"),
            "target_segment_count": data.get("target_segment_count"),
        }

    async def split_chapter(self, chapter: Chapter, overwrite: bool = True) -> Dict[str, Any]:
        stored = self.get_segment_data(chapter)
        if stored["raw"] and not overwrite:
            return {"success": True, "data": stored}

        if not (chapter.content or "").strip():
            return {"success": False, "message": "Chapter content is empty"}

        llm = LLMService()
        llm.temperature = None
        if not llm.api_key and llm.provider not in ("ollama", "custom", "chrome_debug"):
            return {"success": False, "message": "LLM API key is not configured"}

        chapter_code = self._chapter_code(chapter)
        target_segment_count = self._target_segment_count(chapter.content)
        minimum_segment_count = self._minimum_segment_count(target_segment_count)
        user_content = f"""Chapter code: {chapter_code}
Chapter number: {chapter.number}
Chapter title: {chapter.title}
Target segment count: {target_segment_count}
Minimum acceptable segment count: {minimum_segment_count}

Chapter text:
{chapter.content[:70000]}
"""
        result = await llm.chat_completion(
            system_prompt=self._load_prompt(BOUNDARY_PROMPT_PATH),
            user_content=user_content,
            temperature=0.15,
            max_tokens=18000,
            response_format="json_object",
            task_type="segment_boundary_detection",
            novel_id=chapter.novel_id,
            chapter_id=chapter.id,
        )
        if not result.get("success"):
            return {"success": False, "message": result.get("error", "Segment split failed")}

        data = safe_parse_llm_json(result.get("content", ""), default={})
        segments = self._coerce_segment_list(data)
        segments = self._normalize_raw_segments(chapter, segments)
        validation_error = self._validate_raw_segments(
            chapter,
            segments,
            target_segment_count=target_segment_count,
            minimum_segment_count=minimum_segment_count,
        )
        if validation_error:
            return {
                "success": False,
                "message": validation_error,
                "raw_segment_count": len(segments),
                "target_segment_count": target_segment_count,
                "minimum_segment_count": minimum_segment_count,
                "raw": result.get("content", "")[:1000],
            }

        parsed = self._load_parsed_data(chapter)
        existing = parsed.get("segments") if isinstance(parsed.get("segments"), dict) else {}
        parsed["segments"] = {
            **existing,
            "raw": segments,
            "enriched": [] if overwrite else self._as_list(existing.get("enriched")),
            "source": "reference_segment_prompt",
            "target_segment_count": target_segment_count,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self._save_parsed_data(chapter, parsed)
        return {"success": True, "data": parsed["segments"]}

    async def enrich_segments(self, chapter: Chapter, overwrite: bool = True) -> Dict[str, Any]:
        stored = self.get_segment_data(chapter)
        if stored["enriched"] and not overwrite:
            return {"success": True, "data": stored}

        raw_segments = stored["raw"]
        if not raw_segments:
            split_result = await self.split_chapter(chapter, overwrite=False)
            if not split_result.get("success"):
                return split_result
            raw_segments = self._as_list(split_result.get("data", {}).get("raw"))

        llm = LLMService()
        llm.temperature = None
        if not llm.api_key and llm.provider not in ("ollama", "custom", "chrome_debug"):
            return {"success": False, "message": "LLM API key is not configured"}

        chapter_code = self._chapter_code(chapter)
        user_content = f"""Chapter code: {chapter_code}
Chapter number: {chapter.number}
Chapter title: {chapter.title}

Input segments:
{json.dumps(raw_segments, ensure_ascii=False, indent=2)}
"""
        result = await llm.chat_completion(
            system_prompt=self._load_prompt(ENRICH_PROMPT_PATH),
            user_content=user_content,
            temperature=0.2,
            max_tokens=22000,
            response_format="json_object",
            task_type="segment_attribute_enrichment",
            novel_id=chapter.novel_id,
            chapter_id=chapter.id,
        )
        if not result.get("success"):
            return {"success": False, "message": result.get("error", "Segment enrichment failed")}

        data = safe_parse_llm_json(result.get("content", ""), default={})
        enriched = self._coerce_segment_list(data)
        enriched = self._normalize_enriched_segments(chapter, raw_segments, enriched)
        if not enriched:
            return {
                "success": False,
                "message": "LLM returned no enriched segments",
                "raw": result.get("content", "")[:1000],
            }

        parsed = self._load_parsed_data(chapter)
        existing = parsed.get("segments") if isinstance(parsed.get("segments"), dict) else {}
        parsed["segments"] = {
            **existing,
            "raw": raw_segments,
            "enriched": enriched,
            "source": "reference_segment_prompt",
            "updated_at": datetime.utcnow().isoformat(),
        }
        self._save_parsed_data(chapter, parsed)
        return {"success": True, "data": parsed["segments"]}

    async def ensure_enriched_segments(self, chapter: Chapter, enrich: bool = True) -> Dict[str, Any]:
        stored = self.get_segment_data(chapter)
        if enrich and stored["enriched"]:
            return stored
        if not enrich and stored["raw"]:
            return stored
        if not stored["raw"]:
            split_result = await self.split_chapter(chapter, overwrite=False)
            if not split_result.get("success"):
                return {"raw": [], "enriched": [], "error": split_result.get("message")}
            stored = self.get_segment_data(chapter)
        if enrich and not stored["enriched"]:
            enrich_result = await self.enrich_segments(chapter, overwrite=False)
            if not enrich_result.get("success"):
                stored["error"] = enrich_result.get("message")
                return stored
            stored = self.get_segment_data(chapter)
        return stored

    def _normalize_raw_segments(self, chapter: Chapter, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        chapter_code = self._chapter_code(chapter)
        for index, item in enumerate(segments, 1):
            if not isinstance(item, dict):
                continue
            text = self._clean_text(item.get("text_vi") or item.get("text") or item.get("content"))
            if not text:
                continue
            normalized.append(
                {
                    "id": self._segment_id(chapter_code, index, item.get("id")),
                    "text_vi": text,
                }
            )
        return normalized

    def _validate_raw_segments(
        self,
        chapter: Chapter,
        segments: List[Dict[str, Any]],
        target_segment_count: int,
        minimum_segment_count: int,
    ) -> Optional[str]:
        if not segments:
            return "LLM returned no valid segments. Nothing was saved."

        if len(segments) < minimum_segment_count:
            return (
                "LLM returned too few segments "
                f"({len(segments)} raw, expected at least {minimum_segment_count}, "
                f"target {target_segment_count}). Nothing was saved."
            )

        source_compact = re.sub(r"\s+", "", chapter.content or "")
        segment_compact = re.sub(r"\s+", "", "".join(str(item.get("text_vi") or "") for item in segments))
        if source_compact:
            coverage_ratio = len(segment_compact) / len(source_compact)
            if coverage_ratio < 0.85:
                return (
                    "LLM segments do not cover enough of the chapter "
                    f"({coverage_ratio:.0%} estimated coverage). Nothing was saved."
                )
            if coverage_ratio > 1.25:
                return (
                    "LLM segments look duplicated or heavily rewritten "
                    f"({coverage_ratio:.0%} estimated coverage). Nothing was saved."
                )

        total_words = self._word_count(chapter.content or "")
        longest_words = max(self._word_count(item.get("text_vi") or "") for item in segments)
        max_reasonable_words = max(180, round(total_words * 0.2))
        if target_segment_count > 1 and longest_words > max_reasonable_words:
            return (
                "LLM returned at least one segment that is too large "
                f"({longest_words} words, maximum allowed {max_reasonable_words}). Nothing was saved."
            )

        return None

    def _normalize_enriched_segments(
        self,
        chapter: Chapter,
        raw_segments: List[Dict[str, Any]],
        enriched_segments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_id = {str(item.get("id")): item for item in enriched_segments if isinstance(item, dict)}
        chapter_code = self._chapter_code(chapter)
        normalized = []
        for index, raw in enumerate(raw_segments, 1):
            segment_id = raw.get("id") or f"{chapter_code}_seg_{index:02d}"
            item = by_id.get(segment_id, {})
            text = raw.get("text_vi") or item.get("text_vi") or ""
            beat_count = self._safe_int(item.get("suggested_beat_count"), 1, 1, 6)
            panel_count = self._safe_int(item.get("suggested_panel_count"), max(2, beat_count), 2, 8)
            panel_count = max(panel_count, beat_count)
            normalized.append(
                {
                    "id": segment_id,
                    "chapter_id": item.get("chapter_id") or chapter_code,
                    "segment_index": index,
                    "text_vi": text,
                    "word_count": self._safe_int(item.get("word_count"), self._word_count(text), 0, 100000),
                    "narrative_mode": self._enum(
                        item.get("narrative_mode"),
                        {"narration", "internal_monologue", "dialogue", "action", "flashback", "mixed"},
                        "mixed",
                    ),
                    "has_dialogue": bool(item.get("has_dialogue", '"' in text or "“" in text or "”" in text)),
                    "has_flashback": bool(item.get("has_flashback", False)),
                    "has_inner_monologue": bool(item.get("has_inner_monologue", False)),
                    "focus_character": self._clean_text(item.get("focus_character")) or "unknown",
                    "characters_present": self._normalize_characters_present(item.get("characters_present")),
                    "pov": self._enum(item.get("pov"), {"first_person", "close_third", "omniscient"}, "close_third"),
                    "location": self._normalize_location(item.get("location")),
                    "emotional_tone": self._enum(
                        item.get("emotional_tone"),
                        {
                            "resolve",
                            "melancholic",
                            "cold_contempt",
                            "rage",
                            "grief",
                            "tension",
                            "awe",
                            "pride",
                            "warmth",
                            "dread",
                            "neutral_expository",
                            "playful",
                        },
                        "neutral_expository",
                    ),
                    "emotional_intensity": self._safe_int(item.get("emotional_intensity"), 5, 1, 10),
                    "tone_shift": bool(item.get("tone_shift", False)),
                    "tone_start": item.get("tone_start"),
                    "tone_end": item.get("tone_end"),
                    "visual_complexity": self._enum(item.get("visual_complexity"), {"low", "medium", "high"}, "medium"),
                    "key_visual": self._clean_text(item.get("key_visual")) or text[:180],
                    "suggested_beat_count": beat_count,
                    "suggested_panel_count": panel_count,
                    "text_density": self._enum(item.get("text_density"), {"low", "medium", "high"}, "medium"),
                    "prev_segment_id": raw_segments[index - 2].get("id") if index > 1 else None,
                    "next_segment_id": raw_segments[index].get("id") if index < len(raw_segments) else None,
                    "transition_in": self._transition(item.get("transition_in")),
                    "transition_out": self._transition(item.get("transition_out")),
                    "tags": self._normalize_tags(item.get("tags")),
                    "special_notes": self._clean_text(item.get("special_notes")),
                }
            )
        return normalized

    def _normalize_characters_present(self, value: Any) -> List[Dict[str, str]]:
        roles = {"protagonist", "antagonist", "supporting", "antagonist_background", "narrator_voice"}
        result = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = self._clean_text(item.get("name"))
                    role = item.get("role") if item.get("role") in roles else "supporting"
                else:
                    name = self._clean_text(item)
                    role = "supporting"
                if name:
                    result.append({"name": name, "role": role})
        return result

    def _normalize_location(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            value = {}
        return {
            "name": self._clean_text(value.get("name")) or "unknown",
            "type": self._enum(value.get("type"), {"interior", "exterior", "abstract"}, "abstract"),
            "time_of_day": self._enum(
                value.get("time_of_day"),
                {"dawn", "morning", "afternoon", "dusk", "night", "unknown"},
                "unknown",
            ),
            "weather": self._enum(value.get("weather"), {"clear", "rain", "snow", "storm", "unknown"}, "unknown"),
            "atmosphere": self._clean_text(value.get("atmosphere")),
        }

    def _transition(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        return self._enum(
            value,
            {"continuation", "cut", "flashback_enter", "flashback_exit", "scene_change", "character_enter", "time_skip"},
            "cut",
        )

    def _normalize_tags(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [self._clean_text(item) for item in value if self._clean_text(item)]

    def _coerce_segment_list(self, data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("segments", "data", "items", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

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

    def _save_parsed_data(self, chapter: Chapter, parsed: Dict[str, Any]) -> None:
        chapter.parsed_data = json.dumps(parsed, ensure_ascii=False)
        self.db.commit()
        self.db.refresh(chapter)

    def _load_prompt(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _chapter_code(self, chapter: Chapter) -> str:
        return f"ch{int(chapter.number or 1):02d}"

    def _segment_id(self, chapter_code: str, index: int, candidate: Any) -> str:
        text = self._clean_text(candidate)
        if re.fullmatch(r"ch\d{2}_seg_\d{2}", text):
            return text
        return f"{chapter_code}_seg_{index:02d}"

    def _word_count(self, text: str) -> int:
        return len(re.findall(r"\S+", text or ""))

    def _target_segment_count(self, content: Optional[str]) -> int:
        word_count = self._word_count(content or "")
        if word_count < 220:
            return 1
        return max(3, min(50, round(word_count / 70)))

    def _minimum_segment_count(self, target_count: int) -> int:
        if target_count <= 1:
            return 1
        return max(2, min(target_count, round(target_count * 0.65)))

    def _safe_int(self, value: Any, default: int, min_value: int, max_value: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(min_value, min(max_value, number))

    def _enum(self, value: Any, allowed: set[str], default: str) -> str:
        text = self._clean_text(value)
        return text if text in allowed else default

    def _as_list(self, value: Any) -> List[Any]:
        return value if isinstance(value, list) else []

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return re.sub(r"[ \t\r\f\v]+", " ", str(value)).strip()
