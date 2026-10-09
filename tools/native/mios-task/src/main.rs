// AI-hint: mios-task -- validates, renders and edits tasks.jsonl, the one canonical MiOS task list (ADR-0028); rebuilds the retired lists from their frozen provenance.
// AI-related: /usr/share/mios/mios.toml [tasks.store], /usr/lib/mios/schemas/task-record.schema.json, tasks.jsonl, TASKS.md, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: main

mod check;
mod cli;
mod migrate;
mod overrides;
mod record;

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first() {
        Some(verb) if cli::VERBS.contains(&verb.as_str()) => cli::run(verb, &args[1..]),
        Some(verb) if verb == "-h" || verb == "--help" => {
            println!("{}", cli::USAGE);
            ExitCode::SUCCESS
        }
        Some(verb) => {
            eprintln!("mios-task: unknown verb {verb}\n{}", cli::USAGE);
            ExitCode::from(64)
        }
        None => {
            eprintln!("{}", cli::USAGE);
            ExitCode::from(64)
        }
    }
}
