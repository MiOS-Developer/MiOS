// AI-hint: Entry dispatcher for mios CLI: the binary verb dispatcher, shell completions and the OpenAI-compatible /v1 chat fallback.
// AI-related: usr/bin/mios, MIOS_AI_ENDPOINT, usr/libexec/mios/

pub mod ai_fallback {
    use serde::{Deserialize, Serialize};
    use std::io::{Read, Write};
    use std::net::TcpStream;

    #[derive(Serialize)]
    struct ChatMessage {
        role: String,
        content: String,
    }

    #[derive(Serialize)]
    struct ChatCompletionRequest {
        model: String,
        messages: Vec<ChatMessage>,
        stream: bool,
    }

    #[derive(Deserialize)]
    struct ChatChoice {
        message: Option<ChatMessageResponse>,
    }

    #[derive(Deserialize)]
    struct ChatMessageResponse {
        content: Option<String>,
    }

    #[derive(Deserialize)]
    struct ChatCompletionResponse {
        choices: Option<Vec<ChatChoice>>,
    }

    pub struct AiFallback;

    impl AiFallback {
        /// MIOS_AI_ENDPOINT and the model, from the environment or the resolved SSOT.
        pub fn resolve_endpoint() -> Result<(String, String), String> {
            use mios_resolver::runtime::{get, require};
            let ep = require("MIOS_AI_ENDPOINT").map_err(|e| e.to_string())?;
            let model = get("MIOS_AI_MODEL")
                .or_else(|| get("MIOS_AI_GATEWAY_MODEL"))
                .ok_or_else(|| {
                    "neither MIOS_AI_MODEL nor MIOS_AI_GATEWAY_MODEL resolves".to_string()
                })?;
            Ok((ep, model))
        }

        pub fn execute_prompt(prompt: &str) -> i32 {
            let (endpoint, model) = match Self::resolve_endpoint() {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("mios: {}", e);
                    return 1;
                }
            };
            println!(
                "[mios] Connecting to AI endpoint: {} (model: {})",
                endpoint, model
            );

            let req = ChatCompletionRequest {
                model: model.clone(),
                messages: vec![ChatMessage {
                    role: "user".to_string(),
                    content: prompt.to_string(),
                }],
                stream: false,
            };

            let json_body = match serde_json::to_string(&req) {
                Ok(j) => j,
                Err(e) => {
                    eprintln!("mios: failed to serialize chat request: {}", e);
                    return 1;
                }
            };

            // Parse host and port from endpoint URL
            let url_no_proto = endpoint
                .trim_start_matches("http://")
                .trim_start_matches("https://");
            let parts: Vec<&str> = url_no_proto.split('/').collect();
            let host_port = parts[0];
            let path_prefix = if parts.len() > 1 {
                format!("/{}", parts[1..].join("/"))
            } else {
                "/v1".to_string()
            };
            let full_path = format!("{}/chat/completions", path_prefix.trim_end_matches('/'));

            let default_port = if endpoint.starts_with("https://") {
                443
            } else {
                80
            };
            let (host, port) = if host_port.contains(':') {
                let hp: Vec<&str> = host_port.split(':').collect();
                match hp[1].parse::<u16>() {
                    Ok(p) => (hp[0], p),
                    Err(_) => {
                        eprintln!("mios: MIOS_AI_ENDPOINT '{}' has an invalid port", endpoint);
                        return 1;
                    }
                }
            } else {
                (host_port, default_port)
            };

            let stream_res = TcpStream::connect((host, port));
            let mut stream = match stream_res {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("mios: could not reach AI endpoint at {}:{} ({}). Ensure mios-llm-light or agent-pipe is running.", host, port, e);
                    return 1;
                }
            };

            let http_req = format!(
            "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            full_path, host_port, json_body.len(), json_body
        );

            if let Err(e) = stream.write_all(http_req.as_bytes()) {
                eprintln!("mios: failed to write HTTP request: {}", e);
                return 1;
            }

            let mut response_bytes = Vec::new();
            if let Err(e) = stream.read_to_end(&mut response_bytes) {
                eprintln!("mios: failed to read HTTP response: {}", e);
                return 1;
            }

            let response_str = String::from_utf8_lossy(&response_bytes);
            if let Some(body_start) = response_str.find("\r\n\r\n") {
                let body = &response_str[body_start + 4..];
                if let Ok(parsed) = serde_json::from_str::<ChatCompletionResponse>(body) {
                    if let Some(choices) = parsed.choices {
                        if let Some(first) = choices.first() {
                            if let Some(msg) = &first.message {
                                if let Some(content) = &msg.content {
                                    println!("{}", content.trim());
                                    return 0;
                                }
                            }
                        }
                    }
                }
                println!("{}", body.trim());
                return 0;
            }

            println!("{}", response_str);
            0
        }
    }
}
pub mod completions {
    use super::dispatcher::KNOWN_VERBS;

    pub struct CompletionGenerator;

    impl CompletionGenerator {
        pub fn generate(shell: &str) -> String {
            match shell.to_lowercase().as_str() {
                "bash" => Self::generate_bash(),
                "zsh" => Self::generate_zsh(),
                "fish" => Self::generate_fish(),
                "powershell" | "pwsh" => Self::generate_powershell(),
                _ => Self::generate_bash(),
            }
        }

        fn verbs_space_separated() -> String {
            KNOWN_VERBS
                .iter()
                .map(|(v, _)| *v)
                .collect::<Vec<&str>>()
                .join(" ")
        }

        fn generate_bash() -> String {
            let verbs = Self::verbs_space_separated();
            format!(
                r#"# bash completion for mios
_mios() {{
    local cur prev words cword
    _init_completion || return

    local verbs="{}"

    if [ "$cword" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$verbs --help --no-tools --tools --generate-completion" -- "$cur") )
        return 0
    fi
}}
complete -F _mios mios
"#,
                verbs
            )
        }

        fn generate_zsh() -> String {
            let verbs = Self::verbs_space_separated();
            format!(
                r#"#compdef mios
_mios() {{
    local -a verbs
    verbs=({})
    _arguments '1: :($verbs)' '*: :_files'
}}
_mios "$@"
"#,
                verbs
            )
        }

        fn generate_fish() -> String {
            let mut out = String::from("# fish completion for mios\n");
            for (v, _) in KNOWN_VERBS {
                out.push_str(&format!(
                    "complete -c mios -n '__fish_use_subcommand' -a '{}' -d 'MiOS verb {}'\n",
                    v, v
                ));
            }
            out.push_str(
                "complete -c mios -l generate-completion -d 'Generate shell completions'\n",
            );
            out.push_str("complete -c mios -l help -s h -d 'Show help'\n");
            out
        }

        fn generate_powershell() -> String {
            let verbs = Self::verbs_space_separated();
            format!(
                r#"# PowerShell completion for mios
Register-ArgumentCompleter -Native -CommandName mios -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $verbs = @({})
    $verbs | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }}
}}
"#,
                verbs
                    .split_whitespace()
                    .map(|v| format!("'{}'", v))
                    .collect::<Vec<String>>()
                    .join(", ")
            )
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_completion_generation() {
            let bash = CompletionGenerator::generate("bash");
            assert!(bash.contains("complete -F _mios mios"));
            assert!(bash.contains("build"));
            assert!(bash.contains("dash"));

            let fish = CompletionGenerator::generate("fish");
            assert!(fish.contains("complete -c mios"));

            let pwsh = CompletionGenerator::generate("pwsh");
            assert!(pwsh.contains("Register-ArgumentCompleter"));
        }
    }
}
pub mod dispatcher {
    use std::path::{Path, PathBuf};
    use std::process::Command;

    pub static KNOWN_VERBS: &[(&str, &[&str])] = &[
        ("build", &["/usr/libexec/mios/mios-build-driver"]),
        ("dash", &["/usr/bin/mios-gen", "dashboard", "--root", "/"]),
        ("mini", &["/usr/bin/mios-gen", "dashboard", "--root", "/"]),
        ("mon", &["/usr/libexec/mios/mios-dashboard", "--monitor"]),
        (
            "monitor",
            &["/usr/libexec/mios/mios-dashboard", "--monitor"],
        ),
        ("config", &["/usr/libexec/mios/mios-configurator-launch"]),
        (
            "code",
            &["xdg-open", "http://localhost:${MIOS_PORTS_CODE_SERVER}/"],
        ),
        (
            "ai",
            &["xdg-open", "http://localhost:${MIOS_PORTS_OPEN_WEBUI}/"],
        ),
        ("xbox", &["/usr/libexec/mios/xbox-repair.sh"]),
        ("virt", &["/usr/libexec/mios/virt-apply.sh"]),
        ("vfio", &["/usr/libexec/mios/vfio-config.sh"]),
        ("vfio-check", &["/usr/libexec/mios/vfio-check.sh"]),
        ("vfio-toggle", &["/usr/libexec/mios/vfio-toggle.sh"]),
        ("tune", &["/usr/libexec/mios/tune-performance.sh"]),
        ("summary", &["/usr/libexec/mios/system-summary.sh"]),
        ("profile", &["/usr/libexec/mios/system-profile.sh"]),
        ("assess", &["/usr/libexec/mios/capability-audit.sh"]),
        ("theme", &["/usr/libexec/mios/mios-sync-theme"]),
        ("dotfiles", &["/usr/libexec/mios/mios-dotfiles"]),
        ("new", &["/usr/libexec/mios/mios-new"]),
        ("iommu", &["/usr/libexec/mios/hardware-iommu.sh"]),
        (
            "iommu-groups",
            &["/usr/libexec/mios/hardware-iommu-groups.sh"],
        ),
        ("env", &["/usr/libexec/mios/system-env.sh"]),
        ("sync-env", &["/usr/libexec/mios/system-sync-env.sh"]),
        ("blade", &["/usr/libexec/mios/mios-blade"]),
        ("flatpaks", &["/usr/libexec/mios/flatpaks-manage.sh"]),
        ("user", &["/usr/libexec/mios/user-setup.sh"]),
        ("flight", &["/usr/libexec/mios/flight-control.sh"]),
        ("models", &["/usr/libexec/mios/mios-models"]),
        ("update", &["/usr/bin/mios-update"]),
        ("check", &["/usr/libexec/mios/miosd", "check"]),
        ("status", &["/usr/libexec/mios/mios-system-status"]),
        ("logs", &["journalctl", "-u", "miosd.service", "-f"]),
        ("backup", &["/usr/libexec/mios/mios-backup"]),
        ("secret", &["/usr/libexec/mios/miosd", "secret"]),
    ];

    pub struct CliDispatcher;

    impl CliDispatcher {
        pub fn find_verb(verb: &str) -> Option<&'static [&'static str]> {
            for (v, cmd) in KNOWN_VERBS {
                if *v == verb {
                    return Some(cmd);
                }
            }
            None
        }

        pub fn resolve_target(target: &str) -> PathBuf {
            let path = Path::new(target);
            if path.is_file() {
                return path.to_path_buf();
            }

            // Try resolving relative to MIOS_ROOT
            if let Ok(root) =
                std::env::var("MIOS_ROOT").or_else(|_| std::env::var("MIOS_DRIFT_ROOT"))
            {
                let candidate = Path::new(&root).join(target.trim_start_matches('/'));
                if candidate.is_file() {
                    return candidate;
                }
            }

            // Return original
            path.to_path_buf()
        }

        pub fn execute_verb(cmd_spec: &[&str], extra_args: &[String]) -> i32 {
            if cmd_spec.is_empty() {
                return 1;
            }
            let target_raw = cmd_spec[0];
            let resolved = Self::resolve_target(target_raw);
            // ${MIOS_*} in a verb's arguments resolve from the SSOT at dispatch time.
            let spec_args: Vec<String> = match cmd_spec[1..]
                .iter()
                .map(|a| mios_resolver::runtime::expand_refs(a))
                .collect()
            {
                Ok(args) => args,
                Err(e) => {
                    eprintln!("mios: cannot resolve '{}': {}", target_raw, e);
                    return 1;
                }
            };

            let mut cmd = Command::new(&resolved);
            for arg in &spec_args {
                cmd.arg(arg);
            }
            for arg in extra_args {
                cmd.arg(arg);
            }

            match cmd.status() {
                Ok(status) => status.code().unwrap_or(1),
                Err(e) => {
                    // If direct execution failed and target wasn't found, try running as PATH command
                    if e.kind() == std::io::ErrorKind::NotFound {
                        let mut fallback = Command::new(target_raw);
                        for arg in &spec_args {
                            fallback.arg(arg);
                        }
                        for arg in extra_args {
                            fallback.arg(arg);
                        }
                        if let Ok(st) = fallback.status() {
                            return st.code().unwrap_or(1);
                        }
                    }
                    eprintln!("mios: failed to execute '{}': {}", target_raw, e);
                    127
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_known_verbs_table_count() {
            assert!(
                KNOWN_VERBS.len() >= 33,
                "Expected at least 33 known verbs, got {}",
                KNOWN_VERBS.len()
            );
            assert!(CliDispatcher::find_verb("build").is_some());
            assert!(CliDispatcher::find_verb("dash").is_some());
            assert!(CliDispatcher::find_verb("check").is_some());
            assert!(CliDispatcher::find_verb("status").is_some());
            assert!(CliDispatcher::find_verb("logs").is_some());
        }

        #[test]
        fn test_resolve_target() {
            let p = CliDispatcher::resolve_target("/usr/libexec/mios/nonexistent-xyz");
            assert_eq!(p, PathBuf::from("/usr/libexec/mios/nonexistent-xyz"));
        }
    }
}

use ai_fallback::AiFallback;
use completions::CompletionGenerator;
use dispatcher::CliDispatcher;

pub fn print_help() {
    eprintln!(
        "mios -- MiOS binary verb dispatcher + OpenAI agent CLI.\n\n\
        Verbs:\n\
          mios build                           run the MiOS OCI build pipeline\n\
          mios dash                            framed dashboard snapshot (services + telemetry)\n\
          mios mini                            compact framed static dashboard snapshot\n\
          mios mon (or monitor)                unified live TUI for system services and logs\n\
          mios config                          open the HTML configurator in your browser\n\
          mios code                            open code-server in your browser\n\
          mios ai                              open Open WebUI in your browser\n\
          mios ai clear                        wipe chats/jobs/kanban/DBs/RAG clean slate\n\
          mios xbox                            Xbox VM Secure Boot / XML repair\n\
          mios virt                            apply optimized VM config + CPU pinning\n\
          mios vfio                            configure GPU/USB passthrough\n\
          mios vfio-check                      report VFIO / IOMMU binding state\n\
          mios vfio-toggle                     interactive VFIO device selector\n\
          mios tune                            system-wide CPU isolation & latency tuning\n\
          mios summary                         quick ASCII system overview\n\
          mios profile                         interactive hardware/system profiler menu\n\
          mios assess                          comprehensive system capability report\n\
          mios theme                           sync bibata/GTK/Qt themes\n\
          mios dotfiles                        project the SSOT dotfiles to your LIVE HOME\n\
          mios new <type> <name>               scaffold a new file from templates\n\
          mios iommu                           pretty-print hardware IOMMU topology\n\
          mios iommu-groups                    list raw IOMMU groups and devices\n\
          mios env                             inspect layered MIOS_* environment\n\
          mios sync-env                        regenerate install.env from mios.toml\n\
          mios blade                           manage blade roles and activation capabilities\n\
          mios flatpaks                        manage system-wide Flatpaks\n\
          mios user                            initialize user space (dotfiles/XDG)\n\
          mios flight                          flight control hardware profile\n\
          mios models                          manage first-boot large models\n\
          mios update                          perform atomic bootc OS update\n\
          mios check                           run SSOT mios.toml type and schema validator\n\
          mios status                          report unified system and daemon status\n\
          mios logs                            tail unified miosd supervisor logs\n\
          mios backup                          trigger manual snapshot backup\n\
          mios help                            show this help\n\n\
        Options:\n\
          mios --generate-completion <shell>   emit shell completions (bash, zsh, fish, pwsh)\n\
          mios <prompt>                        send prompt to local MIOS_AI_ENDPOINT\n"
    );
}

pub fn dispatch(argv: Vec<String>) -> i32 {
    if argv.len() < 2 {
        print_help();
        return 1;
    }

    let first = &argv[1];

    if first == "-h" || first == "--help" || first == "help" {
        print_help();
        return 0;
    }

    if first == "--generate-completion" || first == "--completion" {
        let shell = argv.get(2).map(|s| s.as_str()).unwrap_or("bash");
        let script = CompletionGenerator::generate(shell);
        print!("{}", script);
        return 0;
    }

    // Special case: ai clear
    if first == "ai" && argv.len() >= 3 && argv[2] == "clear" {
        let clear_bin = "/usr/libexec/mios/mios-ai-clear";
        let resolved = CliDispatcher::resolve_target(clear_bin);
        return CliDispatcher::execute_verb(&[resolved.to_str().unwrap_or(clear_bin)], &argv[3..]);
    }

    if let Some(cmd_spec) = CliDispatcher::find_verb(first) {
        return CliDispatcher::execute_verb(cmd_spec, &argv[2..]);
    }

    // Unrecognized verb -> Treat as prompt for AI Fallback
    let prompt = argv[1..].join(" ");
    AiFallback::execute_prompt(&prompt)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dispatch_help() {
        let rc = dispatch(vec!["mios".into(), "--help".into()]);
        assert_eq!(rc, 0);
    }

    #[test]
    fn test_dispatch_completions() {
        let rc = dispatch(vec![
            "mios".into(),
            "--generate-completion".into(),
            "bash".into(),
        ]);
        assert_eq!(rc, 0);
    }
}
