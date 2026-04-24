use std::env;
use std::path::PathBuf;

use serde::Serialize;
use wiki_cleanroom::codex_cloud::{CodexCloudConfig, CodexCloudWorker};
use wiki_cleanroom::model::{CloudCommandKind, CloudCommandTrace, PacketId};
use wiki_cleanroom::storage::Database;

#[derive(Debug, Serialize)]
struct CanaryReport {
    db_path: String,
    packet_id: String,
    cloud_run_id: String,
    canary_id: String,
    attempt_index: u32,
    environment_id: String,
    resolved_branch: Option<String>,
    task_id: Option<String>,
    final_status: String,
    expected_output_path: String,
    actual_changed_paths: Vec<String>,
    environment_can_produce_diff: Option<bool>,
    error_text: Option<String>,
    command_traces: Vec<CanaryCommandTrace>,
}

#[derive(Debug, Serialize)]
struct CanaryCommandTrace {
    command_kind: String,
    exit_status: Option<i32>,
    stdout_summary: Option<String>,
    stderr_summary: Option<String>,
}

struct CanaryArgs {
    db_path: String,
    canary_id: String,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args(&env::args().skip(1).collect::<Vec<_>>())?;
    let env_id = env::var("CODEX_CLOUD_ENV_ID")
        .map_err(|_| "set CODEX_CLOUD_ENV_ID before running codex_cloud_canary")?;
    let repo_root = env!("CARGO_MANIFEST_DIR");
    let packet_id = PacketId::new(&format!("codex-canary:{}", args.canary_id));
    let mut db = Database::open(&args.db_path)?;
    let attempt_index = db.next_cloud_attempt_index(&packet_id)?;
    let expected_output_path = format!("codex_apply_out/cloud_canary.{}.txt", args.canary_id);
    db.record_audit_event(
        &packet_id.0,
        "packet",
        "cloud",
        "cloud_canary_started",
        Some(
            serde_json::json!({
                "canary_id": args.canary_id,
                "attempt_index": attempt_index,
                "expected_output_path": expected_output_path,
            })
            .to_string(),
        ),
    )?;

    let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo_root, env_id.clone()))?;
    let execution = worker.execute_canary(&args.canary_id, attempt_index);
    db.save_cloud_task_run(&execution.run)?;
    for trace in &execution.command_traces {
        db.save_cloud_command_trace(&packet_id, trace)?;
    }
    let resolved_branch = resolved_branch_from_traces(&execution.command_traces);
    let action = if execution.run.error_text.is_some() {
        "cloud_canary_failed"
    } else {
        "cloud_canary_completed"
    };
    db.record_audit_event(
        &packet_id.0,
        "packet",
        "cloud",
        action,
        Some(
            serde_json::json!({
                "canary_id": args.canary_id,
                "cloud_run_id": execution.run.cloud_run_id,
                "attempt_index": execution.run.attempt_index,
                "resolved_branch": resolved_branch,
                "task_id": execution.run.task_id,
                "final_status": execution.run.final_status,
                "expected_output_path": execution.run.output_path,
                "actual_changed_paths": execution.run.new_apply_paths,
            })
            .to_string(),
        ),
    )?;

    let report = CanaryReport {
        db_path: canonicalize_display(&args.db_path),
        packet_id: packet_id.0.clone(),
        cloud_run_id: execution.run.cloud_run_id.clone(),
        canary_id: args.canary_id.clone(),
        attempt_index,
        environment_id: env_id,
        resolved_branch,
        task_id: execution.run.task_id.clone(),
        final_status: execution.run.final_status.clone(),
        expected_output_path: execution.run.output_path.clone(),
        actual_changed_paths: execution.run.new_apply_paths.clone(),
        environment_can_produce_diff: environment_can_produce_diff(&execution.run.final_status),
        error_text: execution.run.error_text.clone(),
        command_traces: summarize_command_traces(&execution.command_traces),
    };

    println!("{}", serde_json::to_string_pretty(&report)?);

    if execution.run.error_text.is_none() {
        Ok(())
    } else {
        Err(execution
            .run
            .error_text
            .clone()
            .unwrap_or_else(|| format!("canary failed with status {}", execution.run.final_status))
            .into())
    }
}

fn parse_args(args: &[String]) -> Result<CanaryArgs, Box<dyn std::error::Error>> {
    if args.len() == 1 && args[0] == "--help" {
        println!(
            "usage: CODEX_CLOUD_ENV_ID=<env_id> cargo run --bin codex_cloud_canary -- --db-path <path> --canary-id <id>"
        );
        std::process::exit(0);
    }
    let mut db_path = "cleanroom.db".to_string();
    let mut canary_id = None;
    let mut index = 0_usize;
    while index < args.len() {
        match args[index].as_str() {
            "--db-path" => {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or("--db-path requires a value")?
                    .to_string();
                db_path = value;
            }
            "--canary-id" => {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or("--canary-id requires a value")?
                    .to_string();
                canary_id = Some(value);
            }
            other => {
                return Err(format!("unknown argument: {other}").into());
            }
        }
        index += 1;
    }
    let canary_id = canary_id.ok_or("--canary-id is required")?;
    Ok(CanaryArgs { db_path, canary_id })
}

fn summarize_command_traces(traces: &[CloudCommandTrace]) -> Vec<CanaryCommandTrace> {
    traces
        .iter()
        .map(|trace| CanaryCommandTrace {
            command_kind: trace.command_kind.as_str().to_string(),
            exit_status: trace.exit_status,
            stdout_summary: trace.stdout_summary.clone(),
            stderr_summary: trace.stderr_summary.clone(),
        })
        .collect()
}

fn resolved_branch_from_traces(traces: &[CloudCommandTrace]) -> Option<String> {
    traces
        .iter()
        .find(|trace| trace.command_kind == CloudCommandKind::Exec)
        .and_then(|trace| {
            let parts = trace.command_text.split_whitespace().collect::<Vec<_>>();
            parts
                .windows(2)
                .find(|window| window[0] == "--branch")
                .map(|window| window[1].to_string())
        })
}

fn environment_can_produce_diff(final_status: &str) -> Option<bool> {
    if final_status == "cloud_no_diff" {
        Some(false)
    } else if ["completed", "succeeded", "success"].contains(&final_status) {
        Some(true)
    } else {
        None
    }
}

fn canonicalize_display(path: &str) -> String {
    PathBuf::from(path)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(path))
        .display()
        .to_string()
}
