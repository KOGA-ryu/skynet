use crate::cloud::CloudSummarizer;
use crate::error::PipelineError;
use crate::metadata::{promote_to_wiki, MetadataEngine};
use crate::model::{ApprovedPacket, ParsedDocument, RawDocument, WikiNode};
use crate::packetizer::{PacketBuilder, PacketBuilderConfig};
use crate::parser::Parser;
use crate::preflight::LocalMarkerModel;
use crate::review::Reviewer;
use crate::validation::Validator;

pub struct Pipeline<L, C, M>
where
    L: LocalMarkerModel,
    C: CloudSummarizer,
    M: MetadataEngine,
{
    pub local: L,
    pub cloud: C,
    pub metadata: M,
}

impl<L, C, M> Pipeline<L, C, M>
where
    L: LocalMarkerModel,
    C: CloudSummarizer,
    M: MetadataEngine,
{
    pub fn parse(&self, raw: RawDocument) -> Result<ParsedDocument, PipelineError> {
        Parser::parse(raw)
    }

    pub fn prepare_cloud_packet(
        &self,
        parsed: &ParsedDocument,
    ) -> Result<(crate::model::CloudTaskPacket, Vec<crate::model::Marker>), PipelineError> {
        let markers = self.local.scan(parsed);
        let packet = PacketBuilder::build(
            parsed,
            &markers,
            &PacketBuilderConfig {
                context_radius: 1,
                max_work_units: 32,
            },
        )?;
        Ok((packet, markers))
    }

    pub fn run_cloud_and_validate(
        &self,
        packet: crate::model::CloudTaskPacket,
        markers: &[crate::model::Marker],
    ) -> Result<crate::model::ReviewReadyPacket, PipelineError> {
        let result = self.cloud.summarize(&packet)?;
        let validation = Validator::validate(&packet, &result, markers);
        Reviewer::make_review_ready(packet, result, validation)
    }

    pub fn approve(
        &self,
        review_ready: crate::model::ReviewReadyPacket,
        reviewer: &str,
    ) -> ApprovedPacket {
        Reviewer::approve(review_ready, reviewer, "approved after manual review")
    }

    pub fn promote(&self, approved: &ApprovedPacket) -> WikiNode {
        let metadata = self.metadata.enrich(approved);
        promote_to_wiki(approved, metadata)
    }
}
