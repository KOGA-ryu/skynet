use chrono::{DateTime, Utc};

use crate::model::{
    AuditEvent, CloudCommandKind, CloudCommandTrace, CloudSummaryResult, CloudTaskPacket,
    CloudTaskRun, DocumentId, EvidenceRef, Marker, NodeId, PacketId, ParsedDocument, QueueStatus,
    RawDocument, ReplayBundle, ReviewDecision, ReviewDiffState, ReviewEvidenceState,
    ReviewGateState, ReviewQueueItem, SourceKind, Span, SummaryFragment, ValidationIssue,
    ValidationReport, ValidationSeverity, WorkUnit,
};

fn ts(raw: &str) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(raw)
        .expect("fixture timestamp must parse")
        .with_timezone(&Utc)
}

pub fn first_vertical_slice_review_queue_items() -> Vec<ReviewQueueItem> {
    vec![
        ReviewQueueItem {
            packet_id: PacketId("pkt_review_001".to_string()),
            document_id: DocumentId("doc_review_001".to_string()),
            status: QueueStatus::InReview,
            assigned_reviewer: Some("ace".to_string()),
            decision: None,
            notes: None,
            claimed_at: Some(ts("2026-04-23T11:32:00Z")),
            completed_at: None,
            created_at: ts("2026-04-23T11:30:00Z"),
            updated_at: ts("2026-04-23T11:32:00Z"),
        },
        ReviewQueueItem {
            packet_id: PacketId("pkt_review_002".to_string()),
            document_id: DocumentId("doc_review_002".to_string()),
            status: QueueStatus::Pending,
            assigned_reviewer: None,
            decision: None,
            notes: None,
            claimed_at: None,
            completed_at: None,
            created_at: ts("2026-04-23T11:28:00Z"),
            updated_at: ts("2026-04-23T11:28:00Z"),
        },
        ReviewQueueItem {
            packet_id: PacketId("pkt_review_003".to_string()),
            document_id: DocumentId("doc_review_003".to_string()),
            status: QueueStatus::Pending,
            assigned_reviewer: None,
            decision: None,
            notes: None,
            claimed_at: None,
            completed_at: None,
            created_at: ts("2026-04-23T11:33:00Z"),
            updated_at: ts("2026-04-23T11:33:00Z"),
        },
    ]
}

pub fn first_vertical_slice_persisted_gate_state() -> ReviewGateState {
    ReviewGateState {
        packet_id: PacketId("pkt_review_001".to_string()),
        packet_version: "version_for_pkt_review_001".to_string(),
        required_fields_loaded: true,
        validation_status: "warning".to_string(),
        blocker_count: 2,
        diff_reviewed: true,
        evidence_reviewed: false,
        stale_flag: false,
        dirty_flag: false,
        active_diff_target_id: Some("diff_target_001".to_string()),
        active_evidence_id: Some("evidence_003".to_string()),
        active_validation_issue_id: Some("issue_blocker_001".to_string()),
        approve_enabled: false,
        reject_enabled: true,
        rework_enabled: true,
        updated_at: ts("2026-04-23T11:32:30Z"),
    }
}

pub fn first_vertical_slice_replay_bundle() -> ReplayBundle {
    let packet_id = PacketId("pkt_review_001".to_string());
    let document_id = DocumentId("doc_review_001".to_string());
    let raw = RawDocument {
        document_id: document_id.clone(),
        title: "Probability measure cleanroom review".to_string(),
        source_kind: SourceKind::Document,
        source_path: Some("memory://fixture/probability_measure.md".to_string()),
        acquired_at: ts("2026-04-23T11:29:00Z"),
        raw_text: "A probability measure assigns one to the full sample space.".to_string(),
        raw_sha256: crate::model::sha256_hex(
            "A probability measure assigns one to the full sample space.",
        ),
    };
    let packet = CloudTaskPacket {
        packet_id: packet_id.clone(),
        document_id: document_id.clone(),
        work_units: vec![WorkUnit {
            work_unit_id: "wu_probability_measure_001".to_string(),
            target_node_id: NodeId("node_probability_measure".to_string()),
            visible_node_ids: vec![NodeId("node_probability_measure".to_string())],
            context_node_ids: vec![NodeId("node_probability_measure_ctx".to_string())],
            trim_map: vec![],
            rendered_text: "A probability measure assigns one to the full sample space."
                .to_string(),
            instructions: vec!["Preserve normalization language.".to_string()],
        }],
        style_contract: "concise".to_string(),
        completion_contract: "traceable".to_string(),
    };
    let result = CloudSummaryResult {
        packet_id: packet_id.clone(),
        model_name: "fixture-model".to_string(),
        fragments: vec![SummaryFragment {
            target_node_id: NodeId("node_probability_measure".to_string()),
            summary_title: "Probability measure summary".to_string(),
            summary_text: "Probability measures preserve normalization and countable additivity."
                .to_string(),
            unresolved_questions: vec!["Should the example mention sigma algebras?".to_string()],
            evidence: vec![
                EvidenceRef {
                    node_id: NodeId("node_probability_measure".to_string()),
                    span: Span { start: 0, end: 52 },
                },
                EvidenceRef {
                    node_id: NodeId("node_probability_measure_ctx".to_string()),
                    span: Span { start: 0, end: 63 },
                },
            ],
        }],
    };
    let validation = ValidationReport {
        packet_id: packet_id.clone(),
        passed: false,
        issues: vec![
            ValidationIssue {
                issue_id: "issue_blocker_001".to_string(),
                severity: ValidationSeverity::Error,
                blocking: true,
                target_id: Some("node_probability_measure".to_string()),
                message: "Normalization constraint missing in summary body.".to_string(),
            },
            ValidationIssue {
                issue_id: "issue_blocker_002".to_string(),
                severity: ValidationSeverity::Error,
                blocking: true,
                target_id: Some("node_probability_measure".to_string()),
                message: "Countable additivity evidence is incomplete.".to_string(),
            },
            ValidationIssue {
                issue_id: "issue_warning_003".to_string(),
                severity: ValidationSeverity::Warning,
                blocking: false,
                target_id: Some("node_probability_measure".to_string()),
                message: "Subject label could be more specific.".to_string(),
            },
        ],
    };
    let diff_states = vec![ReviewDiffState {
        packet_id: packet_id.clone(),
        diff_target_id: "diff_target_001".to_string(),
        change_count: 4,
        reviewed: true,
        reviewed_by: Some("ace".to_string()),
        reviewed_at: Some(ts("2026-04-23T11:32:10Z")),
        summary: "4 changed fields across summary body and unresolved questions.".to_string(),
    }];
    let evidence_states = vec![
        ReviewEvidenceState {
            packet_id: packet_id.clone(),
            evidence_id: "evidence_001".to_string(),
            target_id: "blocker_001".to_string(),
            reviewed: true,
            reviewed_by: Some("ace".to_string()),
            reviewed_at: Some(ts("2026-04-23T11:32:05Z")),
            payload_json: serde_json::json!({
                "title": "Definition excerpt",
                "excerpt": "A probability measure assigns one to the full sample space."
            })
            .to_string(),
        },
        ReviewEvidenceState {
            packet_id: packet_id.clone(),
            evidence_id: "evidence_002".to_string(),
            target_id: "blocker_002".to_string(),
            reviewed: true,
            reviewed_by: Some("ace".to_string()),
            reviewed_at: Some(ts("2026-04-23T11:32:06Z")),
            payload_json: serde_json::json!({
                "title": "Normalization excerpt",
                "excerpt": "The packet omits the normalization constraint in the summary body."
            })
            .to_string(),
        },
        ReviewEvidenceState {
            packet_id: packet_id.clone(),
            evidence_id: "evidence_003".to_string(),
            target_id: "diff_target_001".to_string(),
            reviewed: false,
            reviewed_by: None,
            reviewed_at: None,
            payload_json: serde_json::json!({
                "title": "Diff support excerpt",
                "excerpt": "The revised paragraph now names countable additivity explicitly."
            })
            .to_string(),
        },
    ];
    let gate_state = first_vertical_slice_persisted_gate_state();

    ReplayBundle {
        raw: Some(raw),
        parsed: Some(ParsedDocument {
            raw: RawDocument {
                document_id: document_id.clone(),
                title: "Probability measure cleanroom review".to_string(),
                source_kind: SourceKind::Document,
                source_path: Some("memory://fixture/probability_measure.md".to_string()),
                acquired_at: ts("2026-04-23T11:29:00Z"),
                raw_text: "A probability measure assigns one to the full sample space.".to_string(),
                raw_sha256: crate::model::sha256_hex(
                    "A probability measure assigns one to the full sample space.",
                ),
            },
            nodes: vec![],
        }),
        markers: Some(Vec::<Marker>::new()),
        packet: Some(packet),
        cloud_task_runs: vec![CloudTaskRun {
            cloud_run_id: "cloudrun_pkt_review_001_attempt_001".to_string(),
            packet_id: PacketId("pkt_review_001".to_string()),
            attempt_index: 1,
            task_id: Some("task_e_fixture".to_string()),
            task_url: Some("https://chatgpt.com/codex/tasks/task_e_fixture".to_string()),
            environment_id: Some("fixture".to_string()),
            matched_remote_identity: Some("github.com/owner/repo".to_string()),
            current_head_sha: Some("deadbeefcafebabefeedface1234567890abcdef".to_string()),
            current_branch: Some("main".to_string()),
            head_contained_in_allowed_remote_ref: Some(true),
            resolution_method: "recent_task_list".to_string(),
            handoff_mode: "inline_packet_visible_output_v3".to_string(),
            packet_path: ".cleanroom/packets/pkt_review_001.attempt-001.packet.json".to_string(),
            schema_path: ".cleanroom/schema/pkt_review_001.attempt-001.summary.schema.json"
                .to_string(),
            output_path: "codex_apply_out/pkt_review_001.attempt-001.summary.json".to_string(),
            allowed_apply_paths: vec![
                "codex_apply_out/pkt_review_001.attempt-001.summary.json".to_string()
            ],
            new_apply_paths: vec![
                "codex_apply_out/pkt_review_001.attempt-001.summary.json".to_string()
            ],
            submitted_at: ts("2026-04-23T11:30:30Z"),
            finished_at: Some(ts("2026-04-23T11:31:00Z")),
            final_status: "success".to_string(),
            error_text: None,
        }],
        cloud_command_traces: vec![
            CloudCommandTrace {
                trace_id: "trace_fixture_exec".to_string(),
                cloud_run_id: "cloudrun_pkt_review_001_attempt_001".to_string(),
                attempt_index: 1,
                command_kind: CloudCommandKind::Exec,
                command_text: "codex cloud exec --env fixture <prompt omitted>".to_string(),
                started_at: ts("2026-04-23T11:30:30Z"),
                finished_at: ts("2026-04-23T11:30:33Z"),
                exit_status: Some(0),
                stdout_summary: Some("submitted task_e_fixture".to_string()),
                stderr_summary: None,
            },
            CloudCommandTrace {
                trace_id: "trace_fixture_apply".to_string(),
                cloud_run_id: "cloudrun_pkt_review_001_attempt_001".to_string(),
                attempt_index: 1,
                command_kind: CloudCommandKind::Apply,
                command_text: "codex apply task_e_fixture".to_string(),
                started_at: ts("2026-04-23T11:30:55Z"),
                finished_at: ts("2026-04-23T11:31:00Z"),
                exit_status: Some(0),
                stdout_summary: Some("applied cleanroom output".to_string()),
                stderr_summary: None,
            },
        ],
        result: Some(result),
        validation: Some(validation),
        lineage: None,
        review_queue_item: Some(ReviewQueueItem {
            packet_id,
            document_id,
            status: QueueStatus::InReview,
            assigned_reviewer: Some("ace".to_string()),
            decision: Some(ReviewDecision::Rework),
            notes: Some("Need explicit normalization language.".to_string()),
            claimed_at: Some(ts("2026-04-23T11:32:00Z")),
            completed_at: None,
            created_at: ts("2026-04-23T11:30:00Z"),
            updated_at: ts("2026-04-23T11:32:00Z"),
        }),
        review_artifact: None,
        gate_state: Some(gate_state),
        diff_states,
        evidence_states,
        approved: None,
        wiki_node: None,
        audit_events: vec![
            AuditEvent {
                event_id: 1,
                aggregate_id: "pkt_review_001".to_string(),
                aggregate_type: "packet".to_string(),
                stage: "packetize".to_string(),
                action: "packet_saved".to_string(),
                payload_json: None,
                created_at: ts("2026-04-23T11:30:00Z"),
            },
            AuditEvent {
                event_id: 2,
                aggregate_id: "pkt_review_001".to_string(),
                aggregate_type: "packet".to_string(),
                stage: "cloud".to_string(),
                action: "result_saved".to_string(),
                payload_json: None,
                created_at: ts("2026-04-23T11:31:00Z"),
            },
            AuditEvent {
                event_id: 3,
                aggregate_id: "pkt_review_001".to_string(),
                aggregate_type: "packet".to_string(),
                stage: "validate".to_string(),
                action: "validation_reported".to_string(),
                payload_json: None,
                created_at: ts("2026-04-23T11:31:30Z"),
            },
            AuditEvent {
                event_id: 4,
                aggregate_id: "pkt_review_001".to_string(),
                aggregate_type: "packet".to_string(),
                stage: "review_queue".to_string(),
                action: "claimed".to_string(),
                payload_json: Some("{\"reviewer\":\"ace\"}".to_string()),
                created_at: ts("2026-04-23T11:32:00Z"),
            },
        ],
    }
}
