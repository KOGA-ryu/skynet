use crate::cloud::CloudSummarizer;
use crate::error::PipelineError;
use crate::metadata::{promote_to_wiki, MetadataEngine};
use crate::model::{
    ApprovedPacket, PacketId, QueueStatus, RawDocument, ReplayBundle, ReviewArtifact,
    ReviewDecision, ReviewDiffState, ReviewEvidenceState, ReviewGateState, ReviewQueueItem,
    ValidationReport, WikiNode,
};
use crate::packetizer::{PacketBuilder, PacketBuilderConfig};
use crate::parser::Parser;
use crate::preflight::LocalMarkerModel;
use crate::review::{
    blocking_issues, compute_review_gate_state, mark_all_diff_states_reviewed,
    mark_all_evidence_states_reviewed, packet_version, Reviewer,
};
use crate::storage::Database;
use crate::validation::Validator;

pub const REVIEW_NOTES_MIN_CHARS: usize = 24;

pub struct Cleanroom<L, C, M>
where
    L: LocalMarkerModel,
    C: CloudSummarizer,
    M: MetadataEngine,
{
    pub db: Database,
    pub local: L,
    pub cloud: C,
    pub metadata: M,
}

impl<L, C, M> Cleanroom<L, C, M>
where
    L: LocalMarkerModel,
    C: CloudSummarizer,
    M: MetadataEngine,
{
    pub fn open(db_path: &str, local: L, cloud: C, metadata: M) -> Result<Self, PipelineError> {
        Ok(Self {
            db: Database::open(db_path)?,
            local,
            cloud,
            metadata,
        })
    }

    pub fn ingest_and_stage(&mut self, raw: RawDocument) -> Result<PacketId, PipelineError> {
        self.db.save_raw(&raw)?;
        let parsed = Parser::parse(raw)?;
        self.db.save_parsed(&parsed)?;
        let markers = self.local.scan(&parsed);
        self.db.save_markers(&parsed.raw.document_id, &markers)?;
        let packet = PacketBuilder::build(
            &parsed,
            &markers,
            &PacketBuilderConfig {
                context_radius: 1,
                max_work_units: 64,
            },
        )?;
        self.db.save_packet(&packet)?;
        Ok(packet.packet_id.clone())
    }

    pub fn run_cloud(&mut self, packet_id: &PacketId) -> Result<(), PipelineError> {
        let packet = self
            .db
            .load_packet(packet_id)?
            .ok_or_else(|| PipelineError::NotFound(format!("packet {} not found", packet_id.0)))?;
        let markers = self.db.load_markers(&packet.document_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!(
                "markers for document {} not found",
                packet.document_id.0
            ))
        })?;
        let attempt_index = self.db.next_cloud_attempt_index(&packet.packet_id)?;
        let execution = self.cloud.execute(&packet, attempt_index);
        self.db.save_cloud_task_run(&execution.run)?;
        for trace in &execution.command_traces {
            self.db.save_cloud_command_trace(&packet.packet_id, trace)?;
        }
        let result = match execution.result {
            Some(result) => result,
            None => {
                self.db.record_audit_event(
                    &packet.packet_id.0,
                    "packet",
                    "cloud",
                    "cloud_failed",
                    Some(
                        serde_json::json!({
                            "cloud_run_id": execution.run.cloud_run_id,
                            "attempt_index": execution.run.attempt_index,
                            "final_status": execution.run.final_status,
                            "error_text": execution.run.error_text,
                        })
                        .to_string(),
                    ),
                )?;
                return Err(PipelineError::Storage(
                    execution
                        .run
                        .error_text
                        .unwrap_or_else(|| "cloud execution failed".to_string()),
                ));
            }
        };
        let validation = Validator::validate(&packet, &result, &markers);
        self.db.save_result(&packet.document_id, &result)?;
        self.db.save_validation(&packet.document_id, &validation)?;
        if validation.passed {
            self.db
                .enqueue_review(&packet.packet_id, &packet.document_id)?;
            for diff_state in build_initial_diff_states(&packet, &result) {
                self.db.save_review_diff_state(&diff_state)?;
            }
            for evidence_state in build_initial_evidence_states(&packet, &result)? {
                self.db.save_review_evidence_state(&evidence_state)?;
            }
            let gate_state = compute_review_gate_state(
                &packet.packet_id,
                &packet_version(&packet.packet_id),
                &validation,
                &self.db.load_review_diff_states(&packet.packet_id)?,
                &self.db.load_review_evidence_states(&packet.packet_id)?,
                false,
                false,
            );
            self.db.save_review_gate_state(&gate_state)?;
            Ok(())
        } else {
            Err(PipelineError::Validation(format!(
                "validation failed for packet {}",
                packet.packet_id.0
            )))
        }
    }

    pub fn claim_next_review(
        &mut self,
        reviewer: &str,
    ) -> Result<Option<ReviewQueueItem>, PipelineError> {
        self.db.claim_next_review(reviewer)
    }

    pub fn claim_review_packet(
        &mut self,
        packet_id: &PacketId,
        reviewer: &str,
    ) -> Result<ReviewQueueItem, PipelineError> {
        self.db.claim_review_packet(packet_id, reviewer)
    }

    pub fn approve_packet(
        &mut self,
        packet_id: &PacketId,
        notes: Option<&str>,
    ) -> Result<ApprovedPacket, PipelineError> {
        let packet = self
            .db
            .load_packet(packet_id)?
            .ok_or_else(|| PipelineError::NotFound(format!("packet {} not found", packet_id.0)))?;
        let result = self
            .db
            .load_result(packet_id)?
            .ok_or_else(|| PipelineError::NotFound(format!("result {} not found", packet_id.0)))?;
        let validation = self.db.load_validation(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("validation {} not found", packet_id.0))
        })?;
        let review_item = self.db.load_review_item(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review item {} not found", packet_id.0))
        })?;
        let reviewer = assigned_reviewer_for_review(&review_item, packet_id)?;
        let notes = notes.unwrap_or("").trim();
        let mut diff_states = self.db.load_review_diff_states(packet_id)?;
        let mut evidence_states = self.db.load_review_evidence_states(packet_id)?;
        let reviewed_at = chrono::Utc::now();
        mark_all_diff_states_reviewed(&mut diff_states, &reviewer, reviewed_at);
        mark_all_evidence_states_reviewed(&mut evidence_states, &reviewer, reviewed_at);
        for diff_state in &diff_states {
            self.db.save_review_diff_state(diff_state)?;
        }
        for evidence_state in &evidence_states {
            self.db.save_review_evidence_state(evidence_state)?;
        }
        let gate_state = compute_review_gate_state(
            &packet.packet_id,
            &packet_version(&packet.packet_id),
            &validation,
            &diff_states,
            &evidence_states,
            false,
            false,
        );
        self.db.save_review_gate_state(&gate_state)?;
        let review_ready = Reviewer::make_review_ready(packet, result, validation)?;
        let approved = Reviewer::approve(review_ready, reviewer.clone(), notes);
        self.db.save_approved(&approved)?;
        let review_artifact = ReviewArtifact {
            review_id: approved.stamp.review_id.clone(),
            packet_id: packet_id.clone(),
            packet_version: packet_version(packet_id),
            reviewer: reviewer.clone(),
            decision: ReviewDecision::Approve,
            notes: notes.to_string(),
            gate_snapshot: gate_state,
            blocker_snapshot: vec![],
            created_at: approved.stamp.reviewed_at,
        };
        self.db.save_review_artifact(&review_artifact)?;
        self.db.complete_review(
            packet_id,
            QueueStatus::Approved,
            Some(ReviewDecision::Approve),
            if notes.is_empty() { None } else { Some(notes) },
        )?;
        Ok(approved)
    }

    pub fn reject_packet(
        &mut self,
        packet_id: &PacketId,
        notes: &str,
    ) -> Result<(), PipelineError> {
        let review_item = self.db.load_review_item(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review item {} not found", packet_id.0))
        })?;
        let reviewer = assigned_reviewer_for_review(&review_item, packet_id)?;
        ensure_review_notes(notes)?;
        let validation = self.db.load_validation(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("validation {} not found", packet_id.0))
        })?;
        let gate_state = resolve_gate_snapshot(&mut self.db, packet_id, &validation)?;
        let stamp = Reviewer::stamp(packet_id, reviewer.clone(), ReviewDecision::Reject, notes);
        self.db.save_review_artifact(&ReviewArtifact {
            review_id: stamp.review_id.clone(),
            packet_id: packet_id.clone(),
            packet_version: packet_version(packet_id),
            reviewer,
            decision: ReviewDecision::Reject,
            notes: notes.to_string(),
            gate_snapshot: gate_state,
            blocker_snapshot: blocking_issues(&validation),
            created_at: stamp.reviewed_at,
        })?;
        self.db.complete_review(
            packet_id,
            QueueStatus::Rejected,
            Some(ReviewDecision::Reject),
            Some(notes),
        )
    }

    pub fn request_rework(
        &mut self,
        packet_id: &PacketId,
        notes: &str,
    ) -> Result<(), PipelineError> {
        let review_item = self.db.load_review_item(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review item {} not found", packet_id.0))
        })?;
        let reviewer = assigned_reviewer_for_review(&review_item, packet_id)?;
        ensure_review_notes(notes)?;
        let validation = self.db.load_validation(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("validation {} not found", packet_id.0))
        })?;
        let gate_state = resolve_gate_snapshot(&mut self.db, packet_id, &validation)?;
        let stamp = Reviewer::stamp(packet_id, reviewer.clone(), ReviewDecision::Rework, notes);
        self.db.save_review_artifact(&ReviewArtifact {
            review_id: stamp.review_id.clone(),
            packet_id: packet_id.clone(),
            packet_version: packet_version(packet_id),
            reviewer,
            decision: ReviewDecision::Rework,
            notes: notes.to_string(),
            gate_snapshot: gate_state,
            blocker_snapshot: blocking_issues(&validation),
            created_at: stamp.reviewed_at,
        })?;
        self.db.complete_review(
            packet_id,
            QueueStatus::ReworkRequested,
            Some(ReviewDecision::Rework),
            Some(notes),
        )
    }

    pub fn promote_approved(&mut self, packet_id: &PacketId) -> Result<WikiNode, PipelineError> {
        let approved = self.db.load_approved(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("approved packet {} not found", packet_id.0))
        })?;
        let metadata = self.metadata.enrich(&approved);
        let wiki_node = promote_to_wiki(&approved, metadata);
        self.db.save_wiki_node(packet_id, &wiki_node)?;
        Ok(wiki_node)
    }

    pub fn replay_packet(&self, packet_id: &PacketId) -> Result<ReplayBundle, PipelineError> {
        self.db.load_replay_bundle(packet_id)
    }
}

pub fn review_notes_min_chars() -> usize {
    REVIEW_NOTES_MIN_CHARS
}

fn assigned_reviewer_for_review(
    review_item: &ReviewQueueItem,
    packet_id: &PacketId,
) -> Result<String, PipelineError> {
    if review_item.status != QueueStatus::InReview {
        return Err(PipelineError::Review(format!(
            "packet {} is not reviewable from status {}",
            packet_id.0,
            review_item.status.as_str()
        )));
    }
    review_item.assigned_reviewer.clone().ok_or_else(|| {
        PipelineError::Review(format!(
            "packet {} has no assigned reviewer in review state",
            packet_id.0
        ))
    })
}

fn ensure_review_notes(notes: &str) -> Result<(), PipelineError> {
    if notes.trim().chars().count() < REVIEW_NOTES_MIN_CHARS {
        return Err(PipelineError::Review(format!(
            "review notes must be at least {} characters for reject/rework",
            REVIEW_NOTES_MIN_CHARS
        )));
    }
    Ok(())
}

fn build_initial_diff_states(
    packet: &crate::model::CloudTaskPacket,
    result: &crate::model::CloudSummaryResult,
) -> Vec<ReviewDiffState> {
    result
        .fragments
        .iter()
        .map(|fragment| ReviewDiffState {
            packet_id: packet.packet_id.clone(),
            diff_target_id: fragment.target_node_id.0.clone(),
            change_count: 1,
            reviewed: false,
            reviewed_by: None,
            reviewed_at: None,
            summary: fragment.summary_title.clone(),
        })
        .collect()
}

fn build_initial_evidence_states(
    packet: &crate::model::CloudTaskPacket,
    result: &crate::model::CloudSummaryResult,
) -> Result<Vec<ReviewEvidenceState>, PipelineError> {
    let mut evidence_states = Vec::new();
    for fragment in &result.fragments {
        for evidence in &fragment.evidence {
            let payload_json = serde_json::to_string(evidence).map_err(|e| {
                PipelineError::Serde(format!("serialize evidence state failed: {e}"))
            })?;
            evidence_states.push(ReviewEvidenceState {
                packet_id: packet.packet_id.clone(),
                evidence_id: format!(
                    "evidence_{}_{}",
                    fragment.target_node_id.0, evidence.node_id.0
                ),
                target_id: fragment.target_node_id.0.clone(),
                reviewed: false,
                reviewed_by: None,
                reviewed_at: None,
                payload_json,
            });
        }
    }
    Ok(evidence_states)
}

fn resolve_gate_snapshot(
    db: &mut Database,
    packet_id: &PacketId,
    validation: &ValidationReport,
) -> Result<ReviewGateState, PipelineError> {
    let packet = db
        .load_packet(packet_id)?
        .ok_or_else(|| PipelineError::NotFound(format!("packet {} not found", packet_id.0)))?;
    let diff_states = db.load_review_diff_states(packet_id)?;
    let evidence_states = db.load_review_evidence_states(packet_id)?;
    let prior = db.load_review_gate_state(packet_id)?;
    let gate_state = compute_review_gate_state(
        &packet.packet_id,
        &packet_version(&packet.packet_id),
        validation,
        &diff_states,
        &evidence_states,
        prior
            .as_ref()
            .map(|state| state.stale_flag)
            .unwrap_or(false),
        prior
            .as_ref()
            .map(|state| state.dirty_flag)
            .unwrap_or(false),
    );
    db.save_review_gate_state(&gate_state)?;
    Ok(gate_state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cloud::{CloudExecution, CloudSummarizer, MockCloudSummarizer};
    use crate::metadata::RuleBasedMetadata;
    use crate::model::{CloudCommandKind, CloudCommandTrace, CloudTaskRun, SourceKind};
    use crate::preflight::RuleBasedPreflight;

    struct FailingCloudSummarizer;

    impl CloudSummarizer for FailingCloudSummarizer {
        fn execute(
            &self,
            packet: &crate::model::CloudTaskPacket,
            attempt_index: u32,
        ) -> CloudExecution {
            let now = chrono::Utc::now();
            CloudExecution {
                run: CloudTaskRun {
                    cloud_run_id: format!(
                        "cloudrun_{}_attempt_{attempt_index:03}",
                        packet.packet_id.0
                    ),
                    packet_id: packet.packet_id.clone(),
                    attempt_index,
                    task_id: None,
                    task_url: None,
                    environment_id: Some("test".to_string()),
                    matched_remote_identity: None,
                    current_head_sha: None,
                    current_branch: None,
                    head_contained_in_allowed_remote_ref: None,
                    resolution_method: "unresolved".to_string(),
                    handoff_mode: "mock_inline_packet_visible_output_v3".to_string(),
                    packet_path: format!(
                        ".cleanroom/packets/{}.attempt-{attempt_index:03}.packet.json",
                        packet.packet_id.0
                    ),
                    schema_path: format!(
                        ".cleanroom/schema/{}.attempt-{attempt_index:03}.summary.schema.json",
                        packet.packet_id.0
                    ),
                    output_path: format!(
                        "codex_apply_out/{}.attempt-{attempt_index:03}.summary.json",
                        packet.packet_id.0
                    ),
                    allowed_apply_paths: vec![],
                    new_apply_paths: vec![],
                    submitted_at: now,
                    finished_at: Some(now),
                    final_status: "exec_failed".to_string(),
                    error_text: Some("codex cloud exec failed".to_string()),
                },
                command_traces: vec![CloudCommandTrace {
                    trace_id: "trace_fail_exec".to_string(),
                    cloud_run_id: format!(
                        "cloudrun_{}_attempt_{attempt_index:03}",
                        packet.packet_id.0
                    ),
                    attempt_index,
                    command_kind: CloudCommandKind::Exec,
                    command_text: "codex cloud exec --env test <prompt omitted>".to_string(),
                    started_at: now,
                    finished_at: now,
                    exit_status: Some(1),
                    stdout_summary: None,
                    stderr_summary: Some("synthetic exec failure".to_string()),
                }],
                result: None,
            }
        }
    }

    #[test]
    fn cleanroom_end_to_end_round_trip_works() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();

        let packet_id = cleanroom
            .ingest_and_stage(RawDocument::new(
                "doc",
                SourceKind::Document,
                None,
                format!("Intro.\n\n{}\n\nTODO: clarify.", "Dense text. ".repeat(100)),
            ))
            .unwrap();
        cleanroom.run_cloud(&packet_id).unwrap();
        let claimed = cleanroom.claim_next_review("ace").unwrap().unwrap();
        let approved = cleanroom
            .approve_packet(&claimed.packet_id, Some("approved"))
            .unwrap();
        let wiki = cleanroom.promote_approved(&packet_id).unwrap();
        let replay = cleanroom.replay_packet(&packet_id).unwrap();

        assert_eq!(approved.review_ready.packet.packet_id, packet_id);
        assert_eq!(wiki.approved_packet_id, packet_id);
        assert!(replay.wiki_node.is_some());
        assert!(replay.lineage.is_some());
        assert!(replay.gate_state.is_some());
        assert!(!replay.diff_states.is_empty());
        assert!(!replay.evidence_states.is_empty());
        assert!(replay.review_artifact.is_some());
    }

    #[test]
    fn reject_requires_minimum_note_length() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();

        let packet_id = cleanroom
            .ingest_and_stage(RawDocument::new(
                "doc",
                SourceKind::Document,
                None,
                format!("Intro.\n\n{}\n\nTODO: clarify.", "Dense text. ".repeat(100)),
            ))
            .unwrap();
        cleanroom.run_cloud(&packet_id).unwrap();
        let _claimed = cleanroom.claim_next_review("ace").unwrap().unwrap();

        let err = cleanroom
            .reject_packet(&packet_id, "too short")
            .unwrap_err();
        assert!(matches!(err, PipelineError::Review(_)));
    }

    #[test]
    fn reject_and_rework_lock_distinct_review_outcomes() {
        let build_packet = |cleanroom: &mut Cleanroom<
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        >| {
            let packet_id = cleanroom
                .ingest_and_stage(RawDocument::new(
                    "doc",
                    SourceKind::Document,
                    None,
                    format!("Intro.\n\n{}\n\nTODO: clarify.", "Dense text. ".repeat(100)),
                ))
                .unwrap();
            cleanroom.run_cloud(&packet_id).unwrap();
            let _claimed = cleanroom.claim_next_review("ace").unwrap().unwrap();
            packet_id
        };

        let mut rejected_cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let rejected_packet_id = build_packet(&mut rejected_cleanroom);
        rejected_cleanroom
            .reject_packet(
                &rejected_packet_id,
                "Rejecting this packet because the evidence chain is incomplete.",
            )
            .unwrap();
        let rejected = rejected_cleanroom
            .replay_packet(&rejected_packet_id)
            .unwrap()
            .review_queue_item
            .unwrap();
        assert_eq!(rejected.status, QueueStatus::Rejected);
        assert_eq!(rejected.decision, Some(ReviewDecision::Reject));
        let rejected_artifact = rejected_cleanroom
            .replay_packet(&rejected_packet_id)
            .unwrap()
            .review_artifact
            .unwrap();
        assert_eq!(rejected_artifact.decision, ReviewDecision::Reject);

        let mut rework_cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let rework_packet_id = build_packet(&mut rework_cleanroom);
        rework_cleanroom
            .request_rework(
                &rework_packet_id,
                "Return this packet for rework because blockers remain unresolved.",
            )
            .unwrap();
        let rework = rework_cleanroom
            .replay_packet(&rework_packet_id)
            .unwrap()
            .review_queue_item
            .unwrap();
        assert_eq!(rework.status, QueueStatus::ReworkRequested);
        assert_eq!(rework.decision, Some(ReviewDecision::Rework));
        let rework_artifact = rework_cleanroom
            .replay_packet(&rework_packet_id)
            .unwrap()
            .review_artifact
            .unwrap();
        assert_eq!(rework_artifact.decision, ReviewDecision::Rework);
    }

    #[test]
    fn cloud_failures_are_persisted_and_do_not_queue_review() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            FailingCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let packet_id = cleanroom
            .ingest_and_stage(RawDocument::new(
                "doc",
                SourceKind::Document,
                None,
                format!("Intro.\n\n{}\n\nTODO: clarify.", "Dense text. ".repeat(100)),
            ))
            .unwrap();

        let error = cleanroom.run_cloud(&packet_id).unwrap_err();

        assert!(matches!(error, PipelineError::Storage(_)));
        assert!(cleanroom.db.load_review_item(&packet_id).unwrap().is_none());
        let replay = cleanroom.replay_packet(&packet_id).unwrap();
        assert_eq!(replay.cloud_task_runs.len(), 1);
        assert_eq!(replay.cloud_task_runs[0].final_status, "exec_failed");
        assert_eq!(replay.cloud_command_traces.len(), 1);
        assert!(replay.result.is_none());
        assert!(replay
            .audit_events
            .iter()
            .any(|event| event.stage == "cloud" && event.action == "cloud_failed"));
    }
}
