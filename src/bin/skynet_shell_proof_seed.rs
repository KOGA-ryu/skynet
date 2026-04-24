use std::env;
use std::path::Path;

use wiki_cleanroom::shell::proof::create_shell_proof_workspace;

struct Args {
    reviewer: String,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args(&env::args().skip(1).collect::<Vec<_>>())?;
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let manifest = create_shell_proof_workspace(repo_root, &args.reviewer)?;
    println!("{}", serde_json::to_string_pretty(&manifest)?);
    Ok(())
}

fn parse_args(args: &[String]) -> Result<Args, Box<dyn std::error::Error>> {
    if args.len() == 1 && args[0] == "--help" {
        println!("usage: cargo run --bin skynet_shell_proof_seed -- [--reviewer <name>]");
        std::process::exit(0);
    }

    let mut reviewer = "ace".to_string();
    let mut index = 0_usize;
    while index < args.len() {
        match args[index].as_str() {
            "--reviewer" => {
                index += 1;
                reviewer = args
                    .get(index)
                    .ok_or("--reviewer requires a value")?
                    .to_string();
            }
            other => {
                return Err(format!("unknown argument: {other}").into());
            }
        }
        index += 1;
    }

    Ok(Args { reviewer })
}
