use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};
use serde::de::DeserializeOwned;
use serde::Serialize;

use crate::error::PipelineError;
use crate::model::{
    ApprovedPacket, AuditEvent, CloudCommandKind, CloudCommandTrace, CloudSummaryResult,
    CloudTaskPacket, CloudTaskRun, DocumentId, Marker, PacketId, PacketLineage, ParsedDocument,
    QueueStatus, RawDocument, ReplayBundle, ReviewArtifact, ReviewDecision, ReviewDiffState,
    ReviewEvidenceState, ReviewGateState, ReviewId, ReviewQueueItem, ValidationReport, WikiNode,
};

pub struct Database {
    conn: Connection,
}

impl Database {
    pub fn open(path: &str) -> Result<Self, PipelineError> {
        let conn = Connection::open(path)
            .map_err(|e| PipelineError::Storage(format!("failed to open db: {e}")))?;
        let db = Self { conn };
        db.init()?;
        Ok(db)
    }

    fn init(&self) -> Result<(), PipelineError> {
        self.conn
            .execute_batch(
                r#"
CREATE TABLE IF NOT EXISTS raw_documents (
    document_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parsed_documents (
    document_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marker_sets (
    document_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_packets (
    packet_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_results (
    packet_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_task_runs (
    cloud_run_id TEXT PRIMARY KEY,
    packet_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    task_id TEXT,
    task_url TEXT,
    environment_id TEXT,
    matched_remote_identity TEXT,
    current_head_sha TEXT,
    current_branch TEXT,
    head_contained_in_allowed_remote_ref INTEGER,
    resolution_method TEXT NOT NULL,
    handoff_mode TEXT NOT NULL DEFAULT 'path_packet_v1',
    packet_path TEXT NOT NULL,
    schema_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    allowed_apply_paths_json TEXT NOT NULL,
    new_apply_paths_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    finished_at TEXT,
    final_status TEXT NOT NULL,
    error_text TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_task_runs_packet_attempt
ON cloud_task_runs(packet_id, attempt_index);
CREATE TABLE IF NOT EXISTS cloud_command_traces (
    trace_id TEXT PRIMARY KEY,
    cloud_run_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    command_kind TEXT NOT NULL,
    command_text TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    exit_status INTEGER,
    stdout_summary TEXT,
    stderr_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_cloud_command_traces_packet_attempt
ON cloud_command_traces(packet_id, attempt_index, started_at);
CREATE TABLE IF NOT EXISTS validation_reports (
    packet_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approved_packets (
    packet_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_nodes (
    wiki_node_id TEXT PRIMARY KEY,
    approved_packet_id TEXT UNIQUE NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    packet_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_reviewer TEXT,
    decision TEXT,
    notes TEXT,
    claimed_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packet_lineage (
    packet_id TEXT PRIMARY KEY,
    lineage_root_packet_id TEXT NOT NULL,
    prior_packet_id TEXT,
    successor_packet_id TEXT,
    spawned_by_review_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_artifacts (
    review_id TEXT PRIMARY KEY,
    packet_id TEXT UNIQUE NOT NULL,
    packet_version TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT NOT NULL,
    gate_snapshot_json TEXT NOT NULL,
    blocker_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_diff_state (
    packet_id TEXT NOT NULL,
    diff_target_id TEXT NOT NULL,
    change_count INTEGER NOT NULL,
    reviewed INTEGER NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    summary_json TEXT NOT NULL,
    PRIMARY KEY (packet_id, diff_target_id)
);
CREATE TABLE IF NOT EXISTS review_evidence_state (
    packet_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    reviewed INTEGER NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (packet_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS review_gate_state (
    packet_id TEXT PRIMARY KEY,
    packet_version TEXT NOT NULL,
    required_fields_loaded INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    blocker_count INTEGER NOT NULL,
    diff_reviewed INTEGER NOT NULL,
    evidence_reviewed INTEGER NOT NULL,
    stale_flag INTEGER NOT NULL,
    dirty_flag INTEGER NOT NULL,
    active_diff_target_id TEXT,
    active_evidence_id TEXT,
    active_validation_issue_id TEXT,
    approve_enabled INTEGER NOT NULL,
    reject_enabled INTEGER NOT NULL,
    rework_enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_queue_status_created
ON review_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_packet_lineage_root
ON packet_lineage(lineage_root_packet_id);
CREATE INDEX IF NOT EXISTS idx_review_artifacts_packet
ON review_artifacts(packet_id);
CREATE INDEX IF NOT EXISTS idx_review_diff_state_packet
ON review_diff_state(packet_id);
CREATE INDEX IF NOT EXISTS idx_review_evidence_state_packet
ON review_evidence_state(packet_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_aggregate
ON audit_events(aggregate_id, event_id);
"#,
            )
            .map_err(|e| PipelineError::Storage(format!("failed to init schema: {e}")))?;
        self.ensure_cloud_task_run_handoff_mode_column()?;
        self.ensure_cloud_task_run_binding_columns()?;
        Ok(())
    }

    fn ensure_cloud_task_run_handoff_mode_column(&self) -> Result<(), PipelineError> {
        match self.conn.execute(
            "ALTER TABLE cloud_task_runs ADD COLUMN handoff_mode TEXT NOT NULL DEFAULT 'path_packet_v1'",
            [],
        ) {
            Ok(_) => Ok(()),
            Err(error) => {
                let message = error.to_string();
                if message.contains("duplicate column name: handoff_mode") {
                    Ok(())
                } else {
                    Err(PipelineError::Storage(format!(
                        "failed to ensure cloud_task_runs.handoff_mode column: {error}"
                    )))
                }
            }
        }
    }

    fn ensure_cloud_task_run_binding_columns(&self) -> Result<(), PipelineError> {
        self.ensure_cloud_task_run_column(
            "ALTER TABLE cloud_task_runs ADD COLUMN matched_remote_identity TEXT",
            "matched_remote_identity",
        )?;
        self.ensure_cloud_task_run_column(
            "ALTER TABLE cloud_task_runs ADD COLUMN current_head_sha TEXT",
            "current_head_sha",
        )?;
        self.ensure_cloud_task_run_column(
            "ALTER TABLE cloud_task_runs ADD COLUMN current_branch TEXT",
            "current_branch",
        )?;
        self.ensure_cloud_task_run_column(
            "ALTER TABLE cloud_task_runs ADD COLUMN head_contained_in_allowed_remote_ref INTEGER",
            "head_contained_in_allowed_remote_ref",
        )?;
        Ok(())
    }

    fn ensure_cloud_task_run_column(
        &self,
        sql: &str,
        column_name: &str,
    ) -> Result<(), PipelineError> {
        match self.conn.execute(sql, []) {
            Ok(_) => Ok(()),
            Err(error) => {
                let message = error.to_string();
                let duplicate_message = format!("duplicate column name: {column_name}");
                if message.contains(&duplicate_message) {
                    Ok(())
                } else {
                    Err(PipelineError::Storage(format!(
                        "failed to ensure cloud_task_runs.{column_name} column: {error}"
                    )))
                }
            }
        }
    }

    pub fn save_raw(&mut self, raw: &RawDocument) -> Result<(), PipelineError> {
        self.save_document_stage(
            "raw_documents",
            &raw.document_id.0,
            raw,
            "acquisition",
            "raw_saved",
        )
    }

    pub fn load_raw(&self, document_id: &DocumentId) -> Result<Option<RawDocument>, PipelineError> {
        self.load_document_stage("raw_documents", &document_id.0)
    }

    pub fn save_parsed(&mut self, parsed: &ParsedDocument) -> Result<(), PipelineError> {
        self.save_document_stage(
            "parsed_documents",
            &parsed.raw.document_id.0,
            parsed,
            "parse",
            "parsed_saved",
        )
    }

    pub fn load_parsed(
        &self,
        document_id: &DocumentId,
    ) -> Result<Option<ParsedDocument>, PipelineError> {
        self.load_document_stage("parsed_documents", &document_id.0)
    }

    pub fn save_markers(
        &mut self,
        document_id: &DocumentId,
        markers: &[Marker],
    ) -> Result<(), PipelineError> {
        self.save_document_stage(
            "marker_sets",
            &document_id.0,
            &markers,
            "preflight",
            "markers_saved",
        )
    }

    pub fn load_markers(
        &self,
        document_id: &DocumentId,
    ) -> Result<Option<Vec<Marker>>, PipelineError> {
        self.load_document_stage("marker_sets", &document_id.0)
    }

    pub fn save_packet(&mut self, packet: &CloudTaskPacket) -> Result<(), PipelineError> {
        self.save_packet_stage(
            "cloud_packets",
            &packet.packet_id.0,
            &packet.document_id.0,
            packet,
            "packetize",
            "packet_saved",
        )?;
        self.ensure_packet_lineage(&packet.packet_id)?;
        Ok(())
    }

    pub fn load_packet(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<CloudTaskPacket>, PipelineError> {
        self.load_packet_stage("cloud_packets", &packet_id.0)
    }

    pub fn save_result(
        &mut self,
        document_id: &DocumentId,
        result: &CloudSummaryResult,
    ) -> Result<(), PipelineError> {
        self.save_packet_stage(
            "cloud_results",
            &result.packet_id.0,
            &document_id.0,
            result,
            "cloud",
            "result_saved",
        )
    }

    pub fn load_result(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<CloudSummaryResult>, PipelineError> {
        self.load_packet_stage("cloud_results", &packet_id.0)
    }

    pub fn next_cloud_attempt_index(&self, packet_id: &PacketId) -> Result<u32, PipelineError> {
        let next: i64 = self
            .conn
            .query_row(
                r#"
SELECT COALESCE(MAX(attempt_index), 0) + 1
FROM cloud_task_runs
WHERE packet_id = ?1
"#,
                params![&packet_id.0],
                |row| row.get(0),
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to query next cloud attempt: {e}"))
            })?;
        Ok(next as u32)
    }

    pub fn save_cloud_task_run(&mut self, run: &CloudTaskRun) -> Result<(), PipelineError> {
        self.conn
            .execute(
                r#"
INSERT INTO cloud_task_runs (
    cloud_run_id, packet_id, attempt_index, task_id, task_url, environment_id,
    matched_remote_identity, current_head_sha, current_branch, head_contained_in_allowed_remote_ref,
    resolution_method, handoff_mode, packet_path, schema_path, output_path, allowed_apply_paths_json,
    new_apply_paths_json, submitted_at, finished_at, final_status, error_text
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21)
ON CONFLICT(cloud_run_id) DO UPDATE SET
    packet_id = excluded.packet_id,
    attempt_index = excluded.attempt_index,
    task_id = excluded.task_id,
    task_url = excluded.task_url,
    environment_id = excluded.environment_id,
    matched_remote_identity = excluded.matched_remote_identity,
    current_head_sha = excluded.current_head_sha,
    current_branch = excluded.current_branch,
    head_contained_in_allowed_remote_ref = excluded.head_contained_in_allowed_remote_ref,
    resolution_method = excluded.resolution_method,
    handoff_mode = excluded.handoff_mode,
    packet_path = excluded.packet_path,
    schema_path = excluded.schema_path,
    output_path = excluded.output_path,
    allowed_apply_paths_json = excluded.allowed_apply_paths_json,
    new_apply_paths_json = excluded.new_apply_paths_json,
    submitted_at = excluded.submitted_at,
    finished_at = excluded.finished_at,
    final_status = excluded.final_status,
    error_text = excluded.error_text
"#,
                params![
                    &run.cloud_run_id,
                    &run.packet_id.0,
                    run.attempt_index as i64,
                    run.task_id.as_deref(),
                    run.task_url.as_deref(),
                    run.environment_id.as_deref(),
                    run.matched_remote_identity.as_deref(),
                    run.current_head_sha.as_deref(),
                    run.current_branch.as_deref(),
                    run.head_contained_in_allowed_remote_ref.map(bool_to_sql),
                    &run.resolution_method,
                    &run.handoff_mode,
                    &run.packet_path,
                    &run.schema_path,
                    &run.output_path,
                    to_json(&run.allowed_apply_paths)?,
                    to_json(&run.new_apply_paths)?,
                    run.submitted_at.to_rfc3339(),
                    run.finished_at.map(|ts| ts.to_rfc3339()),
                    &run.final_status,
                    run.error_text.as_deref(),
                ],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to save cloud task run: {e}")))?;
        self.write_event(
            &run.packet_id.0,
            "packet",
            "cloud",
            "task_run_saved",
            Some(format!(
                r#"{{"cloud_run_id":"{}","attempt_index":{},"final_status":"{}"}}"#,
                run.cloud_run_id, run.attempt_index, run.final_status
            )),
        )?;
        Ok(())
    }

    pub fn load_cloud_task_runs(
        &self,
        packet_id: &PacketId,
    ) -> Result<Vec<CloudTaskRun>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT cloud_run_id, attempt_index, task_id, task_url, environment_id,
       matched_remote_identity, current_head_sha, current_branch, head_contained_in_allowed_remote_ref,
       resolution_method, handoff_mode, packet_path, schema_path, output_path, allowed_apply_paths_json, new_apply_paths_json,
       submitted_at, finished_at, final_status, error_text
FROM cloud_task_runs
WHERE packet_id = ?1
ORDER BY attempt_index ASC
"#,
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to prepare cloud run query: {e}"))
            })?;
        let rows = stmt
            .query_map(params![&packet_id.0], |row| {
                Ok(CloudTaskRun {
                    cloud_run_id: row.get(0)?,
                    packet_id: packet_id.clone(),
                    attempt_index: row.get::<_, i64>(1)? as u32,
                    task_id: row.get(2)?,
                    task_url: row.get(3)?,
                    environment_id: row.get(4)?,
                    matched_remote_identity: row.get(5)?,
                    current_head_sha: row.get(6)?,
                    current_branch: row.get(7)?,
                    head_contained_in_allowed_remote_ref: row
                        .get::<_, Option<i64>>(8)?
                        .map(sql_to_bool),
                    resolution_method: row.get(9)?,
                    handoff_mode: row.get(10)?,
                    packet_path: row.get(11)?,
                    schema_path: row.get(12)?,
                    output_path: row.get(13)?,
                    allowed_apply_paths: from_json(&row.get::<_, String>(14)?)
                        .map_err(to_sqlite_pipeline_err)?,
                    new_apply_paths: from_json(&row.get::<_, String>(15)?)
                        .map_err(to_sqlite_pipeline_err)?,
                    submitted_at: parse_ts(Some(row.get::<_, String>(16)?))
                        .map_err(to_sqlite_parse_err)?
                        .expect("submitted_at is required"),
                    finished_at: parse_ts(row.get::<_, Option<String>>(17)?)
                        .map_err(to_sqlite_parse_err)?,
                    final_status: row.get(18)?,
                    error_text: row.get(19)?,
                })
            })
            .map_err(|e| PipelineError::Storage(format!("failed to map cloud runs: {e}")))?;
        collect_rows(rows)
    }

    pub fn save_cloud_command_trace(
        &mut self,
        packet_id: &PacketId,
        trace: &CloudCommandTrace,
    ) -> Result<(), PipelineError> {
        self.conn
            .execute(
                r#"
INSERT INTO cloud_command_traces (
    trace_id, cloud_run_id, packet_id, attempt_index, command_kind, command_text,
    started_at, finished_at, exit_status, stdout_summary, stderr_summary
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
ON CONFLICT(trace_id) DO UPDATE SET
    cloud_run_id = excluded.cloud_run_id,
    packet_id = excluded.packet_id,
    attempt_index = excluded.attempt_index,
    command_kind = excluded.command_kind,
    command_text = excluded.command_text,
    started_at = excluded.started_at,
    finished_at = excluded.finished_at,
    exit_status = excluded.exit_status,
    stdout_summary = excluded.stdout_summary,
    stderr_summary = excluded.stderr_summary
"#,
                params![
                    &trace.trace_id,
                    &trace.cloud_run_id,
                    &packet_id.0,
                    trace.attempt_index as i64,
                    trace.command_kind.as_str(),
                    &trace.command_text,
                    trace.started_at.to_rfc3339(),
                    trace.finished_at.to_rfc3339(),
                    trace.exit_status,
                    trace.stdout_summary.as_deref(),
                    trace.stderr_summary.as_deref(),
                ],
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to save cloud command trace: {e}"))
            })?;
        Ok(())
    }

    pub fn load_cloud_command_traces(
        &self,
        packet_id: &PacketId,
    ) -> Result<Vec<CloudCommandTrace>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT trace_id, cloud_run_id, attempt_index, command_kind, command_text,
       started_at, finished_at, exit_status, stdout_summary, stderr_summary
FROM cloud_command_traces
WHERE packet_id = ?1
ORDER BY attempt_index ASC, started_at ASC, trace_id ASC
"#,
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to prepare cloud trace query: {e}"))
            })?;
        let rows = stmt
            .query_map(params![&packet_id.0], |row| {
                Ok(CloudCommandTrace {
                    trace_id: row.get(0)?,
                    cloud_run_id: row.get(1)?,
                    attempt_index: row.get::<_, i64>(2)? as u32,
                    command_kind: CloudCommandKind::from_db(&row.get::<_, String>(3)?).ok_or_else(
                        || {
                            rusqlite::Error::InvalidColumnType(
                                3,
                                "command_kind".to_string(),
                                rusqlite::types::Type::Text,
                            )
                        },
                    )?,
                    command_text: row.get(4)?,
                    started_at: parse_ts(Some(row.get::<_, String>(5)?))
                        .map_err(to_sqlite_parse_err)?
                        .expect("started_at is required"),
                    finished_at: parse_ts(Some(row.get::<_, String>(6)?))
                        .map_err(to_sqlite_parse_err)?
                        .expect("finished_at is required"),
                    exit_status: row.get(7)?,
                    stdout_summary: row.get(8)?,
                    stderr_summary: row.get(9)?,
                })
            })
            .map_err(|e| PipelineError::Storage(format!("failed to map cloud traces: {e}")))?;
        collect_rows(rows)
    }

    pub fn save_validation(
        &mut self,
        document_id: &DocumentId,
        report: &ValidationReport,
    ) -> Result<(), PipelineError> {
        self.save_packet_stage(
            "validation_reports",
            &report.packet_id.0,
            &document_id.0,
            report,
            "validate",
            "validation_saved",
        )
    }

    pub fn load_validation(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<ValidationReport>, PipelineError> {
        self.load_packet_stage("validation_reports", &packet_id.0)
    }

    pub fn enqueue_review(
        &mut self,
        packet_id: &PacketId,
        document_id: &DocumentId,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        self.conn
            .execute(
                r#"
INSERT INTO review_queue (
    packet_id, document_id, status, assigned_reviewer, decision, notes,
    claimed_at, completed_at, created_at, updated_at
) VALUES (?1, ?2, ?3, NULL, NULL, NULL, NULL, NULL, ?4, ?4)
ON CONFLICT(packet_id) DO UPDATE SET
    status = excluded.status,
    assigned_reviewer = NULL,
    decision = NULL,
    notes = NULL,
    claimed_at = NULL,
    completed_at = NULL,
    updated_at = excluded.updated_at
"#,
                params![
                    &packet_id.0,
                    &document_id.0,
                    QueueStatus::Pending.as_str(),
                    now
                ],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to enqueue review: {e}")))?;
        self.write_event(
            &packet_id.0,
            "packet",
            "review_queue",
            "queued",
            Some(format!(r#"{{"document_id":"{}"}}"#, document_id.0)),
        )?;
        Ok(())
    }

    pub fn claim_next_review(
        &mut self,
        reviewer: &str,
    ) -> Result<Option<ReviewQueueItem>, PipelineError> {
        let tx = self
            .conn
            .transaction()
            .map_err(|e| PipelineError::Storage(format!("failed to open tx: {e}")))?;
        let packet_id: Option<String> = tx
            .query_row(
                r#"
SELECT packet_id
FROM review_queue
WHERE status = ?1
ORDER BY created_at ASC
LIMIT 1
"#,
                params![QueueStatus::Pending.as_str()],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| {
                PipelineError::Storage(format!("failed to query next review item: {e}"))
            })?;
        let Some(packet_id) = packet_id else {
            tx.commit()
                .map_err(|e| PipelineError::Storage(format!("failed to commit empty tx: {e}")))?;
            return Ok(None);
        };
        let now = Utc::now().to_rfc3339();
        tx.execute(
            r#"
UPDATE review_queue
SET status = ?1,
    assigned_reviewer = ?2,
    claimed_at = ?3,
    updated_at = ?3
WHERE packet_id = ?4
"#,
            params![QueueStatus::InReview.as_str(), reviewer, now, &packet_id],
        )
        .map_err(|e| PipelineError::Storage(format!("failed to claim review item: {e}")))?;
        tx.commit()
            .map_err(|e| PipelineError::Storage(format!("failed to commit claim tx: {e}")))?;
        self.write_event(
            &packet_id,
            "packet",
            "review_queue",
            "claimed",
            Some(format!(r#"{{"reviewer":"{}"}}"#, reviewer)),
        )?;
        self.load_review_item(&PacketId(packet_id))
    }

    pub fn claim_review_packet(
        &mut self,
        packet_id: &PacketId,
        reviewer: &str,
    ) -> Result<ReviewQueueItem, PipelineError> {
        let now = Utc::now().to_rfc3339();
        let affected = self
            .conn
            .execute(
                r#"
UPDATE review_queue
SET status = ?1,
    assigned_reviewer = ?2,
    claimed_at = ?3,
    updated_at = ?3
WHERE packet_id = ?4
  AND status = ?5
"#,
                params![
                    QueueStatus::InReview.as_str(),
                    reviewer,
                    now,
                    &packet_id.0,
                    QueueStatus::Pending.as_str(),
                ],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to claim review item: {e}")))?;
        if affected == 0 {
            return Err(PipelineError::Review(format!(
                "packet {} is not pending",
                packet_id.0
            )));
        }
        self.write_event(
            &packet_id.0,
            "packet",
            "review_queue",
            "claimed",
            Some(format!(r#"{{"reviewer":"{}"}}"#, reviewer)),
        )?;
        self.load_review_item(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review queue item {} not found", packet_id.0))
        })
    }

    pub fn load_review_item(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<ReviewQueueItem>, PipelineError> {
        self.conn
            .query_row(
                r#"
SELECT
    packet_id,
    document_id,
    status,
    assigned_reviewer,
    decision,
    notes,
    claimed_at,
    completed_at,
    created_at,
    updated_at
FROM review_queue
WHERE packet_id = ?1
"#,
                params![&packet_id.0],
                |row| {
                    Ok(ReviewQueueItem {
                        packet_id: PacketId(row.get::<_, String>(0)?),
                        document_id: DocumentId(row.get::<_, String>(1)?),
                        status: QueueStatus::from_db(&row.get::<_, String>(2)?).ok_or_else(
                            || {
                                rusqlite::Error::InvalidColumnType(
                                    2,
                                    "status".to_string(),
                                    rusqlite::types::Type::Text,
                                )
                            },
                        )?,
                        assigned_reviewer: row.get(3)?,
                        decision: parse_decision(row.get::<_, Option<String>>(4)?),
                        notes: row.get(5)?,
                        claimed_at: parse_ts(row.get::<_, Option<String>>(6)?)
                            .map_err(to_sqlite_parse_err)?,
                        completed_at: parse_ts(row.get::<_, Option<String>>(7)?)
                            .map_err(to_sqlite_parse_err)?,
                        created_at: parse_ts(Some(row.get::<_, String>(8)?))
                            .map_err(to_sqlite_parse_err)?
                            .expect("created_at is required"),
                        updated_at: parse_ts(Some(row.get::<_, String>(9)?))
                            .map_err(to_sqlite_parse_err)?
                            .expect("updated_at is required"),
                    })
                },
            )
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed to load review item: {e}")))
    }

    pub fn list_review_items(&self) -> Result<Vec<ReviewQueueItem>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT
    packet_id,
    document_id,
    status,
    assigned_reviewer,
    decision,
    notes,
    claimed_at,
    completed_at,
    created_at,
    updated_at
FROM review_queue
ORDER BY
    CASE status
        WHEN 'in_review' THEN 0
        WHEN 'pending' THEN 1
        ELSE 2
    END,
    updated_at DESC,
    created_at DESC,
    packet_id ASC
"#,
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to prepare review queue listing: {e}"))
            })?;
        let rows = stmt
            .query_map([], |row| {
                Ok(ReviewQueueItem {
                    packet_id: PacketId(row.get::<_, String>(0)?),
                    document_id: DocumentId(row.get::<_, String>(1)?),
                    status: QueueStatus::from_db(&row.get::<_, String>(2)?).ok_or_else(|| {
                        rusqlite::Error::InvalidColumnType(
                            2,
                            "status".to_string(),
                            rusqlite::types::Type::Text,
                        )
                    })?,
                    assigned_reviewer: row.get(3)?,
                    decision: parse_decision(row.get::<_, Option<String>>(4)?),
                    notes: row.get(5)?,
                    claimed_at: parse_ts(row.get::<_, Option<String>>(6)?)
                        .map_err(to_sqlite_parse_err)?,
                    completed_at: parse_ts(row.get::<_, Option<String>>(7)?)
                        .map_err(to_sqlite_parse_err)?,
                    created_at: parse_ts(Some(row.get::<_, String>(8)?))
                        .map_err(to_sqlite_parse_err)?
                        .expect("created_at is required"),
                    updated_at: parse_ts(Some(row.get::<_, String>(9)?))
                        .map_err(to_sqlite_parse_err)?
                        .expect("updated_at is required"),
                })
            })
            .map_err(|e| PipelineError::Storage(format!("failed to map review queue rows: {e}")))?;
        collect_rows(rows)
    }

    pub fn ensure_packet_lineage(&mut self, packet_id: &PacketId) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        self.conn
            .execute(
                r#"
INSERT INTO packet_lineage (
    packet_id, lineage_root_packet_id, prior_packet_id, successor_packet_id,
    spawned_by_review_id, created_at, updated_at
) VALUES (?1, ?1, NULL, NULL, NULL, ?2, ?2)
ON CONFLICT(packet_id) DO NOTHING
"#,
                params![&packet_id.0, now],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to ensure packet lineage: {e}")))?;
        Ok(())
    }

    pub fn link_packet_successor(
        &mut self,
        prior_packet_id: &PacketId,
        successor_packet_id: &PacketId,
        spawned_by_review_id: Option<&ReviewId>,
    ) -> Result<(), PipelineError> {
        self.ensure_packet_lineage(prior_packet_id)?;
        self.ensure_packet_lineage(successor_packet_id)?;
        let prior = self.load_packet_lineage(prior_packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("lineage {} not found", prior_packet_id.0))
        })?;
        let now = Utc::now().to_rfc3339();
        self.conn
            .execute(
                r#"
UPDATE packet_lineage
SET lineage_root_packet_id = ?1,
    prior_packet_id = ?2,
    spawned_by_review_id = ?3,
    updated_at = ?4
WHERE packet_id = ?5
"#,
                params![
                    &prior.lineage_root_packet_id.0,
                    &prior_packet_id.0,
                    spawned_by_review_id.map(|id| id.0.as_str()),
                    &now,
                    &successor_packet_id.0,
                ],
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to update successor lineage: {e}"))
            })?;
        self.conn
            .execute(
                r#"
UPDATE packet_lineage
SET successor_packet_id = ?1,
    updated_at = ?2
WHERE packet_id = ?3
"#,
                params![&successor_packet_id.0, &now, &prior_packet_id.0],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to update prior lineage: {e}")))?;
        self.write_event(
            &prior_packet_id.0,
            "packet",
            "lineage",
            "successor_linked",
            Some(format!(
                r#"{{"successor_packet_id":"{}","review_id":"{}"}}"#,
                successor_packet_id.0,
                spawned_by_review_id
                    .map(|id| id.0.as_str())
                    .unwrap_or("null")
            )),
        )?;
        Ok(())
    }

    pub fn load_packet_lineage(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<PacketLineage>, PipelineError> {
        self.conn
            .query_row(
                r#"
SELECT
    packet_id,
    lineage_root_packet_id,
    prior_packet_id,
    successor_packet_id,
    spawned_by_review_id,
    created_at,
    updated_at
FROM packet_lineage
WHERE packet_id = ?1
"#,
                params![&packet_id.0],
                |row| {
                    Ok(PacketLineage {
                        packet_id: PacketId(row.get::<_, String>(0)?),
                        lineage_root_packet_id: PacketId(row.get::<_, String>(1)?),
                        prior_packet_id: row.get::<_, Option<String>>(2)?.map(PacketId),
                        successor_packet_id: row.get::<_, Option<String>>(3)?.map(PacketId),
                        spawned_by_review_id: row.get::<_, Option<String>>(4)?.map(ReviewId),
                        created_at: parse_ts(Some(row.get::<_, String>(5)?))
                            .map_err(to_sqlite_parse_err)?
                            .expect("created_at is required"),
                        updated_at: parse_ts(Some(row.get::<_, String>(6)?))
                            .map_err(to_sqlite_parse_err)?
                            .expect("updated_at is required"),
                    })
                },
            )
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed to load packet lineage: {e}")))
    }

    pub fn complete_review(
        &mut self,
        packet_id: &PacketId,
        status: QueueStatus,
        decision: Option<ReviewDecision>,
        notes: Option<&str>,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        let decision_db = decision.as_ref().map(decision_to_db);
        let affected = self
            .conn
            .execute(
                r#"
UPDATE review_queue
SET status = ?1,
    decision = ?2,
    notes = ?3,
    completed_at = ?4,
    updated_at = ?4
WHERE packet_id = ?5
"#,
                params![status.as_str(), decision_db, notes, now, &packet_id.0,],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to complete review: {e}")))?;
        if affected == 0 {
            return Err(PipelineError::NotFound(format!(
                "review queue item {} not found",
                packet_id.0
            )));
        }
        self.write_event(
            &packet_id.0,
            "packet",
            "review_queue",
            "completed",
            Some(format!(
                r#"{{"status":"{}","decision":"{}"}}"#,
                status.as_str(),
                decision_db.unwrap_or("null")
            )),
        )?;
        Ok(())
    }

    pub fn save_review_artifact(&mut self, artifact: &ReviewArtifact) -> Result<(), PipelineError> {
        let gate_snapshot_json = to_json(&artifact.gate_snapshot)?;
        let blocker_snapshot_json = to_json(&artifact.blocker_snapshot)?;
        self.conn
            .execute(
                r#"
INSERT INTO review_artifacts (
    review_id, packet_id, packet_version, reviewer, decision, notes,
    gate_snapshot_json, blocker_snapshot_json, created_at
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
ON CONFLICT(packet_id) DO UPDATE SET
    review_id = excluded.review_id,
    packet_version = excluded.packet_version,
    reviewer = excluded.reviewer,
    decision = excluded.decision,
    notes = excluded.notes,
    gate_snapshot_json = excluded.gate_snapshot_json,
    blocker_snapshot_json = excluded.blocker_snapshot_json,
    created_at = excluded.created_at
"#,
                params![
                    &artifact.review_id.0,
                    &artifact.packet_id.0,
                    &artifact.packet_version,
                    &artifact.reviewer,
                    decision_to_db(&artifact.decision),
                    &artifact.notes,
                    gate_snapshot_json,
                    blocker_snapshot_json,
                    artifact.created_at.to_rfc3339(),
                ],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to save review artifact: {e}")))?;
        self.write_event(
            &artifact.packet_id.0,
            "packet",
            "review",
            "artifact_saved",
            Some(format!(
                r#"{{"review_id":"{}","decision":"{}"}}"#,
                artifact.review_id.0,
                decision_to_db(&artifact.decision)
            )),
        )?;
        Ok(())
    }

    pub fn load_review_artifact(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<ReviewArtifact>, PipelineError> {
        let row: Option<(String, String, String, String, String, String, String, String)> = self
            .conn
            .query_row(
                r#"
SELECT review_id, packet_version, reviewer, decision, notes, gate_snapshot_json, blocker_snapshot_json, created_at
FROM review_artifacts
WHERE packet_id = ?1
"#,
                params![&packet_id.0],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                        row.get(5)?,
                        row.get(6)?,
                        row.get(7)?,
                    ))
                },
            )
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed to load review artifact: {e}")))?;
        match row {
            Some((
                review_id,
                packet_version,
                reviewer,
                decision,
                notes,
                gate_snapshot_json,
                blocker_snapshot_json,
                created_at,
            )) => Ok(Some(ReviewArtifact {
                review_id: ReviewId(review_id),
                packet_id: packet_id.clone(),
                packet_version,
                reviewer,
                decision: parse_decision(Some(decision))
                    .ok_or_else(|| PipelineError::Storage("invalid review decision".to_string()))?,
                notes,
                gate_snapshot: from_json(&gate_snapshot_json)?,
                blocker_snapshot: from_json(&blocker_snapshot_json)?,
                created_at: parse_ts(Some(created_at))
                    .map_err(PipelineError::Storage)?
                    .expect("created_at is required"),
            })),
            None => Ok(None),
        }
    }

    pub fn save_review_diff_state(&mut self, state: &ReviewDiffState) -> Result<(), PipelineError> {
        self.conn
            .execute(
                r#"
INSERT INTO review_diff_state (
    packet_id, diff_target_id, change_count, reviewed, reviewed_by, reviewed_at, summary_json
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
ON CONFLICT(packet_id, diff_target_id) DO UPDATE SET
    change_count = excluded.change_count,
    reviewed = excluded.reviewed,
    reviewed_by = excluded.reviewed_by,
    reviewed_at = excluded.reviewed_at,
    summary_json = excluded.summary_json
"#,
                params![
                    &state.packet_id.0,
                    &state.diff_target_id,
                    state.change_count as i64,
                    bool_to_sql(state.reviewed),
                    state.reviewed_by.as_deref(),
                    state.reviewed_at.map(|ts| ts.to_rfc3339()),
                    &state.summary,
                ],
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to save review diff state: {e}"))
            })?;
        Ok(())
    }

    pub fn load_review_diff_states(
        &self,
        packet_id: &PacketId,
    ) -> Result<Vec<ReviewDiffState>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT diff_target_id, change_count, reviewed, reviewed_by, reviewed_at, summary_json
FROM review_diff_state
WHERE packet_id = ?1
ORDER BY diff_target_id ASC
"#,
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to prepare diff state query: {e}"))
            })?;
        let rows = stmt
            .query_map(params![&packet_id.0], |row| {
                Ok(ReviewDiffState {
                    packet_id: packet_id.clone(),
                    diff_target_id: row.get(0)?,
                    change_count: row.get::<_, i64>(1)? as usize,
                    reviewed: sql_to_bool(row.get::<_, i64>(2)?),
                    reviewed_by: row.get(3)?,
                    reviewed_at: parse_ts(row.get::<_, Option<String>>(4)?)
                        .map_err(to_sqlite_parse_err)?,
                    summary: row.get(5)?,
                })
            })
            .map_err(|e| PipelineError::Storage(format!("failed to map diff state rows: {e}")))?;
        collect_rows(rows)
    }

    pub fn save_review_evidence_state(
        &mut self,
        state: &ReviewEvidenceState,
    ) -> Result<(), PipelineError> {
        self.conn
            .execute(
                r#"
INSERT INTO review_evidence_state (
    packet_id, evidence_id, target_id, reviewed, reviewed_by, reviewed_at, payload_json
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
ON CONFLICT(packet_id, evidence_id) DO UPDATE SET
    target_id = excluded.target_id,
    reviewed = excluded.reviewed,
    reviewed_by = excluded.reviewed_by,
    reviewed_at = excluded.reviewed_at,
    payload_json = excluded.payload_json
"#,
                params![
                    &state.packet_id.0,
                    &state.evidence_id,
                    &state.target_id,
                    bool_to_sql(state.reviewed),
                    state.reviewed_by.as_deref(),
                    state.reviewed_at.map(|ts| ts.to_rfc3339()),
                    &state.payload_json,
                ],
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to save review evidence state: {e}"))
            })?;
        Ok(())
    }

    pub fn load_review_evidence_states(
        &self,
        packet_id: &PacketId,
    ) -> Result<Vec<ReviewEvidenceState>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT evidence_id, target_id, reviewed, reviewed_by, reviewed_at, payload_json
FROM review_evidence_state
WHERE packet_id = ?1
ORDER BY evidence_id ASC
"#,
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to prepare evidence state query: {e}"))
            })?;
        let rows = stmt
            .query_map(params![&packet_id.0], |row| {
                Ok(ReviewEvidenceState {
                    packet_id: packet_id.clone(),
                    evidence_id: row.get(0)?,
                    target_id: row.get(1)?,
                    reviewed: sql_to_bool(row.get::<_, i64>(2)?),
                    reviewed_by: row.get(3)?,
                    reviewed_at: parse_ts(row.get::<_, Option<String>>(4)?)
                        .map_err(to_sqlite_parse_err)?,
                    payload_json: row.get(5)?,
                })
            })
            .map_err(|e| {
                PipelineError::Storage(format!("failed to map evidence state rows: {e}"))
            })?;
        collect_rows(rows)
    }

    pub fn save_review_gate_state(&mut self, state: &ReviewGateState) -> Result<(), PipelineError> {
        self.conn
            .execute(
                r#"
INSERT INTO review_gate_state (
    packet_id, packet_version, required_fields_loaded, validation_status, blocker_count,
    diff_reviewed, evidence_reviewed, stale_flag, dirty_flag, active_diff_target_id,
    active_evidence_id, active_validation_issue_id, approve_enabled, reject_enabled,
    rework_enabled, updated_at
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)
ON CONFLICT(packet_id) DO UPDATE SET
    packet_version = excluded.packet_version,
    required_fields_loaded = excluded.required_fields_loaded,
    validation_status = excluded.validation_status,
    blocker_count = excluded.blocker_count,
    diff_reviewed = excluded.diff_reviewed,
    evidence_reviewed = excluded.evidence_reviewed,
    stale_flag = excluded.stale_flag,
    dirty_flag = excluded.dirty_flag,
    active_diff_target_id = excluded.active_diff_target_id,
    active_evidence_id = excluded.active_evidence_id,
    active_validation_issue_id = excluded.active_validation_issue_id,
    approve_enabled = excluded.approve_enabled,
    reject_enabled = excluded.reject_enabled,
    rework_enabled = excluded.rework_enabled,
    updated_at = excluded.updated_at
"#,
                params![
                    &state.packet_id.0,
                    &state.packet_version,
                    bool_to_sql(state.required_fields_loaded),
                    &state.validation_status,
                    state.blocker_count as i64,
                    bool_to_sql(state.diff_reviewed),
                    bool_to_sql(state.evidence_reviewed),
                    bool_to_sql(state.stale_flag),
                    bool_to_sql(state.dirty_flag),
                    state.active_diff_target_id.as_deref(),
                    state.active_evidence_id.as_deref(),
                    state.active_validation_issue_id.as_deref(),
                    bool_to_sql(state.approve_enabled),
                    bool_to_sql(state.reject_enabled),
                    bool_to_sql(state.rework_enabled),
                    state.updated_at.to_rfc3339(),
                ],
            )
            .map_err(|e| {
                PipelineError::Storage(format!("failed to save review gate state: {e}"))
            })?;
        Ok(())
    }

    pub fn load_review_gate_state(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<ReviewGateState>, PipelineError> {
        self.conn
            .query_row(
                r#"
SELECT packet_version, required_fields_loaded, validation_status, blocker_count,
       diff_reviewed, evidence_reviewed, stale_flag, dirty_flag, active_diff_target_id,
       active_evidence_id, active_validation_issue_id, approve_enabled, reject_enabled,
       rework_enabled, updated_at
FROM review_gate_state
WHERE packet_id = ?1
"#,
                params![&packet_id.0],
                |row| {
                    Ok(ReviewGateState {
                        packet_id: packet_id.clone(),
                        packet_version: row.get(0)?,
                        required_fields_loaded: sql_to_bool(row.get::<_, i64>(1)?),
                        validation_status: row.get(2)?,
                        blocker_count: row.get::<_, i64>(3)? as usize,
                        diff_reviewed: sql_to_bool(row.get::<_, i64>(4)?),
                        evidence_reviewed: sql_to_bool(row.get::<_, i64>(5)?),
                        stale_flag: sql_to_bool(row.get::<_, i64>(6)?),
                        dirty_flag: sql_to_bool(row.get::<_, i64>(7)?),
                        active_diff_target_id: row.get(8)?,
                        active_evidence_id: row.get(9)?,
                        active_validation_issue_id: row.get(10)?,
                        approve_enabled: sql_to_bool(row.get::<_, i64>(11)?),
                        reject_enabled: sql_to_bool(row.get::<_, i64>(12)?),
                        rework_enabled: sql_to_bool(row.get::<_, i64>(13)?),
                        updated_at: parse_ts(Some(row.get::<_, String>(14)?))
                            .map_err(to_sqlite_parse_err)?
                            .expect("updated_at is required"),
                    })
                },
            )
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed to load review gate state: {e}")))
    }

    pub fn save_approved(&mut self, approved: &ApprovedPacket) -> Result<(), PipelineError> {
        let packet_id = &approved.review_ready.packet.packet_id.0;
        let document_id = &approved.review_ready.packet.document_id.0;
        self.save_packet_stage(
            "approved_packets",
            packet_id,
            document_id,
            approved,
            "review",
            "approved_saved",
        )
    }

    pub fn load_approved(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<ApprovedPacket>, PipelineError> {
        self.load_packet_stage("approved_packets", &packet_id.0)
    }

    pub fn save_wiki_node(
        &mut self,
        approved_packet_id: &PacketId,
        wiki_node: &WikiNode,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        let payload = to_json(wiki_node)?;
        self.conn
            .execute(
                r#"
INSERT INTO wiki_nodes (wiki_node_id, approved_packet_id, payload_json, created_at, updated_at)
VALUES (?1, ?2, ?3, ?4, ?4)
ON CONFLICT(wiki_node_id) DO UPDATE SET
    approved_packet_id = excluded.approved_packet_id,
    payload_json = excluded.payload_json,
    updated_at = excluded.updated_at
"#,
                params![&wiki_node.wiki_node_id, &approved_packet_id.0, payload, now],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to save wiki node: {e}")))?;
        self.write_event(
            &approved_packet_id.0,
            "packet",
            "promote",
            "wiki_node_saved",
            Some(format!(
                r#"{{"wiki_node_id":"{}"}}"#,
                wiki_node.wiki_node_id
            )),
        )?;
        Ok(())
    }

    pub fn load_wiki_node_by_packet(
        &self,
        packet_id: &PacketId,
    ) -> Result<Option<WikiNode>, PipelineError> {
        let payload: Option<String> = self
            .conn
            .query_row(
                "SELECT payload_json FROM wiki_nodes WHERE approved_packet_id = ?1",
                params![&packet_id.0],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed to load wiki node: {e}")))?;
        match payload {
            Some(json) => Ok(Some(from_json(&json)?)),
            None => Ok(None),
        }
    }

    pub fn load_audit_events(&self, aggregate_id: &str) -> Result<Vec<AuditEvent>, PipelineError> {
        let mut stmt = self
            .conn
            .prepare(
                r#"
SELECT event_id, aggregate_id, aggregate_type, stage, action, payload_json, created_at
FROM audit_events
WHERE aggregate_id = ?1
ORDER BY event_id ASC
"#,
            )
            .map_err(|e| PipelineError::Storage(format!("failed to prepare audit query: {e}")))?;
        let rows = stmt
            .query_map(params![aggregate_id], |row| {
                let created_at_raw: String = row.get(6)?;
                let created_at = DateTime::parse_from_rfc3339(&created_at_raw)
                    .map_err(|e| {
                        to_sqlite_parse_err(format!("bad timestamp {created_at_raw}: {e}"))
                    })?
                    .with_timezone(&Utc);
                Ok(AuditEvent {
                    event_id: row.get(0)?,
                    aggregate_id: row.get(1)?,
                    aggregate_type: row.get(2)?,
                    stage: row.get(3)?,
                    action: row.get(4)?,
                    payload_json: row.get(5)?,
                    created_at,
                })
            })
            .map_err(|e| PipelineError::Storage(format!("failed to map audit rows: {e}")))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| PipelineError::Storage(format!("bad audit row: {e}")))?);
        }
        Ok(out)
    }

    pub fn record_audit_event(
        &mut self,
        aggregate_id: &str,
        aggregate_type: &str,
        stage: &str,
        action: &str,
        payload_json: Option<String>,
    ) -> Result<(), PipelineError> {
        self.write_event(aggregate_id, aggregate_type, stage, action, payload_json)
    }

    pub fn load_replay_bundle(&self, packet_id: &PacketId) -> Result<ReplayBundle, PipelineError> {
        let packet = self.load_packet(packet_id)?;
        let document_id = packet.as_ref().map(|p| p.document_id.clone());
        let raw = match &document_id {
            Some(id) => self.load_raw(id)?,
            None => None,
        };
        let parsed = match &document_id {
            Some(id) => self.load_parsed(id)?,
            None => None,
        };
        let markers = match &document_id {
            Some(id) => self.load_markers(id)?,
            None => None,
        };
        let cloud_task_runs = self.load_cloud_task_runs(packet_id)?;
        let cloud_command_traces = self.load_cloud_command_traces(packet_id)?;
        let result = self.load_result(packet_id)?;
        let validation = self.load_validation(packet_id)?;
        let lineage = self.load_packet_lineage(packet_id)?;
        let review_queue_item = self.load_review_item(packet_id)?;
        let review_artifact = self.load_review_artifact(packet_id)?;
        let gate_state = self.load_review_gate_state(packet_id)?;
        let diff_states = self.load_review_diff_states(packet_id)?;
        let evidence_states = self.load_review_evidence_states(packet_id)?;
        let approved = self.load_approved(packet_id)?;
        let wiki_node = self.load_wiki_node_by_packet(packet_id)?;
        let mut audit_events = self.load_audit_events(&packet_id.0)?;
        if let Some(id) = &document_id {
            audit_events.extend(self.load_audit_events(&id.0)?);
        }
        audit_events.sort_by_key(|e| e.event_id);
        Ok(ReplayBundle {
            raw,
            parsed,
            markers,
            packet,
            cloud_task_runs,
            cloud_command_traces,
            result,
            validation,
            lineage,
            review_queue_item,
            review_artifact,
            gate_state,
            diff_states,
            evidence_states,
            approved,
            wiki_node,
            audit_events,
        })
    }

    fn save_document_stage<T: Serialize>(
        &mut self,
        table: &str,
        document_id: &str,
        payload: &T,
        stage: &str,
        action: &str,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        let payload_json = to_json(payload)?;
        let sql = format!(
            r#"
INSERT INTO {table} (document_id, payload_json, created_at, updated_at)
VALUES (?1, ?2, ?3, ?3)
ON CONFLICT(document_id) DO UPDATE SET
    payload_json = excluded.payload_json,
    updated_at = excluded.updated_at
"#
        );
        self.conn
            .execute(&sql, params![document_id, payload_json, now])
            .map_err(|e| PipelineError::Storage(format!("failed saving to {table}: {e}")))?;
        self.write_event(document_id, "document", stage, action, None)?;
        Ok(())
    }

    fn load_document_stage<T: DeserializeOwned>(
        &self,
        table: &str,
        document_id: &str,
    ) -> Result<Option<T>, PipelineError> {
        let sql = format!("SELECT payload_json FROM {table} WHERE document_id = ?1");
        let payload: Option<String> = self
            .conn
            .query_row(&sql, params![document_id], |row| row.get(0))
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed loading from {table}: {e}")))?;
        match payload {
            Some(json) => Ok(Some(from_json(&json)?)),
            None => Ok(None),
        }
    }

    fn save_packet_stage<T: Serialize>(
        &mut self,
        table: &str,
        packet_id: &str,
        document_id: &str,
        payload: &T,
        stage: &str,
        action: &str,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        let payload_json = to_json(payload)?;
        let sql = format!(
            r#"
INSERT INTO {table} (packet_id, document_id, payload_json, created_at, updated_at)
VALUES (?1, ?2, ?3, ?4, ?4)
ON CONFLICT(packet_id) DO UPDATE SET
    document_id = excluded.document_id,
    payload_json = excluded.payload_json,
    updated_at = excluded.updated_at
"#
        );
        self.conn
            .execute(&sql, params![packet_id, document_id, payload_json, now])
            .map_err(|e| PipelineError::Storage(format!("failed saving to {table}: {e}")))?;
        self.write_event(
            packet_id,
            "packet",
            stage,
            action,
            Some(format!(r#"{{"document_id":"{}"}}"#, document_id)),
        )?;
        Ok(())
    }

    fn load_packet_stage<T: DeserializeOwned>(
        &self,
        table: &str,
        packet_id: &str,
    ) -> Result<Option<T>, PipelineError> {
        let sql = format!("SELECT payload_json FROM {table} WHERE packet_id = ?1");
        let payload: Option<String> = self
            .conn
            .query_row(&sql, params![packet_id], |row| row.get(0))
            .optional()
            .map_err(|e| PipelineError::Storage(format!("failed loading from {table}: {e}")))?;
        match payload {
            Some(json) => Ok(Some(from_json(&json)?)),
            None => Ok(None),
        }
    }

    fn write_event(
        &mut self,
        aggregate_id: &str,
        aggregate_type: &str,
        stage: &str,
        action: &str,
        payload_json: Option<String>,
    ) -> Result<(), PipelineError> {
        let now = Utc::now().to_rfc3339();
        self.conn
            .execute(
                r#"
INSERT INTO audit_events (aggregate_id, aggregate_type, stage, action, payload_json, created_at)
VALUES (?1, ?2, ?3, ?4, ?5, ?6)
"#,
                params![
                    aggregate_id,
                    aggregate_type,
                    stage,
                    action,
                    payload_json,
                    now
                ],
            )
            .map_err(|e| PipelineError::Storage(format!("failed to write audit event: {e}")))?;
        Ok(())
    }
}

fn to_json<T: Serialize>(value: &T) -> Result<String, PipelineError> {
    serde_json::to_string_pretty(value)
        .map_err(|e| PipelineError::Serde(format!("serialize failed: {e}")))
}

fn from_json<T: DeserializeOwned>(json: &str) -> Result<T, PipelineError> {
    serde_json::from_str(json).map_err(|e| PipelineError::Serde(format!("deserialize failed: {e}")))
}

fn parse_ts(value: Option<String>) -> Result<Option<DateTime<Utc>>, String> {
    match value {
        Some(v) => {
            let dt = DateTime::parse_from_rfc3339(&v)
                .map_err(|e| format!("bad timestamp {v}: {e}"))?
                .with_timezone(&Utc);
            Ok(Some(dt))
        }
        None => Ok(None),
    }
}

fn parse_decision(value: Option<String>) -> Option<ReviewDecision> {
    match value.as_deref() {
        Some("approve") => Some(ReviewDecision::Approve),
        Some("reject") => Some(ReviewDecision::Reject),
        Some("rework") => Some(ReviewDecision::Rework),
        _ => None,
    }
}

fn decision_to_db(value: &ReviewDecision) -> &'static str {
    match value {
        ReviewDecision::Approve => "approve",
        ReviewDecision::Reject => "reject",
        ReviewDecision::Rework => "rework",
    }
}

fn bool_to_sql(value: bool) -> i64 {
    if value {
        1
    } else {
        0
    }
}

fn sql_to_bool(value: i64) -> bool {
    value != 0
}

fn collect_rows<T>(
    rows: impl Iterator<Item = rusqlite::Result<T>>,
) -> Result<Vec<T>, PipelineError> {
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| PipelineError::Storage(format!("bad row: {e}")))?);
    }
    Ok(out)
}

fn to_sqlite_parse_err(message: String) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        0,
        rusqlite::types::Type::Text,
        Box::<dyn std::error::Error + Send + Sync>::from(message),
    )
}

fn to_sqlite_pipeline_err(error: PipelineError) -> rusqlite::Error {
    to_sqlite_parse_err(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cloud::{CloudSummarizer, MockCloudSummarizer};
    use crate::metadata::{promote_to_wiki, MetadataEngine, RuleBasedMetadata};
    use crate::model::{CloudCommandKind, MetadataRecord, RawDocument, SourceKind};
    use crate::packetizer::{PacketBuilder, PacketBuilderConfig};
    use crate::parser::Parser;
    use crate::preflight::{LocalMarkerModel, RuleBasedPreflight};
    use crate::review::Reviewer;
    use crate::validation::Validator;

    #[test]
    fn storage_round_trips_review_queue_and_replay() {
        let mut db = Database::open(":memory:").unwrap();
        let raw = RawDocument::new(
            "doc",
            SourceKind::Document,
            None,
            "Dense content. ".repeat(100),
        );
        db.save_raw(&raw).unwrap();
        let parsed = Parser::parse(raw.clone()).unwrap();
        db.save_parsed(&parsed).unwrap();
        let markers = RuleBasedPreflight.scan(&parsed);
        db.save_markers(&parsed.raw.document_id, &markers).unwrap();
        let packet = PacketBuilder::build(
            &parsed,
            &markers,
            &PacketBuilderConfig {
                context_radius: 1,
                max_work_units: 4,
            },
        )
        .unwrap();
        db.save_packet(&packet).unwrap();
        let execution = MockCloudSummarizer.execute(&packet, 1);
        db.save_cloud_task_run(&execution.run).unwrap();
        db.save_cloud_command_trace(
            &packet.packet_id,
            &crate::model::CloudCommandTrace {
                trace_id: "trace_mock_exec".to_string(),
                cloud_run_id: execution.run.cloud_run_id.clone(),
                attempt_index: 1,
                command_kind: CloudCommandKind::Exec,
                command_text: "mock exec".to_string(),
                started_at: Utc::now(),
                finished_at: Utc::now(),
                exit_status: Some(0),
                stdout_summary: Some("mock stdout".to_string()),
                stderr_summary: None,
            },
        )
        .unwrap();
        let result = execution.result.unwrap();
        db.save_result(&packet.document_id, &result).unwrap();
        let validation = Validator::validate(&packet, &result, &markers);
        db.save_validation(&packet.document_id, &validation)
            .unwrap();
        db.enqueue_review(&packet.packet_id, &packet.document_id)
            .unwrap();
        let claimed = db.claim_next_review("ace").unwrap().unwrap();
        assert_eq!(claimed.status, QueueStatus::InReview);
        let lineage = db.load_packet_lineage(&packet.packet_id).unwrap().unwrap();
        assert_eq!(lineage.lineage_root_packet_id, packet.packet_id);

        let diff_state = ReviewDiffState {
            packet_id: packet.packet_id.clone(),
            diff_target_id: "target_001".to_string(),
            change_count: 2,
            reviewed: true,
            reviewed_by: Some("ace".to_string()),
            reviewed_at: Some(Utc::now()),
            summary: "diff summary".to_string(),
        };
        db.save_review_diff_state(&diff_state).unwrap();
        let evidence_state = ReviewEvidenceState {
            packet_id: packet.packet_id.clone(),
            evidence_id: "evidence_001".to_string(),
            target_id: "target_001".to_string(),
            reviewed: true,
            reviewed_by: Some("ace".to_string()),
            reviewed_at: Some(Utc::now()),
            payload_json: r#"{"node_id":"node_1"}"#.to_string(),
        };
        db.save_review_evidence_state(&evidence_state).unwrap();
        let gate_state = ReviewGateState {
            packet_id: packet.packet_id.clone(),
            packet_version: format!("version_for_{}", packet.packet_id.0),
            required_fields_loaded: true,
            validation_status: "pass".to_string(),
            blocker_count: 0,
            diff_reviewed: true,
            evidence_reviewed: true,
            stale_flag: false,
            dirty_flag: false,
            active_diff_target_id: Some("target_001".to_string()),
            active_evidence_id: Some("evidence_001".to_string()),
            active_validation_issue_id: None,
            approve_enabled: true,
            reject_enabled: true,
            rework_enabled: true,
            updated_at: Utc::now(),
        };
        db.save_review_gate_state(&gate_state).unwrap();

        let review_ready = Reviewer::make_review_ready(packet.clone(), result, validation).unwrap();
        let approved = Reviewer::approve(review_ready, "ace", "ok");
        db.save_approved(&approved).unwrap();
        db.save_review_artifact(&ReviewArtifact {
            review_id: approved.stamp.review_id.clone(),
            packet_id: packet.packet_id.clone(),
            packet_version: format!("version_for_{}", packet.packet_id.0),
            reviewer: "ace".to_string(),
            decision: ReviewDecision::Approve,
            notes: "ok".to_string(),
            gate_snapshot: gate_state.clone(),
            blocker_snapshot: vec![],
            created_at: approved.stamp.reviewed_at,
        })
        .unwrap();
        db.complete_review(
            &packet.packet_id,
            QueueStatus::Approved,
            Some(ReviewDecision::Approve),
            Some("ok"),
        )
        .unwrap();
        let wiki_node = promote_to_wiki(
            &approved,
            MetadataRecord {
                subjects: vec!["uncategorized".to_string()],
                tags: vec![],
                related_subjects: vec![],
            },
        );
        db.save_wiki_node(&packet.packet_id, &wiki_node).unwrap();

        let replay = db.load_replay_bundle(&packet.packet_id).unwrap();
        assert!(replay.raw.is_some());
        assert!(replay.parsed.is_some());
        assert!(replay.packet.is_some());
        assert_eq!(replay.cloud_task_runs.len(), 1);
        assert_eq!(replay.cloud_command_traces.len(), 1);
        assert_eq!(
            replay.cloud_task_runs[0].handoff_mode,
            "mock_inline_packet_visible_output_v3"
        );
        assert!(replay.lineage.is_some());
        assert!(replay.review_queue_item.is_some());
        assert!(replay.review_artifact.is_some());
        assert!(replay.gate_state.is_some());
        assert_eq!(replay.diff_states.len(), 1);
        assert_eq!(replay.evidence_states.len(), 1);
        assert!(replay.approved.is_some());
        assert!(replay.wiki_node.is_some());
        assert!(!replay.audit_events.is_empty());
        let metadata = RuleBasedMetadata;
        let enriched = metadata.enrich(replay.approved.as_ref().unwrap());
        assert!(!enriched.subjects.is_empty());
    }

    #[test]
    fn storage_links_successor_packets_in_lineage() {
        let mut db = Database::open(":memory:").unwrap();
        let prior = PacketId("pkt_prior".to_string());
        let successor = PacketId("pkt_successor".to_string());

        db.ensure_packet_lineage(&prior).unwrap();
        db.ensure_packet_lineage(&successor).unwrap();
        db.link_packet_successor(&prior, &successor, Some(&ReviewId("rev_link".to_string())))
            .unwrap();

        let prior_lineage = db.load_packet_lineage(&prior).unwrap().unwrap();
        let successor_lineage = db.load_packet_lineage(&successor).unwrap().unwrap();
        assert_eq!(prior_lineage.successor_packet_id, Some(successor.clone()));
        assert_eq!(successor_lineage.prior_packet_id, Some(prior.clone()));
        assert_eq!(successor_lineage.lineage_root_packet_id, prior);
        assert_eq!(
            successor_lineage.spawned_by_review_id,
            Some(ReviewId("rev_link".to_string()))
        );
    }
}
