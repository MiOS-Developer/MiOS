// AI-hint: Template conformance and verb template checks for miosd drift runner.
// AI-related: usr/share/mios/templates/, mios.toml [templates]

use super::{Check, DriftCtx, Verdict};

pub struct TemplateConformanceCheck;
impl Check for TemplateConformanceCheck {
    fn id(&self) -> &'static str {
        "check_template_conformance"
    }
    fn describe(&self) -> &'static str {
        "Assert file template conformance per Law 14 (ONE-TEMPLATE-PER-TYPE)"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-template-conform", &[])
    }
}

pub struct VerbTemplatesCheck;
impl Check for VerbTemplatesCheck {
    fn id(&self) -> &'static str {
        "check_verb_templates"
    }
    fn describe(&self) -> &'static str {
        "Assert verb templates match SSOT definitions"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict((|| {
            let policy = super::audit::ssot(ctx)?;
            let verbs = super::audit::at(&policy, "verbs")?
                .as_table()
                .ok_or("verbs must be a table")?;
            let placeholder = regex::Regex::new(r"^\{[a-zA-Z_]\w*(?:[=?!*][^}]*)?\}")
                .map_err(|e| e.to_string())?;
            let mut count = 0;
            let mut errors = Vec::new();
            for (name, spec) in verbs {
                for field in [
                    "cmd",
                    "cmd_args",
                    "cmd_positioned",
                    "cmd_pixel",
                    "cmd_resize",
                ] {
                    let Some(template) = spec.get(field).and_then(toml::Value::as_str) else {
                        continue;
                    };
                    count += 1;
                    if template.matches('{').count() != template.matches('}').count() {
                        errors.push(format!("verbs.{name}.{field}: mismatched braces"));
                        continue;
                    }
                    let mut tail = template;
                    while let Some(start) = tail.find('{') {
                        let rest = &tail[start..];
                        let Some(hit) = placeholder.find(rest) else {
                            errors.push(format!(
                                "verbs.{name}.{field}: malformed placeholder at byte {}",
                                template.len() - tail.len() + start
                            ));
                            break;
                        };
                        tail = &rest[hit.end()..];
                    }
                }
            }
            super::audit::finish(count, errors, "verb command placeholder grammar")
        })())
    }
}

pub struct VerbBackendsCheck;
impl Check for VerbBackendsCheck {
    fn id(&self) -> &'static str {
        "check_verb_backends"
    }
    fn describe(&self) -> &'static str {
        "Assert verb backends exist and are executable"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict((|| {
            let policy = super::audit::ssot(ctx)?;
            let verbs = super::audit::at(&policy, "verbs")?
                .as_table()
                .ok_or("verbs must be a table")?;
            let backend = regex::Regex::new(r"\bmios-[a-z0-9-]+").map_err(|e| e.to_string())?;
            let mut errors = Vec::new();
            for (name, spec) in verbs {
                let command = spec.get("cmd").and_then(toml::Value::as_str).unwrap_or("");
                if name == "update" && command.is_empty() {
                    errors.push("verbs.update: cmd missing".into());
                }
                for hit in backend.find_iter(command) {
                    if !["usr/bin", "usr/libexec/mios"]
                        .iter()
                        .any(|base| ctx.root.join(base).join(hit.as_str()).is_file())
                    {
                        errors.push(format!("verbs.{name}: backend {} missing", hit.as_str()));
                    }
                }
            }
            super::audit::finish(verbs.len(), errors, "declared verb backend existence")
        })())
    }
}
