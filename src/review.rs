use chrono::{DateTime, Utc};
use serde_json::json;

use crate::error::PipelineError;
use crate::model::{
    ApprovedPacket, PacketId, QueueStatus, ReviewDecision, ReviewDiffState, ReviewEvidenceState,
    ReviewGateState, ReviewId, ReviewQueueItem, ReviewReadyPacket, ReviewStamp, ValidationIssue,
    ValidationReport,
};
use crate::storage::Database;

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

pub fn derive_review_version(db: &Database, packet_id: &PacketId) -> Result<String, PipelineError> {
    let (packet_json, _) = required_stage_payload(
        db.load_cloud_packet_payload_with_updated_at(packet_id)?,
        "packet",
        packet_id,
    )?;
    let (result_json, _) = required_stage_payload(
        db.load_cloud_result_payload_with_updated_at(packet_id)?,
        "result",
        packet_id,
    )?;
    let (validation_json, _) = required_stage_payload(
        db.load_validation_payload_with_updated_at(packet_id)?,
        "validation",
        packet_id,
    )?;
    let lineage = db
        .load_packet_lineage(packet_id)?
        .ok_or_else(|| PipelineError::NotFound(format!("lineage {} not found", packet_id.0)))?;
    let lineage_json = json!({
        "successor_packet_id": lineage.successor_packet_id.as_ref().map(|value| value.0.clone()),
    })
    .to_string();
    let digest = crate::model::sha256_hex(&format!(
        "packet:{packet_json}\nresult:{result_json}\nvalidation:{validation_json}\nlineage:{lineage_json}"
    ));
    Ok(format!("ver_{}", &digest[..16]))
}

pub fn refresh_review_gate_state(
    db: &mut Database,
    packet_id: &PacketId,
) -> Result<ReviewGateState, PipelineError> {
    let validation = db
        .load_validation(packet_id)?
        .ok_or_else(|| PipelineError::NotFound(format!("validation {} not found", packet_id.0)))?;
    let diff_states = db.load_review_diff_states(packet_id)?;
    let evidence_states = db.load_review_evidence_states(packet_id)?;
    let prior = db.load_review_gate_state(packet_id)?;
    let current_version = derive_review_version(db, packet_id)?;
    let latest_reviewable_change_at = latest_reviewable_change_at(db, packet_id)?;
    let review_item = db.load_review_item(packet_id)?;
    let baseline_version = baseline_review_version(prior.as_ref(), &current_version);
    let stale_flag = current_version != baseline_version;
    let dirty_flag = stale_flag
        && review_item
            .as_ref()
            .and_then(|item| item.claimed_at)
            .map(|claimed_at| latest_reviewable_change_at > claimed_at)
            .unwrap_or(false);
    let next = compute_review_gate_state(
        packet_id,
        &baseline_version,
        &validation,
        &diff_states,
        &evidence_states,
        stale_flag,
        dirty_flag,
    );
    if let Some(prior_state) = prior {
        if review_gate_state_matches(&prior_state, &next) {
            return Ok(prior_state);
        }
    }
    db.save_review_gate_state(&next)?;
    Ok(next)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewActionKind {
    Claim,
    Approve,
    Reject,
    Rework,
}

impl ReviewActionKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Claim => "claim",
            Self::Approve => "approve",
            Self::Reject => "reject",
            Self::Rework => "rework",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewPreconditionKind {
    ReviewerIdentityMissing,
    PacketMissing,
    PacketNotPending,
    PacketNotInReview,
    ClaimedByOtherReviewer,
    ApproveGateBlocked,
    ReviewFieldsNotLoaded,
    TerminalNoteTooShort,
}

impl ReviewPreconditionKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::ReviewerIdentityMissing => "reviewer_identity_missing",
            Self::PacketMissing => "packet_missing",
            Self::PacketNotPending => "packet_not_pending",
            Self::PacketNotInReview => "packet_not_in_review",
            Self::ClaimedByOtherReviewer => "claimed_by_other_reviewer",
            Self::ApproveGateBlocked => "approve_gate_blocked",
            Self::ReviewFieldsNotLoaded => "review_fields_not_loaded",
            Self::TerminalNoteTooShort => "terminal_note_too_short",
        }
    }

    pub fn from_code(value: &str) -> Option<Self> {
        match value {
            "reviewer_identity_missing" => Some(Self::ReviewerIdentityMissing),
            "packet_missing" => Some(Self::PacketMissing),
            "packet_not_pending" => Some(Self::PacketNotPending),
            "packet_not_in_review" => Some(Self::PacketNotInReview),
            "claimed_by_other_reviewer" => Some(Self::ClaimedByOtherReviewer),
            "approve_gate_blocked" => Some(Self::ApproveGateBlocked),
            "review_fields_not_loaded" => Some(Self::ReviewFieldsNotLoaded),
            "terminal_note_too_short" => Some(Self::TerminalNoteTooShort),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewActionAvailability {
    pub enabled: bool,
    pub reason_kind: Option<ReviewPreconditionKind>,
    pub reason_text: Option<String>,
}

impl ReviewActionAvailability {
    fn enabled() -> Self {
        Self {
            enabled: true,
            reason_kind: None,
            reason_text: None,
        }
    }

    fn disabled(
        action: ReviewActionKind,
        kind: ReviewPreconditionKind,
        review_item: Option<&ReviewQueueItem>,
        gate_state: Option<&ReviewGateState>,
    ) -> Self {
        Self {
            enabled: false,
            reason_kind: Some(kind),
            reason_text: Some(precondition_reason_text(
                action,
                kind,
                review_item,
                gate_state,
            )),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewActionPolicy {
    pub claim: ReviewActionAvailability,
    pub approve: ReviewActionAvailability,
    pub reject: ReviewActionAvailability,
    pub rework: ReviewActionAvailability,
}

pub fn review_gate_label(persisted: &ReviewGateState) -> &'static str {
    if persisted.approve_enabled {
        "review_ready"
    } else if persisted.stale_flag {
        "stale_blocked"
    } else if persisted.dirty_flag {
        "dirty_blocked"
    } else if persisted.blocker_count > 0 {
        "blocked"
    } else if !persisted.required_fields_loaded {
        "loading"
    } else {
        "needs_review"
    }
}

pub fn review_action_policy(
    review_item: Option<&ReviewQueueItem>,
    gate_state: Option<&ReviewGateState>,
    reviewer_name: Option<&str>,
) -> ReviewActionPolicy {
    ReviewActionPolicy {
        claim: match validate_claim_action(review_item, reviewer_name) {
            Ok(()) => ReviewActionAvailability::enabled(),
            Err(kind) => ReviewActionAvailability::disabled(
                ReviewActionKind::Claim,
                kind,
                review_item,
                gate_state,
            ),
        },
        approve: match validate_terminal_action(
            ReviewActionKind::Approve,
            review_item,
            gate_state,
            reviewer_name,
        ) {
            Ok(()) => ReviewActionAvailability::enabled(),
            Err(kind) => ReviewActionAvailability::disabled(
                ReviewActionKind::Approve,
                kind,
                review_item,
                gate_state,
            ),
        },
        reject: match validate_terminal_action(
            ReviewActionKind::Reject,
            review_item,
            gate_state,
            reviewer_name,
        ) {
            Ok(()) => ReviewActionAvailability::enabled(),
            Err(kind) => ReviewActionAvailability::disabled(
                ReviewActionKind::Reject,
                kind,
                review_item,
                gate_state,
            ),
        },
        rework: match validate_terminal_action(
            ReviewActionKind::Rework,
            review_item,
            gate_state,
            reviewer_name,
        ) {
            Ok(()) => ReviewActionAvailability::enabled(),
            Err(kind) => ReviewActionAvailability::disabled(
                ReviewActionKind::Rework,
                kind,
                review_item,
                gate_state,
            ),
        },
    }
}

pub fn validate_claim_action(
    review_item: Option<&ReviewQueueItem>,
    reviewer_name: Option<&str>,
) -> Result<(), ReviewPreconditionKind> {
    if reviewer_name.is_none() {
        return Err(ReviewPreconditionKind::ReviewerIdentityMissing);
    }
    let Some(review_item) = review_item else {
        return Err(ReviewPreconditionKind::PacketMissing);
    };
    if review_item.status != QueueStatus::Pending {
        return Err(ReviewPreconditionKind::PacketNotPending);
    }
    Ok(())
}

pub fn validate_terminal_action(
    action: ReviewActionKind,
    review_item: Option<&ReviewQueueItem>,
    gate_state: Option<&ReviewGateState>,
    reviewer_name: Option<&str>,
) -> Result<(), ReviewPreconditionKind> {
    debug_assert!(matches!(
        action,
        ReviewActionKind::Approve | ReviewActionKind::Reject | ReviewActionKind::Rework
    ));
    let Some(reviewer_name) = reviewer_name else {
        return Err(ReviewPreconditionKind::ReviewerIdentityMissing);
    };
    let Some(review_item) = review_item else {
        return Err(ReviewPreconditionKind::PacketMissing);
    };
    if review_item.status != QueueStatus::InReview {
        return Err(ReviewPreconditionKind::PacketNotInReview);
    }
    match review_item.assigned_reviewer.as_deref() {
        Some(assigned) if assigned == reviewer_name => {}
        _ => return Err(ReviewPreconditionKind::ClaimedByOtherReviewer),
    }
    match action {
        ReviewActionKind::Approve => {
            if gate_state.map(|gate| gate.approve_enabled).unwrap_or(false) {
                Ok(())
            } else {
                Err(ReviewPreconditionKind::ApproveGateBlocked)
            }
        }
        ReviewActionKind::Reject | ReviewActionKind::Rework => {
            if gate_state
                .map(|gate| gate.required_fields_loaded)
                .unwrap_or(false)
            {
                Ok(())
            } else {
                Err(ReviewPreconditionKind::ReviewFieldsNotLoaded)
            }
        }
        ReviewActionKind::Claim => unreachable!("claim is validated separately"),
    }
}

pub fn validate_terminal_submission(
    action: ReviewActionKind,
    review_item: Option<&ReviewQueueItem>,
    gate_state: Option<&ReviewGateState>,
    reviewer_name: Option<&str>,
    notes: Option<&str>,
    note_min_chars: usize,
) -> Result<(), ReviewPreconditionKind> {
    validate_terminal_action(action, review_item, gate_state, reviewer_name)?;
    if matches!(action, ReviewActionKind::Reject | ReviewActionKind::Rework)
        && notes.unwrap_or_default().trim().chars().count() < note_min_chars
    {
        return Err(ReviewPreconditionKind::TerminalNoteTooShort);
    }
    Ok(())
}

pub fn primary_disabled_reason(
    policy: &ReviewActionPolicy,
    review_item: Option<&ReviewQueueItem>,
) -> String {
    if let Some(item) = review_item {
        if matches!(
            item.status,
            QueueStatus::Approved | QueueStatus::Rejected | QueueStatus::ReworkRequested
        ) {
            return "Packet review is already complete.".to_string();
        }
    }

    if !policy.approve.enabled {
        if let Some(reason) = &policy.approve.reason_text {
            return reason.clone();
        }
    }
    if policy.approve.enabled || policy.reject.enabled || policy.rework.enabled {
        return String::new();
    }
    if !policy.claim.enabled {
        if let Some(reason) = &policy.claim.reason_text {
            return reason.clone();
        }
    }
    if !policy.reject.enabled {
        if let Some(reason) = &policy.reject.reason_text {
            return reason.clone();
        }
    }
    if !policy.rework.enabled {
        if let Some(reason) = &policy.rework.reason_text {
            return reason.clone();
        }
    }
    String::new()
}

pub fn review_precondition_error(kind: ReviewPreconditionKind) -> PipelineError {
    PipelineError::Review(kind.as_str().to_string())
}

pub fn precondition_reason_text(
    action: ReviewActionKind,
    kind: ReviewPreconditionKind,
    review_item: Option<&ReviewQueueItem>,
    gate_state: Option<&ReviewGateState>,
) -> String {
    match kind {
        ReviewPreconditionKind::ReviewerIdentityMissing => {
            "Reviewer identity is required for shell review actions.".to_string()
        }
        ReviewPreconditionKind::PacketMissing => {
            "Requested packet is not present in the review queue.".to_string()
        }
        ReviewPreconditionKind::PacketNotPending => {
            "Only pending packets can be claimed.".to_string()
        }
        ReviewPreconditionKind::PacketNotInReview => match review_item.map(|item| &item.status) {
            Some(QueueStatus::Pending) => {
                "Claim this packet before approving, rejecting, or requesting rework.".to_string()
            }
            Some(QueueStatus::Approved | QueueStatus::Rejected | QueueStatus::ReworkRequested) => {
                "Packet review is already complete.".to_string()
            }
            _ => "Packet must be in review before it can be completed.".to_string(),
        },
        ReviewPreconditionKind::ClaimedByOtherReviewer => review_item
            .and_then(|item| item.assigned_reviewer.as_ref())
            .map(|assigned| {
                format!("Claimed by {assigned}. Only the assigned reviewer can complete review.")
            })
            .unwrap_or_else(|| "Packet is claimed by another reviewer.".to_string()),
        ReviewPreconditionKind::ApproveGateBlocked => {
            approve_gate_blocked_message(action, gate_state)
        }
        ReviewPreconditionKind::ReviewFieldsNotLoaded => {
            "Required review fields are not loaded yet.".to_string()
        }
        ReviewPreconditionKind::TerminalNoteTooShort => {
            "Reject and rework notes must satisfy the review note policy.".to_string()
        }
    }
}

fn approve_gate_blocked_message(
    action: ReviewActionKind,
    gate_state: Option<&ReviewGateState>,
) -> String {
    let prefix = match action {
        ReviewActionKind::Approve => "Approve is blocked",
        _ => "Review action is blocked",
    };
    match gate_state.map(review_gate_label) {
        Some("stale_blocked") => {
            format!(
                "{prefix} because the packet is marked stale. Reject or rework remain available."
            )
        }
        Some("dirty_blocked") => {
            format!(
                "{prefix} because the packet is marked dirty. Reject or rework remain available."
            )
        }
        Some("blocked") => {
            format!("{prefix} by validation blockers. Reject or rework remain available.")
        }
        Some("loading") => format!("{prefix} until required review fields are loaded."),
        Some("needs_review") => {
            format!("{prefix} until diff and evidence review are complete.")
        }
        _ => "Review gate is blocking approval.".to_string(),
    }
}

fn required_stage_payload(
    row: Option<(String, DateTime<Utc>)>,
    stage: &str,
    packet_id: &PacketId,
) -> Result<(String, DateTime<Utc>), PipelineError> {
    row.ok_or_else(|| PipelineError::NotFound(format!("{stage} {} not found", packet_id.0)))
}

fn latest_reviewable_change_at(
    db: &Database,
    packet_id: &PacketId,
) -> Result<DateTime<Utc>, PipelineError> {
    let (_, packet_updated_at) = required_stage_payload(
        db.load_cloud_packet_payload_with_updated_at(packet_id)?,
        "packet",
        packet_id,
    )?;
    let (_, result_updated_at) = required_stage_payload(
        db.load_cloud_result_payload_with_updated_at(packet_id)?,
        "result",
        packet_id,
    )?;
    let (_, validation_updated_at) = required_stage_payload(
        db.load_validation_payload_with_updated_at(packet_id)?,
        "validation",
        packet_id,
    )?;
    let lineage_updated_at = db
        .load_packet_lineage(packet_id)?
        .ok_or_else(|| PipelineError::NotFound(format!("lineage {} not found", packet_id.0)))?
        .updated_at;
    Ok([
        packet_updated_at,
        result_updated_at,
        validation_updated_at,
        lineage_updated_at,
    ]
    .into_iter()
    .max()
    .expect("reviewable timestamps must not be empty"))
}

fn baseline_review_version(prior: Option<&ReviewGateState>, current_version: &str) -> String {
    match prior {
        Some(state) if state.packet_version.starts_with("ver_") => state.packet_version.clone(),
        Some(_) | None => current_version.to_string(),
    }
}

fn review_gate_state_matches(left: &ReviewGateState, right: &ReviewGateState) -> bool {
    left.packet_id == right.packet_id
        && left.packet_version == right.packet_version
        && left.required_fields_loaded == right.required_fields_loaded
        && left.validation_status == right.validation_status
        && left.blocker_count == right.blocker_count
        && left.diff_reviewed == right.diff_reviewed
        && left.evidence_reviewed == right.evidence_reviewed
        && left.stale_flag == right.stale_flag
        && left.dirty_flag == right.dirty_flag
        && left.active_diff_target_id == right.active_diff_target_id
        && left.active_evidence_id == right.active_evidence_id
        && left.active_validation_issue_id == right.active_validation_issue_id
        && left.approve_enabled == right.approve_enabled
        && left.reject_enabled == right.reject_enabled
        && left.rework_enabled == right.rework_enabled
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

#[cfg(test)]
mod tests {
    use std::thread;
    use std::time::Duration;

    use chrono::Utc;

    use super::*;
    use crate::cleanroom::Cleanroom;
    use crate::cloud::MockCloudSummarizer;
    use crate::metadata::RuleBasedMetadata;
    use crate::model::{DocumentId, PacketId, RawDocument, ReviewQueueItem, SourceKind};
    use crate::preflight::RuleBasedPreflight;

    fn sample_review_item(status: QueueStatus, assigned_reviewer: Option<&str>) -> ReviewQueueItem {
        let now = Utc::now();
        ReviewQueueItem {
            packet_id: PacketId("pkt_review_policy".to_string()),
            document_id: DocumentId("doc_review_policy".to_string()),
            status,
            assigned_reviewer: assigned_reviewer.map(ToString::to_string),
            decision: None,
            notes: None,
            claimed_at: None,
            completed_at: None,
            created_at: now,
            updated_at: now,
        }
    }

    fn sample_gate() -> ReviewGateState {
        ReviewGateState {
            packet_id: PacketId("pkt_review_policy".to_string()),
            packet_version: "ver_review_policy".to_string(),
            required_fields_loaded: true,
            validation_status: "pass".to_string(),
            blocker_count: 0,
            diff_reviewed: true,
            evidence_reviewed: true,
            stale_flag: false,
            dirty_flag: false,
            active_diff_target_id: None,
            active_evidence_id: None,
            active_validation_issue_id: None,
            approve_enabled: true,
            reject_enabled: true,
            rework_enabled: true,
            updated_at: Utc::now(),
        }
    }

    #[test]
    fn approve_policy_blocks_stale_dirty_blockers_and_unreviewed_surfaces() {
        let review_item = sample_review_item(QueueStatus::InReview, Some("ace"));
        let mut gate = sample_gate();
        assert!(validate_terminal_action(
            ReviewActionKind::Approve,
            Some(&review_item),
            Some(&gate),
            Some("ace"),
        )
        .is_ok());

        gate.stale_flag = true;
        gate.approve_enabled = false;
        assert_eq!(
            validate_terminal_action(
                ReviewActionKind::Approve,
                Some(&review_item),
                Some(&gate),
                Some("ace"),
            ),
            Err(ReviewPreconditionKind::ApproveGateBlocked)
        );

        gate.stale_flag = false;
        gate.dirty_flag = true;
        assert_eq!(review_gate_label(&gate), "dirty_blocked");

        gate.dirty_flag = false;
        gate.blocker_count = 2;
        assert_eq!(review_gate_label(&gate), "blocked");

        gate.blocker_count = 0;
        gate.diff_reviewed = false;
        assert_eq!(review_gate_label(&gate), "needs_review");
    }

    #[test]
    fn reject_and_rework_policy_ignore_stale_dirty_and_blockers_once_claimed() {
        let review_item = sample_review_item(QueueStatus::InReview, Some("ace"));
        let mut gate = sample_gate();
        gate.approve_enabled = false;
        gate.stale_flag = true;
        gate.dirty_flag = true;
        gate.blocker_count = 3;

        let policy = review_action_policy(Some(&review_item), Some(&gate), Some("ace"));
        assert!(!policy.approve.enabled);
        assert!(policy.reject.enabled);
        assert!(policy.rework.enabled);
    }

    #[test]
    fn reject_and_rework_require_minimum_note_length_at_submit_time() {
        let review_item = sample_review_item(QueueStatus::InReview, Some("ace"));
        let gate = sample_gate();

        assert_eq!(
            validate_terminal_submission(
                ReviewActionKind::Reject,
                Some(&review_item),
                Some(&gate),
                Some("ace"),
                Some("too short"),
                24,
            ),
            Err(ReviewPreconditionKind::TerminalNoteTooShort)
        );

        assert!(validate_terminal_submission(
            ReviewActionKind::Approve,
            Some(&review_item),
            Some(&gate),
            Some("ace"),
            Some(""),
            24,
        )
        .is_ok());
    }

    #[test]
    fn pending_packets_show_claim_only_reasoning() {
        let review_item = sample_review_item(QueueStatus::Pending, None);
        let gate = sample_gate();
        let policy = review_action_policy(Some(&review_item), Some(&gate), Some("ace"));

        assert!(policy.claim.enabled);
        assert!(!policy.approve.enabled);
        assert_eq!(
            primary_disabled_reason(&policy, Some(&review_item)),
            "Claim this packet before approving, rejecting, or requesting rework."
        );
    }

    #[test]
    fn derived_review_version_is_stable_and_changes_with_reviewable_inputs() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let packet_id = build_pending_packet(&mut cleanroom);

        let initial = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        let repeated = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        assert_eq!(initial, repeated);
        assert!(initial.starts_with("ver_"));

        mutate_validation(&mut cleanroom.db, &packet_id);
        let after_validation = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        assert_ne!(initial, after_validation);

        mutate_result(&mut cleanroom.db, &packet_id);
        let after_result = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        assert_ne!(after_validation, after_result);

        mutate_packet(&mut cleanroom.db, &packet_id);
        let after_packet = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        assert_ne!(after_result, after_packet);

        mutate_lineage(&mut cleanroom.db, &packet_id);
        let after_lineage = derive_review_version(&cleanroom.db, &packet_id).unwrap();
        assert_ne!(after_packet, after_lineage);
    }

    #[test]
    fn refresh_review_gate_state_derives_stale_from_baseline_mismatch() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let packet_id = build_pending_packet(&mut cleanroom);

        let initial = refresh_review_gate_state(&mut cleanroom.db, &packet_id).unwrap();
        assert!(!initial.stale_flag);
        assert!(!initial.dirty_flag);

        mutate_validation(&mut cleanroom.db, &packet_id);
        let refreshed = refresh_review_gate_state(&mut cleanroom.db, &packet_id).unwrap();
        assert!(refreshed.stale_flag);
        assert!(!refreshed.dirty_flag);
        assert_eq!(refreshed.packet_version, initial.packet_version);
    }

    #[test]
    fn refresh_review_gate_state_derives_dirty_only_after_claim_time() {
        let mut cleanroom = Cleanroom::open(
            ":memory:",
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let packet_id = build_pending_packet(&mut cleanroom);
        let _ = refresh_review_gate_state(&mut cleanroom.db, &packet_id).unwrap();
        cleanroom.claim_next_review("ace").unwrap().unwrap();

        mutate_validation(&mut cleanroom.db, &packet_id);
        let refreshed = refresh_review_gate_state(&mut cleanroom.db, &packet_id).unwrap();
        assert!(refreshed.stale_flag);
        assert!(refreshed.dirty_flag);
    }

    fn build_pending_packet(
        cleanroom: &mut Cleanroom<RuleBasedPreflight, MockCloudSummarizer, RuleBasedMetadata>,
    ) -> PacketId {
        let packet_id = cleanroom
            .ingest_and_stage(RawDocument::new(
                "review drift doc",
                SourceKind::Document,
                None,
                format!("Intro.\n\n{}\n\nTODO: clarify.", "Dense text. ".repeat(100)),
            ))
            .unwrap();
        cleanroom.run_cloud(&packet_id).unwrap();
        packet_id
    }

    fn mutate_packet(db: &mut Database, packet_id: &PacketId) {
        let mut packet = db.load_packet(packet_id).unwrap().unwrap();
        thread::sleep(Duration::from_millis(2));
        packet.style_contract.push_str(" Updated.");
        db.save_packet(&packet).unwrap();
    }

    fn mutate_result(db: &mut Database, packet_id: &PacketId) {
        let packet = db.load_packet(packet_id).unwrap().unwrap();
        let mut result = db.load_result(packet_id).unwrap().unwrap();
        thread::sleep(Duration::from_millis(2));
        result.fragments[0]
            .unresolved_questions
            .push("new unresolved question".to_string());
        db.save_result(&packet.document_id, &result).unwrap();
    }

    fn mutate_validation(db: &mut Database, packet_id: &PacketId) {
        let packet = db.load_packet(packet_id).unwrap().unwrap();
        let mut validation = db.load_validation(packet_id).unwrap().unwrap();
        thread::sleep(Duration::from_millis(2));
        validation.issues.push(crate::model::ValidationIssue {
            issue_id: format!("issue_drift_{}", packet_id.0),
            severity: crate::model::ValidationSeverity::Warning,
            blocking: false,
            target_id: Some(packet.packet_id.0.clone()),
            message: "non-blocking validation drift".to_string(),
        });
        db.save_validation(&packet.document_id, &validation)
            .unwrap();
    }

    fn mutate_lineage(db: &mut Database, packet_id: &PacketId) {
        let successor = PacketId::new(&format!("{}-successor", packet_id.0));
        thread::sleep(Duration::from_millis(2));
        db.ensure_packet_lineage(&successor).unwrap();
        db.link_packet_successor(packet_id, &successor, None)
            .unwrap();
    }
}
