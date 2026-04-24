use chrono::Utc;

use crate::model::{ApprovedPacket, MetadataRecord, WikiNode};

pub trait MetadataEngine {
    fn enrich(&self, approved: &ApprovedPacket) -> MetadataRecord;
}

pub struct RuleBasedMetadata;

impl MetadataEngine for RuleBasedMetadata {
    fn enrich(&self, approved: &ApprovedPacket) -> MetadataRecord {
        metadata_from_approved(approved)
    }
}

pub fn metadata_from_approved(approved: &ApprovedPacket) -> MetadataRecord {
    let full_text = approved
        .review_ready
        .result
        .fragments
        .iter()
        .map(|fragment| {
            format!(
                "{} {} {}",
                fragment.summary_title,
                fragment.summary_text,
                fragment.unresolved_questions.join(" ")
            )
        })
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let mut record = MetadataAccumulator::default();

    if contains_any(
        &full_text,
        &["depression", "anxiety", "mood", "shame", "mental health"],
    ) {
        record.subject("mental_health");
    }
    if full_text.contains("depression") {
        record.tag("depression");
    }
    if full_text.contains("anxiety") {
        record.tag("anxiety");
    }

    if contains_any(
        &full_text,
        &[
            "hygiene",
            "routine",
            "brush teeth",
            "shower",
            "laundry",
            "daily living",
        ],
    ) {
        record.subject("daily_living");
    }
    if contains_any(
        &full_text,
        &["hygiene", "brush teeth", "shower", "low energy"],
    ) {
        record.tag("low_energy_hygiene");
    }
    if full_text.contains("routine") {
        record.tag("routine_support");
    }

    if contains_any(
        &full_text,
        &[
            "executive dysfunction",
            "executive function",
            "task initiation",
            "friction",
            "overwhelm",
        ],
    ) {
        record.subject("executive_function");
    }
    if contains_any(&full_text, &["executive dysfunction", "executive function"]) {
        record.tag("executive_dysfunction");
    }
    if contains_any(&full_text, &["friction", "task initiation"]) {
        record.tag("friction_reduction");
    }

    if contains_any(
        &full_text,
        &[
            "probability",
            "countable additivity",
            "sigma algebra",
            "equation",
            "theorem",
            "proof",
            "math",
            "measure",
        ],
    ) {
        record.subject("mathematics");
        record.tag("concept_summary");
    }
    if contains_any(
        &full_text,
        &[
            "probability",
            "countable additivity",
            "sigma algebra",
            "measure",
        ],
    ) {
        record.tag("probability_theory");
    }
    if contains_any(&full_text, &["theorem", "proof"]) {
        record.tag("theorem_summary");
    }

    if record.has_subject("mental_health")
        && record.has_subject("daily_living")
        && !record.has_subject("executive_function")
    {
        record.related_subject("executive_function");
    }
    if record.has_subject("daily_living")
        && record.has_subject("executive_function")
        && !record.has_subject("mental_health")
    {
        record.related_subject("mental_health");
    }
    if record.subjects.is_empty() {
        record.subject("uncategorized");
    }
    record.finish()
}

pub fn primary_subject_label(metadata: &MetadataRecord) -> String {
    humanize_subject_slug(
        metadata
            .subjects
            .first()
            .map(String::as_str)
            .unwrap_or("uncategorized"),
    )
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

#[derive(Default)]
struct MetadataAccumulator {
    subjects: Vec<String>,
    tags: Vec<String>,
    related_subjects: Vec<String>,
}

impl MetadataAccumulator {
    fn subject(&mut self, value: &str) {
        push_unique(&mut self.subjects, value);
    }

    fn tag(&mut self, value: &str) {
        push_unique(&mut self.tags, value);
    }

    fn related_subject(&mut self, value: &str) {
        push_unique(&mut self.related_subjects, value);
    }

    fn has_subject(&self, value: &str) -> bool {
        self.subjects.iter().any(|subject| subject == value)
    }

    fn finish(self) -> MetadataRecord {
        MetadataRecord {
            subjects: self.subjects,
            tags: self.tags,
            related_subjects: self.related_subjects,
        }
    }
}

fn contains_any(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| text.contains(needle))
}

fn push_unique(values: &mut Vec<String>, value: &str) {
    if !values.iter().any(|existing| existing == value) {
        values.push(value.to_string());
    }
}

fn humanize_subject_slug(subject: &str) -> String {
    subject
        .split('_')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use chrono::Utc;

    use super::{metadata_from_approved, primary_subject_label, RuleBasedMetadata};
    use crate::metadata::MetadataEngine;
    use crate::model::{
        ApprovedPacket, CloudSummaryResult, CloudTaskPacket, DocumentId, NodeId, PacketId,
        ReviewDecision, ReviewId, ReviewReadyPacket, ReviewStamp, Span, SummaryFragment,
        ValidationReport,
    };

    #[test]
    fn metadata_engine_derives_canonical_subjects_tags_and_related_subjects() {
        let approved = sample_approved_packet(
            "Low energy hygiene",
            "Depression makes hygiene routines harder and executive dysfunction increases friction.",
        );

        let metadata = RuleBasedMetadata.enrich(&approved);

        assert_eq!(
            metadata.subjects,
            vec![
                "mental_health".to_string(),
                "daily_living".to_string(),
                "executive_function".to_string(),
            ]
        );
        assert!(metadata.tags.contains(&"depression".to_string()));
        assert!(metadata.tags.contains(&"low_energy_hygiene".to_string()));
        assert!(metadata.tags.contains(&"executive_dysfunction".to_string()));
        assert!(metadata.tags.contains(&"friction_reduction".to_string()));
        assert!(metadata.related_subjects.is_empty());
    }

    #[test]
    fn metadata_engine_falls_back_to_uncategorized() {
        let approved = sample_approved_packet(
            "Loose concept note",
            "This summary keeps unresolved wording without any domain-specific cues.",
        );

        let metadata = metadata_from_approved(&approved);

        assert_eq!(metadata.subjects, vec!["uncategorized".to_string()]);
        assert!(metadata.tags.is_empty());
        assert!(metadata.related_subjects.is_empty());
    }

    #[test]
    fn primary_subject_label_humanizes_subject_names() {
        assert_eq!(
            primary_subject_label(&crate::model::MetadataRecord {
                subjects: vec!["executive_function".to_string()],
                tags: vec![],
                related_subjects: vec![],
            }),
            "Executive Function"
        );
    }

    fn sample_approved_packet(title: &str, summary_text: &str) -> ApprovedPacket {
        ApprovedPacket {
            review_ready: ReviewReadyPacket {
                packet: CloudTaskPacket {
                    packet_id: PacketId("pkt_metadata_test".to_string()),
                    document_id: DocumentId("doc_metadata_test".to_string()),
                    work_units: vec![],
                    style_contract: "concise".to_string(),
                    completion_contract: "traceable".to_string(),
                },
                result: CloudSummaryResult {
                    packet_id: PacketId("pkt_metadata_test".to_string()),
                    model_name: "test-model".to_string(),
                    fragments: vec![SummaryFragment {
                        target_node_id: NodeId("node_metadata_test".to_string()),
                        summary_title: title.to_string(),
                        summary_text: summary_text.to_string(),
                        unresolved_questions: vec!["Should the phrasing stay concise?".to_string()],
                        evidence: vec![crate::model::EvidenceRef {
                            node_id: NodeId("node_metadata_test".to_string()),
                            span: Span { start: 0, end: 10 },
                        }],
                    }],
                },
                validation: ValidationReport {
                    packet_id: PacketId("pkt_metadata_test".to_string()),
                    passed: true,
                    issues: vec![],
                },
            },
            stamp: ReviewStamp {
                review_id: ReviewId("rev_metadata_test".to_string()),
                reviewer: "ace".to_string(),
                decision: ReviewDecision::Approve,
                notes: "metadata test approval".to_string(),
                reviewed_at: Utc::now(),
            },
        }
    }
}
