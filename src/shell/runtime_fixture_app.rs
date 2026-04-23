use crate::shell::api::{QueueRowDto, ReviewerIdentityDto, ShellViewDto};
use crate::shell::fixtures::{
    first_vertical_slice_replay_bundle, first_vertical_slice_review_queue_items,
};
use crate::shell::view_model::{build_shell_view, view_error};

pub fn fixture_runtime_view(service_version: &str) -> ShellViewDto {
    let replay = first_vertical_slice_replay_bundle();
    let queue_rows = first_vertical_slice_review_queue_items()
        .into_iter()
        .map(|item| QueueRowDto {
            packet_id: item.packet_id.0.clone(),
            title: match item.packet_id.0.as_str() {
                "pkt_review_001" => "Probability measure packet".to_string(),
                "pkt_review_002" => "Sigma algebra packet".to_string(),
                "pkt_review_003" => "Convergence modes packet".to_string(),
                _ => item.packet_id.0.clone(),
            },
            queue_status: item.status.as_str().to_string(),
            validation_status: match item.packet_id.0.as_str() {
                "pkt_review_001" => "warning".to_string(),
                "pkt_review_002" => "pass".to_string(),
                "pkt_review_003" => "warning".to_string(),
                _ => "unavailable".to_string(),
            },
            blocker_count: match item.packet_id.0.as_str() {
                "pkt_review_001" => 2,
                "pkt_review_002" => 0,
                "pkt_review_003" => 1,
                _ => 0,
            },
            stale: item.packet_id.0 == "pkt_review_003",
        })
        .collect();

    build_shell_view(
        service_version,
        "fixture",
        "ready",
        ReviewerIdentityDto {
            status: "present".to_string(),
            reviewer_name: Some("fixture_reviewer".to_string()),
            source: "client_env".to_string(),
        },
        None,
        None,
        Some("pkt_review_001".to_string()),
        "fixture_default",
        queue_rows,
        Some(&replay),
        view_error("none", "", false),
    )
}
