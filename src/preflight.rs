use std::collections::HashMap;

use crate::model::{Marker, MarkerKind, NodeId, NodeKind, ParsedDocument};

pub trait LocalMarkerModel {
    fn scan(&self, parsed: &ParsedDocument) -> Vec<Marker>;
}

pub struct RuleBasedPreflight;

impl RuleBasedPreflight {
    fn normalize(text: &str) -> String {
        text.split_whitespace()
            .map(|s| s.to_lowercase())
            .collect::<Vec<_>>()
            .join(" ")
    }

    fn repeated_text_counts(parsed: &ParsedDocument) -> HashMap<String, usize> {
        let mut counts = HashMap::new();
        for node in &parsed.nodes {
            if matches!(node.kind, NodeKind::Sentence) {
                continue;
            }
            let key = Self::normalize(&node.text);
            if key.is_empty() {
                continue;
            }
            *counts.entry(key).or_insert(0) += 1;
        }
        counts
    }

    fn add_marker(
        markers: &mut Vec<Marker>,
        node_id: NodeId,
        kind: MarkerKind,
        confidence: f32,
        reason: impl Into<String>,
        instruction: Option<String>,
        related: Vec<NodeId>,
    ) {
        markers.push(Marker::new(
            node_id,
            kind,
            confidence,
            reason,
            instruction,
            related,
        ));
    }
}

impl LocalMarkerModel for RuleBasedPreflight {
    fn scan(&self, parsed: &ParsedDocument) -> Vec<Marker> {
        let mut markers = Vec::new();
        let repeated = Self::repeated_text_counts(parsed);
        for node in &parsed.nodes {
            if matches!(node.kind, NodeKind::Sentence) {
                continue;
            }
            let normalized = Self::normalize(&node.text);
            let repeat_count = repeated.get(&normalized).copied().unwrap_or(0);
            let text = node.text.trim();
            let lower = text.to_lowercase();
            match node.kind {
                NodeKind::Heading => {
                    Self::add_marker(
                        &mut markers,
                        node.node_id.clone(),
                        MarkerKind::CoreContent,
                        0.95,
                        "heading usually anchors a section",
                        Some("preserve heading language and use it as summary anchor".to_string()),
                        vec![],
                    );
                }
                NodeKind::CodeBlock => {
                    Self::add_marker(
                        &mut markers,
                        node.node_id.clone(),
                        MarkerKind::PreserveVerbatim,
                        0.98,
                        "code blocks should not be paraphrased by preflight",
                        Some("keep exact code text".to_string()),
                        vec![],
                    );
                }
                NodeKind::LinkList => {
                    Self::add_marker(
                        &mut markers,
                        node.node_id.clone(),
                        MarkerKind::LikelyNoise,
                        0.70,
                        "link-heavy block is usually support material, not core summary material",
                        Some("collapse unless referenced by nearby core content".to_string()),
                        vec![],
                    );
                }
                NodeKind::Paragraph | NodeKind::Quote | NodeKind::Unknown => {
                    if repeat_count > 1 && text.len() < 220 {
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::CollapseBoilerplate,
                            0.86,
                            "short repeated block looks like boilerplate or repeated framing",
                            Some("replace with collapsed placeholder in cloud packet".to_string()),
                            vec![],
                        );
                    }
                    if text.len() > 900 {
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::NeedsContext,
                            0.88,
                            "long block likely needs neighboring context before summary",
                            Some("include previous and next structural nodes".to_string()),
                            vec![],
                        );
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::EscalateToCloud,
                            0.85,
                            "long dense block is a good candidate for cloud summary",
                            Some("generate bounded summary with evidence references".to_string()),
                            vec![],
                        );
                    }
                    if lower.starts_with("this ")
                        || lower.starts_with("that ")
                        || lower.starts_with("it ")
                        || lower.starts_with("however")
                        || lower.starts_with("but ")
                    {
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::NeedsContext,
                            0.72,
                            "block begins with context-dependent phrasing",
                            Some("include antecedent block in packet".to_string()),
                            vec![],
                        );
                    }
                    if lower.contains("tbd")
                        || lower.contains("todo")
                        || lower.contains("???")
                        || lower.contains("[unclear]")
                    {
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::UnsafeToEdit,
                            0.90,
                            "ambiguous or unresolved drafting markers detected",
                            Some("do not silently normalize this block".to_string()),
                            vec![],
                        );
                    }
                    if text.len() >= 120 && !lower.contains("http") {
                        Self::add_marker(
                            &mut markers,
                            node.node_id.clone(),
                            MarkerKind::CoreContent,
                            0.66,
                            "non-trivial prose block likely contains useful summary material",
                            Some(
                                "consider for summary if escalated or linked to escalated blocks"
                                    .to_string(),
                            ),
                            vec![],
                        );
                    }
                }
                NodeKind::Sentence => {}
            }
        }
        markers
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{RawDocument, SourceKind};
    use crate::parser::Parser;

    #[test]
    fn preflight_marks_long_unsafe_and_code_nodes() {
        let long = "Long dense material. ".repeat(80);
        let raw = RawDocument::new(
            "doc",
            SourceKind::Document,
            None,
            format!("```text\nverbatim\n```\n\n{long}\n\nTODO: clarify this."),
        );
        let parsed = Parser::parse(raw).unwrap();
        let markers = RuleBasedPreflight.scan(&parsed);

        assert!(markers
            .iter()
            .any(|m| m.kind == MarkerKind::PreserveVerbatim));
        assert!(markers
            .iter()
            .any(|m| m.kind == MarkerKind::EscalateToCloud));
        assert!(markers.iter().any(|m| m.kind == MarkerKind::UnsafeToEdit));
    }
}
