use std::env;
use std::io::{self, BufReader, Write};

use wiki_cleanroom::shell::service::ShellService;
use wiki_cleanroom::shell::transport::{encode_message, read_request};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    if args.as_slice() != ["--stdio"] {
        eprintln!("skynet_shell_service only supports --stdio");
        std::process::exit(2);
    }

    let storage_path =
        env::var("SKYNET_SHELL_DB_PATH").unwrap_or_else(|_| "cleanroom.db".to_string());
    let mut service = ShellService::new(storage_path);

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut reader = BufReader::new(stdin.lock());
    let mut writer = stdout.lock();

    while let Some(request) = read_request(&mut reader)? {
        let response = service.handle_request(request);
        let frame = encode_message(&response)?;
        writer.write_all(&frame)?;
        writer.flush()?;
    }

    Ok(())
}
