use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PROTOCOL_VERSION: &str = "1.0";
pub const DTO_VERSION: &str = "1.0";
pub const CLIENT_NAME: &str = "skynet_qt_shell";
pub const TRANSPORT_NAME: &str = "jsonrpc_stdio_content_length";
pub const INTERACTION_MODE_DISPLAY_ONLY: &str = "display_only";
pub const REFRESH_INTERVAL_MS: u32 = 2000;
pub const REVIEWER_IDENTITY_MISSING_MESSAGE: &str =
    "Set SKYNET_REVIEWER before claiming or completing review actions.";

pub const CAPABILITY_FIXTURE_VIEW: &str = "fixture_view";
pub const CAPABILITY_STORAGE_VIEW: &str = "storage_view";
pub const CAPABILITY_POLL_REFRESH: &str = "poll_refresh";
pub const CAPABILITY_DISPLAY_ONLY: &str = "display_only";
pub const CAPABILITY_FIXED_COLLAPSE_GEOMETRY: &str = "fixed_collapse_geometry";

pub const ERROR_CODE_SERVICE_NOT_INITIALIZED: i64 = -32010;
pub const ERROR_CODE_UNSUPPORTED_PROTOCOL_VERSION: i64 = -32011;
pub const ERROR_CODE_UNSUPPORTED_DTO_VERSION: i64 = -32012;
pub const ERROR_CODE_STORAGE_UNAVAILABLE: i64 = -32020;
pub const ERROR_CODE_INTERNAL_SERVICE_ERROR: i64 = -32030;
pub const ERROR_CODE_REVIEW_PRECONDITION: i64 = -32040;

pub fn milestone_capabilities() -> Vec<String> {
    vec![
        CAPABILITY_FIXTURE_VIEW.to_string(),
        CAPABILITY_STORAGE_VIEW.to_string(),
        CAPABILITY_POLL_REFRESH.to_string(),
        CAPABILITY_DISPLAY_ONLY.to_string(),
        CAPABILITY_FIXED_COLLAPSE_GEOMETRY.to_string(),
    ]
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub params: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    pub data: JsonRpcErrorData,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcErrorData {
    pub error_kind: String,
    pub protocol_version: String,
    pub dto_version: String,
    pub service_version: String,
    pub retryable: bool,
    pub details: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct InitializeParams {
    pub protocol_version: String,
    pub dto_version: String,
    pub client_name: String,
    pub client_version: String,
    pub requested_capabilities: Vec<String>,
    #[serde(default)]
    pub reviewer_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct InitializeResult {
    pub protocol_version: String,
    pub dto_version: String,
    pub service_version: String,
    pub capabilities: Vec<String>,
    pub interaction_mode: String,
    pub transport: String,
    pub reviewer_identity: ReviewerIdentityDto,
    pub review_note_policy: ReviewNotePolicyDto,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct GetStorageViewParams {
    pub packet_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct RefreshStorageViewParams {
    pub packet_id: Option<String>,
    pub last_view_revision: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaimStoragePacketParams {
    pub packet_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaimStoragePacketResult {
    pub packet_id: String,
    pub queue_status: String,
    pub assigned_reviewer: String,
    pub claimed_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ApprovePacketParams {
    pub packet_id: String,
    #[serde(default)]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RejectPacketParams {
    pub packet_id: String,
    pub notes: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReworkPacketParams {
    pub packet_id: String,
    pub notes: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PacketDecisionResult {
    pub packet_id: String,
    pub decision: String,
    pub reviewer: String,
    pub timestamp: String,
    pub queue_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ShellViewDto {
    pub protocol_version: String,
    pub dto_version: String,
    pub service_version: String,
    pub source_mode: String,
    pub view_state: String,
    pub view_revision: String,
    pub interaction_mode: String,
    pub active_packet_id: Option<String>,
    pub requested_packet_id: Option<String>,
    pub selection_reason: String,
    pub frame: FrameDto,
    pub status: StatusDto,
    pub tabs: TabsDto,
    pub gate: GateDto,
    pub left_rail: LeftRailPaneDto,
    pub center_surface: CenterSurfacePaneDto,
    pub right_inspector: RightInspectorPaneDto,
    pub bottom_strip: BottomStripPaneDto,
    pub error: ViewErrorDto,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct FrameDto {
    pub app_min_width_px: u16,
    pub app_min_height_px: u16,
    pub status_strip_height_px: u16,
    pub tab_rail_height_px: u16,
    pub left_rail_default_width_px: u16,
    pub left_rail_collapsed_width_px: u16,
    pub left_rail_min_width_px: u16,
    pub right_inspector_default_width_px: u16,
    pub right_inspector_collapsed_width_px: u16,
    pub right_inspector_min_width_px: u16,
    pub bottom_strip_default_height_px: u16,
    pub bottom_strip_collapsed_height_px: u16,
    pub bottom_strip_min_height_px: u16,
    pub center_min_width_px: u16,
    pub center_min_height_px: u16,
    pub refresh_interval_ms: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StatusDto {
    pub title: String,
    pub detail: String,
    pub source_label: String,
    pub last_updated_at: String,
    pub queue_status: Option<String>,
    pub assigned_reviewer: Option<String>,
    pub reviewer_identity: ReviewerIdentityDto,
    pub last_action_receipt: Option<ActionReceiptDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReviewerIdentityDto {
    pub status: String,
    pub reviewer_name: Option<String>,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReviewNotePolicyDto {
    pub approve_note_required: bool,
    pub terminal_note_min_chars: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ActionReceiptDto {
    pub packet_id: String,
    pub decision: String,
    pub reviewer: String,
    pub timestamp: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TabsDto {
    pub active_tab_id: String,
    pub items: Vec<TabItemDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TabItemDto {
    pub tab_id: String,
    pub title: String,
    pub selected: bool,
    pub visible: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GateDto {
    pub source: String,
    pub label: String,
    pub required_fields_loaded: bool,
    pub validation_status: String,
    pub blocker_count: usize,
    pub diff_reviewed: bool,
    pub evidence_reviewed: bool,
    pub stale: bool,
    pub dirty: bool,
    pub approve_enabled: bool,
    pub reject_enabled: bool,
    pub rework_enabled: bool,
    pub active_diff_target_id: Option<String>,
    pub active_evidence_id: Option<String>,
    pub active_validation_issue_id: Option<String>,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LeftRailPaneDto {
    pub pane_id: String,
    pub title: String,
    pub component_state: String,
    pub collapsed: bool,
    pub visible: bool,
    pub banner_kind: String,
    pub banner_text: String,
    pub rows: Vec<QueueRowDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CenterSurfacePaneDto {
    pub pane_id: String,
    pub title: String,
    pub component_state: String,
    pub collapsed: bool,
    pub visible: bool,
    pub banner_kind: String,
    pub banner_text: String,
    pub packet_summary: PacketSummaryDto,
    pub validation_summary: ValidationSummaryDto,
    pub diff_summary: DiffSummaryDto,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RightInspectorPaneDto {
    pub pane_id: String,
    pub title: String,
    pub component_state: String,
    pub collapsed: bool,
    pub visible: bool,
    pub banner_kind: String,
    pub banner_text: String,
    pub evidence_rows: Vec<EvidenceRowDto>,
    pub review_actions: ReviewActionsDto,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BottomStripPaneDto {
    pub pane_id: String,
    pub title: String,
    pub component_state: String,
    pub collapsed: bool,
    pub visible: bool,
    pub banner_kind: String,
    pub banner_text: String,
    pub event_rows: Vec<EventRowDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct QueueRowDto {
    pub packet_id: String,
    pub title: String,
    pub queue_status: String,
    pub validation_status: String,
    pub blocker_count: usize,
    pub stale: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PacketSummaryDto {
    pub packet_id: String,
    pub document_id: String,
    pub version: String,
    pub subject_label: String,
    pub title: String,
    pub render_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ValidationSummaryDto {
    pub run_id: String,
    pub status: String,
    pub blocker_count: usize,
    pub issue_count: usize,
    pub reviewed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiffSummaryDto {
    pub diff_target_id: String,
    pub change_count: usize,
    pub reviewed: bool,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EvidenceRowDto {
    pub evidence_id: String,
    pub target_id: String,
    pub title: String,
    pub excerpt: String,
    pub reviewed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReviewActionsDto {
    pub claim_visible: bool,
    pub claim_enabled: bool,
    pub approve_visible: bool,
    pub reject_visible: bool,
    pub rework_visible: bool,
    pub approve_enabled: bool,
    pub reject_enabled: bool,
    pub rework_enabled: bool,
    pub disabled_reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EventRowDto {
    pub event_id: String,
    pub severity: String,
    pub message: String,
    pub timestamp: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ViewErrorDto {
    pub kind: String,
    pub message: String,
    pub retryable: bool,
    pub details: String,
}

impl ShellViewDto {
    pub fn empty_frame() -> FrameDto {
        FrameDto {
            app_min_width_px: 1440,
            app_min_height_px: 900,
            status_strip_height_px: 40,
            tab_rail_height_px: 32,
            left_rail_default_width_px: 272,
            left_rail_collapsed_width_px: 40,
            left_rail_min_width_px: 232,
            right_inspector_default_width_px: 360,
            right_inspector_collapsed_width_px: 40,
            right_inspector_min_width_px: 320,
            bottom_strip_default_height_px: 192,
            bottom_strip_collapsed_height_px: 28,
            bottom_strip_min_height_px: 144,
            center_min_width_px: 720,
            center_min_height_px: 420,
            refresh_interval_ms: REFRESH_INTERVAL_MS,
        }
    }

    pub fn empty_tabs() -> TabsDto {
        TabsDto {
            active_tab_id: "review".to_string(),
            items: vec![
                TabItemDto {
                    tab_id: "queue".to_string(),
                    title: "Queue".to_string(),
                    selected: false,
                    visible: true,
                },
                TabItemDto {
                    tab_id: "review".to_string(),
                    title: "Review".to_string(),
                    selected: true,
                    visible: true,
                },
                TabItemDto {
                    tab_id: "lineage".to_string(),
                    title: "Lineage".to_string(),
                    selected: false,
                    visible: true,
                },
                TabItemDto {
                    tab_id: "events".to_string(),
                    title: "Events".to_string(),
                    selected: false,
                    visible: true,
                },
            ],
        }
    }

    pub fn empty_packet_summary() -> PacketSummaryDto {
        PacketSummaryDto {
            packet_id: String::new(),
            document_id: String::new(),
            version: String::new(),
            subject_label: String::new(),
            title: String::new(),
            render_status: "unavailable".to_string(),
        }
    }

    pub fn empty_validation_summary() -> ValidationSummaryDto {
        ValidationSummaryDto {
            run_id: String::new(),
            status: "unavailable".to_string(),
            blocker_count: 0,
            issue_count: 0,
            reviewed: false,
        }
    }

    pub fn empty_diff_summary() -> DiffSummaryDto {
        DiffSummaryDto {
            diff_target_id: String::new(),
            change_count: 0,
            reviewed: false,
            summary: String::new(),
        }
    }

    pub fn disabled_review_actions() -> ReviewActionsDto {
        ReviewActionsDto {
            claim_visible: false,
            claim_enabled: false,
            approve_visible: true,
            reject_visible: true,
            rework_visible: true,
            approve_enabled: false,
            reject_enabled: false,
            rework_enabled: false,
            disabled_reason: String::new(),
        }
    }

    pub fn no_error() -> ViewErrorDto {
        ViewErrorDto {
            kind: "none".to_string(),
            message: String::new(),
            retryable: false,
            details: String::new(),
        }
    }
}
