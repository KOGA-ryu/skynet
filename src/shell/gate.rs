use crate::model::ReviewGateState;
use crate::review::review_gate_label;
use crate::shell::api::GateDto;

pub fn gate_label(persisted: &ReviewGateState) -> String {
    review_gate_label(persisted).to_string()
}

pub fn gate_from_persisted(persisted: &ReviewGateState) -> GateDto {
    GateDto {
        source: "persisted".to_string(),
        label: gate_label(persisted),
        required_fields_loaded: persisted.required_fields_loaded,
        validation_status: persisted.validation_status.clone(),
        blocker_count: persisted.blocker_count,
        diff_reviewed: persisted.diff_reviewed,
        evidence_reviewed: persisted.evidence_reviewed,
        stale: persisted.stale_flag,
        dirty: persisted.dirty_flag,
        approve_enabled: persisted.approve_enabled,
        reject_enabled: persisted.reject_enabled,
        rework_enabled: persisted.rework_enabled,
        active_diff_target_id: persisted.active_diff_target_id.clone(),
        active_evidence_id: persisted.active_evidence_id.clone(),
        active_validation_issue_id: persisted.active_validation_issue_id.clone(),
        updated_at: persisted.updated_at.to_rfc3339(),
    }
}

pub fn disabled_gate(label: &str, source: &str) -> GateDto {
    GateDto {
        source: source.to_string(),
        label: label.to_string(),
        required_fields_loaded: false,
        validation_status: "unavailable".to_string(),
        blocker_count: 0,
        diff_reviewed: false,
        evidence_reviewed: false,
        stale: false,
        dirty: false,
        approve_enabled: false,
        reject_enabled: false,
        rework_enabled: false,
        active_diff_target_id: None,
        active_evidence_id: None,
        active_validation_issue_id: None,
        updated_at: String::new(),
    }
}
