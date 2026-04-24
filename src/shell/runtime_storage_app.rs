use crate::error::PipelineError;
use crate::model::{PacketId, QueueStatus, ReviewQueueItem};
use crate::review::refresh_review_gate_state;
use crate::shell::api::{ActionReceiptDto, QueueRowDto, ReviewerIdentityDto, ShellViewDto};
use crate::shell::view_model::{build_shell_view, view_error};
use crate::storage::Database;

pub fn storage_runtime_view(
    db: &mut Database,
    service_version: &str,
    reviewer_identity: ReviewerIdentityDto,
    last_action_receipt: Option<ActionReceiptDto>,
    requested_packet_id: Option<&str>,
    _last_view_revision: Option<&str>,
) -> Result<ShellViewDto, PipelineError> {
    let review_items = db.list_review_items()?;
    refresh_review_queue_gate_states(db, &review_items)?;
    let queue_rows = build_queue_rows(db, &review_items)?;

    if let Some(packet_id) = requested_packet_id {
        let packet_id = PacketId(packet_id.to_string());
        let replay = db.load_replay_bundle(&packet_id)?;
        if replay.packet.is_none() && replay.review_queue_item.is_none() {
            return Ok(build_shell_view(
                service_version,
                "storage",
                "packet_missing",
                reviewer_identity.clone(),
                last_action_receipt.clone(),
                Some(packet_id.0),
                None,
                "requested_packet_missing",
                queue_rows,
                None,
                view_error(
                    "packet_missing",
                    "Requested packet is not present in storage.",
                    false,
                ),
            ));
        }
        let _ = refresh_review_gate_state(db, &packet_id)?;
        let replay = db.load_replay_bundle(&packet_id)?;
        ensure_selected_bundle_ready(&replay, &packet_id.0)?;
        let view_state = if replay
            .gate_state
            .as_ref()
            .map(|state| state.stale_flag)
            .unwrap_or(false)
        {
            "stale_view"
        } else {
            "ready"
        };
        return Ok(build_shell_view(
            service_version,
            "storage",
            view_state,
            reviewer_identity.clone(),
            last_action_receipt.clone(),
            Some(packet_id.0.clone()),
            Some(packet_id.0),
            "requested_packet",
            queue_rows,
            Some(&replay),
            ShellViewDto::no_error(),
        ));
    }

    if let Some(item) = review_items
        .iter()
        .find(|item| item.status == QueueStatus::InReview)
    {
        let _ = refresh_review_gate_state(db, &item.packet_id)?;
        let replay = db.load_replay_bundle(&item.packet_id)?;
        ensure_selected_bundle_ready(&replay, &item.packet_id.0)?;
        let view_state = if replay
            .gate_state
            .as_ref()
            .map(|state| state.stale_flag)
            .unwrap_or(false)
        {
            "stale_view"
        } else {
            "ready"
        };
        return Ok(build_shell_view(
            service_version,
            "storage",
            view_state,
            reviewer_identity.clone(),
            last_action_receipt.clone(),
            None,
            Some(item.packet_id.0.clone()),
            "current_in_review",
            queue_rows,
            Some(&replay),
            ShellViewDto::no_error(),
        ));
    }

    if let Some(item) = review_items
        .iter()
        .find(|item| item.status == QueueStatus::Pending)
    {
        let _ = refresh_review_gate_state(db, &item.packet_id)?;
        let replay = db.load_replay_bundle(&item.packet_id)?;
        ensure_selected_bundle_ready(&replay, &item.packet_id.0)?;
        let view_state = if replay
            .gate_state
            .as_ref()
            .map(|state| state.stale_flag)
            .unwrap_or(false)
        {
            "stale_view"
        } else {
            "ready"
        };
        return Ok(build_shell_view(
            service_version,
            "storage",
            view_state,
            reviewer_identity.clone(),
            last_action_receipt.clone(),
            None,
            Some(item.packet_id.0.clone()),
            "newest_pending",
            queue_rows,
            Some(&replay),
            ShellViewDto::no_error(),
        ));
    }

    Ok(build_shell_view(
        service_version,
        "storage",
        "storage_empty",
        reviewer_identity,
        last_action_receipt,
        None,
        None,
        "no_packet_available",
        queue_rows,
        None,
        view_error(
            "storage_empty",
            "No review packets are available in storage.",
            false,
        ),
    ))
}

fn build_queue_rows(
    db: &Database,
    review_items: &[ReviewQueueItem],
) -> Result<Vec<QueueRowDto>, PipelineError> {
    review_items
        .iter()
        .map(|item| {
            let title = db
                .load_raw(&item.document_id)?
                .map(|raw| raw.title)
                .unwrap_or_else(|| item.packet_id.0.clone());
            let gate = db.load_review_gate_state(&item.packet_id)?;
            Ok(QueueRowDto {
                packet_id: item.packet_id.0.clone(),
                title,
                queue_status: item.status.as_str().to_string(),
                validation_status: gate
                    .as_ref()
                    .map(|state| state.validation_status.clone())
                    .unwrap_or_else(|| "unavailable".to_string()),
                blocker_count: gate.as_ref().map(|state| state.blocker_count).unwrap_or(0),
                stale: gate.as_ref().map(|state| state.stale_flag).unwrap_or(false),
            })
        })
        .collect()
}

fn refresh_review_queue_gate_states(
    db: &mut Database,
    review_items: &[ReviewQueueItem],
) -> Result<(), PipelineError> {
    for item in review_items {
        let _ = refresh_review_gate_state(db, &item.packet_id)?;
    }
    Ok(())
}

fn ensure_selected_bundle_ready(
    replay: &crate::model::ReplayBundle,
    packet_id: &str,
) -> Result<(), PipelineError> {
    if replay.packet.is_none() {
        return Err(PipelineError::Storage(format!(
            "packet {packet_id} is missing its persisted packet payload"
        )));
    }
    if replay.gate_state.is_none() {
        return Err(PipelineError::Storage(format!(
            "packet {packet_id} is missing its persisted gate state"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::thread;
    use std::time::Duration;
    use std::time::{SystemTime, UNIX_EPOCH};

    use crate::cleanroom::Cleanroom;
    use crate::cloud::MockCloudSummarizer;
    use crate::metadata::RuleBasedMetadata;
    use crate::model::{RawDocument, SourceKind};
    use crate::preflight::RuleBasedPreflight;
    use crate::shell::api::ReviewerIdentityDto;
    use crate::storage::Database;

    use super::storage_runtime_view;

    #[test]
    fn explicit_requested_packet_is_selected() {
        let db_path = temp_db_path("runtime_storage_requested");
        let packet_id = build_ready_packets(&db_path, 1, false).remove(0);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            Some(&packet_id),
            None,
        )
        .unwrap();
        assert_eq!(view.selection_reason, "requested_packet");
        assert_eq!(view.active_packet_id.as_deref(), Some(packet_id.as_str()));

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn missing_requested_packet_returns_packet_missing_state() {
        let db_path = temp_db_path("runtime_storage_missing");
        build_ready_packets(&db_path, 1, false);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            Some("pkt_missing"),
            None,
        )
        .unwrap();
        assert_eq!(view.view_state, "packet_missing");
        assert_eq!(view.selection_reason, "requested_packet_missing");
        assert!(view.active_packet_id.is_none());

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn current_in_review_is_selected_when_packet_is_not_requested() {
        let db_path = temp_db_path("runtime_storage_in_review");
        let packet_ids = build_ready_packets(&db_path, 2, true);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(view.selection_reason, "current_in_review");
        assert_eq!(
            view.active_packet_id.as_deref(),
            Some(packet_ids[0].as_str())
        );

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn newest_pending_is_selected_when_no_packet_is_in_review() {
        let db_path = temp_db_path("runtime_storage_newest_pending");
        let packet_ids = build_ready_packets(&db_path, 2, false);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(view.selection_reason, "newest_pending");
        assert_eq!(
            view.active_packet_id.as_deref(),
            Some(packet_ids[1].as_str())
        );

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn storage_empty_state_is_emitted_when_no_packets_exist() {
        let db_path = temp_db_path("runtime_storage_empty");
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(view.view_state, "storage_empty");
        assert_eq!(view.selection_reason, "no_packet_available");
        assert!(view.active_packet_id.is_none());

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn stale_pending_packet_is_selected_and_rendered_as_stale_view() {
        let db_path = temp_db_path("runtime_storage_stale_pending");
        let packet_id = build_ready_packets(&db_path, 1, false).remove(0);
        inject_nonblocking_validation_drift(&db_path, &packet_id);
        let mut db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();

        let view = storage_runtime_view(
            &mut db,
            env!("CARGO_PKG_VERSION"),
            reviewer_identity(),
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(view.selection_reason, "newest_pending");
        assert_eq!(view.view_state, "stale_view");
        assert_eq!(view.active_packet_id.as_deref(), Some(packet_id.as_str()));
        assert_eq!(view.left_rail.rows[0].packet_id, packet_id);
        assert!(view.left_rail.rows[0].stale);

        fs::remove_file(db_path).ok();
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

    fn build_ready_packets(path: &Path, count: usize, claim_first: bool) -> Vec<String> {
        let mut cleanroom = Cleanroom::open(
            path.to_string_lossy().as_ref(),
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let mut packet_ids = Vec::new();
        for index in 0..count {
            let packet_id = cleanroom
                .ingest_and_stage(RawDocument::new(
                    format!("doc-{index}"),
                    SourceKind::Document,
                    None,
                    format!(
                        "Intro {}.\n\n{}\n\nTODO: clarify.",
                        index,
                        "Dense text. ".repeat(100)
                    ),
                ))
                .unwrap();
            cleanroom.run_cloud(&packet_id).unwrap();
            packet_ids.push(packet_id.0.clone());
        }
        if claim_first {
            cleanroom.claim_next_review("ace").unwrap().unwrap();
        }
        packet_ids
    }

    fn inject_nonblocking_validation_drift(path: &Path, packet_id: &str) {
        let mut db = Database::open(path.to_string_lossy().as_ref()).unwrap();
        let packet_id = crate::model::PacketId(packet_id.to_string());
        let packet = db.load_packet(&packet_id).unwrap().unwrap();
        let mut validation = db.load_validation(&packet_id).unwrap().unwrap();
        thread::sleep(Duration::from_millis(2));
        validation.issues.push(crate::model::ValidationIssue {
            issue_id: format!("issue_drift_{}", packet_id.0),
            severity: crate::model::ValidationSeverity::Warning,
            blocking: false,
            target_id: Some(packet.packet_id.0.clone()),
            message: "non-blocking review drift".to_string(),
        });
        db.save_validation(&packet.document_id, &validation)
            .unwrap();
    }
}
