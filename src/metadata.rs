use chrono::Utc;

use crate::model::{ApprovedPacket, MetadataRecord, WikiNode};

pub trait MetadataEngine {
    fn enrich(&self, approved: &ApprovedPacket) -> MetadataRecord;
}

pub struct RuleBasedMetadata;

impl MetadataEngine for RuleBasedMetadata {
    fn enrich(&self, approved: &ApprovedPacket) -> MetadataRecord {
        let full_text = approved
            .review_ready
            .result
            .fragments
            .iter()
            .map(|fragment| format!("{} {}", fragment.summary_title, fragment.summary_text))
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase();
        let mut subjects = Vec::new();
        let mut tags = Vec::new();
        let mut related_subjects = Vec::new();
        if full_text.contains("depression") {
            subjects.push("mental_health".to_string());
            tags.push("depression".to_string());
        }
        if full_text.contains("hygiene") || full_text.contains("routine") {
            subjects.push("daily_living".to_string());
            tags.push("low_energy_hygiene".to_string());
            related_subjects.push("executive_function".to_string());
        }
        if full_text.contains("math") || full_text.contains("equation") {
            subjects.push("math".to_string());
            tags.push("concept_summary".to_string());
        }
        if subjects.is_empty() {
            subjects.push("uncategorized".to_string());
        }
        MetadataRecord {
            subjects,
            tags,
            related_subjects,
        }
    }
}

pub fn promote_to_wiki(approved: &ApprovedPacket, metadata: MetadataRecord) -> WikiNode {
    let body = approved
        .review_ready
        .result
        .fragments
        .iter()
        .map(|fragment| {
            format!(
                "## {}\n\n{}\n",
                fragment.summary_title, fragment.summary_text
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    let title = approved
        .review_ready
        .result
        .fragments
        .first()
        .map(|fragment| fragment.summary_title.clone())
        .unwrap_or_else(|| "Untitled Wiki Node".to_string());
    let source_node_ids = approved
        .review_ready
        .result
        .fragments
        .iter()
        .map(|fragment| fragment.target_node_id.clone())
        .collect::<Vec<_>>();
    WikiNode {
        wiki_node_id: format!("wiki_{}", approved.review_ready.packet.packet_id.0),
        source_document_id: approved.review_ready.packet.document_id.clone(),
        approved_packet_id: approved.review_ready.packet.packet_id.clone(),
        title,
        body,
        metadata,
        source_node_ids,
        created_at: Utc::now(),
    }
}
