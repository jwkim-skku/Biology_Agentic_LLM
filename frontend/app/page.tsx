"use client";

import {
  AlertTriangle,
  BarChart3,
  BookOpenText,
  CheckCircle2,
  Database,
  Dna,
  Download,
  ExternalLink,
  Gauge,
  Loader2,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  ClipboardCheck
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

type Score = {
  cai: number;
  gc_fraction: number;
  gc_penalty: number;
  cpg_count: number;
  cpg_density_per_100nt: number;
  motif_violations: number;
  polyadenylation_signal_count: number;
  restriction_site_count: number;
  cryptic_splice_motif_count: number;
  splice_donor_motif_count: number;
  splice_acceptor_motif_count: number;
  sequence_policy_violation_score: number;
  longest_homopolymer: number;
  gc_window_min: number;
  gc_window_max: number;
  gc_window_max_deviation: number;
  tissue_codon_adaptation: number;
  rare_codon_clusters: number;
  codon_pair_risk: number;
  five_prime_gc_fraction: number;
  five_prime_gc_deviation: number;
  hairpin_proxy_score: number;
  secondary_structure_proxy_score: number;
  mfe_proxy_delta_g: number;
  sequence_complexity: number;
  low_complexity_penalty: number;
  length_nt: number;
  aav_budget_pass: boolean;
  composite_quality: number;
};

type Candidate = {
  candidate_id: string;
  cds: string;
  protein: string;
  scores: Score;
  rank: number;
  selection_trace?: string[];
  constraint_risk?: {
    status: string;
    finding_count: number;
    fail_count: number;
    warning_count: number;
    findings: Array<{
      key: string;
      severity: string;
      value: string | number | boolean | null;
      message: string;
    }>;
  };
};

type EvidenceRecord = {
  id: string;
  title: string;
  collection: string;
  source: string;
  source_url: string;
  evidence_class: string;
  confidence: string;
  summary: string;
  retrieval?: {
    score: number;
    vector_score: number;
    bm25_score?: number;
    rerank_score: number;
    facet_score?: number;
    field_match_score?: number;
    source_priority_score?: number;
    rank_evidence_hash?: string;
    embedding_model: string;
    retrieval_model?: string;
  };
};

type EvidenceSummary = {
  coverage: Record<string, string>;
  retrieval?: {
    index_version: string;
    embedding_model: string;
    retrieval_model?: string;
    embedding_dimensions: number;
  };
  records: EvidenceRecord[];
};

type QcRule = {
  statement: string;
  confidence: string;
  citations: string[];
};

type CandidateDiagnostics = {
  candidate_count: number;
  feasible_count: number;
  recommended_candidate_id?: string;
  recommended_rank?: number;
  selection_policy?: string;
  constraint_risk_summary?: {
    status_counts: Record<string, number>;
    top_findings: Array<{ key: string; count: number }>;
  };
  pareto_front?: {
    size: number;
    candidate_ids: string[];
  };
  pareto_quality?: {
    quality_schema?: string;
    quality_hash?: string;
    front_size?: number;
    feasible_front_count?: number;
    recommended_on_front?: boolean;
    approx_hypervolume_2d?: number;
  };
  diversity?: {
    unique_cds_count: number;
    mean_pairwise_codon_distance: number;
    min_pairwise_codon_distance: number;
    max_pairwise_codon_distance: number;
  };
  best_by_metric?: Record<string, { candidate_id: string; rank: number; value: number } | null>;
  recommendation_audit?: RecommendationAudit;
};

type RecommendationAudit = {
  audit_schema: string;
  recommended_candidate_id?: string | null;
  recommended_rank?: number | null;
  selection_policy: string;
  hard_constraint_status: string;
  is_feasible: boolean;
  pareto_front_member: boolean;
  pareto_quality_hash?: string | null;
  best_metric_count: number;
  tradeoff_count: number;
  max_regret: number;
  primary_tradeoff?: {
    metric: string;
    direction: string;
    recommended_value: number;
    best_value: number;
    best_candidate_id?: string | null;
    regret: number;
    is_metric_best: boolean;
  } | null;
  tradeoffs: Array<{
    metric: string;
    direction: string;
    recommended_value: number;
    best_value: number;
    best_candidate_id?: string | null;
    regret: number;
    is_metric_best: boolean;
  }>;
};

type RecommendedFoldingEvidence = {
  folding_schema?: string;
  status?: string;
  requested_backend?: string;
  active_backend?: string;
  fallback_active?: boolean;
  input_length_nt?: number;
  evaluated_window_nt?: number;
  proxy?: {
    secondary_structure_proxy_score?: number;
    mfe_proxy_delta_g?: number;
  };
  rnafold?: {
    status?: string;
    algorithm?: string;
    sequence_length_nt?: number;
    structure?: string;
    mfe_delta_g?: number;
    message?: string;
  } | null;
  thermodynamic_mfe_delta_g?: number;
  thermodynamic_structure?: string;
  thermodynamic_risk_score?: number;
  folding_evidence_hash?: string;
  warnings?: string[];
};

type RecommendationReadiness = {
  readiness_schema?: string;
  recommended_candidate_id?: string | null;
  release_ready?: boolean;
  readiness_status?: string;
  blocking_reasons?: string[];
  warning_reasons?: string[];
  constraint_risk_status?: string | null;
  constraint_fail_count?: number | null;
  constraint_warning_count?: number | null;
  pareto_front_member?: boolean | null;
  pareto_quality_hash?: string | null;
  max_regret?: number | null;
  tradeoff_count?: number | null;
  folding_status?: string | null;
  folding_backend?: string | null;
  folding_fallback_active?: boolean | null;
  folding_evidence_hash?: string | null;
  data_quality_status?: string | null;
  data_quality_manifest_hash?: string | null;
  optimizer_stress_status?: string | null;
  optimizer_stress_cases_hash?: string | null;
  qc_gate_status?: string | null;
  validation_statuses?: string[];
  readiness_hash?: string | null;
};

type QcReport = {
  evidence_summary: {
    supported_rules: QcRule[];
    uncertain_rules: QcRule[];
    rejected_rules: QcRule[];
    retrieval_quality?: {
      quality_schema: string;
      status: string;
      record_count: number;
      source_count: number;
      collection_count: number;
      high_confidence_count: number;
      rank_evidence_count?: number | null;
      rank_evidence_hash?: string | null;
      retrieval_model?: string | null;
      embedding_model?: string | null;
      top_sources?: Array<{ source: string; records: number }>;
      top_collections?: Array<{ collection: string; records: number }>;
      score_range?: { min?: number | null; max?: number | null; mean?: number | null };
      facet_score_range?: { min?: number | null; max?: number | null; mean?: number | null };
    };
  };
  score_summary: {
    native: Score;
    recommended: Score;
    delta: Partial<Record<keyof Score, number>>;
  };
  recommended_candidate: {
    candidate_id: string;
    rationale: string[];
    selection_trace?: string[];
    constraint_risk?: Candidate["constraint_risk"];
    constraint_status: Record<string, string | number | boolean | null>;
  };
  candidate_diagnostics?: CandidateDiagnostics;
  recommendation_audit?: RecommendationAudit;
  optimizer_reproducibility?: {
    manifest_schema: string;
    manifest_hash: string;
    algorithm: string;
    seed: number;
    objective_inventory: string[];
    optimization_config_hash: string;
    score_config_hash: string;
    repair_policy: {
      enabled: boolean;
      repair_passes: number;
    };
    seed_strategy?: {
      version?: string;
      deterministic_seeds?: string[];
      stochastic_fill?: string;
    };
    search_budget: {
      population_size: number;
      generations: number;
      max_candidates: number;
    };
  };
  data_quality?: {
    quality_schema?: string;
    status?: string;
    manifest_hash?: string;
    record_count?: number;
    dataset_count?: number;
    live_record_fraction?: number;
    release_pinned_fraction?: number;
    blocking_count?: number;
    warning_count?: number;
    operator_actions?: string[];
  };
  target_structured_evidence?: {
    status?: string;
    coverage?: Record<string, string>;
    matched_record_count?: number;
    live_record_count?: number;
    seed_record_count?: number;
    release_pinned_record_count?: number;
    snapshot_record_count?: number;
    datasets?: string[];
    genes?: string[];
    brain_regions?: string[];
    cell_types?: string[];
    top_records?: Array<{
      id?: string;
      dataset?: string;
      release?: string;
      gene?: string | null;
      brain_region?: string | null;
      cell_type?: string | null;
      confidence?: string;
      match_score?: number;
      source_file?: string;
      source_payload_sha256?: string;
      source_snapshot_path?: string;
      is_live?: boolean;
      is_seed?: boolean;
      median_expression?: number;
      unit?: string;
      number_of_cells?: number;
    }>;
  };
  optimizer_stress?: {
    stress_schema?: string;
    status?: string;
    benchmark_status?: string;
    case_count?: number;
    cases_hash?: string;
    summary?: {
      pass_count?: number;
      warning_count?: number;
      fail_count?: number;
    };
    recommendations?: string[];
  };
  sequence_policy?: {
    native?: SequencePolicyAudit;
    recommended?: SequencePolicyAudit;
  };
  recommended_folding_evidence?: RecommendedFoldingEvidence;
  recommendation_readiness?: RecommendationReadiness;
  warnings: string[];
  open_questions: string[];
  qc_gate?: {
    status: string;
    errors: number;
    warnings: number;
    checks: Array<{
      id: string;
      result: string;
      severity: string;
      message: string;
    }>;
  };
};

type SequencePolicyAudit = {
  status: string;
  summary: Record<string, number>;
  findings: Array<{
    category: string;
    severity: string;
    motif: string;
    count: number;
    positions_1based: number[];
    recommended_action: string;
  }>;
};

type DesignResponse = {
  run_id: string;
  timestamp: string;
  pipeline_version: string;
  target: Record<string, string | null>;
  native: {
    cds: string;
    protein: string;
    scores: Score;
  };
  candidates: Candidate[];
  recommended_candidate: Candidate | null;
  candidate_diagnostics?: CandidateDiagnostics;
  recommendation_audit?: RecommendationAudit;
  recommended_folding_evidence?: RecommendedFoldingEvidence;
  warnings: string[];
  provenance: {
    input_source: string;
    codon_weight_source: string;
    structured_manifest_hash?: string;
    external_databases: string[];
    note: string;
  };
  trace?: Array<{
    name: string;
    status: string;
    timestamp: string;
    detail: Record<string, unknown>;
  }>;
  saved_run?: RunSummary;
  evidence?: EvidenceSummary;
  qc_report?: QcReport;
  source_cds?: {
    gene: {
      symbol: string;
      ensembl_gene_id: string;
      description: string;
      assembly_name: string;
      canonical_transcript: string;
    };
    selected_transcript: {
      id: string;
      versioned_id: string;
      display_name: string;
      selection_reason: string;
      mane_select?: {
        type: string;
        refseq_match: string;
        assembly_name: string;
      } | null;
    };
    cds_length_nt: number;
    protein_length_aa: number;
    provenance: Record<string, unknown>;
  };
};

type DataStatus = {
  records: number;
  datasets: Record<string, number>;
  structured_path: string;
  formats: string[];
  manifest_hash?: string;
  validation?: {
    errors: number;
    warnings: number;
  };
};

type StructuredManifest = {
  manifest_hash: string;
  files: Array<{
    file: string;
    path: string;
    sha256: string;
    bytes: number;
    records: number;
    datasets: string[];
    releases: string[];
  }>;
};

type StructuredImportPreview = {
  status: string;
  source: {
    file: string;
    path: string;
    sha256: string;
    bytes: number;
  };
  target: {
    would_replace: boolean;
    existing_records_replaced: number;
  };
  records: {
    import_count: number;
    current_count: number;
    projected_count: number;
    duplicate_import_ids: string[];
    colliding_existing_ids: string[];
  };
  manifest: {
    current_hash: string;
    projected_hash: string;
  };
  validation: {
    import: { error_count: number; warning_count: number };
    projected: { error_count: number; warning_count: number };
  };
};

type DataAudit = {
  status: string;
  checked_at: string;
  manifest_hash: string;
  external_source_coverage?: {
    tracked_records: number;
    source_snapshot_path_fraction: number;
    source_payload_hash_fraction: number;
    records_missing_snapshot: Array<{ id?: string; dataset?: string; source_file?: string }>;
    records_missing_payload_hash: Array<{ id?: string; dataset?: string; source_file?: string }>;
  };
  lockfile?: {
    status: string;
    current_hash?: string;
    locked_hash?: string | null;
    generated_at?: string | null;
    diff: Array<{ section: string; message: string }>;
  };
  release_lock?: {
    status: string;
    current_hash?: string;
    locked_hash?: string | null;
    generated_at?: string | null;
    summary?: {
      datasets: number;
      files: number;
      records: number;
      release_values: string[];
    };
    diff: Array<{ section: string; message: string }>;
  };
  checks: Array<{
    id: string;
    result: string;
    severity: string;
    message: string;
  }>;
};

type DataCatalog = {
  sources: Array<{
    id: string;
    name: string;
    release_default?: string;
    record_type: string;
    refreshable: boolean;
  }>;
  last_refresh?: DataRefreshEntry | null;
};

type DataCoverage = {
  coverage_schema: string;
  manifest_hash: string;
  record_count: number;
  gene_count: number;
  brain_region_count: number;
  cell_type_count: number;
  datasets: Array<{
    dataset: string;
    records: number;
    live_records: number;
    seed_records: number;
    snapshot_fraction: number;
    payload_hash_fraction: number;
    production_status: string;
  }>;
  gene_region_matrix: Array<{
    gene: string;
    brain_region: string;
    records: number;
    datasets: string[];
    live_records: number;
    seed_records: number;
    median_expression_max?: number | null;
  }>;
  cell_type_matrix: Array<{
    brain_region: string;
    cell_type: string;
    records: number;
    datasets: string[];
    live_records: number;
    seed_records: number;
    cell_count_max?: number | null;
  }>;
  production_readiness: {
    status: string;
    live_record_fraction: number;
    seed_record_fraction: number;
    records_with_snapshots_fraction: number;
    records_with_payload_hash_fraction: number;
    seed_record_count: number;
    live_record_count: number;
    operator_action: string;
  };
};

type DataQualityGate = {
  quality_schema: string;
  status: string;
  record_count: number;
  dataset_count: number;
  coverage: {
    live_record_fraction: number;
    release_pinned_fraction: number;
    gene_count?: number;
    brain_region_count?: number;
    cell_type_count?: number;
  };
  summary: {
    pass_count: number;
    warning_count: number;
    fail_count: number;
    blocking_count: number;
    warning_count_total: number;
  };
  datasets: Array<{
    dataset: string;
    status: string;
    records: number;
    live_records: number;
    seed_records: number;
    fractions: Record<string, number>;
    recommendation: string;
  }>;
  operator_actions: string[];
};

type DataRefreshEntry = {
  completed_at: string;
  refresh_status: string;
  manifest_hash?: string | null;
  summary?: {
    planned?: number;
    succeeded?: number;
    failed?: number;
    structured_records?: number;
    structured_files?: number;
    rag_chunks?: number;
    documents?: number;
  };
  operations?: Array<Record<string, unknown>>;
};

type DataRefreshLog = {
  log_path: string;
  entries: DataRefreshEntry[];
};

type DataRefreshValidation = {
  status: string;
  errors: string[];
  warnings: string[];
  normalized_request: {
    genes: string[];
    brain_regions: string[];
    include_gtex: boolean;
    include_allen: boolean;
    allen_query_terms: string[];
    max_allen_records: number;
    dataset_id: string;
  };
  plan: {
    operation_count: number;
    sources: string[];
  };
  current_data: {
    records?: number;
    manifest_hash?: string;
    structured_files?: number;
  };
  release_lock: {
    status?: string;
    current_hash?: string | null;
    locked_hash?: string | null;
  };
};

type ExternalSourceBackfillResult = {
  status: string;
  dry_run: boolean;
  candidate_file_count: number;
  candidate_record_count: number;
  backfilled_file_count: number;
  backfilled_record_count: number;
};

type GovernanceAttestationStatus = {
  status?: string;
  attestation_hash?: string | null;
  errors?: string[];
  warnings?: string[];
  signature?: {
    status?: string;
    messages?: string[];
  };
  artifact_verification?: {
    status?: string;
    manifest_hash?: string | null;
    file_count?: number;
    checked_files?: number;
  };
  summary?: {
    generated_at?: string;
    path_count?: number;
    data_status?: string;
    rag_chunks?: number;
    storage_backend?: string;
    ledger_status?: string;
    signing_status?: string;
  };
};

type StorageReadinessStatus = {
  status?: string;
  target_backend?: string;
  active_runtime_adapter?: string;
  database_url_configured?: boolean;
  sqlite?: {
    total_bytes?: number;
    files?: Array<{ name: string; exists: boolean; size_bytes: number }>;
  };
  postgres?: {
    schema_version?: string;
    schema_hash?: string;
    driver_available?: boolean;
  };
  migration_readiness?: {
    schema_available?: boolean;
    sqlite_source_files_present?: boolean;
    runtime_adapter_available?: boolean;
  };
  warnings?: string[];
};

type StorageMigrationStatus = {
  summary?: {
    status?: string;
    bytes?: number;
    manifest?: {
      total_records?: number;
      manifest_hash?: string;
      tables?: Array<{ table: string; records: number; source_exists: boolean }>;
    };
  };
  parity?: {
    status?: string;
    source?: {
      total_records?: number;
    };
    postgres?: {
      status?: string;
      database_url_configured?: boolean;
    };
    comparisons?: Array<{
      table: string;
      source_records: number;
      target_available: boolean;
      record_count_match: boolean;
      row_hash_match: boolean;
    }>;
  };
  import_plan?: {
    status?: string;
    dry_run?: boolean;
    total_records?: number;
    database_url_configured?: boolean;
    schema_hash?: string;
  };
};

type SecurityStatus = {
  auth_enabled: boolean;
  rbac_enabled: boolean;
  configured_keys: number;
  configured_role_bindings: number;
  roles: string[];
  api_key_header: string;
  bearer_auth_supported: boolean;
  rate_limit_per_minute: number;
  artifact_signing_enabled: boolean;
  artifact_signing_algorithm?: string | null;
  artifact_signing_key_id?: string | null;
  artifact_asymmetric_signing_enabled: boolean;
  artifact_asymmetric_verification_enabled: boolean;
  artifact_ed25519_key_id?: string | null;
  signing?: {
    status?: string;
    hmac?: {
      signing_enabled?: boolean;
      verification_enabled?: boolean;
      key_id?: string | null;
    };
    ed25519?: {
      signing_enabled?: boolean;
      verification_enabled?: boolean;
      key_id?: string | null;
    };
  };
  public_paths: string[];
  role_policy: Record<string, string>;
};

type MetricsSnapshot = {
  uptime_seconds: number;
  requests: Array<{
    method: string;
    path: string;
    count: number;
    avg_duration_ms: number;
    max_duration_ms: number;
    status_counts: Record<string, number>;
  }>;
  stores?: {
    jobs_observed: number;
    job_status_counts: Record<string, number>;
    runs_observed: number;
    artifacts_observed: number;
    artifact_bytes: number;
  };
  rag?: {
    chunks?: number;
    documents?: number;
    retrieval_model?: string;
  };
  structured?: {
    records?: number;
    manifest_hash?: string;
  };
  audit?: {
    total_events?: number;
    by_outcome?: Record<string, number>;
  };
  agent_memory?: {
    memory_schema?: string;
    memory_count?: number;
    distinct_genes?: number;
    latest_updated_at?: string | null;
  };
  data_provenance?: {
    status?: string;
    failed_checks?: number;
    lock_status?: string | null;
  };
};

type DeploymentReadinessStatus = {
  status: string;
  deployment_ready: boolean;
  production_ready: boolean;
  summary: {
    pass: number;
    warning: number;
    fail: number;
  };
  attention_gates?: string[];
  attention_gates_hash?: string;
  required_actions?: Array<{
    gate: string;
    status: string;
    priority: string;
    message: string;
    action: string;
    detail_hash?: string;
  }>;
  required_actions_hash?: string;
  gates: Array<{
    name: string;
    status: string;
    message: string;
    details?: Record<string, unknown>;
  }>;
};

type ProductionAuditStatus = {
  audit_hash?: string;
  evidence_hashes?: {
    hash_schema?: string;
    algorithm?: string;
    evidence_count?: number;
    combined_hash?: string;
    items?: Record<string, string>;
  };
  production_gap_summary?: {
    gap_schema?: string;
    status?: string;
    gap_count?: number;
    blocking_count?: number;
    promotion_count?: number;
    gap_summary_hash?: string;
    gaps?: Array<{
      area?: string;
      status?: string;
      priority?: string;
      action?: string;
      gap_hash?: string;
    }>;
  };
  generated_at?: string;
  cache_policy?: {
    ttl_seconds?: number;
    force_refresh_query?: string;
  };
  evidence?: {
    timings?: {
      total_seconds?: number;
      slowest?: Array<{ name: string; duration_seconds: number }>;
    };
    production_gap_summary?: ProductionAuditStatus["production_gap_summary"];
    artifact_object_store?: {
      status?: string;
      enabled?: boolean;
      configured?: boolean;
      bucket?: string | null;
      prefix?: string;
      region?: string;
      lifecycle_policy_hash?: string | null;
      mirror_plan?: {
        status?: string;
        candidate_count?: number;
        candidate_bytes?: number;
        limit?: number;
      };
    };
    qc_bundle_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        optimizer_manifest_hash?: string | null;
        request_hash?: string | null;
        qc_report_hash?: string | null;
        report_formats_summary_hash?: string | null;
        candidate_ranking_hash?: string | null;
        recommendation_audit_hash?: string | null;
        recommendation_readiness_hash?: string | null;
        recommendation_readiness_status?: string | null;
        recommendation_release_ready?: boolean | null;
        recommendation_readiness_candidate_id?: string | null;
        recommended_folding_evidence_hash?: string | null;
        recommended_folding_status?: string | null;
        recommended_folding_backend?: string | null;
        recommended_folding_fallback_active?: boolean | null;
        recommended_folding_candidate_id?: string | null;
        data_quality_status?: string | null;
        optimizer_stress_status?: string | null;
        objective_count?: number | null;
        retrieval_quality_status?: string | null;
        retrieval_quality_record_count?: number | null;
        retrieval_quality_source_count?: number | null;
        retrieval_quality_collection_count?: number | null;
        retrieval_quality_high_confidence_count?: number | null;
        retrieval_quality_rank_evidence_count?: number | null;
        retrieval_quality_rank_evidence_hash?: string | null;
        retrieval_model?: string | null;
        embedding_model?: string | null;
        checked_files?: number;
        file_count?: number;
      }>;
    };
    data_release_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        promotion_status?: string | null;
        record_count?: number | null;
        records_hash?: string | null;
        records_csv_hash?: string | null;
        record_source_summary_hash?: string | null;
        dataset_count?: number | null;
        release_count?: number | null;
        source_file_count?: number | null;
        release_handoff_hash?: string | null;
        structured_manifest_hash?: string | null;
        rag_structured_manifest_hash?: string | null;
        rag_index_hash?: string | null;
        trna_caveat_count?: number | null;
        trna_blocking_production_use?: boolean | null;
        external_snapshot_referenced_count?: number | null;
        external_snapshot_contained_count?: number | null;
        external_snapshot_missing_count?: number | null;
      }>;
    };
    data_snapshot_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        snapshot_manifest_hash?: string | null;
        structured_manifest_hash?: string | null;
        rag_index_hash?: string | null;
        external_snapshot_file_count?: number | null;
        snapshot_file_count?: number | null;
      }>;
    };
    data_refresh_plan_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        operation_count?: number | null;
        request_hash?: string | null;
        operations_hash?: string | null;
        data_catalog_hash?: string | null;
        external_sources_hash?: string | null;
        structured_quality_hash?: string | null;
        data_provenance_hash?: string | null;
        rag_status_hash?: string | null;
        validation_status?: string | null;
        structured_manifest_hash?: string | null;
        release_lock_status?: string | null;
        dataset_id?: string | null;
      }>;
    };
    structured_import_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        structured_manifest_hash?: string | null;
        validation_error_count?: number | null;
        validation_warning_count?: number | null;
      }>;
    };
    rag_vector_index_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        chunk_count?: number | null;
        embedding_dimensions?: number | null;
        embedding_model?: string | null;
        retrieval_model?: string | null;
        recommended_backend?: string | null;
        migration_target_backend?: string | null;
        parity_status?: string | null;
        vector_row_hash?: string | null;
        structured_manifest_hash?: string | null;
      }>;
    };
    rag_evaluation_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        source_provenance_hash?: string | null;
        source_provenance_count?: number | null;
        source_payload_hash_count?: number | null;
        source_snapshot_count?: number | null;
      }>;
    };
    rag_regression_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        cases_hash?: string | null;
        results_hash?: string | null;
        quality_summary_hash?: string | null;
        case_metrics_hash?: string | null;
        quality_status?: string | null;
        source_provenance_summary_hash?: string | null;
        source_provenance_case_count?: number | null;
        source_snapshot_case_count?: number | null;
        top_source_count?: number | null;
        missing_term_case_count?: number | null;
      }>;
    };
    optimizer_benchmark_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        benchmark_status?: string | null;
        diagnostics_status?: string | null;
        stress_status?: string | null;
        case_count?: number | null;
        results_hash?: string | null;
        benchmark_hash?: string | null;
        candidate_diagnostics_hash?: string | null;
        recommendation_summary_hash?: string | null;
        recommendation_summary_status?: string | null;
        recommended_on_pareto_front_count?: number | null;
        recommendation_max_regret?: number | null;
        case_provenance_hash?: string | null;
        case_fingerprint_count?: number | null;
        recommended_folding_evidence_hash?: string | null;
        recommended_folding_evidence_count?: number | null;
        recommended_folding_candidate_match_count?: number | null;
      }>;
    };
    workflow_trace_archive_semantics?: ArchiveSemanticFreshness & {
      status: string;
      checked_count: number;
      semantic_pass_count: number;
      semantic_warning_count: number;
      semantic_fail_count: number;
      latest_artifacts?: Array<{
        artifact_id: string;
        semantic_status?: string;
        workflow_id?: string | null;
        run_id?: string | null;
        task_type?: string | null;
        trace_hash?: string | null;
        trace_step_count?: number | null;
        structured_manifest_hash?: string | null;
      }>;
    };
  };
  summary?: {
    status: string;
    deployment_ready: boolean;
    production_ready: boolean;
    counts: {
      pass: number;
      warning: number;
      fail: number;
    };
    blocking_checks: string[];
    warning_checks: string[];
  };
  verification?: {
    status?: string;
    audit_hash?: string | null;
    semantic_checks?: Record<string, string>;
    artifact_verification?: {
      file_count?: number;
      checked_files?: number;
    };
    signature?: {
      status?: string;
    };
  };
};

type RunSummary = {
  run_id: string;
  run_type: string;
  updated_at: string;
  gene: string | null;
  brain_region: string | null;
  cell_type: string | null;
  modality: string | null;
  recommended_candidate_id: string | null;
  composite_quality: number | null;
  structured_manifest_hash: string | null;
};

type AgentMemorySummary = {
  memory_schema?: string;
  run_id: string;
  run_type: string;
  updated_at: string;
  gene: string | null;
  brain_region: string | null;
  cell_type: string | null;
  modality: string | null;
  recommended_candidate_id: string | null;
  memory_hash?: string | null;
};

type AgentMemoryDetail = AgentMemorySummary & {
  session_memory?: {
    workflow_id?: string | null;
    trace_steps?: string[];
    target?: Record<string, string | null | undefined>;
  };
  semantic_memory?: {
    supported_rules?: string[];
    uncertain_rules?: string[];
    rejected_rules?: string[];
    open_questions?: string[];
    coverage?: Record<string, unknown>;
    candidate_diagnostics?: {
      feasible_count?: number;
      candidate_count?: number;
      selection_policy?: string;
      recommendation_audit?: {
        schema?: string;
        selected_candidate_id?: string;
        best_objective_count?: number;
        max_regret?: number;
        audit_hash?: string;
      };
    };
  };
  artifact_memory?: {
    request_hash?: string;
    design_hash?: string;
    trace_hash?: string;
    optimizer_manifest_hash?: string | null;
    structured_manifest_hash?: string | null;
    recommended_scores?: {
      composite_quality?: number;
      cai?: number;
      tissue_codon_adaptation?: number;
      constraint_risk?: string;
      secondary_structure_proxy_score?: number;
    };
  };
};

type JobSummary = {
  job_id: string;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  updated_at: string;
  result?: {
    run_id?: string;
    batch_status?: string;
    runs?: Array<{ gene: string; run_id: string; recommended_candidate_id?: string; qc_gate_status?: string }>;
    recommended_candidate_id?: string;
    refresh_status?: string;
    summary?: {
      requested?: number;
      planned?: number;
      succeeded?: number;
      failed?: number;
    };
  };
  error?: string | null;
};

type AuditEvent = {
  event_id: string;
  timestamp: string;
  event_type: string;
  action: string;
  outcome: string;
  actor: string;
  request_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  detail?: Record<string, unknown>;
};

type AuditTrail = {
  summary: {
    total_events: number;
    by_event_type: Record<string, number>;
    by_outcome: Record<string, number>;
    latest_event?: AuditEvent | null;
  };
  events: AuditEvent[];
};

type ArchivedArtifact = {
  artifact_id: string;
  created_at: string;
  artifact_type: string | null;
  resource_type: string;
  resource_id: string;
  action: string;
  filename: string;
  bytes: number;
  manifest_hash?: string | null;
  verification_status?: string | null;
};

type ArchivedArtifactVerification = {
  artifact: ArchivedArtifact;
  status: string;
  errors?: string[];
  warnings?: string[];
  sha256: string;
  manifest_hash?: string | null;
  bundle_verification?: {
    status: string;
    semantic_status?: string;
    optimizer_manifest_hash?: string | null;
    recommended_folding_evidence_hash?: string | null;
    recommended_folding_status?: string | null;
    recommendation_readiness_hash?: string | null;
    recommendation_readiness_status?: string | null;
    recommendation_release_ready?: boolean | null;
    recommendation_readiness_candidate_id?: string | null;
    recommended_folding_candidate_id?: string | null;
    checked_files?: number;
    file_count?: number;
    semantic_checks?: Record<string, string>;
  };
};

type ArchiveSemanticFreshness = {
  freshness_status?: string;
  latest_created_at?: string | null;
  latest_age_hours?: number | null;
  freshness_policy?: {
    warning_hours?: number;
  };
};

type QcBundleArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    optimizer_manifest_hash?: string | null;
    request_hash?: string | null;
    qc_report_hash?: string | null;
    report_formats_summary_hash?: string | null;
    candidate_ranking_hash?: string | null;
    recommendation_audit_hash?: string | null;
    recommendation_readiness_hash?: string | null;
    recommendation_readiness_status?: string | null;
    recommendation_release_ready?: boolean | null;
    recommendation_readiness_candidate_id?: string | null;
    recommended_folding_evidence_hash?: string | null;
    recommended_folding_status?: string | null;
    recommended_folding_backend?: string | null;
    recommended_folding_fallback_active?: boolean | null;
    recommended_folding_candidate_id?: string | null;
    data_quality_status?: string | null;
    optimizer_stress_status?: string | null;
    objective_count?: number | null;
    retrieval_quality_status?: string | null;
    retrieval_quality_record_count?: number | null;
    retrieval_quality_source_count?: number | null;
    retrieval_quality_collection_count?: number | null;
    retrieval_quality_high_confidence_count?: number | null;
    retrieval_quality_rank_evidence_count?: number | null;
    retrieval_quality_rank_evidence_hash?: string | null;
    retrieval_model?: string | null;
    embedding_model?: string | null;
    request_payload_status?: string | null;
    request_target_checks?: Record<string, string>;
    checked_files?: number;
    file_count?: number;
  }>;
};

type StructuredImportArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    structured_manifest_hash?: string | null;
    validation_error_count?: number | null;
    validation_warning_count?: number | null;
  }>;
};

type DataReleaseArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    promotion_status?: string | null;
    record_count?: number | null;
    records_hash?: string | null;
    records_csv_hash?: string | null;
    record_source_summary_hash?: string | null;
    dataset_count?: number | null;
    release_count?: number | null;
    source_file_count?: number | null;
    release_handoff_hash?: string | null;
    structured_manifest_hash?: string | null;
    rag_structured_manifest_hash?: string | null;
    rag_index_hash?: string | null;
    trna_caveat_count?: number | null;
    trna_blocking_production_use?: boolean | null;
    external_snapshot_reference_count?: number | null;
    external_snapshot_referenced_count?: number | null;
    external_snapshot_contained_count?: number | null;
    external_snapshot_missing_count?: number | null;
  }>;
};

type DataSnapshotArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    snapshot_manifest_hash?: string | null;
    structured_manifest_hash?: string | null;
    rag_index_hash?: string | null;
    external_snapshot_file_count?: number | null;
    snapshot_file_count?: number | null;
  }>;
};

type DataRefreshPlanArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    operation_count?: number | null;
    request_hash?: string | null;
    operations_hash?: string | null;
    data_catalog_hash?: string | null;
    external_sources_hash?: string | null;
    structured_quality_hash?: string | null;
    data_provenance_hash?: string | null;
    rag_status_hash?: string | null;
    validation_status?: string | null;
    structured_manifest_hash?: string | null;
    release_lock_status?: string | null;
    dataset_id?: string | null;
  }>;
};

type AuditBundleArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    query_fingerprint?: string | null;
    evaluation_hash?: string | null;
    retrieval_trace_hash?: string | null;
    evidence_sufficiency_hash?: string | null;
    facet_gap_analysis_hash?: string | null;
    query_term_coverage_hash?: string | null;
    top_sources_hash?: string | null;
    source_provenance_hash?: string | null;
    source_provenance_count?: number | null;
    source_payload_hash_count?: number | null;
    source_snapshot_count?: number | null;
    score_breakdown_hash?: string | null;
    chunks_hash?: string | null;
    benchmark_status?: string | null;
    diagnostics_status?: string | null;
    stress_status?: string | null;
    case_count?: number | null;
    cases_hash?: string | null;
    results_hash?: string | null;
    quality_summary_hash?: string | null;
    quality_status?: string | null;
    source_provenance_summary_hash?: string | null;
    source_provenance_case_count?: number | null;
    source_provenance_source_count?: number | null;
    source_snapshot_case_count?: number | null;
    top_source_count?: number | null;
    missing_term_case_count?: number | null;
    benchmark_hash?: string | null;
    diagnostics_hash?: string | null;
    case_metrics_hash?: string | null;
    candidate_diagnostics_hash?: string | null;
    recommendation_summary_hash?: string | null;
    recommendation_summary_status?: string | null;
    recommended_on_pareto_front_count?: number | null;
    recommendation_max_regret?: number | null;
    case_provenance_hash?: string | null;
    case_fingerprint_count?: number | null;
    recommended_folding_evidence_hash?: string | null;
    recommended_folding_evidence_count?: number | null;
    recommended_folding_candidate_match_count?: number | null;
    structured_manifest_hash?: string | null;
    workflow_id?: string | null;
    run_id?: string | null;
    task_type?: string | null;
    trace_hash?: string | null;
    trace_step_count?: number | null;
  }>;
};

type RagVectorIndexArchiveSemanticSummary = ArchiveSemanticFreshness & {
  status: string;
  checked_count: number;
  semantic_pass_count: number;
  semantic_warning_count: number;
  semantic_fail_count: number;
  latest_artifacts: Array<{
    artifact_id: string;
    semantic_status?: string;
    chunk_count?: number | null;
    embedding_dimensions?: number | null;
    embedding_model?: string | null;
    retrieval_model?: string | null;
    recommended_backend?: string | null;
    migration_target_backend?: string | null;
    parity_status?: string | null;
    vector_row_hash?: string | null;
    structured_manifest_hash?: string | null;
  }>;
};

type ArtifactArchiveSummary = {
  total_artifacts: number;
  total_bytes: number;
  ledger?: {
    status: string;
    entry_count: number;
    latest_hash: string;
    missing_from_ledger_count: number;
  };
  retention_policy?: {
    retention_days: number;
    keep_min: number;
    enabled: boolean;
  };
  object_store?: {
    enabled: boolean;
    configured: boolean;
    bucket?: string | null;
    prefix?: string;
    region?: string;
    lifecycle_policy?: {
      status: string;
      retention_days: number;
      keep_min: number;
      object_store_mirror_ready: boolean;
    };
    lifecycle_policy_hash?: string | null;
  };
};

type ArtifactRetentionPlan = {
  status: string;
  retention_days: number;
  keep_min: number;
  enabled: boolean;
  cutoff?: string | null;
  candidate_count: number;
  candidate_bytes: number;
  preserved_count: number;
  total_considered: number;
  dry_run?: boolean;
  deleted_count?: number;
  deleted_bytes?: number;
};

type ArtifactObjectStoreMirrorPlan = {
  mirror_schema: string;
  status: string;
  candidate_count: number;
  candidate_bytes: number;
  object_store?: {
    enabled: boolean;
    configured: boolean;
    status: string;
    bucket?: string | null;
    prefix?: string;
    region?: string;
    lifecycle_policy?: {
      status: string;
      retention_days: number;
      keep_min: number;
      object_store_mirror_ready: boolean;
    };
    lifecycle_policy_hash?: string | null;
    recommendation?: string;
  };
};

type RagRegressionStatus = {
  status: string;
  case_count: number;
  pass_count: number;
  fail_count: number;
  cases_hash?: string;
  results_hash?: string;
  quality_summary_hash?: string;
  quality_summary?: {
    status: string;
    top_source_count: number;
    missing_term_case_count: number;
  };
  macro: {
    recall_at_k: number;
    ndcg_at_k: number;
    source_coverage: number;
    collection_coverage: number;
  };
  results?: Array<{
    case_id: string;
    status: string;
    errors: string[];
    warnings: string[];
  }>;
};

type RagDiagnosticsStatus = {
  status: string;
  embedding_backend?: {
    embedding_schema: string;
    status: string;
    requested_backend: string;
    active_backend: string;
    fallback_active: boolean;
    embedding_model: string;
    embedding_dimensions: number;
    model_fingerprint_hash?: string | null;
    model_fingerprint?: {
      fingerprint_schema: string;
      requested_backend: string;
      active_backend: string;
      fallback_active: boolean;
      embedding_model: string;
      embedding_dimensions: number;
      production_candidate: boolean;
    };
    production_ready: boolean;
    recommendation?: string;
    warnings?: string[];
    openai?: {
      api_key_configured: boolean;
      base_url: string;
      configured_model: string;
      configured_dimensions: number;
      budget?: {
        price_per_1k_tokens_usd: number;
        budget_usd: number;
        estimated_spend_usd: number;
        estimated_remaining_usd?: number | null;
        budget_configured: boolean;
        price_configured: boolean;
        within_budget?: boolean;
        budget_exceeded?: boolean;
        next_request_policy?: string;
        usage_estimate_policy?: string;
        missing_usage_entries?: number;
      };
      cache?: {
        cache_schema: string;
        path: string;
        exists?: boolean;
        file_sha256?: string | null;
        entries: number;
        entry_keys_hash?: string | null;
        models?: Record<string, number>;
        dimensions?: Record<string, number>;
        estimated_input_tokens?: number;
        estimated_cost_usd?: number;
        missing_usage_entries?: number;
        invalid_entries?: number;
        enabled: boolean;
      };
    };
    sentence_transformers?: {
      package_available: boolean;
      configured_model: string;
      configured_dimensions: number;
      model_load_policy: string;
    };
  };
  index: {
    chunk_count: number;
    document_count: number;
    embedding_model?: string;
    embedding_dimensions: number;
    structured_manifest_hash?: string;
    chunking_policy?: {
      version?: string;
      max_words?: number;
      max_lexical_tokens?: number;
      sentence_boundary_split?: boolean;
      drop_empty_lexical_chunks?: boolean;
    };
  };
  distributions: {
    sources: Array<{ value: string; count: number }>;
    collections: Array<{ value: string; count: number }>;
    evidence_classes: Array<{ value: string; count: number }>;
  };
  token_stats: {
    mean: number;
    empty_chunks: number;
    short_chunks: number;
    long_chunks: number;
  };
  embedding_stats: {
    dimension_mismatch_count: number;
    mean_nonzero_dimensions: number;
    empty_embedding_count: number;
  };
  vector_store_readiness?: {
    readiness_schema: string;
    status: string;
    active_backend: string;
    target_backend?: string;
    recommended_backend: string;
    chunk_count: number;
    embedding_dimensions: number;
    required_metadata_missing_chunks: number;
    facet_completeness: Record<string, number>;
    backend_candidates: Array<{ backend: string; status: string; fit: string }>;
    runtime?: {
      runtime_schema: string;
      status: string;
      target_backend: string;
      active_backend: string;
      fallback_active: boolean;
      warnings?: string[];
    };
    migration_contract?: {
      distance?: string;
      required_payload_fields?: string[];
      filter_payload_fields?: string[];
    };
  };
  regression: {
    status: string;
    case_count?: number;
    pass_count?: number;
    warning_count?: number;
    fail_count?: number;
    cases_hash?: string;
    results_hash?: string;
    macro: {
      recall_at_k: number;
      ndcg_at_k: number;
      source_coverage: number;
      collection_coverage: number;
    };
    weak_cases: Array<{ case_id: string; status: string; message: string }>;
  };
  recommendations: string[];
  warnings: string[];
};

type OptimizerBenchmarkStatus = {
  status: string;
  case_count: number;
  pass_count: number;
  warning_count?: number;
  fail_count: number;
  cases_hash?: string;
  results_hash?: string;
  macro: {
    candidate_count: number;
    unique_cds_count: number;
    constraint_violation_rate: number;
    mean_pairwise_codon_distance: number;
    recommended_composite_delta: number;
    approx_hypervolume_2d: number;
    runtime_ms: number;
  };
  results?: Array<{
    case_id: string;
    description?: string;
    status: string;
    errors: string[];
    warnings: string[];
    metrics: Record<string, number>;
    thresholds?: Record<string, number>;
    recommended_candidate_id?: string | null;
  }>;
};

type OptimizerDiagnosticsStatus = {
  status: string;
  optimizer?: {
    algorithm?: string;
    seed_strategy?: string;
    search_strategy?: {
      strategy_schema?: string;
      algorithm?: string;
      seed_strategy?: string;
      deterministic_seed_variants?: string[];
      stochastic_operator?: {
        rng_seed?: number;
        mutation_rate?: number;
        crossover_rate?: number;
        population_size?: number;
        generations?: number;
      };
      repair_policy?: {
        enabled?: boolean;
        repair_passes?: number;
      };
      selection_policy?: string;
    };
  };
  benchmark: {
    status: string;
    case_count: number;
    fail_count: number;
    cases_hash?: string;
    results_hash?: string;
    macro: OptimizerBenchmarkStatus["macro"];
    weak_cases: Array<{ case_id: string; status: string; message: string }>;
  };
  quality_bands: Record<string, string>;
  stress_gate?: OptimizerStressStatus;
  rna_folding?: {
    folding_schema: string;
    status: string;
    requested_backend: string;
    active_backend: string;
    fallback_active: boolean;
    production_ready: boolean;
    executable?: string;
    recommendation?: string;
  };
  warnings: string[];
  recommendations: string[];
};

type OptimizerStressStatus = {
  stress_schema: string;
  status: string;
  case_count: number;
  summary: {
    pass_count: number;
    warning_count: number;
    fail_count: number;
  };
  stress_checks: Array<{
    id: string;
    status: string;
    case_count: number;
    fail_count: number;
    warning_count: number;
  }>;
  recommendations: string[];
};

type RagInspectionResult = {
  query: string;
  query_fingerprint?: string;
  ranking_policy?: {
    version: string;
    weights: Record<string, number>;
  };
  result_count: number;
  coverage: Record<string, unknown>;
  missing_facets: string[];
  facet_gap_analysis?: {
    analysis_schema: string;
    missing_from_results: string[];
    missing_from_corpus: string[];
    facets: Record<
      string,
      {
        requested?: string | null;
        status: string;
        matched_result_count: number;
        corpus_available_count: number;
        top_available_values: Array<{ value: string; count: number }>;
        recommendation: string;
      }
    >;
  };
  query_term_coverage?: {
    coverage_schema: string;
    query_token_count: number;
    expanded_token_count: number;
    matched_query_tokens: string[];
    missing_query_tokens: string[];
    matched_expanded_tokens: string[];
    coverage_fraction: number;
  };
  evidence_sufficiency?: {
    sufficiency_schema: string;
    status: string;
    result_count: number;
    source_count: number;
    collection_count: number;
    high_confidence_count: number;
    requested_facets: string[];
    matched_facets: string[];
    missing_from_results: string[];
    missing_from_corpus: string[];
    query_term_coverage_fraction: number;
    recommendations?: string[];
  };
  retrieval_trace?: {
    trace_schema: string;
    ranking_policy: string;
    chunking_policy: string;
    aliases_added_count: number;
    returned_chunks: number;
    top_ranked_chunk_ids: string[];
    query_term_coverage_fraction?: number;
  };
  top_sources: Array<{ source: string; chunks: number }>;
  score_breakdown: Array<{
    chunk_id: string;
    document_id: string;
    title: string;
    collection: string;
    source: string;
    score: number;
    vector_score: number;
    bm25_score: number;
    rerank_score: number;
    facet_score?: number;
    field_match_score?: number;
    source_priority_score?: number;
    matched_facets?: string[];
    rationale?: string[];
  }>;
  recommended_query_terms?: string[];
};

type RagEvaluationBundleVerification = {
  status: string;
  semantic_status: string;
  bundle_fingerprint?: string;
  result_count?: number;
  structured_manifest_hash?: string;
  semantic_checks?: Record<string, string>;
  errors?: string[];
  warnings?: string[];
};

type RagRegressionBundleVerification = {
  status: string;
  semantic_status: string;
  regression_status?: string;
  case_count?: number;
  cases_hash?: string;
  results_hash?: string;
  quality_summary_hash?: string;
  case_metrics_hash?: string;
  source_provenance_summary_hash?: string;
  weak_case_count?: number;
  structured_manifest_hash?: string;
  semantic_checks?: Record<string, string>;
  errors?: string[];
  warnings?: string[];
};

type RagVectorIndexBundleVerification = {
  status: string;
  semantic_status: string;
  chunk_count?: number;
  embedding_dimensions?: number;
  embedding_model?: string;
  recommended_backend?: string;
  structured_manifest_hash?: string;
  errors?: string[];
  warnings?: string[];
};

type RagVectorStoreMigrationStatus = {
  migration_schema: string;
  status: string;
  dry_run?: boolean;
  target_backend: string;
  source?: {
    records?: number;
    row_fingerprint?: {
      combined_row_hash?: string;
    };
  };
  target?: {
    status?: string;
    backend?: string;
    records?: number;
  };
  comparison?: {
    record_count_match?: boolean;
    row_hash_match?: boolean;
    source_records?: number;
    target_records?: number;
  };
  warnings?: string[];
};

type OptimizerBenchmarkBundleVerification = {
  status: string;
  semantic_status: string;
  cases_hash?: string;
  results_hash?: string;
  benchmark_hash?: string;
  diagnostics_hash?: string;
  case_metrics_hash?: string;
  candidate_diagnostics_hash?: string;
  recommendation_summary_hash?: string;
  recommendation_summary_status?: string;
  recommended_on_pareto_front_count?: number;
  recommendation_max_regret?: number;
  case_count?: number;
  structured_manifest_hash?: string;
  semantic_checks?: Record<string, string>;
  errors?: string[];
  warnings?: string[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

const sampleGenes = ["SNCA", "HTT", "GBA1", "MECP2"];

function apiHeaders(headers: HeadersInit = {}): HeadersInit {
  return API_KEY ? { ...headers, "X-API-Key": API_KEY } : headers;
}

export default function Dashboard() {
  const [inputMode, setInputMode] = useState<"gene" | "cds">("gene");
  const [gene, setGene] = useState("SNCA");
  const [cdsInput, setCdsInput] = useState("ATGGCTGACGAGTTCGCCAAGGGTTACTAA");
  const [batchGenes, setBatchGenes] = useState("SNCA, GBA1, MECP2");
  const [brainRegion, setBrainRegion] = useState("substantia nigra");
  const [cellType, setCellType] = useState("dopaminergic neuron");
  const [diseaseContext, setDiseaseContext] = useState("Parkinson disease demo");
  const [modality, setModality] = useState("AAV");
  const [populationSize, setPopulationSize] = useState(32);
  const [generations, setGenerations] = useState(10);
  const [maxCandidates, setMaxCandidates] = useState(5);
  const [seed, setSeed] = useState(42);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [design, setDesign] = useState<DesignResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [dataManifest, setDataManifest] = useState<StructuredManifest | null>(null);
  const [dataAudit, setDataAudit] = useState<DataAudit | null>(null);
  const [dataCatalog, setDataCatalog] = useState<DataCatalog | null>(null);
  const [dataCoverage, setDataCoverage] = useState<DataCoverage | null>(null);
  const [dataQuality, setDataQuality] = useState<DataQualityGate | null>(null);
  const [dataRefreshLog, setDataRefreshLog] = useState<DataRefreshLog | null>(null);
  const [externalBackfill, setExternalBackfill] = useState<ExternalSourceBackfillResult | null>(null);
  const [structuredImportPreview, setStructuredImportPreview] = useState<StructuredImportPreview | null>(null);
  const [dataRefreshValidation, setDataRefreshValidation] = useState<DataRefreshValidation | null>(null);
  const [dataLoading, setDataLoading] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [agentMemories, setAgentMemories] = useState<AgentMemorySummary[]>([]);
  const [selectedAgentMemory, setSelectedAgentMemory] = useState<AgentMemoryDetail | null>(null);
  const [agentMemoryLoading, setAgentMemoryLoading] = useState(false);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [auditTrail, setAuditTrail] = useState<AuditTrail | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [governanceStatus, setGovernanceStatus] = useState<GovernanceAttestationStatus | null>(null);
  const [governanceLoading, setGovernanceLoading] = useState(false);
  const [storageStatus, setStorageStatus] = useState<StorageReadinessStatus | null>(null);
  const [storageMigration, setStorageMigration] = useState<StorageMigrationStatus | null>(null);
  const [storageLoading, setStorageLoading] = useState(false);
  const [securityStatus, setSecurityStatus] = useState<SecurityStatus | null>(null);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [metricsSnapshot, setMetricsSnapshot] = useState<MetricsSnapshot | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [deploymentReadiness, setDeploymentReadiness] = useState<DeploymentReadinessStatus | null>(null);
  const [deploymentLoading, setDeploymentLoading] = useState(false);
  const [productionAudit, setProductionAudit] = useState<ProductionAuditStatus | null>(null);
  const [productionAuditLoading, setProductionAuditLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<ArchivedArtifact[]>([]);
  const [artifactSummary, setArtifactSummary] = useState<ArtifactArchiveSummary | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactVerifyingId, setArtifactVerifyingId] = useState("");
  const [artifactVerification, setArtifactVerification] = useState<ArchivedArtifactVerification | null>(null);
  const [qcBundleSemantics, setQcBundleSemantics] = useState<QcBundleArchiveSemanticSummary | null>(null);
  const [structuredImportSemantics, setStructuredImportSemantics] = useState<StructuredImportArchiveSemanticSummary | null>(null);
  const [dataReleaseSemantics, setDataReleaseSemantics] = useState<DataReleaseArchiveSemanticSummary | null>(null);
  const [dataSnapshotSemantics, setDataSnapshotSemantics] = useState<DataSnapshotArchiveSemanticSummary | null>(null);
  const [dataRefreshPlanSemantics, setDataRefreshPlanSemantics] = useState<DataRefreshPlanArchiveSemanticSummary | null>(null);
  const [ragEvaluationSemantics, setRagEvaluationSemantics] = useState<AuditBundleArchiveSemanticSummary | null>(null);
  const [ragRegressionSemantics, setRagRegressionSemantics] = useState<AuditBundleArchiveSemanticSummary | null>(null);
  const [ragVectorIndexSemantics, setRagVectorIndexSemantics] = useState<RagVectorIndexArchiveSemanticSummary | null>(null);
  const [optimizerBenchmarkSemantics, setOptimizerBenchmarkSemantics] = useState<AuditBundleArchiveSemanticSummary | null>(null);
  const [workflowTraceSemantics, setWorkflowTraceSemantics] = useState<AuditBundleArchiveSemanticSummary | null>(null);
  const [retentionPlan, setRetentionPlan] = useState<ArtifactRetentionPlan | null>(null);
  const [objectStorePlan, setObjectStorePlan] = useState<ArtifactObjectStoreMirrorPlan | null>(null);
  const [retentionLoading, setRetentionLoading] = useState(false);
  const [ragRegression, setRagRegression] = useState<RagRegressionStatus | null>(null);
  const [ragDiagnostics, setRagDiagnostics] = useState<RagDiagnosticsStatus | null>(null);
  const [optimizerBenchmark, setOptimizerBenchmark] = useState<OptimizerBenchmarkStatus | null>(null);
  const [optimizerDiagnostics, setOptimizerDiagnostics] = useState<OptimizerDiagnosticsStatus | null>(null);
  const [optimizerStress, setOptimizerStress] = useState<OptimizerStressStatus | null>(null);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [ragQuery, setRagQuery] = useState("SNCA substantia nigra dopaminergic neuron AAV");
  const [ragInspection, setRagInspection] = useState<RagInspectionResult | null>(null);
  const [ragInspectLoading, setRagInspectLoading] = useState(false);
  const [ragBundleLoading, setRagBundleLoading] = useState(false);
  const [ragBundleVerification, setRagBundleVerification] = useState<RagEvaluationBundleVerification | null>(null);
  const [ragRegressionBundleLoading, setRagRegressionBundleLoading] = useState(false);
  const [ragRegressionBundleVerification, setRagRegressionBundleVerification] = useState<RagRegressionBundleVerification | null>(null);
  const [ragVectorBundleLoading, setRagVectorBundleLoading] = useState(false);
  const [ragVectorBundleVerification, setRagVectorBundleVerification] = useState<RagVectorIndexBundleVerification | null>(null);
  const [ragVectorMigrationLoading, setRagVectorMigrationLoading] = useState(false);
  const [ragVectorImportPlan, setRagVectorImportPlan] = useState<RagVectorStoreMigrationStatus | null>(null);
  const [ragVectorParity, setRagVectorParity] = useState<RagVectorStoreMigrationStatus | null>(null);
  const [optimizerBundleLoading, setOptimizerBundleLoading] = useState(false);
  const [optimizerBundleVerification, setOptimizerBundleVerification] = useState<OptimizerBenchmarkBundleVerification | null>(null);
  const objectStoreDisplayStatus = objectStorePlan?.status ?? (artifactSummary?.object_store?.configured ? "ready" : "n/a");

  const selectedCandidate = useMemo(() => {
    if (!design) return null;
    return design.candidates.find((candidate) => candidate.candidate_id === selectedId) ?? design.recommended_candidate;
  }, [design, selectedId]);

  useEffect(() => {
    void refreshDataStatus();
    void refreshRuns();
    void refreshAgentMemory();
    void refreshJobs();
    void refreshAuditTrail();
    void refreshSecurityStatus();
    void refreshMetricsSnapshot();
    void refreshDeploymentReadiness();
    void refreshProductionAudit();
    void refreshGovernanceAttestation();
    void refreshStorageReadiness();
    void refreshArtifacts();
    void refreshQualityGates();
  }, []);

  async function refreshDataStatus() {
    try {
      const [statusResponse, manifestResponse, auditResponse, catalogResponse, coverageResponse, qualityResponse, refreshLogResponse] = await Promise.all([
        fetch(`${API_BASE}/structured/status`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/structured/manifest`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/data/provenance`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/data/catalog`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/data/coverage`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/data/quality`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/data/refresh-log?limit=3`, { headers: apiHeaders() })
      ]);
      const [statusPayload, manifestPayload, auditPayload, catalogPayload, coveragePayload, qualityPayload, refreshLogPayload] = await Promise.all([
        statusResponse.json(),
        manifestResponse.json(),
        auditResponse.json(),
        catalogResponse.json(),
        coverageResponse.json(),
        qualityResponse.json(),
        refreshLogResponse.json()
      ]);
      if (statusResponse.ok) {
        setDataStatus(statusPayload.data);
      }
      if (manifestResponse.ok) {
        setDataManifest(manifestPayload.data);
      }
      if (auditResponse.ok) {
        setDataAudit(auditPayload.data);
      }
      if (catalogResponse.ok) {
        setDataCatalog(catalogPayload.data);
      }
      if (coverageResponse.ok) {
        setDataCoverage(coveragePayload.data);
      }
      if (qualityResponse.ok) {
        setDataQuality(qualityPayload.data);
      }
      if (refreshLogResponse.ok) {
        setDataRefreshLog(refreshLogPayload.data);
      }
    } catch {
      // Status is auxiliary; design flow can still run without it.
    }
  }

  async function refreshRuns() {
    setHistoryLoading(true);
    try {
      const response = await fetch(`${API_BASE}/runs?limit=8`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setRuns(payload.data.runs ?? []);
      }
    } catch {
      // Run history is auxiliary.
    } finally {
      setHistoryLoading(false);
    }
  }

  async function refreshAgentMemory(runId?: string) {
    setAgentMemoryLoading(true);
    try {
      const response = await fetch(`${API_BASE}/agent-memory?limit=6`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        const memories = payload.data.memories ?? [];
        setAgentMemories(memories);
        const nextRunId = runId ?? selectedAgentMemory?.run_id ?? memories[0]?.run_id;
        if (nextRunId) {
          await openAgentMemory(nextRunId, false);
        } else {
          setSelectedAgentMemory(null);
        }
      }
    } catch {
      // Agent memory is auxiliary audit context.
    } finally {
      setAgentMemoryLoading(false);
    }
  }

  async function openAgentMemory(runId: string, setLoading = true) {
    if (setLoading) {
      setAgentMemoryLoading(true);
    }
    try {
      const response = await fetch(`${API_BASE}/agent-memory/${runId}`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setSelectedAgentMemory(payload.data);
      }
    } catch {
      // Keep the previous memory selection if a detail lookup fails.
    } finally {
      if (setLoading) {
        setAgentMemoryLoading(false);
      }
    }
  }

  async function refreshJobs() {
    setJobsLoading(true);
    try {
      const response = await fetch(`${API_BASE}/jobs?limit=8`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setJobs(payload.data.jobs ?? []);
      }
    } catch {
      // Job history is auxiliary.
    } finally {
      setJobsLoading(false);
    }
  }

  async function refreshAuditTrail() {
    setAuditLoading(true);
    try {
      const [summaryResponse, eventsResponse] = await Promise.all([
        fetch(`${API_BASE}/audit/summary`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/audit/events?limit=6`, { headers: apiHeaders() })
      ]);
      const summaryPayload = await summaryResponse.json();
      const eventsPayload = await eventsResponse.json();
      if (summaryResponse.ok && eventsResponse.ok) {
        setAuditTrail({
          summary: summaryPayload.data,
          events: eventsPayload.data.events ?? []
        });
      }
    } catch {
      // Audit trail is operational context; the main design workflow can continue without it.
    } finally {
      setAuditLoading(false);
    }
  }

  async function refreshSecurityStatus() {
    setSecurityLoading(true);
    try {
      const response = await fetch(`${API_BASE}/security/status`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setSecurityStatus(payload.data);
      }
    } catch {
      // Security status is operational context; main design flow can continue without it.
    } finally {
      setSecurityLoading(false);
    }
  }

  async function refreshMetricsSnapshot() {
    setMetricsLoading(true);
    try {
      const response = await fetch(`${API_BASE}/metrics`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setMetricsSnapshot(payload.data);
      }
    } catch {
      // Metrics are operational context; keep design flow usable without them.
    } finally {
      setMetricsLoading(false);
    }
  }

  async function refreshDeploymentReadiness() {
    setDeploymentLoading(true);
    try {
      const response = await fetch(`${API_BASE}/deployment/readiness`, { headers: apiHeaders() });
      const payload = await response.json();
      if (response.ok) {
        setDeploymentReadiness(payload.data);
      }
    } catch {
      // Deployment readiness is operational context; keep design workflow usable without it.
    } finally {
      setDeploymentLoading(false);
    }
  }

  async function refreshProductionAudit(force = false) {
    setProductionAuditLoading(true);
    try {
      const query = force ? "?refresh=true" : "";
      const [auditResponse, verifyResponse] = await Promise.all([
        fetch(`${API_BASE}/deployment/audit${query}`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/deployment/audit/verify${query}`, { headers: apiHeaders() })
      ]);
      const [auditPayload, verifyPayload] = await Promise.all([auditResponse.json(), verifyResponse.json()]);
      if (auditResponse.ok && verifyResponse.ok) {
        setProductionAudit({
          audit_hash: auditPayload.data.audit_hash,
          generated_at: auditPayload.data.generated_at,
          cache_policy: auditPayload.data.cache_policy,
          summary: auditPayload.data.summary,
          production_gap_summary: auditPayload.data.production_gap_summary,
          evidence: auditPayload.data.evidence,
          verification: verifyPayload.data
        });
      }
    } catch {
      // Production audit is deployment context; keep design workflow usable without it.
    } finally {
      setProductionAuditLoading(false);
    }
  }

  async function refreshGovernanceAttestation() {
    setGovernanceLoading(true);
    try {
      const [attestationResponse, verifyResponse] = await Promise.all([
        fetch(`${API_BASE}/governance/attestation`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/governance/attestation/verify`, { headers: apiHeaders() })
      ]);
      const [attestationPayload, verifyPayload] = await Promise.all([attestationResponse.json(), verifyResponse.json()]);
      if (attestationResponse.ok && verifyResponse.ok) {
        const attestation = attestationPayload.data;
        const verification = verifyPayload.data;
        setGovernanceStatus({
          ...verification,
          summary: {
            generated_at: attestation.generated_at,
            path_count: attestation.openapi?.path_count,
            data_status: attestation.data?.data_provenance?.status,
            rag_chunks: attestation.data?.rag?.chunks,
            storage_backend: attestation.runtime?.storage_backend,
            ledger_status: attestation.artifact_archive?.ledger_verification?.status,
            signing_status: attestation.runtime?.signing?.status
          }
        });
      }
    } catch {
      // Governance status is operational context; keep the design flow usable without it.
    } finally {
      setGovernanceLoading(false);
    }
  }

  async function refreshStorageReadiness() {
    setStorageLoading(true);
    try {
      const [statusResponse, summaryResponse, parityResponse, importResponse] = await Promise.all([
        fetch(`${API_BASE}/storage/status`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/storage/migration/sqlite/summary`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/storage/migration/sqlite/parity`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/storage/migration/sqlite/import?dry_run=true`, {
          method: "POST",
          headers: apiHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({})
        })
      ]);
      const [statusPayload, summaryPayload, parityPayload, importPayload] = await Promise.all([
        statusResponse.json(),
        summaryResponse.json(),
        parityResponse.json(),
        importResponse.json()
      ]);
      if (statusResponse.ok) {
        setStorageStatus(statusPayload.data);
      }
      setStorageMigration({
        summary: summaryResponse.ok ? summaryPayload.data : undefined,
        parity: parityResponse.ok ? parityPayload.data : undefined,
        import_plan: importResponse.ok ? importPayload.data : undefined
      });
    } catch {
      // Storage readiness is deployment context; the design workflow can continue without it.
    } finally {
      setStorageLoading(false);
    }
  }

  async function refreshArtifacts() {
    setArtifactLoading(true);
    try {
      const [
        artifactsResponse,
        summaryResponse,
        objectStoreResponse,
        qcSemanticsResponse,
        importSemanticsResponse,
        dataReleaseSemanticsResponse,
        dataSnapshotSemanticsResponse,
        dataRefreshPlanSemanticsResponse,
        ragSemanticsResponse,
        ragRegressionSemanticsResponse,
        ragVectorIndexSemanticsResponse,
        optimizerSemanticsResponse,
        workflowTraceSemanticsResponse
      ] = await Promise.all([
        fetch(`${API_BASE}/artifacts?limit=6`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/summary`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/object-store/mirror/plan?limit=6`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/qc-bundles/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/structured-imports/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/data-releases/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/data-snapshots/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/data-refresh-plans/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/rag-evaluations/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/rag-regressions/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/rag-vector-indexes/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/optimizer-benchmarks/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/artifacts/workflow-traces/semantic-summary?limit=6&verify_files=false`, { headers: apiHeaders() })
      ]);
      const artifactsPayload = await artifactsResponse.json();
      const summaryPayload = await summaryResponse.json();
      const objectStorePayload = await objectStoreResponse.json();
      const qcSemanticsPayload = await qcSemanticsResponse.json();
      const importSemanticsPayload = await importSemanticsResponse.json();
      const dataReleaseSemanticsPayload = await dataReleaseSemanticsResponse.json();
      const dataSnapshotSemanticsPayload = await dataSnapshotSemanticsResponse.json();
      const dataRefreshPlanSemanticsPayload = await dataRefreshPlanSemanticsResponse.json();
      const ragSemanticsPayload = await ragSemanticsResponse.json();
      const ragRegressionSemanticsPayload = await ragRegressionSemanticsResponse.json();
      const ragVectorIndexSemanticsPayload = await ragVectorIndexSemanticsResponse.json();
      const optimizerSemanticsPayload = await optimizerSemanticsResponse.json();
      const workflowTraceSemanticsPayload = await workflowTraceSemanticsResponse.json();
      if (artifactsResponse.ok) {
        setArtifacts(artifactsPayload.data.artifacts ?? []);
      }
      if (summaryResponse.ok) {
        setArtifactSummary(summaryPayload.data);
      }
      if (objectStoreResponse.ok) {
        setObjectStorePlan(objectStorePayload.data);
      }
      if (qcSemanticsResponse.ok) {
        setQcBundleSemantics(qcSemanticsPayload.data);
      }
      if (importSemanticsResponse.ok) {
        setStructuredImportSemantics(importSemanticsPayload.data);
      }
      if (dataReleaseSemanticsResponse.ok) {
        setDataReleaseSemantics(dataReleaseSemanticsPayload.data);
      }
      if (dataSnapshotSemanticsResponse.ok) {
        setDataSnapshotSemantics(dataSnapshotSemanticsPayload.data);
      }
      if (dataRefreshPlanSemanticsResponse.ok) {
        setDataRefreshPlanSemantics(dataRefreshPlanSemanticsPayload.data);
      }
      if (ragSemanticsResponse.ok) {
        setRagEvaluationSemantics(ragSemanticsPayload.data);
      }
      if (ragRegressionSemanticsResponse.ok) {
        setRagRegressionSemantics(ragRegressionSemanticsPayload.data);
      }
      if (ragVectorIndexSemanticsResponse.ok) {
        setRagVectorIndexSemantics(ragVectorIndexSemanticsPayload.data);
      }
      if (optimizerSemanticsResponse.ok) {
        setOptimizerBenchmarkSemantics(optimizerSemanticsPayload.data);
      }
      if (workflowTraceSemanticsResponse.ok) {
        setWorkflowTraceSemantics(workflowTraceSemanticsPayload.data);
      }
    } catch {
      // Archive status is auxiliary to the design workflow.
    } finally {
      setArtifactLoading(false);
    }
  }

  async function refreshQualityGates() {
    setQualityLoading(true);
    try {
      const [ragResponse, diagnosticsResponse, vectorPlanResponse, vectorParityResponse, optimizerResponse, optimizerDiagnosticsResponse, optimizerStressResponse] = await Promise.all([
        fetch(`${API_BASE}/rag/regression`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/rag/diagnostics`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/rag/vector-store/import/plan?target_backend=pgvector`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/rag/vector-store/parity?target_backend=local_json`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/optimizer/benchmark`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/optimizer/diagnostics`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/optimizer/stress`, { headers: apiHeaders() })
      ]);
      const [ragPayload, diagnosticsPayload, vectorPlanPayload, vectorParityPayload, optimizerPayload, optimizerDiagnosticsPayload, optimizerStressPayload] = await Promise.all([
        ragResponse.json(),
        diagnosticsResponse.json(),
        vectorPlanResponse.json(),
        vectorParityResponse.json(),
        optimizerResponse.json(),
        optimizerDiagnosticsResponse.json(),
        optimizerStressResponse.json()
      ]);
      if (ragResponse.ok) {
        setRagRegression(ragPayload.data);
      }
      if (diagnosticsResponse.ok) {
        setRagDiagnostics(diagnosticsPayload.data);
      }
      if (vectorPlanResponse.ok) {
        setRagVectorImportPlan(vectorPlanPayload.data);
      }
      if (vectorParityResponse.ok) {
        setRagVectorParity(vectorParityPayload.data);
      }
      if (optimizerResponse.ok) {
        setOptimizerBenchmark(optimizerPayload.data);
      }
      if (optimizerDiagnosticsResponse.ok) {
        setOptimizerDiagnostics(optimizerDiagnosticsPayload.data);
      }
      if (optimizerStressResponse.ok) {
        setOptimizerStress(optimizerStressPayload.data);
      }
    } catch {
      // Quality gates are operational context; keep the dashboard usable if they are unavailable.
    } finally {
      setQualityLoading(false);
    }
  }

  async function openRun(runId: string) {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/runs/${runId}`, { headers: apiHeaders() });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Run lookup failed.");
      }
      setDesign(payload.data.design);
      setSelectedId(payload.data.design?.recommended_candidate?.candidate_id ?? "");
      void openAgentMemory(runId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Run lookup failed.");
    }
  }

  async function importGtexForGene() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/external/gtex/import-gene-expression`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          gene,
          brain_regions: [brainRegion, "striatum", "cortex", "hippocampus"],
          dataset_id: "gtex_v8",
          rebuild_index: true
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "GTEx import failed.");
      }
      setDataStatus(payload.data.status);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "GTEx import failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function importAllenTaxonomy() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/external/allen/import-whb-taxonomy`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          query_terms: [cellType, "dopaminergic", "medium spiny", "astrocyte", "microglia"],
          max_records: 120,
          rebuild_index: true
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Allen import failed.");
      }
      setDataStatus(payload.data.status);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Allen import failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function previewStructuredImport() {
    const source = dataManifest?.files?.[0];
    if (!source) {
      setError("No structured source file is available for import preview.");
      return;
    }
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/structured/import/preview`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          source_path: source.path,
          rebuild_index: false
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Structured import preview failed.");
      }
      setStructuredImportPreview(payload.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Structured import preview failed.");
    } finally {
      setDataLoading(false);
    }
  }

  function referencePanelPayload() {
    return {
      genes: [gene],
      brain_regions: [brainRegion, "striatum", "cortex", "hippocampus"],
      include_gtex: true,
      include_allen: true,
      allen_query_terms: [cellType, "dopaminergic", "medium spiny", "astrocyte", "microglia"],
      max_allen_records: 120,
      dataset_id: "gtex_v8",
      rebuild_index: true
    };
  }

  async function validateReferencePanel() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/refresh/validate`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(referencePanelPayload())
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Reference refresh validation failed.");
      }
      setDataRefreshValidation(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Reference refresh validation failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function refreshReferencePanel() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/refresh`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(referencePanelPayload())
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Reference refresh failed.");
      }
      setDataStatus(payload.data.status);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Reference refresh failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function downloadDataSnapshot() {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/snapshot.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Data snapshot export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentic_rag_data_snapshot.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Data snapshot export failed.");
    }
  }

  async function downloadDataRefreshPlanBundle() {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/refresh/plan/export.zip`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...referencePanelPayload(), dry_run: true })
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Data refresh plan export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "data_refresh_plan_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Data refresh plan export failed.");
    }
  }

  async function writeDataLockfile() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/lockfile/write`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Data lock failed.");
      }
      void refreshDataStatus();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Data lock failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function writeDataReleaseLock() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/release-lock/write`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Release lock failed.");
      }
      void refreshDataStatus();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Release lock failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function recordDataBaseline() {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/provenance/baseline`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Baseline record failed.");
      }
      if (payload.data?.provenance) {
        setDataAudit(payload.data.provenance);
      }
      await refreshDataStatus();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Baseline record failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function backfillExternalSourceSnapshots(dryRun: boolean) {
    setDataLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/data/external-sources/backfill?dry_run=${dryRun ? "true" : "false"}`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "External source backfill failed.");
      }
      setExternalBackfill(payload.data);
      await refreshDataStatus();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "External source backfill failed.");
    } finally {
      setDataLoading(false);
    }
  }

  async function runDesign(event?: FormEvent) {
    event?.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/${inputMode === "cds" ? "optimize" : "design-from-gene"}`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(inputMode === "cds" ? cdsDesignRequestPayload() : designRequestPayload())
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Design request failed.");
      }
      setDesign(payload.data);
      setSelectedId(payload.data.recommended_candidate?.candidate_id ?? "");
      void refreshRuns();
      void refreshAgentMemory(payload.data.run_id);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unknown request failure.");
    } finally {
      setLoading(false);
    }
  }

  async function queueDesignJob() {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/jobs/design-from-gene`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(designRequestPayload())
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Job enqueue failed.");
      }
      void refreshJobs();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Job enqueue failed.");
    }
  }

  async function queueBatchDesignJob() {
    setError("");
    const genes = parseBatchGenes(batchGenes);
    if (!genes.length) {
      setError("Enter at least one gene for the batch queue.");
      return;
    }
    try {
      const response = await fetch(`${API_BASE}/jobs/batch-design-from-genes`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          ...designRequestPayload(),
          genes,
          continue_on_error: true
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Batch enqueue failed.");
      }
      void refreshJobs();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Batch enqueue failed.");
    }
  }

  function designRequestPayload() {
    return {
      gene,
      species: "human",
      target: {
        disease_context: diseaseContext,
        brain_region: brainRegion,
        cell_type: cellType,
        modality
      },
      optimization_settings: {
        population_size: populationSize,
        generations,
        mutation_rate: 0.06,
        crossover_rate: 0.8,
        seed,
        max_candidates: maxCandidates
      }
    };
  }

  function cdsDesignRequestPayload() {
    return {
      cds: cdsInput,
      target: {
        gene,
        disease_context: diseaseContext,
        brain_region: brainRegion,
        cell_type: cellType,
        modality,
        species: "human"
      },
      optimization_settings: {
        population_size: populationSize,
        generations,
        mutation_rate: 0.06,
        crossover_rate: 0.8,
        seed,
        max_candidates: maxCandidates
      }
    };
  }

  function downloadReport() {
    if (!design) return;
    const blob = new Blob([JSON.stringify(design, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${design.run_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function downloadQcExport(format: "markdown" | "html" | "json" | "pdf") {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/${inputMode === "cds" ? "report" : "report-from-gene"}/export/${format}`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(inputMode === "cds" ? cdsDesignRequestPayload() : designRequestPayload())
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Report export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${design?.run_id ?? gene}_qc_report.${format === "html" ? "html" : format === "json" ? "json" : format === "pdf" ? "pdf" : "md"}`;
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshRuns();
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Report export failed.");
    }
  }

  async function downloadQcBundle() {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/${inputMode === "cds" ? "report" : "report-from-gene"}/export-bundle.zip`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(inputMode === "cds" ? cdsDesignRequestPayload() : designRequestPayload())
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "QC bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${design?.run_id ?? gene}_qc_report_bundle.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshRuns();
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "QC bundle export failed.");
    }
  }

  async function downloadRunBundle() {
    if (!design) return;
    setError("");
    try {
      const response = await fetch(`${API_BASE}/runs/${design.run_id}/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Run bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${design.run_id}_audit_bundle.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Run bundle export failed.");
    }
  }

  async function downloadJobBundle(jobId: string) {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Job bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${jobId}_job_bundle.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Job bundle export failed.");
    }
  }

  async function downloadArchivedArtifact(artifact: ArchivedArtifact) {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/artifacts/${artifact.artifact_id}/download`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Archived artifact download failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Archived artifact download failed.");
    }
  }

  async function verifyArchivedArtifact(artifact: ArchivedArtifact) {
    setArtifactVerifyingId(artifact.artifact_id);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/artifacts/${artifact.artifact_id}/verify`, { headers: apiHeaders() });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Archived artifact verification failed.");
      }
      setArtifactVerification(payload.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Archived artifact verification failed.");
    } finally {
      setArtifactVerifyingId("");
    }
  }

  async function downloadGovernanceAttestation() {
    setGovernanceLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/governance/attestation/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Governance attestation export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "governance_attestation.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshGovernanceAttestation();
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Governance attestation export failed.");
    } finally {
      setGovernanceLoading(false);
    }
  }

  async function downloadProductionAudit() {
    setProductionAuditLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/deployment/audit/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Production audit export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "production_audit.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshProductionAudit();
      void refreshAuditTrail();
      void refreshArtifacts();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Production audit export failed.");
    } finally {
      setProductionAuditLoading(false);
    }
  }

  async function downloadStorageMigrationBundle() {
    setStorageLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/storage/migration/sqlite/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Storage migration export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "sqlite_to_postgres_migration_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Storage migration export failed.");
    } finally {
      setStorageLoading(false);
    }
  }

  async function planArtifactRetention() {
    setRetentionLoading(true);
    setError("");
    try {
      const policy = artifactSummary?.retention_policy;
      const retentionDays = policy?.enabled ? policy.retention_days : 30;
      const keepMin = policy?.keep_min ?? 100;
      const response = await fetch(
        `${API_BASE}/artifacts/retention/apply?dry_run=true&retention_days=${retentionDays}&keep_min=${keepMin}`,
        {
          method: "POST",
          headers: apiHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({})
        }
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Artifact retention planning failed.");
      }
      setRetentionPlan(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Artifact retention planning failed.");
    } finally {
      setRetentionLoading(false);
    }
  }

  async function inspectRag() {
    const query = ragQuery.trim() || `${gene} ${brainRegion} ${cellType} ${modality}`;
    setRagInspectLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/evaluate`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(ragEvaluationRequest(query))
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "RAG inspection failed.");
      }
      setRagInspection(payload.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG inspection failed.");
    } finally {
      setRagInspectLoading(false);
    }
  }

  function ragEvaluationRequest(query = ragQuery.trim() || `${gene} ${brainRegion} ${cellType} ${modality}`) {
    return {
      query,
      filters: {
        gene,
        brain_region: brainRegion,
        cell_type: cellType,
        modality,
        species: "human"
      },
      limit: 8
    };
  }

  async function verifyRagEvaluationBundle() {
    setRagBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/evaluate/export/verify`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(ragEvaluationRequest())
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "RAG evaluation bundle verification failed.");
      }
      setRagBundleVerification(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG evaluation bundle verification failed.");
    } finally {
      setRagBundleLoading(false);
    }
  }

  async function downloadRagEvaluationBundle() {
    setRagBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/evaluate/export.zip`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(ragEvaluationRequest())
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "RAG evaluation bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "rag_evaluation_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
      void verifyRagEvaluationBundle();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG evaluation bundle export failed.");
      setRagBundleLoading(false);
    }
  }

  async function verifyRagRegressionBundle() {
    setRagRegressionBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/regression/export/verify`, { headers: apiHeaders() });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "RAG regression bundle verification failed.");
      }
      setRagRegressionBundleVerification(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG regression bundle verification failed.");
    } finally {
      setRagRegressionBundleLoading(false);
    }
  }

  async function downloadRagRegressionBundle() {
    setRagRegressionBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/regression/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "RAG regression bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "rag_regression_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
      void verifyRagRegressionBundle();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG regression bundle export failed.");
      setRagRegressionBundleLoading(false);
    }
  }

  async function verifyRagVectorIndexBundle() {
    setRagVectorBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/vector-index/export/verify`, { headers: apiHeaders() });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "RAG vector index bundle verification failed.");
      }
      setRagVectorBundleVerification(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG vector index bundle verification failed.");
    } finally {
      setRagVectorBundleLoading(false);
    }
  }

  async function downloadRagVectorIndexBundle() {
    setRagVectorBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/rag/vector-index/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "RAG vector index bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "rag_vector_index_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
      void verifyRagVectorIndexBundle();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG vector index bundle export failed.");
      setRagVectorBundleLoading(false);
    }
  }

  async function planRagVectorStoreImport() {
    setRagVectorMigrationLoading(true);
    setError("");
    try {
      const [planResponse, importResponse, parityResponse] = await Promise.all([
        fetch(`${API_BASE}/rag/vector-store/import/plan?target_backend=pgvector`, { headers: apiHeaders() }),
        fetch(`${API_BASE}/rag/vector-store/import?target_backend=pgvector&dry_run=true`, {
          method: "POST",
          headers: apiHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({})
        }),
        fetch(`${API_BASE}/rag/vector-store/parity?target_backend=local_json`, { headers: apiHeaders() })
      ]);
      const [planPayload, importPayload, parityPayload] = await Promise.all([planResponse.json(), importResponse.json(), parityResponse.json()]);
      if (!planResponse.ok || !importResponse.ok || !parityResponse.ok) {
        throw new Error(importPayload.detail ?? planPayload.detail ?? parityPayload.detail ?? "RAG vector-store import planning failed.");
      }
      setRagVectorImportPlan(importPayload.data ?? planPayload.data);
      setRagVectorParity(parityPayload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RAG vector-store import planning failed.");
    } finally {
      setRagVectorMigrationLoading(false);
    }
  }

  async function verifyOptimizerBenchmarkBundle() {
    setOptimizerBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/optimizer/benchmark/export/verify`, { headers: apiHeaders() });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Optimizer benchmark bundle verification failed.");
      }
      setOptimizerBundleVerification(payload.data);
      void refreshAuditTrail();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Optimizer benchmark bundle verification failed.");
    } finally {
      setOptimizerBundleLoading(false);
    }
  }

  async function downloadOptimizerBenchmarkBundle() {
    setOptimizerBundleLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/optimizer/benchmark/export.zip`, { headers: apiHeaders() });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail ?? "Optimizer benchmark bundle export failed.");
      }
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "optimizer_benchmark_bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      void refreshAuditTrail();
      void refreshArtifacts();
      void verifyOptimizerBenchmarkBundle();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Optimizer benchmark bundle export failed.");
      setOptimizerBundleLoading(false);
    }
  }

  return (
    <main className="shell">
      <section className="workspace">
        <aside className="control-panel" aria-label="Design controls">
          <div className="brand-row">
            <Dna size={24} aria-hidden />
            <div>
              <h1>Gene Therapy Design Console</h1>
              <p>MANE-aware CDS retrieval and codon optimization MVP</p>
            </div>
          </div>

          <form onSubmit={runDesign} className="form-stack">
            <fieldset className="segmented">
              <legend>Input</legend>
              {(["gene", "cds"] as const).map((item) => (
                <button
                  type="button"
                  key={item}
                  className={inputMode === item ? "active" : ""}
                  onClick={() => setInputMode(item)}
                  title={item === "gene" ? "Use gene input mode" : "Use CDS input mode"}
                >
                  {item === "gene" ? "Gene" : "CDS"}
                </button>
              ))}
            </fieldset>

            <label>
              <span>Gene symbol</span>
              <div className="input-with-icon">
                <Search size={16} aria-hidden />
                <input aria-label="Gene symbol" value={gene} onChange={(event) => setGene(event.target.value.toUpperCase())} />
              </div>
            </label>

            <div className="quick-row" aria-label="Example genes">
              {sampleGenes.map((item) => (
                <button type="button" key={item} onClick={() => setGene(item)} className="chip">
                  {item}
                </button>
              ))}
            </div>

            <label>
              <span>Batch genes</span>
              <textarea
                value={batchGenes}
                onChange={(event) => setBatchGenes(event.target.value.toUpperCase())}
                rows={3}
                aria-label="Batch gene symbols"
              />
            </label>

            <label>
              <span>CDS</span>
              <textarea
                value={cdsInput}
                onChange={(event) => setCdsInput(event.target.value.toUpperCase())}
                rows={3}
                aria-label="CDS sequence"
              />
            </label>

            <label>
              <span>Disease context</span>
              <input aria-label="Disease context" value={diseaseContext} onChange={(event) => setDiseaseContext(event.target.value)} />
            </label>

            <label>
              <span>Brain region</span>
              <input aria-label="Brain region" value={brainRegion} onChange={(event) => setBrainRegion(event.target.value)} />
            </label>

            <label>
              <span>Cell type</span>
              <input aria-label="Cell type" value={cellType} onChange={(event) => setCellType(event.target.value)} />
            </label>

            <fieldset className="segmented">
              <legend>Modality</legend>
              {["AAV", "mRNA", "plasmid", "other"].map((item) => (
                <button
                  type="button"
                  key={item}
                  className={modality === item ? "active" : ""}
                  onClick={() => setModality(item)}
                >
                  {item}
                </button>
              ))}
            </fieldset>

            <div className="numeric-grid">
              <label>
                <span>Population</span>
                <input
                  type="number"
                  min={8}
                  max={500}
                  aria-label="Population size"
                  value={populationSize}
                  onChange={(event) => setPopulationSize(Number(event.target.value))}
                />
              </label>
              <label>
                <span>Generations</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  aria-label="Generations"
                  value={generations}
                  onChange={(event) => setGenerations(Number(event.target.value))}
                />
              </label>
              <label>
                <span>Candidates</span>
                <input
                  type="number"
                  min={1}
                  max={50}
                  aria-label="Candidate count"
                  value={maxCandidates}
                  onChange={(event) => setMaxCandidates(Number(event.target.value))}
                />
              </label>
              <label>
                <span>Seed</span>
                <input type="number" aria-label="Seed" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
              </label>
            </div>

            <div className="button-row">
              <button type="submit" className="primary-action" disabled={loading || (inputMode === "gene" ? !gene.trim() : !cdsInput.trim())} title="Run design">
                {loading ? <Loader2 className="spin" size={18} aria-hidden /> : <Play size={18} aria-hidden />}
                Run design
              </button>
              <button type="button" className="icon-action" onClick={() => runDesign()} disabled={loading} title="Rerun">
                <RefreshCw size={18} aria-hidden />
              </button>
              <button type="button" className="secondary-action" onClick={queueDesignJob} disabled={loading || inputMode !== "gene" || !gene.trim()}>
                <ClipboardCheck size={17} aria-hidden />
                Queue
              </button>
              <button type="button" className="secondary-action" onClick={queueBatchDesignJob} disabled={loading || inputMode !== "gene" || !parseBatchGenes(batchGenes).length}>
                <ClipboardCheck size={17} aria-hidden />
                Batch
              </button>
            </div>
          </form>

          <section className="data-panel" aria-label="Structured data status">
            <div>
              <span>Structured records</span>
              <strong>{dataStatus?.records ?? "n/a"}</strong>
            </div>
            <div className="data-quality">
              <span>Manifest {dataStatus?.manifest_hash ?? "n/a"}</span>
              <span>
                Validation E{dataStatus?.validation?.errors ?? "n/a"} / W{dataStatus?.validation?.warnings ?? "n/a"}
              </span>
              <span>
                Provenance {dataAudit?.status ?? "n/a"} / {dataAudit ? failedAuditChecks(dataAudit) : "n/a"} checks flagged
              </span>
              <span>
                Lock {dataAudit?.lockfile?.status ?? "n/a"} / {(dataAudit?.lockfile?.current_hash ?? "").slice(0, 8) || "n/a"}
              </span>
              <span>
                Release {dataAudit?.release_lock?.status ?? "n/a"} / {(dataAudit?.release_lock?.current_hash ?? "").slice(0, 8) || "n/a"}
              </span>
            </div>
            <div className="data-datasets">
              {["GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA"].map((dataset) => (
                <span key={dataset}>
                  {dataset.replace(" Brain Cell Atlas", "").replace("Kapur brain ", "")}: {dataStatus?.datasets?.[dataset] ?? 0}
                </span>
              ))}
            </div>
            <div className="data-catalog">
              {(dataCatalog?.sources ?? []).slice(0, 4).map((source) => (
                <span key={source.id} title={source.name}>
                  {source.id.replaceAll("_", " ")} / {source.refreshable ? "refreshable" : "pinned"} / {source.release_default ?? "n/a"}
                </span>
              ))}
            </div>
            <div className="data-quality" aria-label="Structured coverage matrix">
              <span>
                Coverage {dataCoverage?.production_readiness.status ?? "n/a"} / live{" "}
                {formatPercent(dataCoverage?.production_readiness.live_record_fraction)} / seed{" "}
                {formatPercent(dataCoverage?.production_readiness.seed_record_fraction)}
              </span>
              <span>
                Targets G{dataCoverage?.gene_count ?? "n/a"} / R{dataCoverage?.brain_region_count ?? "n/a"} / C
                {dataCoverage?.cell_type_count ?? "n/a"}
              </span>
              <span>
                Top gene-region {formatGeneRegionCoverage(dataCoverage?.gene_region_matrix?.[0])}
              </span>
              <span>
                Top cell {formatCellTypeCoverage(dataCoverage?.cell_type_matrix?.[0])}
              </span>
            </div>
            <div className="data-quality" aria-label="Structured quality gate">
              <span>
                Quality {dataQuality?.status ?? "n/a"} / block {dataQuality?.summary.blocking_count ?? "n/a"} / warn{" "}
                {dataQuality?.summary.warning_count_total ?? "n/a"}
              </span>
              <span>
                Live {formatPercent(dataQuality?.coverage.live_record_fraction)} / release{" "}
                {formatPercent(dataQuality?.coverage.release_pinned_fraction)}
              </span>
              <span>
                Datasets P{dataQuality?.summary.pass_count ?? "n/a"} / W{dataQuality?.summary.warning_count ?? "n/a"} / F
                {dataQuality?.summary.fail_count ?? "n/a"}
              </span>
              <span>{dataQuality?.operator_actions?.[0] ?? "Quality gate not checked"}</span>
            </div>
            <div className="data-quality">
              <span>Last refresh {formatRefreshEntry(dataCatalog?.last_refresh ?? dataRefreshLog?.entries.at(-1))}</span>
              <span>Refresh log {dataRefreshLog?.entries.length ?? 0} recent entries</span>
              <span>{formatRefreshSummary(dataRefreshLog?.entries.at(-1))}</span>
            </div>
            <div className="data-quality" aria-label="Reference refresh validation">
              <span>
                Refresh validation {dataRefreshValidation?.status ?? "n/a"} / ops {dataRefreshValidation?.plan.operation_count ?? "n/a"}
              </span>
              <span>
                Sources {dataRefreshValidation?.plan.sources.join(" / ") || "n/a"}
              </span>
              <span>
                Lock {dataRefreshValidation?.release_lock.status ?? "n/a"} /{" "}
                {dataRefreshValidation?.release_lock.current_hash?.slice(0, 8) ?? "n/a"}
              </span>
              <span>{dataRefreshValidation?.errors[0] ?? dataRefreshValidation?.warnings[0] ?? "Plan not validated"}</span>
            </div>
            <div className="data-quality" aria-label="Structured import preview">
              <span>
                Import preview {structuredImportPreview?.status ?? "n/a"} / {structuredImportPreview?.source?.file ?? dataManifest?.files?.[0]?.file ?? "n/a"}
              </span>
              <span>
                Projected E{structuredImportPreview?.validation?.projected?.error_count ?? "n/a"} / W
                {structuredImportPreview?.validation?.projected?.warning_count ?? "n/a"}
              </span>
              <span>
                Projected hash {structuredImportPreview?.manifest?.projected_hash?.slice(0, 10) ?? "n/a"} / replace{" "}
                {structuredImportPreview ? (structuredImportPreview.target.would_replace ? "yes" : "no") : "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="External source snapshot coverage">
              <span>
                Source snapshots {formatPercent(dataAudit?.external_source_coverage?.source_snapshot_path_fraction)}
              </span>
              <span>
                Payload hashes {formatPercent(dataAudit?.external_source_coverage?.source_payload_hash_fraction)}
              </span>
              <span>
                Missing snapshots {dataAudit?.external_source_coverage?.records_missing_snapshot.length ?? "n/a"} / hashes{" "}
                {dataAudit?.external_source_coverage?.records_missing_payload_hash.length ?? "n/a"}
              </span>
              <span>
                Backfill{" "}
                {externalBackfill
                  ? `${externalBackfill.status} / ${externalBackfill.dry_run ? externalBackfill.candidate_record_count : externalBackfill.backfilled_record_count} records`
                  : "not run"}
              </span>
            </div>
            <div className="data-actions">
              <button type="button" onClick={importGtexForGene} disabled={dataLoading || !gene.trim()} title="Import GTEx expression">
                {dataLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Database size={15} aria-hidden />}
                GTEx
              </button>
              <button type="button" onClick={importAllenTaxonomy} disabled={dataLoading} title="Import Allen taxonomy">
                <BookOpenText size={15} aria-hidden />
                Allen
              </button>
              <button type="button" onClick={refreshReferencePanel} disabled={dataLoading || !gene.trim()} title="Refresh GTEx and Allen reference panel">
                <ClipboardCheck size={15} aria-hidden />
                Panel
              </button>
              <button type="button" onClick={validateReferencePanel} disabled={dataLoading || !gene.trim()} title="Validate reference refresh plan">
                <ShieldCheck size={15} aria-hidden />
                Validate
              </button>
              <button type="button" onClick={previewStructuredImport} disabled={dataLoading || !dataManifest?.files?.length} title="Preview structured import impact">
                <Search size={15} aria-hidden />
                Preview
              </button>
              <button type="button" onClick={downloadDataSnapshot} disabled={dataLoading} title="Export data snapshot">
                <Download size={15} aria-hidden />
                Snapshot
              </button>
              <button type="button" onClick={downloadDataRefreshPlanBundle} disabled={dataLoading || !gene.trim()} title="Export data refresh plan audit bundle">
                <Download size={15} aria-hidden />
                Refresh ZIP
              </button>
              <button type="button" onClick={writeDataLockfile} disabled={dataLoading} title="Write data lockfile">
                <ShieldCheck size={15} aria-hidden />
                Lock
              </button>
              <button type="button" onClick={writeDataReleaseLock} disabled={dataLoading} title="Write data release lockfile">
                <ShieldCheck size={15} aria-hidden />
                Release
              </button>
              <button type="button" onClick={recordDataBaseline} disabled={dataLoading} title="Record data audit baseline">
                <ClipboardCheck size={15} aria-hidden />
                Audit
              </button>
              <button type="button" onClick={() => backfillExternalSourceSnapshots(true)} disabled={dataLoading} title="Plan external source snapshot backfill">
                <Search size={15} aria-hidden />
                Plan
              </button>
              <button type="button" onClick={() => backfillExternalSourceSnapshots(false)} disabled={dataLoading} title="Apply external source snapshot backfill">
                <ShieldCheck size={15} aria-hidden />
                Backfill
              </button>
              <button type="button" onClick={refreshDataStatus} disabled={dataLoading} title="Refresh data status">
                <RefreshCw size={15} aria-hidden />
              </button>
            </div>
          </section>

          <section className="history-panel" aria-label="Quality gates">
            <div className="history-heading">
              <span>Quality Gates</span>
              <button type="button" onClick={refreshQualityGates} disabled={qualityLoading} title="Refresh quality gates">
                {qualityLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <RefreshCw size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>RAG {ragRegression?.status ?? "n/a"}</span>
              <span>OPT {optimizerBenchmark?.status ?? "n/a"}</span>
              <span>{qualityLoading ? "Loading" : "Ready"}</span>
            </div>
            <div className="quality-list">
              <article>
                <strong>
                  <Gauge size={14} aria-hidden />
                  RAG regression
                </strong>
                <div className="quality-metrics">
                  <span>{ragRegression ? `${ragRegression.pass_count}/${ragRegression.case_count} pass` : "n/a"}</span>
                  <span>Recall {formatMetric(ragRegression?.macro.recall_at_k)}</span>
                  <span>nDCG {formatMetric(ragRegression?.macro.ndcg_at_k)}</span>
                  <span>Source {formatMetric(ragRegression?.macro.source_coverage)}</span>
                </div>
                <small>{qualityIssueText(ragRegression?.results)}</small>
              </article>
              <article>
                <strong>
                  <Dna size={14} aria-hidden />
                  Optimizer benchmark
                </strong>
                <div className="quality-metrics">
                  <span>{optimizerBenchmark ? `${optimizerBenchmark.pass_count}/${optimizerBenchmark.case_count} pass` : "n/a"}</span>
                  <span>Viol {formatMetric(optimizerBenchmark?.macro.constraint_violation_rate)}</span>
                  <span>Unique {formatMetric(optimizerBenchmark?.macro.unique_cds_count, 1)}</span>
                  <span>HV {formatMetric(optimizerBenchmark?.macro.approx_hypervolume_2d)}</span>
                  <span>Delta {formatMetric(optimizerBenchmark?.macro.recommended_composite_delta)}</span>
                  <span>Runtime {formatMetric(optimizerBenchmark?.macro.runtime_ms, 1)} ms</span>
                </div>
                <div className="quality-metrics">
                  <span>Diag {optimizerDiagnostics?.status ?? "n/a"}</span>
                  <span>Diversity {optimizerDiagnostics?.quality_bands.candidate_diversity ?? "n/a"}</span>
                  <span>Constraints {optimizerDiagnostics?.quality_bands.constraint_control ?? "n/a"}</span>
                  <span>Runtime {optimizerDiagnostics?.quality_bands.runtime ?? "n/a"}</span>
                </div>
                <div className="quality-metrics">
                  <span>Alg {optimizerDiagnostics?.optimizer?.algorithm ?? optimizerDiagnostics?.optimizer?.search_strategy?.algorithm ?? "n/a"}</span>
                  <span>Seed {optimizerDiagnostics?.optimizer?.seed_strategy ?? optimizerDiagnostics?.optimizer?.search_strategy?.seed_strategy ?? "n/a"}</span>
                  <span>Variants {optimizerDiagnostics?.optimizer?.search_strategy?.deterministic_seed_variants?.length ?? "n/a"}</span>
                  <span>
                    Repair{" "}
                    {optimizerDiagnostics?.optimizer?.search_strategy?.repair_policy?.enabled
                      ? `${optimizerDiagnostics.optimizer.search_strategy.repair_policy.repair_passes ?? "n/a"}x`
                      : "off"}
                  </span>
                </div>
                <div className="quality-metrics">
                  <span>Stress {optimizerStress?.status ?? optimizerDiagnostics?.stress_gate?.status ?? "n/a"}</span>
                  <span>Stress pass {optimizerStress?.summary.pass_count ?? optimizerDiagnostics?.stress_gate?.summary.pass_count ?? "n/a"}</span>
                  <span>Stress warn {optimizerStress?.summary.warning_count ?? optimizerDiagnostics?.stress_gate?.summary.warning_count ?? "n/a"}</span>
                  <span>Stress fail {optimizerStress?.summary.fail_count ?? optimizerDiagnostics?.stress_gate?.summary.fail_count ?? "n/a"}</span>
                </div>
                <div className="quality-metrics">
                  <span>Folding {optimizerDiagnostics?.rna_folding?.status ?? "n/a"}</span>
                  <span>Backend {optimizerDiagnostics?.rna_folding?.active_backend ?? "n/a"}</span>
                  <span>Prod {optimizerDiagnostics?.rna_folding?.production_ready ? "ready" : "warning"}</span>
                </div>
                <div className="data-actions retention-actions">
                  <button
                    type="button"
                    onClick={verifyOptimizerBenchmarkBundle}
                    disabled={optimizerBundleLoading}
                    title="Verify optimizer benchmark audit bundle"
                  >
                    {optimizerBundleLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                    Verify
                  </button>
                  <button
                    type="button"
                    onClick={downloadOptimizerBenchmarkBundle}
                    disabled={optimizerBundleLoading}
                    title="Export optimizer benchmark audit bundle"
                  >
                    <Download size={15} aria-hidden />
                    Bundle
                  </button>
                </div>
                <div className="data-quality" aria-label="Optimizer benchmark bundle verification">
                  <span>Bundle {optimizerBundleVerification?.semantic_status ?? "n/a"}</span>
                  <span>Cases {optimizerBundleVerification?.case_count ?? optimizerBenchmark?.case_count ?? "n/a"}</span>
                  <span>Case hash {optimizerBundleVerification?.cases_hash?.slice(0, 10) ?? optimizerBenchmark?.cases_hash?.slice(0, 10) ?? "n/a"}</span>
                  <span>Result hash {optimizerBundleVerification?.results_hash?.slice(0, 10) ?? optimizerBenchmark?.results_hash?.slice(0, 10) ?? "n/a"}</span>
                  <span>Metrics hash {optimizerBundleVerification?.case_metrics_hash?.slice(0, 10) ?? "n/a"}</span>
                  <span>Strategy {optimizerBundleVerification?.semantic_checks?.search_strategy_schema ?? "n/a"}</span>
                  <span>{optimizerBundleVerification?.warnings?.[0] ?? optimizerBundleVerification?.errors?.[0] ?? "Benchmark bundle not verified"}</span>
                </div>
                <small>{qualityIssueText(optimizerBenchmark?.results)}</small>
                <small>
                  {optimizerDiagnostics?.rna_folding?.recommendation ??
                    optimizerStress?.recommendations?.[0] ??
                    optimizerDiagnostics?.recommendations?.[0] ??
                    "Optimizer diagnostics not loaded."}
                </small>
                {optimizerBenchmark?.results?.length ? (
                  <div className="benchmark-case-list">
                    {optimizerBenchmark.results.slice(0, 3).map((result) => (
                      <div key={result.case_id}>
                        <strong>{result.case_id} / {result.status}</strong>
                        <span>{optimizerCaseSummary(result.metrics)}</span>
                        <small>{[...result.errors, ...result.warnings][0] ?? result.recommended_candidate_id ?? "constraints satisfied"}</small>
                      </div>
                    ))}
                  </div>
                ) : null}
              </article>
            </div>
          </section>

          <section className="history-panel" aria-label="RAG inspector">
            <div className="history-heading">
              <span>RAG Inspector</span>
              <button type="button" onClick={inspectRag} disabled={ragInspectLoading} title="Evaluate RAG query">
                {ragInspectLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Search size={15} aria-hidden />}
              </button>
            </div>
            <label className="rag-query-box">
              <span>Query</span>
              <textarea
                value={ragQuery}
                onChange={(event) => setRagQuery(event.target.value)}
                rows={3}
                placeholder="gene region cell type modality"
              />
            </label>
            <div className="data-actions retention-actions">
              <button type="button" onClick={inspectRag} disabled={ragInspectLoading || !ragQuery.trim()} title="Run RAG evaluation">
                {ragInspectLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Search size={15} aria-hidden />}
                Evaluate
              </button>
              <button
                type="button"
                onClick={() => setRagQuery(`${gene} ${brainRegion} ${cellType} ${modality}`)}
                disabled={ragInspectLoading}
                title="Use current design context"
              >
                <ClipboardCheck size={15} aria-hidden />
                Context
              </button>
              <button
                type="button"
                onClick={verifyRagEvaluationBundle}
                disabled={ragBundleLoading || !ragQuery.trim()}
                title="Verify RAG evaluation audit bundle"
              >
                {ragBundleLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                Verify
              </button>
              <button
                type="button"
                onClick={downloadRagEvaluationBundle}
                disabled={ragBundleLoading || !ragQuery.trim()}
                title="Export RAG evaluation audit bundle"
              >
                <Download size={15} aria-hidden />
                Bundle
              </button>
              <button
                type="button"
                onClick={verifyRagRegressionBundle}
                disabled={ragRegressionBundleLoading}
                title="Verify RAG regression suite audit bundle"
              >
                {ragRegressionBundleLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                Regress
              </button>
              <button
                type="button"
                onClick={downloadRagRegressionBundle}
                disabled={ragRegressionBundleLoading}
                title="Export RAG regression suite audit bundle"
              >
                <Download size={15} aria-hidden />
                Suite
              </button>
              <button
                type="button"
                onClick={verifyRagVectorIndexBundle}
                disabled={ragVectorBundleLoading}
                title="Verify full RAG vector index migration bundle"
              >
                {ragVectorBundleLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                Vector
              </button>
              <button
                type="button"
                onClick={downloadRagVectorIndexBundle}
                disabled={ragVectorBundleLoading}
                title="Export full RAG vector index migration bundle"
              >
                <Download size={15} aria-hidden />
                Index
              </button>
              <button
                type="button"
                onClick={planRagVectorStoreImport}
                disabled={ragVectorMigrationLoading}
                title="Dry-run pgvector import planning and local parity"
              >
                {ragVectorMigrationLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Database size={15} aria-hidden />}
                Plan
              </button>
            </div>
            <div className="audit-summary">
              <span>Hits {ragInspection?.result_count ?? "n/a"}</span>
              <span>Missing {ragInspection?.missing_facets?.length ?? "n/a"}</span>
              <span>{ragInspection?.top_sources?.[0]?.source ?? "source n/a"}</span>
              <span>Trace {ragInspection?.query_fingerprint?.slice(0, 10) ?? "n/a"}</span>
              <span>Bundle {ragBundleVerification?.semantic_status ?? "n/a"}</span>
              <span>Vector {ragVectorBundleVerification?.semantic_status ?? "n/a"}</span>
              <span>Import {ragVectorImportPlan?.status ?? "n/a"}</span>
            </div>
            <div className="data-quality" aria-label="RAG diagnostics">
              <span>
                Diagnostics {ragDiagnostics?.status ?? "n/a"} / {ragDiagnostics?.index.chunk_count ?? "n/a"} chunks
              </span>
              <span>
                Regression {ragDiagnostics?.regression.status ?? ragRegression?.status ?? "n/a"} / recall{" "}
                {formatMetric(ragDiagnostics?.regression.macro.recall_at_k ?? ragRegression?.macro.recall_at_k, 2)}
              </span>
              <span>
                Corpus {ragDiagnostics?.distributions.sources.slice(0, 3).map((source) => `${source.value} ${source.count}`).join(" / ") || "n/a"}
              </span>
              <span>
                Tokens mean {formatMetric(ragDiagnostics?.token_stats.mean, 1)} / embed{" "}
                {formatMetric(ragDiagnostics?.embedding_stats.mean_nonzero_dimensions, 1)}
              </span>
              <span>
                Embed {ragDiagnostics?.embedding_backend?.active_backend ?? "n/a"} /{" "}
                {ragDiagnostics?.embedding_backend?.embedding_model ?? ragDiagnostics?.index.embedding_model ?? "n/a"} /{" "}
                {ragDiagnostics?.embedding_backend?.production_ready ? "prod" : "warn"}
              </span>
              <span>
                Embed request {ragDiagnostics?.embedding_backend?.requested_backend ?? "n/a"} -&gt;{" "}
                {ragDiagnostics?.embedding_backend?.active_backend ?? "n/a"} / fallback{" "}
                {ragDiagnostics?.embedding_backend?.fallback_active ? "yes" : "no"}
              </span>
              <span>
                Embed fingerprint {(ragDiagnostics?.embedding_backend?.model_fingerprint_hash ?? "").slice(0, 10) || "n/a"} /{" "}
                {ragDiagnostics?.embedding_backend?.model_fingerprint?.production_candidate ? "candidate" : "dev"}
              </span>
              <span>
                OpenAI key {ragDiagnostics?.embedding_backend?.openai?.api_key_configured ? "set" : "not set"} / dims{" "}
                {ragDiagnostics?.embedding_backend?.openai?.configured_dimensions ?? ragDiagnostics?.embedding_backend?.embedding_dimensions ?? "n/a"}
              </span>
              <span>
                OpenAI cache {ragDiagnostics?.embedding_backend?.openai?.cache?.entries ?? "n/a"} entries /{" "}
                {ragDiagnostics?.embedding_backend?.openai?.cache?.enabled ? "on" : "off"}
              </span>
              <span>
                OpenAI tokens {ragDiagnostics?.embedding_backend?.openai?.cache?.estimated_input_tokens ?? "n/a"} / spend{" "}
                {formatCurrency(ragDiagnostics?.embedding_backend?.openai?.budget?.estimated_spend_usd)}
              </span>
              <span>
                OpenAI budget {formatCurrency(ragDiagnostics?.embedding_backend?.openai?.budget?.budget_usd)} / left{" "}
                {formatCurrency(ragDiagnostics?.embedding_backend?.openai?.budget?.estimated_remaining_usd)}
              </span>
              <span>
                OpenAI guard {ragDiagnostics?.embedding_backend?.openai?.budget?.within_budget ? "within" : "blocked"} /{" "}
                {ragDiagnostics?.embedding_backend?.openai?.budget?.budget_exceeded ? "exceeded" : "ok"}
              </span>
              <span>
                Cache hash {ragDiagnostics?.embedding_backend?.openai?.cache?.file_sha256?.slice(0, 10) ?? "n/a"} / keys{" "}
                {ragDiagnostics?.embedding_backend?.openai?.cache?.entry_keys_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Vector {ragDiagnostics?.vector_store_readiness?.target_backend ?? ragDiagnostics?.vector_store_readiness?.runtime?.target_backend ?? "n/a"} /{" "}
                {ragDiagnostics?.vector_store_readiness?.active_backend ?? "n/a"} -&gt;{" "}
                {ragDiagnostics?.vector_store_readiness?.recommended_backend ?? "n/a"} /{" "}
                {ragDiagnostics?.vector_store_readiness?.status ?? "n/a"}
              </span>
              <span>
                Policy {ragDiagnostics?.index.chunking_policy?.version ?? "n/a"} / max{" "}
                {ragDiagnostics?.index.chunking_policy?.max_lexical_tokens ?? "n/a"} tokens
              </span>
              <span>
                Payload missing {ragDiagnostics?.vector_store_readiness?.required_metadata_missing_chunks ?? "n/a"} / facets{" "}
                {formatPercent(ragDiagnostics?.vector_store_readiness?.facet_completeness?.regions)}
              </span>
              <span>
                Bundle hash {ragBundleVerification?.bundle_fingerprint?.slice(0, 10) ?? "n/a"} / results{" "}
                {ragBundleVerification?.result_count ?? ragInspection?.result_count ?? "n/a"}
              </span>
              <span>
                Regression bundle {ragRegressionBundleVerification?.semantic_status ?? "n/a"} / cases{" "}
                {ragRegressionBundleVerification?.case_count ?? ragDiagnostics?.regression.case_count ?? "n/a"}
              </span>
              <span>
                Weak {ragRegressionBundleVerification?.weak_case_count ?? ragDiagnostics?.regression.weak_cases.length ?? "n/a"} / hash{" "}
                {ragRegressionBundleVerification?.cases_hash?.slice(0, 10) ?? ragDiagnostics?.regression.cases_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                RAG result hash{" "}
                {ragRegressionBundleVerification?.results_hash?.slice(0, 10) ??
                  ragDiagnostics?.regression.results_hash?.slice(0, 10) ??
                  ragRegression?.results_hash?.slice(0, 10) ??
                  "n/a"}
              </span>
              <span>
                RAG metrics hash {ragRegressionBundleVerification?.case_metrics_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Vector bundle {ragVectorBundleVerification?.chunk_count ?? ragDiagnostics?.index.chunk_count ?? "n/a"} chunks /{" "}
                {ragVectorBundleVerification?.recommended_backend ?? ragDiagnostics?.vector_store_readiness?.recommended_backend ?? "n/a"}
              </span>
              <span>
                Runtime {ragDiagnostics?.vector_store_readiness?.runtime?.status ?? "n/a"} / fallback{" "}
                {ragDiagnostics?.vector_store_readiness?.runtime?.fallback_active ? "yes" : "no"}
              </span>
              <span>
                Import plan {ragVectorImportPlan?.target_backend ?? "pgvector"} / {ragVectorImportPlan?.source?.records ?? ragDiagnostics?.index.chunk_count ?? "n/a"} rows
              </span>
              <span>
                Vector parity {ragVectorParity?.status ?? "n/a"} / hash {ragVectorParity?.comparison?.row_hash_match ? "match" : "n/a"}
              </span>
              <span>{ragDiagnostics?.recommendations?.[0] ?? "Diagnostics not loaded"}</span>
            </div>
            {ragInspection ? (
              <div className="rag-inspector-results">
                <div className="data-quality">
                  <span>Coverage {formatRagCoverage(ragInspection.coverage)}</span>
                  <span>Missing facets {ragInspection.missing_facets.length ? ragInspection.missing_facets.join(", ") : "none"}</span>
                  <span>Terms {(ragInspection.recommended_query_terms ?? []).slice(0, 5).join(", ") || "n/a"}</span>
                  <span>
                    Policy {ragInspection.retrieval_trace?.ranking_policy ?? ragInspection.ranking_policy?.version ?? "n/a"} / aliases{" "}
                    {ragInspection.retrieval_trace?.aliases_added_count ?? "n/a"}
                  </span>
                  <span>
                    Query terms {formatMetric(ragInspection.query_term_coverage?.coverage_fraction, 2)} / missing{" "}
                    {ragInspection.query_term_coverage?.missing_query_tokens.slice(0, 4).join(", ") || "none"}
                  </span>
                  <span>
                    Suff {ragInspection.evidence_sufficiency?.status ?? "n/a"} / src{" "}
                    {ragInspection.evidence_sufficiency?.source_count ?? "n/a"} / high{" "}
                    {ragInspection.evidence_sufficiency?.high_confidence_count ?? "n/a"}
                  </span>
                </div>
                <div className="data-quality" aria-label="RAG facet gap analysis">
                  <span>Facet gaps {(ragInspection.facet_gap_analysis?.missing_from_results ?? []).join(", ") || "none"}</span>
                  <span>Corpus gaps {(ragInspection.facet_gap_analysis?.missing_from_corpus ?? []).join(", ") || "none"}</span>
                  <span>Suff check {ragBundleVerification?.semantic_checks?.evidence_sufficiency_schema ?? "n/a"}</span>
                  <span>{ragFacetGapSummary(ragInspection.facet_gap_analysis)}</span>
                </div>
                <div className="history-list">
                  {ragInspection.score_breakdown.slice(0, 3).map((item) => (
                    <article className="rag-result-row" key={item.chunk_id}>
                      <strong>{item.title}</strong>
                      <span>{item.source} / {item.collection} / score {item.score.toFixed(3)}</span>
                      <small>
                        v {item.vector_score.toFixed(2)} / bm25 {item.bm25_score.toFixed(2)} / rerank {item.rerank_score.toFixed(2)}
                      </small>
                      <small>{item.rationale?.[0] ?? "retrieval rationale unavailable"}</small>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <p className="rag-empty">Run an evaluation to inspect retrieval coverage.</p>
            )}
          </section>

          <section className="history-panel" aria-label="Run history">
            <div className="history-heading">
              <span>Run history</span>
              <button type="button" onClick={refreshRuns} disabled={historyLoading} title="Refresh run history">
                <RefreshCw size={15} aria-hidden />
              </button>
            </div>
            <div className="history-list">
              {runs.length ? (
                runs.map((run) => (
                  <button type="button" key={run.run_id} onClick={() => openRun(run.run_id)}>
                    <strong>{run.gene ?? "CDS"} - {run.recommended_candidate_id ?? "n/a"}</strong>
                    <span>{run.brain_region ?? "region n/a"} / {run.cell_type ?? "cell n/a"}</span>
                    <small>{run.run_id}</small>
                  </button>
                ))
              ) : (
                <p>{historyLoading ? "Loading runs..." : "No saved runs yet."}</p>
              )}
            </div>
          </section>

          <section className="history-panel" aria-label="Agent memory">
            <div className="history-heading">
              <span>Agent memory</span>
              <button type="button" onClick={() => refreshAgentMemory()} disabled={agentMemoryLoading} title="Refresh agent memory">
                {agentMemoryLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <BookOpenText size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Mem {metricsSnapshot?.agent_memory?.memory_count ?? agentMemories.length}</span>
              <span>Genes {metricsSnapshot?.agent_memory?.distinct_genes ?? "n/a"}</span>
              <span>{selectedAgentMemory?.memory_schema?.replace("agentic-rag-", "") ?? "memory-v1"}</span>
            </div>
            <div className="history-list agent-memory-list">
              {agentMemories.length ? (
                agentMemories.map((memory) => (
                  <button
                    type="button"
                    key={memory.run_id}
                    className={selectedAgentMemory?.run_id === memory.run_id ? "active-memory" : undefined}
                    onClick={() => openAgentMemory(memory.run_id)}
                  >
                    <strong>{memory.gene ?? "CDS"} - {memory.recommended_candidate_id ?? "n/a"}</strong>
                    <span>{memory.brain_region ?? "region n/a"} / {memory.cell_type ?? "cell n/a"}</span>
                    <small>{memory.run_id} / {memory.run_type}</small>
                  </button>
                ))
              ) : (
                <p>{agentMemoryLoading ? "Loading memories..." : "No agent memories indexed yet."}</p>
              )}
            </div>
            {selectedAgentMemory ? (
              <div className="agent-memory-detail">
                <div className="security-grid">
                  <span>Supported {selectedAgentMemory.semantic_memory?.supported_rules?.length ?? 0}</span>
                  <span>Uncertain {selectedAgentMemory.semantic_memory?.uncertain_rules?.length ?? 0}</span>
                  <span>Rejected {selectedAgentMemory.semantic_memory?.rejected_rules?.length ?? 0}</span>
                  <span>Trace {selectedAgentMemory.session_memory?.trace_steps?.length ?? "n/a"}</span>
                  <span>Feasible {selectedAgentMemory.semantic_memory?.candidate_diagnostics?.feasible_count ?? "n/a"}</span>
                  <span>Candidates {selectedAgentMemory.semantic_memory?.candidate_diagnostics?.candidate_count ?? "n/a"}</span>
                </div>
                <div className="governance-card">
                  <span>Policy {selectedAgentMemory.semantic_memory?.candidate_diagnostics?.selection_policy ?? "n/a"}</span>
                  <span>
                    Best objectives{" "}
                    {selectedAgentMemory.semantic_memory?.candidate_diagnostics?.recommendation_audit?.best_objective_count ?? "n/a"}
                  </span>
                  <span>
                    Max regret {formatMetric(selectedAgentMemory.semantic_memory?.candidate_diagnostics?.recommendation_audit?.max_regret, 3)}
                  </span>
                  <span>
                    Composite {formatMetric(selectedAgentMemory.artifact_memory?.recommended_scores?.composite_quality, 3)} / structure{" "}
                    {formatMetric(selectedAgentMemory.artifact_memory?.recommended_scores?.secondary_structure_proxy_score, 3)}
                  </span>
                </div>
                <div className="data-quality">
                  <span>Memory hash {selectedAgentMemory.memory_hash?.slice(0, 12) ?? "n/a"}</span>
                  <span>Design {selectedAgentMemory.artifact_memory?.design_hash?.slice(0, 12) ?? "n/a"}</span>
                  <span>Optimizer {selectedAgentMemory.artifact_memory?.optimizer_manifest_hash?.slice(0, 12) ?? "n/a"}</span>
                </div>
              </div>
            ) : null}
          </section>

          <section className="history-panel" aria-label="Job queue">
            <div className="history-heading">
              <span>Job queue</span>
              <button type="button" onClick={refreshJobs} disabled={jobsLoading} title="Refresh jobs">
                <RefreshCw size={15} aria-hidden />
              </button>
            </div>
            <div className="history-list">
              {jobs.length ? (
                jobs.map((job) => (
                  <article className="job-row" key={job.job_id}>
                    <button type="button" onClick={() => firstJobRunId(job) && openRun(firstJobRunId(job)!)} disabled={!firstJobRunId(job)}>
                      <strong>{job.job_type.replaceAll("_", " ")} - {job.status}</strong>
                      <span>{jobStatusText(job)}</span>
                      <small>{job.job_id}</small>
                    </button>
                    <button type="button" className="job-export" onClick={() => downloadJobBundle(job.job_id)} title="Export job bundle">
                      <Download size={15} aria-hidden />
                    </button>
                  </article>
                ))
              ) : (
                <p>{jobsLoading ? "Loading jobs..." : "No queued jobs yet."}</p>
              )}
            </div>
          </section>

          <section className="history-panel" aria-label="Operational audit trail">
            <div className="history-heading">
              <span>Audit trail</span>
              <button type="button" onClick={refreshAuditTrail} disabled={auditLoading} title="Refresh audit trail">
                {auditLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <RefreshCw size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Total {auditTrail?.summary.total_events ?? "n/a"}</span>
              <span>Pass {auditTrail?.summary.by_outcome?.pass ?? 0}</span>
              <span>Failed {auditTrail?.summary.by_outcome?.failed ?? 0}</span>
            </div>
            <div className="history-list audit-list">
              {auditTrail?.events.length ? (
                auditTrail.events.map((event) => (
                  <article key={event.event_id}>
                    <strong>{event.action.replaceAll("_", " ")} - {event.outcome}</strong>
                    <span>{event.event_type} / {event.resource_type ?? "resource"} {event.resource_id ?? "n/a"}</span>
                    <small>{event.actor} / {event.request_id?.slice(0, 12) ?? "system"}</small>
                  </article>
                ))
              ) : (
                <p>{auditLoading ? "Loading audit trail..." : "No audit events yet."}</p>
              )}
            </div>
          </section>

          <section className="history-panel" aria-label="Security controls">
            <div className="history-heading">
              <span>Security</span>
              <button type="button" onClick={refreshSecurityStatus} disabled={securityLoading} title="Refresh security controls">
                {securityLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Auth {securityStatus?.auth_enabled ? "on" : "off"}</span>
              <span>RBAC {securityStatus?.rbac_enabled ? "on" : "off"}</span>
              <span>Rate {securityStatus?.rate_limit_per_minute ?? "n/a"}/m</span>
            </div>
            <div className="security-grid">
              <span>Keys {securityStatus?.configured_keys ?? "n/a"}</span>
              <span>Roles {(securityStatus?.roles ?? []).join(", ") || "n/a"}</span>
              <span>Header {securityStatus?.api_key_header ?? "n/a"}</span>
              <span>Bearer {securityStatus?.bearer_auth_supported ? "yes" : "no"}</span>
              <span>HMAC {securityStatus?.signing?.hmac?.signing_enabled ? securityStatus.signing.hmac.key_id ?? "on" : "off"}</span>
              <span>Ed25519 {securityStatus?.signing?.ed25519?.verification_enabled ? securityStatus.signing.ed25519.key_id ?? "on" : "off"}</span>
            </div>
            <div className="governance-card">
              <span>Signing {securityStatus?.signing?.status ?? "n/a"}</span>
              <span>Public paths {securityStatus?.public_paths?.length ?? "n/a"}</span>
              <span>Admin policy {securityStatus?.role_policy?.admin ?? "n/a"}</span>
            </div>
          </section>

          <section className="history-panel" aria-label="Operational metrics">
            <div className="history-heading">
              <span>Observability</span>
              <button type="button" onClick={refreshMetricsSnapshot} disabled={metricsLoading} title="Refresh operational metrics">
                {metricsLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <BarChart3 size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Req {totalRequestCount(metricsSnapshot)}</span>
              <span>Runs {metricsSnapshot?.stores?.runs_observed ?? "n/a"}</span>
              <span>Jobs {metricsSnapshot?.stores?.jobs_observed ?? "n/a"}</span>
            </div>
            <div className="security-grid">
              <span>Uptime {formatDuration(metricsSnapshot?.uptime_seconds)}</span>
              <span>RAG {metricsSnapshot?.rag?.chunks ?? "n/a"} chunks</span>
              <span>Data {metricsSnapshot?.structured?.records ?? "n/a"} records</span>
              <span>Audit {metricsSnapshot?.audit?.total_events ?? "n/a"} events</span>
              <span>Artifacts {metricsSnapshot?.stores?.artifacts_observed ?? "n/a"}</span>
              <span>{formatBytes(metricsSnapshot?.stores?.artifact_bytes ?? 0)}</span>
            </div>
            <div className="metrics-list">
              {topRequests(metricsSnapshot).map((request) => (
                <article key={`${request.method}-${request.path}`}>
                  <strong>{request.method} {request.path}</strong>
                  <span>{request.count} calls / avg {request.avg_duration_ms.toFixed(1)} ms / max {request.max_duration_ms.toFixed(1)} ms</span>
                  <small>{Object.entries(request.status_counts).map(([key, value]) => `${key} ${value}`).join(" / ")}</small>
                </article>
              ))}
              {!metricsSnapshot?.requests.length ? <p className="rag-empty">No request metrics recorded yet.</p> : null}
            </div>
            <div className="data-quality">
              <span>Provenance {metricsSnapshot?.data_provenance?.status ?? "n/a"} / failed {metricsSnapshot?.data_provenance?.failed_checks ?? "n/a"}</span>
              <span>Memory {metricsSnapshot?.agent_memory?.memory_count ?? "n/a"} / genes {metricsSnapshot?.agent_memory?.distinct_genes ?? "n/a"}</span>
              <span>Jobs {formatJobStatusCounts(metricsSnapshot?.stores?.job_status_counts)}</span>
            </div>
          </section>

          <section className="history-panel" aria-label="Deployment readiness">
            <div className="history-heading">
              <span>Deployment</span>
              <button type="button" onClick={refreshDeploymentReadiness} disabled={deploymentLoading} title="Refresh deployment readiness">
                {deploymentLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Status {deploymentReadiness?.status ?? "n/a"}</span>
              <span>Deploy {deploymentReadiness?.deployment_ready ? "ready" : "hold"}</span>
              <span>Prod {deploymentReadiness?.production_ready ? "ready" : "warn"}</span>
            </div>
            <div className="security-grid">
              <span>Pass {deploymentReadiness?.summary?.pass ?? "n/a"}</span>
              <span>Warn {deploymentReadiness?.summary?.warning ?? "n/a"}</span>
              <span>Fail {deploymentReadiness?.summary?.fail ?? "n/a"}</span>
              <span>Actions {deploymentReadiness?.required_actions?.length ?? "n/a"}</span>
              <span>Action hash {deploymentReadiness?.required_actions_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>Attention {deploymentReadiness?.attention_gates?.length ?? "n/a"}</span>
              <span>Attention hash {deploymentReadiness?.attention_gates_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>Action coverage {deploymentActionCoverage(deploymentReadiness)}</span>
              <span>Object mirror {deploymentGateStatus(deploymentReadiness, "artifact_object_store")}</span>
              <span>
                Mirror candidates {deploymentGateDetail(deploymentReadiness, "artifact_object_store", "mirror_plan_candidate_count")} /
                bytes {deploymentGateDetail(deploymentReadiness, "artifact_object_store", "mirror_plan_candidate_bytes")}
              </span>
              <span>Mirror lifecycle {deploymentGateHash(deploymentReadiness, "artifact_object_store", "lifecycle_policy_hash")}</span>
              <span>QC archive {deploymentGateStatus(deploymentReadiness, "qc_bundle_archive_semantics")}</span>
              <span>QC ready {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommendation_readiness_status")}</span>
              <span>QC ready hash {deploymentGateHash(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommendation_readiness_hash")}</span>
              <span>QC release {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommendation_release_ready")}</span>
              <span>QC folding {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommended_folding_status")}</span>
              <span>
                QC candidate {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommendation_readiness_candidate_id")} /
                fold {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommended_folding_candidate_id")}
              </span>
              <span>QC fold hash {deploymentGateHash(deploymentReadiness, "qc_bundle_archive_semantics", "latest_recommended_folding_evidence_hash")}</span>
              <span>
                QC rank evidence {deploymentGateDetail(deploymentReadiness, "qc_bundle_archive_semantics", "latest_retrieval_quality_rank_evidence_count")} /
                hash {deploymentGateHash(deploymentReadiness, "qc_bundle_archive_semantics", "latest_retrieval_quality_rank_evidence_hash")}
              </span>
              <span>QC formats {deploymentGateHash(deploymentReadiness, "qc_bundle_archive_semantics", "latest_report_formats_summary_hash")}</span>
              <span>Data release {deploymentGateStatus(deploymentReadiness, "data_release_archive_semantics")}</span>
              <span>Release fresh {deploymentGateFreshness(deploymentReadiness, "data_release_archive_semantics")}</span>
              <span>Release handoff {deploymentGateHash(deploymentReadiness, "data_release_archive_semantics", "latest_release_handoff_hash")}</span>
              <span>
                Release sources {deploymentGateHash(deploymentReadiness, "data_release_archive_semantics", "latest_record_source_summary_hash")} /
                datasets {deploymentGateDetail(deploymentReadiness, "data_release_archive_semantics", "latest_dataset_count")} /
                files {deploymentGateDetail(deploymentReadiness, "data_release_archive_semantics", "latest_source_file_count")}
              </span>
              <span>Snapshot archive {deploymentGateStatus(deploymentReadiness, "data_snapshot_archive_semantics")}</span>
              <span>
                Snapshot manifest {deploymentGateHash(deploymentReadiness, "data_snapshot_archive_semantics", "latest_snapshot_manifest_hash")} /
                RAG {deploymentGateHash(deploymentReadiness, "data_snapshot_archive_semantics", "latest_rag_index_hash")}
              </span>
              <span>
                Snapshot files {deploymentGateDetail(deploymentReadiness, "data_snapshot_archive_semantics", "latest_snapshot_file_count")} /
                external {deploymentGateDetail(deploymentReadiness, "data_snapshot_archive_semantics", "latest_external_snapshot_file_count")}
              </span>
              <span>Refresh plan {deploymentGateStatus(deploymentReadiness, "data_refresh_plan_archive_semantics")}</span>
              <span>Plan fresh {deploymentGateFreshness(deploymentReadiness, "data_refresh_plan_archive_semantics")}</span>
              <span>
                Plan ops {deploymentGateDetail(deploymentReadiness, "data_refresh_plan_archive_semantics", "latest_operation_count")} /
                validation {deploymentGateDetail(deploymentReadiness, "data_refresh_plan_archive_semantics", "latest_validation_status")}
              </span>
              <span>
                Plan dataset {deploymentGateDetail(deploymentReadiness, "data_refresh_plan_archive_semantics", "latest_dataset_id")} /
                req {deploymentGateHash(deploymentReadiness, "data_refresh_plan_archive_semantics", "latest_request_hash")}
              </span>
              <span>Plan op hash {deploymentGateHash(deploymentReadiness, "data_refresh_plan_archive_semantics", "latest_operations_hash")}</span>
              <span>Import audit {deploymentGateStatus(deploymentReadiness, "structured_import_archive_semantics")}</span>
              <span>
                Import manifest {deploymentGateHash(deploymentReadiness, "structured_import_archive_semantics", "latest_structured_manifest_hash")} /
                files {deploymentGateDetail(deploymentReadiness, "structured_import_archive_semantics", "latest_checked_files")}
              </span>
              <span>RAG eval {deploymentGateStatus(deploymentReadiness, "rag_evaluation_archive_semantics")}</span>
              <span>RAG source {deploymentGateHash(deploymentReadiness, "rag_evaluation_archive_semantics", "latest_source_provenance_hash")}</span>
              <span>Vector index {deploymentGateStatus(deploymentReadiness, "rag_vector_index_archive_semantics")}</span>
              <span>Vector fresh {deploymentGateFreshness(deploymentReadiness, "rag_vector_index_archive_semantics")}</span>
              <span>
                Vector backend {deploymentGateDetail(deploymentReadiness, "rag_vector_index_archive_semantics", "latest_recommended_backend")} /
                migrate {deploymentGateDetail(deploymentReadiness, "rag_vector_index_archive_semantics", "latest_migration_target_backend")}
              </span>
              <span>
                Vector parity {deploymentGateDetail(deploymentReadiness, "rag_vector_index_archive_semantics", "latest_parity_status")} /
                row {deploymentGateHash(deploymentReadiness, "rag_vector_index_archive_semantics", "latest_vector_row_hash")}
              </span>
              <span>RAG regress {deploymentGateStatus(deploymentReadiness, "rag_regression_archive_semantics")}</span>
              <span>RAG metrics {deploymentGateHash(deploymentReadiness, "rag_regression_archive_semantics", "latest_case_metrics_hash")}</span>
              <span>Bench summary {deploymentGateHash(deploymentReadiness, "optimizer_benchmark_archive_semantics", "latest_recommendation_summary_hash")}</span>
              <span>
                Bench front {deploymentGateDetail(deploymentReadiness, "optimizer_benchmark_archive_semantics", "latest_recommended_on_pareto_front_count")} /
                regret {deploymentGateDetail(deploymentReadiness, "optimizer_benchmark_archive_semantics", "latest_recommendation_max_regret")}
              </span>
              <span>Bench folding {deploymentGateHash(deploymentReadiness, "optimizer_benchmark_archive_semantics", "latest_recommended_folding_evidence_hash")}</span>
              <span>Bench fold match {deploymentGateDetail(deploymentReadiness, "optimizer_benchmark_archive_semantics", "latest_recommended_folding_candidate_match_count")}</span>
              <span>
                Trace steps {deploymentGateDetail(deploymentReadiness, "workflow_trace_archive_semantics", "latest_trace_step_count")} /
                task {deploymentGateDetail(deploymentReadiness, "workflow_trace_archive_semantics", "latest_task_type")}
              </span>
              <span>Trace hash {deploymentGateHash(deploymentReadiness, "workflow_trace_archive_semantics", "latest_trace_hash")}</span>
              <span>tRNA prior {deploymentTrnaCaveatCount(deploymentReadiness)}</span>
            </div>
            <div className="metrics-list">
              {(deploymentReadiness?.required_actions ?? []).slice(0, 3).map((item) => (
                <article key={`${item.gate}-${item.priority}`}>
                  <strong>{item.gate.replaceAll("_", " ")} - {item.priority}</strong>
                  <span>{item.action}</span>
                  <small>detail {item.detail_hash?.slice(0, 10) ?? "n/a"}</small>
                </article>
              ))}
              {readinessAttentionGates(deploymentReadiness).map((gate) => (
                <article key={gate.name}>
                  <strong>{gate.name.replaceAll("_", " ")} - {gate.status}</strong>
                  <span>{gate.message}</span>
                </article>
              ))}
              {!deploymentReadiness?.gates?.length ? <p className="rag-empty">Deployment readiness has not been checked yet.</p> : null}
            </div>
          </section>

          <section className="history-panel" aria-label="Production audit">
            <div className="history-heading">
              <span>Production audit</span>
              <button type="button" onClick={() => refreshProductionAudit()} disabled={productionAuditLoading} title="Refresh production audit">
                {productionAuditLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ClipboardCheck size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Status {productionAudit?.summary?.status ?? "n/a"}</span>
              <span>Deploy {productionAudit?.summary?.deployment_ready ? "ready" : "hold"}</span>
              <span>Prod {productionAudit?.summary?.production_ready ? "ready" : "warn"}</span>
              <span>Gaps {productionAudit?.production_gap_summary?.gap_count ?? "n/a"}</span>
            </div>
            <div className="security-grid">
              <span>Pass {productionAudit?.summary?.counts?.pass ?? "n/a"}</span>
              <span>Warn {productionAudit?.summary?.counts?.warning ?? "n/a"}</span>
              <span>Fail {productionAudit?.summary?.counts?.fail ?? "n/a"}</span>
              <span>Verify {productionAudit?.verification?.status ?? "n/a"}</span>
              <span>QC archive {productionAudit?.evidence?.qc_bundle_archive_semantics?.status ?? "n/a"}</span>
              <span>QC checked {productionAudit?.evidence?.qc_bundle_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>QC ready {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommendation_readiness_status ?? "n/a"}</span>
              <span>QC ready hash {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommendation_readiness_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>
                QC candidate {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommendation_readiness_candidate_id ?? "n/a"} /
                fold {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_candidate_id ?? "n/a"}
              </span>
              <span>Data release {productionAudit?.evidence?.data_release_archive_semantics?.status ?? "n/a"}</span>
              <span>Release checked {productionAudit?.evidence?.data_release_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Release fresh {formatArchiveFreshness(productionAudit?.evidence?.data_release_archive_semantics)}</span>
              <span>Data snapshot {productionAudit?.evidence?.data_snapshot_archive_semantics?.status ?? "n/a"}</span>
              <span>Snapshot checked {productionAudit?.evidence?.data_snapshot_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Snapshot fresh {formatArchiveFreshness(productionAudit?.evidence?.data_snapshot_archive_semantics)}</span>
              <span>Refresh plan {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.status ?? "n/a"}</span>
              <span>Plan checked {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Plan fresh {formatArchiveFreshness(productionAudit?.evidence?.data_refresh_plan_archive_semantics)}</span>
              <span>RAG eval {productionAudit?.evidence?.rag_evaluation_archive_semantics?.status ?? "n/a"}</span>
              <span>RAG source {productionAudit?.evidence?.rag_evaluation_archive_semantics?.latest_artifacts?.[0]?.source_provenance_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>Vector index {productionAudit?.evidence?.rag_vector_index_archive_semantics?.status ?? "n/a"}</span>
              <span>Vector checked {productionAudit?.evidence?.rag_vector_index_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Vector fresh {formatArchiveFreshness(productionAudit?.evidence?.rag_vector_index_archive_semantics)}</span>
              <span>RAG regress {productionAudit?.evidence?.rag_regression_archive_semantics?.status ?? "n/a"}</span>
              <span>RAG metrics {productionAudit?.evidence?.rag_regression_archive_semantics?.latest_artifacts?.[0]?.case_metrics_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>Optimizer archive {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.status ?? "n/a"}</span>
              <span>Optimizer checked {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Workflow trace {productionAudit?.evidence?.workflow_trace_archive_semantics?.status ?? "n/a"}</span>
              <span>Trace checked {productionAudit?.evidence?.workflow_trace_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Trace fresh {formatArchiveFreshness(productionAudit?.evidence?.workflow_trace_archive_semantics)}</span>
              <span>Import audit {productionAudit?.evidence?.structured_import_archive_semantics?.status ?? "n/a"}</span>
              <span>Import checked {productionAudit?.evidence?.structured_import_archive_semantics?.checked_count ?? "n/a"}</span>
              <span>Object mirror {productionAudit?.evidence?.artifact_object_store?.status ?? "n/a"}</span>
              <span>
                Mirror candidates {productionAudit?.evidence?.artifact_object_store?.mirror_plan?.candidate_count ?? "n/a"} /
                bytes {productionAudit?.evidence?.artifact_object_store?.mirror_plan?.candidate_bytes ?? "n/a"}
              </span>
              <span>Audit {formatSeconds(productionAudit?.evidence?.timings?.total_seconds)}</span>
              <span>Gap status {productionAudit?.production_gap_summary?.status ?? "n/a"}</span>
            </div>
            <div className="governance-card">
              <span>Hash {productionAudit?.audit_hash?.slice(0, 16) ?? "n/a"}</span>
              <span>
                Evidence hash {productionAudit?.evidence_hashes?.combined_hash?.slice(0, 10) ?? "n/a"} /{" "}
                {productionAudit?.evidence_hashes?.evidence_count ?? "n/a"} files
              </span>
              <span>
                Evidence alg {productionAudit?.evidence_hashes?.algorithm ?? "n/a"} /{" "}
                {productionAudit?.evidence_hashes?.hash_schema ?? "n/a"}
              </span>
              <span>
                Verify hashes {productionAudit?.verification?.semantic_checks?.audit_hash ?? "n/a"} / evidence{" "}
                {productionAudit?.verification?.semantic_checks?.evidence_hashes_manifest ?? "n/a"}
              </span>
              <span>
                Verify details {productionAudit?.verification?.semantic_checks?.check_detail_hashes ?? "n/a"} / actions{" "}
                {productionAudit?.verification?.semantic_checks?.required_action_coverage ?? "n/a"}
              </span>
              <span>
                Gap hash {productionAudit?.production_gap_summary?.gap_summary_hash?.slice(0, 10) ?? "n/a"} / blocking{" "}
                {productionAudit?.production_gap_summary?.blocking_count ?? "n/a"}
              </span>
              <span>{productionAudit?.production_gap_summary?.gaps?.[0]?.action ?? "No production gaps recorded"}</span>
              <span>
                Bundle {productionAudit?.verification?.artifact_verification?.checked_files ?? "n/a"}/
                {productionAudit?.verification?.artifact_verification?.file_count ?? "n/a"} files
              </span>
              <span>Warnings {(productionAudit?.summary?.warning_checks ?? []).slice(0, 3).join(", ") || "none"}</span>
              <span>Sign {productionAudit?.verification?.signature?.status ?? "n/a"}</span>
              <span>
                QC semantic pass {productionAudit?.evidence?.qc_bundle_archive_semantics?.semantic_pass_count ?? "n/a"} /
                fail {productionAudit?.evidence?.qc_bundle_archive_semantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>
                QC optimizer {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.optimizer_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                QC formats {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.report_formats_summary_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                QC recommend {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommendation_audit_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                QC folding {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_status ?? "n/a"} /{" "}
                {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_backend ?? "n/a"}
              </span>
              <span>
                QC fold hash{" "}
                {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_evidence_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                QC retrieval {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.retrieval_quality_status ?? "n/a"} /
                sources {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.retrieval_quality_source_count ?? "n/a"}
              </span>
              <span>
                QC rank evidence {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.retrieval_quality_rank_evidence_count ?? "n/a"} /
                hash{" "}
                {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.retrieval_quality_rank_evidence_hash?.slice(0, 10) ??
                  "n/a"}
              </span>
              <span>
                QC data {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.data_quality_status ?? "n/a"} /
                stress {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.optimizer_stress_status ?? "n/a"} /
                objectives {productionAudit?.evidence?.qc_bundle_archive_semantics?.latest_artifacts?.[0]?.objective_count ?? "n/a"}
              </span>
              <span>
                Release records {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.record_count ?? "n/a"} /
                promotion {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.promotion_status ?? "n/a"}
              </span>
              <span>
                Release hash {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.records_hash?.slice(0, 10) ?? "n/a"} /
                CSV {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.records_csv_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Release source{" "}
                {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.record_source_summary_hash?.slice(0, 10) ?? "n/a"} /
                datasets {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.dataset_count ?? "n/a"} / files{" "}
                {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.source_file_count ?? "n/a"}
              </span>
              <span>
                Release handoff{" "}
                {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.release_handoff_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Release RAG {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.rag_index_hash?.slice(0, 10) ?? "n/a"} /
                manifest {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.rag_structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                tRNA caveats {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.trna_caveat_count ?? "n/a"} /
                blocking {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.trna_blocking_production_use ? "yes" : "no"}
              </span>
              <span>
                Release snapshots {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.external_snapshot_contained_count ?? "n/a"}/
                {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.external_snapshot_referenced_count ?? "n/a"} / missing{" "}
                {productionAudit?.evidence?.data_release_archive_semantics?.latest_artifacts?.[0]?.external_snapshot_missing_count ?? "n/a"}
              </span>
              <span>
                Snapshot manifest{" "}
                {productionAudit?.evidence?.data_snapshot_archive_semantics?.latest_artifacts?.[0]?.snapshot_manifest_hash?.slice(0, 10) ?? "n/a"} /
                structured{" "}
                {productionAudit?.evidence?.data_snapshot_archive_semantics?.latest_artifacts?.[0]?.structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Snapshot RAG {productionAudit?.evidence?.data_snapshot_archive_semantics?.latest_artifacts?.[0]?.rag_index_hash?.slice(0, 10) ?? "n/a"} /
                files {productionAudit?.evidence?.data_snapshot_archive_semantics?.latest_artifacts?.[0]?.snapshot_file_count ?? "n/a"}
              </span>
              <span>Snapshot source files {productionAudit?.evidence?.data_snapshot_archive_semantics?.latest_artifacts?.[0]?.external_snapshot_file_count ?? "n/a"}</span>
              <span>
                Plan ops {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.operation_count ?? "n/a"} /
                validation {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.validation_status ?? "n/a"}
              </span>
              <span>
                Plan hash {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.operations_hash?.slice(0, 10) ?? "n/a"} /
                request {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.request_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Catalog {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.data_catalog_hash?.slice(0, 10) ?? "n/a"} /
                sources {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.external_sources_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Plan quality {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.structured_quality_hash?.slice(0, 10) ?? "n/a"} /
                RAG {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.rag_status_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Plan dataset {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.dataset_id ?? "n/a"} /
                lock {productionAudit?.evidence?.data_refresh_plan_archive_semantics?.latest_artifacts?.[0]?.release_lock_status ?? "n/a"}
              </span>
              <span>
                Vector chunks {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.chunk_count ?? "n/a"} /
                dim {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.embedding_dimensions ?? "n/a"}
              </span>
              <span>
                Vector model {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.embedding_model ?? "n/a"} /
                backend {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.recommended_backend ?? "n/a"}
              </span>
              <span>
                Vector parity {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.parity_status ?? "n/a"} /
                row {productionAudit?.evidence?.rag_vector_index_archive_semantics?.latest_artifacts?.[0]?.vector_row_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Bench result {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.results_hash?.slice(0, 10) ?? "n/a"} /
                cases {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.case_count ?? "n/a"}
              </span>
              <span>
                Bench recommendation{" "}
                {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommendation_summary_hash?.slice(0, 10) ?? "n/a"} /
                front {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommended_on_pareto_front_count ?? "n/a"}
              </span>
              <span>
                Bench regret {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommendation_max_regret ?? "n/a"} /
                status {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommendation_summary_status ?? "n/a"}
              </span>
              <span>
                Bench provenance{" "}
                {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.case_provenance_hash?.slice(0, 10) ?? "n/a"} /
                fingerprints {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.case_fingerprint_count ?? "n/a"}
              </span>
              <span>
                Bench folding{" "}
                {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_evidence_hash?.slice(0, 10) ?? "n/a"} /
                count {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_evidence_count ?? "n/a"}
              </span>
              <span>
                Bench fold match {productionAudit?.evidence?.optimizer_benchmark_archive_semantics?.latest_artifacts?.[0]?.recommended_folding_candidate_match_count ?? "n/a"}
              </span>
              <span>
                Trace steps {productionAudit?.evidence?.workflow_trace_archive_semantics?.latest_artifacts?.[0]?.trace_step_count ?? "n/a"} /
                task {productionAudit?.evidence?.workflow_trace_archive_semantics?.latest_artifacts?.[0]?.task_type ?? "n/a"}
              </span>
              <span>
                Trace hash {productionAudit?.evidence?.workflow_trace_archive_semantics?.latest_artifacts?.[0]?.trace_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Import semantic pass {productionAudit?.evidence?.structured_import_archive_semantics?.semantic_pass_count ?? "n/a"} /
                fail {productionAudit?.evidence?.structured_import_archive_semantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>
                Import manifest{" "}
                {productionAudit?.evidence?.structured_import_archive_semantics?.latest_artifacts?.[0]?.structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Slowest {productionAudit?.evidence?.timings?.slowest?.[0]?.name?.replaceAll("_", " ") ?? "n/a"}{" "}
                {formatSeconds(productionAudit?.evidence?.timings?.slowest?.[0]?.duration_seconds)}
              </span>
              <span>Cache TTL {productionAudit?.cache_policy?.ttl_seconds ?? "n/a"}s</span>
            </div>
            <div className="data-actions retention-actions">
              <button type="button" onClick={downloadProductionAudit} disabled={productionAuditLoading} title="Export production audit ZIP">
                {productionAuditLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Download size={15} aria-hidden />}
                Audit ZIP
              </button>
              <button type="button" onClick={() => refreshProductionAudit()} disabled={productionAuditLoading} title="Refresh cached production audit verification">
                <RefreshCw size={15} aria-hidden />
                Verify
              </button>
              <button type="button" onClick={() => refreshProductionAudit(true)} disabled={productionAuditLoading} title="Force fresh production audit">
                <ClipboardCheck size={15} aria-hidden />
                Fresh
              </button>
            </div>
          </section>

          <section className="history-panel" aria-label="Governance attestation">
            <div className="history-heading">
              <span>Governance</span>
              <button type="button" onClick={refreshGovernanceAttestation} disabled={governanceLoading} title="Verify governance attestation">
                {governanceLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Verify {governanceStatus?.status ?? "n/a"}</span>
              <span>Ledger {governanceStatus?.summary?.ledger_status ?? "n/a"}</span>
              <span>Sign {governanceStatus?.signature?.status ?? governanceStatus?.summary?.signing_status ?? "n/a"}</span>
            </div>
            <div className="governance-card">
              <span>Hash {governanceStatus?.attestation_hash?.slice(0, 16) ?? "n/a"}</span>
              <span>OpenAPI {governanceStatus?.summary?.path_count ?? "n/a"} paths / RAG {governanceStatus?.summary?.rag_chunks ?? "n/a"} chunks</span>
              <span>Data {governanceStatus?.summary?.data_status ?? "n/a"} / Storage {governanceStatus?.summary?.storage_backend ?? "n/a"}</span>
              <span>
                Bundle {governanceStatus?.artifact_verification?.checked_files ?? "n/a"}/
                {governanceStatus?.artifact_verification?.file_count ?? "n/a"} files
              </span>
            </div>
            <div className="data-actions retention-actions">
              <button type="button" onClick={downloadGovernanceAttestation} disabled={governanceLoading} title="Export governance attestation ZIP">
                {governanceLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Download size={15} aria-hidden />}
                Attest ZIP
              </button>
              <button type="button" onClick={refreshGovernanceAttestation} disabled={governanceLoading} title="Refresh governance verification">
                <RefreshCw size={15} aria-hidden />
                Verify
              </button>
            </div>
            {governanceStatus?.errors?.length || governanceStatus?.warnings?.length ? (
              <div className="data-quality">
                <span>{governanceStatus.errors?.[0] ?? governanceStatus.warnings?.[0]}</span>
              </div>
            ) : null}
          </section>

          <section className="history-panel" aria-label="Storage readiness">
            <div className="history-heading">
              <span>Storage</span>
              <button type="button" onClick={refreshStorageReadiness} disabled={storageLoading} title="Refresh storage readiness">
                {storageLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <RefreshCw size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>{storageStatus?.active_runtime_adapter ?? "adapter n/a"}</span>
              <span>PG {storageStatus?.database_url_configured ? "configured" : "local"}</span>
              <span>Parity {storageMigration?.parity?.status ?? "n/a"}</span>
            </div>
            <div className="governance-card storage-card">
              <span>
                Target {storageStatus?.target_backend ?? "n/a"} / schema {(storageStatus?.postgres?.schema_hash ?? "").slice(0, 12) || "n/a"}
              </span>
              <span>
                SQLite {formatBytes(storageStatus?.sqlite?.total_bytes ?? 0)} / records {storageMigration?.summary?.manifest?.total_records ?? "n/a"}
              </span>
              <span>
                Import {storageMigration?.import_plan?.status ?? "n/a"} / dry-run {storageMigration?.import_plan?.dry_run ? "yes" : "n/a"}
              </span>
              <span>
                Target {storageMigration?.parity?.postgres?.status ?? "unconfigured"} / source {storageMigration?.parity?.source?.total_records ?? "n/a"} rows
              </span>
            </div>
            <div className="storage-table">
              {(storageMigration?.summary?.manifest?.tables ?? []).slice(0, 4).map((table) => (
                <span key={table.table}>
                  {table.table}: {table.records} rows / {table.source_exists ? "ready" : "missing"}
                </span>
              ))}
            </div>
            <div className="data-actions retention-actions">
              <button type="button" onClick={downloadStorageMigrationBundle} disabled={storageLoading} title="Export SQLite to Postgres migration bundle">
                {storageLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <Download size={15} aria-hidden />}
                Migration ZIP
              </button>
              <button type="button" onClick={refreshStorageReadiness} disabled={storageLoading} title="Refresh migration readiness">
                <ShieldCheck size={15} aria-hidden />
                Check
              </button>
            </div>
            {storageStatus?.warnings?.length ? (
              <div className="data-quality">
                <span>{storageStatus.warnings[0]}</span>
              </div>
            ) : null}
          </section>

          <section className="history-panel" aria-label="Immutable artifact archive">
            <div className="history-heading">
              <span>Artifact archive</span>
              <button type="button" onClick={refreshArtifacts} disabled={artifactLoading} title="Refresh artifact archive">
                {artifactLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <RefreshCw size={15} aria-hidden />}
              </button>
            </div>
            <div className="audit-summary">
              <span>Total {artifactSummary?.total_artifacts ?? artifacts.length}</span>
              <span>Ledger {artifactSummary?.ledger?.status ?? "n/a"}</span>
              <span>Entries {artifactSummary?.ledger?.entry_count ?? "n/a"}</span>
            </div>
            <div className="retention-strip">
              <div>
                <strong>{artifactSummary?.retention_policy?.enabled ? `${artifactSummary.retention_policy.retention_days}d` : "Manual"}</strong>
                <span>Retention</span>
              </div>
              <div>
                <strong>{artifactSummary?.retention_policy?.keep_min ?? 100}</strong>
                <span>Keep min</span>
              </div>
              <div>
                <strong>{retentionPlan ? formatBytes(retentionPlan.candidate_bytes) : "n/a"}</strong>
                <span>{retentionPlan ? `${retentionPlan.candidate_count} eligible` : "Dry-run"}</span>
              </div>
              <div>
                <strong>{objectStorePlan?.object_store?.configured ? "Ready" : artifactSummary?.object_store?.enabled ? "Config" : "Off"}</strong>
                <span>Object store</span>
              </div>
              <div>
                <strong>{objectStorePlan ? formatBytes(objectStorePlan.candidate_bytes) : "n/a"}</strong>
                <span>{objectStorePlan ? `${objectStorePlan.candidate_count} mirror` : "Mirror plan"}</span>
              </div>
            </div>
            <div className="data-quality" aria-label="Object-store archive mirror status">
              <span>
                Object mirror {objectStoreDisplayStatus} /{" "}
                {artifactSummary?.object_store?.bucket ?? objectStorePlan?.object_store?.bucket ?? "local only"}
              </span>
              <span>
                Prefix {artifactSummary?.object_store?.prefix ?? objectStorePlan?.object_store?.prefix ?? "n/a"} / region{" "}
                {artifactSummary?.object_store?.region ?? objectStorePlan?.object_store?.region ?? "n/a"}
              </span>
              <span>
                Lifecycle {artifactSummary?.object_store?.lifecycle_policy?.status ?? objectStorePlan?.object_store?.lifecycle_policy?.status ?? "n/a"} / hash{" "}
                {(artifactSummary?.object_store?.lifecycle_policy_hash ?? objectStorePlan?.object_store?.lifecycle_policy_hash ?? "").slice(0, 10) || "n/a"}
              </span>
              <span>{objectStorePlan?.object_store?.recommendation ?? "Archive mirror status not checked"}</span>
            </div>
            <div className="data-quality" aria-label="QC bundle semantic archive summary">
              <span>
                QC semantic {qcBundleSemantics?.status ?? "n/a"} / checked {qcBundleSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {qcBundleSemantics?.semantic_pass_count ?? "n/a"} / warn {qcBundleSemantics?.semantic_warning_count ?? "n/a"} /
                fail {qcBundleSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(qcBundleSemantics)}</span>
              <span>
                Latest optimizer {qcBundleSemantics?.latest_artifacts?.[0]?.optimizer_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Recommend audit {qcBundleSemantics?.latest_artifacts?.[0]?.recommendation_audit_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Ready {qcBundleSemantics?.latest_artifacts?.[0]?.recommendation_readiness_status ?? "n/a"} / release{" "}
                {formatBoolean(qcBundleSemantics?.latest_artifacts?.[0]?.recommendation_release_ready)}
              </span>
              <span>
                Ready hash {qcBundleSemantics?.latest_artifacts?.[0]?.recommendation_readiness_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Candidate {qcBundleSemantics?.latest_artifacts?.[0]?.recommendation_readiness_candidate_id ?? "n/a"} / fold{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.recommended_folding_candidate_id ?? "n/a"}
              </span>
              <span>
                Folding {qcBundleSemantics?.latest_artifacts?.[0]?.recommended_folding_status ?? "n/a"} /{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.recommended_folding_backend ?? "n/a"}
              </span>
              <span>
                Folding hash {qcBundleSemantics?.latest_artifacts?.[0]?.recommended_folding_evidence_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Request {qcBundleSemantics?.latest_artifacts?.[0]?.request_payload_status ?? "n/a"} / target{" "}
                {qcRequestTargetSummary(qcBundleSemantics?.latest_artifacts?.[0]?.request_target_checks)}
              </span>
              <span>
                Retrieval {qcBundleSemantics?.latest_artifacts?.[0]?.retrieval_quality_status ?? "n/a"} / sources{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.retrieval_quality_source_count ?? "n/a"} / high{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.retrieval_quality_high_confidence_count ?? "n/a"}
              </span>
              <span>
                Rank evidence {qcBundleSemantics?.latest_artifacts?.[0]?.retrieval_quality_rank_evidence_count ?? "n/a"} / hash{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.retrieval_quality_rank_evidence_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Data {qcBundleSemantics?.latest_artifacts?.[0]?.data_quality_status ?? "n/a"} / stress{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.optimizer_stress_status ?? "n/a"} / objectives{" "}
                {qcBundleSemantics?.latest_artifacts?.[0]?.objective_count ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="Structured import audit archive summary">
              <span>
                Import audit {structuredImportSemantics?.status ?? "n/a"} / checked {structuredImportSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {structuredImportSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {structuredImportSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {structuredImportSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(structuredImportSemantics)}</span>
              <span>
                Latest manifest {structuredImportSemantics?.latest_artifacts?.[0]?.structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="Data release archive summary">
              <span>
                Data release {dataReleaseSemantics?.status ?? "n/a"} / checked {dataReleaseSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {dataReleaseSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {dataReleaseSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {dataReleaseSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(dataReleaseSemantics)}</span>
              <span>
                Records {dataReleaseSemantics?.latest_artifacts?.[0]?.record_count ?? "n/a"} / promotion{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.promotion_status ?? "n/a"}
              </span>
              <span>
                Record hash {dataReleaseSemantics?.latest_artifacts?.[0]?.records_hash?.slice(0, 10) ?? "n/a"} / CSV{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.records_csv_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Source summary {dataReleaseSemantics?.latest_artifacts?.[0]?.record_source_summary_hash?.slice(0, 10) ?? "n/a"} / datasets{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.dataset_count ?? "n/a"} / files{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.source_file_count ?? "n/a"}
              </span>
              <span>Handoff hash {dataReleaseSemantics?.latest_artifacts?.[0]?.release_handoff_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>
                RAG index {dataReleaseSemantics?.latest_artifacts?.[0]?.rag_index_hash?.slice(0, 10) ?? "n/a"} / manifest{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.rag_structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                tRNA caveats {dataReleaseSemantics?.latest_artifacts?.[0]?.trna_caveat_count ?? "n/a"} / blocking{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.trna_blocking_production_use ? "yes" : "no"}
              </span>
              <span>
                Snapshots {dataReleaseSemantics?.latest_artifacts?.[0]?.external_snapshot_contained_count ?? "n/a"}/
                {dataReleaseSemantics?.latest_artifacts?.[0]?.external_snapshot_referenced_count ?? "n/a"} / missing{" "}
                {dataReleaseSemantics?.latest_artifacts?.[0]?.external_snapshot_missing_count ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="Data snapshot archive summary">
              <span>
                Data snapshot {dataSnapshotSemantics?.status ?? "n/a"} / checked {dataSnapshotSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {dataSnapshotSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {dataSnapshotSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {dataSnapshotSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(dataSnapshotSemantics)}</span>
              <span>
                Manifest {dataSnapshotSemantics?.latest_artifacts?.[0]?.snapshot_manifest_hash?.slice(0, 10) ?? "n/a"} / structured{" "}
                {dataSnapshotSemantics?.latest_artifacts?.[0]?.structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                RAG index {dataSnapshotSemantics?.latest_artifacts?.[0]?.rag_index_hash?.slice(0, 10) ?? "n/a"} / files{" "}
                {dataSnapshotSemantics?.latest_artifacts?.[0]?.snapshot_file_count ?? "n/a"}
              </span>
              <span>External snapshots {dataSnapshotSemantics?.latest_artifacts?.[0]?.external_snapshot_file_count ?? "n/a"}</span>
            </div>
            <div className="data-quality" aria-label="Data refresh plan archive summary">
              <span>
                Refresh plan {dataRefreshPlanSemantics?.status ?? "n/a"} / checked {dataRefreshPlanSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {dataRefreshPlanSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {dataRefreshPlanSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {dataRefreshPlanSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(dataRefreshPlanSemantics)}</span>
              <span>
                Ops {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.operation_count ?? "n/a"} / validation{" "}
                {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.validation_status ?? "n/a"}
              </span>
              <span>
                Dataset {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.dataset_id ?? "n/a"} / manifest{" "}
                {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.structured_manifest_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Ops hash {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.operations_hash?.slice(0, 10) ?? "n/a"} / request{" "}
                {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.request_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Catalog {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.data_catalog_hash?.slice(0, 10) ?? "n/a"} / sources{" "}
                {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.external_sources_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Quality {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.structured_quality_hash?.slice(0, 10) ?? "n/a"} / provenance{" "}
                {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.data_provenance_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>RAG status {dataRefreshPlanSemantics?.latest_artifacts?.[0]?.rag_status_hash?.slice(0, 10) ?? "n/a"}</span>
            </div>
            <div className="data-quality" aria-label="RAG evaluation archive summary">
              <span>
                RAG eval archive {ragEvaluationSemantics?.status ?? "n/a"} / checked {ragEvaluationSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {ragEvaluationSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {ragEvaluationSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {ragEvaluationSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(ragEvaluationSemantics)}</span>
              <span>
                Latest trace {ragEvaluationSemantics?.latest_artifacts?.[0]?.query_fingerprint?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Eval hash {ragEvaluationSemantics?.latest_artifacts?.[0]?.evaluation_hash?.slice(0, 10) ?? "n/a"} / chunks{" "}
                {ragEvaluationSemantics?.latest_artifacts?.[0]?.chunks_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>Top sources {ragEvaluationSemantics?.latest_artifacts?.[0]?.top_sources_hash?.slice(0, 10) ?? "n/a"}</span>
              <span>
                Source prov {ragEvaluationSemantics?.latest_artifacts?.[0]?.source_provenance_hash?.slice(0, 10) ?? "n/a"} / sources{" "}
                {ragEvaluationSemantics?.latest_artifacts?.[0]?.source_provenance_count ?? "n/a"} / snapshots{" "}
                {ragEvaluationSemantics?.latest_artifacts?.[0]?.source_snapshot_count ?? "n/a"}
              </span>
              <span>
                Trace {ragEvaluationSemantics?.latest_artifacts?.[0]?.retrieval_trace_hash?.slice(0, 10) ?? "n/a"} / suff{" "}
                {ragEvaluationSemantics?.latest_artifacts?.[0]?.evidence_sufficiency_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Facets {ragEvaluationSemantics?.latest_artifacts?.[0]?.facet_gap_analysis_hash?.slice(0, 10) ?? "n/a"} / terms{" "}
                {ragEvaluationSemantics?.latest_artifacts?.[0]?.query_term_coverage_hash?.slice(0, 10) ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="RAG regression archive summary">
              <span>
                RAG regress archive {ragRegressionSemantics?.status ?? "n/a"} / checked {ragRegressionSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {ragRegressionSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {ragRegressionSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {ragRegressionSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(ragRegressionSemantics)}</span>
              <span>
                Latest cases {ragRegressionSemantics?.latest_artifacts?.[0]?.cases_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Results {ragRegressionSemantics?.latest_artifacts?.[0]?.results_hash?.slice(0, 10) ?? "n/a"} / quality{" "}
                {ragRegressionSemantics?.latest_artifacts?.[0]?.quality_summary_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                RAG metrics hash {ragRegressionSemantics?.latest_artifacts?.[0]?.case_metrics_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Quality {ragRegressionSemantics?.latest_artifacts?.[0]?.quality_status ?? "n/a"} / top src{" "}
                {ragRegressionSemantics?.latest_artifacts?.[0]?.top_source_count ?? "n/a"} / term gaps{" "}
                {ragRegressionSemantics?.latest_artifacts?.[0]?.missing_term_case_count ?? "n/a"}
              </span>
              <span>
                Source prov {ragRegressionSemantics?.latest_artifacts?.[0]?.source_provenance_summary_hash?.slice(0, 10) ?? "n/a"} / cases{" "}
                {ragRegressionSemantics?.latest_artifacts?.[0]?.source_provenance_case_count ?? "n/a"} / snapshots{" "}
                {ragRegressionSemantics?.latest_artifacts?.[0]?.source_snapshot_case_count ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="RAG vector index archive summary">
              <span>
                RAG vector archive {ragVectorIndexSemantics?.status ?? "n/a"} / checked {ragVectorIndexSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {ragVectorIndexSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {ragVectorIndexSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {ragVectorIndexSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(ragVectorIndexSemantics)}</span>
              <span>
                Chunks {ragVectorIndexSemantics?.latest_artifacts?.[0]?.chunk_count ?? "n/a"} / dim{" "}
                {ragVectorIndexSemantics?.latest_artifacts?.[0]?.embedding_dimensions ?? "n/a"}
              </span>
              <span>
                Model {ragVectorIndexSemantics?.latest_artifacts?.[0]?.embedding_model ?? "n/a"} / backend{" "}
                {ragVectorIndexSemantics?.latest_artifacts?.[0]?.recommended_backend ?? "n/a"}
              </span>
              <span>
                Migration {ragVectorIndexSemantics?.latest_artifacts?.[0]?.migration_target_backend ?? "n/a"} / parity{" "}
                {ragVectorIndexSemantics?.latest_artifacts?.[0]?.parity_status ?? "n/a"}
              </span>
              <span>Row hash {ragVectorIndexSemantics?.latest_artifacts?.[0]?.vector_row_hash?.slice(0, 10) ?? "n/a"}</span>
            </div>
            <div className="data-quality" aria-label="Optimizer benchmark archive summary">
              <span>
                OPT bench archive {optimizerBenchmarkSemantics?.status ?? "n/a"} / checked{" "}
                {optimizerBenchmarkSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {optimizerBenchmarkSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {optimizerBenchmarkSemantics?.semantic_warning_count ?? "n/a"} / fail{" "}
                {optimizerBenchmarkSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(optimizerBenchmarkSemantics)}</span>
              <span>
                Latest cases {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.cases_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Metrics {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.case_metrics_hash?.slice(0, 10) ?? "n/a"} / diagnostics{" "}
                {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.diagnostics_hash?.slice(0, 10) ?? "n/a"}
              </span>
              <span>
                Stress {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.stress_status ?? "n/a"} / cases{" "}
                {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.case_count ?? "n/a"}
              </span>
              <span>
                Folding hash {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.recommended_folding_evidence_hash?.slice(0, 10) ?? "n/a"} / count{" "}
                {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.recommended_folding_evidence_count ?? "n/a"}
              </span>
              <span>
                Folding candidate match {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.recommended_folding_candidate_match_count ?? "n/a"}
              </span>
              <span>
                Recommendation {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.recommendation_summary_hash?.slice(0, 10) ?? "n/a"} / front{" "}
                {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.recommended_on_pareto_front_count ?? "n/a"}
              </span>
              <span>
                Case provenance {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.case_provenance_hash?.slice(0, 10) ?? "n/a"} / count{" "}
                {optimizerBenchmarkSemantics?.latest_artifacts?.[0]?.case_fingerprint_count ?? "n/a"}
              </span>
            </div>
            <div className="data-quality" aria-label="Workflow trace archive summary">
              <span>
                Workflow trace {workflowTraceSemantics?.status ?? "n/a"} / checked {workflowTraceSemantics?.checked_count ?? "n/a"}
              </span>
              <span>
                Pass {workflowTraceSemantics?.semantic_pass_count ?? "n/a"} / warn{" "}
                {workflowTraceSemantics?.semantic_warning_count ?? "n/a"} / fail {workflowTraceSemantics?.semantic_fail_count ?? "n/a"}
              </span>
              <span>Freshness {formatArchiveFreshness(workflowTraceSemantics)}</span>
              <span>
                Steps {workflowTraceSemantics?.latest_artifacts?.[0]?.trace_step_count ?? "n/a"} / task{" "}
                {workflowTraceSemantics?.latest_artifacts?.[0]?.task_type ?? "n/a"}
              </span>
              <span>Trace hash {workflowTraceSemantics?.latest_artifacts?.[0]?.trace_hash?.slice(0, 10) ?? "n/a"}</span>
            </div>
            <div className="data-actions retention-actions">
              <button type="button" onClick={planArtifactRetention} disabled={retentionLoading} title="Preview artifact retention cleanup">
                {retentionLoading ? <Loader2 className="spin" size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                Retention plan
              </button>
              <button type="button" onClick={refreshArtifacts} disabled={artifactLoading || retentionLoading} title="Refresh archive status">
                <RefreshCw size={15} aria-hidden />
                Refresh
              </button>
            </div>
            {retentionPlan ? (
              <div className="data-quality retention-plan">
                <span>
                  Dry-run {retentionPlan.dry_run ? "ready" : "checked"} / {retentionPlan.candidate_count} candidates
                </span>
                <span>
                  Policy {retentionPlan.retention_days}d / keep {retentionPlan.keep_min} / considered {retentionPlan.total_considered}
                </span>
                <span>Cutoff {retentionPlan.cutoff ? new Date(retentionPlan.cutoff).toLocaleDateString() : "not configured"}</span>
              </div>
            ) : null}
            {artifactVerification ? (
              <div className="data-quality artifact-verification" aria-label="Archived artifact verification">
                <span>
                  Verify {artifactVerification.status} / {artifactVerification.bundle_verification?.checked_files ?? "n/a"}/
                  {artifactVerification.bundle_verification?.file_count ?? "n/a"} files
                </span>
                <span>
                  Semantic {artifactVerification.bundle_verification?.semantic_status ?? "n/a"} / QC optimizer{" "}
                  {artifactVerification.bundle_verification?.optimizer_manifest_hash?.slice(0, 10) ?? "n/a"}
                </span>
                <span>
                  Folding {artifactVerification.bundle_verification?.recommended_folding_status ?? "n/a"} /{" "}
                  {artifactVerification.bundle_verification?.recommended_folding_evidence_hash?.slice(0, 10) ?? "n/a"}
                </span>
                <span>Artifact {artifactVerification.artifact.artifact_id}</span>
              </div>
            ) : null}
            <div className="history-list">
              {artifacts.length ? (
                artifacts.map((artifact) => (
                  <article className="job-row artifact-row" key={artifact.artifact_id}>
                    <button type="button" onClick={() => downloadArchivedArtifact(artifact)}>
                      <strong>{(artifact.artifact_type ?? "bundle").replaceAll("_", " ")} - {artifact.verification_status ?? "unknown"}</strong>
                      <span>{artifact.resource_type} {artifact.resource_id} / {formatBytes(artifact.bytes)}</span>
                      <small>{artifact.manifest_hash?.slice(0, 12) ?? artifact.artifact_id}</small>
                    </button>
                    <button
                      type="button"
                      className="job-export verify-artifact"
                      onClick={() => verifyArchivedArtifact(artifact)}
                      disabled={artifactVerifyingId === artifact.artifact_id}
                      title="Verify archived artifact"
                    >
                      {artifactVerifyingId === artifact.artifact_id ? (
                        <Loader2 className="spin" size={15} aria-hidden />
                      ) : (
                        <ShieldCheck size={15} aria-hidden />
                      )}
                    </button>
                    <button type="button" className="job-export" onClick={() => downloadArchivedArtifact(artifact)} title="Download archived artifact">
                      <Download size={15} aria-hidden />
                    </button>
                  </article>
                ))
              ) : (
                <p>{artifactLoading ? "Loading archive..." : "No archived artifacts yet."}</p>
              )}
            </div>
          </section>
        </aside>

        <section className="results-panel" aria-label="Design results">
          {error ? (
            <div className="notice error">
              <AlertTriangle size={20} aria-hidden />
              <span>{error}</span>
            </div>
          ) : null}

          {!design ? (
            <div className="empty-state">
              <BarChart3 size={34} aria-hidden />
              <h2>Ready for gene-to-design analysis</h2>
              <p>Run the default SNCA case or enter a human gene symbol with a protein-coding transcript.</p>
            </div>
          ) : (
            <>
              <div className="top-strip">
                <div>
                  <span className="eyebrow">Run</span>
                  <h2>{design.run_id}</h2>
                </div>
                <div className="export-actions" aria-label="Report exports">
                  <button className="secondary-action" onClick={downloadReport}>
                    <Download size={17} aria-hidden />
                    JSON
                  </button>
                  <button className="secondary-action" onClick={() => downloadQcExport("markdown")}>
                    <Download size={17} aria-hidden />
                    MD
                  </button>
                  <button className="secondary-action" onClick={() => downloadQcExport("html")}>
                    <Download size={17} aria-hidden />
                    HTML
                  </button>
                  <button className="secondary-action" onClick={() => downloadQcExport("json")}>
                    <Download size={17} aria-hidden />
                    QC JSON
                  </button>
                  <button className="secondary-action" onClick={() => downloadQcExport("pdf")}>
                    <Download size={17} aria-hidden />
                    PDF
                  </button>
                  <button className="secondary-action" onClick={downloadQcBundle}>
                    <Download size={17} aria-hidden />
                    QC ZIP
                  </button>
                  <button className="secondary-action" onClick={downloadRunBundle}>
                    <Download size={17} aria-hidden />
                    ZIP
                  </button>
                </div>
              </div>

              <div className="summary-grid">
                <MetricCard label="Transcript" value={design.source_cds?.selected_transcript.id ?? "n/a"} icon={<Database />} />
                <MetricCard
                  label="Selection"
                  value={formatReason(design.source_cds?.selected_transcript.selection_reason)}
                  icon={<ShieldCheck />}
                />
                <MetricCard
                  label="RefSeq match"
                  value={design.source_cds?.selected_transcript.mane_select?.refseq_match ?? "not available"}
                  icon={<CheckCircle2 />}
                />
                <MetricCard label="Protein length" value={`${design.source_cds?.protein_length_aa ?? 0} aa`} icon={<Dna />} />
              </div>

              {design.warnings.length ? (
                <div className="notice warning">
                  <AlertTriangle size={20} aria-hidden />
                  <span>{design.warnings.join(" ")}</span>
                </div>
              ) : null}

              {design.evidence ? <EvidencePanel evidence={design.evidence} /> : null}

              {design.qc_report ? <QcReportPanel report={design.qc_report} /> : null}

              {design.trace?.length ? <TracePanel trace={design.trace} /> : null}

              <div className="split-grid">
                <section className="table-panel" aria-label="Candidate ranking">
                  <div className="panel-heading">
                    <h3>Candidate Ranking</h3>
                    <span>{design.candidates.length} candidates</span>
                  </div>
                  <div className="candidate-list">
                    {design.candidates.map((candidate) => (
                      <button
                        type="button"
                        key={candidate.candidate_id}
                        className={candidate.candidate_id === selectedCandidate?.candidate_id ? "candidate-row active" : "candidate-row"}
                        onClick={() => setSelectedId(candidate.candidate_id)}
                      >
                        <span>{candidate.candidate_id}</span>
                        <strong>{candidate.scores.composite_quality.toFixed(4)}</strong>
                        <small>CAI {candidate.scores.cai.toFixed(3)}</small>
                        <small>GC {(candidate.scores.gc_fraction * 100).toFixed(1)}%</small>
                      </button>
                    ))}
                  </div>
                </section>

                <section className="detail-panel" aria-label="Selected candidate detail">
                  <div className="panel-heading">
                    <h3>{selectedCandidate?.candidate_id ?? "Candidate"}</h3>
                    <span>{selectedCandidate?.candidate_id === design.recommended_candidate?.candidate_id ? "recommended" : "alternative"}</span>
                  </div>
                  {selectedCandidate ? (
                    <>
                      <ScoreBars scores={selectedCandidate.scores} nativeScores={design.native.scores} />
                      <div className="data-quality" aria-label="Candidate selection trace">
                        <span>
                          Risk {selectedCandidate.constraint_risk?.status ?? "n/a"} / findings{" "}
                          {selectedCandidate.constraint_risk?.finding_count ?? "n/a"}
                        </span>
                        <span>{selectedCandidate.selection_trace?.[0] ?? "selection trace unavailable"}</span>
                        <span>{selectedCandidate.constraint_risk?.findings?.[0]?.message ?? "No constraint risk findings"}</span>
                      </div>
                      <div className="sequence-box">
                        <div>
                          <span>CDS</span>
                          <strong>{selectedCandidate.cds.length} nt</strong>
                        </div>
                        <code>{chunkSequence(selectedCandidate.cds)}</code>
                      </div>
                    </>
                  ) : null}
                </section>
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function QcReportPanel({ report }: { report: QcReport }) {
  const delta = report.score_summary.delta;
  const constraintRows = Object.entries(report.recommended_candidate.constraint_status);
  const diagnostics = report.candidate_diagnostics;
  const optimizerRepro = report.optimizer_reproducibility;
  const dataQuality = report.data_quality;
  const targetStructured = report.target_structured_evidence;
  const optimizerStress = report.optimizer_stress;
  const retrievalQuality = report.evidence_summary.retrieval_quality;
  const bestByMetric = diagnostics?.best_by_metric ?? {};
  const recommendationAudit = report.recommendation_audit ?? diagnostics?.recommendation_audit;
  const foldingEvidence = report.recommended_folding_evidence;
  const readiness = report.recommendation_readiness;

  return (
    <section className="qc-panel" aria-label="QC report summary">
      <div className="panel-heading">
        <h3>
          <ClipboardCheck size={17} aria-hidden />
          QC Report
        </h3>
        <span>{report.recommended_candidate.candidate_id}</span>
      </div>
      <div className="qc-grid">
        <div>
          <span>Supported rules</span>
          <strong>{report.evidence_summary.supported_rules.length}</strong>
        </div>
        <div>
          <span>Uncertain rules</span>
          <strong>{report.evidence_summary.uncertain_rules.length}</strong>
        </div>
        <div>
          <span>Rejected rules</span>
          <strong>{report.evidence_summary.rejected_rules.length}</strong>
        </div>
        <div>
          <span>QC gate</span>
          <strong>{report.qc_gate?.status ?? "n/a"}</strong>
        </div>
        <div>
          <span>Risk</span>
          <strong>{report.recommended_candidate.constraint_risk?.status ?? "n/a"}</strong>
        </div>
        <div>
          <span>Feasible</span>
          <strong>{diagnostics ? `${diagnostics.feasible_count}/${diagnostics.candidate_count}` : "n/a"}</strong>
        </div>
        <div>
          <span>Pareto front</span>
          <strong>{diagnostics?.pareto_front?.size ?? "n/a"}</strong>
        </div>
        <div>
          <span>Pareto HV</span>
          <strong>{formatMetric(diagnostics?.pareto_quality?.approx_hypervolume_2d, 3)}</strong>
        </div>
        <div>
          <span>Pareto hash</span>
          <strong>{diagnostics?.pareto_quality?.quality_hash?.slice(0, 10) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Data quality</span>
          <strong>{dataQuality?.status ?? "n/a"}</strong>
        </div>
        <div>
          <span>Stress gate</span>
          <strong>{optimizerStress?.status ?? "n/a"}</strong>
        </div>
      </div>
      {retrievalQuality ? (
        <div className="policy-strip" aria-label="Retrieval evidence quality">
          <div>
            <span>Retrieval</span>
            <strong>{retrievalQuality.status ?? "n/a"}</strong>
          </div>
          <div>
            <span>Records / sources</span>
            <strong>
              {retrievalQuality.record_count ?? "n/a"} / {retrievalQuality.source_count ?? "n/a"}
            </strong>
          </div>
          <div>
            <span>High confidence</span>
            <strong>{retrievalQuality.high_confidence_count ?? "n/a"}</strong>
          </div>
          <div>
            <span>Rank evidence</span>
            <strong>{retrievalQuality.rank_evidence_count ?? "n/a"}</strong>
          </div>
          <div>
            <span>Rank hash</span>
            <strong>{retrievalQuality.rank_evidence_hash?.slice(0, 10) ?? "n/a"}</strong>
          </div>
          <div>
            <span>Top source</span>
            <strong>{retrievalQuality.top_sources?.[0]?.source ?? "n/a"}</strong>
          </div>
          <div>
            <span>Model</span>
            <strong>{retrievalQuality.retrieval_model ?? retrievalQuality.embedding_model ?? "n/a"}</strong>
          </div>
        </div>
      ) : null}
      {targetStructured ? (
        <>
          <div className="policy-strip" aria-label="Target structured evidence">
            <div>
              <span>Target data</span>
              <strong>{targetStructured.status ?? "n/a"}</strong>
            </div>
            <div>
              <span>Matched records</span>
              <strong>{targetStructured.matched_record_count ?? "n/a"}</strong>
            </div>
            <div>
              <span>Live / seed</span>
              <strong>
                {targetStructured.live_record_count ?? "n/a"} / {targetStructured.seed_record_count ?? "n/a"}
              </strong>
            </div>
            <div>
              <span>Release / snapshots</span>
              <strong>
                {targetStructured.release_pinned_record_count ?? "n/a"} / {targetStructured.snapshot_record_count ?? "n/a"}
              </strong>
            </div>
            <div>
              <span>Datasets</span>
              <strong>{targetStructured.datasets?.slice(0, 3).join(", ") || "n/a"}</strong>
            </div>
          </div>
          <div className="structured-evidence-list" aria-label="Target matched structured records">
            {(targetStructured.top_records ?? []).slice(0, 4).map((record) => (
              <div key={record.id ?? `${record.dataset}-${record.source_file}`}>
                <strong>{record.dataset ?? "Structured data"}</strong>
                <span>
                  {record.id ?? "n/a"} · {record.release ?? "n/a"} · {record.is_live ? "live" : record.is_seed ? "seed/local" : "release-pinned"}
                </span>
                <small>
                  {[record.gene, record.brain_region, record.cell_type].filter(Boolean).join(" / ") || "target context"} · match{" "}
                  {formatMetric(record.match_score, 2)}
                  {typeof record.median_expression === "number" ? ` · ${record.median_expression} ${record.unit ?? ""}` : ""}
                  {typeof record.number_of_cells === "number" ? ` · ${record.number_of_cells} cells` : ""}
                </small>
              </div>
            ))}
          </div>
        </>
      ) : null}
      {dataQuality || optimizerStress ? (
        <div className="policy-strip" aria-label="QC export evidence">
          <div>
            <span>Data records</span>
            <strong>{dataQuality?.record_count ?? "n/a"}</strong>
          </div>
          <div>
            <span>Live data</span>
            <strong>{formatMetric(dataQuality?.live_record_fraction, 2)}</strong>
          </div>
          <div>
            <span>Release pinned</span>
            <strong>{formatMetric(dataQuality?.release_pinned_fraction, 2)}</strong>
          </div>
          <div>
            <span>Stress checks</span>
            <strong>
              P{optimizerStress?.summary?.pass_count ?? "n/a"} / W{optimizerStress?.summary?.warning_count ?? "n/a"} / F
              {optimizerStress?.summary?.fail_count ?? "n/a"}
            </strong>
          </div>
          <div>
            <span>Stress cases</span>
            <strong>{optimizerStress?.case_count ?? "n/a"}</strong>
          </div>
        </div>
      ) : null}
      {diagnostics ? (
        <div className="policy-strip" aria-label="Candidate diagnostics">
          <div>
            <span>Unique CDS</span>
            <strong>{diagnostics.diversity?.unique_cds_count ?? "n/a"}</strong>
          </div>
          <div>
            <span>Codon distance</span>
            <strong>{diagnostics.diversity?.mean_pairwise_codon_distance?.toFixed(3) ?? "n/a"}</strong>
          </div>
          <div>
            <span>Recommended rank</span>
            <strong>{diagnostics.recommended_rank ?? "n/a"}</strong>
          </div>
          <div>
            <span>Risk split</span>
            <strong>
              P{diagnostics.constraint_risk_summary?.status_counts?.pass ?? "n/a"} / W
              {diagnostics.constraint_risk_summary?.status_counts?.warning ?? "n/a"} / F
              {diagnostics.constraint_risk_summary?.status_counts?.fail ?? "n/a"}
            </strong>
          </div>
          <div>
            <span>Best CAI</span>
            <strong>{bestByMetric.cai?.candidate_id ?? "n/a"}</strong>
          </div>
        </div>
      ) : null}
      {optimizerRepro ? (
        <div className="policy-strip" aria-label="Optimizer reproducibility">
          <div>
            <span>Config hash</span>
            <strong>{optimizerRepro.manifest_hash.slice(0, 12)}</strong>
          </div>
          <div>
            <span>Seed</span>
            <strong>{optimizerRepro.seed}</strong>
          </div>
          <div>
            <span>Seed strategy</span>
            <strong>{optimizerRepro.seed_strategy?.version ?? "n/a"}</strong>
          </div>
          <div>
            <span>Repair</span>
            <strong>{optimizerRepro.repair_policy.enabled ? `${optimizerRepro.repair_policy.repair_passes} pass` : "off"}</strong>
          </div>
          <div>
            <span>Objectives</span>
            <strong>{optimizerRepro.objective_inventory.length}</strong>
          </div>
        </div>
      ) : null}
      {recommendationAudit ? (
        <div className="policy-strip" aria-label="Recommendation audit">
          <div>
            <span>Best objectives</span>
            <strong>{recommendationAudit.best_metric_count}</strong>
          </div>
          <div>
            <span>Tradeoffs</span>
            <strong>{recommendationAudit.tradeoff_count}</strong>
          </div>
          <div>
            <span>Max regret</span>
            <strong>{formatMetric(recommendationAudit.max_regret, 3)}</strong>
          </div>
          <div>
            <span>Hard constraints</span>
            <strong>{recommendationAudit.hard_constraint_status}</strong>
          </div>
          <div>
            <span>Primary tradeoff</span>
            <strong>{recommendationAudit.primary_tradeoff?.metric?.replaceAll("_", " ") ?? "none"}</strong>
          </div>
        </div>
      ) : null}
      {readiness ? (
        <div className="policy-strip" aria-label="Recommendation readiness">
          <div>
            <span>Readiness</span>
            <strong>{readiness.readiness_status ?? "n/a"}</strong>
          </div>
          <div>
            <span>Release ready</span>
            <strong>{formatBoolean(readiness.release_ready)}</strong>
          </div>
          <div>
            <span>Ready hash</span>
            <strong>{readiness.readiness_hash?.slice(0, 12) ?? "n/a"}</strong>
          </div>
          <div>
            <span>Constraint / Pareto</span>
            <strong>{readiness.constraint_risk_status ?? "n/a"} / {formatBoolean(readiness.pareto_front_member)}</strong>
          </div>
          <div>
            <span>Fold / stress</span>
            <strong>{readiness.folding_status ?? "n/a"} / {readiness.optimizer_stress_status ?? "n/a"}</strong>
          </div>
          <div>
            <span>Blocks / warns</span>
            <strong>{readiness.blocking_reasons?.length ?? 0} / {readiness.warning_reasons?.length ?? 0}</strong>
          </div>
        </div>
      ) : null}
      {foldingEvidence ? (
        <div className="policy-strip" aria-label="Recommended folding evidence">
          <div>
            <span>Folding evidence</span>
            <strong>{foldingEvidence.status ?? "n/a"}</strong>
          </div>
          <div>
            <span>Backend</span>
            <strong>{foldingEvidence.active_backend ?? "n/a"}</strong>
          </div>
          <div>
            <span>Fallback</span>
            <strong>{foldingEvidence.fallback_active ? "yes" : "no"}</strong>
          </div>
          <div>
            <span>Window</span>
            <strong>{foldingEvidence.evaluated_window_nt ?? "n/a"} nt</strong>
          </div>
          <div>
            <span>Thermo risk</span>
            <strong>{formatMetric(foldingEvidence.thermodynamic_risk_score, 3)}</strong>
          </div>
          <div>
            <span>Folding hash</span>
            <strong>{foldingEvidence.folding_evidence_hash?.slice(0, 12) ?? "n/a"}</strong>
          </div>
        </div>
      ) : null}
      <div className="qc-score-summary" aria-label="QC score summary">
        <div>
          <span>Composite delta</span>
          <strong>{formatDelta(delta.composite_quality)}</strong>
        </div>
        <div>
          <span>CAI delta</span>
          <strong>{formatDelta(delta.cai)}</strong>
        </div>
        <div>
          <span>CpG / 100 nt delta</span>
          <strong>{formatDelta(delta.cpg_density_per_100nt)}</strong>
        </div>
        <div>
          <span>tRNA adaptation</span>
          <strong>{report.score_summary.recommended.tissue_codon_adaptation?.toFixed(3) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Motif violations</span>
          <strong>{report.score_summary.recommended.motif_violations}</strong>
        </div>
        <div>
          <span>PolyA signals</span>
          <strong>{report.score_summary.recommended.polyadenylation_signal_count ?? "n/a"}</strong>
        </div>
        <div>
          <span>Restriction sites</span>
          <strong>{report.score_summary.recommended.restriction_site_count}</strong>
        </div>
        <div>
          <span>Splice proxy</span>
          <strong>
            {(report.score_summary.recommended.splice_donor_motif_count ?? 0) +
              (report.score_summary.recommended.splice_acceptor_motif_count ?? 0)}
          </strong>
        </div>
        <div>
          <span>Policy score</span>
          <strong>{report.score_summary.recommended.sequence_policy_violation_score?.toFixed(2) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Local GC dev</span>
          <strong>{report.score_summary.recommended.gc_window_max_deviation?.toFixed(3) ?? "n/a"}</strong>
        </div>
        <div>
          <span>5 prime GC dev</span>
          <strong>{report.score_summary.recommended.five_prime_gc_deviation?.toFixed(3) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Hairpin proxy</span>
          <strong>{report.score_summary.recommended.hairpin_proxy_score?.toFixed(3) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Structure proxy</span>
          <strong>{report.score_summary.recommended.secondary_structure_proxy_score?.toFixed(3) ?? "n/a"}</strong>
        </div>
        <div>
          <span>MFE proxy</span>
          <strong>{report.score_summary.recommended.mfe_proxy_delta_g?.toFixed(1) ?? "n/a"}</strong>
        </div>
        <div>
          <span>Codon-pair risk</span>
          <strong>{report.score_summary.recommended.codon_pair_risk ?? "n/a"}</strong>
        </div>
      </div>
      {report.sequence_policy?.recommended ? (
        <div className="policy-strip" aria-label="Sequence policy audit">
          <div>
            <span>Policy audit</span>
            <strong>{report.sequence_policy.recommended.status}</strong>
          </div>
          {report.sequence_policy.recommended.findings.slice(0, 4).map((finding) => (
            <div key={`${finding.category}-${finding.motif}`}>
              <span>{finding.category.replaceAll("_", " ")}</span>
              <strong>{finding.motif} x{finding.count}</strong>
            </div>
          ))}
          {!report.sequence_policy.recommended.findings.length ? (
            <div>
              <span>Findings</span>
              <strong>0</strong>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="qc-constraints" aria-label="QC constraint status">
        {constraintRows.map(([key, value]) => (
          <div key={key}>
            <span>{key.replaceAll("_", " ")}</span>
            <strong>{String(value ?? "n/a")}</strong>
          </div>
        ))}
      </div>
      <div className="qc-columns">
        <div>
          <h4>QC Gate</h4>
          <ul>
            {(report.qc_gate?.checks ?? []).slice(0, 4).map((item) => (
              <li key={item.id}>{item.id.replaceAll("_", " ")}: {item.result}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Rationale</h4>
          <ul>
            {report.recommended_candidate.rationale.slice(0, 4).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Warnings</h4>
          <ul>
            {(report.warnings.length ? report.warnings : ["No blocking QC warnings."]).slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Open Questions</h4>
          <ul>
            {report.open_questions.slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function formatDelta(value?: number) {
  if (typeof value !== "number") return "n/a";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

function EvidencePanel({ evidence }: { evidence: EvidenceSummary }) {
  return (
    <section className="evidence-panel" aria-label="Evidence summary">
      <div className="panel-heading">
        <h3>
          <BookOpenText size={17} aria-hidden />
          Evidence Coverage
        </h3>
        <span>{evidence.retrieval?.retrieval_model ?? evidence.retrieval?.embedding_model ?? "local index"} - {evidence.records.length} sources</span>
      </div>
      <div className="coverage-row">
        {Object.entries(evidence.coverage).map(([key, value]) => (
          <div className="coverage-pill" key={key}>
            <span>{key.replaceAll("_", " ")}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="evidence-list">
        {evidence.records.map((record) => (
          <article className="evidence-item" key={record.id}>
            <div>
              <strong>{record.title}</strong>
              <span>
                {record.source} - {record.confidence} - score {record.retrieval?.score?.toFixed(3) ?? "n/a"}
                {typeof record.retrieval?.facet_score === "number" ? ` / facet ${record.retrieval.facet_score.toFixed(2)}` : ""}
              </span>
            </div>
            <p>{record.summary}</p>
            {record.source_url?.startsWith("http") ? (
              <a href={record.source_url} target="_blank" rel="noreferrer" aria-label={`Open ${record.source}`}>
                <ExternalLink size={16} aria-hidden />
              </a>
            ) : (
              <span className="evidence-link-placeholder" aria-hidden>
                <ExternalLink size={16} />
              </span>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function TracePanel({
  trace
}: {
  trace: Array<{ name: string; status: string; timestamp: string; detail: Record<string, unknown> }>;
}) {
  return (
    <section className="trace-panel" aria-label="Agent trace">
      <div className="panel-heading">
        <h3>
          <ShieldCheck size={17} aria-hidden />
          Agent Trace
        </h3>
        <span>{trace.length} steps</span>
      </div>
      <div className="trace-list">
        {trace.map((step) => (
          <article key={`${step.name}-${step.timestamp}`}>
            <div>
              <strong>{step.name.replaceAll("_", " ")}</strong>
              <span>{step.status} - {new Date(step.timestamp).toLocaleTimeString()}</span>
            </div>
            <code>{JSON.stringify(step.detail)}</code>
          </article>
        ))}
      </div>
    </section>
  );
}

function MetricCard({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="metric-card">
      <div className="metric-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ScoreBars({ scores, nativeScores }: { scores: Score; nativeScores: Score }) {
  const rows = [
    { label: "Composite", value: scores.composite_quality, native: nativeScores.composite_quality, max: 1 },
    { label: "CAI", value: scores.cai, native: nativeScores.cai, max: 1 },
    { label: "tRNA adaptation", value: scores.tissue_codon_adaptation ?? 0, native: nativeScores.tissue_codon_adaptation ?? 0, max: 1.2 },
    { label: "GC", value: scores.gc_fraction, native: nativeScores.gc_fraction, max: 1 },
    { label: "GC window dev", value: scores.gc_window_max_deviation ?? 0, native: nativeScores.gc_window_max_deviation ?? 0, max: 0.5 },
    { label: "5 prime GC dev", value: scores.five_prime_gc_deviation ?? 0, native: nativeScores.five_prime_gc_deviation ?? 0, max: 0.5 },
    { label: "Hairpin proxy", value: scores.hairpin_proxy_score ?? 0, native: nativeScores.hairpin_proxy_score ?? 0, max: 1 },
    { label: "Structure proxy", value: scores.secondary_structure_proxy_score ?? 0, native: nativeScores.secondary_structure_proxy_score ?? 0, max: 1 },
    { label: "Sequence complexity", value: scores.sequence_complexity ?? 0, native: nativeScores.sequence_complexity ?? 0, max: 1 },
    { label: "Policy score", value: scores.sequence_policy_violation_score ?? 0, native: nativeScores.sequence_policy_violation_score ?? 0, max: 5 },
    { label: "CpG / 100 nt", value: scores.cpg_density_per_100nt, native: nativeScores.cpg_density_per_100nt, max: 25 }
  ];

  return (
    <div className="score-bars">
      {rows.map((row) => (
        <div className="score-row" key={row.label}>
          <div>
            <span>{row.label}</span>
            <strong>{row.value.toFixed(row.label === "CpG / 100 nt" ? 2 : 3)}</strong>
          </div>
          <div className="bar-track">
            <span className="bar native" style={{ width: `${clampPercent(row.native, row.max)}%` }} />
            <span className="bar optimized" style={{ width: `${clampPercent(row.value, row.max)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function clampPercent(value: number, max: number) {
  return Math.max(0, Math.min(100, (value / max) * 100));
}

function formatReason(reason?: string) {
  if (!reason) return "not available";
  return reason.replaceAll("_", " ");
}

function formatBytes(value: number) {
  if (!Number.isFinite(value)) return "n/a";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMetric(value?: number, digits = 3) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  return value.toFixed(digits);
}

function formatBoolean(value?: boolean | null) {
  if (typeof value !== "boolean") return "n/a";
  return value ? "yes" : "no";
}

function formatCurrency(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  return `$${value.toFixed(value >= 1 ? 2 : 6)}`;
}

function formatPercent(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toFixed(0)}%`;
}

function qualityIssueText(results?: Array<{ case_id: string; status: string; errors: string[]; warnings: string[] }>) {
  if (!results?.length) return "Waiting for benchmark results.";
  const issue = results.find((result) => result.status !== "pass" || result.errors.length || result.warnings.length);
  if (!issue) return "All configured gates pass.";
  const message = issue.errors[0] ?? issue.warnings[0] ?? issue.status;
  return `${issue.case_id}: ${message}`;
}

function optimizerCaseSummary(metrics: Record<string, number>) {
  const feasible = typeof metrics.feasible_count === "number" ? metrics.feasible_count : "n/a";
  const candidates = typeof metrics.candidate_count === "number" ? metrics.candidate_count : "n/a";
  const unique = typeof metrics.unique_cds_count === "number" ? metrics.unique_cds_count : "n/a";
  const distance = formatMetric(metrics.mean_pairwise_codon_distance, 3);
  const delta = formatMetric(metrics.recommended_composite_delta, 3);
  const structure = formatMetric(metrics.recommended_secondary_structure_proxy, 3);
  return `feasible ${feasible}/${candidates} / unique ${unique} / distance ${distance} / delta ${delta} / structure ${structure}`;
}

function formatRefreshEntry(entry?: DataRefreshEntry | null) {
  if (!entry) return "not recorded";
  const date = entry.completed_at ? new Date(entry.completed_at).toLocaleDateString() : "date n/a";
  return `${entry.refresh_status.replaceAll("_", " ")} / ${date}`;
}

function formatRefreshSummary(entry?: DataRefreshEntry | null) {
  if (!entry?.summary) return "No refresh summary available";
  const summary = entry.summary;
  if (typeof summary.structured_records === "number") {
    return `Baseline ${summary.structured_records} records / ${summary.rag_chunks ?? "n/a"} RAG chunks`;
  }
  return `Ops ${summary.succeeded ?? 0}/${summary.planned ?? 0} succeeded / ${summary.failed ?? 0} failed`;
}

function formatGeneRegionCoverage(row?: DataCoverage["gene_region_matrix"][number]) {
  if (!row) return "n/a";
  const expression = typeof row.median_expression_max === "number" ? ` / TPM ${formatMetric(row.median_expression_max, 1)}` : "";
  return `${row.gene} ${row.brain_region} ${row.live_records}/${row.records} live${expression}`;
}

function formatCellTypeCoverage(row?: DataCoverage["cell_type_matrix"][number]) {
  if (!row) return "n/a";
  const cells = typeof row.cell_count_max === "number" ? ` / cells ${formatMetric(row.cell_count_max, 0)}` : "";
  return `${row.cell_type} ${row.live_records}/${row.records} live${cells}`;
}

function formatRagCoverage(coverage: Record<string, unknown>) {
  const values = ["brain_region", "cell_type", "modality", "species"]
    .map((key) => `${key.replace("_", " ")} ${String(coverage[key] ?? "n/a")}`);
  const highConfidence = coverage.high_confidence_results;
  return `${values.join(" / ")} / high ${typeof highConfidence === "number" ? highConfidence : "n/a"}`;
}

function ragFacetGapSummary(analysis?: RagInspectionResult["facet_gap_analysis"]) {
  const facets = analysis?.facets ?? {};
  const issue = Object.entries(facets).find(([, value]) => value.status === "available_but_not_retrieved" || value.status === "missing_from_corpus");
  if (!issue) return "Requested facets are represented in retrieved evidence.";
  const [name, value] = issue;
  const top = value.top_available_values[0];
  const topText = top ? ` / corpus top ${top.value} ${top.count}` : "";
  return `${name.replace("_", " ")} ${value.status.replaceAll("_", " ")}${topText}`;
}

function qcRequestTargetSummary(checks?: Record<string, string>) {
  const values = Object.values(checks ?? {});
  if (!values.length) return "n/a";
  const pass = values.filter((value) => value === "pass").length;
  const fail = values.filter((value) => value === "fail").length;
  return `${pass} pass / ${fail} fail`;
}

function totalRequestCount(metrics?: MetricsSnapshot | null) {
  return metrics?.requests.reduce((total, request) => total + request.count, 0) ?? "n/a";
}

function topRequests(metrics?: MetricsSnapshot | null) {
  return [...(metrics?.requests ?? [])].sort((left, right) => right.count - left.count).slice(0, 3);
}

function readinessAttentionGates(readiness?: DeploymentReadinessStatus | null) {
  const gates = readiness?.gates ?? [];
  const attention = gates.filter((gate) => gate.status !== "pass");
  return (attention.length ? attention : gates).slice(0, 4);
}

function deploymentActionCoverage(readiness?: DeploymentReadinessStatus | null) {
  const attentionCount = readiness?.attention_gates?.length;
  const actionCount = readiness?.required_actions?.length;
  if (attentionCount === undefined || actionCount === undefined) {
    return "n/a";
  }
  return `${actionCount}/${attentionCount}`;
}

function deploymentGateStatus(readiness: DeploymentReadinessStatus | null | undefined, name: string) {
  return readiness?.gates?.find((gate) => gate.name === name)?.status ?? "n/a";
}

function deploymentGateDetail(readiness: DeploymentReadinessStatus | null | undefined, name: string, key: string) {
  const value = readiness?.gates?.find((gate) => gate.name === name)?.details?.[key];
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "n/a";
}

function deploymentGateHash(readiness: DeploymentReadinessStatus | null | undefined, name: string, key: string) {
  const value = readiness?.gates?.find((gate) => gate.name === name)?.details?.[key];
  return typeof value === "string" ? value.slice(0, 10) : "n/a";
}

function deploymentGateFreshness(readiness: DeploymentReadinessStatus | null | undefined, name: string) {
  const details = readiness?.gates?.find((gate) => gate.name === name)?.details;
  if (!details) return "n/a";
  return formatArchiveFreshness({
    freshness_status: typeof details.freshness_status === "string" ? details.freshness_status : undefined,
    latest_age_hours: typeof details.latest_age_hours === "number" ? details.latest_age_hours : null,
    latest_created_at: typeof details.latest_created_at === "string" ? details.latest_created_at : null,
    freshness_policy: {
      warning_hours: typeof details.freshness_warning_hours === "number" ? details.freshness_warning_hours : undefined,
    },
  });
}

function deploymentTrnaCaveatCount(readiness: DeploymentReadinessStatus | null | undefined) {
  const provenance = readiness?.gates?.find((gate) => gate.name === "data_provenance");
  const caveats = provenance?.details?.trna_prior_caveats;
  if (!caveats || typeof caveats !== "object") return "n/a";
  const count = (caveats as { caveat_count?: unknown }).caveat_count;
  return typeof count === "number" ? count : "n/a";
}

function formatDuration(seconds?: number) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "n/a";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatSeconds(seconds?: number) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "n/a";
  return seconds < 10 ? `${seconds.toFixed(2)}s` : `${seconds.toFixed(1)}s`;
}

function formatArchiveFreshness(summary?: ArchiveSemanticFreshness | null) {
  if (!summary) return "n/a";
  const status = summary.freshness_status ?? "n/a";
  const age = typeof summary.latest_age_hours === "number" && Number.isFinite(summary.latest_age_hours)
    ? `${summary.latest_age_hours.toFixed(summary.latest_age_hours < 10 ? 1 : 0)}h`
    : "age n/a";
  return `${status} / ${age}`;
}

function formatJobStatusCounts(counts?: Record<string, number>) {
  const entries = Object.entries(counts ?? {});
  if (!entries.length) return "n/a";
  return entries.map(([status, count]) => `${status} ${count}`).join(" / ");
}

function parseBatchGenes(value: string) {
  return Array.from(new Set(value.split(/[\s,;]+/).map((item) => item.trim().toUpperCase()).filter(Boolean))).slice(0, 50);
}

function firstJobRunId(job: JobSummary) {
  return job.result?.run_id ?? job.result?.runs?.[0]?.run_id ?? "";
}

function jobStatusText(job: JobSummary) {
  if (job.result?.run_id) return job.result.run_id;
  if (job.result?.runs?.length) {
    const summary = job.result.summary;
    return `${job.result.batch_status ?? "batch"}: ${summary?.succeeded ?? job.result.runs.length}/${summary?.requested ?? job.result.runs.length} succeeded`;
  }
  return job.result?.refresh_status ?? job.error ?? "waiting";
}

function failedAuditChecks(audit: DataAudit) {
  return audit.checks.filter((check) => check.result !== "pass").length;
}

function chunkSequence(sequence: string) {
  return sequence.match(/.{1,12}/g)?.join(" ") ?? sequence;
}
