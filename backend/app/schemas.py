from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TargetDefinition(BaseModel):
    gene: str | None = None
    disease_context: str | None = None
    species: str = "human"
    brain_region: str | None = None
    cell_type: str | None = None
    modality: Literal["AAV", "mRNA", "plasmid", "other"] = "AAV"


class ScoreSettings(BaseModel):
    gc_min: float = Field(default=0.40, ge=0.0, le=1.0)
    gc_max: float = Field(default=0.65, ge=0.0, le=1.0)
    target_gc: float = Field(default=0.55, ge=0.0, le=1.0)
    aav_payload_limit_nt: int = Field(default=4300, gt=0)
    forbidden_motifs: list[str] = Field(default_factory=lambda: ["AATAAA", "ATTAAA", "GGGGGG", "CCCCCC", "TTTTTT", "AAAAAA"])
    polyadenylation_signals: list[str] = Field(default_factory=lambda: ["AATAAA", "ATTAAA", "AGTAAA", "TATAAA", "CATAAA", "GATAAA"])
    restriction_sites: list[str] = Field(default_factory=lambda: ["GAATTC", "GGATCC", "AAGCTT", "GCGGCCGC", "TCTAGA", "ACTAGT"])
    cryptic_splice_motifs: list[str] = Field(default_factory=lambda: ["CAGG", "GTAGT", "GTAAGT", "TTTTCAG"])
    splice_donor_motifs: list[str] = Field(default_factory=lambda: ["GTAAGT", "GTGAGT", "GTATGT"])
    splice_acceptor_motifs: list[str] = Field(default_factory=lambda: ["TTTTCAG", "CTTTCAG", "TCTTCAG"])
    gc_window_size_nt: int = Field(default=60, ge=9, le=300)
    codon_weight_multipliers: dict[str, float] = Field(default_factory=dict)
    codon_availability_weights: dict[str, float] = Field(default_factory=dict)


class OptimizationSettings(BaseModel):
    population_size: int = Field(default=48, ge=8, le=500)
    generations: int = Field(default=24, ge=1, le=1000)
    mutation_rate: float = Field(default=0.04, ge=0.0, le=1.0)
    crossover_rate: float = Field(default=0.80, ge=0.0, le=1.0)
    seed: int = 42
    max_candidates: int = Field(default=8, ge=1, le=50)
    enable_repair: bool = True
    repair_passes: int = Field(default=3, ge=0, le=12)
    score_settings: ScoreSettings = Field(default_factory=ScoreSettings)


class ScoreRequest(BaseModel):
    cds: str
    score_settings: ScoreSettings = Field(default_factory=ScoreSettings)


class OptimizeRequest(BaseModel):
    cds: str
    target: TargetDefinition = Field(default_factory=TargetDefinition)
    optimization_settings: OptimizationSettings = Field(default_factory=OptimizationSettings)


class GeneRequest(BaseModel):
    gene: str
    species: str = "human"


class DesignFromGeneRequest(BaseModel):
    gene: str
    species: str = "human"
    target: TargetDefinition = Field(default_factory=TargetDefinition)
    optimization_settings: OptimizationSettings = Field(default_factory=OptimizationSettings)


class BatchDesignFromGenesRequest(BaseModel):
    genes: list[str] = Field(min_length=1, max_length=50)
    species: str = "human"
    target: TargetDefinition = Field(default_factory=TargetDefinition)
    optimization_settings: OptimizationSettings = Field(default_factory=OptimizationSettings)
    continue_on_error: bool = True


class EvidenceRequest(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=5, ge=1, le=20)


class RagSearchRequest(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=6, ge=1, le=50)


class IngestLocalDocumentRequest(BaseModel):
    source_path: str
    title: str | None = None
    collection: str = "project_document"
    source: str | None = None
    source_url: str | None = None
    evidence_class: str = "project_document"
    confidence: str = "medium"
    species: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    cell_types: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    copy_source: bool = False
    rebuild_index: bool = True


class StructuredImportRequest(BaseModel):
    source_path: str
    rebuild_index: bool = True


class GtexImportRequest(BaseModel):
    gene: str
    brain_regions: list[str] = Field(default_factory=lambda: ["substantia nigra", "striatum", "cortex", "hippocampus"])
    dataset_id: str = "gtex_v8"
    rebuild_index: bool = True


class AllenTaxonomyImportRequest(BaseModel):
    query_terms: list[str] = Field(default_factory=list)
    max_records: int = Field(default=250, ge=1, le=5000)
    rebuild_index: bool = True


class DataRefreshRequest(BaseModel):
    genes: list[str] = Field(default_factory=list)
    brain_regions: list[str] = Field(default_factory=list)
    include_gtex: bool = True
    include_allen: bool = True
    allen_query_terms: list[str] = Field(default_factory=list)
    max_allen_records: int = Field(default=250, ge=1, le=5000)
    dataset_id: str = "gtex_v8"
    rebuild_index: bool = True
    dry_run: bool = False


class ApiResponse(BaseModel):
    data: dict[str, Any]
