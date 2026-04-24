use serde::Serialize;
use serde_json::{json, Value};

use crate::model::{ReplayBundle, ReviewQueueItem};
use crate::review::{primary_disabled_reason, review_action_policy};
use crate::shell::api::{
    ActionReceiptDto, BottomStripPaneDto, CenterSurfacePaneDto, DiffSummaryDto, EventRowDto,
    EvidenceRowDto, LeftRailPaneDto, PacketSummaryDto, QueueRowDto, ReviewActionsDto,
    ReviewerIdentityDto, RightInspectorPaneDto, ShellViewDto, StatusDto, ValidationSummaryDto,
    ViewErrorDto, DTO_VERSION, INTERACTION_MODE_DISPLAY_ONLY, PROTOCOL_VERSION,
    REVIEWER_IDENTITY_MISSING_MESSAGE,
};
use crate::shell::gate::{disabled_gate, gate_from_persisted};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaneBanner {
    pub kind: String,
    pub text: String,
}

pub fn build_shell_view(
    service_version: &str,
    source_mode: &str,
    view_state: &str,
    reviewer_identity: ReviewerIdentityDto,
    last_action_receipt: Option<ActionReceiptDto>,
    requested_packet_id: Option<String>,
    active_packet_id: Option<String>,
    selection_reason: &str,
    queue_rows: Vec<QueueRowDto>,
    replay: Option<&ReplayBundle>,
    error: ViewErrorDto,
) -> ShellViewDto {
    let gate = match replay.and_then(|bundle| bundle.gate_state.as_ref()) {
        Some(persisted) => gate_from_persisted(persisted),
        None => disabled_gate(view_state, source_mode),
    };
    let review_item = replay.and_then(|bundle| bundle.review_queue_item.as_ref());
    let banner = pane_banner(view_state, requested_packet_id.as_deref(), &error.message);
    let status = build_status(
        source_mode,
        view_state,
        &gate.updated_at,
        active_packet_id.as_deref(),
        review_item,
        reviewer_identity.clone(),
        last_action_receipt,
    );
    let packet_summary = replay
        .map(packet_summary_from_replay)
        .unwrap_or_else(ShellViewDto::empty_packet_summary);
    let validation_summary = replay
        .map(validation_summary_from_replay)
        .unwrap_or_else(ShellViewDto::empty_validation_summary);
    let diff_summary = replay
        .map(diff_summary_from_replay)
        .unwrap_or_else(ShellViewDto::empty_diff_summary);
    let evidence_rows = replay.map(evidence_rows_from_replay).unwrap_or_default();
    let event_rows = replay.map(event_rows_from_replay).unwrap_or_default();
    let review_actions = review_actions(view_state, replay, review_item, &reviewer_identity);
    let component_state = pane_component_state(view_state, &gate);
    let mut dto = ShellViewDto {
        protocol_version: PROTOCOL_VERSION.to_string(),
        dto_version: DTO_VERSION.to_string(),
        service_version: service_version.to_string(),
        source_mode: source_mode.to_string(),
        view_state: view_state.to_string(),
        view_revision: String::new(),
        interaction_mode: INTERACTION_MODE_DISPLAY_ONLY.to_string(),
        active_packet_id,
        requested_packet_id,
        selection_reason: selection_reason.to_string(),
        frame: ShellViewDto::empty_frame(),
        status,
        tabs: ShellViewDto::empty_tabs(),
        gate,
        left_rail: LeftRailPaneDto {
            pane_id: "left_rail".to_string(),
            title: "Review Queue".to_string(),
            component_state: component_state.clone(),
            collapsed: false,
            visible: true,
            banner_kind: banner.kind.clone(),
            banner_text: banner.text.clone(),
            rows: queue_rows,
        },
        center_surface: CenterSurfacePaneDto {
            pane_id: "center_surface".to_string(),
            title: "Packet Surface".to_string(),
            component_state: component_state.clone(),
            collapsed: false,
            visible: true,
            banner_kind: banner.kind.clone(),
            banner_text: banner.text.clone(),
            packet_summary,
            validation_summary,
            diff_summary,
        },
        right_inspector: RightInspectorPaneDto {
            pane_id: "right_inspector".to_string(),
            title: "Review Inspector".to_string(),
            component_state: component_state.clone(),
            collapsed: false,
            visible: true,
            banner_kind: banner.kind.clone(),
            banner_text: banner.text.clone(),
            evidence_rows,
            review_actions,
        },
        bottom_strip: BottomStripPaneDto {
            pane_id: "bottom_strip".to_string(),
            title: "Event Blotter".to_string(),
            component_state,
            collapsed: false,
            visible: true,
            banner_kind: banner.kind,
            banner_text: banner.text,
            event_rows,
        },
        error,
    };
    dto.view_revision = format!("srv:{}", revision_hash_for(&dto));
    dto
}

pub fn canonical_json_without_view_revision(view: &ShellViewDto) -> String {
    canonical_json_value_without_view_revision(view)
}

fn canonical_json_value_without_view_revision<T: Serialize>(value: &T) -> String {
    let mut json_value = serde_json::to_value(value).expect("shell dto must serialize");
    if let Value::Object(object) = &mut json_value {
        object.remove("view_revision");
    }
    serde_json::to_string(&json_value).expect("shell dto value must serialize")
}

fn revision_hash_for(view: &ShellViewDto) -> String {
    crate::model::sha256_hex(&canonical_json_without_view_revision(view))
}

fn build_status(
    source_mode: &str,
    view_state: &str,
    updated_at: &str,
    active_packet_id: Option<&str>,
    review_item: Option<&ReviewQueueItem>,
    reviewer_identity: ReviewerIdentityDto,
    last_action_receipt: Option<ActionReceiptDto>,
) -> StatusDto {
    StatusDto {
        title: active_packet_id
            .map(|packet_id| format!("Active packet {packet_id}"))
            .unwrap_or_else(|| "No active packet".to_string()),
        detail: format!("{} {}", source_mode, view_state),
        source_label: source_mode.to_string(),
        last_updated_at: updated_at.to_string(),
        queue_status: review_item.map(|item| item.status.as_str().to_string()),
        assigned_reviewer: review_item.and_then(|item| item.assigned_reviewer.clone()),
        reviewer_identity,
        last_action_receipt,
    }
}

fn pane_banner(
    view_state: &str,
    requested_packet_id: Option<&str>,
    error_message: &str,
) -> PaneBanner {
    match view_state {
        "ready" => PaneBanner {
            kind: "none".to_string(),
            text: String::new(),
        },
        "stale_view" => PaneBanner {
            kind: "warning".to_string(),
            text: "Persisted gate state marks this packet stale.".to_string(),
        },
        "storage_empty" => PaneBanner {
            kind: "info".to_string(),
            text: "No review packets are available in storage.".to_string(),
        },
        "packet_missing" => PaneBanner {
            kind: "warning".to_string(),
            text: format!(
                "Requested packet {} is not present in storage.",
                requested_packet_id.unwrap_or("unknown")
            ),
        },
        "service_unavailable" | "invalid_reply" => PaneBanner {
            kind: "error".to_string(),
            text: error_message.to_string(),
        },
        _ => PaneBanner {
            kind: "warning".to_string(),
            text: view_state.replace('_', " "),
        },
    }
}

fn pane_component_state(view_state: &str, gate: &crate::shell::api::GateDto) -> String {
    match view_state {
        "ready" => {
            if gate.blocker_count > 0 {
                "warning".to_string()
            } else {
                "focused".to_string()
            }
        }
        "stale_view" => "stale".to_string(),
        "service_unavailable" | "invalid_reply" => "error".to_string(),
        _ => "blocked".to_string(),
    }
}

fn review_actions(
    view_state: &str,
    replay: Option<&ReplayBundle>,
    review_item: Option<&ReviewQueueItem>,
    reviewer_identity: &ReviewerIdentityDto,
) -> ReviewActionsDto {
    if matches!(
        view_state,
        "storage_empty" | "packet_missing" | "service_unavailable" | "invalid_reply"
    ) {
        ShellViewDto::disabled_review_actions()
    } else {
        review_actions_for_item(
            review_item,
            replay.and_then(|bundle| bundle.gate_state.as_ref()),
            reviewer_identity,
        )
    }
}

fn review_actions_for_item(
    item: Option<&ReviewQueueItem>,
    gate_state: Option<&crate::model::ReviewGateState>,
    reviewer_identity: &ReviewerIdentityDto,
) -> ReviewActionsDto {
    if item.is_none() {
        return ShellViewDto::disabled_review_actions();
    }
    let item = item.expect("checked above");
    let policy = review_action_policy(
        Some(item),
        gate_state,
        reviewer_identity.reviewer_name.as_deref(),
    );
    ReviewActionsDto {
        claim_visible: true,
        claim_enabled: policy.claim.enabled,
        approve_visible: true,
        reject_visible: true,
        rework_visible: true,
        approve_enabled: policy.approve.enabled,
        reject_enabled: policy.reject.enabled,
        rework_enabled: policy.rework.enabled,
        disabled_reason: if reviewer_identity.status == "missing" {
            REVIEWER_IDENTITY_MISSING_MESSAGE.to_string()
        } else {
            primary_disabled_reason(&policy, Some(item))
        },
    }
}

fn packet_summary_from_replay(bundle: &ReplayBundle) -> PacketSummaryDto {
    match bundle.packet.as_ref() {
        Some(packet) => PacketSummaryDto {
            packet_id: packet.packet_id.0.clone(),
            document_id: packet.document_id.0.clone(),
            version: bundle
                .gate_state
                .as_ref()
                .map(|state| state.packet_version.clone())
                .unwrap_or_default(),
            subject_label: packet
                .work_units
                .first()
                .map(|work_unit| work_unit.target_node_id.0.clone())
                .unwrap_or_else(|| packet.packet_id.0.clone()),
            title: bundle
                .raw
                .as_ref()
                .map(|raw| raw.title.clone())
                .unwrap_or_else(|| packet.packet_id.0.clone()),
            render_status: "renderable".to_string(),
        },
        None => ShellViewDto::empty_packet_summary(),
    }
}

fn validation_summary_from_replay(bundle: &ReplayBundle) -> ValidationSummaryDto {
    match bundle.validation.as_ref() {
        Some(validation) => ValidationSummaryDto {
            run_id: format!("validation_{}", validation.packet_id.0),
            status: bundle
                .gate_state
                .as_ref()
                .map(|state| state.validation_status.clone())
                .unwrap_or_else(|| {
                    if validation.passed {
                        "pass".to_string()
                    } else {
                        "error".to_string()
                    }
                }),
            blocker_count: validation
                .issues
                .iter()
                .filter(|issue| issue.blocking)
                .count(),
            issue_count: validation.issues.len(),
            reviewed: bundle
                .gate_state
                .as_ref()
                .map(|state| state.required_fields_loaded)
                .unwrap_or(false),
        },
        None => ShellViewDto::empty_validation_summary(),
    }
}

fn diff_summary_from_replay(bundle: &ReplayBundle) -> DiffSummaryDto {
    bundle
        .diff_states
        .first()
        .map(|state| DiffSummaryDto {
            diff_target_id: state.diff_target_id.clone(),
            change_count: state.change_count,
            reviewed: state.reviewed,
            summary: state.summary.clone(),
        })
        .unwrap_or_else(ShellViewDto::empty_diff_summary)
}

fn evidence_rows_from_replay(bundle: &ReplayBundle) -> Vec<EvidenceRowDto> {
    bundle
        .evidence_states
        .iter()
        .map(|state| {
            let payload = serde_json::from_str::<Value>(&state.payload_json).unwrap_or_else(
                |_| json!({ "title": state.evidence_id, "excerpt": state.payload_json }),
            );
            EvidenceRowDto {
                evidence_id: state.evidence_id.clone(),
                target_id: state.target_id.clone(),
                title: payload
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or(&state.evidence_id)
                    .to_string(),
                excerpt: payload
                    .get("excerpt")
                    .and_then(Value::as_str)
                    .unwrap_or(&state.payload_json)
                    .to_string(),
                reviewed: state.reviewed,
            }
        })
        .collect()
}

fn event_rows_from_replay(bundle: &ReplayBundle) -> Vec<EventRowDto> {
    bundle
        .audit_events
        .iter()
        .map(|event| EventRowDto {
            event_id: event.event_id.to_string(),
            severity: severity_for_event(event.stage.as_str(), event.action.as_str()),
            message: format!("{} {}", event.stage, event.action),
            timestamp: event.created_at.to_rfc3339(),
        })
        .collect()
}

fn severity_for_event(stage: &str, action: &str) -> String {
    if action.contains("rejected") || action.contains("rework") {
        "warning".to_string()
    } else if stage == "validate" {
        "warning".to_string()
    } else {
        "info".to_string()
    }
}

pub fn view_error(kind: &str, message: impl Into<String>, retryable: bool) -> ViewErrorDto {
    ViewErrorDto {
        kind: kind.to_string(),
        message: message.into(),
        retryable,
        details: String::new(),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    use serde_json::Value;

    use crate::cleanroom::Cleanroom;
    use crate::cloud::MockCloudSummarizer;
    use crate::metadata::RuleBasedMetadata;
    use crate::model::{QueueStatus, RawDocument, SourceKind};
    use crate::preflight::RuleBasedPreflight;
    use crate::shell::api::ReviewerIdentityDto;
    use crate::shell::fixtures::first_vertical_slice_replay_bundle;
    use crate::shell::runtime_fixture_app::fixture_runtime_view;
    use crate::shell::runtime_storage_app::storage_runtime_view;
    use crate::storage::Database;

    use super::{build_shell_view, view_error};

    #[test]
    fn fixture_and_storage_serialization_share_identical_key_sets() {
        let db_path = temp_db_path("view_model_contract");
        let _packet_id = build_reviewable_db(&db_path);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let fixture = fixture_runtime_view(env!("CARGO_PKG_VERSION"));
        let storage = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            None,
            None,
        )
        .unwrap();

        assert_eq!(
            object_keys(&serde_json::to_value(&fixture).unwrap()),
            object_keys(&serde_json::to_value(&storage).unwrap())
        );
        assert_eq!(
            nested_object_keys(&serde_json::to_value(&fixture).unwrap(), "left_rail"),
            nested_object_keys(&serde_json::to_value(&storage).unwrap(), "left_rail")
        );
        assert_eq!(
            nested_object_keys(&serde_json::to_value(&fixture).unwrap(), "center_surface"),
            nested_object_keys(&serde_json::to_value(&storage).unwrap(), "center_surface")
        );
        assert_eq!(
            nested_object_keys(&serde_json::to_value(&fixture).unwrap(), "right_inspector"),
            nested_object_keys(&serde_json::to_value(&storage).unwrap(), "right_inspector")
        );
        assert_eq!(
            nested_object_keys(&serde_json::to_value(&fixture).unwrap(), "bottom_strip"),
            nested_object_keys(&serde_json::to_value(&storage).unwrap(), "bottom_strip")
        );

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn view_revision_is_stable_and_changes_with_payload_updates() {
        let fixture_a = fixture_runtime_view(env!("CARGO_PKG_VERSION"));
        let fixture_b = fixture_runtime_view(env!("CARGO_PKG_VERSION"));
        assert_eq!(fixture_a.view_revision, fixture_b.view_revision);

        let replay = first_vertical_slice_replay_bundle();
        let mut queue = vec![crate::shell::api::QueueRowDto {
            packet_id: "pkt_review_001".to_string(),
            title: "Probability measure packet".to_string(),
            queue_status: "in_review".to_string(),
            validation_status: "warning".to_string(),
            blocker_count: 2,
            stale: false,
        }];
        let view_a = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            queue.clone(),
            Some(&replay),
            view_error("none", "", false),
        );
        queue[0].title = "Updated title".to_string();
        let view_b = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            queue,
            Some(&replay),
            view_error("none", "", false),
        );
        assert_ne!(view_a.view_revision, view_b.view_revision);
    }

    #[test]
    fn stale_view_only_comes_from_persisted_stale_flag() {
        let mut replay = first_vertical_slice_replay_bundle();
        replay.gate_state.as_mut().unwrap().stale_flag = false;
        let ready_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert_eq!(ready_view.view_state, "ready");

        replay.gate_state.as_mut().unwrap().stale_flag = true;
        let stale_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "stale_view",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert_eq!(stale_view.view_state, "stale_view");
        assert!(stale_view.gate.stale);
    }

    #[test]
    fn gate_booleans_and_labels_are_driven_by_persisted_gate_state() {
        let mut replay = first_vertical_slice_replay_bundle();
        let gate = replay.gate_state.as_mut().unwrap();
        gate.approve_enabled = true;
        gate.reject_enabled = false;
        gate.rework_enabled = false;
        gate.stale_flag = false;
        gate.dirty_flag = false;
        gate.blocker_count = 0;
        let view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert!(view.gate.approve_enabled);
        assert!(!view.gate.reject_enabled);
        assert!(!view.gate.rework_enabled);
        assert_eq!(view.gate.label, "review_ready");
    }

    #[test]
    fn review_actions_follow_queue_status_and_reviewer_identity() {
        let mut replay = first_vertical_slice_replay_bundle();
        let gate = replay.gate_state.as_mut().unwrap();
        gate.approve_enabled = true;
        gate.reject_enabled = true;
        gate.rework_enabled = true;

        replay.review_queue_item.as_mut().unwrap().status = QueueStatus::Pending;
        let pending_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert!(pending_view.right_inspector.review_actions.claim_enabled);
        assert!(!pending_view.right_inspector.review_actions.approve_enabled);

        replay.review_queue_item.as_mut().unwrap().status = QueueStatus::InReview;
        replay.review_queue_item.as_mut().unwrap().assigned_reviewer = Some("ace".to_string());
        let self_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert!(self_view.right_inspector.review_actions.approve_enabled);
        assert!(self_view
            .right_inspector
            .review_actions
            .disabled_reason
            .is_empty());

        replay.review_queue_item.as_mut().unwrap().assigned_reviewer = Some("bea".to_string());
        let other_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert!(!other_view.right_inspector.review_actions.approve_enabled);
        assert!(other_view
            .right_inspector
            .review_actions
            .disabled_reason
            .contains("Claimed by bea"));

        let missing_identity_view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            ReviewerIdentityDto {
                status: "missing".to_string(),
                reviewer_name: None,
                source: "missing".to_string(),
            },
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert_eq!(
            missing_identity_view
                .right_inspector
                .review_actions
                .disabled_reason,
            crate::shell::api::REVIEWER_IDENTITY_MISSING_MESSAGE
        );
    }

    #[test]
    fn blocked_approve_still_allows_reject_and_rework_with_meaningful_reason() {
        let mut replay = first_vertical_slice_replay_bundle();
        let gate = replay.gate_state.as_mut().unwrap();
        gate.approve_enabled = false;
        gate.reject_enabled = true;
        gate.rework_enabled = true;
        gate.stale_flag = true;
        replay.review_queue_item.as_mut().unwrap().status = QueueStatus::InReview;
        replay.review_queue_item.as_mut().unwrap().assigned_reviewer = Some("ace".to_string());

        let view = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );

        assert!(!view.right_inspector.review_actions.approve_enabled);
        assert!(view.right_inspector.review_actions.reject_enabled);
        assert!(view.right_inspector.review_actions.rework_enabled);
        assert!(view
            .right_inspector
            .review_actions
            .disabled_reason
            .contains("marked stale"));
    }

    #[test]
    fn gate_labels_follow_persisted_priority_order() {
        let mut replay = first_vertical_slice_replay_bundle();
        let gate = replay.gate_state.as_mut().unwrap();
        gate.approve_enabled = false;
        gate.required_fields_loaded = true;
        gate.diff_reviewed = true;
        gate.evidence_reviewed = true;
        gate.blocker_count = 2;
        let blocked = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert_eq!(blocked.gate.label, "blocked");

        replay.gate_state.as_mut().unwrap().blocker_count = 0;
        replay.gate_state.as_mut().unwrap().dirty_flag = true;
        let dirty = build_shell_view(
            env!("CARGO_PKG_VERSION"),
            "fixture",
            "ready",
            reviewer_identity(),
            None,
            None,
            Some("pkt_review_001".to_string()),
            "fixture_default",
            Vec::new(),
            Some(&replay),
            view_error("none", "", false),
        );
        assert_eq!(dirty.gate.label, "dirty_blocked");
    }

    fn object_keys(value: &Value) -> BTreeSet<String> {
        value
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>()
    }

    fn nested_object_keys(value: &Value, key: &str) -> BTreeSet<String> {
        value
            .as_object()
            .unwrap()
            .get(key)
            .unwrap()
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>()
    }

    fn temp_db_path(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("{name}_{unique}.db"))
    }

    fn reviewer_identity() -> ReviewerIdentityDto {
        ReviewerIdentityDto {
            status: "present".to_string(),
            reviewer_name: Some("ace".to_string()),
            source: "client_env".to_string(),
        }
    }

    fn build_reviewable_db(path: &Path) -> String {
        let mut cleanroom = Cleanroom::open(
            path.to_string_lossy().as_ref(),
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
        cleanroom.claim_next_review("ace").unwrap().unwrap();
        packet_id.0
    }
}
