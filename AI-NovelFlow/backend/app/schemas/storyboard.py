"""Schemas for storyboard normalization and panel-to-shot conversion."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StoryboardNormalizeRequest(BaseModel):
    """Normalize external storyboard panels against NovelFlow asset libraries."""

    panels: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Raw panel objects. If omitted, panels are read from chapter.parsed_data.",
    )
    style: Optional[str] = Field(
        None,
        description="Optional style lock injected into generated shot prompts.",
    )
    store: bool = Field(
        True,
        description="Whether to store normalized panels under chapter.parsed_data.storyboard.",
    )
    create_shots: bool = Field(
        False,
        description="Whether to immediately create Shot rows from normalized panels.",
    )
    overwrite_shots: bool = Field(
        False,
        description="When create_shots is true, delete existing chapter shots first.",
    )


class StoryboardConvertRequest(BaseModel):
    """Convert normalized or raw panels into NovelFlow Shot rows."""

    panels: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Panel objects. If omitted, stored normalized panels are used.",
    )
    style: Optional[str] = Field(
        None,
        description="Optional style lock injected into generated shot prompts.",
    )
    overwrite: bool = Field(
        True,
        description="Delete existing chapter shots before creating storyboard shots.",
    )
    store: bool = Field(
        True,
        description="Whether to store normalized panels and conversion metadata.",
    )


class StoryboardGeneratePanelsRequest(BaseModel):
    """Generate storyboard panels from enriched chapter segments."""

    style: Optional[str] = Field(
        None,
        description="Optional style lock injected into generated panel prompts.",
    )
    store: bool = Field(
        True,
        description="Whether to store generated panels under chapter.parsed_data.storyboard.",
    )
    create_shots: bool = Field(
        False,
        description="Whether to immediately normalize panels and create Shot rows.",
    )
    overwrite_shots: bool = Field(
        True,
        description="When create_shots is true, delete existing chapter shots first.",
    )
    max_segments: int = Field(
        80,
        ge=1,
        le=160,
        description="Maximum enriched segments to convert into panels in one request.",
    )


class SegmentSplitRequest(BaseModel):
    """Split raw chapter text into visual/cinematic segments."""

    overwrite: bool = Field(
        True,
        description="Replace existing stored segments for this chapter.",
    )


class SegmentEnrichRequest(BaseModel):
    """Enrich stored raw segments with storyboard planning attributes."""

    overwrite: bool = Field(
        True,
        description="Replace existing enriched segment metadata for this chapter.",
    )


class VisualAssetExtractRequest(BaseModel):
    """Extract a production-ready visual asset library from chapter text."""

    store: bool = Field(
        True,
        description="Whether to create/update Character, Scene, and Prop rows.",
    )
    update_existing: bool = Field(
        True,
        description="Whether to enrich existing assets when the new description is stronger.",
    )
    max_characters: int = Field(
        16,
        ge=1,
        le=60,
        description="Upper bound for character candidates requested from the LLM.",
    )
    max_scenes: int = Field(
        20,
        ge=1,
        le=80,
        description="Upper bound for scene candidates requested from the LLM.",
    )
    max_props: int = Field(
        40,
        ge=1,
        le=120,
        description="Upper bound for prop and set-dressing candidates requested from the LLM.",
    )
    ensure_segments: bool = Field(
        True,
        description="Create stored story segments first when they do not exist.",
    )
    enrich_segments: bool = Field(
        True,
        description="Create enriched segment metadata before asset extraction.",
    )
