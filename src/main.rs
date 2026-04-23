use std::env;

use wiki_cleanroom::cleanroom::Cleanroom;
use wiki_cleanroom::cloud::CodexCloudSummarizer;
use wiki_cleanroom::codex_cloud::CodexCloudConfig;
use wiki_cleanroom::metadata::RuleBasedMetadata;
use wiki_cleanroom::model::{RawDocument, SourceKind};
use wiki_cleanroom::preflight::RuleBasedPreflight;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let env_id = env::var("CODEX_CLOUD_ENV_ID")
        .map_err(|_| "set CODEX_CLOUD_ENV_ID before running the cleanroom demo")?;
    let codex =
        CodexCloudSummarizer::new(CodexCloudConfig::new(env!("CARGO_MANIFEST_DIR"), env_id))?;
    let mut cleanroom =
        Cleanroom::open("cleanroom.db", RuleBasedPreflight, codex, RuleBasedMetadata)?;
    let long_dense = "This section explains that low-energy hygiene routines should preserve the minimum viable habit while lowering friction and reducing shame. ".repeat(18);
    let raw = RawDocument::new(
        "depression guide seed doc",
        SourceKind::Document,
        Some("memory://demo/depression_guide.md".to_string()),
        format!(
            r#"
# low energy hygiene
This section explains why hygiene gets harder during depression.
This section explains why hygiene gets harder during depression.
When energy is low, brushing teeth for ten seconds is still better than doing nothing at all.
However, this can feel impossible if the person is dealing with executive dysfunction.
https://example.com/further-reading
https://example.com/further-reading
{long_dense}
TODO: clarify if this paragraph belongs under sensory barriers or routine fallback.
"#
        ),
    );
    let packet_id = cleanroom.ingest_and_stage(raw)?;
    println!("staged packet: {}", packet_id.0);
    cleanroom.run_cloud(&packet_id)?;
    println!("cloud summary stored and queued for review.");
    if let Some(item) = cleanroom.claim_next_review("ace")? {
        let approved = cleanroom.approve_packet(&item.packet_id, Some("approved after review"))?;
        println!(
            "approved packet: {} by {}",
            approved.review_ready.packet.packet_id.0, approved.stamp.reviewer
        );
        let wiki = cleanroom.promote_approved(&item.packet_id)?;
        println!("promoted wiki node {}", wiki.wiki_node_id);
    }
    Ok(())
}
