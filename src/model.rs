use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

fn short_hash(prefix: &str, body: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(body.as_bytes());
    let digest = hasher.finalize();
    format!("{prefix}_{}", hex16(&digest[..8]))
}

fn hex16(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()
}

pub fn sha256_hex(body: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(body.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>()
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct DocumentId(pub String);

impl DocumentId {
    pub fn new(content: &str) -> Self {
        Self(short_hash("doc", content))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct NodeId(pub String);

impl NodeId {
    pub fn new(seed: &str) -> Self {
        Self(short_hash("node", seed))
    }

    pub fn for_document(document_id: &DocumentId, seed: &str) -> Self {
        Self(short_hash("node", &format!("{}:{seed}", document_id.0)))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PacketId(pub String);

impl PacketId {
    pub fn new(seed: &str) -> Self {
        Self(short_hash("pkt", seed))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ReviewId(pub String);

impl ReviewId {
    pub fn new(seed: &str) -> Self {
        Self(short_hash("rev", seed))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceKind {
    Conversation,
    Document,
    Note,
    ImportedFile,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Span {
    pub start: usize,
    pub end: usize,
}

impl Span {
    pub fn len(&self) -> usize {
        self.end.saturating_sub(self.start)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawDocument {
    pub document_id: DocumentId,
    pub title: String,
    pub source_kind: SourceKind,
    pub source_path: Option<String>,
    pub acquired_at: DateTime<Utc>,
    pub raw_text: String,
    pub raw_sha256: String,
}

impl RawDocument {
    pub fn new(
        title: impl Into<String>,
        source_kind: SourceKind,
        source_path: Option<String>,
        raw_text: impl Into<String>,
    ) -> Self {
        let raw_text = raw_text.into();
        let raw_sha256 = sha256_hex(&raw_text);
        Self {
            document_id: DocumentId::new(&raw_text),
            title: title.into(),
            source_kind,
            source_path,
            acquired_at: Utc::now(),
            raw_text,
            raw_sha256,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum NodeKind {
    Heading,
    Paragraph,
    Sentence,
    CodeBlock,
    Quote,
    LinkList,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParsedNode {
    pub node_id: NodeId,
    pub parent_node_id: Option<NodeId>,
    pub kind: NodeKind,
    pub span: Span,
    pub text: String,
    pub ordinal: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParsedDocument {
    pub raw: RawDocument,
    pub nodes: Vec<ParsedNode>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum MarkerKind {
    PreserveVerbatim,
    CollapseBoilerplate,
    NeedsContext,
    LikelyNoise,
    CoreContent,
    DuplicateOf,
    EscalateToCloud,
    UnsafeToEdit,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Marker {
    pub marker_id: String,
    pub target_node_id: NodeId,
    pub kind: MarkerKind,
    pub confidence: f32,
    pub reason: String,
    pub instruction: Option<String>,
    pub related_node_ids: Vec<NodeId>,
}

impl Marker {
    pub fn new(
        target_node_id: NodeId,
        kind: MarkerKind,
        confidence: f32,
        reason: impl Into<String>,
        instruction: Option<String>,
        related_node_ids: Vec<NodeId>,
    ) -> Self {
        let reason = reason.into();
        let seed = format!("{:?}:{}:{reason}", kind, target_node_id.0);
        Self {
            marker_id: short_hash("marker", &seed),
            target_node_id,
            kind,
            confidence,
            reason,
            instruction,
            related_node_ids,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum TrimAction {
    KeptVisible,
    CollapsedPlaceholder,
    HiddenButReferenced,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrimEntry {
    pub node_id: NodeId,
    pub action: TrimAction,
    pub explanation: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkUnit {
    pub work_unit_id: String,
    pub target_node_id: NodeId,
    pub visible_node_ids: Vec<NodeId>,
    pub context_node_ids: Vec<NodeId>,
    pub trim_map: Vec<TrimEntry>,
    pub rendered_text: String,
    pub instructions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudTaskPacket {
    pub packet_id: PacketId,
    pub document_id: DocumentId,
    pub work_units: Vec<WorkUnit>,
    pub style_contract: String,
    pub completion_contract: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvidenceRef {
    pub node_id: NodeId,
    pub span: Span,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SummaryFragment {
    pub target_node_id: NodeId,
    pub summary_title: String,
    pub summary_text: String,
    pub unresolved_questions: Vec<String>,
    pub evidence: Vec<EvidenceRef>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudSummaryResult {
    pub packet_id: PacketId,
    pub model_name: String,
    pub fragments: Vec<SummaryFragment>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum CloudCommandKind {
    GitCheckoutCheck,
    GitWorktreeCheck,
    GitRemoteCheck,
    GitHeadCheck,
    GitRemoteContainmentCheck,
    LoginStatus,
    Exec,
    List,
    Status,
    GitStatusBeforeApply,
    Apply,
    GitStatusAfterApply,
}

impl CloudCommandKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::GitCheckoutCheck => "git_checkout_check",
            Self::GitWorktreeCheck => "git_worktree_check",
            Self::GitRemoteCheck => "git_remote_check",
            Self::GitHeadCheck => "git_head_check",
            Self::GitRemoteContainmentCheck => "git_remote_containment_check",
            Self::LoginStatus => "login_status",
            Self::Exec => "exec",
            Self::List => "list",
            Self::Status => "status",
            Self::GitStatusBeforeApply => "git_status_before_apply",
            Self::Apply => "apply",
            Self::GitStatusAfterApply => "git_status_after_apply",
        }
    }

    pub fn from_db(value: &str) -> Option<Self> {
        match value {
            "git_checkout_check" => Some(Self::GitCheckoutCheck),
            "git_worktree_check" => Some(Self::GitWorktreeCheck),
            "git_remote_check" => Some(Self::GitRemoteCheck),
            "git_head_check" => Some(Self::GitHeadCheck),
            "git_remote_containment_check" => Some(Self::GitRemoteContainmentCheck),
            "login_status" => Some(Self::LoginStatus),
            "exec" => Some(Self::Exec),
            "list" => Some(Self::List),
            "status" => Some(Self::Status),
            "git_status_before_apply" => Some(Self::GitStatusBeforeApply),
            "apply" => Some(Self::Apply),
            "git_status_after_apply" => Some(Self::GitStatusAfterApply),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudCommandTrace {
    pub trace_id: String,
    pub cloud_run_id: String,
    pub attempt_index: u32,
    pub command_kind: CloudCommandKind,
    pub command_text: String,
    pub started_at: DateTime<Utc>,
    pub finished_at: DateTime<Utc>,
    pub exit_status: Option<i32>,
    pub stdout_summary: Option<String>,
    pub stderr_summary: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudTaskRun {
    pub cloud_run_id: String,
    pub packet_id: PacketId,
    pub attempt_index: u32,
    pub task_id: Option<String>,
    pub task_url: Option<String>,
    pub environment_id: Option<String>,
    pub matched_remote_identity: Option<String>,
    pub current_head_sha: Option<String>,
    pub current_branch: Option<String>,
    pub head_contained_in_allowed_remote_ref: Option<bool>,
    pub resolution_method: String,
    pub handoff_mode: String,
    pub packet_path: String,
    pub schema_path: String,
    pub output_path: String,
    pub allowed_apply_paths: Vec<String>,
    pub new_apply_paths: Vec<String>,
    pub submitted_at: DateTime<Utc>,
    pub finished_at: Option<DateTime<Utc>>,
    pub final_status: String,
    pub error_text: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ValidationSeverity {
    Info,
    Warning,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationIssue {
    pub issue_id: String,
    pub severity: ValidationSeverity,
    pub blocking: bool,
    pub target_id: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationReport {
    pub packet_id: PacketId,
    pub passed: bool,
    pub issues: Vec<ValidationIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ReviewDecision {
    Approve,
    Reject,
    Rework,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewStamp {
    pub review_id: ReviewId,
    pub reviewer: String,
    pub decision: ReviewDecision,
    pub notes: String,
    pub reviewed_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewReadyPacket {
    pub packet: CloudTaskPacket,
    pub result: CloudSummaryResult,
    pub validation: ValidationReport,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovedPacket {
    pub review_ready: ReviewReadyPacket,
    pub stamp: ReviewStamp,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PacketLineage {
    pub packet_id: PacketId,
    pub lineage_root_packet_id: PacketId,
    pub prior_packet_id: Option<PacketId>,
    pub successor_packet_id: Option<PacketId>,
    pub spawned_by_review_id: Option<ReviewId>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewDiffState {
    pub packet_id: PacketId,
    pub diff_target_id: String,
    pub change_count: usize,
    pub reviewed: bool,
    pub reviewed_by: Option<String>,
    pub reviewed_at: Option<DateTime<Utc>>,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewEvidenceState {
    pub packet_id: PacketId,
    pub evidence_id: String,
    pub target_id: String,
    pub reviewed: bool,
    pub reviewed_by: Option<String>,
    pub reviewed_at: Option<DateTime<Utc>>,
    pub payload_json: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewGateState {
    pub packet_id: PacketId,
    pub packet_version: String,
    pub required_fields_loaded: bool,
    pub validation_status: String,
    pub blocker_count: usize,
    pub diff_reviewed: bool,
    pub evidence_reviewed: bool,
    pub stale_flag: bool,
    pub dirty_flag: bool,
    pub active_diff_target_id: Option<String>,
    pub active_evidence_id: Option<String>,
    pub active_validation_issue_id: Option<String>,
    pub approve_enabled: bool,
    pub reject_enabled: bool,
    pub rework_enabled: bool,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewArtifact {
    pub review_id: ReviewId,
    pub packet_id: PacketId,
    pub packet_version: String,
    pub reviewer: String,
    pub decision: ReviewDecision,
    pub notes: String,
    pub gate_snapshot: ReviewGateState,
    pub blocker_snapshot: Vec<ValidationIssue>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetadataRecord {
    pub subjects: Vec<String>,
    pub tags: Vec<String>,
    pub related_subjects: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WikiNode {
    pub wiki_node_id: String,
    pub source_document_id: DocumentId,
    pub approved_packet_id: PacketId,
    pub title: String,
    pub body: String,
    pub metadata: MetadataRecord,
    pub source_node_ids: Vec<NodeId>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum QueueStatus {
    Pending,
    InReview,
    Approved,
    Rejected,
    ReworkRequested,
}

impl QueueStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::InReview => "in_review",
            Self::Approved => "approved",
            Self::Rejected => "rejected",
            Self::ReworkRequested => "rework_requested",
        }
    }

    pub fn from_db(value: &str) -> Option<Self> {
        match value {
            "pending" => Some(Self::Pending),
            "in_review" => Some(Self::InReview),
            "approved" => Some(Self::Approved),
            "rejected" => Some(Self::Rejected),
            "rework_requested" => Some(Self::ReworkRequested),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewQueueItem {
    pub packet_id: PacketId,
    pub document_id: DocumentId,
    pub status: QueueStatus,
    pub assigned_reviewer: Option<String>,
    pub decision: Option<ReviewDecision>,
    pub notes: Option<String>,
    pub claimed_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEvent {
    pub event_id: i64,
    pub aggregate_id: String,
    pub aggregate_type: String,
    pub stage: String,
    pub action: String,
    pub payload_json: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayBundle {
    pub raw: Option<RawDocument>,
    pub parsed: Option<ParsedDocument>,
    pub markers: Option<Vec<Marker>>,
    pub packet: Option<CloudTaskPacket>,
    pub cloud_task_runs: Vec<CloudTaskRun>,
    pub cloud_command_traces: Vec<CloudCommandTrace>,
    pub result: Option<CloudSummaryResult>,
    pub validation: Option<ValidationReport>,
    pub lineage: Option<PacketLineage>,
    pub review_queue_item: Option<ReviewQueueItem>,
    pub review_artifact: Option<ReviewArtifact>,
    pub gate_state: Option<ReviewGateState>,
    pub diff_states: Vec<ReviewDiffState>,
    pub evidence_states: Vec<ReviewEvidenceState>,
    pub approved: Option<ApprovedPacket>,
    pub wiki_node: Option<WikiNode>,
    pub audit_events: Vec<AuditEvent>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_document_ids_and_hashes_are_stable_for_content() {
        let a = RawDocument::new("A", SourceKind::Document, None, "same text");
        let b = RawDocument::new("B", SourceKind::Note, None, "same text");

        assert_eq!(a.document_id, b.document_id);
        assert_eq!(a.raw_sha256, b.raw_sha256);
        assert_eq!(a.raw_sha256.len(), 64);
    }

    #[test]
    fn marker_new_preserves_reason_directly() {
        let node = NodeId("node_test".to_string());
        let marker = Marker::new(
            node,
            MarkerKind::NeedsContext,
            0.7,
            "context-dependent phrasing",
            None,
            vec![],
        );

        assert_eq!(marker.reason, "context-dependent phrasing");
    }
}
