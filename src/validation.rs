use std::collections::HashSet;

use crate::model::{
    CloudSummaryResult, CloudTaskPacket, Marker, MarkerKind, ValidationIssue, ValidationReport,
    ValidationSeverity,
};

pub struct Validator;

impl Validator {
    pub fn validate(
        packet: &CloudTaskPacket,
        result: &CloudSummaryResult,
        markers: &[Marker],
    ) -> ValidationReport {
        let mut issues = Vec::new();
        if packet.work_units.is_empty() {
            issues.push(issue(
                ValidationSeverity::Error,
                true,
                Some(packet.packet_id.0.clone()),
                "packet has no work units".to_string(),
            ));
        }
        if packet.packet_id != result.packet_id {
            issues.push(issue(
                ValidationSeverity::Error,
                true,
                Some(packet.packet_id.0.clone()),
                "packet id mismatch between task and summary result".to_string(),
            ));
        }
        let valid_target_ids = packet
            .work_units
            .iter()
            .map(|w| w.target_node_id.clone())
            .collect::<HashSet<_>>();
        let visible_ids = packet
            .work_units
            .iter()
            .flat_map(|w| w.visible_node_ids.iter().cloned())
            .collect::<HashSet<_>>();
        for fragment in &result.fragments {
            if !valid_target_ids.contains(&fragment.target_node_id) {
                issues.push(ValidationIssue {
                    issue_id: format!("issue_unknown_target_{}", fragment.target_node_id.0),
                    severity: ValidationSeverity::Error,
                    blocking: true,
                    target_id: Some(fragment.target_node_id.0.clone()),
                    message: format!(
                        "fragment references unknown target node {}",
                        fragment.target_node_id.0
                    ),
                });
            }
            if fragment.evidence.is_empty() {
                issues.push(issue(
                    ValidationSeverity::Error,
                    true,
                    Some(fragment.target_node_id.0.clone()),
                    format!(
                        "fragment for target {} has no evidence refs",
                        fragment.target_node_id.0
                    ),
                ));
            }
            for evidence in &fragment.evidence {
                if !visible_ids.contains(&evidence.node_id) {
                    issues.push(issue(
                        ValidationSeverity::Error,
                        true,
                        Some(evidence.node_id.0.clone()),
                        format!(
                            "evidence references hidden or unknown node {}",
                            evidence.node_id.0
                        ),
                    ));
                }
            }
            if fragment.summary_text.trim().is_empty() {
                issues.push(issue(
                    ValidationSeverity::Error,
                    true,
                    Some(fragment.target_node_id.0.clone()),
                    format!(
                        "fragment for target {} has empty summary text",
                        fragment.target_node_id.0
                    ),
                ));
            }
        }
        if result.fragments.len() != packet.work_units.len() {
            issues.push(issue(
                ValidationSeverity::Error,
                true,
                Some(packet.packet_id.0.clone()),
                format!(
                    "fragment count {} does not match work unit count {}",
                    result.fragments.len(),
                    packet.work_units.len()
                ),
            ));
        }
        let unsafe_nodes = markers
            .iter()
            .filter(|m| matches!(m.kind, MarkerKind::UnsafeToEdit))
            .map(|m| m.target_node_id.clone())
            .collect::<HashSet<_>>();
        for fragment in &result.fragments {
            if unsafe_nodes.contains(&fragment.target_node_id)
                && fragment.unresolved_questions.is_empty()
            {
                issues.push(ValidationIssue {
                    issue_id: format!("issue_unsafe_target_{}", fragment.target_node_id.0),
                    severity: ValidationSeverity::Warning,
                    blocking: false,
                    target_id: Some(fragment.target_node_id.0.clone()),
                    message: format!(
                        "unsafe-to-edit target {} was summarized without unresolved questions",
                        fragment.target_node_id.0
                    ),
                });
            }
        }
        let passed = !issues
            .iter()
            .any(|issue| matches!(issue.severity, ValidationSeverity::Error));
        ValidationReport {
            packet_id: packet.packet_id.clone(),
            passed,
            issues,
        }
    }
}

fn issue(
    severity: ValidationSeverity,
    blocking: bool,
    target_id: Option<String>,
    message: String,
) -> ValidationIssue {
    let stable_target = target_id.clone().unwrap_or_else(|| "packet".to_string());
    ValidationIssue {
        issue_id: format!(
            "issue_{stable_target}_{}",
            crate::model::sha256_hex(&message)[..12].to_string()
        ),
        severity,
        blocking,
        target_id,
        message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cloud::{CloudSummarizer, MockCloudSummarizer};
    use crate::model::{PacketId, RawDocument, SourceKind};
    use crate::packetizer::{PacketBuilder, PacketBuilderConfig};
    use crate::parser::Parser;
    use crate::preflight::{LocalMarkerModel, RuleBasedPreflight};

    #[test]
    fn validator_rejects_packet_mismatch() {
        let raw = RawDocument::new("doc", SourceKind::Document, None, "Long. ".repeat(200));
        let parsed = Parser::parse(raw).unwrap();
        let markers = RuleBasedPreflight.scan(&parsed);
        let packet = PacketBuilder::build(
            &parsed,
            &markers,
            &PacketBuilderConfig {
                context_radius: 1,
                max_work_units: 1,
            },
        )
        .unwrap();
        let mut result = MockCloudSummarizer.summarize(&packet).unwrap();
        result.packet_id = PacketId("pkt_wrong".to_string());

        let report = Validator::validate(&packet, &result, &markers);

        assert!(!report.passed);
    }

    #[test]
    fn validator_rejects_empty_packets() {
        let packet = CloudTaskPacket {
            packet_id: PacketId("pkt_empty".to_string()),
            document_id: crate::model::DocumentId("doc_empty".to_string()),
            work_units: vec![],
            style_contract: "style".to_string(),
            completion_contract: "done".to_string(),
        };
        let result = CloudSummaryResult {
            packet_id: packet.packet_id.clone(),
            model_name: "mock".to_string(),
            fragments: vec![],
        };

        let report = Validator::validate(&packet, &result, &[]);

        assert!(!report.passed);
    }
}
