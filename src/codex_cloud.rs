use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

use chrono::{DateTime, Duration as ChronoDuration, Utc};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::cloud::CloudExecution;
use crate::model::{
    CloudCommandKind, CloudCommandTrace, CloudSummaryResult, CloudTaskPacket, CloudTaskRun,
    EvidenceRef, NodeId, PacketId, Span, SummaryFragment,
};

const ACTIVE_TASK_STATUSES: &[&str] = &["queued", "pending", "running", "in_progress"];
const SUCCESS_TASK_STATUSES: &[&str] = &["completed", "succeeded", "success"];
const FAILURE_TASK_STATUSES: &[&str] = &["failed", "cancelled", "canceled", "error"];
const MAX_LIST_LIMIT: &str = "20";
const ENV_MANIFEST_REL_PATH: &str = "config/codex_cloud_env.json";

#[derive(Debug, Clone)]
pub struct CodexCloudConfig {
    pub repo_root: PathBuf,
    pub env_id: String,
    pub poll_interval: Duration,
    pub timeout: Duration,
    pub attempts: u8,
    pub packet_dir: PathBuf,
    pub output_dir: PathBuf,
    pub schema_dir: PathBuf,
}

impl CodexCloudConfig {
    pub fn new(repo_root: impl Into<PathBuf>, env_id: impl Into<String>) -> Self {
        let repo_root = repo_root.into();
        Self {
            packet_dir: repo_root.join(".cleanroom/packets"),
            output_dir: repo_root.join("codex_apply_out"),
            schema_dir: repo_root.join(".cleanroom/schema"),
            repo_root,
            env_id: env_id.into(),
            poll_interval: Duration::from_secs(10),
            timeout: Duration::from_secs(60 * 30),
            attempts: 1,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodexTaskSummary {
    pub files_changed: Option<u32>,
    pub lines_added: Option<u32>,
    pub lines_removed: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodexCloudTaskInfo {
    pub id: String,
    pub url: String,
    #[serde(default)]
    pub title: Option<String>,
    pub status: String,
    pub updated_at: String,
    pub environment_id: Option<String>,
    pub environment_label: Option<String>,
    #[serde(default)]
    pub summary: Option<CodexTaskSummary>,
    pub is_review: Option<bool>,
    pub attempt_total: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodexCloudListResponse {
    pub tasks: Vec<CodexCloudTaskInfo>,
    pub cursor: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct CodexCloudEnvManifest {
    version: u32,
    environment_id: String,
    #[serde(default)]
    environment_label: Option<String>,
    allowed_fetch_remote_identities: Vec<String>,
}

#[derive(Debug, Clone)]
enum TaskStatusClass {
    Active,
    Success,
    Failure,
}

#[derive(Debug, Clone)]
struct AttemptArtifacts {
    packet_path: PathBuf,
    schema_path: PathBuf,
    output_path: PathBuf,
    packet_rel: String,
    schema_rel: String,
    output_rel: String,
}

#[derive(Debug, Clone)]
struct ResolvedTask {
    task_id: String,
    task_url: Option<String>,
    resolution_method: String,
}

#[derive(Debug, Clone)]
struct CommandCapture {
    trace: CloudCommandTrace,
    stdout: String,
    success: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FetchRemoteBinding {
    remote_name: String,
    identity: String,
}

pub struct CodexCloudWorker {
    config: CodexCloudConfig,
}

impl CodexCloudWorker {
    pub fn new(config: CodexCloudConfig) -> Result<Self, crate::error::PipelineError> {
        fs::create_dir_all(&config.packet_dir).map_err(|e| {
            crate::error::PipelineError::Storage(format!("failed to create packet dir: {e}"))
        })?;
        fs::create_dir_all(&config.output_dir).map_err(|e| {
            crate::error::PipelineError::Storage(format!("failed to create output dir: {e}"))
        })?;
        fs::create_dir_all(&config.schema_dir).map_err(|e| {
            crate::error::PipelineError::Storage(format!("failed to create schema dir: {e}"))
        })?;
        Ok(Self { config })
    }

    pub fn execute_summary_task(
        &self,
        packet: &CloudTaskPacket,
        attempt_index: u32,
    ) -> CloudExecution {
        let submitted_at = Utc::now();
        let artifacts = self.artifacts_for(packet, attempt_index);
        let mut run = CloudTaskRun {
            cloud_run_id: cloud_run_id(packet, attempt_index),
            packet_id: packet.packet_id.clone(),
            attempt_index,
            task_id: None,
            task_url: None,
            environment_id: Some(self.config.env_id.clone()),
            matched_remote_identity: None,
            current_head_sha: None,
            current_branch: None,
            head_contained_in_allowed_remote_ref: None,
            resolution_method: "unresolved".to_string(),
            handoff_mode: "inline_packet_visible_output_v3".to_string(),
            packet_path: artifacts.packet_rel.clone(),
            schema_path: artifacts.schema_rel.clone(),
            output_path: artifacts.output_rel.clone(),
            allowed_apply_paths: vec![artifacts.output_rel.clone()],
            new_apply_paths: vec![],
            submitted_at,
            finished_at: None,
            final_status: "submission_started".to_string(),
            error_text: None,
        };
        let mut traces = Vec::new();
        let mut trace_seq = 0_u32;

        if !self.ensure_command_success(
            &mut traces,
            &mut trace_seq,
            &mut run,
            CloudCommandKind::GitCheckoutCheck,
            "git",
            &["rev-parse", "--is-inside-work-tree"],
            "git checkout preflight failed",
            "git_checkout_check_failed",
        ) {
            return cloud_execution(run, traces, None);
        }
        let manifest = match self.load_env_manifest() {
            Ok(manifest) => manifest,
            Err((status, message)) => return fail_execution(run, traces, &status, message),
        };
        if self.config.env_id != manifest.environment_id {
            return fail_execution(
                run,
                traces,
                "environment_id_mismatch",
                environment_id_mismatch_message(&self.config.env_id, &manifest),
            );
        }
        let worktree_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitWorktreeCheck,
            "git",
            &[
                "status".to_string(),
                "--porcelain".to_string(),
                "--branch".to_string(),
            ],
        );
        traces.push(worktree_capture.trace.clone());
        if !worktree_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_worktree_check_failed",
                "git status failed during worktree preflight",
                &worktree_capture.trace,
            );
        }
        run.current_branch = parse_current_branch(&worktree_capture.stdout);
        if worktree_is_dirty(&worktree_capture.stdout) {
            let message = dirty_worktree_message(run.current_branch.as_deref());
            return fail_execution(run, traces, "git_worktree_dirty", message);
        }
        let remote_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitRemoteCheck,
            "git",
            &["remote".to_string(), "-v".to_string()],
        );
        traces.push(remote_capture.trace.clone());
        if !remote_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_remote_check_failed",
                "git remote -v failed during repo binding preflight",
                &remote_capture.trace,
            );
        }
        let fetch_bindings = parse_fetch_remote_bindings(&remote_capture.stdout);
        if fetch_bindings.is_empty() {
            return fail_execution(
                run,
                traces,
                "git_remote_missing",
                format!(
                    "no fetch remotes were configured; add an allowed fetch remote listed in {}",
                    ENV_MANIFEST_REL_PATH
                ),
            );
        }
        let matched_bindings = matching_fetch_remote_bindings(
            &fetch_bindings,
            &manifest.allowed_fetch_remote_identities,
        );
        if matched_bindings.is_empty() {
            return fail_execution(
                run,
                traces,
                "git_remote_mismatch",
                format!(
                    "no fetch remote matched allowed identities in {}: found {}",
                    ENV_MANIFEST_REL_PATH,
                    render_fetch_remote_identities(&fetch_bindings)
                ),
            );
        }
        run.matched_remote_identity = Some(matched_bindings[0].identity.clone());
        let head_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitHeadCheck,
            "git",
            &["rev-parse".to_string(), "HEAD".to_string()],
        );
        traces.push(head_capture.trace.clone());
        if !head_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_head_check_failed",
                "git rev-parse HEAD failed during repo binding preflight",
                &head_capture.trace,
            );
        }
        let current_head_sha = match parse_head_sha(&head_capture.stdout) {
            Some(current_head_sha) => current_head_sha,
            None => {
                return fail_execution(
                    run,
                    traces,
                    "git_head_check_failed",
                    "git rev-parse HEAD returned no commit sha".to_string(),
                );
            }
        };
        run.current_head_sha = Some(current_head_sha.clone());
        let containment_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitRemoteContainmentCheck,
            "git",
            &[
                "branch".to_string(),
                "-r".to_string(),
                "--contains".to_string(),
                current_head_sha.clone(),
            ],
        );
        traces.push(containment_capture.trace.clone());
        if !containment_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_remote_containment_check_failed",
                "git branch -r --contains HEAD failed during repo binding preflight",
                &containment_capture.trace,
            );
        }
        let matched_remote_names = matched_bindings
            .iter()
            .map(|binding| binding.remote_name.as_str())
            .collect::<Vec<_>>();
        let head_contained = head_is_contained_in_allowed_remote_ref(
            &containment_capture.stdout,
            &matched_remote_names,
        );
        run.head_contained_in_allowed_remote_ref = Some(head_contained);
        if !head_contained {
            let matched_remote_identity = run
                .matched_remote_identity
                .clone()
                .unwrap_or_else(|| "<unknown remote>".to_string());
            return fail_execution(
                run,
                traces,
                "git_head_unpushed",
                format!(
                    "HEAD {} is not contained in any allowed remote ref for {}",
                    current_head_sha, matched_remote_identity
                ),
            );
        }
        if !self.ensure_command_success(
            &mut traces,
            &mut trace_seq,
            &mut run,
            CloudCommandKind::LoginStatus,
            "codex",
            &["login", "status"],
            "codex CLI is not authenticated",
            "login_status_failed",
        ) {
            return cloud_execution(run, traces, None);
        }
        if let Err(error) = self.write_packet(packet, &artifacts.packet_path) {
            return fail_execution(
                run,
                traces,
                "artifact_write_failed",
                format!("failed to write packet artifact: {error}"),
            );
        }
        if let Err(error) = self.write_output_schema(&artifacts.schema_path) {
            return fail_execution(
                run,
                traces,
                "artifact_write_failed",
                format!("failed to write schema artifact: {error}"),
            );
        }

        let packet_json = match serde_json::to_string(packet) {
            Ok(packet_json) => packet_json,
            Err(error) => {
                return fail_execution(
                    run,
                    traces,
                    "artifact_write_failed",
                    format!("failed to serialize inline packet JSON: {error}"),
                );
            }
        };
        let prompt = self.build_prompt(packet, &artifacts.output_rel, &packet_json);
        let attempts = self.config.attempts.to_string();
        let exec_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::Exec,
            "codex",
            &[
                "cloud".to_string(),
                "exec".to_string(),
                "--env".to_string(),
                self.config.env_id.clone(),
                "--attempts".to_string(),
                attempts,
                prompt,
            ],
        );
        traces.push(exec_capture.trace.clone());
        if !exec_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "exec_failed",
                "codex cloud exec failed",
                &exec_capture.trace,
            );
        }

        match extract_task_from_exec_stdout(&exec_capture.stdout) {
            Some(resolved) => {
                run.task_id = Some(resolved.task_id);
                run.task_url = resolved.task_url;
                run.resolution_method = resolved.resolution_method;
            }
            None => {
                let resolution =
                    self.resolve_recent_task(&run, &mut traces, &mut trace_seq, submitted_at);
                let resolved = match resolution {
                    Ok(resolved) => resolved,
                    Err((status, message)) => return fail_execution(run, traces, &status, message),
                };
                run.task_id = Some(resolved.task_id);
                run.task_url = resolved.task_url;
                run.resolution_method = resolved.resolution_method;
            }
        }

        let task_id = match run.task_id.clone() {
            Some(task_id) => task_id,
            None => {
                return fail_execution(
                    run,
                    traces,
                    "task_resolution_failed",
                    "cloud task id was not resolved".to_string(),
                )
            }
        };

        let started = Instant::now();
        loop {
            if started.elapsed() > self.config.timeout {
                return fail_execution(
                    run,
                    traces,
                    "timeout",
                    format!("timed out waiting for Codex cloud task {task_id}"),
                );
            }

            let list_capture = self.run_command_capture(
                &run,
                &mut trace_seq,
                CloudCommandKind::List,
                "codex",
                &[
                    "cloud".to_string(),
                    "list".to_string(),
                    "--env".to_string(),
                    self.config.env_id.clone(),
                    "--limit".to_string(),
                    MAX_LIST_LIMIT.to_string(),
                    "--json".to_string(),
                ],
            );
            traces.push(list_capture.trace.clone());
            if !list_capture.success {
                return fail_execution_from_trace(
                    run,
                    traces,
                    "list_failed",
                    "codex cloud list failed while polling",
                    &list_capture.trace,
                );
            }

            let listing = match parse_list_response(&list_capture.stdout) {
                Ok(listing) => listing,
                Err(message) => return fail_execution(run, traces, "list_parse_failed", message),
            };
            let task = listing
                .tasks
                .into_iter()
                .find(|candidate| candidate.id == task_id);
            let Some(task) = task else {
                thread::sleep(self.config.poll_interval);
                continue;
            };
            let status_class = match classify_task_status(&task.status) {
                Ok(status_class) => status_class,
                Err(message) => {
                    return fail_execution(run, traces, "unknown_task_status", message);
                }
            };
            run.task_url = Some(task.url.clone());
            match status_class {
                TaskStatusClass::Active => thread::sleep(self.config.poll_interval),
                TaskStatusClass::Success => {
                    run.final_status = task.status;
                    run.finished_at = Some(Utc::now());
                    break;
                }
                TaskStatusClass::Failure => {
                    let status_capture = self.run_command_capture(
                        &run,
                        &mut trace_seq,
                        CloudCommandKind::Status,
                        "codex",
                        &["cloud".to_string(), "status".to_string(), task.id.clone()],
                    );
                    traces.push(status_capture.trace.clone());
                    return fail_execution(
                        run,
                        traces,
                        &task.status,
                        append_status_trace_detail(
                            format!(
                                "Codex cloud task {} ended with status {}",
                                task.id, task.status
                            ),
                            &status_capture.trace,
                        ),
                    );
                }
            }
        }

        let before_apply = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitStatusBeforeApply,
            "git",
            &["status".to_string(), "--porcelain".to_string()],
        );
        traces.push(before_apply.trace.clone());
        if !before_apply.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_status_before_apply_failed",
                "git status failed before codex apply",
                &before_apply.trace,
            );
        }

        let apply_capture = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::Apply,
            "codex",
            &["apply".to_string(), task_id.clone()],
        );
        traces.push(apply_capture.trace.clone());
        if !apply_capture.success {
            return fail_execution_from_trace(
                run,
                traces,
                "apply_failed",
                "codex apply failed",
                &apply_capture.trace,
            );
        }

        let after_apply = self.run_command_capture(
            &run,
            &mut trace_seq,
            CloudCommandKind::GitStatusAfterApply,
            "git",
            &["status".to_string(), "--porcelain".to_string()],
        );
        traces.push(after_apply.trace.clone());
        if !after_apply.success {
            return fail_execution_from_trace(
                run,
                traces,
                "git_status_after_apply_failed",
                "git status failed after codex apply",
                &after_apply.trace,
            );
        }

        let new_apply_paths = newly_changed_paths(&before_apply.stdout, &after_apply.stdout);
        run.new_apply_paths = new_apply_paths.clone();
        let unexpected_paths = unexpected_apply_paths(&new_apply_paths, &run.allowed_apply_paths);
        if !unexpected_paths.is_empty() {
            return fail_execution(
                run,
                traces,
                "apply_dirty_paths_unexpected",
                format!(
                    "codex apply introduced unexpected paths: {}",
                    unexpected_paths.join(", ")
                ),
            );
        }
        if !artifacts.output_path.exists() {
            return fail_execution(
                run,
                traces,
                "output_missing",
                format!(
                    "expected Codex output artifact {} was not created",
                    artifacts.output_rel
                ),
            );
        }

        let json = match fs::read_to_string(&artifacts.output_path) {
            Ok(json) => json,
            Err(error) => {
                return fail_execution(
                    run,
                    traces,
                    "output_read_failed",
                    format!(
                        "failed to read Codex output file {}: {error}",
                        artifacts.output_rel
                    ),
                );
            }
        };
        let output: CodexSummaryFile = match serde_json::from_str(&json) {
            Ok(output) => output,
            Err(error) => {
                return fail_execution(
                    run,
                    traces,
                    "output_invalid_json",
                    format!("invalid Codex output JSON: {error}"),
                );
            }
        };
        match output.into_cloud_summary_result() {
            Ok(result) => cloud_execution(run, traces, Some(result)),
            Err(error) => fail_execution(run, traces, "output_invalid", error),
        }
    }

    fn ensure_command_success(
        &self,
        traces: &mut Vec<CloudCommandTrace>,
        trace_seq: &mut u32,
        run: &mut CloudTaskRun,
        kind: CloudCommandKind,
        program: &str,
        args: &[&str],
        message: &str,
        status: &str,
    ) -> bool {
        let capture = self.run_command_capture(
            run,
            trace_seq,
            kind,
            program,
            &args
                .iter()
                .map(|value| (*value).to_string())
                .collect::<Vec<_>>(),
        );
        traces.push(capture.trace.clone());
        if capture.success {
            true
        } else {
            run.finished_at = Some(Utc::now());
            run.final_status = status.to_string();
            run.error_text = Some(command_failure_message(message, &capture.trace));
            false
        }
    }

    fn artifacts_for(&self, packet: &CloudTaskPacket, attempt_index: u32) -> AttemptArtifacts {
        let stem = format!("{}.attempt-{attempt_index:03}", packet.packet_id.0);
        let packet_path = self.config.packet_dir.join(format!("{stem}.packet.json"));
        let schema_path = self
            .config
            .schema_dir
            .join(format!("{stem}.summary.schema.json"));
        let output_path = self.config.output_dir.join(format!("{stem}.summary.json"));
        AttemptArtifacts {
            packet_rel: self.repo_relative_path(&packet_path),
            schema_rel: self.repo_relative_path(&schema_path),
            output_rel: self.repo_relative_path(&output_path),
            packet_path,
            schema_path,
            output_path,
        }
    }

    fn repo_relative_path(&self, path: &Path) -> String {
        path.strip_prefix(&self.config.repo_root)
            .unwrap_or(path)
            .to_string_lossy()
            .to_string()
    }

    fn resolve_recent_task(
        &self,
        run: &CloudTaskRun,
        traces: &mut Vec<CloudCommandTrace>,
        trace_seq: &mut u32,
        submitted_at: DateTime<Utc>,
    ) -> Result<ResolvedTask, (String, String)> {
        let list_capture = self.run_command_capture(
            run,
            trace_seq,
            CloudCommandKind::List,
            "codex",
            &[
                "cloud".to_string(),
                "list".to_string(),
                "--env".to_string(),
                self.config.env_id.clone(),
                "--limit".to_string(),
                MAX_LIST_LIMIT.to_string(),
                "--json".to_string(),
            ],
        );
        traces.push(list_capture.trace.clone());
        if !list_capture.success {
            return Err((
                "list_failed".to_string(),
                command_failure_message(
                    "codex cloud list failed during task resolution",
                    &list_capture.trace,
                ),
            ));
        }
        let listing = parse_list_response(&list_capture.stdout)
            .map_err(|message| ("list_parse_failed".to_string(), message))?;
        resolve_recent_task_candidate(&listing.tasks, &self.config.env_id, submitted_at)
            .map_err(|message| ("task_resolution_failed".to_string(), message))
    }

    fn build_prompt(
        &self,
        packet: &CloudTaskPacket,
        output_rel: &str,
        packet_json: &str,
    ) -> String {
        let output_template = build_output_template(packet);
        let work_unit_hints = build_work_unit_hints(packet);
        format!(
            r#"PACKET_ID: {}
HANDOFF_MODE: inline_packet_visible_output_v3
WRITE_EXACTLY_ONE_FILE: {}
WRITE_RULE: create or overwrite that file and leave every other repo file unchanged

Task:
- create the JSON file
- make sure the task produces a git diff
- a minimal valid JSON file is better than no diff

Recommended write method:
1. run `mkdir -p codex_apply_out`
2. write the JSON file directly
3. verify the file exists before finishing

Use only the inline packet JSON below.
Do not read packet or schema files from disk.
Create the parent directory if needed.
Do not modify any other file.
Do not print any prose.
Use only evidence from the packet's visible content.
Return one fragment per work unit.
Do not invent ids; reuse the exact ids from the output template.
If unsure about wording, keep the summary conservative and add unresolved questions.
If unsure about evidence spans, keep evidence node ids valid and use start 0 end 1.

BEGIN_OUTPUT_CONTRACT
{{"packet_id":"{}","model_name":"<string>","fragments":[{{"target_node_id":"<string>","summary_title":"<string>","summary_text":"<string>","unresolved_questions":["<string>"],"evidence":[{{"node_id":"<string>","start":0,"end":0}}]}}]}}
END_OUTPUT_CONTRACT

BEGIN_OUTPUT_TEMPLATE
{}
END_OUTPUT_TEMPLATE

BEGIN_WORK_UNIT_HINTS
{}
END_WORK_UNIT_HINTS

BEGIN_CLEANROOM_PACKET_JSON
{}
END_CLEANROOM_PACKET_JSON"#,
            packet.packet_id.0,
            output_rel,
            packet.packet_id.0,
            output_template,
            work_unit_hints,
            packet_json,
        )
    }

    fn load_env_manifest(&self) -> Result<CodexCloudEnvManifest, (String, String)> {
        let manifest_path = self.config.repo_root.join(ENV_MANIFEST_REL_PATH);
        let manifest_text = fs::read_to_string(&manifest_path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                (
                    "environment_manifest_missing".to_string(),
                    format!(
                        "missing tracked Codex environment manifest at {}",
                        manifest_path.display()
                    ),
                )
            } else {
                (
                    "environment_manifest_invalid".to_string(),
                    format!(
                        "failed to read Codex environment manifest {}: {error}",
                        manifest_path.display()
                    ),
                )
            }
        })?;
        let mut manifest: CodexCloudEnvManifest =
            serde_json::from_str(&manifest_text).map_err(|error| {
                (
                    "environment_manifest_invalid".to_string(),
                    format!(
                        "invalid Codex environment manifest {}: {error}",
                        manifest_path.display()
                    ),
                )
            })?;
        if manifest.version != 1 {
            return Err((
                "environment_manifest_invalid".to_string(),
                format!(
                    "unsupported Codex environment manifest version {} in {}",
                    manifest.version,
                    manifest_path.display()
                ),
            ));
        }
        manifest.environment_id = manifest.environment_id.trim().to_string();
        manifest.environment_label = manifest
            .environment_label
            .take()
            .map(|label| label.trim().to_string())
            .filter(|label| !label.is_empty());
        if manifest.environment_id.is_empty() {
            return Err((
                "environment_manifest_invalid".to_string(),
                format!(
                    "manifest {} must include a non-empty environment_id",
                    manifest_path.display()
                ),
            ));
        }
        let mut normalized_identities = Vec::new();
        for identity in &manifest.allowed_fetch_remote_identities {
            let normalized = normalize_remote_identity(identity).ok_or_else(|| {
                (
                    "environment_manifest_invalid".to_string(),
                    format!(
                        "manifest {} contains an invalid allowed_fetch_remote_identities entry: {}",
                        manifest_path.display(),
                        identity
                    ),
                )
            })?;
            normalized_identities.push(normalized);
        }
        normalized_identities.sort();
        normalized_identities.dedup();
        if normalized_identities.is_empty() {
            return Err((
                "environment_manifest_unbound".to_string(),
                format!(
                    "manifest {} must declare at least one allowed_fetch_remote_identity",
                    manifest_path.display()
                ),
            ));
        }
        manifest.allowed_fetch_remote_identities = normalized_identities;
        Ok(manifest)
    }

    fn write_packet(
        &self,
        packet: &CloudTaskPacket,
        path: &Path,
    ) -> Result<(), crate::error::PipelineError> {
        let json = serde_json::to_string_pretty(packet).map_err(|e| {
            crate::error::PipelineError::Serde(format!("failed to serialize packet: {e}"))
        })?;
        fs::write(path, json).map_err(|e| {
            crate::error::PipelineError::Storage(format!(
                "failed to write packet {}: {e}",
                path.display()
            ))
        })
    }

    fn write_output_schema(&self, path: &Path) -> Result<(), crate::error::PipelineError> {
        fs::write(path, codex_output_schema()).map_err(|e| {
            crate::error::PipelineError::Storage(format!(
                "failed to write schema {}: {e}",
                path.display()
            ))
        })
    }

    fn run_command_capture(
        &self,
        run: &CloudTaskRun,
        trace_seq: &mut u32,
        command_kind: CloudCommandKind,
        program: &str,
        args: &[String],
    ) -> CommandCapture {
        *trace_seq += 1;
        let started_at = Utc::now();
        let output = Command::new(program)
            .args(args)
            .current_dir(&self.config.repo_root)
            .output();
        let finished_at = Utc::now();
        match output {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                CommandCapture {
                    trace: CloudCommandTrace {
                        trace_id: trace_id_for(run, *trace_seq, &command_kind),
                        cloud_run_id: run.cloud_run_id.clone(),
                        attempt_index: run.attempt_index,
                        command_kind,
                        command_text: render_command_text(program, args),
                        started_at,
                        finished_at,
                        exit_status: output.status.code(),
                        stdout_summary: summarize_text(&stdout),
                        stderr_summary: summarize_text(&stderr),
                    },
                    stdout,
                    success: output.status.success(),
                }
            }
            Err(error) => CommandCapture {
                trace: CloudCommandTrace {
                    trace_id: trace_id_for(run, *trace_seq, &command_kind),
                    cloud_run_id: run.cloud_run_id.clone(),
                    attempt_index: run.attempt_index,
                    command_kind,
                    command_text: render_command_text(program, args),
                    started_at,
                    finished_at,
                    exit_status: None,
                    stdout_summary: None,
                    stderr_summary: Some(format!("failed to launch command: {error}")),
                },
                stdout: String::new(),
                success: false,
            },
        }
    }
}

fn build_output_template(packet: &CloudTaskPacket) -> String {
    let fragments = packet
        .work_units
        .iter()
        .map(|work_unit| {
            let fallback_node_id = work_unit
                .visible_node_ids
                .first()
                .cloned()
                .unwrap_or_else(|| work_unit.target_node_id.clone());
            json!({
                "target_node_id": work_unit.target_node_id.0,
                "summary_title": "",
                "summary_text": "",
                "unresolved_questions": [],
                "evidence": [
                    {
                        "node_id": fallback_node_id.0,
                        "start": 0,
                        "end": 1
                    }
                ]
            })
        })
        .collect::<Vec<_>>();

    serde_json::to_string_pretty(&json!({
        "packet_id": packet.packet_id.0,
        "model_name": "codex-cloud",
        "fragments": fragments
    }))
    .unwrap_or_else(|_| "{}".to_string())
}

fn build_work_unit_hints(packet: &CloudTaskPacket) -> String {
    packet
        .work_units
        .iter()
        .map(|work_unit| {
            let visible_ids = work_unit
                .visible_node_ids
                .iter()
                .map(|node_id| node_id.0.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "TARGET_NODE_ID={} | ALLOWED_EVIDENCE_NODE_IDS=[{}]",
                work_unit.target_node_id.0, visible_ids
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CodexSummaryFile {
    packet_id: String,
    model_name: String,
    fragments: Vec<CodexSummaryFragment>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CodexSummaryFragment {
    target_node_id: String,
    summary_title: String,
    summary_text: String,
    unresolved_questions: Vec<String>,
    evidence: Vec<CodexEvidenceRef>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CodexEvidenceRef {
    node_id: String,
    start: usize,
    end: usize,
}

impl CodexSummaryFile {
    fn into_cloud_summary_result(self) -> Result<CloudSummaryResult, String> {
        let fragments = self
            .fragments
            .into_iter()
            .map(|fragment| SummaryFragment {
                target_node_id: NodeId(fragment.target_node_id),
                summary_title: fragment.summary_title,
                summary_text: fragment.summary_text,
                unresolved_questions: fragment.unresolved_questions,
                evidence: fragment
                    .evidence
                    .into_iter()
                    .map(|evidence| EvidenceRef {
                        node_id: NodeId(evidence.node_id),
                        span: Span {
                            start: evidence.start,
                            end: evidence.end,
                        },
                    })
                    .collect(),
            })
            .collect();
        Ok(CloudSummaryResult {
            packet_id: PacketId(self.packet_id),
            model_name: self.model_name,
            fragments,
        })
    }
}

fn cloud_run_id(packet: &CloudTaskPacket, attempt_index: u32) -> String {
    format!("cloudrun_{}_attempt_{attempt_index:03}", packet.packet_id.0)
}

fn trace_id_for(run: &CloudTaskRun, sequence: u32, kind: &CloudCommandKind) -> String {
    format!("trace_{}_{}_{}", run.cloud_run_id, sequence, kind.as_str())
}

fn cloud_execution(
    run: CloudTaskRun,
    command_traces: Vec<CloudCommandTrace>,
    result: Option<CloudSummaryResult>,
) -> CloudExecution {
    CloudExecution {
        run,
        command_traces,
        result,
    }
}

fn fail_execution(
    mut run: CloudTaskRun,
    command_traces: Vec<CloudCommandTrace>,
    final_status: &str,
    error_text: String,
) -> CloudExecution {
    run.final_status = final_status.to_string();
    run.finished_at = Some(Utc::now());
    run.error_text = Some(error_text);
    cloud_execution(run, command_traces, None)
}

fn fail_execution_from_trace(
    run: CloudTaskRun,
    command_traces: Vec<CloudCommandTrace>,
    final_status: &str,
    message: &str,
    trace: &CloudCommandTrace,
) -> CloudExecution {
    fail_execution(
        run,
        command_traces,
        final_status,
        command_failure_message(message, trace),
    )
}

fn command_failure_message(message: &str, trace: &CloudCommandTrace) -> String {
    match &trace.stderr_summary {
        Some(stderr) if !stderr.is_empty() => format!("{message}: {stderr}"),
        _ => message.to_string(),
    }
}

fn append_status_trace_detail(base_message: String, trace: &CloudCommandTrace) -> String {
    match status_trace_excerpt(trace) {
        Some(excerpt) => format!("{base_message}: {excerpt}"),
        None => base_message,
    }
}

fn status_trace_excerpt(trace: &CloudCommandTrace) -> Option<String> {
    let stdout = trace
        .stdout_summary
        .as_deref()
        .filter(|value| !value.is_empty());
    let stderr = trace
        .stderr_summary
        .as_deref()
        .filter(|value| !value.is_empty());
    match (stdout, stderr) {
        (Some(stdout), Some(stderr)) if stdout != stderr => {
            Some(format!("status stdout={stdout}; stderr={stderr}"))
        }
        (Some(stdout), _) => Some(format!("status stdout={stdout}")),
        (_, Some(stderr)) => Some(format!("status stderr={stderr}")),
        _ => trace
            .exit_status
            .map(|exit_status| format!("status exit_status={exit_status}")),
    }
}

fn parse_list_response(stdout: &str) -> Result<CodexCloudListResponse, String> {
    serde_json::from_str(stdout).map_err(|e| format!("failed to parse codex cloud list JSON: {e}"))
}

fn classify_task_status(status: &str) -> Result<TaskStatusClass, String> {
    if ACTIVE_TASK_STATUSES.contains(&status) {
        Ok(TaskStatusClass::Active)
    } else if SUCCESS_TASK_STATUSES.contains(&status) {
        Ok(TaskStatusClass::Success)
    } else if FAILURE_TASK_STATUSES.contains(&status) {
        Ok(TaskStatusClass::Failure)
    } else {
        Err(format!("unknown Codex cloud task status: {status}"))
    }
}

fn extract_task_from_exec_stdout(stdout: &str) -> Option<ResolvedTask> {
    let task_url_re =
        Regex::new(r"(https://chatgpt\.com/codex/tasks/(?P<id>task_[A-Za-z0-9_]+))").ok()?;
    if let Some(captures) = task_url_re.captures(stdout) {
        return Some(ResolvedTask {
            task_id: captures.name("id")?.as_str().to_string(),
            task_url: Some(captures.get(1)?.as_str().to_string()),
            resolution_method: "stdout_task_url".to_string(),
        });
    }
    let task_id_re = Regex::new(r"\b(task_[A-Za-z0-9_]+)\b").ok()?;
    task_id_re.find(stdout).map(|matched| ResolvedTask {
        task_id: matched.as_str().to_string(),
        task_url: None,
        resolution_method: "stdout_task_id".to_string(),
    })
}

fn resolve_recent_task_candidate(
    tasks: &[CodexCloudTaskInfo],
    env_ref: &str,
    submitted_at: DateTime<Utc>,
) -> Result<ResolvedTask, String> {
    let earliest = submitted_at - ChronoDuration::seconds(30);
    let mut candidates = Vec::new();
    for task in tasks {
        let updated_at = DateTime::parse_from_rfc3339(&task.updated_at)
            .map_err(|e| format!("invalid task updated_at {}: {e}", task.updated_at))?
            .with_timezone(&Utc);
        if updated_at < earliest {
            continue;
        }
        classify_task_status(&task.status)?;
        if !environment_matches(task, env_ref) {
            continue;
        }
        candidates.push(task.clone());
    }
    match candidates.len() {
        1 => {
            let task = candidates.remove(0);
            Ok(ResolvedTask {
                task_id: task.id,
                task_url: Some(task.url),
                resolution_method: "recent_task_list".to_string(),
            })
        }
        0 => Err("no recent Codex cloud task matched the current submission window".to_string()),
        _ => Err(
            "multiple recent Codex cloud tasks matched the current submission window".to_string(),
        ),
    }
}

fn environment_matches(task: &CodexCloudTaskInfo, env_ref: &str) -> bool {
    matches!(task.environment_id.as_deref(), Some(environment_id) if environment_id == env_ref)
}

fn environment_id_mismatch_message(env_id: &str, manifest: &CodexCloudEnvManifest) -> String {
    match manifest.environment_label.as_deref() {
        Some(environment_label) => format!(
            "CODEX_CLOUD_ENV_ID {} did not match manifest environment_id {} ({})",
            env_id, manifest.environment_id, environment_label
        ),
        None => format!(
            "CODEX_CLOUD_ENV_ID {} did not match manifest environment_id {}",
            env_id, manifest.environment_id
        ),
    }
}

fn dirty_worktree_message(current_branch: Option<&str>) -> String {
    match current_branch {
        Some(current_branch) => format!(
            "git worktree is dirty on branch {}; commit, stash, or clean changes before live Codex runs",
            current_branch
        ),
        None => "git worktree is dirty; commit, stash, or clean changes before live Codex runs"
            .to_string(),
    }
}

fn render_command_text(program: &str, args: &[String]) -> String {
    let mut rendered = Vec::with_capacity(args.len() + 1);
    rendered.push(program.to_string());
    rendered.extend(args.iter().cloned());
    if rendered.len() > 2
        && rendered[0] == "codex"
        && rendered[1] == "cloud"
        && rendered.get(2) == Some(&"exec".to_string())
    {
        if let Some(last) = rendered.last_mut() {
            *last = "<prompt omitted>".to_string();
        }
    }
    rendered.join(" ")
}

fn summarize_text(text: &str) -> Option<String> {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        None
    } else if collapsed.len() > 280 {
        Some(format!("{}...", &collapsed[..280]))
    } else {
        Some(collapsed)
    }
}

fn normalize_remote_identity(input: &str) -> Option<String> {
    let trimmed = input.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return None;
    }
    if let Some(rest) = trimmed.strip_prefix("git@") {
        let (host, path) = rest.split_once(':')?;
        return normalize_host_and_path(host, path);
    }
    if let Some(rest) = trimmed
        .strip_prefix("https://")
        .or_else(|| trimmed.strip_prefix("http://"))
    {
        let (host, path) = rest.split_once('/')?;
        return normalize_host_and_path(host, path);
    }
    if let Some(rest) = trimmed.strip_prefix("ssh://") {
        let rest = rest.strip_prefix("git@").unwrap_or(rest);
        let (host, path) = rest.split_once('/')?;
        return normalize_host_and_path(host, path);
    }
    normalize_host_and_path_from_identity(trimmed)
}

fn normalize_host_and_path(host: &str, path: &str) -> Option<String> {
    let host = host.trim().trim_end_matches('/');
    let path = path
        .trim()
        .trim_start_matches('/')
        .trim_end_matches('/')
        .trim_end_matches(".git");
    if host.is_empty() || path.is_empty() {
        return None;
    }
    let parts = path.split('/').collect::<Vec<_>>();
    if parts.len() != 2 || parts.iter().any(|part| part.is_empty()) {
        return None;
    }
    Some(format!(
        "{}/{}/{}",
        host.to_ascii_lowercase(),
        parts[0].to_ascii_lowercase(),
        parts[1].to_ascii_lowercase()
    ))
}

fn normalize_host_and_path_from_identity(identity: &str) -> Option<String> {
    let trimmed = identity.trim().trim_end_matches(".git");
    let parts = trimmed.split('/').collect::<Vec<_>>();
    if parts.len() != 3 || parts.iter().any(|part| part.is_empty()) {
        return None;
    }
    Some(format!(
        "{}/{}/{}",
        parts[0].to_ascii_lowercase(),
        parts[1].to_ascii_lowercase(),
        parts[2].to_ascii_lowercase()
    ))
}

fn parse_fetch_remote_bindings(stdout: &str) -> Vec<FetchRemoteBinding> {
    let mut bindings = Vec::new();
    for line in stdout.lines() {
        let parts = line.split_whitespace().collect::<Vec<_>>();
        if parts.len() < 3 || parts[2] != "(fetch)" {
            continue;
        }
        if let Some(identity) = normalize_remote_identity(parts[1]) {
            bindings.push(FetchRemoteBinding {
                remote_name: parts[0].to_string(),
                identity,
            });
        }
    }
    bindings
}

fn matching_fetch_remote_bindings(
    bindings: &[FetchRemoteBinding],
    allowed_identities: &[String],
) -> Vec<FetchRemoteBinding> {
    bindings
        .iter()
        .filter(|binding| allowed_identities.contains(&binding.identity))
        .cloned()
        .collect()
}

fn render_fetch_remote_identities(bindings: &[FetchRemoteBinding]) -> String {
    let mut identities = bindings
        .iter()
        .map(|binding| binding.identity.clone())
        .collect::<Vec<_>>();
    identities.sort();
    identities.dedup();
    if identities.is_empty() {
        "<none>".to_string()
    } else {
        identities.join(", ")
    }
}

fn parse_current_branch(status_output: &str) -> Option<String> {
    let header = status_output.lines().find(|line| line.starts_with("## "))?;
    let branch_info = header.trim_start_matches("## ").trim();
    if branch_info.starts_with("HEAD") {
        return None;
    }
    if let Some(branch) = branch_info.strip_prefix("No commits yet on ") {
        return Some(branch.trim().to_string());
    }
    let branch = branch_info
        .split_once("...")
        .map(|(branch, _)| branch)
        .unwrap_or(branch_info)
        .trim();
    if branch.is_empty() {
        None
    } else {
        Some(branch.to_string())
    }
}

fn worktree_is_dirty(status_output: &str) -> bool {
    status_output
        .lines()
        .any(|line| !line.trim().is_empty() && !line.starts_with("## "))
}

fn parse_head_sha(stdout: &str) -> Option<String> {
    stdout
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| line.to_string())
}

fn head_is_contained_in_allowed_remote_ref(stdout: &str, allowed_remote_names: &[&str]) -> bool {
    stdout.lines().any(|line| {
        let trimmed = line.trim().trim_start_matches('*').trim();
        if trimmed.is_empty() || trimmed.contains("->") {
            return false;
        }
        let remote_name = trimmed.split('/').next().unwrap_or_default();
        allowed_remote_names.contains(&remote_name)
    })
}

fn parse_dirty_paths(status_output: &str) -> Vec<String> {
    let mut paths = Vec::new();
    for line in status_output.lines() {
        let trimmed = line.trim_end();
        if trimmed.len() < 4 {
            continue;
        }
        let path = trimmed[3..].trim();
        if !path.is_empty() {
            paths.push(path.to_string());
        }
    }
    paths.sort();
    paths.dedup();
    paths
}

fn newly_changed_paths(before: &str, after: &str) -> Vec<String> {
    let before_paths = parse_dirty_paths(before);
    let after_paths = parse_dirty_paths(after);
    after_paths
        .into_iter()
        .filter(|path| !before_paths.contains(path))
        .collect()
}

fn unexpected_apply_paths(new_paths: &[String], allowed_paths: &[String]) -> Vec<String> {
    new_paths
        .iter()
        .filter(|path| !allowed_paths.contains(path))
        .cloned()
        .collect()
}

fn codex_output_schema() -> &'static str {
    r#"{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["packet_id", "model_name", "fragments"],
  "properties": {
    "packet_id": { "type": "string" },
    "model_name": { "type": "string" },
    "fragments": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "target_node_id",
          "summary_title",
          "summary_text",
          "unresolved_questions",
          "evidence"
        ],
        "properties": {
          "target_node_id": { "type": "string" },
          "summary_title": { "type": "string" },
          "summary_text": { "type": "string" },
          "unresolved_questions": {
            "type": "array",
            "items": { "type": "string" }
          },
          "evidence": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["node_id", "start", "end"],
              "properties": {
                "node_id": { "type": "string" },
                "start": { "type": "integer", "minimum": 0 },
                "end": { "type": "integer", "minimum": 0 }
              }
            }
          }
        }
      }
    }
  }
}"#
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;
    use crate::model::DocumentId;

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn codex_summary_file_maps_into_internal_result() {
        let result = CodexSummaryFile {
            packet_id: "pkt_1".to_string(),
            model_name: "codex-cloud".to_string(),
            fragments: vec![CodexSummaryFragment {
                target_node_id: "node_1".to_string(),
                summary_title: "Title".to_string(),
                summary_text: "Summary".to_string(),
                unresolved_questions: vec!["Q".to_string()],
                evidence: vec![CodexEvidenceRef {
                    node_id: "node_e".to_string(),
                    start: 1,
                    end: 2,
                }],
            }],
        }
        .into_cloud_summary_result()
        .unwrap();

        assert_eq!(result.packet_id.0, "pkt_1");
        assert_eq!(result.fragments.len(), 1);
        assert_eq!(result.fragments[0].evidence[0].span.start, 1);
    }

    #[test]
    fn parses_realistic_codex_cloud_list_shape() {
        let listing = parse_list_response(
            r#"{
  "tasks": [
    {
      "id": "task_e_123",
      "url": "https://chatgpt.com/codex/tasks/task_e_123",
      "status": "error",
      "updated_at": "2026-04-23T21:11:03.331Z",
      "environment_id": "env_123",
      "environment_label": "suits",
      "summary": {
        "files_changed": 0,
        "lines_added": 0,
        "lines_removed": 0
      }
    }
  ],
  "cursor": null
}"#,
        )
        .unwrap();

        assert_eq!(listing.tasks.len(), 1);
        assert_eq!(listing.tasks[0].environment_id.as_deref(), Some("env_123"));
        assert_eq!(
            listing.tasks[0].summary.as_ref().unwrap().files_changed,
            Some(0)
        );
    }

    #[test]
    fn extracts_task_id_from_exec_stdout_url() {
        let resolved = extract_task_from_exec_stdout(
            "submitted https://chatgpt.com/codex/tasks/task_e_abc123",
        )
        .unwrap();

        assert_eq!(resolved.task_id, "task_e_abc123");
        assert_eq!(resolved.resolution_method, "stdout_task_url");
    }

    #[test]
    fn resolves_unique_recent_task_candidate() {
        let submitted_at = DateTime::parse_from_rfc3339("2026-04-23T21:10:50Z")
            .unwrap()
            .with_timezone(&Utc);
        let resolved = resolve_recent_task_candidate(
            &[CodexCloudTaskInfo {
                id: "task_e_123".to_string(),
                url: "https://chatgpt.com/codex/tasks/task_e_123".to_string(),
                title: None,
                status: "running".to_string(),
                updated_at: "2026-04-23T21:11:03.331Z".to_string(),
                environment_id: Some("env_123".to_string()),
                environment_label: Some("suits".to_string()),
                summary: None,
                is_review: None,
                attempt_total: None,
            }],
            "env_123",
            submitted_at,
        )
        .unwrap();

        assert_eq!(resolved.task_id, "task_e_123");
        assert_eq!(resolved.resolution_method, "recent_task_list");
    }

    #[test]
    fn recent_task_resolution_requires_environment_id_not_label() {
        let submitted_at = DateTime::parse_from_rfc3339("2026-04-23T21:10:50Z")
            .unwrap()
            .with_timezone(&Utc);
        let error = resolve_recent_task_candidate(
            &[CodexCloudTaskInfo {
                id: "task_e_123".to_string(),
                url: "https://chatgpt.com/codex/tasks/task_e_123".to_string(),
                title: None,
                status: "running".to_string(),
                updated_at: "2026-04-23T21:11:03.331Z".to_string(),
                environment_id: None,
                environment_label: Some("suits".to_string()),
                summary: None,
                is_review: None,
                attempt_total: None,
            }],
            "env_123",
            submitted_at,
        )
        .unwrap_err();

        assert!(error.contains("no recent Codex cloud task matched"));
    }

    #[test]
    fn ambiguous_recent_task_resolution_fails_closed() {
        let submitted_at = DateTime::parse_from_rfc3339("2026-04-23T21:10:50Z")
            .unwrap()
            .with_timezone(&Utc);
        let error = resolve_recent_task_candidate(
            &[
                CodexCloudTaskInfo {
                    id: "task_e_123".to_string(),
                    url: "https://chatgpt.com/codex/tasks/task_e_123".to_string(),
                    title: None,
                    status: "running".to_string(),
                    updated_at: "2026-04-23T21:11:03.331Z".to_string(),
                    environment_id: Some("env_123".to_string()),
                    environment_label: Some("suits".to_string()),
                    summary: None,
                    is_review: None,
                    attempt_total: None,
                },
                CodexCloudTaskInfo {
                    id: "task_e_456".to_string(),
                    url: "https://chatgpt.com/codex/tasks/task_e_456".to_string(),
                    title: None,
                    status: "queued".to_string(),
                    updated_at: "2026-04-23T21:11:04.331Z".to_string(),
                    environment_id: Some("env_123".to_string()),
                    environment_label: Some("suits".to_string()),
                    summary: None,
                    is_review: None,
                    attempt_total: None,
                },
            ],
            "env_123",
            submitted_at,
        )
        .unwrap_err();

        assert!(error.contains("multiple recent Codex cloud tasks"));
    }

    #[test]
    fn unknown_task_status_fails_closed() {
        let error = classify_task_status("mystery").unwrap_err();
        assert!(error.contains("unknown Codex cloud task status"));
    }

    #[test]
    fn dirty_repo_guard_excludes_preexisting_paths() {
        let before = "?? .cleanroom/packets/pkt.attempt-001.packet.json\n M README.md\n?? .cleanroom/schema/pkt.attempt-001.summary.schema.json\n";
        let after = "?? .cleanroom/packets/pkt.attempt-001.packet.json\n M README.md\n?? .cleanroom/schema/pkt.attempt-001.summary.schema.json\n?? codex_apply_out/pkt.attempt-001.summary.json\n";

        let new_paths = newly_changed_paths(before, after);

        assert_eq!(
            new_paths,
            vec!["codex_apply_out/pkt.attempt-001.summary.json"]
        );
    }

    #[test]
    fn unexpected_apply_paths_fail_exact_attempt_set() {
        let unexpected = unexpected_apply_paths(
            &[
                "codex_apply_out/pkt.attempt-001.summary.json".to_string(),
                "README.md".to_string(),
            ],
            &["codex_apply_out/pkt.attempt-001.summary.json".to_string()],
        );

        assert_eq!(unexpected, vec!["README.md".to_string()]);
    }

    #[test]
    fn attempt_artifacts_are_attempt_specific() {
        let config = CodexCloudConfig::new("/tmp/skynet", "env_123");
        let worker = CodexCloudWorker::new(config).unwrap();
        let packet = CloudTaskPacket {
            packet_id: PacketId("pkt_alpha".to_string()),
            document_id: DocumentId("doc_alpha".to_string()),
            work_units: vec![],
            style_contract: "style".to_string(),
            completion_contract: "done".to_string(),
        };

        let first = worker.artifacts_for(&packet, 1);
        let second = worker.artifacts_for(&packet, 2);

        assert_ne!(first.packet_rel, second.packet_rel);
        assert!(first.packet_rel.contains("attempt-001"));
        assert!(second.output_rel.contains("attempt-002"));
        assert!(first.output_rel.starts_with("codex_apply_out/"));
    }

    #[test]
    fn trace_ids_include_attempt_identity() {
        let packet = CloudTaskPacket {
            packet_id: PacketId("pkt_alpha".to_string()),
            document_id: DocumentId("doc_alpha".to_string()),
            work_units: vec![],
            style_contract: "style".to_string(),
            completion_contract: "done".to_string(),
        };
        let run = CloudTaskRun {
            cloud_run_id: cloud_run_id(&packet, 3),
            packet_id: packet.packet_id.clone(),
            attempt_index: 3,
            task_id: None,
            task_url: None,
            environment_id: None,
            matched_remote_identity: None,
            current_head_sha: None,
            current_branch: None,
            head_contained_in_allowed_remote_ref: None,
            resolution_method: "unresolved".to_string(),
            handoff_mode: "inline_packet_visible_output_v3".to_string(),
            packet_path: String::new(),
            schema_path: String::new(),
            output_path: String::new(),
            allowed_apply_paths: vec![],
            new_apply_paths: vec![],
            submitted_at: Utc::now(),
            finished_at: None,
            final_status: "submission_started".to_string(),
            error_text: None,
        };

        let trace_id = trace_id_for(&run, 7, &CloudCommandKind::Apply);

        assert!(trace_id.contains("attempt_003"));
        assert!(trace_id.contains("apply"));
    }

    #[test]
    fn prompt_uses_inline_packet_and_visible_output_path() {
        let config = CodexCloudConfig::new("/tmp/skynet", "env_123");
        let worker = CodexCloudWorker::new(config).unwrap();
        let packet = CloudTaskPacket {
            packet_id: PacketId("pkt_inline".to_string()),
            document_id: DocumentId("doc_inline".to_string()),
            work_units: vec![crate::model::WorkUnit {
                work_unit_id: "wu_inline".to_string(),
                target_node_id: NodeId("node_target".to_string()),
                visible_node_ids: vec![
                    NodeId("node_context".to_string()),
                    NodeId("node_target".to_string()),
                ],
                context_node_ids: vec![NodeId("node_context".to_string())],
                trim_map: vec![],
                rendered_text: "rendered".to_string(),
                instructions: vec!["instruction".to_string()],
            }],
            style_contract: "style".to_string(),
            completion_contract: "done".to_string(),
        };
        let packet_json = serde_json::to_string(&packet).unwrap();
        let prompt = worker.build_prompt(
            &packet,
            "codex_apply_out/pkt_inline.attempt-001.summary.json",
            &packet_json,
        );

        assert!(prompt.contains("HANDOFF_MODE: inline_packet_visible_output_v3"));
        assert!(prompt.contains(
            "WRITE_EXACTLY_ONE_FILE: codex_apply_out/pkt_inline.attempt-001.summary.json"
        ));
        assert!(prompt.contains("BEGIN_OUTPUT_CONTRACT"));
        assert!(prompt.contains("END_OUTPUT_CONTRACT"));
        assert!(prompt.contains("BEGIN_CLEANROOM_PACKET_JSON"));
        assert!(prompt.contains("END_CLEANROOM_PACKET_JSON"));
        assert!(prompt.contains("BEGIN_OUTPUT_TEMPLATE"));
        assert!(prompt.contains("END_OUTPUT_TEMPLATE"));
        assert!(prompt.contains("BEGIN_WORK_UNIT_HINTS"));
        assert!(prompt.contains("END_WORK_UNIT_HINTS"));
        assert!(prompt.contains("mkdir -p codex_apply_out"));
        assert!(prompt.contains("minimal valid JSON file is better than no diff"));
        assert!(prompt.contains(r#""target_node_id": "node_target""#));
        assert!(prompt.contains("TARGET_NODE_ID=node_target | ALLOWED_EVIDENCE_NODE_IDS=[node_context, node_target]"));
        assert!(prompt.contains(&packet_json));
        assert!(!prompt.contains("Read the packet at:"));
        assert!(!prompt.contains("schema at:"));
        assert!(prompt.contains(r#""packet_id":"pkt_inline""#));
    }

    #[test]
    fn status_trace_excerpt_is_preserved_even_for_nonzero_status_command() {
        let trace = CloudCommandTrace {
            trace_id: "trace_status".to_string(),
            cloud_run_id: "cloudrun_pkt_inline_attempt_001".to_string(),
            attempt_index: 1,
            command_kind: CloudCommandKind::Status,
            command_text: "codex cloud status task_e_123".to_string(),
            started_at: Utc::now(),
            finished_at: Utc::now(),
            exit_status: Some(1),
            stdout_summary: Some("[ERROR] Generate summary JSON from packet".to_string()),
            stderr_summary: None,
        };

        let message = append_status_trace_detail(
            "Codex cloud task task_e_123 ended with status error".to_string(),
            &trace,
        );

        assert!(message.contains("Generate summary JSON from packet"));
    }

    #[test]
    fn normalizes_https_and_ssh_remotes_to_same_identity() {
        assert_eq!(
            normalize_remote_identity("https://github.com/Owner/Repo.git").as_deref(),
            Some("github.com/owner/repo")
        );
        assert_eq!(
            normalize_remote_identity("git@github.com:Owner/Repo.git").as_deref(),
            Some("github.com/owner/repo")
        );
    }

    #[test]
    fn parse_fetch_remote_bindings_ignores_push_only_lines() {
        let bindings = parse_fetch_remote_bindings(
            "origin https://github.com/owner/repo.git (fetch)\norigin https://github.com/owner/repo.git (push)\nbackup git@github.com:owner/repo.git (push)\n",
        );

        assert_eq!(
            bindings,
            vec![FetchRemoteBinding {
                remote_name: "origin".to_string(),
                identity: "github.com/owner/repo".to_string(),
            }]
        );
    }

    #[test]
    fn parse_current_branch_handles_common_status_headers() {
        assert_eq!(
            parse_current_branch("## main...origin/main\n"),
            Some("main".to_string())
        );
        assert_eq!(
            parse_current_branch("## No commits yet on draft\n"),
            Some("draft".to_string())
        );
        assert_eq!(parse_current_branch("## HEAD (detached at abc123)\n"), None);
    }

    #[test]
    fn worktree_dirty_detection_ignores_branch_header() {
        assert!(!worktree_is_dirty("## main...origin/main\n"));
        assert!(worktree_is_dirty("## main...origin/main\n M README.md\n"));
        assert!(worktree_is_dirty("## main\n?? new.txt\n"));
    }

    #[test]
    fn head_containment_checks_allowed_remote_names_only() {
        assert!(head_is_contained_in_allowed_remote_ref(
            "  origin/main\n  origin/feature\n",
            &["origin"]
        ));
        assert!(!head_is_contained_in_allowed_remote_ref(
            "  upstream/main\n",
            &["origin"]
        ));
        assert!(!head_is_contained_in_allowed_remote_ref(
            "  origin/HEAD -> origin/main\n",
            &["origin"]
        ));
    }

    #[test]
    fn missing_manifest_fails_before_codex_commands() {
        let repo = TempGitRepo::new();
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "environment_manifest_missing");
        assert!(execution.run.task_id.is_none());
        assert_eq!(execution.command_traces.len(), 1);
        assert_eq!(
            execution.command_traces[0].command_kind,
            CloudCommandKind::GitCheckoutCheck
        );
    }

    #[test]
    fn invalid_manifest_fails_closed() {
        let repo = TempGitRepo::new();
        repo.write_manifest_json(serde_json::json!({
            "version": 9,
            "environment_id": "env_123",
            "allowed_fetch_remote_identities": ["github.com/owner/repo"]
        }));
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "environment_manifest_invalid");
        assert!(execution.run.task_id.is_none());
    }

    #[test]
    fn empty_allowed_remote_identities_is_unbound() {
        let repo = TempGitRepo::new();
        repo.write_manifest_json(serde_json::json!({
            "version": 1,
            "environment_id": "env_123",
            "environment_label": "fixture",
            "allowed_fetch_remote_identities": []
        }));
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "environment_manifest_unbound");
        assert!(execution.run.task_id.is_none());
    }

    #[test]
    fn environment_id_mismatch_fails_before_git_status() {
        let repo = TempGitRepo::new();
        repo.write_manifest("env_expected", Some("fixture"), &["github.com/owner/repo"]);
        let worker =
            CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_actual")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "environment_id_mismatch");
        assert_eq!(execution.command_traces.len(), 1);
        assert!(execution.run.current_branch.is_none());
        assert!(execution
            .command_traces
            .iter()
            .all(|trace| trace.command_kind != CloudCommandKind::GitWorktreeCheck));
    }

    #[test]
    fn dirty_worktree_fails_before_remote_and_codex_checks() {
        let repo = TempGitRepo::new();
        repo.write_manifest("env_123", Some("fixture"), &["github.com/owner/repo"]);
        repo.write_file("dirty.txt", "untracked change");
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "git_worktree_dirty");
        assert_eq!(execution.run.current_branch.as_deref(), Some("master"));
        assert!(execution.run.task_id.is_none());
        assert_eq!(execution.command_traces.len(), 2);
        assert_eq!(
            execution.command_traces[1].command_kind,
            CloudCommandKind::GitWorktreeCheck
        );
        assert!(execution
            .command_traces
            .iter()
            .all(|trace| trace.command_kind != CloudCommandKind::LoginStatus));
    }

    #[test]
    fn clean_repo_without_remote_fails_fast() {
        let repo = TempGitRepo::new();
        repo.write_manifest("env_123", Some("fixture"), &["github.com/owner/repo"]);
        repo.commit_all("add manifest");
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "git_remote_missing");
        assert!(execution.run.matched_remote_identity.is_none());
        assert!(execution.run.task_id.is_none());
        assert!(execution
            .command_traces
            .iter()
            .any(|trace| trace.command_kind == CloudCommandKind::GitRemoteCheck));
        assert!(execution
            .command_traces
            .iter()
            .all(|trace| trace.command_kind != CloudCommandKind::LoginStatus));
    }

    #[test]
    fn unmatched_fetch_remote_fails_as_remote_mismatch() {
        let repo = TempGitRepo::new();
        repo.write_manifest("env_123", Some("fixture"), &["github.com/owner/repo"]);
        repo.commit_all("add manifest");
        repo.add_remote("origin", "https://github.com/other/repo.git");
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "git_remote_mismatch");
        assert!(execution.run.matched_remote_identity.is_none());
        assert!(execution.run.task_id.is_none());
    }

    #[test]
    fn matched_remote_without_remote_containment_fails_as_unpushed() {
        let repo = TempGitRepo::new();
        repo.write_manifest("env_123", Some("fixture"), &["github.com/owner/repo"]);
        repo.commit_all("add manifest");
        repo.add_remote("origin", "https://github.com/owner/repo.git");
        let worker = CodexCloudWorker::new(CodexCloudConfig::new(repo.path(), "env_123")).unwrap();

        let execution = worker.execute_summary_task(&sample_packet(), 1);

        assert_eq!(execution.run.final_status, "git_head_unpushed");
        assert_eq!(
            execution.run.matched_remote_identity.as_deref(),
            Some("github.com/owner/repo")
        );
        assert!(execution.run.current_head_sha.is_some());
        assert_eq!(
            execution.run.head_contained_in_allowed_remote_ref,
            Some(false)
        );
        assert!(execution
            .command_traces
            .iter()
            .any(|trace| trace.command_kind == CloudCommandKind::GitHeadCheck));
        assert!(execution
            .command_traces
            .iter()
            .all(|trace| trace.command_kind != CloudCommandKind::LoginStatus));
    }

    fn sample_packet() -> CloudTaskPacket {
        CloudTaskPacket {
            packet_id: PacketId("pkt_alpha".to_string()),
            document_id: DocumentId("doc_alpha".to_string()),
            work_units: vec![],
            style_contract: "style".to_string(),
            completion_contract: "done".to_string(),
        }
    }

    struct TempGitRepo {
        path: PathBuf,
    }

    impl TempGitRepo {
        fn new() -> Self {
            let path = unique_temp_dir();
            fs::create_dir_all(path.join("config")).unwrap();
            run_git(&path, &["init", "--initial-branch=master"]);
            fs::write(path.join("README.md"), "seed\n").unwrap();
            run_git(&path, &["add", "README.md"]);
            run_git(
                &path,
                &[
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "init",
                ],
            );
            Self { path }
        }

        fn path(&self) -> &Path {
            &self.path
        }

        fn write_manifest(
            &self,
            environment_id: &str,
            environment_label: Option<&str>,
            allowed_identities: &[&str],
        ) {
            self.write_manifest_json(serde_json::json!({
                "version": 1,
                "environment_id": environment_id,
                "environment_label": environment_label,
                "allowed_fetch_remote_identities": allowed_identities,
            }));
        }

        fn write_manifest_json(&self, payload: serde_json::Value) {
            fs::write(
                self.path.join(ENV_MANIFEST_REL_PATH),
                serde_json::to_string_pretty(&payload).unwrap(),
            )
            .unwrap();
        }

        fn write_file(&self, relative_path: &str, contents: &str) {
            fs::write(self.path.join(relative_path), contents).unwrap();
        }

        fn add_remote(&self, name: &str, url: &str) {
            run_git(&self.path, &["remote", "add", name, url]);
        }

        fn commit_all(&self, message: &str) {
            run_git(&self.path, &["add", "."]);
            run_git(
                &self.path,
                &[
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    message,
                ],
            );
        }
    }

    impl Drop for TempGitRepo {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    fn unique_temp_dir() -> PathBuf {
        let unique = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("wiki_cleanroom_codex_cloud_{nanos}_{unique}"))
    }

    fn run_git(repo_root: &Path, args: &[&str]) {
        let status = Command::new("git")
            .args(args)
            .current_dir(repo_root)
            .status()
            .unwrap();
        assert!(
            status.success(),
            "git command failed: git {}",
            args.join(" ")
        );
    }
}
