use std::collections::{HashMap, HashSet};

use crate::error::PipelineError;
use crate::model::{
    CloudTaskPacket, Marker, MarkerKind, NodeId, PacketId, ParsedDocument, TrimAction, TrimEntry,
    WorkUnit,
};

pub struct PacketBuilderConfig {
    pub context_radius: usize,
    pub max_work_units: usize,
}

pub struct PacketBuilder;

impl PacketBuilder {
    pub fn build(
        parsed: &ParsedDocument,
        markers: &[Marker],
        config: &PacketBuilderConfig,
    ) -> Result<CloudTaskPacket, PipelineError> {
        let marker_map = group_markers(markers);
        let node_index = parsed
            .nodes
            .iter()
            .enumerate()
            .map(|(i, n)| (n.node_id.clone(), i))
            .collect::<HashMap<_, _>>();
        let mut work_units = Vec::new();
        for node in &parsed.nodes {
            let node_markers = marker_map.get(&node.node_id).cloned().unwrap_or_default();
            let escalated = node_markers
                .iter()
                .any(|m| matches!(m.kind, MarkerKind::EscalateToCloud));
            if !escalated {
                continue;
            }
            let idx = *node_index
                .get(&node.node_id)
                .ok_or_else(|| PipelineError::Packet("missing node index".to_string()))?;
            let start = idx.saturating_sub(config.context_radius);
            let end = (idx + config.context_radius + 1).min(parsed.nodes.len());
            let mut visible = Vec::new();
            let mut context = Vec::new();
            let mut trim_map = Vec::new();
            let mut rendered = String::new();
            for neighbor in &parsed.nodes[start..end] {
                if neighbor.node_id == node.node_id {
                    visible.push(neighbor.node_id.clone());
                    trim_map.push(TrimEntry {
                        node_id: neighbor.node_id.clone(),
                        action: TrimAction::KeptVisible,
                        explanation: "target node".to_string(),
                    });
                    rendered.push_str(&format!(
                        "\n[TARGET:{}]\n{}\n",
                        neighbor.node_id.0, neighbor.text
                    ));
                    continue;
                }
                let neighbor_markers = marker_map
                    .get(&neighbor.node_id)
                    .cloned()
                    .unwrap_or_default();
                let collapsed = neighbor_markers.iter().any(|m| {
                    matches!(
                        m.kind,
                        MarkerKind::CollapseBoilerplate | MarkerKind::LikelyNoise
                    )
                });
                if collapsed {
                    trim_map.push(TrimEntry {
                        node_id: neighbor.node_id.clone(),
                        action: TrimAction::CollapsedPlaceholder,
                        explanation: "collapsed by preflight".to_string(),
                    });
                    rendered.push_str(&format!(
                        "\n[COLLAPSED:{}]\n[support text collapsed by local preflight]\n",
                        neighbor.node_id.0
                    ));
                    continue;
                }
                visible.push(neighbor.node_id.clone());
                context.push(neighbor.node_id.clone());
                trim_map.push(TrimEntry {
                    node_id: neighbor.node_id.clone(),
                    action: TrimAction::KeptVisible,
                    explanation: "neighbor kept for context".to_string(),
                });
                rendered.push_str(&format!(
                    "\n[CONTEXT:{}]\n{}\n",
                    neighbor.node_id.0, neighbor.text
                ));
            }
            let instructions = instructions_for(&node_markers);
            work_units.push(WorkUnit {
                work_unit_id: format!("wu_{}", node.node_id.0),
                target_node_id: node.node_id.clone(),
                visible_node_ids: visible,
                context_node_ids: context,
                trim_map,
                rendered_text: rendered.trim().to_string(),
                instructions,
            });
            if work_units.len() >= config.max_work_units {
                break;
            }
        }
        let seed = format!("{}-{}", parsed.raw.document_id.0, parsed.raw.raw_sha256);
        Ok(CloudTaskPacket {
            packet_id: PacketId::new(&seed),
            document_id: parsed.raw.document_id.clone(),
            work_units,
            style_contract: "Write concise, evidence-grounded summaries. Do not invent claims. Preserve ambiguity where unresolved.".to_string(),
            completion_contract: "Return one fragment per work unit with title, summary_text, unresolved_questions, and evidence refs.".to_string(),
        })
    }
}

fn group_markers(markers: &[Marker]) -> HashMap<NodeId, Vec<Marker>> {
    let mut grouped: HashMap<NodeId, Vec<Marker>> = HashMap::new();
    for marker in markers {
        grouped
            .entry(marker.target_node_id.clone())
            .or_default()
            .push(marker.clone());
    }
    grouped
}

fn instructions_for(markers: &[Marker]) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for marker in markers {
        if let Some(instr) = &marker.instruction {
            if seen.insert(instr.clone()) {
                out.push(instr.clone());
            }
        }
        match marker.kind {
            MarkerKind::NeedsContext => {
                let s = "use nearby context to resolve pronouns and topic jumps".to_string();
                if seen.insert(s.clone()) {
                    out.push(s);
                }
            }
            MarkerKind::UnsafeToEdit => {
                let s =
                    "do not normalize unresolved drafting language into fake certainty".to_string();
                if seen.insert(s.clone()) {
                    out.push(s);
                }
            }
            MarkerKind::PreserveVerbatim => {
                let s = "preserve verbatim spans when referenced".to_string();
                if seen.insert(s.clone()) {
                    out.push(s);
                }
            }
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Marker, MarkerKind, RawDocument, SourceKind, TrimAction};
    use crate::parser::Parser;

    #[test]
    fn packetizer_uses_reversible_collapsed_placeholders() {
        let raw = RawDocument::new(
            "doc",
            SourceKind::Document,
            None,
            format!(
                "Repeated setup.\n\n{}\n\nRepeated setup.",
                "Dense material. ".repeat(80)
            ),
        );
        let parsed = Parser::parse(raw).unwrap();
        let target = parsed.nodes[1].node_id.clone();
        let markers = vec![
            Marker::new(
                parsed.nodes[0].node_id.clone(),
                MarkerKind::CollapseBoilerplate,
                0.9,
                "collapse",
                None,
                vec![],
            ),
            Marker::new(
                target.clone(),
                MarkerKind::EscalateToCloud,
                0.9,
                "long",
                None,
                vec![],
            ),
        ];

        let packet = PacketBuilder::build(
            &parsed,
            &markers,
            &PacketBuilderConfig {
                context_radius: 1,
                max_work_units: 4,
            },
        )
        .unwrap();

        assert_eq!(packet.work_units.len(), 1);
        assert!(packet.work_units[0].rendered_text.contains("[COLLAPSED:"));
        assert!(packet.work_units[0]
            .trim_map
            .iter()
            .any(|entry| entry.action == TrimAction::CollapsedPlaceholder));
    }
}
