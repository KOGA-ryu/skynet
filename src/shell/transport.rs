use std::fmt;
use std::io::BufRead;

use serde::Serialize;
use serde_json::Value;

use crate::shell::api::JsonRpcRequest;

#[derive(Debug)]
pub enum TransportError {
    MissingContentLength,
    InvalidContentLength,
    ExtraHeaders,
    UnexpectedEof,
    InvalidJson(String),
    BatchNotAllowed,
    NonObjectPayload,
    InvalidRequest(String),
    Io(std::io::Error),
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingContentLength => write!(f, "missing Content-Length header"),
            Self::InvalidContentLength => write!(f, "invalid Content-Length header"),
            Self::ExtraHeaders => write!(f, "extra headers are not allowed"),
            Self::UnexpectedEof => write!(f, "unexpected EOF while reading frame"),
            Self::InvalidJson(err) => write!(f, "invalid JSON: {err}"),
            Self::BatchNotAllowed => write!(f, "batch messages are not allowed"),
            Self::NonObjectPayload => write!(f, "payload must be a JSON object"),
            Self::InvalidRequest(err) => write!(f, "invalid request: {err}"),
            Self::Io(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for TransportError {}

impl From<std::io::Error> for TransportError {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value)
    }
}

pub fn read_request<R: BufRead>(reader: &mut R) -> Result<Option<JsonRpcRequest>, TransportError> {
    let Some(payload) = read_payload(reader)? else {
        return Ok(None);
    };
    decode_request_payload(&payload).map(Some)
}

pub fn decode_request_payload(payload: &[u8]) -> Result<JsonRpcRequest, TransportError> {
    let value = serde_json::from_slice::<Value>(payload)
        .map_err(|err| TransportError::InvalidJson(err.to_string()))?;
    if value.is_array() {
        return Err(TransportError::BatchNotAllowed);
    }
    if !value.is_object() {
        return Err(TransportError::NonObjectPayload);
    }
    let request = serde_json::from_value::<JsonRpcRequest>(value)
        .map_err(|err| TransportError::InvalidRequest(err.to_string()))?;
    if request.jsonrpc != "2.0" {
        return Err(TransportError::InvalidRequest(
            "jsonrpc must equal 2.0".to_string(),
        ));
    }
    if request.id == 0 {
        return Err(TransportError::InvalidRequest(
            "request ids must be positive integers".to_string(),
        ));
    }
    Ok(request)
}

pub fn encode_message<T: Serialize>(message: &T) -> Result<Vec<u8>, TransportError> {
    let payload =
        serde_json::to_vec(message).map_err(|err| TransportError::InvalidJson(err.to_string()))?;
    let mut frame = format!("Content-Length: {}\r\n\r\n", payload.len()).into_bytes();
    frame.extend_from_slice(&payload);
    Ok(frame)
}

fn read_payload<R: BufRead>(reader: &mut R) -> Result<Option<Vec<u8>>, TransportError> {
    let mut header = String::new();
    let bytes = reader.read_line(&mut header)?;
    if bytes == 0 {
        return Ok(None);
    }
    if !header.starts_with("Content-Length: ") {
        return Err(TransportError::MissingContentLength);
    }
    let content_length = header
        .trim_end()
        .strip_prefix("Content-Length: ")
        .ok_or(TransportError::MissingContentLength)?
        .parse::<usize>()
        .map_err(|_| TransportError::InvalidContentLength)?;

    let mut separator = String::new();
    let bytes = reader.read_line(&mut separator)?;
    if bytes == 0 {
        return Err(TransportError::UnexpectedEof);
    }
    if separator != "\r\n" {
        return Err(TransportError::ExtraHeaders);
    }

    let mut payload = vec![0_u8; content_length];
    reader.read_exact(&mut payload)?;
    Ok(Some(payload))
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use serde_json::json;

    use crate::shell::api::{JsonRpcRequest, JsonRpcResponse};

    use super::{decode_request_payload, encode_message, read_request, TransportError};

    #[test]
    fn content_length_round_trip_works() {
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: 1,
            method: "shell.initialize".to_string(),
            params: Some(json!({"protocol_version":"1.0"})),
        };
        let frame = encode_message(&request).unwrap();
        let mut cursor = Cursor::new(frame);
        let decoded = read_request(&mut cursor).unwrap().unwrap();
        assert_eq!(decoded, request);

        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: 1,
            result: Some(json!({"ok": true})),
            error: None,
        };
        let encoded = encode_message(&response).unwrap();
        assert!(std::str::from_utf8(&encoded)
            .unwrap()
            .starts_with("Content-Length: "));
    }

    #[test]
    fn rejects_missing_header() {
        let mut cursor = Cursor::new(br#"{}\r\n"#.to_vec());
        let err = read_request(&mut cursor).unwrap_err();
        assert!(matches!(err, TransportError::MissingContentLength));
    }

    #[test]
    fn rejects_extra_headers() {
        let mut cursor =
            Cursor::new(b"Content-Length: 2\r\nContent-Type: application/json\r\n\r\n{}".to_vec());
        let err = read_request(&mut cursor).unwrap_err();
        assert!(matches!(err, TransportError::ExtraHeaders));
    }

    #[test]
    fn rejects_batch_messages() {
        let err = decode_request_payload(br#"[{"jsonrpc":"2.0","id":1}]"#).unwrap_err();
        assert!(matches!(err, TransportError::BatchNotAllowed));
    }

    #[test]
    fn rejects_non_object_payload() {
        let err = decode_request_payload(br#""not-object""#).unwrap_err();
        assert!(matches!(err, TransportError::NonObjectPayload));
    }
}
