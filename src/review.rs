use chrono::{DateTime, Utc};

use crate::error::PipelineError;
use crate::model::{
    ApprovedPacket, PacketId, ReviewDecision, ReviewDiffState, ReviewEvidenceState,
    ReviewGateState, ReviewId, ReviewReadyPacket, ReviewStamp, ValidationIssue, ValidationReport,
};

pub struct Reviewer;

impl Reviewer {
    pub fn make_review_ready(
        packet: crate::model::CloudTaskPacket,
        result: crate::model::CloudSummaryResult,
        validation: crate::model::ValidationReport,
    ) -> Result<ReviewReadyPacket, PipelineError> {
        if !validation.passed {
            return Err(PipelineError::Review(
                "cannot create review packet from failed validation".to_string(),
            ));
        }
        Ok(ReviewReadyPacket {
            packet,
            result,
            validation,
        })
    }

    pub fn approve(
        review_ready: ReviewReadyPacket,
        reviewer: impl Into<String>,
        notes: impl Into<String>,
    ) -> ApprovedPacket {
        let reviewer = reviewer.into();
        let notes = notes.into();
        let stamp = Self::stamp(
            &review_ready.packet.packet_id,
            reviewer,
            ReviewDecision::Approve,
            notes,
        );
        ApprovedPacket {
            review_ready,
            stamp,
        }
    }

    pub fn stamp(
        packet_id: &crate::model::PacketId,
        reviewer: impl Into<String>,
        decision: ReviewDecision,
        notes: impl Into<String>,
    ) -> ReviewStamp {
        let reviewer = reviewer.into();
        let notes = notes.into();
        ReviewStamp {
            review_id: ReviewId::new(&format!("{}-{}-{:?}", reviewer, packet_id.0, decision)),
            reviewer,
            decision,
            notes,
            reviewed_at: Utc::now(),
        }
    }
}

pub fn packet_version(packet_id: &PacketId) -> String {
    format!("version_for_{}", packet_id.0)
}

pub fn compute_review_gate_state(
    packet_id: &PacketId,
    packet_version_value: &str,
    validation: &ValidationReport,
    diff_states: &[ReviewDiffState],
    evidence_states: &[ReviewEvidenceState],
    stale_flag: bool,
    dirty_flag: bool,
) -> ReviewGateState {
    let blocker_count = validation
        .issues
        .iter()
        .filter(|issue| issue.blocking)
        .count();
    let diff_reviewed = !diff_states.is_empty() && diff_states.iter().all(|state| state.reviewed);
    let evidence_reviewed =
        !evidence_states.is_empty() && evidence_states.iter().all(|state| state.reviewed);
    let required_fields_loaded = true;
    let approve_enabled = required_fields_loaded
        && validation.passed
        && blocker_count == 0
        && diff_reviewed
        && evidence_reviewed
        && !stale_flag
        && !dirty_flag;

    ReviewGateState {
        packet_id: packet_id.clone(),
        packet_version: packet_version_value.to_string(),
        required_fields_loaded,
        validation_status: if validation.passed {
            "pass".to_string()
        } else {
            "error".to_string()
        },
        blocker_count,
        diff_reviewed,
        evidence_reviewed,
        stale_flag,
        dirty_flag,
        active_diff_target_id: diff_states
            .iter()
            .find(|state| !state.reviewed)
            .or_else(|| diff_states.first())
            .map(|state| state.diff_target_id.clone()),
        active_evidence_id: evidence_states
            .iter()
            .find(|state| !state.reviewed)
            .or_else(|| evidence_states.first())
            .map(|state| state.evidence_id.clone()),
        active_validation_issue_id: validation
            .issues
            .iter()
            .find(|issue| issue.blocking)
            .or_else(|| validation.issues.first())
            .map(|issue| issue.issue_id.clone()),
        approve_enabled,
        reject_enabled: required_fields_loaded,
        rework_enabled: required_fields_loaded,
        updated_at: Utc::now(),
    }
}

pub fn mark_all_diff_states_reviewed(
    diff_states: &mut [ReviewDiffState],
    reviewer: &str,
    reviewed_at: DateTime<Utc>,
) {
    for state in diff_states {
        state.reviewed = true;
        state.reviewed_by = Some(reviewer.to_string());
        state.reviewed_at = Some(reviewed_at);
    }
}

pub fn mark_all_evidence_states_reviewed(
    evidence_states: &mut [ReviewEvidenceState],
    reviewer: &str,
    reviewed_at: DateTime<Utc>,
) {
    for state in evidence_states {
        state.reviewed = true;
        state.reviewed_by = Some(reviewer.to_string());
        state.reviewed_at = Some(reviewed_at);
    }
}

pub fn blocking_issues(validation: &ValidationReport) -> Vec<ValidationIssue> {
    validation
        .issues
        .iter()
        .filter(|issue| issue.blocking)
        .cloned()
        .collect()
}
