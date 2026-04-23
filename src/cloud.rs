use crate::codex_cloud::{CodexCloudConfig, CodexCloudWorker};
use crate::error::PipelineError;
use crate::model::{
    CloudCommandTrace, CloudSummaryResult, CloudTaskPacket, CloudTaskRun, EvidenceRef, Span,
    SummaryFragment,
};

#[derive(Debug, Clone)]
pub struct CloudExecution {
    pub run: CloudTaskRun,
    pub command_traces: Vec<CloudCommandTrace>,
    pub result: Option<CloudSummaryResult>,
}

pub trait CloudSummarizer {
    fn execute(&self, packet: &CloudTaskPacket, attempt_index: u32) -> CloudExecution;

    fn summarize(&self, packet: &CloudTaskPacket) -> Result<CloudSummaryResult, PipelineError> {
        let execution = self.execute(packet, 1);
        execution.result.ok_or_else(|| {
            PipelineError::Storage(
                execution
                    .run
                    .error_text
                    .clone()
                    .unwrap_or_else(|| "cloud execution produced no result".to_string()),
            )
        })
    }
}

pub struct CodexCloudSummarizer {
    worker: CodexCloudWorker,
}

impl CodexCloudSummarizer {
    pub fn new(config: CodexCloudConfig) -> Result<Self, PipelineError> {
        Ok(Self {
            worker: CodexCloudWorker::new(config)?,
        })
    }
}

pub struct MockCloudSummarizer;

impl CloudSummarizer for MockCloudSummarizer {
    fn execute(&self, packet: &CloudTaskPacket, attempt_index: u32) -> CloudExecution {
        let fragments = packet
            .work_units
            .iter()
            .map(|unit| SummaryFragment {
                target_node_id: unit.target_node_id.clone(),
                summary_title: format!("Summary for {}", unit.target_node_id.0),
                summary_text: "This summary was produced from a bounded packet. It preserves only the target block and nearby context while collapsing local boilerplate.".to_string(),
                unresolved_questions: if unit.instructions.iter().any(|i| i.contains("unresolved")) {
                    vec!["Ambiguous drafting markers were present.".to_string()]
                } else {
                    vec![]
                },
                evidence: unit
                    .visible_node_ids
                    .iter()
                    .take(2)
                    .map(|node_id| EvidenceRef {
                        node_id: node_id.clone(),
                        span: Span { start: 0, end: 1 },
                    })
                    .collect(),
            })
            .collect::<Vec<_>>();
        let now = chrono::Utc::now();
        CloudExecution {
            run: CloudTaskRun {
                cloud_run_id: format!("cloudrun_{}_attempt_{attempt_index:03}", packet.packet_id.0),
                packet_id: packet.packet_id.clone(),
                attempt_index,
                task_id: None,
                task_url: None,
                environment_id: Some("mock".to_string()),
                matched_remote_identity: None,
                current_head_sha: None,
                current_branch: None,
                head_contained_in_allowed_remote_ref: None,
                resolution_method: "mock".to_string(),
                handoff_mode: "mock_inline_packet_visible_output_v3".to_string(),
                packet_path: format!(
                    "mock://packets/{}.attempt-{attempt_index:03}.json",
                    packet.packet_id.0
                ),
                schema_path: format!(
                    "mock://schema/{}.attempt-{attempt_index:03}.schema.json",
                    packet.packet_id.0
                ),
                output_path: format!(
                    "mock://codex_apply_out/{}.attempt-{attempt_index:03}.summary.json",
                    packet.packet_id.0
                ),
                allowed_apply_paths: vec![],
                new_apply_paths: vec![],
                submitted_at: now,
                finished_at: Some(now),
                final_status: "success".to_string(),
                error_text: None,
            },
            command_traces: vec![],
            result: Some(CloudSummaryResult {
                packet_id: packet.packet_id.clone(),
                model_name: "mock-cloud".to_string(),
                fragments,
            }),
        }
    }
}

impl CloudSummarizer for CodexCloudSummarizer {
    fn execute(&self, packet: &CloudTaskPacket, attempt_index: u32) -> CloudExecution {
        self.worker.execute_summary_task(packet, attempt_index)
    }
}
