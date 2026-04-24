use std::collections::HashSet;
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::Utc;
use serde::Serialize;
use wiki_cleanroom::cleanroom::Cleanroom;
use wiki_cleanroom::cloud::CodexCloudSummarizer;
use wiki_cleanroom::codex_cloud::CodexCloudConfig;
use wiki_cleanroom::metadata::RuleBasedMetadata;
use wiki_cleanroom::model::{PacketId, RawDocument, ReplayBundle, SourceKind};
use wiki_cleanroom::preflight::RuleBasedPreflight;

const ACCEPTED_SUCCESS_STATUSES: &[&str] = &["completed", "succeeded", "success", "ready"];

type LiveCleanroom = Cleanroom<RuleBasedPreflight, CodexCloudSummarizer, RuleBasedMetadata>;

#[derive(Debug, Serialize)]
struct LiveProofRecord {
    outcome: String,
    proof_generated_at: String,
    packet_id: Option<String>,
    cloud_run_id: Option<String>,
    db_path: String,
    environment_id: String,
    task_id: Option<String>,
    task_url: Option<String>,
    final_status: Option<String>,
    packet_path: Option<String>,
    schema_path: Option<String>,
    output_path: Option<String>,
    matched_remote_identity: Option<String>,
    current_head_sha: Option<String>,
    current_branch: Option<String>,
    head_contained_in_allowed_remote_ref: Option<bool>,
    proof_manifest_path: String,
    error_text: Option<String>,
}

impl LiveProofRecord {
    fn new(db_path: &Path, environment_id: &str, proof_manifest_path: &Path) -> Self {
        Self {
            outcome: "started".to_string(),
            proof_generated_at: Utc::now().to_rfc3339(),
            packet_id: None,
            cloud_run_id: None,
            db_path: db_path.display().to_string(),
            environment_id: environment_id.to_string(),
            task_id: None,
            task_url: None,
            final_status: None,
            packet_path: None,
            schema_path: None,
            output_path: None,
            matched_remote_identity: None,
            current_head_sha: None,
            current_branch: None,
            head_contained_in_allowed_remote_ref: None,
            proof_manifest_path: proof_manifest_path.display().to_string(),
            error_text: None,
        }
    }

    fn attach_packet(&mut self, packet_id: &PacketId) {
        self.packet_id = Some(packet_id.0.clone());
    }

    fn hydrate_from_replay(&mut self, replay: &ReplayBundle) {
        if self.packet_id.is_none() {
            if let Some(packet) = &replay.packet {
                self.packet_id = Some(packet.packet_id.0.clone());
            }
        }
        if let Some(run) = replay.cloud_task_runs.last() {
            self.cloud_run_id = Some(run.cloud_run_id.clone());
            self.task_id = run.task_id.clone();
            self.task_url = run.task_url.clone();
            self.final_status = Some(run.final_status.clone());
            self.packet_path = Some(run.packet_path.clone());
            self.schema_path = Some(run.schema_path.clone());
            self.output_path = Some(run.output_path.clone());
            self.matched_remote_identity = run.matched_remote_identity.clone();
            self.current_head_sha = run.current_head_sha.clone();
            self.current_branch = run.current_branch.clone();
            self.head_contained_in_allowed_remote_ref = run.head_contained_in_allowed_remote_ref;
        }
    }

    fn mark_success(&mut self) {
        self.outcome = "success".to_string();
        self.error_text = None;
        self.proof_generated_at = Utc::now().to_rfc3339();
    }

    fn mark_failure(&mut self, error_text: impl Into<String>) {
        self.outcome = "failure".to_string();
        self.error_text = Some(error_text.into());
        self.proof_generated_at = Utc::now().to_rfc3339();
    }

    fn persist(&self) -> Result<(), Box<dyn std::error::Error>> {
        fs::write(
            &self.proof_manifest_path,
            serde_json::to_string_pretty(self)?,
        )?;
        Ok(())
    }

    fn print(&self) -> Result<(), Box<dyn std::error::Error>> {
        println!("LIVE_PROOF {}", serde_json::to_string(self)?);
        Ok(())
    }
}

#[test]
#[ignore = "requires CODEX_CLOUD_ENV_ID and a live Codex environment"]
fn live_codex_cloud_round_trip() -> Result<(), Box<dyn std::error::Error>> {
    let environment_id = env::var("CODEX_CLOUD_ENV_ID")
        .expect("set CODEX_CLOUD_ENV_ID before running live_codex_cloud_round_trip");
    let proof_dir = create_live_proof_dir()?;
    let db_path = proof_dir.join("cleanroom_live_proof.db");
    let proof_manifest_path = proof_dir.join("proof.json");
    let mut proof = LiveProofRecord::new(&db_path, &environment_id, &proof_manifest_path);

    let codex = match CodexCloudSummarizer::new(CodexCloudConfig::new(
        env!("CARGO_MANIFEST_DIR"),
        environment_id.clone(),
    )) {
        Ok(codex) => codex,
        Err(error) => return fail_live_proof(&mut proof, None, None, error.to_string(), false),
    };
    let mut cleanroom = match Cleanroom::open(
        db_path.to_string_lossy().as_ref(),
        RuleBasedPreflight,
        codex,
        RuleBasedMetadata,
    ) {
        Ok(cleanroom) => cleanroom,
        Err(error) => return fail_live_proof(&mut proof, None, None, error.to_string(), false),
    };

    let packet_id = match cleanroom.ingest_and_stage(seed_document()) {
        Ok(packet_id) => {
            proof.attach_packet(&packet_id);
            packet_id
        }
        Err(error) => {
            proof.mark_failure(error.to_string());
            proof.persist()?;
            proof.print()?;
            return Err(Box::new(error));
        }
    };

    if let Err(error) = cleanroom.run_cloud(&packet_id) {
        return fail_live_proof(
            &mut proof,
            Some(&cleanroom),
            Some(&packet_id),
            error.to_string(),
            true,
        );
    }

    let claimed = match cleanroom.claim_next_review("ace") {
        Ok(Some(item)) => item,
        Ok(None) => {
            return fail_live_proof(
                &mut proof,
                Some(&cleanroom),
                Some(&packet_id),
                "expected one queued review item after successful live cloud run".to_string(),
                false,
            );
        }
        Err(error) => {
            return fail_live_proof(
                &mut proof,
                Some(&cleanroom),
                Some(&packet_id),
                error.to_string(),
                false,
            );
        }
    };

    if let Err(error) = cleanroom.approve_packet(
        &claimed.packet_id,
        Some("approved after live Codex cloud review"),
    ) {
        return fail_live_proof(
            &mut proof,
            Some(&cleanroom),
            Some(&packet_id),
            error.to_string(),
            false,
        );
    }

    if let Err(error) = cleanroom.promote_approved(&claimed.packet_id) {
        return fail_live_proof(
            &mut proof,
            Some(&cleanroom),
            Some(&packet_id),
            error.to_string(),
            false,
        );
    }

    let replay = match cleanroom.replay_packet(&packet_id) {
        Ok(replay) => replay,
        Err(error) => {
            return fail_live_proof(
                &mut proof,
                Some(&cleanroom),
                Some(&packet_id),
                error.to_string(),
                false,
            );
        }
    };
    proof.hydrate_from_replay(&replay);

    if let Err(error_text) = validate_success_replay(&replay) {
        return fail_live_proof(
            &mut proof,
            Some(&cleanroom),
            Some(&packet_id),
            error_text,
            false,
        );
    }

    proof.mark_success();
    proof.persist()?;
    proof.print()?;

    Ok(())
}

fn create_live_proof_dir() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join(".cleanroom/live_proofs");
    let timestamp = Utc::now().format("%Y-%m-%dT%H-%M-%S%.3fZ").to_string();
    let proof_dir = root.join(timestamp);
    fs::create_dir_all(&proof_dir)?;
    Ok(proof_dir)
}

fn seed_document() -> RawDocument {
    let long_dense = "This section explains that low-energy hygiene routines should preserve the minimum viable habit while lowering friction and reducing shame. ".repeat(12);
    RawDocument::new(
        "live codex cloud proof seed doc",
        SourceKind::Document,
        Some("memory://live-proof/depression_guide.md".to_string()),
        format!(
            r#"
# low energy hygiene
This section explains why hygiene gets harder during depression.
This section explains why hygiene gets harder during depression.
When energy is low, brushing teeth for ten seconds is still better than doing nothing at all.
However, this can feel impossible if the person is dealing with executive dysfunction.
https://example.com/further-reading
https://example.com/further-reading
{long_dense}
TODO: clarify if this paragraph belongs under sensory barriers or routine fallback.
"#
        ),
    )
}

fn fail_live_proof(
    proof: &mut LiveProofRecord,
    cleanroom: Option<&LiveCleanroom>,
    packet_id: Option<&PacketId>,
    error_text: String,
    assert_no_review_item: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    if let (Some(cleanroom), Some(packet_id)) = (cleanroom, packet_id) {
        if let Ok(replay) = cleanroom.replay_packet(packet_id) {
            proof.hydrate_from_replay(&replay);
            if assert_no_review_item && replay.review_queue_item.is_some() {
                proof.mark_failure(format!(
                    "{error_text}; review queue item unexpectedly existed after failed cloud run"
                ));
                proof.persist()?;
                proof.print()?;
                return Err(io::Error::other(
                    "review queue item unexpectedly existed after failed cloud run",
                )
                .into());
            }
        }
    }

    proof.mark_failure(error_text.clone());
    proof.persist()?;
    proof.print()?;
    Err(io::Error::other(error_text).into())
}

fn validate_success_replay(replay: &ReplayBundle) -> Result<(), String> {
    if replay.cloud_task_runs.len() != 1 {
        return Err(format!(
            "expected exactly one cloud task run, found {}",
            replay.cloud_task_runs.len()
        ));
    }
    let run = &replay.cloud_task_runs[0];
    if run.attempt_index != 1 {
        return Err(format!(
            "expected attempt_index 1, found {}",
            run.attempt_index
        ));
    }
    if !ACCEPTED_SUCCESS_STATUSES.contains(&run.final_status.as_str()) {
        return Err(format!(
            "unexpected terminal status for live proof: {}",
            run.final_status
        ));
    }
    if run.task_id.is_none() {
        return Err("live proof should persist task_id".to_string());
    }
    if run.handoff_mode != "inline_packet_visible_output_v3" {
        return Err(format!(
            "unexpected live proof handoff_mode: {}",
            run.handoff_mode
        ));
    }
    if !run.output_path.starts_with("codex_apply_out/") {
        return Err(format!(
            "live proof output path must be repo-visible under codex_apply_out/: {}",
            run.output_path
        ));
    }
    if run.matched_remote_identity.is_none() {
        return Err("live proof should persist matched_remote_identity".to_string());
    }
    if run.current_head_sha.is_none() {
        return Err("live proof should persist current_head_sha".to_string());
    }
    if run.head_contained_in_allowed_remote_ref != Some(true) {
        return Err(format!(
            "live proof should persist head_contained_in_allowed_remote_ref=true, found {:?}",
            run.head_contained_in_allowed_remote_ref
        ));
    }
    if replay.result.is_none() {
        return Err("live proof should persist cloud result".to_string());
    }
    if replay.validation.is_none() {
        return Err("live proof should persist validation".to_string());
    }
    if replay.review_queue_item.is_none() {
        return Err("live proof should persist review queue state".to_string());
    }
    if replay.approved.is_none() {
        return Err("live proof should persist approved packet".to_string());
    }
    if replay.wiki_node.is_none() {
        return Err("live proof should persist wiki node".to_string());
    }

    let seen_command_kinds = replay
        .cloud_command_traces
        .iter()
        .map(|trace| trace.command_kind.as_str())
        .collect::<HashSet<_>>();
    for required_kind in [
        "git_checkout_check",
        "git_worktree_check",
        "git_remote_check",
        "git_head_check",
        "git_remote_containment_check",
        "login_status",
        "exec",
        "list",
        "git_status_before_apply",
        "apply",
        "git_status_after_apply",
    ] {
        if !seen_command_kinds.contains(required_kind) {
            return Err(format!("missing required command trace: {required_kind}"));
        }
    }

    for new_path in &run.new_apply_paths {
        if !run.allowed_apply_paths.contains(new_path) {
            return Err(format!(
                "unexpected apply artifact outside allowed set: {new_path}"
            ));
        }
    }

    Ok(())
}
