use std::env;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::Utc;
use serde::{Deserialize, Serialize};

use crate::cleanroom::Cleanroom;
use crate::cloud::MockCloudSummarizer;
use crate::error::PipelineError;
use crate::metadata::RuleBasedMetadata;
use crate::model::{QueueStatus, RawDocument, SourceKind};
use crate::preflight::RuleBasedPreflight;
use crate::shell::api::ReviewerIdentityDto;
use crate::shell::runtime_storage_app::storage_runtime_view;
use crate::storage::Database;

const DEFAULT_PACKET_COUNT: usize = 2;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShellProofQueueState {
    pub packet_id: String,
    pub title: String,
    pub queue_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShellProofManifest {
    pub created_at: String,
    pub workspace_dir: String,
    pub manifest_path: String,
    pub db_path: String,
    pub reviewer: String,
    pub seeded_packet_ids: Vec<String>,
    pub selected_target_packet_id: String,
    pub expected_selection_reason: String,
    pub expected_gate_label: String,
    pub expected_initial_queue_state: Vec<ShellProofQueueState>,
    pub before_claim_png_path: String,
    pub after_claim_png_path: String,
    pub after_approve_png_path: String,
}

impl ShellProofManifest {
    pub fn persist(&self) -> Result<(), PipelineError> {
        fs::write(
            &self.manifest_path,
            serde_json::to_string_pretty(self)
                .map_err(|err| PipelineError::Serde(format!("serialize failed: {err}")))?,
        )
        .map_err(|err| {
            PipelineError::Storage(format!(
                "failed to write shell proof manifest {}: {err}",
                self.manifest_path
            ))
        })
    }
}

pub fn create_shell_proof_workspace(
    repo_root: &Path,
    reviewer: &str,
) -> Result<ShellProofManifest, PipelineError> {
    let workspace_dir = repo_root
        .join(".cleanroom")
        .join("shell_proofs")
        .join(timestamp_dir_name());
    fs::create_dir_all(&workspace_dir).map_err(|err| {
        PipelineError::Storage(format!(
            "failed to create shell proof workspace {}: {err}",
            workspace_dir.display()
        ))
    })?;

    let seed_source_db_path =
        env::temp_dir().join(format!("skynet_shell_proof_seed_{}.db", unique_suffix()));
    let seeded_packet_ids = seed_pending_review_db(&seed_source_db_path, DEFAULT_PACKET_COUNT)?;

    let db_path = workspace_dir.join("shell_proof.db");
    fs::copy(&seed_source_db_path, &db_path).map_err(|err| {
        PipelineError::Storage(format!(
            "failed to copy seed db {} to {}: {err}",
            seed_source_db_path.display(),
            db_path.display()
        ))
    })?;
    fs::remove_file(&seed_source_db_path).ok();

    let db = Database::open(db_path.to_string_lossy().as_ref())?;
    let queue_items = db.list_review_items()?;
    let expected_initial_queue_state = queue_items
        .iter()
        .map(|item| {
            let title = db
                .load_raw(&item.document_id)
                .ok()
                .flatten()
                .map(|raw| raw.title)
                .unwrap_or_else(|| item.packet_id.0.clone());
            ShellProofQueueState {
                packet_id: item.packet_id.0.clone(),
                title,
                queue_status: item.status.as_str().to_string(),
            }
        })
        .collect::<Vec<_>>();

    let target_packet_id = queue_items
        .iter()
        .find(|item| item.status == QueueStatus::Pending)
        .map(|item| item.packet_id.0.clone())
        .ok_or_else(|| {
            PipelineError::Storage("shell proof db did not contain a pending packet".to_string())
        })?;

    let view = storage_runtime_view(
        &db,
        env!("CARGO_PKG_VERSION"),
        reviewer_identity(reviewer),
        None,
        None,
        None,
    )?;
    let active_packet_id = view.active_packet_id.clone().ok_or_else(|| {
        PipelineError::Storage(
            "shell proof storage view did not select an active packet".to_string(),
        )
    })?;
    if active_packet_id != target_packet_id {
        return Err(PipelineError::Storage(format!(
            "shell proof target packet mismatch: queue target {} but view selected {}",
            target_packet_id, active_packet_id
        )));
    }

    let manifest = ShellProofManifest {
        created_at: Utc::now().to_rfc3339(),
        workspace_dir: display_path(&workspace_dir),
        manifest_path: display_path(&workspace_dir.join("shell_proof_manifest.json")),
        db_path: display_path(&db_path),
        reviewer: reviewer.to_string(),
        seeded_packet_ids,
        selected_target_packet_id: target_packet_id,
        expected_selection_reason: view.selection_reason,
        expected_gate_label: view.gate.label,
        expected_initial_queue_state,
        before_claim_png_path: display_path(&workspace_dir.join("before_claim.png")),
        after_claim_png_path: display_path(&workspace_dir.join("after_claim.png")),
        after_approve_png_path: display_path(&workspace_dir.join("after_approve.png")),
    };
    manifest.persist()?;
    Ok(manifest)
}

pub fn seed_pending_review_db(
    db_path: &Path,
    packet_count: usize,
) -> Result<Vec<String>, PipelineError> {
    let mut cleanroom = Cleanroom::open(
        db_path.to_string_lossy().as_ref(),
        RuleBasedPreflight,
        MockCloudSummarizer,
        RuleBasedMetadata,
    )?;
    let mut packet_ids = Vec::new();
    for index in 0..packet_count {
        let packet_id = cleanroom.ingest_and_stage(RawDocument::new(
            format!("shell proof seed doc {index}"),
            SourceKind::Document,
            Some(format!("memory://shell-proof/doc_{index}.md")),
            format!(
                "# shell proof seed {index}\n\nIntro {index}.\n\n{}\n\nTODO: clarify fallback {}.",
                "Dense text. ".repeat(120),
                index
            ),
        ))?;
        cleanroom.run_cloud(&packet_id)?;
        packet_ids.push(packet_id.0.clone());
    }
    Ok(packet_ids)
}

fn reviewer_identity(reviewer: &str) -> ReviewerIdentityDto {
    ReviewerIdentityDto {
        status: "present".to_string(),
        reviewer_name: Some(reviewer.to_string()),
        source: "proof_seed".to_string(),
    }
}

fn timestamp_dir_name() -> String {
    Utc::now().format("%Y-%m-%dT%H-%M-%S%.3fZ").to_string()
}

fn unique_suffix() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
        .to_string()
}

fn display_path(path: &Path) -> String {
    path.display().to_string()
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    use crate::model::QueueStatus;
    use crate::storage::Database;

    use super::{create_shell_proof_workspace, seed_pending_review_db};

    #[test]
    fn seed_pending_review_db_creates_pending_packets() {
        let db_path = temp_db_path("shell_proof_seed_pending");
        let packet_ids = seed_pending_review_db(&db_path, 2).unwrap();
        let db = Database::open(db_path.to_string_lossy().as_ref()).unwrap();
        let queue = db.list_review_items().unwrap();

        assert_eq!(packet_ids.len(), 2);
        assert_eq!(queue.len(), 2);
        assert!(queue.iter().all(|item| item.status == QueueStatus::Pending));

        fs::remove_file(db_path).ok();
    }

    #[test]
    fn create_shell_proof_workspace_writes_manifest_and_copied_db() {
        let repo_root = temp_repo_root("shell_proof_workspace");
        let manifest = create_shell_proof_workspace(&repo_root, "ace").unwrap();

        assert_eq!(manifest.reviewer, "ace");
        assert_eq!(manifest.seeded_packet_ids.len(), 2);
        assert_eq!(manifest.expected_selection_reason, "newest_pending");
        assert_eq!(manifest.expected_initial_queue_state.len(), 2);
        assert!(PathBuf::from(&manifest.db_path).exists());
        assert!(PathBuf::from(&manifest.manifest_path).exists());
        assert!(manifest.before_claim_png_path.ends_with("before_claim.png"));
        assert!(manifest.after_claim_png_path.ends_with("after_claim.png"));
        assert!(manifest
            .after_approve_png_path
            .ends_with("after_approve.png"));

        let db = Database::open(&manifest.db_path).unwrap();
        let queue = db.list_review_items().unwrap();
        assert_eq!(queue[0].packet_id.0, manifest.selected_target_packet_id);

        fs::remove_dir_all(repo_root).ok();
    }

    fn temp_repo_root(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("{name}_{unique}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn temp_db_path(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("{name}_{unique}.db"))
    }
}
