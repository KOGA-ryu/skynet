use thiserror::Error;

#[derive(Debug, Error)]
pub enum PipelineError {
    #[error("parse error: {0}")]
    Parse(String),
    #[error("packet build error: {0}")]
    Packet(String),
    #[error("validation error: {0}")]
    Validation(String),
    #[error("review error: {0}")]
    Review(String),
    #[error("storage error: {0}")]
    Storage(String),
    #[error("serialization error: {0}")]
    Serde(String),
    #[error("not found: {0}")]
    NotFound(String),
}
