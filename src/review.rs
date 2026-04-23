use chrono::Utc;

use crate::error::PipelineError;
use crate::model::{ApprovedPacket, ReviewDecision, ReviewId, ReviewReadyPacket, ReviewStamp};

pub struct Reviewer;

impl Reviewer {
    pub fn make_review_ready(
        packet: crate::model::CloudTaskPacket,
        result: crate::model::CloudSummaryResult,
        validation: crate::model::ValidationReport,
    ) -> Result<ReviewReadyPacket, PipelineError> {
        if !validation.passed {
            return Err(PipelineError::Review(
                "cannot create review packet from failed validation".to_string(),
            ));
        }
        Ok(ReviewReadyPacket {
            packet,
            result,
            validation,
        })
    }

    pub fn approve(
        review_ready: ReviewReadyPacket,
        reviewer: impl Into<String>,
        notes: impl Into<String>,
    ) -> ApprovedPacket {
        let reviewer = reviewer.into();
        let notes = notes.into();
        let stamp = Self::stamp(
            &review_ready.packet.packet_id,
            reviewer,
            ReviewDecision::Approve,
            notes,
        );
        ApprovedPacket {
            review_ready,
            stamp,
        }
    }

    pub fn stamp(
        packet_id: &crate::model::PacketId,
        reviewer: impl Into<String>,
        decision: ReviewDecision,
        notes: impl Into<String>,
    ) -> ReviewStamp {
        let reviewer = reviewer.into();
        let notes = notes.into();
        ReviewStamp {
            review_id: ReviewId::new(&format!("{}-{}-{:?}", reviewer, packet_id.0, decision)),
            reviewer,
            decision,
            notes,
            reviewed_at: Utc::now(),
        }
    }
}
