use serde::Serialize;
use serde_json::{json, Value};

use crate::cleanroom::{review_notes_min_chars, Cleanroom};
use crate::cloud::MockCloudSummarizer;
use crate::error::PipelineError;
use crate::metadata::RuleBasedMetadata;
use crate::model::{PacketId, ReviewDecision};
use crate::preflight::RuleBasedPreflight;
use crate::review::{
    precondition_reason_text, refresh_review_gate_state, validate_claim_action,
    validate_terminal_submission, ReviewActionKind, ReviewPreconditionKind,
};
use crate::shell::api::{
    milestone_capabilities, ActionReceiptDto, ApprovePacketParams, ClaimStoragePacketParams,
    ClaimStoragePacketResult, GetStorageViewParams, InitializeParams, InitializeResult,
    JsonRpcError, JsonRpcErrorData, JsonRpcRequest, JsonRpcResponse, PacketDecisionResult,
    RefreshStorageViewParams, RejectPacketParams, ReviewNotePolicyDto, ReviewerIdentityDto,
    ReworkPacketParams, CLIENT_NAME, DTO_VERSION, ERROR_CODE_INTERNAL_SERVICE_ERROR,
    ERROR_CODE_REVIEW_PRECONDITION, ERROR_CODE_SERVICE_NOT_INITIALIZED,
    ERROR_CODE_STORAGE_UNAVAILABLE, ERROR_CODE_UNSUPPORTED_DTO_VERSION,
    ERROR_CODE_UNSUPPORTED_PROTOCOL_VERSION, INTERACTION_MODE_DISPLAY_ONLY, PROTOCOL_VERSION,
    TRANSPORT_NAME,
};
use crate::shell::runtime_fixture_app::fixture_runtime_view;
use crate::shell::runtime_storage_app::storage_runtime_view;
use crate::storage::Database;

pub struct ShellService {
    initialized: bool,
    storage_path: String,
    service_version: String,
    reviewer_identity: ReviewerIdentityDto,
    last_action_receipt: Option<ActionReceiptDto>,
}

impl ShellService {
    pub fn new(storage_path: impl Into<String>) -> Self {
        Self {
            initialized: false,
            storage_path: storage_path.into(),
            service_version: env!("CARGO_PKG_VERSION").to_string(),
            reviewer_identity: missing_reviewer_identity(),
            last_action_receipt: None,
        }
    }

    pub fn service_version(&self) -> &str {
        &self.service_version
    }

    pub fn handle_request(&mut self, request: JsonRpcRequest) -> JsonRpcResponse {
        let id = request.id;
        match request.method.as_str() {
            "shell.initialize" => match parse_params::<InitializeParams>(request.params) {
                Ok(params) => self.handle_initialize(id, params),
                Err(err) => self.internal_error(id, err),
            },
            _ if !self.initialized => self.error_response(
                id,
                ERROR_CODE_SERVICE_NOT_INITIALIZED,
                "service_not_initialized",
                "Shell service is not initialized.",
                false,
                json!({"expected_first_method": "shell.initialize"}),
            ),
            "shell.get_fixture_view" => {
                self.serialize_result(id, fixture_runtime_view(&self.service_version))
            }
            "shell.get_storage_view" => {
                match parse_params::<GetStorageViewParams>(request.params) {
                    Ok(params) => self.handle_storage_view(id, params.packet_id, None),
                    Err(err) => self.internal_error(id, err),
                }
            }
            "shell.refresh_storage_view" => {
                match parse_params::<RefreshStorageViewParams>(request.params) {
                    Ok(params) => {
                        self.handle_storage_view(id, params.packet_id, params.last_view_revision)
                    }
                    Err(err) => self.internal_error(id, err),
                }
            }
            "shell.claim_storage_packet" => {
                match parse_params::<ClaimStoragePacketParams>(request.params) {
                    Ok(params) => self.handle_claim_storage_packet(id, params),
                    Err(err) => self.internal_error(id, err),
                }
            }
            "shell.approve_packet" => match parse_params::<ApprovePacketParams>(request.params) {
                Ok(params) => self.handle_approve_packet(id, params),
                Err(err) => self.internal_error(id, err),
            },
            "shell.reject_packet" => match parse_params::<RejectPacketParams>(request.params) {
                Ok(params) => self.handle_reject_packet(id, params),
                Err(err) => self.internal_error(id, err),
            },
            "shell.rework_packet" => match parse_params::<ReworkPacketParams>(request.params) {
                Ok(params) => self.handle_rework_packet(id, params),
                Err(err) => self.internal_error(id, err),
            },
            _ => self.error_response(
                id,
                ERROR_CODE_INTERNAL_SERVICE_ERROR,
                "internal_service_error",
                "Unknown shell method.",
                false,
                json!({"method": request.method}),
            ),
        }
    }

    fn handle_initialize(&mut self, id: u64, params: InitializeParams) -> JsonRpcResponse {
        if params.protocol_version != PROTOCOL_VERSION {
            return self.error_response(
                id,
                ERROR_CODE_UNSUPPORTED_PROTOCOL_VERSION,
                "unsupported_protocol_version",
                "Unsupported shell protocol version.",
                false,
                json!({"requested_protocol_version": params.protocol_version}),
            );
        }
        if params.dto_version != DTO_VERSION {
            return self.error_response(
                id,
                ERROR_CODE_UNSUPPORTED_DTO_VERSION,
                "unsupported_dto_version",
                "Unsupported shell DTO version.",
                false,
                json!({"requested_dto_version": params.dto_version}),
            );
        }
        if params.client_name != CLIENT_NAME {
            return self.error_response(
                id,
                ERROR_CODE_INTERNAL_SERVICE_ERROR,
                "internal_service_error",
                "Unexpected shell client name.",
                false,
                json!({"client_name": params.client_name}),
            );
        }
        self.reviewer_identity = reviewer_identity_from_initialize(params.reviewer_name);
        self.last_action_receipt = None;
        self.initialized = true;
        self.serialize_result(
            id,
            InitializeResult {
                protocol_version: PROTOCOL_VERSION.to_string(),
                dto_version: DTO_VERSION.to_string(),
                service_version: self.service_version.clone(),
                capabilities: milestone_capabilities(),
                interaction_mode: INTERACTION_MODE_DISPLAY_ONLY.to_string(),
                transport: TRANSPORT_NAME.to_string(),
                reviewer_identity: self.reviewer_identity.clone(),
                review_note_policy: ReviewNotePolicyDto {
                    approve_note_required: false,
                    terminal_note_min_chars: review_notes_min_chars(),
                },
            },
        )
    }

    fn handle_storage_view(
        &self,
        id: u64,
        packet_id: Option<String>,
        last_view_revision: Option<String>,
    ) -> JsonRpcResponse {
        let mut db = match Database::open(&self.storage_path) {
            Ok(db) => db,
            Err(err) => return self.storage_error(id, err),
        };
        match storage_runtime_view(
            &mut db,
            &self.service_version,
            self.reviewer_identity.clone(),
            self.last_action_receipt.clone(),
            packet_id.as_deref(),
            last_view_revision.as_deref(),
        ) {
            Ok(view) => self.serialize_result(id, view),
            Err(err) => self.storage_error(id, err),
        }
    }

    fn handle_claim_storage_packet(
        &mut self,
        id: u64,
        params: ClaimStoragePacketParams,
    ) -> JsonRpcResponse {
        let packet_id = PacketId(params.packet_id);
        let reviewer = self.reviewer_name().map(ToString::to_string);
        let db = match Database::open(&self.storage_path) {
            Ok(db) => db,
            Err(err) => return self.storage_error(id, err),
        };
        let review_item = match db.load_review_item(&packet_id) {
            Ok(item) => item,
            Err(err) => return self.storage_error(id, err),
        };
        if let Err(kind) = validate_claim_action(review_item.as_ref(), reviewer.as_deref()) {
            return self.review_precondition_response_for_kind(
                id,
                kind,
                &packet_id,
                review_item.as_ref(),
                None,
            );
        }
        let reviewer = reviewer.expect("validated reviewer identity must be present");
        let mut cleanroom = match self.open_shell_cleanroom() {
            Ok(cleanroom) => cleanroom,
            Err(err) => return self.storage_error(id, err),
        };
        match cleanroom.claim_review_packet(&packet_id, &reviewer) {
            Ok(claimed) => self.serialize_result(
                id,
                ClaimStoragePacketResult {
                    packet_id: claimed.packet_id.0,
                    queue_status: claimed.status.as_str().to_string(),
                    assigned_reviewer: claimed
                        .assigned_reviewer
                        .unwrap_or_else(|| reviewer.clone()),
                    claimed_at: claimed
                        .claimed_at
                        .expect("claimed review item must include claimed_at")
                        .to_rfc3339(),
                },
            ),
            Err(PipelineError::Review(message)) => {
                if let Some(kind) = ReviewPreconditionKind::from_code(&message) {
                    self.review_precondition_response_for_kind(
                        id,
                        kind,
                        &packet_id,
                        review_item.as_ref(),
                        None,
                    )
                } else {
                    self.storage_error(id, PipelineError::Review(message))
                }
            }
            Err(err) => self.storage_error(id, err),
        }
    }

    fn handle_approve_packet(&mut self, id: u64, params: ApprovePacketParams) -> JsonRpcResponse {
        let packet_id = PacketId(params.packet_id);
        if let Err(response) = self.ensure_terminal_action_allowed(
            id,
            ReviewActionKind::Approve,
            &packet_id,
            params.notes.as_deref(),
        ) {
            return response;
        }
        let mut cleanroom = match self.open_shell_cleanroom() {
            Ok(cleanroom) => cleanroom,
            Err(err) => return self.storage_error(id, err),
        };
        match cleanroom.approve_packet(&packet_id, params.notes.as_deref()) {
            Ok(_) => match self.terminal_success_result(
                &cleanroom.db,
                &packet_id,
                ReviewDecision::Approve,
            ) {
                Ok(result) => self.serialize_result(id, result),
                Err(err) => self.storage_error(id, err),
            },
            Err(err) => self.handle_terminal_mutation_error(id, &packet_id, err),
        }
    }

    fn handle_reject_packet(&mut self, id: u64, params: RejectPacketParams) -> JsonRpcResponse {
        let packet_id = PacketId(params.packet_id);
        if let Err(response) = self.ensure_terminal_action_allowed(
            id,
            ReviewActionKind::Reject,
            &packet_id,
            Some(&params.notes),
        ) {
            return response;
        }
        let mut cleanroom = match self.open_shell_cleanroom() {
            Ok(cleanroom) => cleanroom,
            Err(err) => return self.storage_error(id, err),
        };
        match cleanroom.reject_packet(&packet_id, &params.notes) {
            Ok(()) => match self.terminal_success_result(
                &cleanroom.db,
                &packet_id,
                ReviewDecision::Reject,
            ) {
                Ok(result) => self.serialize_result(id, result),
                Err(err) => self.storage_error(id, err),
            },
            Err(err) => self.handle_terminal_mutation_error(id, &packet_id, err),
        }
    }

    fn handle_rework_packet(&mut self, id: u64, params: ReworkPacketParams) -> JsonRpcResponse {
        let packet_id = PacketId(params.packet_id);
        if let Err(response) = self.ensure_terminal_action_allowed(
            id,
            ReviewActionKind::Rework,
            &packet_id,
            Some(&params.notes),
        ) {
            return response;
        }
        let mut cleanroom = match self.open_shell_cleanroom() {
            Ok(cleanroom) => cleanroom,
            Err(err) => return self.storage_error(id, err),
        };
        match cleanroom.request_rework(&packet_id, &params.notes) {
            Ok(()) => match self.terminal_success_result(
                &cleanroom.db,
                &packet_id,
                ReviewDecision::Rework,
            ) {
                Ok(result) => self.serialize_result(id, result),
                Err(err) => self.storage_error(id, err),
            },
            Err(err) => self.handle_terminal_mutation_error(id, &packet_id, err),
        }
    }

    fn reviewer_name(&self) -> Option<&str> {
        self.reviewer_identity.reviewer_name.as_deref()
    }

    fn open_shell_cleanroom(
        &self,
    ) -> Result<Cleanroom<RuleBasedPreflight, MockCloudSummarizer, RuleBasedMetadata>, PipelineError>
    {
        Cleanroom::open(
            &self.storage_path,
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
    }

    fn ensure_terminal_action_allowed(
        &self,
        id: u64,
        action: ReviewActionKind,
        packet_id: &PacketId,
        notes: Option<&str>,
    ) -> Result<(), JsonRpcResponse> {
        let reviewer = self.reviewer_name();
        let mut db = match Database::open(&self.storage_path) {
            Ok(db) => db,
            Err(err) => return Err(self.storage_error(id, err)),
        };
        let review_item = match db.load_review_item(packet_id) {
            Ok(item) => item,
            Err(err) => return Err(self.storage_error(id, err)),
        };
        let gate_state = match refresh_review_gate_state(&mut db, packet_id) {
            Ok(state) => Some(state),
            Err(err) => return Err(self.storage_error(id, err)),
        };
        match validate_terminal_submission(
            action,
            review_item.as_ref(),
            gate_state.as_ref(),
            reviewer,
            notes,
            review_notes_min_chars(),
        ) {
            Ok(()) => Ok(()),
            Err(kind) => Err(self.review_precondition_response_for_kind(
                id,
                kind,
                packet_id,
                review_item.as_ref(),
                gate_state.as_ref(),
            )),
        }
    }

    fn terminal_success_result(
        &mut self,
        db: &Database,
        packet_id: &PacketId,
        decision: ReviewDecision,
    ) -> Result<PacketDecisionResult, PipelineError> {
        let review_item = db.load_review_item(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review item {} not found", packet_id.0))
        })?;
        let review_artifact = db.load_review_artifact(packet_id)?.ok_or_else(|| {
            PipelineError::NotFound(format!("review artifact {} not found", packet_id.0))
        })?;
        let receipt = ActionReceiptDto {
            packet_id: packet_id.0.clone(),
            decision: decision_name(&decision).to_string(),
            reviewer: review_artifact.reviewer.clone(),
            timestamp: review_artifact.created_at.to_rfc3339(),
        };
        self.last_action_receipt = Some(receipt.clone());
        Ok(PacketDecisionResult {
            packet_id: packet_id.0.clone(),
            decision: receipt.decision,
            reviewer: receipt.reviewer,
            timestamp: receipt.timestamp,
            queue_status: review_item.status.as_str().to_string(),
        })
    }

    fn handle_terminal_mutation_error(
        &self,
        id: u64,
        packet_id: &PacketId,
        err: PipelineError,
    ) -> JsonRpcResponse {
        match err {
            PipelineError::Review(message) => {
                let review_item = Database::open(&self.storage_path)
                    .ok()
                    .and_then(|db| db.load_review_item(packet_id).ok())
                    .flatten();
                let gate_state = Database::open(&self.storage_path)
                    .ok()
                    .and_then(|db| db.load_review_gate_state(packet_id).ok())
                    .flatten();
                if let Some(kind) = ReviewPreconditionKind::from_code(&message) {
                    self.review_precondition_response_for_kind(
                        id,
                        kind,
                        packet_id,
                        review_item.as_ref(),
                        gate_state.as_ref(),
                    )
                } else {
                    self.storage_error(id, PipelineError::Review(message))
                }
            }
            PipelineError::NotFound(_) => self.review_precondition_response(
                id,
                "packet_missing",
                "Requested packet is not present in the review queue.",
                json!({ "packet_id": packet_id.0 }),
            ),
            other => self.storage_error(id, other),
        }
    }

    fn review_precondition_response_for_kind(
        &self,
        id: u64,
        kind: ReviewPreconditionKind,
        packet_id: &PacketId,
        review_item: Option<&crate::model::ReviewQueueItem>,
        gate_state: Option<&crate::model::ReviewGateState>,
    ) -> JsonRpcResponse {
        let mut details = serde_json::Map::new();
        details.insert("packet_id".to_string(), Value::String(packet_id.0.clone()));
        details.insert(
            "operator_message".to_string(),
            Value::String(precondition_reason_text(
                ReviewActionKind::Approve,
                kind,
                review_item,
                gate_state,
            )),
        );
        if let Some(item) = review_item {
            details.insert(
                "queue_status".to_string(),
                Value::String(item.status.as_str().to_string()),
            );
            if let Some(assigned) = &item.assigned_reviewer {
                details.insert(
                    "assigned_reviewer".to_string(),
                    Value::String(assigned.clone()),
                );
            }
        }
        if let Some(reviewer_name) = self.reviewer_name() {
            details.insert(
                "reviewer_name".to_string(),
                Value::String(reviewer_name.to_string()),
            );
        }
        if let Some(gate_state) = gate_state {
            details.insert(
                "gate_label".to_string(),
                Value::String(crate::review::review_gate_label(gate_state).to_string()),
            );
        }
        if kind == ReviewPreconditionKind::TerminalNoteTooShort {
            details.insert(
                "terminal_note_min_chars".to_string(),
                Value::from(review_notes_min_chars()),
            );
        }
        self.review_precondition_response(
            id,
            kind.as_str(),
            &precondition_reason_text(ReviewActionKind::Approve, kind, review_item, gate_state),
            Value::Object(details),
        )
    }

    fn storage_error(&self, id: u64, err: PipelineError) -> JsonRpcResponse {
        self.error_response(
            id,
            ERROR_CODE_STORAGE_UNAVAILABLE,
            "storage_unavailable",
            "Shell storage is unavailable.",
            true,
            json!({ "reason": err.to_string() }),
        )
    }

    fn review_precondition_response(
        &self,
        id: u64,
        reason_kind: &str,
        message: &str,
        details: Value,
    ) -> JsonRpcResponse {
        let mut details_with_reason = match details {
            Value::Object(map) => map,
            other => {
                let mut map = serde_json::Map::new();
                map.insert("detail".to_string(), other);
                map
            }
        };
        details_with_reason.insert(
            "reason_kind".to_string(),
            Value::String(reason_kind.to_string()),
        );
        self.error_response(
            id,
            ERROR_CODE_REVIEW_PRECONDITION,
            "review_precondition_failed",
            message,
            false,
            Value::Object(details_with_reason),
        )
    }

    fn internal_error(&self, id: u64, err: impl ToString) -> JsonRpcResponse {
        self.error_response(
            id,
            ERROR_CODE_INTERNAL_SERVICE_ERROR,
            "internal_service_error",
            "Shell service failed to process the request.",
            false,
            json!({ "reason": err.to_string() }),
        )
    }

    fn serialize_result<T: Serialize>(&self, id: u64, value: T) -> JsonRpcResponse {
        match serde_json::to_value(value) {
            Ok(result) => JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id,
                result: Some(result),
                error: None,
            },
            Err(err) => self.internal_error(id, err),
        }
    }

    fn error_response(
        &self,
        id: u64,
        code: i64,
        error_kind: &str,
        message: &str,
        retryable: bool,
        details: Value,
    ) -> JsonRpcResponse {
        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id,
            result: None,
            error: Some(JsonRpcError {
                code,
                message: message.to_string(),
                data: JsonRpcErrorData {
                    error_kind: error_kind.to_string(),
                    protocol_version: PROTOCOL_VERSION.to_string(),
                    dto_version: DTO_VERSION.to_string(),
                    service_version: self.service_version.clone(),
                    retryable,
                    details,
                },
            }),
        }
    }
}

fn reviewer_identity_from_initialize(reviewer_name: Option<String>) -> ReviewerIdentityDto {
    reviewer_name
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
        .map(|reviewer_name| ReviewerIdentityDto {
            status: "present".to_string(),
            reviewer_name: Some(reviewer_name),
            source: "client_env".to_string(),
        })
        .unwrap_or_else(missing_reviewer_identity)
}

fn missing_reviewer_identity() -> ReviewerIdentityDto {
    ReviewerIdentityDto {
        status: "missing".to_string(),
        reviewer_name: None,
        source: "missing".to_string(),
    }
}

fn decision_name(decision: &ReviewDecision) -> &'static str {
    match decision {
        ReviewDecision::Approve => "approve",
        ReviewDecision::Reject => "reject",
        ReviewDecision::Rework => "rework",
    }
}

fn parse_params<T: serde::de::DeserializeOwned>(
    params: Option<Value>,
) -> Result<T, serde_json::Error> {
    serde_json::from_value(params.unwrap_or_else(|| json!({})))
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::thread;
    use std::time::Duration;
    use std::time::{SystemTime, UNIX_EPOCH};

    use serde_json::json;

    use crate::cleanroom::Cleanroom;
    use crate::cloud::MockCloudSummarizer;
    use crate::metadata::RuleBasedMetadata;
    use crate::model::{RawDocument, SourceKind};
    use crate::preflight::RuleBasedPreflight;
    use crate::shell::api::{
        ERROR_CODE_REVIEW_PRECONDITION, ERROR_CODE_SERVICE_NOT_INITIALIZED,
        ERROR_CODE_UNSUPPORTED_PROTOCOL_VERSION,
    };
    use crate::storage::Database;

    use super::ShellService;

    #[test]
    fn initialize_is_required_before_other_methods() {
        let mut service = ShellService::new(":memory:");
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 1,
            method: "shell.get_fixture_view".to_string(),
            params: None,
        });
        let error = response.error.expect("error response");
        assert_eq!(error.code, ERROR_CODE_SERVICE_NOT_INITIALIZED);
        assert_eq!(error.data.error_kind, "service_not_initialized");
    }

    #[test]
    fn version_mismatch_returns_structured_error() {
        let mut service = ShellService::new(":memory:");
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 1,
            method: "shell.initialize".to_string(),
            params: Some(json!({
                "protocol_version": "9.9",
                "dto_version": "1.0",
                "client_name": "skynet_qt_shell",
                "client_version": "0.1.0",
                "requested_capabilities": ["fixture_view"]
            })),
        });
        let error = response.error.expect("error response");
        assert_eq!(error.code, ERROR_CODE_UNSUPPORTED_PROTOCOL_VERSION);
        assert_eq!(error.data.protocol_version, "1.0");
        assert_eq!(error.data.dto_version, "1.0");
        assert_eq!(error.data.service_version, service.service_version());
        assert!(!error.data.retryable);
    }

    #[test]
    fn initialize_and_fixture_view_succeed() {
        let mut service = ShellService::new(":memory:");
        let response = initialize(&mut service);
        assert!(response.error.is_none());
        let result = response.result.as_ref().expect("initialize result");
        assert_eq!(result["reviewer_identity"]["status"], "missing");
        assert_eq!(result["review_note_policy"]["terminal_note_min_chars"], 24);

        let fixture = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 2,
            method: "shell.get_fixture_view".to_string(),
            params: None,
        });
        let result = fixture.result.expect("fixture result");
        assert_eq!(result["source_mode"], "fixture");
        assert_eq!(result["view_state"], "ready");
    }

    #[test]
    fn storage_view_selects_requested_packet() {
        let db_path = temp_db_path("shell_service_storage_select");
        let packet_id = build_reviewable_db(&db_path);

        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize(&mut service);
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 3,
            method: "shell.get_storage_view".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let result = response.result.expect("storage result");
        assert_eq!(result["source_mode"], "storage");
        assert_eq!(result["selection_reason"], "requested_packet");
        fs::remove_file(db_path).ok();
    }

    #[test]
    fn initialize_records_present_reviewer_identity() {
        let mut service = ShellService::new(":memory:");
        let response = initialize_with_reviewer(&mut service, Some("ace"));
        let result = response.result.expect("initialize result");
        assert_eq!(result["reviewer_identity"]["status"], "present");
        assert_eq!(result["reviewer_identity"]["reviewer_name"], "ace");
        assert_eq!(result["reviewer_identity"]["source"], "client_env");
    }

    #[test]
    fn claim_storage_packet_requires_reviewer_identity() {
        let db_path = temp_db_path("shell_service_claim_identity");
        let packet_id = build_reviewable_db(&db_path);
        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize(&mut service);
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 4,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let error = response.error.expect("claim error");
        assert_eq!(error.code, ERROR_CODE_REVIEW_PRECONDITION);
        assert_eq!(error.data.error_kind, "review_precondition_failed");
        assert_eq!(
            error.data.details["reason_kind"],
            "reviewer_identity_missing"
        );
        fs::remove_file(db_path).ok();
    }

    #[test]
    fn claim_storage_packet_updates_queue_assignment() {
        let db_path = temp_db_path("shell_service_claim_success");
        let packet_id = build_pending_db(&db_path).remove(0);
        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut service, Some("ace"));
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 5,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let result = response.result.expect("claim result");
        assert_eq!(result["queue_status"], "in_review");
        assert_eq!(result["assigned_reviewer"], "ace");
        let db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();
        let gate_state = db
            .load_review_gate_state(&crate::model::PacketId(packet_id.clone()))
            .unwrap()
            .unwrap();
        assert!(gate_state.diff_reviewed);
        assert!(gate_state.evidence_reviewed);
        assert!(gate_state.approve_enabled);
        let view = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 6,
            method: "shell.get_storage_view".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let view_result = view.result.expect("storage view");
        assert_eq!(view_result["gate"]["label"], "review_ready");
        assert_eq!(
            view_result["right_inspector"]["review_actions"]["approve_enabled"],
            true
        );
        fs::remove_file(db_path).ok();
    }

    #[test]
    fn terminal_actions_fail_when_packet_is_not_claimed_by_session_reviewer() {
        let db_path = temp_db_path("shell_service_claim_mismatch");
        let packet_id = build_reviewable_db(&db_path);

        let mut other_service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut other_service, Some("bea"));
        let _claim = other_service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 6,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });

        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut service, Some("ace"));
        let response = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 7,
            method: "shell.approve_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let error = response.error.expect("approve error");
        assert_eq!(error.code, ERROR_CODE_REVIEW_PRECONDITION);
        assert_eq!(
            error.data.details["reason_kind"],
            "claimed_by_other_reviewer"
        );
        assert_eq!(error.data.details["assigned_reviewer"], "bea");
        fs::remove_file(db_path).ok();
    }

    #[test]
    fn terminal_action_sets_receipt_and_advances_to_next_pending_packet() {
        let db_path = temp_db_path("shell_service_terminal_receipt");
        let packet_ids = build_pending_db(&db_path);
        let target_packet_id = packet_ids[0].clone();
        let next_packet_id = packet_ids[1].clone();
        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut service, Some("ace"));

        let claim = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 8,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": target_packet_id })),
        });
        assert!(claim.error.is_none());

        let approve = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 9,
            method: "shell.approve_packet".to_string(),
            params: Some(json!({ "packet_id": target_packet_id, "notes": null })),
        });
        let approve_result = approve.result.expect("approve result");
        assert_eq!(approve_result["reviewer"], "ace");
        assert_eq!(approve_result["queue_status"], "approved");

        let db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();
        let artifact = db
            .load_review_artifact(&crate::model::PacketId(target_packet_id.clone()))
            .unwrap()
            .unwrap();
        assert_eq!(artifact.reviewer, "ace");

        let view = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 10,
            method: "shell.get_storage_view".to_string(),
            params: Some(json!({})),
        });
        let result = view.result.expect("storage view");
        assert_eq!(result["active_packet_id"], next_packet_id);
        assert_eq!(
            result["status"]["last_action_receipt"]["packet_id"],
            target_packet_id
        );
        assert_eq!(
            result["status"]["last_action_receipt"]["decision"],
            "approve"
        );

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn approve_returns_structured_gate_block_reason_and_reject_can_still_succeed() {
        let db_path = temp_db_path("shell_service_gate_block");
        let packet_id = build_pending_db(&db_path).remove(0);
        inject_nonblocking_validation_drift(&db_path, &packet_id);
        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut service, Some("ace"));
        let claim = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 11,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        assert!(claim.error.is_none());

        let approve = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 12,
            method: "shell.approve_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let error = approve.error.expect("approve gate error");
        assert_eq!(error.data.details["reason_kind"], "approve_gate_blocked");
        assert_eq!(error.data.details["gate_label"], "stale_blocked");

        let reject = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 13,
            method: "shell.reject_packet".to_string(),
            params: Some(json!({
                "packet_id": packet_id,
                "notes": "Rejecting this packet because the stale flag still blocks approval.",
            })),
        });
        let result = reject.result.expect("reject result");
        assert_eq!(result["queue_status"], "rejected");
        fs::remove_file(db_path).ok();
    }

    #[test]
    fn claim_returns_packet_not_pending_and_terminal_notes_are_enforced() {
        let db_path = temp_db_path("shell_service_precondition_reasons");
        let packet_id = build_pending_db(&db_path).remove(0);

        let mut service = ShellService::new(db_path.to_string_lossy().to_string());
        initialize_with_reviewer(&mut service, Some("ace"));
        let claim = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 14,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        assert!(claim.error.is_none());

        let second_claim = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 15,
            method: "shell.claim_storage_packet".to_string(),
            params: Some(json!({ "packet_id": packet_id })),
        });
        let second_claim_error = second_claim.error.expect("claim precondition error");
        assert_eq!(
            second_claim_error.data.details["reason_kind"],
            "packet_not_pending"
        );

        let short_note = service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 16,
            method: "shell.rework_packet".to_string(),
            params: Some(json!({
                "packet_id": packet_id,
                "notes": "too short",
            })),
        });
        let short_note_error = short_note.error.expect("note policy error");
        assert_eq!(
            short_note_error.data.details["reason_kind"],
            "terminal_note_too_short"
        );
        assert_eq!(short_note_error.data.details["terminal_note_min_chars"], 24);
        fs::remove_file(db_path).ok();
    }

    fn initialize(service: &mut ShellService) -> crate::shell::api::JsonRpcResponse {
        initialize_with_reviewer(service, None)
    }

    fn initialize_with_reviewer(
        service: &mut ShellService,
        reviewer_name: Option<&str>,
    ) -> crate::shell::api::JsonRpcResponse {
        service.handle_request(crate::shell::api::JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 1,
            method: "shell.initialize".to_string(),
            params: Some(json!({
                "protocol_version": "1.0",
                "dto_version": "1.0",
                "client_name": "skynet_qt_shell",
                "client_version": "0.1.0",
                "requested_capabilities": [
                    "fixture_view",
                    "storage_view",
                    "poll_refresh",
                    "display_only",
                    "fixed_collapse_geometry"
                ],
                "reviewer_name": reviewer_name,
            })),
        })
    }

    fn temp_db_path(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("{name}_{unique}.db"))
    }

    fn build_reviewable_db(path: &std::path::Path) -> String {
        build_pending_db(path).remove(0)
    }

    fn build_pending_db(path: &std::path::Path) -> Vec<String> {
        let mut cleanroom = Cleanroom::open(
            path.to_string_lossy().as_ref(),
            RuleBasedPreflight,
            MockCloudSummarizer,
            RuleBasedMetadata,
        )
        .unwrap();
        let mut packet_ids = Vec::new();
        for index in 0..2 {
            let packet_id = cleanroom
                .ingest_and_stage(RawDocument::new(
                    format!("doc_{index}"),
                    SourceKind::Document,
                    None,
                    format!(
                        "Intro {index}.\n\n{}\n\nTODO: clarify unique {}.",
                        "Dense text. ".repeat(100),
                        index
                    ),
                ))
                .unwrap();
            cleanroom.run_cloud(&packet_id).unwrap();
            packet_ids.push(packet_id.0);
        }
        packet_ids
    }

    fn inject_nonblocking_validation_drift(path: &std::path::Path, packet_id: &str) {
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
