use regex::Regex;

use crate::error::PipelineError;
use crate::model::{NodeId, NodeKind, ParsedDocument, ParsedNode, RawDocument, Span};

pub struct Parser;

impl Parser {
    pub fn parse(raw: RawDocument) -> Result<ParsedDocument, PipelineError> {
        let mut nodes = Vec::new();
        let mut ordinal = 0usize;
        let mut offset = 0usize;
        let mut buffer = String::new();
        let mut buffer_start = 0usize;
        let mut in_code = false;

        for line in raw.raw_text.split_inclusive('\n') {
            let trimmed = line.trim();
            if !in_code && is_heading_line(trimmed) {
                if !buffer.is_empty() {
                    let kind = classify_block(&buffer);
                    push_block(
                        &raw,
                        &mut nodes,
                        &mut ordinal,
                        &mut buffer,
                        buffer_start,
                        kind,
                    );
                }
                let heading_text = line.to_string();
                let heading_start = offset;
                offset += line.len();
                push_heading_block(&raw, &mut nodes, &mut ordinal, heading_start, heading_text);
                continue;
            }
            if trimmed.starts_with("```") {
                if !buffer.is_empty() && !in_code {
                    push_block(
                        &raw,
                        &mut nodes,
                        &mut ordinal,
                        &mut buffer,
                        buffer_start,
                        NodeKind::Paragraph,
                    );
                }
                if !in_code {
                    buffer_start = offset;
                }
                in_code = !in_code;
                buffer.push_str(line);
                offset += line.len();
                if !in_code {
                    push_block(
                        &raw,
                        &mut nodes,
                        &mut ordinal,
                        &mut buffer,
                        buffer_start,
                        NodeKind::CodeBlock,
                    );
                }
                continue;
            }
            if in_code {
                buffer.push_str(line);
                offset += line.len();
                continue;
            }
            if trimmed.is_empty() {
                if !buffer.is_empty() {
                    let kind = classify_block(&buffer);
                    push_block(
                        &raw,
                        &mut nodes,
                        &mut ordinal,
                        &mut buffer,
                        buffer_start,
                        kind,
                    );
                }
                offset += line.len();
                continue;
            }
            if buffer.is_empty() {
                buffer_start = offset;
            }
            buffer.push_str(line);
            offset += line.len();
        }
        if !buffer.is_empty() {
            let kind = if in_code {
                NodeKind::CodeBlock
            } else {
                classify_block(&buffer)
            };
            push_block(
                &raw,
                &mut nodes,
                &mut ordinal,
                &mut buffer,
                buffer_start,
                kind,
            );
        }
        Ok(ParsedDocument { raw, nodes })
    }
}

fn is_heading_line(trimmed: &str) -> bool {
    trimmed.starts_with('#')
}

fn push_heading_block(
    raw: &RawDocument,
    nodes: &mut Vec<ParsedNode>,
    ordinal: &mut usize,
    start: usize,
    text: String,
) {
    let span = Span {
        start,
        end: start + text.len(),
    };
    let node_id = NodeId::for_document(
        &raw.document_id,
        &format!("Heading:{}:{}:{}", span.start, span.end, *ordinal),
    );
    nodes.push(ParsedNode {
        node_id,
        parent_node_id: None,
        kind: NodeKind::Heading,
        span,
        text,
        ordinal: *ordinal,
    });
    *ordinal += 1;
}

fn push_block(
    raw: &RawDocument,
    nodes: &mut Vec<ParsedNode>,
    ordinal: &mut usize,
    buffer: &mut String,
    buffer_start: usize,
    kind: NodeKind,
) {
    if buffer.trim().is_empty() {
        buffer.clear();
        return;
    }
    let text = buffer.clone();
    let span = Span {
        start: buffer_start,
        end: buffer_start + text.len(),
    };
    let node_id = NodeId::for_document(
        &raw.document_id,
        &format!("{:?}:{}:{}:{}", kind, span.start, span.end, *ordinal),
    );
    nodes.push(ParsedNode {
        node_id: node_id.clone(),
        parent_node_id: None,
        kind: kind.clone(),
        span: span.clone(),
        text: text.clone(),
        ordinal: *ordinal,
    });
    *ordinal += 1;

    if matches!(kind, NodeKind::Paragraph | NodeKind::Quote) {
        let sentence_re = Regex::new(r#"[^.!?]+[.!?]+|[^.!?]+$"#).unwrap();
        for sentence in sentence_re.find_iter(&text) {
            let sentence_text = sentence.as_str().trim().to_string();
            if sentence_text.is_empty() {
                continue;
            }
            let global_start = buffer_start + sentence.start();
            let global_end = buffer_start + sentence.end();
            nodes.push(ParsedNode {
                node_id: NodeId::for_document(
                    &raw.document_id,
                    &format!("sentence:{global_start}:{global_end}:{}", *ordinal),
                ),
                parent_node_id: Some(node_id.clone()),
                kind: NodeKind::Sentence,
                span: Span {
                    start: global_start,
                    end: global_end,
                },
                text: sentence_text,
                ordinal: *ordinal,
            });
            *ordinal += 1;
        }
    }
    buffer.clear();
}

fn classify_block(text: &str) -> NodeKind {
    let trimmed = text.trim();
    if trimmed.starts_with('#') {
        return NodeKind::Heading;
    }
    if trimmed
        .lines()
        .all(|line| line.trim_start().starts_with('>'))
    {
        return NodeKind::Quote;
    }
    if trimmed.contains("http://") || trimmed.contains("https://") {
        let linkish_lines = trimmed.lines().filter(|line| line.contains("http")).count();
        let total_lines = trimmed.lines().count().max(1);
        if linkish_lines * 2 >= total_lines {
            return NodeKind::LinkList;
        }
    }
    NodeKind::Paragraph
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SourceKind;

    #[test]
    fn parser_preserves_code_blocks_and_sentences() {
        let raw = RawDocument::new(
            "doc",
            SourceKind::Document,
            None,
            "# Title\n\nFirst sentence. Second sentence.\n\n```rust\nlet x = 1;\n```\n",
        );

        let parsed = Parser::parse(raw.clone()).unwrap();

        assert_eq!(parsed.raw.raw_text, raw.raw_text);
        assert!(parsed.nodes.iter().any(|n| n.kind == NodeKind::Heading));
        assert!(parsed.nodes.iter().any(|n| n.kind == NodeKind::CodeBlock));
        assert!(parsed.nodes.iter().any(|n| n.kind == NodeKind::Paragraph));
        assert_eq!(
            parsed
                .nodes
                .iter()
                .filter(|n| n.kind == NodeKind::Sentence)
                .count(),
            2
        );
    }

    #[test]
    fn parser_classifies_link_lists() {
        let raw = RawDocument::new(
            "links",
            SourceKind::Document,
            None,
            "https://example.com/a\nhttps://example.com/b\n",
        );

        let parsed = Parser::parse(raw).unwrap();

        assert!(parsed.nodes.iter().any(|n| n.kind == NodeKind::LinkList));
    }

    #[test]
    fn parser_splits_heading_from_following_paragraph() {
        let raw = RawDocument::new(
            "doc",
            SourceKind::Document,
            None,
            "# Title\nBody paragraph.\n",
        );

        let parsed = Parser::parse(raw).unwrap();

        assert_eq!(parsed.nodes[0].kind, NodeKind::Heading);
        assert_eq!(parsed.nodes[1].kind, NodeKind::Paragraph);
    }
}
