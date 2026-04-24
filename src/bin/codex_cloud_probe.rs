use std::env;

use chrono::Utc;
use serde::Serialize;
use wiki_cleanroom::codex_cloud::{CodexCloudConfig, CodexCloudWorker};
use wiki_cleanroom::model::{
    CloudCommandTrace, CloudTaskPacket, DocumentId, NodeId, PacketId, TrimAction, TrimEntry,
    WorkUnit,
};

#[derive(Debug, Serialize)]
struct ProbeReport {
    repo_root: String,
    environment_id: String,
    attempt_index: u32,
    packet_id: String,
    document_id: String,
    final_status: String,
    error_text: Option<String>,
    task_id: Option<String>,
    task_url: Option<String>,
    handoff_mode: String,
    matched_remote_identity: Option<String>,
    current_head_sha: Option<String>,
    current_branch: Option<String>,
    head_contained_in_allowed_remote_ref: Option<bool>,
    packet_path: String,
    schema_path: String,
    output_path: String,
    allowed_apply_paths: Vec<String>,
    new_apply_paths: Vec<String>,
    result_fragment_count: usize,
    command_traces: Vec<ProbeCommandTrace>,
}

#[derive(Debug, Serialize)]
struct ProbeCommandTrace {
    command_kind: String,
    exit_status: Option<i32>,
    stdout_summary: Option<String>,
    stderr_summary: Option<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    let attempt_index = parse_attempt_index(&args)?;
    let env_id = env::var("CODEX_CLOUD_ENV_ID")
        .map_err(|_| "set CODEX_CLOUD_ENV_ID before running codex_cloud_probe")?;
    let repo_root = env!("CARGO_MANIFEST_DIR");
    let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo_root, env_id.clone()))?;
    let packet = build_probe_packet(attempt_index);
    let execution = worker.execute_summary_task(&packet, attempt_index);

    let report = ProbeReport {
        repo_root: repo_root.to_string(),
        environment_id: env_id,
        attempt_index,
        packet_id: packet.packet_id.0.clone(),
        document_id: packet.document_id.0.clone(),
        final_status: execution.run.final_status.clone(),
        error_text: execution.run.error_text.clone(),
        task_id: execution.run.task_id.clone(),
        task_url: execution.run.task_url.clone(),
        handoff_mode: execution.run.handoff_mode.clone(),
        matched_remote_identity: execution.run.matched_remote_identity.clone(),
        current_head_sha: execution.run.current_head_sha.clone(),
        current_branch: execution.run.current_branch.clone(),
        head_contained_in_allowed_remote_ref: execution.run.head_contained_in_allowed_remote_ref,
        packet_path: execution.run.packet_path.clone(),
        schema_path: execution.run.schema_path.clone(),
        output_path: execution.run.output_path.clone(),
        allowed_apply_paths: execution.run.allowed_apply_paths.clone(),
        new_apply_paths: execution.run.new_apply_paths.clone(),
        result_fragment_count: execution
            .result
            .as_ref()
            .map(|result| result.fragments.len())
            .unwrap_or(0),
        command_traces: summarize_command_traces(&execution.command_traces),
    };

    println!("{}", serde_json::to_string_pretty(&report)?);

    if execution.result.is_some() {
        Ok(())
    } else {
        Err(report
            .error_text
            .clone()
            .unwrap_or_else(|| format!("probe failed with status {}", report.final_status))
            .into())
    }
}

fn parse_attempt_index(args: &[String]) -> Result<u32, Box<dyn std::error::Error>> {
    if args.is_empty() {
        return Ok(1);
    }
    if args.len() == 1 && args[0] == "--help" {
        println!(
            "usage: CODEX_CLOUD_ENV_ID=<env_id> cargo run --bin codex_cloud_probe -- [--attempt-index N]"
        );
        std::process::exit(0);
    }
    if args.len() != 2 || args[0] != "--attempt-index" {
        return Err("usage: codex_cloud_probe [--attempt-index N]".into());
    }
    let attempt_index = args[1]
        .parse::<u32>()
        .map_err(|_| "--attempt-index must be a positive integer")?;
    if attempt_index == 0 {
        return Err("--attempt-index must be greater than zero".into());
    }
    Ok(attempt_index)
}

fn build_probe_packet(attempt_index: u32) -> CloudTaskPacket {
    let probe_seed = format!(
        "probe:{}:attempt-{attempt_index:03}",
        Utc::now().to_rfc3339()
    );
    let packet_id = PacketId::new(&probe_seed);
    let document_id = DocumentId::new(&format!("document:{probe_seed}"));
    let context_node_id = NodeId::for_document(&document_id, "probe_context");
    let target_node_id = NodeId::for_document(&document_id, "probe_target");
    CloudTaskPacket {
        packet_id,
        document_id,
        work_units: vec![WorkUnit {
            work_unit_id: format!("wu_{}", attempt_index),
            target_node_id: target_node_id.clone(),
            visible_node_ids: vec![context_node_id.clone(), target_node_id.clone()],
            context_node_ids: vec![context_node_id.clone()],
            trim_map: vec![
                TrimEntry {
                    node_id: context_node_id,
                    action: TrimAction::KeptVisible,
                    explanation: "probe context".to_string(),
                },
                TrimEntry {
                    node_id: target_node_id.clone(),
                    action: TrimAction::KeptVisible,
                    explanation: "probe target".to_string(),
                },
            ],
            rendered_text: format!(
                "# codex cloud probe\n\nContext: this is a narrow backend probe for attempt {attempt_index}.\n\nTarget: write the summary artifact only, with no extra file edits."
            ),
            instructions: vec![
                "Return exactly one summary fragment for the target node.".to_string(),
                "Use only the visible probe text and keep evidence refs minimal.".to_string(),
            ],
        }],
        style_contract:
            "Write concise, evidence-grounded summaries. Do not invent claims. Preserve ambiguity where unresolved.".to_string(),
        completion_contract:
            "Return one fragment per work unit with title, summary_text, unresolved_questions, and evidence refs.".to_string(),
    }
}

fn summarize_command_traces(traces: &[CloudCommandTrace]) -> Vec<ProbeCommandTrace> {
    traces
        .iter()
        .map(|trace| ProbeCommandTrace {
            command_kind: trace.command_kind.as_str().to_string(),
            exit_status: trace.exit_status,
            stdout_summary: trace.stdout_summary.clone(),
            stderr_summary: trace.stderr_summary.clone(),
        })
        .collect()
}
