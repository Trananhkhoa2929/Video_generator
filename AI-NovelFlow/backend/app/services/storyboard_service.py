"""Storyboard asset mapping and panel-to-shot prompt composition.

This service is intentionally deterministic. The heavy segment/beat/panel
reasoning can happen in LLM prompts, but final image/video prompts should be
composed from NovelFlow's canonical Character, Scene, and Prop libraries so
visual identity stays stable across panels.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.novel import Chapter, Character, Prop, Scene
from app.repositories.shot_repository import ShotRepository
from app.services.llm_service import LLMService
from app.services.segment_service import SegmentService
from app.utils.json_parser import safe_parse_llm_json


DEFAULT_STYLE_LOCK = "standard anime manhua style, high quality, detailed, professional artwork"
SEGMENT_TO_PANEL_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "prompt_templates",
    "segment_to_panel.txt",
)


class StoryboardService:
    """Normalize external storyboard panels against NovelFlow asset libraries."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_asset_library(self, novel_id: str) -> Dict[str, Any]:
        characters = (
            self.db.query(Character)
            .filter(Character.novel_id == novel_id)
            .order_by(Character.name.asc())
            .all()
        )
        scenes = (
            self.db.query(Scene)
            .filter(Scene.novel_id == novel_id)
            .order_by(Scene.name.asc())
            .all()
        )
        props = (
            self.db.query(Prop)
            .filter(Prop.novel_id == novel_id)
            .order_by(Prop.name.asc())
            .all()
        )

        return {
            "characters": [self._character_asset(c) for c in characters],
            "scenes": [self._scene_asset(s) for s in scenes],
            "props": [self._prop_asset(p) for p in props],
        }

    async def generate_panels_from_segments(
        self,
        chapter: Chapter,
        style: Optional[str] = None,
        store: bool = True,
        create_shots: bool = False,
        overwrite_shots: bool = True,
        max_segments: int = 80,
    ) -> Dict[str, Any]:
        segment_data = await SegmentService(self.db).ensure_enriched_segments(chapter, enrich=True)
        if segment_data.get("error"):
            return {"success": False, "message": segment_data["error"]}

        segments = segment_data.get("enriched") or []
        if not segments:
            return {"success": False, "message": "No enriched segments available"}

        segments = segments[:max_segments]
        asset_library = self.get_asset_library(chapter.novel_id)
        compact_library = self._compact_asset_library_for_prompt(asset_library)
        style_lock = style or DEFAULT_STYLE_LOCK

        result = await self._ask_llm_for_chapter_panels(
            chapter=chapter,
            segments=segments,
            asset_library=compact_library,
            style_lock=style_lock,
        )
        if result.get("error"):
            return {"success": False, "message": result["error"], "raw": result.get("raw")}

        generated_panels: List[Dict[str, Any]] = []
        notes: List[str] = [str(note) for note in result.get("notes", []) if note]
        segment_panel_counts: Dict[str, int] = {}

        for order, panel in enumerate(result.get("panels", []), 1):
            if not isinstance(panel, dict):
                continue
            segment_id = str(panel.get("segment_id") or "")
            if not segment_id:
                notes.append(f"panel_{order:03d}: missing segment_id from LLM response")
                segment_id = "unknown_segment"
            segment_panel_counts[segment_id] = segment_panel_counts.get(segment_id, 0) + 1
            panel["segment_id"] = segment_id
            panel["panel_id"] = panel.get("panel_id") or f"{segment_id}_p{segment_panel_counts[segment_id]:02d}"
            panel["order"] = order
            generated_panels.append(panel)

        normalized = self.normalize_panels(
            chapter=chapter,
            panels=generated_panels,
            style=style_lock,
            store=False,
        ) if generated_panels else {
            "assetLibrary": asset_library,
            "normalizedPanels": [],
            "warnings": ["No panels were generated."],
        }

        data = {
            "assetLibrary": normalized.get("assetLibrary", asset_library),
            "panels": generated_panels,
            "normalizedPanels": normalized.get("normalizedPanels", []),
            "warnings": normalized.get("warnings", []) + notes,
            "segmentCount": len(segments),
            "panelCount": len(generated_panels),
        }

        if store:
            self._store_storyboard_data(
                chapter,
                {
                    "panels": generated_panels,
                    "normalized_panels": data["normalizedPanels"],
                    "panel_generation_warnings": data["warnings"],
                    "panel_generation_mode": "all_segments_single_request",
                    "panel_source_segment_count": len(segments),
                },
            )

        if create_shots:
            converted = self.convert_panels_to_shots(
                chapter=chapter,
                panels=data["normalizedPanels"],
                style=style_lock,
                overwrite=overwrite_shots,
                store=store,
            )
            data.update(converted)

        return {"success": True, "data": data}

    def normalize_panels(
        self,
        chapter: Chapter,
        panels: Optional[List[Dict[str, Any]]] = None,
        style: Optional[str] = None,
        store: bool = True,
    ) -> Dict[str, Any]:
        raw_panels = panels if panels is not None else self._get_panels_from_chapter(chapter)
        if not raw_panels:
            return {
                "assetLibrary": self.get_asset_library(chapter.novel_id),
                "normalizedPanels": [],
                "warnings": ["No panels were provided or found in chapter.parsed_data."],
            }

        asset_library = self.get_asset_library(chapter.novel_id)
        indexes = self._build_indexes(asset_library)
        normalized_panels: List[Dict[str, Any]] = []
        warnings: List[str] = []
        style_lock = style or DEFAULT_STYLE_LOCK

        for order, panel in enumerate(raw_panels, 1):
            normalized = self._normalize_panel(panel, order, indexes, style_lock)
            normalized_panels.append(normalized)
            warnings.extend(normalized.get("warnings", []))

        result = {
            "assetLibrary": asset_library,
            "normalizedPanels": normalized_panels,
            "warnings": warnings,
        }

        if store:
            self._store_storyboard_data(
                chapter,
                {
                    "asset_library": self._compact_asset_library(asset_library),
                    "normalized_panels": normalized_panels,
                    "warnings": warnings,
                },
            )

        return result

    def convert_panels_to_shots(
        self,
        chapter: Chapter,
        panels: Optional[List[Dict[str, Any]]] = None,
        style: Optional[str] = None,
        overwrite: bool = True,
        store: bool = True,
    ) -> Dict[str, Any]:
        candidate_panels = panels if panels is not None else self._get_panels_from_chapter(chapter)
        if not candidate_panels:
            normalized = {
                "assetLibrary": self.get_asset_library(chapter.novel_id),
                "normalizedPanels": [],
                "warnings": ["No stored panels available to convert into shots."],
            }
            if store:
                self._store_storyboard_data(
                    chapter,
                    {
                        "converted_shot_count": 0,
                        "conversion_warnings": normalized["warnings"],
                    },
                )
            return {
                **normalized,
                "shots": [],
                "error": "No stored panels available to convert into shots.",
            }

        if candidate_panels and all(self._is_normalized_panel(panel) for panel in candidate_panels):
            normalized = {
                "assetLibrary": self.get_asset_library(chapter.novel_id),
                "normalizedPanels": candidate_panels,
                "warnings": [],
            }
            if store:
                self._store_storyboard_data(
                    chapter,
                    {
                        "asset_library": self._compact_asset_library(normalized["assetLibrary"]),
                        "normalized_panels": candidate_panels,
                    },
                )
        else:
            normalized = self.normalize_panels(
                chapter=chapter,
                panels=candidate_panels,
                style=style,
                store=store,
            )
        normalized_panels = normalized.get("normalizedPanels", [])

        shot_repo = ShotRepository(self.db)
        if overwrite:
            shot_repo.delete_by_chapter(chapter.id)
            start_index = 1
        else:
            start_index = shot_repo.count_by_chapter(chapter.id) + 1

        created_shots = []
        for offset, panel in enumerate(normalized_panels):
            shot = shot_repo.create(
                chapter_id=chapter.id,
                index=start_index + offset,
                description=panel.get("description", ""),
                video_description=panel.get("video_description", ""),
                characters=panel.get("characters", []),
                scene=panel.get("scene", ""),
                props=panel.get("props", []),
                duration=panel.get("duration", 4),
                dialogues=panel.get("dialogues", []),
            )
            created_shots.append(shot)

        if store:
            self._store_storyboard_data(
                chapter,
                {
                    "converted_shot_count": len(created_shots),
                    "conversion_warnings": normalized.get("warnings", []),
                },
            )

        return {
            **normalized,
            "shots": [shot_repo.to_response(s) for s in created_shots],
        }

    async def _ask_llm_for_chapter_panels(
        self,
        chapter: Chapter,
        segments: List[Dict[str, Any]],
        asset_library: Dict[str, Any],
        style_lock: str,
    ) -> Dict[str, Any]:
        llm = LLMService()
        llm.temperature = None
        if not llm.api_key and llm.provider not in ("ollama", "custom", "chrome_debug"):
            return {"error": "LLM API key is not configured."}

        user_content = f"""Canonical asset library:
{json.dumps(asset_library, ensure_ascii=False, indent=2)}

Style lock:
{style_lock}

Chapter:
- number: {chapter.number}
- title: {chapter.title}

All enriched segments:
{json.dumps(segments, ensure_ascii=False, indent=2)}

Important:
- Read the full segment list before deciding panel rhythm and continuity.
- Preserve segment order.
- Every returned panel must include a segment_id copied exactly from the input.
- Use canonical asset names from the library whenever possible.
"""
        result = await llm.chat_completion(
            system_prompt=self._get_segment_to_panel_prompt(),
            user_content=user_content,
            temperature=0.28,
            max_tokens=30000,
            response_format="json_object",
            task_type="segment_to_panels",
            novel_id=chapter.novel_id,
            chapter_id=chapter.id,
        )
        if not result.get("success"):
            return {"error": result.get("error", "Panel generation failed.")}
        data = safe_parse_llm_json(result.get("content", ""), default=None)
        if not data:
            return {
                "error": "Failed to parse panel JSON response.",
                "raw": result.get("content", "")[:1000],
            }
        if not isinstance(data, dict):
            return {
                "error": "LLM returned the wrong JSON shape for panel generation. Expected an object.",
                "raw": result.get("content", "")[:1000],
            }
        if not isinstance(data.get("panels"), list):
            return {
                "error": "LLM panel response did not include a panels array.",
                "raw": result.get("content", "")[:1000],
            }
        return data

    def _get_segment_to_panel_prompt(self) -> str:
        with open(SEGMENT_TO_PANEL_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _is_normalized_panel(self, panel: Dict[str, Any]) -> bool:
        return all(
            key in panel
            for key in ("description", "video_description", "characters", "scene", "props", "duration")
        )

    # ------------------------------------------------------------------
    # Asset library
    # ------------------------------------------------------------------

    def _character_asset(self, character: Character) -> Dict[str, Any]:
        visual_lock = self._join_non_empty(
            [
                character.appearance,
                character.description,
                "keep the same face, hair, age appearance, body proportions, outfit silhouette, and identity",
            ]
        )
        return {
            "id": character.id,
            "name": character.name,
            "description": character.description or "",
            "appearance": character.appearance or "",
            "visual_lock": visual_lock,
            "image_url": character.image_url,
            "is_narrator": bool(character.is_narrator),
            "negative_lock": "different face, different hairstyle, different age, different body type, inconsistent outfit",
        }

    def _scene_asset(self, scene: Scene) -> Dict[str, Any]:
        visual_lock = self._join_non_empty([scene.setting, scene.description, scene.name])
        return {
            "id": scene.id,
            "name": scene.name,
            "description": scene.description or "",
            "setting": scene.setting or "",
            "visual_lock": visual_lock,
            "image_url": scene.image_url,
            "negative_lock": "modern buildings, unrelated location, inconsistent architecture, mismatched weather",
        }

    def _prop_asset(self, prop: Prop) -> Dict[str, Any]:
        visual_lock = self._join_non_empty([prop.appearance, prop.description, prop.name])
        return {
            "id": prop.id,
            "name": prop.name,
            "description": prop.description or "",
            "appearance": prop.appearance or "",
            "visual_lock": visual_lock,
            "image_url": prop.image_url,
            "negative_lock": "different shape, different material, inconsistent size, unrelated object",
        }

    def _build_indexes(self, asset_library: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "characters": self._index_assets(asset_library.get("characters", [])),
            "scenes": self._index_assets(asset_library.get("scenes", [])),
            "props": self._index_assets(asset_library.get("props", [])),
            "narrator": self._find_narrator(asset_library.get("characters", [])),
        }

    def _index_assets(self, assets: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_id = {str(asset.get("id")): asset for asset in assets if asset.get("id")}
        by_key = {}
        for asset in assets:
            key = self._normalize_key(asset.get("name", ""))
            if key:
                by_key[key] = asset
        return {"items": assets, "by_id": by_id, "by_key": by_key}

    def _find_narrator(self, characters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for character in characters:
            if character.get("is_narrator"):
                return character
        for character in characters:
            if self._normalize_key(character.get("name", "")) in {"narrator", "pangbai"}:
                return character
        return None

    # ------------------------------------------------------------------
    # Panel normalization
    # ------------------------------------------------------------------

    def _normalize_panel(
        self,
        panel: Dict[str, Any],
        order: int,
        indexes: Dict[str, Any],
        style_lock: str,
    ) -> Dict[str, Any]:
        panel_id = str(panel.get("panel_id") or panel.get("id") or f"panel_{order:03d}")
        warnings: List[str] = []

        character_matches = self._match_many(
            self._extract_character_candidates(panel),
            indexes["characters"],
            "character",
            panel_id,
            warnings,
        )
        scene_match = self._match_one(
            self._extract_scene_candidates(panel),
            indexes["scenes"],
            "scene",
            panel_id,
            warnings,
        )
        prop_matches = self._match_many(
            self._extract_prop_candidates(panel),
            indexes["props"],
            "prop",
            panel_id,
            warnings,
        )

        characters = [m["asset"]["name"] for m in character_matches if m.get("asset")]
        props = [m["asset"]["name"] for m in prop_matches if m.get("asset")]
        scene = scene_match["asset"]["name"] if scene_match and scene_match.get("asset") else ""

        action = self._extract_action(panel)
        camera = self._extract_camera(panel)
        lighting = self._extract_lighting(panel)
        composition = self._extract_composition(panel)
        dialogues = self._extract_dialogues(panel, indexes)
        duration = self._duration_for_panel(panel, dialogues)

        description = self._compose_description(
            scene_asset=scene_match.get("asset") if scene_match else None,
            character_assets=[m["asset"] for m in character_matches if m.get("asset")],
            prop_assets=[m["asset"] for m in prop_matches if m.get("asset")],
            action=action,
            camera=camera,
            lighting=lighting,
            composition=composition,
            style_lock=style_lock,
        )
        video_description = self._compose_video_description(
            scene_asset=scene_match.get("asset") if scene_match else None,
            character_assets=[m["asset"] for m in character_matches if m.get("asset")],
            prop_assets=[m["asset"] for m in prop_matches if m.get("asset")],
            action=action,
            camera=camera,
            dialogues=dialogues,
        )

        return {
            "panel_id": panel_id,
            "order": int(panel.get("order") or order),
            "characters": characters,
            "character_ids": [m["asset"]["id"] for m in character_matches if m.get("asset")],
            "scene": scene,
            "scene_id": scene_match["asset"]["id"] if scene_match and scene_match.get("asset") else None,
            "props": props,
            "prop_ids": [m["asset"]["id"] for m in prop_matches if m.get("asset")],
            "duration": duration,
            "dialogues": dialogues,
            "description": description,
            "video_description": video_description,
            "mapping": {
                "characters": character_matches,
                "scene": scene_match,
                "props": prop_matches,
            },
            "warnings": warnings,
        }

    def _match_one(
        self,
        candidates: List[str],
        index: Dict[str, Any],
        asset_type: str,
        panel_id: str,
        warnings: List[str],
    ) -> Optional[Dict[str, Any]]:
        for candidate in candidates:
            matched = self._match_asset(candidate, index)
            if matched:
                return matched
        if candidates:
            warnings.append(
                f"{panel_id}: no {asset_type} library match for {self._dedupe(candidates)}"
            )
        return None

    def _match_many(
        self,
        candidates: List[str],
        index: Dict[str, Any],
        asset_type: str,
        panel_id: str,
        warnings: List[str],
    ) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        seen_ids = set()
        unmatched = []
        for candidate in self._dedupe(candidates):
            matched = self._match_asset(candidate, index)
            if not matched:
                unmatched.append(candidate)
                continue
            asset_id = matched["asset"]["id"]
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            matches.append(matched)

        if unmatched:
            warnings.append(f"{panel_id}: no {asset_type} library match for {unmatched}")
        return matches

    def _match_asset(self, candidate: Any, index: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raw = self._stringify_candidate(candidate)
        if not raw:
            return None

        if raw in index["by_id"]:
            return {"input": raw, "matched": index["by_id"][raw]["name"], "asset": index["by_id"][raw], "score": 1.0}

        key = self._normalize_key(raw)
        if key in index["by_key"]:
            asset = index["by_key"][key]
            return {"input": raw, "matched": asset["name"], "asset": asset, "score": 1.0}

        best_asset = None
        best_score = 0.0
        for asset in index["items"]:
            asset_key = self._normalize_key(asset.get("name", ""))
            if not asset_key:
                continue
            if key and (key in asset_key or asset_key in key):
                score = 0.94
            else:
                score = SequenceMatcher(None, key, asset_key).ratio()
            if score > best_score:
                best_score = score
                best_asset = asset

        if best_asset and best_score >= 0.82:
            return {"input": raw, "matched": best_asset["name"], "asset": best_asset, "score": round(best_score, 3)}
        return None

    # ------------------------------------------------------------------
    # Prompt composition
    # ------------------------------------------------------------------

    def _compose_description(
        self,
        scene_asset: Optional[Dict[str, Any]],
        character_assets: List[Dict[str, Any]],
        prop_assets: List[Dict[str, Any]],
        action: str,
        camera: str,
        lighting: str,
        composition: str,
        style_lock: str,
    ) -> str:
        scene_text = scene_asset["visual_lock"] if scene_asset else "unmapped scene, keep environment simple and coherent"
        lines = [
            f"Scene: {scene_text}, {lighting}, {camera}, {composition}, {style_lock}.",
        ]

        if character_assets:
            lines.append("Characters:")
            for asset in character_assets:
                lines.append(
                    f"- {asset['name']}: keep canonical identity: {asset['visual_lock']}. "
                    "Do not change age, face, hairstyle, body type, or outfit silhouette."
                )
        else:
            lines.append("Characters: none visible.")

        if prop_assets:
            lines.append("Props:")
            for asset in prop_assets:
                lines.append(f"- {asset['name']}: keep canonical prop design: {asset['visual_lock']}.")

        lines.append(f"Action: {action}.")

        negative_parts = []
        for asset in character_assets + prop_assets:
            if asset.get("negative_lock"):
                negative_parts.append(asset["negative_lock"])
        if scene_asset and scene_asset.get("negative_lock"):
            negative_parts.append(scene_asset["negative_lock"])
        if negative_parts:
            lines.append(f"Identity negative lock: {', '.join(self._dedupe(negative_parts))}.")

        return "\n".join(lines)

    def _compose_video_description(
        self,
        scene_asset: Optional[Dict[str, Any]],
        character_assets: List[Dict[str, Any]],
        prop_assets: List[Dict[str, Any]],
        action: str,
        camera: str,
        dialogues: List[Dict[str, Any]],
    ) -> str:
        lines = ["Character Constraints:"]
        if character_assets:
            for asset in character_assets:
                lines.append(
                    f"- {asset['name']}: keep canonical identity: {asset['visual_lock']}. "
                    "Do not change age, face, hair, body type, outfit silhouette, or identity."
                )
        else:
            lines.append("- NONE")

        if scene_asset:
            lines.append("Scene Constraints:")
            lines.append(f"- {scene_asset['name']}: keep canonical environment: {scene_asset['visual_lock']}.")

        if prop_assets:
            lines.append("Prop Constraints:")
            for asset in prop_assets:
                lines.append(f"- {asset['name']}: keep canonical object design: {asset['visual_lock']}.")

        lines.append(f"Motion: start from the panel composition -> {action} -> {camera} -> end in a stable readable frame.")

        spoken = [d for d in dialogues if d.get("type") == "character" and d.get("text")]
        if spoken:
            lines.append("Dialogue (spoken, no on-screen text):")
            for dialogue in spoken:
                lines.append(f"- {dialogue.get('character_name', 'Unknown')}: \"{dialogue.get('text', '')}\"")
        else:
            lines.append("Dialogue (spoken, no on-screen text): NONE")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_character_candidates(self, panel: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for key in ("character_ids", "characters", "appearing_characters", "characters_present"):
            candidates.extend(self._names_from_value(panel.get(key)))
        candidates.extend(self._names_from_value(self._get_path(panel, ["source_mapping", "character_ids"])))
        return self._dedupe(candidates)

    def _extract_scene_candidates(self, panel: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for key in ("scene_id", "scene", "location"):
            candidates.extend(self._names_from_value(panel.get(key)))
        candidates.extend(self._names_from_value(self._get_path(panel, ["location", "name"])))
        candidates.extend(self._names_from_value(self._get_path(panel, ["environment", "location_name"])))
        candidates.extend(self._names_from_value(self._get_path(panel, ["environment", "name"])))
        candidates.extend(self._names_from_value(self._get_path(panel, ["environment", "setting"])))
        return self._dedupe(candidates)

    def _extract_prop_candidates(self, panel: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for key in ("prop_ids", "props", "objects", "items"):
            candidates.extend(self._names_from_value(panel.get(key)))
        candidates.extend(self._names_from_value(self._get_path(panel, ["environment", "props"])))
        candidates.extend(self._names_from_value(self._get_path(panel, ["environment", "key_props"])))
        candidates.extend(self._names_from_value(self._get_path(panel, ["visual", "priority", "must_show"])))
        return self._dedupe(candidates)

    def _extract_action(self, panel: Dict[str, Any]) -> str:
        parts = [
            panel.get("action"),
            panel.get("beat_summary"),
            self._get_path(panel, ["panel_role", "narrative_function"]),
            self._get_path(panel, ["studio_decision", "reason"]),
            self._get_path(panel, ["camera", "composition", "focus_point"]),
        ]
        return self._first_text(parts) or "characters hold the described storyboard moment with readable body language"

    def _extract_camera(self, panel: Dict[str, Any]) -> str:
        camera = panel.get("camera") if isinstance(panel.get("camera"), dict) else {}
        geometry = panel.get("geometry") if isinstance(panel.get("geometry"), dict) else {}
        parts = [
            camera.get("shot_size"),
            camera.get("angle"),
            camera.get("view_direction"),
            geometry.get("aspect_ratio"),
            geometry.get("layout_hint"),
        ]
        return self._join_non_empty(parts) or "medium shot, three-quarter angle, stable cinematic framing"

    def _extract_lighting(self, panel: Dict[str, Any]) -> str:
        environment = panel.get("environment") if isinstance(panel.get("environment"), dict) else {}
        lighting = environment.get("lighting") if isinstance(environment.get("lighting"), dict) else environment.get("lighting")
        if isinstance(lighting, dict):
            return self._join_non_empty(lighting.values())
        return self._stringify_candidate(lighting) or "consistent scene lighting"

    def _extract_composition(self, panel: Dict[str, Any]) -> str:
        camera = panel.get("camera") if isinstance(panel.get("camera"), dict) else {}
        composition = camera.get("composition") if isinstance(camera.get("composition"), dict) else {}
        parts = [
            composition.get("rule_applied"),
            composition.get("framing_description"),
            composition.get("reading_flow"),
        ]
        return self._join_non_empty(parts) or "clear foreground, midground, and background separation"

    def _extract_dialogues(self, panel: Dict[str, Any], indexes: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_items: List[Dict[str, Any]] = []
        for key in ("dialogues", "dialogue"):
            value = panel.get(key)
            if isinstance(value, list):
                raw_items.extend([item for item in value if isinstance(item, dict)])

        text_elements = panel.get("text_elements")
        if text_elements:
            raw_items.extend(self._walk_text_items(text_elements))

        dialogues: List[Dict[str, Any]] = []
        narrator = indexes.get("narrator")
        narrator_name = narrator["name"] if narrator else "Narrator"

        for item in raw_items:
            text = self._first_text([item.get("text"), item.get("content"), item.get("caption")])
            if not text:
                continue
            delivery = self._normalize_key(item.get("delivery") or item.get("type") or item.get("text_type") or "")
            is_narration = delivery in {"narration", "narrationbox", "caption", "internal", "thoughtbubble"}
            speaker_raw = self._first_text([item.get("speaker"), item.get("character_name"), item.get("character_id")])
            speaker_match = self._match_asset(speaker_raw, indexes["characters"]) if speaker_raw else None
            character_name = speaker_match["asset"]["name"] if speaker_match else (narrator_name if is_narration else speaker_raw or "Unknown")
            dialogues.append(
                {
                    "type": "narration" if is_narration else "character",
                    "order": len(dialogues),
                    "character_name": character_name,
                    "text": text,
                    "emotion_prompt": item.get("emotion_prompt") or item.get("emotion") or item.get("intent") or "",
                }
            )
        return dialogues

    def _walk_text_items(self, value: Any) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if isinstance(value, dict):
            if any(k in value for k in ("text", "content", "caption")):
                items.append(value)
            for child in value.values():
                items.extend(self._walk_text_items(child))
        elif isinstance(value, list):
            for child in value:
                items.extend(self._walk_text_items(child))
        return items

    def _duration_for_panel(self, panel: Dict[str, Any], dialogues: List[Dict[str, Any]]) -> int:
        geometry = panel.get("geometry") if isinstance(panel.get("geometry"), dict) else {}
        role = panel.get("panel_role") if isinstance(panel.get("panel_role"), dict) else {}
        size_weight = geometry.get("size_weight")
        try:
            weight = int(size_weight)
        except (TypeError, ValueError):
            weight = 2

        duration = {1: 3, 2: 4, 3: 5, 4: 6, 5: 8}.get(max(1, min(weight, 5)), 4)
        primary_role = self._normalize_key(role.get("primary", ""))
        if dialogues:
            duration = max(duration, 6)
        if primary_role in {"actionpeak", "impactshot", "closingpanel"}:
            duration = max(duration, 6)
        return duration

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _get_panels_from_chapter(self, chapter: Chapter) -> List[Dict[str, Any]]:
        parsed = self._read_parsed_data(chapter)
        storyboard = parsed.get("storyboard") if isinstance(parsed.get("storyboard"), dict) else {}

        for key in ("normalized_panels", "panels"):
            panels = storyboard.get(key)
            if isinstance(panels, list):
                return panels

        panels = parsed.get("panels")
        if isinstance(panels, list):
            return panels

        panel_chunks = storyboard.get("panel_chunks")
        if isinstance(panel_chunks, list):
            flattened: List[Dict[str, Any]] = []
            for chunk in panel_chunks:
                if isinstance(chunk, list):
                    flattened.extend([p for p in chunk if isinstance(p, dict)])
                elif isinstance(chunk, dict) and isinstance(chunk.get("panels"), list):
                    flattened.extend([p for p in chunk["panels"] if isinstance(p, dict)])
            return flattened

        return []

    def _store_storyboard_data(self, chapter: Chapter, updates: Dict[str, Any]) -> None:
        parsed = self._read_parsed_data(chapter)
        storyboard = parsed.get("storyboard")
        if not isinstance(storyboard, dict):
            storyboard = {}
        storyboard.update(updates)
        parsed["storyboard"] = storyboard
        chapter.parsed_data = json.dumps(parsed, ensure_ascii=False)
        self.db.commit()

    def _read_parsed_data(self, chapter: Chapter) -> Dict[str, Any]:
        if not chapter.parsed_data:
            return {}
        if isinstance(chapter.parsed_data, dict):
            return chapter.parsed_data
        try:
            data = json.loads(chapter.parsed_data)
            return data if isinstance(data, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _compact_asset_library(self, asset_library: Dict[str, Any]) -> Dict[str, Any]:
        compact = {}
        for key, assets in asset_library.items():
            compact[key] = [
                {
                    "id": asset.get("id"),
                    "name": asset.get("name"),
                    "has_image": bool(asset.get("image_url")),
                }
                for asset in assets
            ]
        return compact

    def _compact_asset_library_for_prompt(self, asset_library: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "characters": [
                {
                    "name": asset.get("name"),
                    "description": asset.get("description", ""),
                    "visual_lock": asset.get("visual_lock", ""),
                    "is_narrator": asset.get("is_narrator", False),
                }
                for asset in asset_library.get("characters", [])
            ],
            "scenes": [
                {
                    "name": asset.get("name"),
                    "description": asset.get("description", ""),
                    "visual_lock": asset.get("visual_lock", ""),
                }
                for asset in asset_library.get("scenes", [])
            ],
            "props": [
                {
                    "name": asset.get("name"),
                    "description": asset.get("description", ""),
                    "visual_lock": asset.get("visual_lock", ""),
                }
                for asset in asset_library.get("props", [])
            ],
        }

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _get_path(self, data: Dict[str, Any], path: List[str]) -> Any:
        current: Any = data
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def _names_from_value(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, dict):
            for key in ("name", "id", "character_id", "scene_id", "prop_id", "label", "title"):
                if value.get(key):
                    return [str(value[key])]
            return []
        if isinstance(value, Iterable):
            names: List[str] = []
            for item in value:
                names.extend(self._names_from_value(item))
            return names
        return [str(value)]

    def _stringify_candidate(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("name", "id", "character_id", "scene_id", "prop_id", "label", "title", "text"):
                if value.get(key):
                    return str(value[key]).strip()
            return ""
        return str(value).strip()

    def _normalize_key(self, value: Any) -> str:
        text = self._stringify_candidate(value).lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)
        return text

    def _first_text(self, values: Iterable[Any]) -> str:
        for value in values:
            text = self._stringify_candidate(value)
            if text:
                return text
        return ""

    def _join_non_empty(self, values: Iterable[Any], sep: str = ", ") -> str:
        return sep.join(self._dedupe([self._stringify_candidate(v) for v in values if self._stringify_candidate(v)]))

    def _dedupe(self, values: Iterable[Any]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            text = self._stringify_candidate(value)
            key = self._normalize_key(text)
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result
